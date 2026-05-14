#!/usr/bin/env python3
"""Scan each LLM provider's catalog, compare against current model in
models.json, propose upgrades that match the upgrade_rules. Writes the
updated models.json in place (the GH Actions workflow detects the diff
and opens a draft PR). Prints a markdown report to stdout.

Conservative: only proposes when a clearly-newer model in the same
family + jurisdiction-allowed appears in the catalog. Operator reviews.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = json.loads((ROOT / 'freshsky_common' / 'models.json').read_text(encoding='utf-8'))

BANS = set(MODELS.get('upgrade_rules', {}).get('ban_list', []))


def _is_banned(name: str) -> bool:
    n = name.lower()
    return any(b.replace('-direct', '') in n for b in BANS)


def _list_groq() -> list[str]:
    key = os.environ.get('GROQ_API_KEY')
    if not key:
        return []
    try:
        r = requests.get('https://api.groq.com/openai/v1/models',
                         headers={'Authorization': f'Bearer {key}'}, timeout=15)
        return [m['id'] for m in r.json().get('data', [])]
    except Exception:
        return []


def _list_openrouter_free() -> list[str]:
    try:
        r = requests.get('https://openrouter.ai/api/v1/models', timeout=15)
        out = []
        for m in r.json().get('data', []):
            price = (m.get('pricing') or {}).get('prompt', '0')
            if str(price) == '0':
                out.append(m['id'])
        return out
    except Exception:
        return []


def _list_huggingface() -> list[str]:
    try:
        r = requests.get('https://huggingface.co/api/models',
                         params={'filter': 'text-generation', 'sort': 'downloads', 'limit': 30}, timeout=15)
        return [m['id'] for m in r.json() if isinstance(m, dict) and 'id' in m]
    except Exception:
        return []


SCANNERS = {
    'groq': _list_groq,
    'openrouter': _list_openrouter_free,
    'huggingface': _list_huggingface,
    # Other providers don't expose stable public catalog endpoints we trust
    # — operator updates them by hand. Add scanners as APIs become reliable.
}


def _propose_for(provider: str, info: dict, available: list[str]) -> str | None:
    """Return a proposed new model name, or None if no upgrade qualifies."""
    if not available:
        return None
    current = info.get('current', '')
    prefer_size = info.get('prefer_size', '')

    # Prefer Llama 4 > 3.3 > 3.1 within the same provider, when present.
    def rank(name: str) -> tuple:
        n = name.lower()
        if _is_banned(n):
            return (-99, 0, name)
        family = 0
        if 'llama-4' in n or 'llama4' in n:
            family = 3
        elif 'llama-3.3' in n or '3.3-70b' in n:
            family = 2
        elif 'llama-3.1' in n or '3.1-70b' in n:
            family = 1
        size = 0
        m = re.search(r'(\d+)b', n)
        if m:
            size = int(m.group(1))
        return (family, size, name)

    best = max(available, key=rank)
    if best == current:
        return None
    # Only upgrade if rank strictly increases.
    if rank(best) <= rank(current):
        return None
    return best


def main() -> int:
    lines = ['# Model registry — proposed upgrades', '']
    any_change = False
    for provider, info in MODELS.get('providers', {}).items():
        scanner = SCANNERS.get(provider)
        if not scanner:
            continue
        catalog = scanner()
        proposed = _propose_for(provider, info, catalog)
        if proposed:
            lines.append(f'- **{provider}**: `{info["current"]}` → `{proposed}`')
            info['current'] = proposed
            any_change = True
        else:
            lines.append(f'- {provider}: no change (current: `{info["current"]}`)')

    lines.append('')
    if any_change:
        from datetime import datetime
        MODELS['updated_at'] = datetime.utcnow().date().isoformat()
        (ROOT / 'freshsky_common' / 'models.json').write_text(
            json.dumps(MODELS, indent=2) + '\n', encoding='utf-8',
        )
        lines.append('Diff written to `freshsky_common/models.json`. Operator review required.')
    else:
        lines.append('No upgrades proposed this run.')

    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())
