"""Rate limiters for Flask — per-IP, per-user, and global registration helper.

Free users have hard fair-use limits at the infrastructure layer to prevent
abuse from exhausting provider quotas. Consumer Pro sessions can bypass these
limits; civic apps can disable that bypass.
"""
from __future__ import annotations

import threading
import time
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

    def check(self) -> bool:
        now = time.time()
        key = self._client_key()
        if not key:
            return True  # nothing to limit (anonymous + unknown)
        with self._lock:
            history = self._buckets[key]
            cutoff = now - self.window
            history[:] = [t for t in history if t > cutoff]
            if len(history) >= self.max_requests:
                return False
            history.append(now)
        return True

    def guard(self, fn: Callable):
        """Decorator enforcing the limit on a Flask view."""
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not self.check():
                return jsonify(error="Too many requests. Please slow down."), 429
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
    pro_bypass: Optional[Callable[[], bool]] = None,
) -> None:
    """Wire global per-IP and per-user rate limits on POST endpoints.

    Defaults give every IP up to ``ip_per_hour`` POSTs/hour and every signed-in
    Google user up to ``user_per_day`` POSTs/day. Owner email and an optional
    verified Pro callback bypass both. Health, status, and OAuth routes are
    excluded from limiting.

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
        if pro_bypass and pro_bypass():
            return None
        # Per-IP first (short window, fast cutoff against bots)
        if not ip_limiter.check():
            return (
                jsonify(
                    error="Too many requests from your network. "
                          "Please wait a few minutes and try again.",
                    rate_limit="ip",
                ),
                429,
            )
        # Per-user (long window, only meaningful when logged in)
        if session.get("user_email") and not user_limiter.check():
            return (
                jsonify(
                    error="Daily usage limit reached for your account. "
                          "Please come back tomorrow or upgrade to Pro.",
                    rate_limit="user",
                    pricing_url="https://www.freshskyai.com/pricing",
                ),
                429,
            )
        return None
