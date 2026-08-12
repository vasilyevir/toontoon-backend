"""Webhooks.

The Boostyfi payment webhook is gone with the rest of that integration (CH-17).
App Store Server Notifications land here next; the router stays so the mount
point and its exemption from app-key signing do not move later.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
