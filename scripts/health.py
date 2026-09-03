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
import shutil
import subprocess
import sys

sys.path.insert(0, ".")

from sqlalchemy import text                             # noqa: E402

from app.config import settings                         # noqa: E402
from app.db import session_scope                        # noqa: E402
from app.db.session import connect, disconnect          # noqa: E402
from app.services import diagnosis                      # noqa: E402


def rule(title: str) -> None:
    print(f"\n{title}\n{'─' * len(title)}")


async def pulse(notify: bool) -> int:
    """Один вопрос — всё ли в порядке — и код возврата вместо чтения.

    Отдельно от сводки, потому что сводку читает человек, а это — расписание.
    Ноль значит «спокойно», единица — «иди посмотри», и cron с launchd умеют
    отличать одно от другого без нашего участия.

    Тот же самый диагноз, что отдаёт /health/pulse. Нарочно тот же: сводка,
    которая расходится с ручкой, годится только на то, чтобы спорить с ней.
    """
    await connect()
    try:
        async with session_scope() as s:
            d = await diagnosis.diagnose(s)
    finally:
        await disconnect()

    if d.ok:
        print("Спокойно. " + ", ".join(f"{k}: {v}" for k, v in d.facts.items()))
        return 0

    print("Нехорошо:")
    for why in d.reasons:
        print(f"  • {why}")
    if notify:
        _knock("Toontoon: нехорошо", "; ".join(d.reasons))
    return 1


def _knock(title: str, body: str) -> None:
    """Постучаться в macOS, пока сервер не выставлен наружу.

    До кластера снаружи его не опросить ничем, и до тех пор единственный
    способ, которым отказ находит нас сам, — уведомление на той же машине.
    Когда появится домен, эту роль заберёт аптайм-монитор через /health/pulse,
    и стук останется для локальных прогонов.
    """
    if not (osa := shutil.which("osascript")):
        return
    safe = body.replace('"', "'")[:200]
    subprocess.run([osa, "-e",
                    f'display notification "{safe}" with title "{title}" sound name "Basso"'],
                   check=False)


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



# Запросы вынесены сюда, а не оставлены внутри печатающих функций, чтобы тест
# мог выполнить ИМЕННО ИХ. Проверка, пересказывающая запрос своими словами,
# продолжает проходить и после того, как настоящий запрос сломали, — а этот
# считает себестоимость, по которой ставят цену подписки.
#
# Лестница источников в обоих одна и та же: измеренная цена → медиана
# собственных замеров этого же провайдера за тот же срок → прайс реестра.
PROVIDER_COSTS_SQL = """
        WITH measured AS (
            SELECT provider_id,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_cost_usd) AS usd
              FROM generations
             WHERE status = 'done' AND provider_cost_usd IS NOT NULL
               AND created_at > now() - make_interval(days => :d)
             GROUP BY provider_id
        )
        SELECT g.provider_id, count(*) AS n,
               round(avg(extract(epoch FROM g.finished_at - g.created_at))::numeric, 1) AS secs,
               round(avg(coalesce(
                   g.provider_cost_usd, m.usd,
                   (p.cost_hint->>'usd_per_image')::float))::numeric, 4) AS avg_usd,
               round(sum(coalesce(
                   g.provider_cost_usd, m.usd,
                   (p.cost_hint->>'usd_per_image')::float))::numeric, 2) AS sum_usd,
               count(*) FILTER (WHERE g.provider_cost_usd IS NULL
                                  AND m.usd IS NOT NULL) AS from_median,
               count(*) FILTER (WHERE g.provider_cost_usd IS NULL
                                  AND m.usd IS NULL
                                  AND p.cost_hint->>'usd_per_image' IS NOT NULL) AS from_list,
               count(*) FILTER (WHERE g.provider_cost_usd IS NULL
                                  AND m.usd IS NULL
                                  AND p.cost_hint->>'usd_per_image' IS NULL) AS unknown
        FROM generations g
        LEFT JOIN generation_providers p ON p.id = g.provider_id
        LEFT JOIN measured m ON m.provider_id = g.provider_id
        WHERE g.status = 'done' AND g.created_at > now() - make_interval(days => :d)
        GROUP BY g.provider_id ORDER BY n DESC
"""

ECONOMICS_SQL = """
        WITH measured AS (
            SELECT provider_id,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_cost_usd) AS usd
              FROM generations
             WHERE status = 'done' AND provider_cost_usd IS NOT NULL
               AND created_at > now() - make_interval(days => :d)
             GROUP BY provider_id
        )
        SELECT sum(g.cost) AS toontoon,
               sum(coalesce(g.provider_cost_usd, m.usd,
                            (p.cost_hint->>'usd_per_image')::float)) AS usd,
               sum(g.provider_cost_usd) AS measured,
               count(*) AS n,
               count(*) FILTER (WHERE g.provider_cost_usd IS NULL) AS guessed,
               count(*) FILTER (WHERE g.provider_cost_usd IS NULL
                                  AND m.usd IS NULL
                                  AND p.cost_hint->>'usd_per_image' IS NULL) AS unknown
        FROM generations g
        LEFT JOIN generation_providers p ON p.id = g.provider_id
        LEFT JOIN measured m ON m.provider_id = g.provider_id
        WHERE g.status = 'done' AND g.created_at > now() - make_interval(days => :d)
"""


async def providers(s, days: int) -> None:
    rule(f"Кто рисовал и почём (последние {days} дн.)")
    # Цена берётся измеренная; где её нет — по медиане СОБСТВЕННЫХ замеров
    # этого же провайдера за тот же срок; и только если замеров нет вовсе —
    # из прайса строки реестра.
    #
    # Медиана появилась здесь потому, что одиночное число — не цена, а нижняя
    # граница. Цена кадра гуляет ВТРОЕ внутри одной и той же модели: длина
    # промпта и число референсов у живых запросов разные. Ориентиры в реестре
    # и в docs/PROMPTING.md брались с одного лабораторного кадра — короткий
    # промпт, один референс, — и легли ровно на дно этого разброса:
    #
    #     модель                     в доке    минимум   медиана   разброс
    #     gpt-image-2                $0.042    $0.0334   $0.0667     3.3x
    #     gemini-3.1-flash-image     $0.068    $0.0680   $0.2048     3.0x
    #     gemini-3-pro-image         $0.137    $0.1349   $0.1379     2.1x
    #
    # Совпадение с минимумом, а не с медианой, видно по всем трём строкам
    # разом. Значит любой такой ориентир занижает ПО ПОСТРОЕНИЮ, и тем
    # сильнее, чем шире разброс у модели. У `openrouter_gpt` это стоило 37
    # процентов расхода.
    #
    # Медиана собственного трафика ошибиться так не может: она сложена из тех
    # самых запросов, которые считаем, и следует за ними сама. Прайс остаётся
    # тем, чем и должен быть, — последней опорой для тех, кто цену не сообщает
    # никогда (fal), а не первой.
    #
    # Колонка «откуда» показывает, чему верить: «факт» — сказал провайдер,
    # «по замерам» — медиана его же кадров, «прайс» — прайс-лист без скидок.
    rows = (await s.execute(text(PROVIDER_COSTS_SQL), {"d": days})).all()
    if not rows:
        print("  за этот срок никто ничего не нарисовал")
        return
    print(f"  {'провайдер':<24}{'кадров':>8}{'секунд':>9}{'$/кадр':>10}{'$ всего':>10}  откуда")
    for pid, n, secs, avg_usd, sum_usd, from_median, from_list, unknown in rows:
        # Три разных состояния, и путать их нельзя. «Прайс» значит, что число
        # взято из прайс-листа; «нет цены» — что кадры не учтены ВООБЩЕ, и
        # сумма занижена. Раньше подпись называла прайсом и то и другое, то
        # есть врала ровно там, где заведена не врать.
        части = []
        if from_median: части.append(f"по замерам {from_median}/{n}")
        if from_list: части.append(f"прайс {from_list}/{n}")
        if unknown: части.append(f"нет цены {unknown}/{n}")
        откуда = ", ".join(части) if части else "факт"
        print(f"  {(pid or '—'):<24}{n:>8}{(secs if secs is not None else '—'):>9}"
              f"{(avg_usd if avg_usd is not None else '—'):>10}"
              f"{(sum_usd if sum_usd is not None else '—'):>10}  {откуда}")


async def economics(s, days: int) -> None:
    """Во что обошёлся один TOONTOON.

    Здесь считалась только ИЗМЕРЕННАЯ цена, и это молча врало. Числитель брал
    доллары лишь у тех провайдеров, кто их сообщает, а знаменатель — TOONTOON у
    всех. Пока всё рисовал OpenRouter, разницы не было; с приходом fal, который
    цену не сообщает вовсе, себестоимость поехала бы вниз тем сильнее, чем
    больше работы к нему уходит. По этому числу ставят цену подписки.

    Теперь недостающее добирается по лестнице: медиана собственных замеров
    этого же провайдера за тот же срок, а если замеров нет вовсе — прайс
    реестра. Доля неизмеренного печатается рядом: число без указания, из чего
    оно сложено, — это то же враньё, только вежливое.

    Порядок именно такой, потому что одиночный замер — это пол, а не цена:
    внутри одной модели кадр стоит от $0.0334 до $0.1090. Ориентиры в реестре
    брались с одного лабораторного кадра и легли на дно разброса, занижая
    расход на треть. Медиана собственного трафика так ошибиться не может —
    она сложена из тех же запросов, которые считаем.
    """
    row = (await s.execute(text(ECONOMICS_SQL), {"d": days})).one()
    toontoon, usd, measured, n, guessed, unknown = row
    if not toontoon or not usd:
        return
    rule("Себестоимость")
    print(f"  Взято с людей: {toontoon} TOONTOON")
    print(f"  Заплачено провайдерам: ${usd:.2f}", end="")
    if guessed:
        доля = (usd - (measured or 0)) / usd * 100
        print(f"   (из них {доля:.0f}% оценкой, не по факту)")
    else:
        print()
    # Ради этого числа всё и считается: цена подписки должна стоять выше него,
    # иначе каждый активный человек стоит нам денег.
    print(f"  Один TOONTOON обошёлся нам в ${usd / toontoon:.4f}")
    if unknown:
        # Молчать нельзя: эти кадры не попали в сумму ни фактом, ни прайсом,
        # значит число занижено, и неизвестно насколько.
        print(f"  ⚠ {unknown} из {n} кадров не учтены вовсе: у их исполнителя "
              f"нет ни цены в ответе, ни прайса в реестре")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7, help="за сколько дней смотреть")
    ap.add_argument("--pulse", action="store_true",
                    help="только диагноз; код возврата 1, когда нехорошо")
    ap.add_argument("--notify", action="store_true",
                    help="с --pulse: постучаться уведомлением macOS")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(pulse(a.notify) if a.pulse else main(a.days)))
