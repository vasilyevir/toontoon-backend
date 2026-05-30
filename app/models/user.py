from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class AuthProvider(str, Enum):
    MAGIC = "magic"
    BOOSTIFY = "boostify"


class User(BaseModel):
    """A persisted ARTEKI user. Stored in Redis under ``user:{id}``."""

    id: str
    provider: AuthProvider = AuthProvider.MAGIC
    email: Optional[str] = None
    name: Optional[str] = None
    avatar: Optional[str] = None

    # Local wallet — only authoritative for magic-link users. For Boostify
    # users the balance is always read live from Boostify and this is ignored.
    teki_balance: int = 0

    # Link back to the Boostify identity when provider == boostify.
    boostify_user_id: Optional[str] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PublicUser(BaseModel):
    """Shape returned to the frontend (no secrets)."""

    id: str
    provider: AuthProvider
    email: Optional[str] = None
    name: Optional[str] = None
    avatar: Optional[str] = None
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "PublicUser":
        return cls(
            id=user.id,
            provider=user.provider,
            email=user.email,
            name=user.name,
            avatar=user.avatar,
            created_at=user.created_at,
        )


# ─── Request bodies ───────────────────────────────────────────────────────────


class MagicLinkRequest(BaseModel):
    email: EmailStr


class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
