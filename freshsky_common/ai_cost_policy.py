"""Fail-closed, zero-cash inference eligibility. No credentials or inputs are logged.

The operator mounts a private verification record outside source control. It is
an attestation, not a substitute for the provider enforcing a no-billing plan.
Only Groq's reviewed text endpoint is currently eligible. Missing verification
means deterministic/manual operation, never a paid fallback.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .privacy import enforce_deidentified_public_input

POLICY = "free_only"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
REVIEWED_MODELS = frozenset({"openai/gpt-oss-20b", "openai/gpt-oss-120b"})
MAX_VERIFICATION_AGE = timedelta(hours=24)
MAX_INPUT_CHARACTERS = 12000
MAX_OUTPUT_TOKENS = 2000


def _time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def _verified_record():
    """Read a bounded private record afresh; stale/missing/invalid fails closed."""
    try:
        if os.environ.get("FRESHSKY_AI_COST_POLICY", POLICY) != POLICY:
            return None
        path = Path(os.environ.get("FRESHSKY_FREE_AI_VERIFICATION_FILE", ""))
        if not path.is_absolute():
            return None
        with path.open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            return None
        record = json.loads(raw)
        if not isinstance(record, dict) or type(record.get("schema_version")) is not int or record["schema_version"] != 1:
            return None
        if record.get("provider") != "groq" or record.get("plan") != "free":
            return None
        if not isinstance(record.get("account_id"), str) or not record["account_id"].strip():
            return None
        for field in ("no_billable_overage", "commercial_use_reviewed", "privacy_reviewed", "non_sensitive_only"):
            if record.get(field) is not True:
                return None
        if record.get("automatic_upgrade") is not False or record.get("paid_credits") is not False:
            return None
        if os.environ.get("GROQ_ZDR_CONFIRMED", "").lower() not in {"true", "1", "yes", "on"}:
            return None
        key = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_KEY", "")
        digest = record.get("credential_sha256", "")
        if not key or not isinstance(digest, str) or not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), digest):
            return None
        evidence = record.get("evidence_sha256", "")
        if not isinstance(evidence, str) or len(evidence) != 64 or any(c not in "0123456789abcdef" for c in evidence):
            return None
        now = datetime.now(timezone.utc)
        checked, expires = _time(record["verified_at"]), _time(record["expires_at"])
        if not checked <= now < expires or expires - checked > MAX_VERIFICATION_AGE:
            return None
        models = record.get("models")
        if not isinstance(models, list) or not models or any(not isinstance(m, str) or m not in REVIEWED_MODELS for m in models):
            return None
        return record
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return None


def ai_availability() -> dict:
    record = _verified_record()
    configured_model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
    available = bool(record and configured_model in record["models"])
    return {"policy": POLICY, "available": available,
            "reason": "verified_free_provider" if available else "free_provider_unverified"}


def free_call_allowed(provider: str, url: str, headers: dict, payload: dict) -> bool:
    """Check account, exact endpoint/model, bounded text and privacy before I/O.

    This deliberately supports no tools, images, files, URLs to fetch, batch
    jobs, paid credits or provider-selected routing. Privacy regexes are an
    additional guard, not proof that arbitrary user text is non-sensitive.
    """
    if provider != "groq" or url != GROQ_URL:
        return False
    record = _verified_record()
    if not record or not isinstance(payload, dict) or not isinstance(headers, dict):
        return False
    key = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_KEY", "")
    if headers.get("Authorization") != "Bearer " + key:
        return False
    if set(payload) - {"model", "messages", "max_tokens", "temperature"}:
        return False
    if payload.get("model") not in record["models"]:
        return False
    limit = payload.get("max_tokens")
    if type(limit) is not int or not 0 < limit <= MAX_OUTPUT_TOKENS:
        return False
    messages = payload.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 2:
        return False
    text = []
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            return False
        if message["role"] not in {"user", "system"} or not isinstance(message["content"], str):
            return False
        text.append(message["content"])
    combined = "\n".join(text)
    if not combined.strip() or len(combined) > MAX_INPUT_CHARACTERS:
        return False
    enforce_deidentified_public_input(combined)
    return True
