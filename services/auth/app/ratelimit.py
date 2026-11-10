"""Fixed-window rate limiter. Redis-backed when available (shared across workers),
in-process otherwise (dev/tests)."""

from __future__ import annotations

import threading
import time

from rp_shared.errors import TooManyRequests


class RateLimiter:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._mem: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def hit(self, redis, key: str, limit: int, window: int, what: str = "attempts") -> None:
        """Count one hit; raise TooManyRequests once `limit` is exceeded within `window` s."""
        count, retry_after = self._incr(redis, f"rl:{key}", window)
        if count > limit:
            raise TooManyRequests(f"Too many {what}, try again later", retry_after)

    def reset(self, redis, key: str) -> None:
        key = f"rl:{key}"
        if redis is not None:
            redis.delete(key)
        with self._lock:
            self._mem.pop(key, None)

    def _incr(self, redis, key: str, window: int) -> tuple[int, int]:
        if redis is not None:
            redis.set(key, 0, ex=window, nx=True)
            count = int(redis.incr(key))
            ttl = redis.ttl(key)
            return count, ttl if ttl and ttl > 0 else window
        now = self._clock()
        with self._lock:
            count, reset_at = self._mem.get(key, (0, now + window))
            if now >= reset_at:
                count, reset_at = 0, now + window
            count += 1
            self._mem[key] = (count, reset_at)
            return count, int(reset_at - now) + 1
