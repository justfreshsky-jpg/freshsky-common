"""Token-bucket per-IP rate limiter for Flask."""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Callable

from flask import jsonify, request


class RateLimiter:
    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._lock = threading.Lock()
        self._buckets: "dict[str, list[float]]" = defaultdict(list)

    def _client_key(self) -> str:
        return request.headers.get("X-Forwarded-For", request.remote_addr or "anon").split(",")[0].strip()

    def check(self) -> bool:
        now = time.time()
        key = self._client_key()
        with self._lock:
            history = self._buckets[key]
            cutoff = now - self.window
            history[:] = [t for t in history if t > cutoff]
            if len(history) >= self.max_requests:
                return False
            history.append(now)
        return True

    def guard(self, fn: Callable):
        """Decorator that enforces the limit on a Flask view."""
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not self.check():
                return jsonify(error="Too many requests. Please slow down."), 429
            return fn(*args, **kwargs)

        return wrapper
