"""Token bucket rate limiter para el backend de Itzel."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class TokenBucket:
    """Token bucket thread-safe para async."""

    rate: float  # tokens por segundo
    capacity: float  # máximo de tokens
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = time.monotonic()

    async def acquire(self, tokens: float = 1.0) -> bool:
        """Intenta consumir `tokens`. Devuelve True si se pudo, False si hay rate limit."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now

            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False


class RateLimiter:
    """Gestor de rate limits para los distintos endpoints de Itzel."""

    def __init__(self, model_rps: int = 10, voice_rps: int = 5, web_rps: int = 2) -> None:
        self._buckets: dict[str, TokenBucket] = {
            "model": TokenBucket(rate=model_rps, capacity=model_rps * 2),
            "voice": TokenBucket(rate=voice_rps, capacity=voice_rps * 2),
            "web": TokenBucket(rate=web_rps, capacity=web_rps * 2),
        }

    async def check(self, bucket: str) -> bool:
        """Retorna True si la request está permitida."""
        b = self._buckets.get(bucket)
        if b is None:
            return True
        return await b.acquire()

    async def wait(self, bucket: str, max_wait_s: float = 5.0) -> bool:
        """Espera hasta que haya tokens disponibles o se agote max_wait_s."""
        deadline = time.monotonic() + max_wait_s
        while time.monotonic() < deadline:
            if await self.check(bucket):
                return True
            await asyncio.sleep(0.05)
        return False
