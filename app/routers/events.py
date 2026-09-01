"""First-party analytics ingestion.

Self-hosted, privacy-friendly event collection: the mobile/web client POSTs
batches of anonymous product-analytics events; we store a capped log in Redis
and per-name counters. No PII expected — the client sends metadata only.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from fastapi import APIRouter, Cookie, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config import settings
from app.core import rate_limit
from app.deps import session_id_from
from app.redis_client import get_client
from app.services import auth_service

router = APIRouter(prefix="/api", tags=["events"])

_LOG_KEY = "events:log"
_LOG_CAP = 5000          # keep only the most recent N events
_MAX_BATCH = 50          # events per request
_MAX_PROP_BYTES = 4096   # per-event property size guard

# Счётчики — одним хешем, а не ключом на имя.
#
# Было по ключу на имя (`events:count:{name}`), имя выбирает отправитель, TTL
# нет, числа разных имён нет. Миллион придуманных имён — миллион вечных ключей;
# в том же Redis лежат сессии, и при `maxmemory-policy allkeys-lru` заполнение
# выкидывало бы их, то есть разлогинивало живых людей.
#
# Хеш решает это устройством, а не бдительностью: ключ ровно один, TTL на нём
# один, а число полей ограничено ниже.
_COUNTS_KEY = "events:counts"
# Сколько разных имён согласны помнить. В приложении их несколько десятков;
# двести — с запасом на рост и всё же потолок.
#
# Граница приблизительная, и это осознанно. Список известных имён читается
# один раз на запрос, поэтому два одновременных запроса могут завести на
# несколько полей больше — замер показал 201 при потолке 200. Точность здесь
# не нужна: смысл в том, что число полей ограничено сверху, а не в том, что
# оно ровно двести. Точная граница стоила бы обращения к Redis на каждое
# событие из пачки в пятьдесят.
_MAX_NAMES = 200
# Имена сверх потолка складываются сюда все вместе. Не молча выбрасываются:
# ненулевое число здесь означает «кто-то шлёт незнакомое», и это стоит увидеть.
_OVERFLOW = "_прочее"
# Счётчики живут месяц. Продуктовая аналитика смотрит недели, а вечный ключ —
# это ключ, который никто никогда не сотрёт.
_COUNTS_TTL = 30 * 24 * 3600

# Имя события: строчные латинские, цифры и подчёркивание. Не список известных
# имён — такой список пришлось бы держать в двух местах, и новое событие в
# приложении молча пропадало бы. Здесь ограничивается форма, а количество —
# потолком выше.
_NAME_SHAPE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class EventIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    ts: Optional[int] = None
    props: dict = Field(default_factory=dict)


class EventBatch(BaseModel):
    events: list[EventIn] = Field(default_factory=list)
    anon_id: Optional[str] = Field(default=None, max_length=64)


# Общий разбор — тот же, что в аутентификации. Своя копия здесь врала так же:
# адрес брался из заголовка, который пишет отправитель.
_client_ip = rate_limit.client_ip


async def _known_names(redis) -> set[str]:
    """Имена, которые счётчики уже помнят.

    Спрашивается один раз на запрос, а не на событие: в пачке до пятидесяти
    событий, и пятьдесят обращений к Redis ради проверки потолка стоили бы
    дороже самой записи.
    """
    try:
        return set(await redis.hkeys(_COUNTS_KEY))
    except Exception:  # noqa: BLE001 — аналитика не повод сорвать запрос
        return set()


@router.post("/events")
async def ingest_events(
    batch: EventBatch,
    request: Request,
    session_cookie: Optional[str] = Cookie(default=None, alias=settings.session_cookie_name),
    authorization: Optional[str] = Header(default=None),
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
    #
    # Тем же разбором, что и везде. Раньше читалась только кука — а приложение
    # ходит с Bearer, поэтому события с телефона всегда были безымянными. Не
    # дыра, но аналитика врала: «доля событий без пользователя» означала долю
    # веба, а читалась как доля неавторизованных.
    user_id = None
    sid = session_id_from(authorization, session_cookie)
    if sid:
        session = await auth_service.get_session(sid)
        user_id = session.user_id if session else None

    redis = get_client()
    известные = await _known_names(redis)
    место_есть = len(известные) < _MAX_NAMES
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
        # Считаем под своим именем, пока имя знакомое или пока есть место.
        # Незнакомое сверх потолка складывается в общую корзину — не молча
        # выбрасывается: ненулевое число там означает «кто-то шлёт то, чего мы
        # не знаем», и это стоит увидеть.
        годится = bool(_NAME_SHAPE.match(ev.name))
        поле = ev.name if годится and (ev.name in известные or место_есть) else _OVERFLOW
        if поле not in известные:
            известные.add(поле)
            место_есть = len(известные) < _MAX_NAMES
        pipe.hincrby(_COUNTS_KEY, поле, 1)
        accepted += 1
    pipe.ltrim(_LOG_KEY, -_LOG_CAP, -1)  # cap the log
    # TTL ставится каждый раз: ключ один, и продлевать его, пока события идут,
    # правильнее, чем однажды потерять счётчики целиком.
    pipe.expire(_COUNTS_KEY, _COUNTS_TTL)
    await pipe.execute()

    return {"ok": True, "accepted": accepted}
