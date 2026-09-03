"""Поставить или снять флаг «проверенное публичное лицо».

Проверка политики отказывает на снимке, похожем на известного человека, — и
самому известному человеку тоже: снимок один и тот же, согласия на нём не
видно. Поддержка проверяет, что это он (официальный канал, видео с телефона),
и ставит флаг руками. Автоматизировать это нельзя: фотографию экрана камера
не отличит.

    PYTHONPATH=. .venv/bin/python -m scripts.verify_public_figure usr_…
    PYTHONPATH=. .venv/bin/python -m scripts.verify_public_figure usr_… --revoke

Текстовый фильтр флаг не отключает: «сделай меня как Бекхэма» не нужно и
самому Бекхэму.
"""
from __future__ import annotations

import argparse
import asyncio

from app.db import models as m
from app.db.session import connect, disconnect, session_scope


async def _run(user_id: str, verified: bool) -> None:
    await connect()
    try:
        async with session_scope() as db:
            user = await db.get(m.User, user_id)
            if user is None:
                raise SystemExit(f"пользователя {user_id} нет")
            user.verified_public_figure = verified
            await db.flush()
            print(f"{user_id}: verified_public_figure = {verified}")
    finally:
        await disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("user_id")
    parser.add_argument("--revoke", action="store_true", help="снять флаг")
    args = parser.parse_args()
    asyncio.run(_run(args.user_id, not args.revoke))


if __name__ == "__main__":
    main()
