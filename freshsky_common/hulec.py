"""
HULEC scorer — periodic retirement signal across the portfolio.

HULEC is the operator's gate for whether an app should keep existing:

  H — Helpful (does it actually save a person time?)
  U — Unique  (is there a free .gov tool that already does this?)
  L — Lean    (is the surface small / one job well done?)
  E — Efficient (is it fast — Cloud Run p95 within target?)
  C — Cheap   (does it stay on free-tier providers, no paid bill?)

This module operationalizes U / E / C as automatable checks. H + L stay
operator-judged (they require reading the product, not metrics).

Two pieces:

  1. `install_hulec(app, slug)` — registers GET /api/hulec on the app,
     exposing the signals that hub-side scoring needs:
        - p95_ms across last N requests (in-memory ring buffer)
        - request_count, llm_calls, estimated_cost_usd
        - minutes_saved (instance lifetime, from freshsky_common.timesaved)
        - canonical_gov_url (looked up from canonical_gov.json)

  2. `score_all(slugs, app_urls)` — hub-side fan-out. Hits each
     /api/hulec, applies thresholds, returns a retirement queue.

Thresholds (overridable):
  - p95_ms > 8000 → flag Efficient
  - estimated_cost_usd > 0 → flag Cheap
  - canonical_gov_url present AND covers_fully=true → flag Unique

The 7-consecutive-days rule from the plan would need persistence
(Firestore daily snapshots). For now, the hub aggregator surfaces the
current snapshot; the operator can run the dashboard weekly and a
flagged app is a candidate to look at — not an auto-retire.
"""
from __future__ import annotations

import collections
import json
import logging
import os
import pathlib
import threading
import time
from typing import Optional

from flask import Flask, jsonify

logger = logging.getLogger(__name__)

_CANONICAL_PATH = pathlib.Path(__file__).parent / 'canonical_gov.json'

# Ring buffer of recent response times (ms) per process. Small fixed
# size — we just need a rough p95, not a full histogram.
_RING_SIZE = 200
_LOCK = threading.Lock()
_RING: collections.deque[float] = collections.deque(maxlen=_RING_SIZE)
_REQ_COUNT = 0
_INSTANCE_STARTED = time.time()


def _load_canonicals() -> dict:
    try:
        return json.loads(_CANONICAL_PATH.read_text(encoding='utf-8'))
    except Exception:
        logger.exception('hulec: failed to load canonical_gov.json')
        return {}


_CANONICALS = _load_canonicals()


def _percentile(values: list[float], p: float) -> float:
    """Crude p-th percentile on a sorted list of floats (no numpy)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def install_hulec(app: Flask, *, slug: str) -> None:
    """Register the timing middleware + GET /api/hulec endpoint.

    Idempotent: a second call no-ops because Flask raises on duplicate
    route registration; we catch that.
    """
    canonical = _CANONICALS.get(slug, {})

    @app.before_request
    def _hulec_t0():
        from flask import g, request
        # Skip the health/hulec endpoints themselves so we don't pollute
        # the ring with their (very fast) responses.
        if request.path in ('/api/hulec', '/health'):
            return None
        g._hulec_t0 = time.time()
        return None

    @app.after_request
    def _hulec_t1(resp):
        from flask import g
        t0 = getattr(g, '_hulec_t0', None)
        if t0 is None:
            return resp
        try:
            ms = (time.time() - t0) * 1000.0
            global _REQ_COUNT
            with _LOCK:
                _RING.append(ms)
                _REQ_COUNT += 1
        except Exception:
            pass
        return resp

    try:
        @app.route('/api/hulec', methods=['GET'])
        def _hulec_report():
            with _LOCK:
                samples = list(_RING)
                req_count = _REQ_COUNT
            p95 = _percentile(samples, 95)
            p50 = _percentile(samples, 50)
            # Pull current minutes-saved from timesaved if installed.
            try:
                from freshsky_common.timesaved import get_instance_totals
                ts = get_instance_totals()
                mins = ts.get('minutes', {}).get(slug, 0)
                calls = ts.get('calls', {}).get(slug, 0)
            except Exception:
                mins = 0
                calls = 0
            return jsonify({
                'slug': slug,
                'instance_uptime_seconds': int(time.time() - _INSTANCE_STARTED),
                'request_count': req_count,
                'p50_ms': round(p50, 1),
                'p95_ms': round(p95, 1),
                'samples': len(samples),
                'minutes_saved_instance': mins,
                'tool_calls_instance': calls,
                # The LLM-cost telemetry hook: apps wire their own _metrics
                # _llm_cost_usd counter here when they add a paid provider.
                # With the current 10-provider always-free chain this stays
                # 0; non-zero = budget signal worth reading.
                'llm_estimated_cost_usd': float(os.environ.get('LLM_COST_USD_HINT', '0') or 0),
                'canonical_gov': canonical,
            }), 200, {
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'no-store',
            }
    except Exception as e:
        # Route already registered (duplicate install_hulec call) — skip.
        logger.debug('hulec: skip duplicate /api/hulec registration: %s', e)


# ── Hub-side scoring helpers ────────────────────────────────────────────


def score_snapshot(
    snapshot: dict,
    *,
    p95_threshold_ms: int = 8000,
    cost_threshold_usd: float = 0.0,
) -> dict:
    """Apply HULEC thresholds to one app's /api/hulec snapshot.

    Returns a dict with `flags` (list of flagged dimensions) and
    `reasons` (human-readable strings). An empty `flags` list means
    the app is healthy on the automatable HULEC dimensions.
    """
    flags: list[str] = []
    reasons: list[str] = []

    p95 = snapshot.get('p95_ms') or 0
    samples = snapshot.get('samples') or 0
    if samples >= 10 and p95 > p95_threshold_ms:
        flags.append('E')
        reasons.append(f'p95 {p95:.0f}ms > {p95_threshold_ms}ms threshold')

    cost = snapshot.get('llm_estimated_cost_usd') or 0
    if cost > cost_threshold_usd:
        flags.append('C')
        reasons.append(f'LLM cost ${cost:.2f} > ${cost_threshold_usd:.2f} threshold')

    canon = snapshot.get('canonical_gov') or {}
    if canon.get('covers_fully'):
        flags.append('U')
        url = canon.get('url', '(no url)')
        reasons.append(f'free .gov canonical fully covers this: {url}')

    mins = snapshot.get('minutes_saved_instance') or 0
    calls = snapshot.get('tool_calls_instance') or 0
    # H/L are operator judgement, not auto-flagged. We surface the data.
    return {
        'flags': flags,
        'reasons': reasons,
        'p95_ms': p95,
        'request_count': snapshot.get('request_count', 0),
        'minutes_saved': mins,
        'tool_calls': calls,
        'canonical_gov': canon,
    }


def score_all(app_urls: dict, *, fetch_timeout: float = 4.0) -> list[dict]:
    """Hub-side fan-out: hit each app's /api/hulec, score it, return
    sorted-by-severity list.

    `app_urls`: {slug: 'https://app.freshskyai.com'}
    """
    import urllib.request
    results: list[dict] = []
    for slug, base in app_urls.items():
        url = base.rstrip('/') + '/api/hulec'
        snap: Optional[dict] = None
        err: Optional[str] = None
        try:
            with urllib.request.urlopen(url, timeout=fetch_timeout) as r:
                snap = json.loads(r.read().decode('utf-8'))
        except Exception as e:
            err = str(e)
        if snap is None:
            results.append({
                'slug': slug, 'url': base, 'error': err,
                'flags': ['?'], 'reasons': [f'fetch failed: {err}'],
                'p95_ms': 0, 'minutes_saved': 0, 'tool_calls': 0,
                'request_count': 0, 'canonical_gov': {},
            })
            continue
        scored = score_snapshot(snap)
        scored['slug'] = slug
        scored['url'] = base
        results.append(scored)
    # Sort: most flags first, then highest p95, then by slug.
    results.sort(key=lambda r: (-len(r['flags']), -(r.get('p95_ms') or 0), r['slug']))
    return results
