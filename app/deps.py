"""FastAPI dependencies for auth/session resolution.

The session id lives in the ``arteki-session`` cookie (name is configurable via
``settings.session_cookie_name``). We build the cookie-reading dependency once at
import time so the configured name is bound into the OpenAPI schema.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, status

from app.config import settings
from app.models.session import Session
from app.models.user import User
from app.services import auth_service

Context = tuple[User, Session]


def _build_optional_context():
    cookie_name = settings.session_cookie_name

    async def optional_context(
        session_cookie: Optional[str] = Cookie(default=None, alias=cookie_name),
        authorization: Optional[str] = Header(default=None),
    ) -> Optional[Context]:
        # The web sends the session id as a cookie. Native / third-party
        # clients that can't (or shouldn't have to) manage a cookie jar send
        # the exact same session id as a Bearer token instead — it's the
        # same underlying Redis-backed session either way, so every route
        # that already depends on this function (accounts AND generation
        # endpoints) works identically for both, with zero per-route changes.
        sid = session_cookie
        if not sid and authorization and authorization.lower().startswith("bearer "):
            sid = authorization[7:].strip()
        if not sid:
            return None
        session = await auth_service.get_session(sid)
        if not session:
            return None
        user = await auth_service.get_user(session.user_id)
        if not user:
            return None
        return user, session

    return optional_context


optional_context = _build_optional_context()


async def required_context(ctx: Optional[Context] = Depends(optional_context)) -> Context:
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return ctx


async def current_user(ctx: Context = Depends(required_context)) -> User:
    return ctx[0]


async def current_session(ctx: Context = Depends(required_context)) -> Session:
    return ctx[1]
