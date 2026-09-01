"""FastAPI dependencies for auth/session resolution.

The session id lives in the ``toontoon-session`` cookie (name is configurable via
``settings.session_cookie_name``) or in an ``Authorization: Bearer`` header —
the web uses the first, native clients the second, and both resolve to the same
Redis-backed session.

The **identity** behind that session now comes from PostgreSQL. Sessions stayed
in Redis on purpose: they are short-lived and TTL-driven, which is exactly what
Redis is for. Everything that must survive a restart moved.
"""
from __future__ import annotations

from datetime import timezone
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, status

from app.config import settings
from app.core import rate_limit
from app.db import models as db_models
from app.db.repositories import users as users_repo
from app.db.session import session_scope
from app.models.session import Session
from app.services import auth_service

# (user row, session). The user is a detached SQLAlchemy row — the session that
# loaded it is closed, and ``expire_on_commit=False`` keeps its attributes
# readable. Routers that need to WRITE take their own session via
# ``Depends(get_session)``.
Context = tuple[db_models.User, Session]


def session_id_from(authorization: Optional[str], session_cookie: Optional[str]) -> Optional[str]:
    """Какую сессию имел в виду этот запрос.

    Заголовок побеждает куку, когда есть оба. Родной клиент шлёт Bearer
    намеренно, а кука может остаться от прежнего входа — и проиграв эту гонку,
    мы опознали бы человека как другого, без единой ошибки, которую видно.

    Общей функцией, потому что порядок разошёлся. Здесь заголовок побеждал
    куку, а в выходе — кука побеждала заголовок: клиент с обоими работал под
    сессией из заголовка, а «выйти» гасило сессию из куки. Заголовочная
    оставалась жить, то есть выход не выходил.
    """
    if authorization and authorization.lower().startswith("bearer "):
        сид = authorization[7:].strip()
        if сид:
            return сид
    return session_cookie or None


def _build_optional_context():
    cookie_name = settings.session_cookie_name

    async def optional_context(
        session_cookie: Optional[str] = Cookie(default=None, alias=cookie_name),
        authorization: Optional[str] = Header(default=None),
    ) -> Optional[Context]:
        sid = session_id_from(authorization, session_cookie)
        if not sid:
            return None
        session = await auth_service.get_session(sid)
        if not session:
            return None

        async with session_scope() as db:
            user = await users_repo.get(db, session.user_id)
            if user is None:
                return None
            # Сессия, выданная до последней смены пароля, больше не пускает.
            # Сама она живёт в Redis до конца своего TTL — тридцать дней, — и
            # без этой сверки смена пароля не выгоняла бы того, кто уже внутри.
            if _issued_before_the_cutoff(session, user):
                return None
            # Cheap and useful: abandoned-guest cleanup needs to know who is
            # still around, and this is the one place every request passes.
            await users_repo.touch(db, user.id)
        return user, session

    return optional_context


def _issued_before_the_cutoff(session: Session, user: db_models.User) -> bool:
    """Выдана ли сессия раньше, чем человек отозвал всё выданное."""
    cutoff = user.sessions_valid_from
    if cutoff is None:
        return False
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    return session.issued_at < cutoff.timestamp()


optional_context = _build_optional_context()


async def required_context(ctx: Optional[Context] = Depends(optional_context)) -> Context:
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return ctx


async def costs_money(ctx: Context = Depends(required_context)) -> Context:
    """То же, что `required_context`, но со счётчиком: ручка платит за модель.

    Зависимостью, а не строкой в каждой ручке. Строку в новой ручке забудут —
    и забыли: разговор, зрение по снимку, разбор набора фотографий и идеи к
    кадру не имели ограничителя вовсе, хотя каждый вызов там стоит денег.
    Разбор набора — до пятнадцати картинок в зрение за один запрос.

    Само по себе это было бы полбеды, но гостей заводили без счёта, а значит
    и бесплатных аккаунтов было сколько угодно. Ограничитель здесь считает по
    человеку; тому, что человек стоит дороже одного запроса, служит счётчик на
    заведении гостя.

    Возвращает тот же `Context`, поэтому в ручке меняется одно слово.
    """
    user, _ = ctx
    allowed, _remaining = await rate_limit.hit(
        f"model:{user.id}", settings.model_calls_per_hour, 3600
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком часто. Попробуйте через несколько минут.",
        )
    return ctx


async def current_user(ctx: Context = Depends(required_context)) -> db_models.User:
    return ctx[0]


async def current_session(ctx: Context = Depends(required_context)) -> Session:
    return ctx[1]
