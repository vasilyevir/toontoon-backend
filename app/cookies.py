"""Helpers for setting/clearing the session cookie consistently."""
from __future__ import annotations

from fastapi import Response

from app.config import settings


def set_session_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=sid,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite=settings.session_cookie_samesite,
        secure=settings.session_cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        samesite=settings.session_cookie_samesite,
        secure=settings.session_cookie_secure,
    )
