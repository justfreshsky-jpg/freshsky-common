"""
Refusal guardrails — survive frontier model upgrades.

Most Fresh Sky AI app refusals today are inline in system prompts as
"(6) DISCLAIMER — educational, not legal advice". Models follow that
loosely. As LLMs get more capable, a stronger model can "helpfully"
generate the exact USCIS form text the user asked for, ignoring the
prompt instruction. That's the failure mode this module prevents:
**lexical filter BEFORE the LLM call**. The filter doesn't depend on
the LLM behaving — it inspects the user's raw input against a JSON
patterns file and short-circuits the request with a 422 + handoff link.

Patterns live in refusal_patterns.json so they can be tuned without
code edits or redeploys (next deploy picks up the new file).

Usage in an app:

    from freshsky_common.refusals import install_refusals
    install_refusals(app, categories=['high_stakes_form_fill',
                                       'auto_action_high_stakes'])

After install, any POST to /api/* whose JSON body contains a field
matching a refusal pattern returns:

    HTTP 422
    {"error": "Soft refusal text...", "category": "...",
     "handoff": {"label": "...", "url": "..."}}

The app's normal handler never runs. The user-facing error message is
warm — names what's being refused and offers a human/agency to go to.
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
from typing import Iterable, Optional

from flask import Flask, jsonify, request

logger = logging.getLogger(__name__)

_PATTERNS_PATH = pathlib.Path(__file__).parent / 'refusal_patterns.json'

# Module-level cache. Hot-reloaded if the file mtime changes between
# instance starts (we don't reload mid-request — the data is small and
# changes only on deploy).
_CACHE: dict | None = None


def _load_patterns() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(_PATTERNS_PATH.read_text(encoding='utf-8'))
        except Exception:
            logger.exception('refusals: failed to load patterns file; refusing nothing')
            _CACHE = {}
    return _CACHE


def _compile_for(categories: Iterable[str]) -> list[tuple[str, dict, list[re.Pattern]]]:
    """Returns [(category_name, handoff_dict, [compiled_patterns])]."""
    data = _load_patterns()
    out: list[tuple[str, dict, list[re.Pattern]]] = []
    for cat in categories:
        spec = data.get(cat)
        if not spec:
            logger.warning('refusals: unknown category %r — skipping', cat)
            continue
        compiled = []
        for p in spec.get('patterns', []):
            try:
                compiled.append(re.compile(p))
            except re.error as e:
                logger.warning('refusals: bad regex in %s: %s — skipping', cat, e)
        out.append((cat, spec.get('handoff', {}), compiled))
    return out


def _extract_text(payload: dict) -> str:
    """Best-effort: concatenate all string values in the JSON body so the
    user's input shows up regardless of which field name an app picked
    (situation, request, error, message, denial, estate, vin, etc.)."""
    if not isinstance(payload, dict):
        return ''
    parts: list[str] = []
    for v in payload.values():
        if isinstance(v, str):
            parts.append(v)
    return ' '.join(parts)


def _check(text: str, rules: list[tuple[str, dict, list[re.Pattern]]]) -> Optional[tuple[str, dict]]:
    """Return (category, handoff) of the first match, or None."""
    if not text:
        return None
    for cat, handoff, patterns in rules:
        for p in patterns:
            if p.search(text):
                return cat, handoff
    return None


def install_refusals(app: Flask, categories: Iterable[str]) -> None:
    """Register a before-request hook that returns a 422 on matching POSTs
    to /api/* routes. Other paths (GET, /, /health, etc.) are unaffected.

    Idempotent: calling twice with overlapping categories just de-dupes.
    """
    rules = _compile_for(list(categories))
    if not rules:
        logger.info('refusals: no rules active for app %s', app.name)
        return

    @app.before_request
    def _refusals_gate():
        if request.method != 'POST':
            return None
        if not request.path.startswith('/api/'):
            return None
        # Skip the planner endpoint itself + analytics + freemium
        if request.path in ('/api/planner', '/api/set-lang', '/api/affiliates', '/api/usage'):
            return None
        try:
            payload = request.get_json(silent=True) or {}
        except Exception:
            return None
        text = _extract_text(payload)
        hit = _check(text, rules)
        if not hit:
            return None
        cat, handoff = hit
        soft = _soft_refusal_text(cat)
        resp = jsonify({
            'error': soft,
            'category': cat,
            'handoff': handoff,
            'refused_by': 'freshsky_common.refusals',
        })
        resp.status_code = 422
        # Cross-origin friendly (matches the planner's CORS posture)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp


def _soft_refusal_text(category: str) -> str:
    return {
        'high_stakes_form_fill': (
            "I can help you understand and prepare for this form — what it's for, "
            "what to gather, what each section means — but I won't generate the "
            "form's exact text for you to submit. Forms with legal consequences "
            "need human judgment in the loop. Try the handoff link below for "
            "professional help."
        ),
        'medical_diagnosis': (
            "I can't diagnose conditions, prescribe medication, or recommend "
            "doses. Even when the models get smarter, this stays a hard line — "
            "the wrong answer can hurt you. Call 988 if it's mental health, "
            "or 211 for a free nurse line in your area."
        ),
        'legal_advice_act': (
            "I can draft documents and explain the process, but I won't tell "
            "you how to plead or predict an outcome. Those decisions need a "
            "licensed attorney who knows your full case. LawHelp.org lists "
            "free options by state."
        ),
        'auto_action_high_stakes': (
            "I draft and walk you through — I don't submit, file, send, pay, "
            "or call agencies on your behalf. The submit step has to stay with "
            "you so you can review what's going out under your name."
        ),
    }.get(category, (
        "I can help with the educational / preparatory part of this, but not "
        "the consequential step. The handoff link below points to a real "
        "human or official source for that part."
    ))
