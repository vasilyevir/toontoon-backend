"""Amplitude Agent Analytics: сессии разговора и ответы моделей.

Один вызов модели у нас — `gpt._call`; он и сообщает об ответе. Сессии
открывают обработчики (чат, генерация, идеи, профили, воркер), потому что
только они знают, кто человек и к какому разговору относится вызов. Разговор
у человека один (см. routers/chat.py), поэтому id сессии стабилен:
`chat-<user>` для переписки и `studio-<user>` для конвейера кадра.

Без ключа `AMPLITUDE_AI_API_KEY` всё здесь — пустые операции: сессия не
открывается, ответы не уходят, приложение не замечает разницы.

Что уезжает в Amplitude при `content_mode=full`: текст реплик и ответов
моделей с вырезанными почтами/телефонами (`redact_pii`). Снимки не уезжают —
`_call` отдаёт только текст. Amplitude перечислен в политике
конфиденциальности как обработчик; менять режим на `metadata_only` можно
настройкой `AMPLITUDE_CONTENT_MODE`, тогда уходят только метрики.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator, Optional

from app.config import settings

log = logging.getLogger(__name__)

CHAT = "toontoon-chat"      # переписка: реплики человека и ответы
STUDIO = "toontoon-studio"  # конвейер кадра: сборка промпта, разбор, сторож, идеи

_ai = None
_agents: dict = {}
_current: ContextVar[Optional[object]] = ContextVar("toontoon_agent_session", default=None)


def configure(ai) -> None:
    """Подставить готовый клиент (тесты: MockAmplitudeAI). None — выключить."""
    global _ai
    _ai = ai
    _agents.clear()


def _client():
    global _ai
    if _ai is not None:
        return _ai
    if not settings.amplitude_ai_api_key:
        return None
    try:
        from amplitude import Amplitude, Config
        from amplitude_ai import AIConfig, AmplitudeAI

        amplitude = Amplitude(settings.amplitude_ai_api_key,
                              configuration=Config(server_zone=settings.amplitude_server_zone))
        _ai = AmplitudeAI(amplitude=amplitude,
                          config=AIConfig(content_mode=settings.amplitude_content_mode,
                                          redact_pii=True))
    except Exception:  # pragma: no cover - неверный ключ/зона не должны ронять сервер
        log.exception("Amplitude Agent Analytics не запустился — выключен")
        _ai = None
    return _ai


def enabled() -> bool:
    return _client() is not None


def _agent(agent_id: str):
    # Агент — на уровне модуля, один на процесс: новый объект на каждый запрос
    # давал бы разные Agent ID и рвал сессии в отчётах.
    if agent_id not in _agents:
        _agents[agent_id] = _client().agent(agent_id)
    return _agents[agent_id]


class _Lazy:
    """Сессия, которая открывается в Amplitude только при первом событии.

    Обработчик открывает её на каждый запрос, но до модели доходит не каждый
    (ответ из кэша, ранний выход). Без этого в отчёте копились бы сессии из
    одного `Session End` без единой реплики.
    """

    def __init__(self, agent_id: str, user_id: str, session_id: str) -> None:
        self.agent_id, self.user_id, self.session_id = agent_id, user_id, session_id
        self.opened = None


def current():
    """Активная сессия этого запроса (ленивая), если обработчик её открыл."""
    return _current.get()


def _opened_now():
    """Открыть SDK-сессию синхронно из трекинга: `__aenter__` у SDK без await-ов
    кроме установки контекста, поэтому его можно прогнать сразу."""
    lazy = current()
    if lazy is None:
        return None
    if lazy.opened is None:
        s = _agent(lazy.agent_id).session(user_id=lazy.user_id, session_id=lazy.session_id,
                                           idle_timeout_minutes=30)
        lazy.opened = s.__enter__()
    return lazy.opened


@asynccontextmanager
async def session(agent_id: str, *, user_id: Optional[str],
                  session_id: Optional[str] = None) -> AsyncIterator[Optional[object]]:
    """Открыть (лениво) сессию агента на время обработки запроса.

    Сервер долгоживущий, поэтому после выхода — `flush()`: иначе события
    копятся в памяти до следующего интервала или до перезапуска.
    """
    ai = _client()
    if ai is None or not user_id:
        yield None
        return
    sid = session_id or f"{agent_id.removeprefix('toontoon-')}-{user_id}"
    lazy = _Lazy(agent_id, user_id, sid)
    token = _current.set(lazy)
    try:
        yield lazy
    finally:
        _current.reset(token)
        try:
            if lazy.opened is not None:
                lazy.opened.__exit__(None, None, None)
            ai.flush()
        except Exception:  # pragma: no cover
            log.debug("Amplitude: закрытие сессии не удалось", exc_info=True)


def user_said(text: str) -> None:
    """Реплика человека — только из чата, где она и есть реплика."""
    if not text:
        return
    s = _opened_now()
    if s is None:
        return
    try:
        s.track_user_message(text[:2000])
    except Exception:  # pragma: no cover
        log.debug("Amplitude track_user_message не удался", exc_info=True)


def model_answered(*, content: str, model: str, provider: str, latency_ms: float,
                   usage: Optional[dict] = None, purpose: Optional[str] = None) -> None:
    """Ответ модели. Зовётся из `gpt._call`; вне сессии — молча ничего."""
    s = _opened_now()
    if s is None:
        return
    usage = usage or {}
    text = content[:2000]
    if purpose:
        text = f"[{purpose}] {text}"
    kwargs = {}
    if usage.get("prompt_tokens") is not None:
        kwargs["input_tokens"] = int(usage.get("prompt_tokens") or 0)
        kwargs["output_tokens"] = int(usage.get("completion_tokens") or 0)
        kwargs["total_tokens"] = int(usage.get("total_tokens") or 0)
    try:
        s.track_ai_message(content=text, model=model, provider=provider,
                           latency_ms=max(latency_ms, 1.0), **kwargs)
    except Exception:  # pragma: no cover
        log.debug("Amplitude track_ai_message не удался", exc_info=True)


def canonical_model(name: str) -> tuple[str, str]:
    """`openai/gpt-4.1-mini` с витрины → (`gpt-4.1-mini`, `openrouter`).

    Цену Amplitude считает по каноническому имени модели; префикс вендора
    витрины он не знает.
    """
    if "/" in name:
        return name.split("/", 1)[1], "openrouter"
    return name, "openai"


def in_session(agent_id: str):
    """Декоратор для обработчиков FastAPI с зависимостью `ctx: Context`.

    Открывает сессию агента на пользователя из `ctx` (кортеж user, session)
    и закрывает после ответа. Обработчики без `ctx` в kwargs идут как есть.
    """
    import functools

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            ctx = kwargs.get("ctx")
            user_id = getattr(ctx[0], "id", None) if ctx else None
            async with session(agent_id, user_id=user_id):
                return await fn(*args, **kwargs)
        return wrapper
    return deco
