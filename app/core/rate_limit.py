"""Fixed-window rate limiting backed by Redis."""
from __future__ import annotations

from fastapi import Request

from app.config import settings
from app.redis_client import get_client


def client_ip(request: Request) -> str:
    """Чей это запрос — по мнению того, кому можно верить.

    Раньше здесь стояло «взять `X-Real-IP`, иначе первый элемент
    `X-Forwarded-For`» — и то и другое пишет кто угодно. Проверено на живом
    сервере: четырнадцать запросов с одного адреса ограничитель резал после
    десятого, а те же четырнадцать с подменой заголовка проходили все. То есть
    ограничителей по адресу фактически не было — ни на регистрации, ни на
    сбросе пароля, ни на чеках.

    Ловушка тут в слове «первый». Прокси ДОПИСЫВАЕТ себя справа, значит слева
    в `X-Forwarded-For` лежит ровно то, что прислал клиент, — и брать оттуда
    адрес для ограничителя всё равно что спрашивать у него, кто он.

    Считаем справа, по числу СВОИХ прокси. `trusted_proxy_count = 1` (ingress)
    означает: последний элемент дописал наш ingress, ему и верим. Ноль означает,
    что перед нами никого нет и заголовкам верить нельзя вовсе — это умолчание,
    потому что забытая настройка должна закрывать, а не открывать.
    """
    depth = settings.trusted_proxy_count
    if depth <= 0:
        return request.client.host if request.client else "unknown"

    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if len(chain) >= depth:
        return chain[-depth]
    # Цепочка короче ожидаемой: запрос пришёл не через наш прокси, либо прокси
    # настроен иначе, чем мы думаем. Верить такому заголовку нельзя.
    return request.client.host if request.client else "unknown"


async def hit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Register one hit against ``key``.

    Returns ``(allowed, remaining)``. The window starts on the first hit and
    expires after ``window_seconds``.
    """
    redis = get_client()
    redis_key = f"ratelimit:{key}"
    current = await redis.incr(redis_key)
    if current == 1:
        await redis.expire(redis_key, window_seconds)
    allowed = current <= limit
    remaining = max(0, limit - current)
    return allowed, remaining
