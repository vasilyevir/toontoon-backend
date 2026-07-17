"""First-party analytics ingestion.

Self-hosted, privacy-friendly event collection: the mobile/web client POSTs
batches of anonymous product-analytics events; we store a capped log in Redis
and per-name counters. No PII expected — the client sends metadata only.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config import settings
from app.core import rate_limit
from app.deps import optional_context  # noqa: F401  (kept for future auth tagging)
from app.redis_client import get_client
from app.services import auth_service

router = APIRouter(prefix="/api", tags=["events"])

_LOG_KEY = "events:log"
_LOG_CAP = 5000          # keep only the most recent N events
_MAX_BATCH = 50          # events per request
_MAX_PROP_BYTES = 4096   # per-event property size guard


class EventIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    ts: Optional[int] = None
    props: dict = Field(default_factory=dict)


class EventBatch(BaseModel):
    events: list[EventIn] = Field(default_factory=list)
    anon_id: Optional[str] = Field(default=None, max_length=64)


def _client_ip(request: Request) -> str:
    return (
        request.headers.get("x-real-ip")
        or (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
        or (request.client.host if request.client else "unknown")
    )


@router.post("/events")
async def ingest_events(
    batch: EventBatch,
    request: Request,
    session_cookie: Optional[str] = Cookie(default=None, alias=settings.session_cookie_name),
):
    """Accept a batch of anonymous analytics events. Rate-limited per IP."""
    if not batch.events:
        return {"ok": True, "accepted": 0}
    if len(batch.events) > _MAX_BATCH:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Batch too large")

    allowed, _ = await rate_limit.hit(f"events:ip:{_client_ip(request)}", 120, 60)
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many events")

    # Best-effort user tag (never required — events work fully anonymously).
    user_id = None
    if session_cookie:
        session = await auth_service.get_session(session_cookie)
        user_id = session.user_id if session else None

    redis = get_client()
    now = int(time.time())
    accepted = 0
    pipe = redis.pipeline()
    for ev in batch.events:
        props = ev.props if isinstance(ev.props, dict) else {}
        if len(json.dumps(props)) > _MAX_PROP_BYTES:
            props = {"_dropped": "oversized"}
        record = {
            "name": ev.name,
            "ts": ev.ts or now,
            "server_ts": now,
            "anon_id": batch.anon_id,
            "user_id": user_id,
            "props": props,
        }
        pipe.rpush(_LOG_KEY, json.dumps(record, ensure_ascii=False))
        pipe.incr(f"events:count:{ev.name}")
        accepted += 1
    pipe.ltrim(_LOG_KEY, -_LOG_CAP, -1)  # cap the log
    await pipe.execute()

    return {"ok": True, "accepted": accepted}
