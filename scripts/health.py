#!/usr/bin/env python
"""Как оно там на самом деле — одной командой.

    PYTHONPATH=. .venv/bin/python scripts/health.py
    PYTHONPATH=. .venv/bin/python scripts/health.py --days 7

Отказы и их причины уже лежат в базе: у каждой неудавшейся работы записан
провайдер и текст ошибки. Не хватало не данных, а того, чтобы их кто-то
заметил — а никто не станет писать SQL, чтобы узнать, всё ли в порядке.

Здесь же и себестоимость. Мы берём с человека TOONTOON, а платим провайдеру
доллары, и связь между ними видна только так: сколько долларов стоил один
TOONTOON за последние сутки.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import text                             # noqa: E402

from app.config import settings                         # noqa: E402
from app.db import session_scope                        # noqa: E402
from app.db.session import connect, disconnect          # noqa: E402


def rule(title: str) -> None:
    print(f"\n{title}\n{'─' * len(title)}")


async def main(days: int) -> int:
    await connect()
    try:
        async with session_scope() as s:
            await outcomes(s, days)
            await reasons(s, days)
            await stuck(s)
            await providers(s, days)
            await economics(s, days)
    finally:
        await disconnect()
    return 0


async def outcomes(s, days: int) -> None:
    rule(f"Чем кончались работы, по дням (последние {days})")
    rows = (await s.execute(text("""
        SELECT created_at::date AS d,
               count(*) FILTER (WHERE status = 'done')    AS ok,
               count(*) FILTER (WHERE status = 'failed')  AS bad,
               count(*) FILTER (WHERE status = 'running') AS live
        FROM generations
        WHERE created_at > now() - make_interval(days => :d)
        GROUP BY d ORDER BY d DESC
    """), {"d": days})).all()
    if not rows:
        print("  за этот срок работ не было")
        return
    print(f"  {'день':<12}{'готово':>8}{'отказ':>8}{'идёт':>7}{'доля отказов':>15}")
    for d, ok, bad, live in rows:
        total = ok + bad
        share = f"{bad / total:.1%}" if total else "—"
        # Доля важнее числа: две неудачи из трёх и две из трёхсот — разные новости.
        print(f"  {d.strftime('%d.%m'):<12}{ok:>8}{bad:>8}{live:>7}{share:>15}")


async def reasons(s, days: int) -> None:
    rows = (await s.execute(text("""
        SELECT split_part(coalesce(error, 'без текста'), ':', 1) AS kind,
               count(*) AS n, max(created_at) AS last
        FROM generations
        WHERE status = 'failed' AND created_at > now() - make_interval(days => :d)
        GROUP BY kind ORDER BY n DESC LIMIT 10
    """), {"d": days})).all()
    if not rows:
        return
    rule("Почему отказывали")
    for kind, n, last in rows:
        print(f"  {n:>4}×  {kind[:70]:<72} последний {last:%d.%m %H:%M}")


async def stuck(s) -> None:
    """Две беды, за которые человек уже заплатил, а работы не получил."""
    row = (await s.execute(text("""
        SELECT count(*) FILTER (WHERE status = 'running'
                                AND created_at < now() - make_interval(mins => :m)) AS dead,
               count(*) FILTER (
                   WHERE status = 'failed'
                     AND request_params->>'payment_id' IS NOT NULL
                     AND cost > 0
                     AND NOT EXISTS (
                         SELECT 1 FROM wallet_ledger l
                         WHERE l.ref_id = generations.request_params->>'payment_id'
                           AND l.reason = 'refund')) AS owed
        FROM generations
    """), {"m": settings.stale_generation_minutes})).one()
    dead, owed = row
    if not dead and not owed:
        return
    rule("Долги перед людьми")
    if dead:
        print(f"  {dead} работ(ы) висят дольше {settings.stale_generation_minutes} мин — оборвались")
    if owed:
        print(f"  {owed} работ(ы) неудачны, а деньги не вернулись")
    print("  Разобрать: PYTHONPATH=. .venv/bin/python scripts/settle_refunds.py")


async def providers(s, days: int) -> None:
    rule(f"Кто рисовал и почём (последние {days} дн.)")
    rows = (await s.execute(text("""
        SELECT provider_id, count(*) AS n,
               round(avg(extract(epoch FROM finished_at - created_at))::numeric, 1) AS secs,
               round(avg(provider_cost_usd)::numeric, 4) AS avg_usd,
               round(sum(provider_cost_usd)::numeric, 2) AS sum_usd
        FROM generations
        WHERE status = 'done' AND created_at > now() - make_interval(days => :d)
        GROUP BY provider_id ORDER BY n DESC
    """), {"d": days})).all()
    if not rows:
        print("  за этот срок никто ничего не нарисовал")
        return
    print(f"  {'провайдер':<24}{'кадров':>8}{'секунд':>9}{'$/кадр':>10}{'$ всего':>10}")
    for pid, n, secs, avg_usd, sum_usd in rows:
        # Пусто там, где провайдер цену не сообщает: прочерк честнее нуля.
        print(f"  {(pid or '—'):<24}{n:>8}{(secs if secs is not None else '—'):>9}"
              f"{(avg_usd if avg_usd is not None else '—'):>10}"
              f"{(sum_usd if sum_usd is not None else '—'):>10}")


async def economics(s, days: int) -> None:
    row = (await s.execute(text("""
        SELECT sum(cost) AS toontoon, sum(provider_cost_usd) AS usd
        FROM generations
        WHERE status = 'done' AND created_at > now() - make_interval(days => :d)
    """), {"d": days})).one()
    toontoon, usd = row
    if not toontoon or not usd:
        return
    rule("Себестоимость")
    print(f"  Взято с людей: {toontoon} TOONTOON")
    print(f"  Заплачено провайдерам: ${usd:.2f}")
    # Ради этого числа всё и считается: цена подписки должна стоять выше него,
    # иначе каждый активный человек стоит нам денег.
    print(f"  Один TOONTOON обошёлся нам в ${usd / toontoon:.4f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7, help="за сколько дней смотреть")
    raise SystemExit(asyncio.run(main(ap.parse_args().days)))
