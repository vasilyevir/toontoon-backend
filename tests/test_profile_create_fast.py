"""Заведение профиля: быстро и один раз.

21 сентября 2026 на тестовом телефоне заведение профиля шло десятки секунд и
оставило восемь профилей вместо одного. Причин на сервере было три, и каждая
проверяется здесь отдельно:

* модель смотрела на те же десять снимков дважды — при разборе и при
  заведении; теперь разбор помнится, и заведение берёт готовый;
* снимки читались из хранилища по одному — 2 с вместо 0,3 с;
* повтор того же набора заводил ещё один профиль.

Базы эти проверки не требуют: границы с ней подменены.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_profile_create_fast.py -q
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.routers import profiles


VERDICT = {"photos": [{"index": 1, "ok": True}], "missing": ["full-body"], "chosen": [2, 1]}
IDS = ["med_a", "med_b", "med_c"]


# ── Разбор помнится ──────────────────────────────────────────────────────────

async def test_готовый_разбор_берётся_из_памяти(monkeypatch):
    """Второй раз модель на тот же набор не смотрит."""
    async def помню(user_id, media_ids):
        return VERDICT

    async def модель(images):
        raise AssertionError("модель не должна смотреть второй раз")

    monkeypatch.setattr(profiles, "_recalled", помню)
    monkeypatch.setattr(profiles.gpt_service, "review_profile_photos", модель)
    assert await profiles._verdict(None, "usr_1", IDS) == VERDICT


async def test_без_памяти_смотрит_модель_и_запоминает(monkeypatch):
    запомнено = {}

    async def не_помню(user_id, media_ids):
        return None

    async def помнить(user_id, media_ids, verdict):
        запомнено[(user_id, tuple(media_ids))] = verdict

    async def снимки(db, media_ids):
        return [b"x"] * len(media_ids)

    async def модель(images):
        assert len(images) == len(IDS)
        return VERDICT

    monkeypatch.setattr(profiles, "_recalled", не_помню)
    monkeypatch.setattr(profiles, "_remember", помнить)
    monkeypatch.setattr(profiles, "_images", снимки)
    monkeypatch.setattr(profiles.gpt_service, "review_profile_photos", модель)

    assert await profiles._verdict(None, "usr_1", IDS) == VERDICT
    assert запомнено == {("usr_1", tuple(IDS)): VERDICT}


async def test_пустой_разбор_не_запоминается(monkeypatch):
    """Модель недоступна — следующая попытка вправе посмотреть снова."""
    запомнено = []

    async def не_помню(user_id, media_ids):
        return None

    async def помнить(*args):
        запомнено.append(args)

    async def снимки(db, media_ids):
        return []

    async def модель(images):
        return {"photos": [], "missing": [], "chosen": []}

    monkeypatch.setattr(profiles, "_recalled", не_помню)
    monkeypatch.setattr(profiles, "_remember", помнить)
    monkeypatch.setattr(profiles, "_images", снимки)
    monkeypatch.setattr(profiles.gpt_service, "review_profile_photos", модель)

    await profiles._verdict(None, "usr_1", IDS)
    assert запомнено == []


def test_ключ_памяти_зависит_от_порядка_и_человека():
    """Разбор отвечает номерами снимков: другой порядок — другой разбор."""
    k = profiles._verdict_key
    assert k("usr_1", IDS) == k("usr_1", list(IDS))
    assert k("usr_1", IDS) != k("usr_1", list(reversed(IDS)))
    assert k("usr_1", IDS) != k("usr_2", IDS)


async def test_память_недоступна_не_ломает_разбор(monkeypatch):
    """Redis лёг — разбор всё равно делается, просто без ускорения."""
    def нет_redis():
        raise RuntimeError("Redis client is not initialised")

    monkeypatch.setattr("app.redis_client.get_client", нет_redis)
    assert await profiles._recalled("usr_1", IDS) is None
    await profiles._remember("usr_1", IDS, VERDICT)  # не бросает


# ── Снимки читаются разом ────────────────────────────────────────────────────

async def test_снимки_читаются_разом_и_по_порядку(monkeypatch):
    одновременно = 0
    пик = 0

    class Хранилище:
        async def get(self, key):
            nonlocal одновременно, пик
            одновременно += 1
            пик = max(пик, одновременно)
            await asyncio.sleep(0.01)
            одновременно -= 1
            return key.encode()

    class База:
        async def get(self, model, media_id):
            return SimpleNamespace(storage_key=f"k-{media_id}")

    monkeypatch.setattr(profiles, "get_storage", lambda: Хранилище())
    снимки = await profiles._images(База(), IDS)
    assert снимки == [f"k-{i}".encode() for i in IDS]
    assert пик == len(IDS), "читались по одному, а не разом"


# ── Повтор того же набора ────────────────────────────────────────────────────

async def test_тот_же_набор_возвращает_прежний_профиль(monkeypatch):
    прежний = SimpleNamespace(id="prf_1", name="Илья", kind="person", is_default=True,
                              media_ids=list(reversed(IDS)), reference_ids=[])

    async def подписка(db, user_id):
        return object()

    async def профили(db, user_id):
        return [прежний]

    async def завести(*args, **kwargs):
        raise AssertionError("второй профиль из того же набора заводиться не должен")

    monkeypatch.setattr(profiles.subscriptions_repo, "active_for_user", подписка)
    monkeypatch.setattr(profiles.profiles_repo, "list_for_user", профили)
    monkeypatch.setattr(profiles.profiles_repo, "create", завести)

    ответ = await profiles.create_profile(
        profiles.CreateRequest(name="Илья", media_ids=IDS),
        ctx=(SimpleNamespace(id="usr_1"), None), db=None)
    assert ответ.id == "prf_1"
