#!/usr/bin/env python
"""Сколько кадров сделано без лица там, где человек просил себя — и жалеет ли он.

    PYTHONPATH=. .venv/bin/python scripts/faceless_frames.py

Решение принято 25 августа 2026: когда человек говорит «сделай меня», а снимка
нет ни приложенного, ни в профиле, разговор предупреждает словами и кнопку не
отнимает. Это его выбор — но выбор надо считать, иначе мы не узнаем, читают ли
предупреждение вообще.

Меряем двумя числами: сколько таких кадров сделано и сколько из них стёрли в
первую минуту. Быстрое удаление — единственный доступный нам голос человека,
который не стал писать в поддержку.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.db import models as m
from app.db.session import connect, disconnect, get_factory

QUICK = timedelta(minutes=1)


async def main() -> None:
    await connect()
    async with get_factory()() as session:
        rows = (await session.scalars(
            select(m.Generation).where(m.Generation.status == "done")
        )).all()
        faceless = [r for r in rows if (r.request_params or {}).get("made_without_face")]
        if not rows:
            print("В базе нет ни одной готовой работы.")
            return

        share = 100 * len(faceless) / len(rows)
        print(f"готовых работ: {len(rows)}")
        print(f"из них без лица там, где просили себя: {len(faceless)} ({share:.1f}%)")
        if not faceless:
            return

        deleted = [r for r in faceless if r.deleted_at is not None]
        quick = [r for r in deleted
                 if r.finished_at and r.deleted_at - r.finished_at <= QUICK]
        print(f"удалено вообще: {len(deleted)} из {len(faceless)}")
        print(f"удалено в первую минуту: {len(quick)}")

        # Для сравнения — те же числа по всем остальным работам. Само по себе
        # «стёрли пятую часть» не значит ничего: может, столько стирают всегда.
        rest = [r for r in rows if r not in faceless]
        rest_quick = [r for r in rest if r.deleted_at is not None and r.finished_at
                      and r.deleted_at - r.finished_at <= QUICK]
        if rest:
            print(f"для сравнения, по остальным работам в первую минуту: "
                  f"{len(rest_quick)} из {len(rest)} "
                  f"({100 * len(rest_quick) / len(rest):.1f}%)")
    await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
