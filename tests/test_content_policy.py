"""Политика контента: что мы не рисуем, кем бы ни был заказчик.

Проверяется решение, а не модель: модель подменяется заготовленным ответом,
и тест смотрит, что мы с этим ответом делаем. Отдельно — что запоминается на
снимке и что считается отказом провайдера.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_content_policy.py -q
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app import redis_client
from app.config import settings
from app.db import models as m
from app.db.session import connect, disconnect, get_factory
from app.services import gpt, policy

pytestmark = pytest.mark.asyncio

T = policy.TextVerdict
P = policy.PhotoVerdict


# ─── Решение по словам ───────────────────────────────────────────────────────

def test_nudity_in_words_is_refused_before_anything_else():
    with pytest.raises(policy.PolicyRefusal) as exc:
        policy.judge_text(T(nudity=True, public_figure=True))
    assert exc.value.code == "nudity"


def test_a_named_public_figure_is_refused_even_for_a_verified_one():
    """Флаг помогает на снимке, но не в тексте: «сделай меня как Бекхэма»
    не нужно и самому Бекхэму."""
    with pytest.raises(policy.PolicyRefusal) as exc:
        policy.judge_text(T(public_figure=True))
    assert exc.value.code == "public_figure"
    assert settings.support_email in exc.value.detail


def test_a_child_in_words_is_fine_until_the_words_are_about_the_body():
    policy.judge_text(T(minor=True))
    with pytest.raises(policy.PolicyRefusal) as exc:
        policy.judge_text(T(minor=True, revealing=True))
    assert exc.value.code == "minor"


def test_deceptive_scenes_are_refused_and_clean_text_passes():
    with pytest.raises(policy.PolicyRefusal):
        policy.judge_text(T(deceptive=True))
    policy.judge_text(T())


# ─── Решение по снимкам ──────────────────────────────────────────────────────

def test_a_child_is_allowed_only_in_safe_directions():
    policy.judge_photos([P(minor=True)], text=T(), category="cartoon_me")
    policy.judge_photos([P(minor=True)], text=T(), category="family_fun")
    with pytest.raises(policy.PolicyRefusal) as exc:
        policy.judge_photos([P(minor=True)], text=T(), category="glow_up")
    assert exc.value.code == "minor"
    with pytest.raises(policy.PolicyRefusal):
        policy.judge_photos([P(minor=True)], text=T(), category="paparazzi_flash")


def test_a_child_in_a_free_prompt_is_fine_unless_the_words_are_about_the_body():
    policy.judge_photos([P(minor=True)], text=T(), category=None)
    with pytest.raises(policy.PolicyRefusal) as exc:
        policy.judge_photos([P(minor=True)], text=T(revealing=True), category=None)
    assert exc.value.code == "minor"


def test_a_public_figure_on_the_photo_is_refused_unless_verified():
    with pytest.raises(policy.PolicyRefusal) as exc:
        policy.judge_photos([P(public_figure=True)], text=T(), category="ai_photo_studio")
    assert exc.value.code == "public_figure"
    # Поддержка проверила, что это он сам, — снимок проходит.
    policy.judge_photos([P(public_figure=True)], text=T(), category="ai_photo_studio",
                        verified_public_figure=True)


def test_nudity_on_any_photo_is_refused_regardless_of_the_flag():
    with pytest.raises(policy.PolicyRefusal) as exc:
        policy.judge_photos([P(), P(nudity=True)], text=T(), category=None,
                            verified_public_figure=True)
    assert exc.value.code == "nudity"


# ─── Разбор ответа модели ────────────────────────────────────────────────────

@pytest.fixture
def модель(monkeypatch):
    sent: dict = {}

    def отвечает(payload):
        async def _call(messages, **kwargs):
            sent["messages"] = messages
            if isinstance(payload, Exception):
                raise payload
            return payload
        monkeypatch.setattr(gpt, "_call", _call)
        monkeypatch.setattr(settings, "policy_enabled", True)
        # `openai_enabled` — свойство от ключа; подменяем свойство, не значение.
        monkeypatch.setattr(type(settings), "openai_enabled", property(lambda self: True))
        return sent
    return отвечает


async def test_text_verdict_takes_only_true_and_ignores_unknown_fields(модель):
    модель(json.dumps({"nudity": "yes", "public_figure": True, "extra": True}))
    v = await policy.screen_text("me as a famous politician")
    assert v.public_figure is True and v.nudity is False


async def test_when_the_model_is_down_the_dictionary_still_catches_the_obvious(модель):
    модель(RuntimeError("витрина легла"))
    assert (await policy.screen_text("сделай меня голой на пляже")).nudity is True
    assert (await policy.screen_text("me on a rooftop at sunset")).nudity is False


async def test_empty_text_does_not_call_the_model(модель):
    sent = модель(json.dumps({"nudity": True}))
    assert await policy.screen_text("   ") == T()
    assert "messages" not in sent


async def test_photo_verdict_none_when_the_model_is_down(модель):
    модель(RuntimeError("зрение недоступно"))
    assert await policy.screen_photo(b"\x89PNG") is None


def test_text_of_gathers_words_and_answers():
    assert policy.text_of("a", None, " b ", answers={"x": "c", "y": ""}) == "a b c"


def test_provider_moderation_is_recognised_by_its_marks():
    assert policy.looks_like_moderation("fal HTTP 422: content policy violation")
    assert policy.looks_like_moderation("OpenAI: request was flagged by safety system")
    assert not policy.looks_like_moderation("ReadTimeout('')")


# ─── Память о снимке и счётчик отказов ───────────────────────────────────────

@pytest_asyncio.fixture
async def база():
    settings.use_fake_redis = True
    await redis_client.connect()
    await connect()
    async with get_factory()() as session:
        user = m.User(kind="guest")
        session.add(user)
        await session.flush()
        asset = m.MediaAsset(user_id=user.id, kind="upload", storage_key="t/p.png",
                             mime="image/png", bytes=3)
        session.add(asset)
        await session.flush()
        yield session, user, asset
        await session.rollback()
        await session.execute(delete(m.MediaAsset).where(m.MediaAsset.user_id == user.id))
        await session.execute(delete(m.User).where(m.User.id == user.id))
        await session.commit()
    await disconnect()
    await redis_client.disconnect()


async def test_a_real_verdict_is_remembered_on_the_asset_and_not_asked_twice(база, monkeypatch):
    session, _, asset = база
    calls = []

    async def смотрит(data):
        calls.append(data)
        return P(minor=True)
    monkeypatch.setattr(policy, "screen_photo", смотрит)

    first = await policy.screen_asset(session, asset, b"png")
    second = await policy.screen_asset(session, asset, b"png")
    assert first.minor and second.minor
    assert len(calls) == 1, "второй кадр с тем же снимком зрение звать не должен"
    assert asset.screening == {"nudity": False, "minor": True, "public_figure": False,
                               "v": policy.SCREENING_VERSION}


async def test_not_having_looked_is_not_remembered_as_clean(база, monkeypatch):
    session, _, asset = база

    async def не_посмотрело(data):
        return None
    monkeypatch.setattr(policy, "screen_photo", не_посмотрело)

    assert await policy.screen_asset(session, asset, b"png") == P()
    assert asset.screening is None, "«не посмотрели» нельзя запомнить как «чисто»"


async def test_refusals_add_up_to_a_day_long_block(база, monkeypatch):
    _, user, _ = база
    monkeypatch.setattr(settings, "policy_refusals_per_day", 3)
    assert not await policy.is_blocked(user.id)
    for _ in range(3):
        await policy.note_refusal(user.id)
    assert await policy.is_blocked(user.id)
