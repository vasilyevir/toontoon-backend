"""Helpers for setting/clearing the session cookie consistently."""
from __future__ import annotations

from fastapi import Response

from app.config import settings


def _secure() -> bool:
    """Страховка: за https кука `Secure` независимо от флага — забытый флаг не
    должен отдавать её по http."""
    return settings.session_cookie_secure or settings.public_base_url.startswith("https://")


def set_session_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=sid,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite=settings.session_cookie_samesite,
        secure=_secure(),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        samesite=settings.session_cookie_samesite,
        secure=_secure(),
    )
