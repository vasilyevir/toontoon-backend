"""Storage interface.

Generation code must not know where bytes land. Today that is MinIO in our own
cluster; if egress ever makes an external provider cheaper, only this package
changes — the database stores object *keys*, not URLs, precisely so that switch
stays a configuration change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class Storage(ABC):
    """Put bytes in, get a key back. Read access is always via a signed link."""

    @abstractmethod
    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        """Store ``data`` under ``key`` and return the key."""

    @abstractmethod
    async def get(self, key: str) -> Optional[bytes]:
        """Read an object back. Returns None if it is gone."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove an object. Deleting an account must actually erase the files,
        not just the rows that point at them."""

    @abstractmethod
    async def signed_url(self, key: str, *, ttl_seconds: Optional[int] = None) -> str:
        """Short-lived read link. The bucket itself stays private."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        ...
