from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class AuthProvider(str, Enum):
    MAGIC = "magic"
    BOOSTIFY = "boostify"
    EMAIL = "email"
    GOOGLE = "google"
    APPLE = "apple"
    # An anonymous user with a real session: balance, history and chat all work
    # before sign-in, and are merged into the account afterwards (CH-16).
    GUEST = "guest"


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

    # Argon2 password hash — only set for provider == email. Never exposed.
    password_hash: Optional[str] = None

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
    def from_row(cls, row, *, provider: AuthProvider) -> "PublicUser":
        """Build from a PostgreSQL row.

        The row has no ``provider`` column — a person can sign in several ways
        (``auth_identities``), so which one they used is a property of the
        session, not of the account.
        """
        return cls(
            id=row.id,
            provider=provider,
            email=row.email,
            name=row.name,
            avatar=row.avatar_key,
            created_at=row.created_at,
        )

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


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class AppleAuthRequest(BaseModel):
    """Body for POST /api/auth/oauth/apple.

    The app completes ``ASAuthorizationAppleIDProvider`` locally and hands us
    the resulting identity_token (a JWT signed by Apple) to verify. ``name``
    is only ever sent by Apple to the client on the FIRST authorization ever
    (never to the server, never again after) — the client should pass it
    through here so we can store it on first login.
    """
    identity_token: str = Field(min_length=10)
    name: Optional[str] = Field(default=None, max_length=120)


class AuthResult(BaseModel):
    """Returned by register/login so mobile clients get the session token in JSON
    (the cookie is also set, for the web)."""
    user: PublicUser
    session_token: str


class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    avatar: Optional[str] = Field(default=None, max_length=2048)
