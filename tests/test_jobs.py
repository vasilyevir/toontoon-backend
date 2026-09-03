"""Заказ на кадр как данные: спецификация, очередь, подбор после падения.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_jobs.py -q
"""
from __future__ import annotations

import io

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete

from app import storage
from app.config import settings
from app.db import models as m
from app.db.repositories import media as media_repo
from app.db.session import connect, disconnect, get_factory
from app.redis_client import connect as rconnect, disconnect as rdisconnect, get_client
from app.services import image_job, jobs
from app.services.generation.operations import GenerationRequest, Operation

pytestmark = pytest.mark.asyncio
ОЧЕРЕДЬ = "toontoon:jobs:test"


def картинка() -> bytes:
    b = io.BytesIO(); Image.new("RGB", (24, 24), (90, 120, 150)).save(b, "PNG"); return b.getvalue()


@pytest_asyncio.fixture
async def db(monkeypatch):
    monkeypatch.setattr(settings, "worker_queue_key", ОЧЕРЕДЬ, raising=False)
    # Свежий клиент Redis на КАЖДЫЙ тест: предыдущий файл мог оставить свой,
    # привязанный к уже закрытому циклу событий, — и первый же brpop/lpush
    # упадёт с «attached to a different loop».
    await rdisconnect(); await rconnect(); await connect(); await storage.startup()
    await get_client().delete(ОЧЕРЕДЬ)
    factory = get_factory()
    async with factory() as session:
        user = m.User(kind="guest"); session.add(user); await session.flush(); uid = user.id
        await session.commit()
        try:
            yield session, uid
        finally:
            await session.execute(delete(m.Generation).where(m.Generation.user_id == uid))
            await session.execute(delete(m.MediaAsset).where(m.MediaAsset.user_id == uid))
            await session.execute(delete(m.User).where(m.User.id == uid))
            await session.commit()
    await get_client().delete(ОЧЕРЕДЬ)
    await disconnect()
    # Redis-клиент тоже привязан к циклу событий этого теста: не закрыть его —
    # значит отдать следующему тесту соединение из мёртвого цикла.
    await rdisconnect()


def _вызов(gen_id, uid, photo_id):
    req = GenerationRequest(operation=Operation.IMAGE_TO_IMAGE, prompt="scene", negative_prompt="neg",
                            image=b"x", image_mime="image/png", params={"aspect": "9:16"})
    kwargs = dict(gen_id=gen_id, user_id=uid, payment_id="pay_t", payment_amount=10, request=req,
                  prompt="полный промпт", prefer="fal_gpt2_edit", sample_brands=["acme"],
                  check_drawn=False, from_chat=True, said="сделай")
    spec = jobs.spec_from_call(photo_media_id=photo_id, extra_media_ids=[], **kwargs)
    return kwargs, spec


async def _работа(session, uid):
    g = m.Generation(user_id=uid, operation="image_to_image", status="queued", cost=10, request_params={})
    session.add(g); await session.flush(); gid = g.id; await session.commit(); return gid


def test_спецификация_без_байтов_и_обратимая():
    kwargs, spec = _вызов("gen_x", "usr_x", "med_x")
    rec = spec.to_record()
    assert "image" not in rec and rec["photo_media_id"] == "med_x" and rec["style_prompt"] == "полный промпт"
    assert jobs.JobSpec.from_record(rec) == spec


async def test_очередь_кладёт_спецификацию_в_строку_и_id_в_redis(db):
    session, uid = db
    gid = await _работа(session, uid)
    _, spec = _вызов(gid, uid, None)
    await jobs.enqueue(session, spec)
    row = await session.get(m.Generation, gid); await session.refresh(row)
    assert row.request_params["job"]["gen_id"] == gid
    assert await get_client().rpop(ОЧЕРЕДЬ) == gid


async def test_воркер_собирает_аргументы_из_хранилища_и_зовёт_ту_же_функцию(db, monkeypatch):
    session, uid = db
    asset = await media_repo.save_image(session, user_id=uid, kind="upload", data=картинка(), make_thumbnail=False)
    await session.commit()
    gid = await _работа(session, uid)
    kwargs, spec = _вызов(gid, uid, asset.id)
    await jobs.enqueue(session, spec)
    увидел = {}
    async def подмена(**kw): увидел.update(kw)
    monkeypatch.setattr(image_job, "run_image_job", подмена)
    from app import worker
    assert await worker.handle(gid) is True
    assert увидел["gen_id"] == gid and увидел["prompt"] == "полный промпт" and увидел["prefer"] == "fal_gpt2_edit"
    assert увидел["request"].image and увидел["request"].image_mime.startswith("image/")
    assert увидел["request"].params == {"aspect": "9:16"} and увидел["said"] == "сделай"


async def test_подбор_возвращает_недорисованное_в_очередь(db):
    session, uid = db
    gid = await _работа(session, uid)
    _, spec = _вызов(gid, uid, None)
    await jobs.enqueue(session, spec)
    await get_client().delete(ОЧЕРЕДЬ)          # «упали» — очередь пуста, строка осталась
    from app import worker
    n = await worker.recover()
    assert n >= 1 and gid in (await get_client().lrange(ОЧЕРЕДЬ, 0, -1))


async def test_готовую_работу_воркер_не_трогает(db, monkeypatch):
    session, uid = db
    gid = await _работа(session, uid)
    row = await session.get(m.Generation, gid); row.status = "done"; await session.commit()
    monkeypatch.setattr(image_job, "run_image_job", None)   # позвать нельзя — упадёт
    from app import worker
    assert await worker.handle(gid) is False


async def test_умолчание_inline_не_меняет_прежний_путь(db, monkeypatch):
    session, uid = db
    monkeypatch.setattr(settings, "worker_mode", "inline", raising=False)
    вызвано = {}
    monkeypatch.setattr(image_job, "schedule", lambda **kw: вызвано.update(kw))
    kwargs, spec = _вызов("gen_i", uid, None)
    assert await jobs.dispatch(session, spec, **kwargs) == "inline"
    assert вызвано["gen_id"] == "gen_i"
    assert await get_client().llen(ОЧЕРЕДЬ) == 0


async def test_воркер_рисует_несколько_кадров_разом(monkeypatch):
    """Три заказа по 0,3 с при concurrency=3 — за ~0,3 с, а не ~0,9."""
    import asyncio, time
    from app import worker
    monkeypatch.setattr(settings, "worker_concurrency", 3, raising=False)
    monkeypatch.setattr(settings, "worker_queue_key", "toontoon:jobs:conc", raising=False)
    from app.redis_client import connect as rc, disconnect as rd, get_client
    await rd(); r = await rc()
    await r.delete("toontoon:jobs:conc")
    await r.lpush("toontoon:jobs:conc", "a", "b", "c")
    одновременно = {"сейчас": 0, "макс": 0}
    async def handle(gen_id):
        одновременно["сейчас"] += 1; одновременно["макс"] = max(одновременно["макс"], одновременно["сейчас"])
        await asyncio.sleep(0.3); одновременно["сейчас"] -= 1
    monkeypatch.setattr(worker, "handle", handle)
    stop = asyncio.Event()
    t = time.monotonic()
    task = asyncio.create_task(worker.loop(stop))
    while (await r.llen("toontoon:jobs:conc")) or одновременно["сейчас"]:
        await asyncio.sleep(0.05)
    stop.set(); await task
    assert одновременно["макс"] == 3, "заказы должны идти параллельно"
    assert time.monotonic() - t < 0.9 + 5.5, "остановка ждёт brpop не дольше его таймаута"
    await rd()


async def test_воркер_не_набирает_больше_слотов(monkeypatch):
    import asyncio
    from app import worker
    monkeypatch.setattr(settings, "worker_concurrency", 2, raising=False)
    monkeypatch.setattr(settings, "worker_queue_key", "toontoon:jobs:cap", raising=False)
    from app.redis_client import connect as rc, disconnect as rd
    await rd(); r = await rc()
    await r.delete("toontoon:jobs:cap"); await r.lpush("toontoon:jobs:cap", "a", "b", "c", "d")
    одновременно = {"сейчас": 0, "макс": 0}
    async def handle(gen_id):
        одновременно["сейчас"] += 1; одновременно["макс"] = max(одновременно["макс"], одновременно["сейчас"])
        await asyncio.sleep(0.2); одновременно["сейчас"] -= 1
    monkeypatch.setattr(worker, "handle", handle)
    stop = asyncio.Event(); task = asyncio.create_task(worker.loop(stop))
    while (await r.llen("toontoon:jobs:cap")) or одновременно["сейчас"]:
        await asyncio.sleep(0.05)
    stop.set(); await task
    assert одновременно["макс"] == 2
    await rd()


async def test_пульс_видит_воркер_даже_без_подключённого_redis(monkeypatch):
    """Скрипт пульса Redis не подключает; проверка обязана подключиться сама."""
    from app.services import diagnosis
    import app.redis_client as rc
    class Fake:
        async def exists(self, k): return 0
        async def llen(self, k): return 3
    def сломан(): raise RuntimeError("not initialised")
    async def подключить(): return Fake()
    monkeypatch.setattr(settings, "worker_mode", "queue", raising=False)
    monkeypatch.setattr(rc, "get_client", сломан); monkeypatch.setattr(rc, "connect", подключить)
    facts = await diagnosis._worker_facts()
    assert facts == {"воркер_жив": False, "в_очереди": 3}
