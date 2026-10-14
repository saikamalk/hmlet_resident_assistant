"""Per-actor rate limiting with Redis in production and an explicit local fallback."""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()
import os
import threading
import time
from collections import defaultdict, deque


class RateLimitExceeded(Exception):
    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class RateLimiter:
    def __init__(self, limit: int = 30, window_seconds: int = 60, redis_url: str | None = None):
        if limit < 1 or window_seconds < 1:
            raise ValueError("Rate limit and window must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self.redis_url = redis_url or os.getenv("REDIS_URL")
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._redis = None
        if self.redis_url:
            try:
                import redis
                self._redis = redis.Redis.from_url(
                    self.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=1,
                    socket_timeout=1,
                )
                self._redis.ping()
            except Exception as exc:
                raise RuntimeError("Configured Redis rate limiter is unavailable") from exc
        elif os.getenv("APP_ENV", "development").lower() == "production":
            raise RuntimeError("REDIS_URL is required when APP_ENV=production")

    @property
    def distributed(self) -> bool:
        return self._redis is not None

    def check(self, actor_id: str) -> None:
        now = time.time()
        if self._redis is not None:
            bucket = int(now // self.window_seconds)
            key = f"hmlet:ratelimit:{actor_id}:{bucket}"
            # Increment and TTL assignment are atomic so a process crash cannot
            # leave a counter key without an expiry.
            script = (
                "local count = redis.call('INCR', KEYS[1]); "
                "if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]); end; "
                "return count"
            )
            count = int(self._redis.eval(script, 1, key, self.window_seconds + 1))
            if count > self.limit:
                retry_after = self.window_seconds - (int(now) % self.window_seconds)
                raise RateLimitExceeded("Rate limit exceeded", max(1, retry_after))
            return

        # Local fallback is for development/tests only; not shared between workers.
        with self._lock:
            events = self._events[actor_id]
            cutoff = now - self.window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                retry_after = max(1, int(events[0] + self.window_seconds - now) + 1)
                raise RateLimitExceeded("Rate limit exceeded", retry_after)
            events.append(now)

    def health_check(self) -> bool:
        """Return whether the configured limiter backend is reachable."""
        if self._redis is None:
            return True
        try:
            return bool(self._redis.ping())
        except Exception as exc:
            raise RuntimeError("Redis rate limiter is unavailable") from exc

    def close(self) -> None:
        """Close the Redis connection pool during application shutdown."""
        if self._redis is not None:
            self._redis.close()
