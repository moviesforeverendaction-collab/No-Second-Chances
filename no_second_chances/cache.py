import asyncio
import time
from collections import defaultdict
from typing import Any, Optional


class TTLCache:
    def __init__(self, default_ttl: int = 300):
        self._store: dict = {}
        self._default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        if key in self._store:
            value, expires_at = self._store[key]
            if time.monotonic() < expires_at:
                return value
            del self._store[key]
        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl if ttl is not None else self._default_ttl
        self._store[key] = (value, time.monotonic() + ttl)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._store.items() if now >= exp]
        for k in expired:
            del self._store[k]

    def size(self) -> int:
        return len(self._store)


class RateLimiter:
    def __init__(self, max_calls: int = 3, window_seconds: float = 10.0):
        self._max_calls = max_calls
        self._window = window_seconds
        self._user_calls: dict = defaultdict(list)
        self._semaphore = asyncio.Semaphore(100)

    def is_rate_limited(self, user_id: int) -> bool:
        now = time.monotonic()
        calls = self._user_calls[user_id]
        self._user_calls[user_id] = [t for t in calls if now - t < self._window]
        if len(self._user_calls[user_id]) >= self._max_calls:
            return True
        self._user_calls[user_id].append(now)
        return False


blacklist_cache = TTLCache(default_ttl=300)
member_count_cache = TTLCache(default_ttl=60)
stats_cache = TTLCache(default_ttl=120)
wallpaper_cache = TTLCache(default_ttl=60)
settings_cache = TTLCache(default_ttl=300)
bot_users_cache = TTLCache(default_ttl=120)
rate_limiter = RateLimiter(max_calls=3, window_seconds=10)
