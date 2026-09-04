"""Amplitude Agent Analytics: сессии, реплики, ответы моделей, данные для отчётов."""
import pytest
from amplitude_ai import (PROP_AGENT_ID, PROP_COST_USD, PROP_INPUT_TOKENS, PROP_LATENCY_MS, PROP_MODEL_NAME,
                          PROP_OUTPUT_TOKENS, PROP_PROVIDER, PROP_SESSION_ID)
from amplitude_ai.testing import MockAmplitudeAI

from app.services import agent_analytics as aa


@pytest.fixture
def mock(monkeypatch):
    # Ключ из .env в тестах не нужен: иначе выключенный режим поднял бы
    # настоящий клиент и слал бы тестовые события в проект.
    monkeypatch.setattr("app.services.agent_analytics.settings.amplitude_ai_api_key", "")
    m = MockAmplitudeAI()
    aa.configure(m)
    yield m
    aa.configure(None)


async def test_chat_session_emits_user_and_ai_messages(mock):
    async with aa.session(aa.CHAT, user_id="user-0001") as s:
        assert s is not None
        aa.user_said("нарисуй меня на закате")
        aa.model_answered(content="Готово: закат, тёплый свет.", model="gpt-4.1-mini",
                          provider="openrouter", latency_ms=420.0,
                          usage={"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150})
    mock.assert_event_tracked("[Agent] User Message", **{PROP_AGENT_ID: aa.CHAT})
    mock.assert_event_tracked("[Agent] AI Response", **{PROP_AGENT_ID: aa.CHAT})
    mock.assert_session_closed("chat-user-0001")


async def test_studio_session_id_is_stable_per_user(mock):
    for _ in range(2):
        async with aa.session(aa.STUDIO, user_id="user-0007"):
            aa.model_answered(content="prompt", model="gpt-4.1-mini", provider="openai",
                              latency_ms=10.0, usage={"prompt_tokens": 5, "completion_tokens": 3,
                                                       "total_tokens": 8})
    ids = {e.event_properties[PROP_SESSION_ID] for e in mock.get_events("[Agent] AI Response")}
    assert ids == {"studio-user-0007"}


async def test_data_quality_gate(mock):
    """Семь полей, без которых отчёты Agent Analytics пустые."""
    async with aa.session(aa.CHAT, user_id="user-0001"):
        aa.user_said("привет")
        aa.model_answered(content="привет!", model="gpt-4o-mini", provider="openai",
                          latency_ms=150.0, usage={"prompt_tokens": 12, "completion_tokens": 4,
                                                   "total_tokens": 16})
    events = mock.get_events("[Agent] AI Response")
    assert events
    for e in events:
        p = e.event_properties or {}
        assert e.user_id or e.device_id
        assert p.get(PROP_SESSION_ID)
        assert p.get(PROP_MODEL_NAME) and p.get(PROP_PROVIDER)
        assert p.get(PROP_LATENCY_MS, 0) > 0
        assert p.get(PROP_INPUT_TOKENS, 0) > 0 and p.get(PROP_OUTPUT_TOKENS, 0) > 0
        assert p.get(PROP_COST_USD) is not None


async def test_outside_session_and_disabled_are_silent(mock):
    aa.model_answered(content="x", model="gpt-4o-mini", provider="openai", latency_ms=1.0)
    aa.user_said("x")
    assert not mock.get_events("[Agent] AI Response")
    aa.configure(None)
    async with aa.session(aa.CHAT, user_id="user-0001") as s:   # ключа в тестах нет → выключено
        assert s is None or aa.enabled()


def test_canonical_model():
    assert aa.canonical_model("openai/gpt-4.1-mini") == ("gpt-4.1-mini", "openrouter")
    assert aa.canonical_model("gpt-4o-mini") == ("gpt-4o-mini", "openai")
