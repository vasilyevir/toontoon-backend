#!/usr/bin/env python
"""Стереть снимки заброшенных гостей.

    PYTHONPATH=. .venv/bin/python scripts/retention.py           # только показать
    PYTHONPATH=. .venv/bin/python scripts/retention.py --apply    # и стереть

Срока хранения не было вовсе: фотографии лиц лежали у нас вечно. Хуже того,
уборка заброшенных гостей упоминалась в трёх комментариях как существующая
(`deps.py`, `models.py`, `users.py` — «abandoned-guest cleanup reads this»), и
ради неё исправно писалось `last_seen_at`. Читать это было некому.

Кого трогаем и почему именно этих. Гость — человек, не оставивший нам ни почты,
ни покупки: спросить, нужны ли ему ещё его снимки, невозможно, а держать чужое
лицо без спроса бесконечно нельзя. Зарегистрированных не трогаем: их данные их,
и у них есть кнопка удаления в настройках.

Стираются ФАЙЛЫ, а не строки. Строка остаётся пустым следом: на медиа
ссылаются работы, а на работы — книга проводок, и рвать эту цепь база
справедливо не даст. Ровно так же устроено удаление аккаунта — та же функция,
`media_repo.erase_everything_of`.

Показ по умолчанию, стирание по флагу. Обратной стороны у этой операции нет:
файл, стёртый из хранилища, не возвращается, поэтому посмотреть, кого тронет,
надо раньше, чем трогать.

Запускать по расписанию — CronJob в чарте. Пока запускается руками; и то и
другое лучше, чем ничего, а «ничего» здесь означает вечное хранение.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

sys.path.insert(0, ".")

from app.config import settings                        # noqa: E402
from app.db import session_scope                       # noqa: E402
from app.db.session import connect, disconnect         # noqa: E402
from app.db.repositories import media as media_repo    # noqa: E402
from app.db.repositories import users as users_repo    # noqa: E402
from app import storage                                # noqa: E402


def сколько(n: int, одна: str, две: str, много: str) -> str:
    хвост = n % 100
    if 11 <= хвост <= 14:
        слово = много
    elif n % 10 == 1:
        слово = одна
    elif 2 <= n % 10 <= 4:
        слово = две
    else:
        слово = много
    return f"{n} {слово}"


async def main(стирать: bool, дней: int) -> int:
    await connect()
    try:
        await storage.startup()
    except Exception as exc:  # noqa: BLE001 — без хранилища стирать нечем
        print(f"Хранилище недоступно ({exc}). Стирать нечем — выходим.")
        await disconnect()
        return 1
    try:
        return await _run(стирать, дней)
    finally:
        await disconnect()


async def _run(стирать: bool, дней: int) -> int:
    from datetime import timedelta

    срок = timedelta(days=дней)
    async with session_scope() as session:
        гости = await users_repo.abandoned_guests(session, older_than=срок)

        if not гости:
            print(f"Заброшенных гостей старше {сколько(дней, 'дня', 'дней', 'дней')} "
                  "со снимками нет — стирать нечего.")
            return 0

        print(f"Не заходили дольше {сколько(дней, 'дня', 'дней', 'дней')}, "
              f"и снимки ещё лежат: {сколько(len(гости), 'гость', 'гостя', 'гостей')}")
        for гость in гости:
            когда = гость.last_seen_at or гость.created_at
            print(f"  {гость.id}  последний заход {когда:%Y-%m-%d}")

        if not стирать:
            print("\nЭто показ. Стереть: scripts/retention.py --apply")
            return 0

        всего = 0
        for гость in гости:
            всего += await media_repo.erase_everything_of(session, гость.id)
        print(f"\nСтёрто {сколько(всего, 'снимок', 'снимка', 'снимков')} "
              f"у {сколько(len(гости), 'гостя', 'гостей', 'гостей')}.")
        return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true",
                   help="стереть, а не только показать")
    p.add_argument("--days", type=int, default=settings.guest_media_retention_days,
                   help=f"срок в днях (по умолчанию {settings.guest_media_retention_days})")
    args = p.parse_args()
    raise SystemExit(asyncio.run(main(args.apply, args.days)))
