"""Процесс, который рисует кадры из очереди.

    WORKER_MODE=queue python -m app.worker

Один цикл: взять id из очереди, найти работу, собрать аргументы из
спецификации (снимки — из хранилища), позвать тот же `run_image_job`, что и
веб-процесс раньше. Всё, что тот делал сам — возврат денег при отказе, запись
в историю, переписка, — он делает и здесь.

При старте — подбор: всё, что осталось queued/running со спецификацией в
строке, снова кладётся в очередь. Это и есть причина существования файла:
деплой больше не уносит чужие кадры.

Пульс: раз в цикл воркер обновляет ключ с TTL; сторож (`scripts/health.py
--pulse`) увидит, если воркер молчит дольше, чем живёт ключ.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta

from sqlalchemy import select

from app import storage
from app.config import settings
from app.db import models as m
from app.db.session import connect as db_connect, disconnect as db_disconnect, session_scope
from app.redis_client import connect as redis_connect, disconnect as redis_disconnect, get_client
from app.services import image_job, jobs

logger = logging.getLogger("toontoon.worker")
HEARTBEAT_KEY = "toontoon:worker:heartbeat"
HEARTBEAT_TTL = 30


async def recover() -> int:
    """Вернуть в очередь всё, что не дорисовалось. Возвращает, сколько."""
    async with session_scope() as db:
        rows = (await db.execute(
            select(m.Generation).where(m.Generation.status.in_(("queued", "running")),
                                       m.Generation.deleted_at.is_(None))
        )).scalars().all()
        ids = [r.id for r in rows if jobs.spec_of(r) is not None]
    if ids:
        await get_client().lpush(settings.worker_queue_key, *ids)
        logger.warning("Подобрано недорисованных работ: %d", len(ids))
    return len(ids)


async def handle(gen_id: str) -> bool:
    """Одна работа. Возвращает True, если рисовали."""
    async with session_scope() as db:
        record = await db.get(m.Generation, gen_id)
        if record is None or record.deleted_at is not None:
            logger.info("Работа %s исчезла, пропускаю", gen_id); return False
        if record.status not in ("queued", "running"):
            logger.info("Работа %s уже %s, пропускаю", gen_id, record.status); return False
        spec = jobs.spec_of(record)
        if spec is None:
            logger.warning("У работы %s нет спецификации — рисовать нечем", gen_id); return False
        try:
            kwargs = await jobs.kwargs_from_spec(db, spec)
        except LookupError as exc:
            logger.warning("Работа %s: %s", gen_id, exc); return False
    await image_job.run_image_job(**kwargs)
    return True


async def heartbeat() -> None:
    await get_client().set(HEARTBEAT_KEY, "1", ex=HEARTBEAT_TTL)


async def _pulse(stop: asyncio.Event) -> None:
    """Пульс — своей задачей, а не между работами: пока рисуется долгий кадр,
    цикл ниже стоит в `await`, и сторож решил бы, что воркер умер."""
    while not stop.is_set():
        try:
            await heartbeat()
        except Exception:  # noqa: BLE001 — Redis моргнул; следующий удар через секунды
            logger.warning("Пульс не записался")
        await asyncio.sleep(HEARTBEAT_TTL / 3)


async def loop(stop: asyncio.Event) -> None:
    """Забирать заказы и рисовать до `worker_concurrency` кадров разом.

    Семафор берётся ДО того, как заказ снят с очереди: иначе воркер набрал бы
    в память всю очередь, и падение унесло бы их все — а подбор при старте
    вернул бы только те, что успели попасть в строку как queued/running.
    """
    redis = get_client()
    slots = asyncio.Semaphore(max(1, settings.worker_concurrency))
    inflight: set[asyncio.Task] = set()
    pulse = asyncio.create_task(_pulse(stop))

    async def one(gen_id: str) -> None:
        try:
            await handle(gen_id)
        except Exception:  # noqa: BLE001 — одна плохая работа не должна ронять воркер
            logger.exception("Работа %s уронила бы воркер — идём дальше", gen_id)
        finally:
            slots.release()

    try:
        while not stop.is_set():
            await slots.acquire()
            item = await redis.brpop(settings.worker_queue_key, timeout=5)
            if not item:
                slots.release()
                continue
            _, gen_id = item
            task = asyncio.create_task(one(gen_id))
            inflight.add(task)
            task.add_done_callback(inflight.discard)
    finally:
        # Остановка — дорисовать то, что в руках: заказ уже снят с очереди, и
        # бросить его значит отдать сверке через полчаса.
        if inflight:
            logger.info("Останавливаюсь: дорисовываю %d", len(inflight))
            await asyncio.gather(*inflight, return_exceptions=True)
        pulse.cancel()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    await redis_connect(); await db_connect(); await storage.startup()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    await recover()
    logger.info("Воркер слушает очередь %s", settings.worker_queue_key)
    try:
        await loop(stop)
    finally:
        await db_disconnect(); await redis_disconnect()


if __name__ == "__main__":
    asyncio.run(main())
