from __future__ import annotations

import time
import threading
from collections import deque
from typing import Deque

from app.core.config import get_settings


class RateLimiter:
    def __init__(self, rpm: int, max_concurrency: int):
        self.rpm = int(max(1, rpm))
        self.max_concurrency = int(max(1, max_concurrency))
        self._lock = threading.Lock()
        self._concurrency = threading.Semaphore(self.max_concurrency)
        self._tokens: Deque[float] = deque()  # monotonic timestamps of last permits
        self._waiting = 0

    def acquire(self) -> int:
        # Indicate waiting
        with self._lock:
            self._waiting += 1
        # Concurrency gate
        self._concurrency.acquire()
        # RPM gate
        q_size = 0
        while True:
            with self._lock:
                now = time.monotonic()
                # Drop tokens older than 60s
                while self._tokens and now - self._tokens[0] >= 60.0:
                    self._tokens.popleft()
                if len(self._tokens) < self.rpm:
                    self._tokens.append(now)
                    # capture queue size excluding this thread
                    q_size = max(self._waiting - 1, 0)
                    self._waiting -= 1
                    break
                else:
                    wait = 60.0 - (now - self._tokens[0])
            time.sleep(min(max(wait, 0.01), 0.5))
        return q_size

    def release(self) -> None:
        self._concurrency.release()

    def queue_size(self) -> int:
        with self._lock:
            return max(self._waiting, 0)


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        s = get_settings()
        _limiter = RateLimiter(rpm=s.gemini_requests_per_minute, max_concurrency=s.gemini_max_concurrency)
    return _limiter
