"""Object storage.

Files never live next to the app again: results, uploads and masks go into a
private bucket, and clients receive short-lived signed links instead of paths.
The database stores object keys, so swapping the provider — or putting a CDN in
front — does not touch a single row.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.core.security import new_id
from app.storage.base import Storage
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage

_storage: Optional[Storage] = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = S3Storage() if settings.storage_backend == "s3" else LocalStorage()
    return _storage


async def startup() -> None:
    """Prepare the bucket. Safe to call on every boot."""
    storage = get_storage()
    if isinstance(storage, S3Storage):
        await storage.ensure_bucket()


def make_key(*, user_id: str, kind: str, ext: str) -> str:
    """Object key: kind/user/YYYY/MM/id.ext.

    Grouping by kind and month keeps a bucket listing readable when it holds
    millions of objects, and keeps one user's files together for account
    deletion.
    """
    now = datetime.now(timezone.utc)
    name = f"{new_id()}{ext}"
    return f"{kind}/{user_id}/{now:%Y/%m}/{name}"


__all__ = ["Storage", "get_storage", "startup", "make_key"]
