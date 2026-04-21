"""Security headers and input sanitization helpers."""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Optional

from flask import request


DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
    "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def install_security_headers(
    app,
    *,
    csp: Optional[str] = DEFAULT_CSP,
    no_store_paths: tuple = ("/metrics",),
):
    """Add a Flask after_request handler that injects standard security headers.

    Pass ``csp=None`` to skip Content-Security-Policy, or a custom string to
    override the default.
    """

    @app.after_request
    def _add_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Origin-Agent-Cluster", "?1")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        resp.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        resp.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), browsing-topics=()",
        )
        if csp:
            resp.headers.setdefault("Content-Security-Policy", csp)
        if request.is_secure or os.environ.get("FORCE_HTTPS"):
            resp.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        if request.path in no_store_paths:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    return app


_INJECTION_PATTERNS = [
    re.compile(r"(?i)(ignore|forget|disregard)\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?)"),
    re.compile(r"(?i)you\s+are\s+now\s+"),
    re.compile(r"(?i)system\s*:\s*"),
]


def sanitize_user_input(value: str, max_length: int = 4000) -> str:
    """Strip null bytes, common prompt-injection phrases, and overlong content."""
    cleaned = (value or "").replace("\x00", "").strip()
    for pat in _INJECTION_PATTERNS:
        cleaned = pat.sub("", cleaned)
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned


def clean_ai_text(text: str) -> str:
    """Remove markdown formatting and HTML-significant characters from LLM output."""
    text = (text or "").replace("**", "")
    text = re.sub(r"(?m)^#+\s*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.replace("<", "").replace(">", "")
    cleaned = []
    for c in text:
        if ord(c) < 128 or unicodedata.category(c).startswith(("L", "M", "N", "P", "S", "Z")):
            cleaned.append(c)
    return "".join(cleaned).strip()
