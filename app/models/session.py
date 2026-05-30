from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.models.user import AuthProvider


class Session(BaseModel):
    """Server-side session stored in Redis under ``session:{sid}``.

    The session id itself lives in the ``arteki-session`` http-only cookie.
    """

    sid: str
    user_id: str
    provider: AuthProvider

    # Boostify OAuth tokens (only present for provider == boostify).
    boostify_access_token: Optional[str] = None
    boostify_refresh_token: Optional[str] = None
    boostify_access_expires_at: Optional[float] = None  # epoch seconds
