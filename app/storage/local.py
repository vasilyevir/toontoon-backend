"""Filesystem backend for development.

Deliberately limited: it cannot enforce private access, so ``signed_url`` here
returns an API path that the app itself serves after checking the session.
Never use this in production — that is exactly the arrangement we are migrating
away from.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.storage.base import Storage

ROOT = Path("uploads")


class LocalStorage(Storage):
    def _path(self, key: str) -> Path:
        # Keys look like "gen/2026/08/xxx.png"; keep the shape on disk.
        path = ROOT / key
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        self._path(key).write_bytes(data)
        return key

    async def get(self, key: str) -> Optional[bytes]:
        path = ROOT / key
        return path.read_bytes() if path.exists() else None

    async def delete(self, key: str) -> None:
        path = ROOT / key
        if path.exists():
            path.unlink()

    async def signed_url(self, key: str, *, ttl_seconds: Optional[int] = None) -> str:
        # Served by the app behind an auth check, not by a static mount.
        return f"/api/media/{key}"

    async def exists(self, key: str) -> bool:
        return (ROOT / key).exists()
