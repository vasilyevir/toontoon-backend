#!/usr/bin/env python
"""Кому мы должны и доплатить.

    PYTHONPATH=. .venv/bin/python scripts/settle_refunds.py          # только показать
    PYTHONPATH=. .venv/bin/python scripts/settle_refunds.py --pay    # и вернуть

То же самое делает приложение при старте. Скрипт нужен, когда доплатить надо
не дожидаясь перезапуска, и — главное — чтобы посмотреть список, ничего не
трогая: «за что вернули» это разговор с людьми, которым не повезло.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import sys

sys.path.insert(0, ".")

from app.db import session_scope                      # noqa: E402
from app.db.session import connect, disconnect        # noqa: E402
from app.config import settings                        # noqa: E402
from app.db.repositories import generations as generations_repo  # noqa: E402
from app.db.repositories import wallet as wallet_repo  # noqa: E402
from app.services import wallet                        # noqa: E402


async def main(pay: bool) -> int:
    # Скрипт живёт вне приложения: подключение поднимает сам.
    await connect()
    try:
        return await _run(pay)
    finally:
        await disconnect()


async def _run(pay: bool) -> int:
    async with session_scope() as session:
        # Две разные беды, и показать надо обе: выплата тронет и те, и другие.
        owed = await wallet_repo.owed_refunds(session)
        stale = await generations_repo.stale_running(
            session, older_than=timedelta(minutes=settings.stale_generation_minutes))

        if not owed and not stale:
            print("Долгов нет: за каждую неудавшуюся работу деньги вернулись.")
            return 0

        if owed:
            total = sum(r["amount"] for r in owed)
            people = len({r["user_id"] for r in owed})
            print(f"Не вернулось {total} TOONTOON за {len(owed)} работ(ы), людей: {people}")
            for r in owed:
                print(f"  {r['generation_id']}  {r['user_id']}  "
                      f"{r['amount']} TOONTOON  {r['payment_id']}")
            print()

        if stale:
            print(f"Оборвались на середине — {len(stale)} работ(ы). Процесс убили "
                  f"раньше, чем она себя пометила; висят дольше "
                  f"{settings.stale_generation_minutes} мин:")
            for g in stale:
                print(f"  {g.id}  {g.user_id}  {g.cost} TOONTOON  начата {g.created_at:%d.%m %H:%M}")
            print()

        if not pay:
            print("Это только показ. Чтобы вернуть — тот же запуск с --pay.")
            return 1

        settled = await wallet.settle_owed(session)
        print(f"\nВернули за {len(settled)} работ(ы).")
        return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pay", action="store_true", help="не только показать, но и вернуть")
    raise SystemExit(asyncio.run(main(ap.parse_args().pay)))
