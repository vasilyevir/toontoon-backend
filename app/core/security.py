"""Token generation and webhook signature helpers."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid


def new_token() -> str:
    """Opaque URL-safe token for magic links / sessions."""
    return secrets.token_urlsafe(32)


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex
    return f"{prefix}{raw}" if prefix else raw


def sign_payload(secret: str, body: bytes) -> str:
    """HMAC-SHA256 hex signature of a raw request body."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    expected = sign_payload(secret, body)
    # Constant-time comparison; tolerate "sha256=" prefixes.
    provided = signature.split("=", 1)[-1].strip()
    return hmac.compare_digest(expected, provided)
