"""Tests para el rate limiter."""


import pytest

from itzel_core.rate_limiter import RateLimiter, TokenBucket


@pytest.mark.asyncio
async def test_token_bucket_allows_first_request():
    bucket = TokenBucket(rate=10, capacity=10)
    assert await bucket.acquire() is True


@pytest.mark.asyncio
async def test_token_bucket_denies_when_empty():
    bucket = TokenBucket(rate=1, capacity=1)
    await bucket.acquire()
    assert await bucket.acquire() is False


@pytest.mark.asyncio
async def test_rate_limiter_known_bucket():
    rl = RateLimiter(model_rps=10, voice_rps=5, web_rps=2)
    assert await rl.check("model") is True


@pytest.mark.asyncio
async def test_rate_limiter_unknown_bucket_always_passes():
    rl = RateLimiter()
    assert await rl.check("unknown_bucket") is True
