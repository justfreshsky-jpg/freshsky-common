"""Public routing identity; deliberately separate from authenticated owner identity."""
from __future__ import annotations

import os

from flask import has_request_context, request

CANONICAL_ORIGIN = "https://freshskyllc.org"
LEGACY_ORIGIN = "https://www.freshskyai.com"
HUB_HOSTS = frozenset({"freshskyllc.org", "www.freshskyllc.org", "freshskyai.com", "www.freshskyai.com"})


def public_origin() -> str:
    """Permit only the canonical origin or an explicitly enabled legacy rollback.

    This setting never changes OAuth audiences, owner allowlists, or account IDs.
    Deployments must verify provider configuration separately. During an explicit
    legacy hold, only exact canonical request hosts select canonical public links.
    Background jobs and all other hosts retain the configured legacy destination.
    """
    origin = os.environ.get("FRESHSKY_PUBLIC_ORIGIN", CANONICAL_ORIGIN).rstrip("/")
    if origin == CANONICAL_ORIGIN:
        return origin
    if origin == LEGACY_ORIGIN and os.environ.get("FRESHSKY_LEGACY_DOMAIN_COMPATIBILITY") == "true":
        if has_request_context() and request.host.lower() in {"freshskyllc.org", "www.freshskyllc.org"}:
            return CANONICAL_ORIGIN
        return origin
    raise ValueError("public origin must be freshskyllc.org; legacy rollback requires explicit compatibility")
