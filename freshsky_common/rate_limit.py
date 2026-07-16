"""Optional request-abuse limiters for Flask.

The shared free-access layer does not install these limits. An app may opt in
only when it needs protection against automated or abusive traffic.
"""
from __future__ import annotations

import threading
import time
from math import ceil
from collections import defaultdict
from typing import Callable, Optional

from flask import Flask, jsonify, request, session


class RateLimiter:
    """Sliding-window per-key limiter. Keyed by IP by default, but ``key_fn``
    can return any string (e.g., the Google email of a logged-in user)."""

    def __init__(
        self,
        max_requests: int = 30,
        window_seconds: int = 60,
        key_fn: Optional[Callable[[], str]] = None,
    ):
        self.max_requests = max_requests
        self.window = window_seconds
        self.key_fn = key_fn or self._default_ip_key
        self._lock = threading.Lock()
        self._buckets: "dict[str, list[float]]" = defaultdict(list)

    @staticmethod
    def _default_ip_key() -> str:
        return (
            request.headers.get("X-Forwarded-For", request.remote_addr or "anon")
            .split(",")[0]
            .strip()
        )

    def _client_key(self) -> str:
        return self.key_fn()

    def check_with_retry_after(self) -> tuple[bool, int]:
        now = time.time()
        key = self._client_key()
        if not key:
            return True, 0  # nothing to limit (anonymous + unknown)
        with self._lock:
            history = self._buckets[key]
            cutoff = now - self.window
            history[:] = [t for t in history if t > cutoff]
            if len(history) >= self.max_requests:
                return False, max(1, ceil(history[0] + self.window - now))
            history.append(now)
        return True, 0

    def check(self) -> bool:
        allowed, _ = self.check_with_retry_after()
        return allowed

    def guard(self, fn: Callable):
        """Decorator enforcing the limit on a Flask view."""
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            allowed, retry_after = self.check_with_retry_after()
            if not allowed:
                response = jsonify(error="Too many requests. Please slow down.")
                response.headers["Retry-After"] = str(retry_after)
                return response, 429
            return fn(*args, **kwargs)

        return wrapper


def _user_email_key() -> str:
    return (session.get("user_email") or "").lower()


def register_global_rate_limits(
    app: Flask,
    *,
    ip_per_hour: int = 60,
    user_per_day: int = 200,
    skip_paths: tuple = ("/health", "/healthz", "/api/user-status",
                          "/freemium.js", "/api/affiliates", "/auth/google",
                          "/auth/google/callback", "/logout"),
    only_methods: tuple = ("POST",),
    owner_email: str = "admin@freshskyllc.com",
) -> None:
    """Wire global per-IP and per-user rate limits on POST endpoints.

    Defaults give every IP up to ``ip_per_hour`` POSTs/hour and every signed-in
    Google user up to ``user_per_day`` POSTs/day. The owner email bypasses
    both. Health, status, and OAuth routes are excluded from limiting.

    The limits are enforced *before* the freemium gate / view runs, so an
    abusive IP burning the IP-hour budget never reaches the LLM call site.
    """
    ip_limiter = RateLimiter(max_requests=ip_per_hour, window_seconds=3600)
    user_limiter = RateLimiter(
        max_requests=user_per_day, window_seconds=86400, key_fn=_user_email_key,
    )
    owner_email = (owner_email or "").strip().lower()

    @app.before_request
    def _global_rate_limit():
        if request.method not in only_methods:
            return None
        path = request.path
        for skip in skip_paths:
            if path == skip or path.startswith(skip + "/"):
                return None
        # Owner bypass
        if owner_email and (session.get("user_email") or "").lower() == owner_email:
            return None
        # Per-IP first (short window, fast cutoff against bots)
        ip_allowed, ip_retry_after = ip_limiter.check_with_retry_after()
        if not ip_allowed:
            response = jsonify(
                error="Too many requests from your network. "
                      "Please wait a few minutes and try again.",
                rate_limit="ip",
            )
            response.headers["Retry-After"] = str(ip_retry_after)
            return response, 429
        # Per-user (long window, only meaningful when logged in)
        user_allowed, user_retry_after = user_limiter.check_with_retry_after()
        if session.get("user_email") and not user_allowed:
            response = jsonify(
                error="Automated request protection was triggered. "
                      "Please wait and try again.",
                rate_limit="user",
            )
            response.headers["Retry-After"] = str(user_retry_after)
            return response, 429
        return None
