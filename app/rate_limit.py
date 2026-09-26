"""Bounded per-process login throttling; use a shared limiter at the proxy in clusters."""
from __future__ import annotations
import math
import threading
import time


class LoginLimiter:
    def __init__(self, limit: int = 20, window: int = 300, capacity: int = 4096):
        self.limit, self.window, self.capacity = limit, window, capacity
        self.entries: dict[str, tuple[float, int]] = {}
        self.lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        now = time.monotonic()
        with self.lock:
            self.entries = {k: v for k, v in self.entries.items() if v[0] > now}
            if key not in self.entries and len(self.entries) >= self.capacity:
                return self.window
            expires, count = self.entries.get(key, (now + self.window, 0))
            if count >= self.limit:
                return max(1, math.ceil(expires - now))
            self.entries[key] = (expires, count + 1)
            return 0
