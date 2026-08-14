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

from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, status

from app.config import settings
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


def _build_optional_context():
    cookie_name = settings.session_cookie_name

    async def optional_context(
        session_cookie: Optional[str] = Cookie(default=None, alias=cookie_name),
        authorization: Optional[str] = Header(default=None),
    ) -> Optional[Context]:
        # The header wins over the cookie when both are present. A native
        # client sends the Bearer token deliberately, while a cookie can be
        # left over from an earlier session — and losing that race would
        # authenticate someone as the wrong user without any error to notice.
        sid = None
        if authorization and authorization.lower().startswith("bearer "):
            sid = authorization[7:].strip()
        if not sid:
            sid = session_cookie
        if not sid:
            return None
        session = await auth_service.get_session(sid)
        if not session:
            return None

        async with session_scope() as db:
            user = await users_repo.get(db, session.user_id)
            if user is None:
                return None
            # Cheap and useful: abandoned-guest cleanup needs to know who is
            # still around, and this is the one place every request passes.
            await users_repo.touch(db, user.id)
        return user, session

    return optional_context


optional_context = _build_optional_context()


async def required_context(ctx: Optional[Context] = Depends(optional_context)) -> Context:
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return ctx


async def current_user(ctx: Context = Depends(required_context)) -> db_models.User:
    return ctx[0]


async def current_session(ctx: Context = Depends(required_context)) -> Session:
    return ctx[1]
