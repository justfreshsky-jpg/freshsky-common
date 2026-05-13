"""
Time-given-back tracking — the one metric we measure.

Every successful tool call records `(slug, tool_key)` against a baseline
'minutes a person would spend manually' from baselines.json. The
running total is the metric we care about: minutes given back to users'
lives. There is NO equivalent counter for time-spent-with-us or
sessions / DAU / streaks / retention — those metrics aren't tracked
anywhere by design (`feedback_paper_themes.md`).

Storage strategy:
  - In-process: a thread-safe Counter for "this instance's lifetime"
    totals (cheap, lossy across scale-down).
  - Firestore (optional): per-app daily aggregate doc at
    `timesaved/{slug}/{YYYY-MM-DD}` — survives scale-to-zero, feeds
    the hub-wide `/admin/timesaved` dashboard. Only writes if
    GOOGLE_CLOUD_PROJECT is set and google-cloud-firestore is importable.

Wiring in an app:

    from freshsky_common.timesaved import install_timesaved
    record = install_timesaved(app, slug='foiahelper')
    # then in a tool route, after the LLM call succeeds:
    record('tool1')  # consults baselines.json for minutes for this tool

The footer chip auto-injects into HTML responses via the same
after-request hook pattern the futuristic skin uses, showing a small
"~X min saved on Fresh Sky AI tools this month" line.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import threading
from collections import Counter
from datetime import datetime, timezone
from typing import Callable, Optional

from flask import Flask

logger = logging.getLogger(__name__)

_BASELINES_PATH = pathlib.Path(__file__).parent / 'baselines.json'

# Per-process metrics. Survives only the current Cloud Run instance — for
# durable cross-instance totals, the Firestore writer below is the source
# of truth.
_LOCK = threading.Lock()
_INSTANCE_MINUTES: dict[str, int] = Counter()  # keyed by slug
_INSTANCE_CALLS: dict[str, int] = Counter()


def _load_baselines() -> dict:
    try:
        return json.loads(_BASELINES_PATH.read_text(encoding='utf-8'))
    except Exception:
        logger.exception('timesaved: failed to load baselines.json')
        return {}


_BASELINES = _load_baselines()


def _firestore_client():
    """Lazy-import Firestore so apps without it (or running locally) work."""
    if not os.environ.get('GOOGLE_CLOUD_PROJECT'):
        return None
    try:
        from google.cloud import firestore  # type: ignore
        return firestore.Client()
    except Exception:
        logger.debug('timesaved: firestore unavailable; using in-process only')
        return None


_FS = _firestore_client()


def _firestore_doc_id(slug: str) -> str:
    """Daily aggregate doc — one per slug per UTC day."""
    return f'{slug}__{datetime.now(timezone.utc).date().isoformat()}'


def _firestore_increment(slug: str, minutes: int) -> None:
    if not _FS:
        return
    try:
        from google.cloud import firestore  # type: ignore
        doc = _FS.collection('timesaved').document(_firestore_doc_id(slug))
        doc.set({
            'slug': slug,
            'date': datetime.now(timezone.utc).date().isoformat(),
            'minutes_saved': firestore.Increment(minutes),
            'calls': firestore.Increment(1),
            'updated_at': firestore.SERVER_TIMESTAMP,
        }, merge=True)
    except Exception:
        logger.exception('timesaved: firestore write failed for %s', slug)


def install_timesaved(app: Flask, *, slug: str) -> Callable[..., int]:
    """Returns a callable `record(tool_key=None)` that increments the
    counters and returns the minutes credited for this call. The callable
    is the entire integration surface — call it from each tool route
    after the LLM call succeeds:

        out = _llm(system, user)
        record('tool1')         # or record() for single-tool apps
        return jsonify(result=out, ...)

    The footer chip auto-renders via a separate after_request hook that
    looks up the in-process total for this slug.
    """
    app_slug = slug
    baselines = _BASELINES.get(slug, {})

    def record(tool_key: Optional[str] = None) -> int:
        if isinstance(baselines, dict):
            mins = int(baselines.get(tool_key, baselines.get('default', 0))) if tool_key else int(baselines.get('default', 0))
        else:
            mins = int(baselines)
        if mins <= 0:
            return 0
        with _LOCK:
            _INSTANCE_MINUTES[app_slug] = _INSTANCE_MINUTES.get(app_slug, 0) + mins
            _INSTANCE_CALLS[app_slug] = _INSTANCE_CALLS.get(app_slug, 0) + 1
        _firestore_increment(app_slug, mins)
        return mins

    # ── footer chip injection ─────────────────────────────────────────
    # Injects right before </body> on text/html responses. Idempotent
    # (skips if already injected). Mirrors the futuristic-skin pattern.
    _CHIP_MARKER = 'fs-timesaved-chip'

    @app.after_request
    def _inject_chip(resp):
        ct = (resp.content_type or '').lower()
        if 'text/html' not in ct:
            return resp
        if getattr(resp, 'direct_passthrough', False):
            return resp
        try:
            body = resp.get_data(as_text=True)
        except Exception:
            return resp
        if _CHIP_MARKER in body or '</body>' not in body:
            return resp
        with _LOCK:
            mins = _INSTANCE_MINUTES.get(app_slug, 0)
            calls = _INSTANCE_CALLS.get(app_slug, 0)
        if mins <= 0:
            return resp  # don't show a zero
        chip = (
            f'<div id="{_CHIP_MARKER}" '
            'style="position:fixed;bottom:14px;left:14px;z-index:99996;'
            'padding:8px 12px;background:rgba(6,9,26,0.78);'
            '-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);'
            'border:1px solid rgba(99,102,241,.25);border-radius:999px;'
            'color:#cbd5e1;font-size:11.5px;font-weight:500;'
            'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
            'letter-spacing:.02em;box-shadow:0 4px 14px rgba(0,0,0,.25);">'
            f'⏱ ~{mins:,} min given back · {calls:,} task{"s" if calls != 1 else ""} this instance'
            '</div>'
        )
        resp.set_data(body.replace('</body>', chip + '</body>', 1))
        return resp

    return record


def get_instance_totals() -> dict:
    """For the hub-side /admin/timesaved aggregator: returns the current
    instance's totals. The hub calls this on each sub-app via a small
    /api/timesaved endpoint registered by install_timesaved_endpoint()."""
    with _LOCK:
        return {
            'minutes': dict(_INSTANCE_MINUTES),
            'calls': dict(_INSTANCE_CALLS),
        }


def install_timesaved_endpoint(app: Flask) -> None:
    """Optional public /api/timesaved endpoint on each app — exposes its
    own in-process totals so the hub can aggregate. Cheap; reads no DB."""
    from flask import jsonify as _jsonify

    @app.route('/api/timesaved', methods=['GET'])
    def _timesaved():
        return _jsonify(get_instance_totals()), 200, {
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'no-store',
        }
