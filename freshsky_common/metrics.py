"""Thread-safe per-app metrics counter."""
from __future__ import annotations

import threading
from collections import Counter, defaultdict


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters: "defaultdict[str, Counter]" = defaultdict(Counter)

    def incr(self, group: str, key: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[group][key] += amount

    def snapshot(self) -> dict:
        with self._lock:
            return {g: dict(c) for g, c in self._counters.items()}
