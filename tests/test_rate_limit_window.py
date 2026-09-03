"""Окно ограничителя обязано истекать.

`INCR`, потом `EXPIRE` — падение между ними оставляло ключ без срока, и
человек оставался заперт лимитом навсегда. Теперь срок ставится до счёта,
одной транзакцией, и не сдвигается последующими попаданиями.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_rate_limit_window.py -q
"""
import pytest
import pytest_asyncio

from app import redis_client
from app.config import settings
from app.core import rate_limit

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def поддельный_redis():
    settings.use_fake_redis = True
    await redis_client.connect()
    yield redis_client.get_client()
    await redis_client.disconnect()


async def test_every_key_carries_a_ttl_from_the_first_hit(поддельный_redis):
    allowed, remaining = await rate_limit.hit("t:one", 3, 60)
    assert allowed and remaining == 2
    assert 0 < await поддельный_redis.ttl("ratelimit:t:one") <= 60


async def test_the_window_does_not_slide_and_the_limit_holds(поддельный_redis):
    for _ in range(3):
        allowed, _ = await rate_limit.hit("t:two", 3, 60)
        assert allowed
    allowed, remaining = await rate_limit.hit("t:two", 3, 60)
    assert not allowed and remaining == 0
    # Срок остался от первого попадания, а не переставился четвёртым.
    assert await поддельный_redis.ttl("ratelimit:t:two") <= 60
