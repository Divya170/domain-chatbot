"""Rate limits: per IP and per session per minute (in memory), plus a per-session
daily cap counted from storage so it survives restarts (D6).

In-memory windows are per process; production runs a single worker (D7).
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from app.services.errors import ChatError, ErrorCode
from app.storage.base import utc_now

WINDOW_SECONDS = 60.0
MAX_KEYS = 50_000


class SlidingWindow:
    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float]) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= MAX_KEYS:
                self._evict(now)
            hits = self._hits[key] = deque()
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        return hits

    def _evict(self, now: float) -> None:
        stale = [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window]
        for k in stale:
            del self._hits[k]
        if len(self._hits) >= MAX_KEYS:  # still full: drop oldest entries
            for k in sorted(self._hits, key=lambda k: self._hits[k][-1])[: MAX_KEYS // 10]:
                del self._hits[k]

    def retry_after(self, key: str) -> int | None:
        """Seconds to wait if the key is over the limit, else None (does not record)."""
        now = self._clock()
        hits = self._prune(key, now)
        if len(hits) < self.limit:
            return None
        return max(1, math.ceil(hits[0] + self.window - now))

    def record(self, key: str) -> None:
        now = self._clock()
        self._prune(key, now).append(now)


DailyCounter = Callable[[str, datetime], Awaitable[int]]


class RateLimiter:
    def __init__(
        self,
        *,
        per_minute: int,
        daily_cap: int,
        daily_counter: DailyCounter,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.ip_window = SlidingWindow(per_minute, WINDOW_SECONDS, clock)
        self.session_window = SlidingWindow(per_minute, WINDOW_SECONDS, clock)
        self.daily_cap = daily_cap
        self._daily_counter = daily_counter
        self._wall_clock = wall_clock

    def check_ip(self, ip: str) -> None:
        wait = self.ip_window.retry_after(ip)
        if wait is not None:
            raise ChatError(ErrorCode.RATE_LIMIT, detail="ip minute limit", retry_after=wait)

    async def check_session(self, session_id: str) -> None:
        wait = self.session_window.retry_after(session_id)
        if wait is not None:
            raise ChatError(ErrorCode.RATE_LIMIT, detail="session minute limit", retry_after=wait)
        now = self._wall_clock()
        used = await self._daily_counter(session_id, now - timedelta(days=1))
        if used >= self.daily_cap:
            raise ChatError(ErrorCode.RATE_LIMIT, detail="daily cap", retry_after=3600)

    def record(self, ip: str, session_id: str) -> None:
        self.ip_window.record(ip)
        self.session_window.record(session_id)
