"""
Agentic orchestration helper — pick the right tool, then run it.

Today most Fresh Sky apps have 2 sequential tools the user manually picks
between (tabs / buttons). Phase 2 of the futureproofing plan moves apps
to **agentic-by-default**: user types one prompt, the agent picks the
tool, runs it, returns the result + which tool was chosen + why.

Same shape as the hub /api/planner which routes ACROSS apps. This
routes WITHIN one app's tool set.

Two-call design (keeps cost predictable):
  1. Router call: cheap LLM call with a `tools` list + user message →
     returns the chosen tool key. Falls back to keyword scoring (same
     pattern as the planner) if the LLM is rate-limited or returns
     garbage.
  2. Tool call: the chosen tool's full system prompt + user message →
     the actual work. Reuses the app's existing _llm() chain.

Apps install one /api/agent endpoint:

    from freshsky_common.orchestrate import orchestrate

    @app.route('/api/agent', methods=['POST'])
    def agent():
        msg = (request.get_json() or {}).get('message', '').strip()
        if not msg:
            return jsonify(error='message required'), 400
        result = orchestrate(
            msg,
            tools=[
                {'key': 'tool1', 'name': 'Draft FOIA request',
                 'when_to_use': 'user wants a NEW records request',
                 'system_prompt': _TOOL1_SYSTEM},
                {'key': 'tool2', 'name': 'Draft FOIA appeal',
                 'when_to_use': 'user got denied or stonewalled',
                 'system_prompt': _TOOL2_SYSTEM},
            ],
            llm=_llm,
        )
        _record_timesaved(result['tool_key'])
        return jsonify(result=result['response'], routed_to=result['tool_key'],
                       brand=result['tool_name'], why=result['why'])
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional, TypedDict

logger = logging.getLogger(__name__)


class _Tool(TypedDict):
    key: str
    name: str
    when_to_use: str
    system_prompt: str


_ROUTER_PROMPT = (
    "You're the in-app router for one Fresh Sky AI tool. Given a user's "
    "question and a list of 2-4 tools the app offers, pick exactly one "
    "tool. Return STRICT JSON: {\"tool\": \"<key>\", \"why\": \"<one sentence in 2nd person>\"}. "
    "Output ONLY the JSON, no markdown fence, no greeting."
)


def _keyword_score(query: str, tool: _Tool) -> int:
    """Fallback scoring when the LLM router fails or returns garbage."""
    q = ' ' + query.lower() + ' '
    score = 0
    if (' ' + tool['key'].lower() + ' ') in q:
        score += 20
    for token in (tool.get('when_to_use', '') + ' ' + tool.get('name', '')).lower().split():
        token = token.strip('—.,();:!?').lower()
        if len(token) < 4:
            continue
        if (' ' + token + ' ') in q or (' ' + token + 's ') in q:
            score += 2
    return score


def _route(query: str, tools: list[_Tool], llm: Callable[[str, str], str]) -> tuple[_Tool, str]:
    """Pick the best tool. Returns (tool_dict, why_string)."""
    # Build the router system prompt with tool descriptions
    catalog = '\n'.join(
        f'- {t["key"]}: {t["name"]} — use when {t["when_to_use"]}'
        for t in tools
    )
    full_prompt = _ROUTER_PROMPT + '\n\nTOOLS:\n' + catalog
    try:
        raw = (llm(full_prompt, f'User question: {query}') or '').strip()
        if raw.startswith('```'):
            raw = raw.strip('`').lstrip('json').strip()
        obj = None
        try:
            obj = json.loads(raw)
        except Exception:
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except Exception:
                    obj = None
        if isinstance(obj, dict) and obj.get('tool'):
            for t in tools:
                if t['key'] == obj['tool']:
                    why = (obj.get('why') or t['when_to_use'])[:280]
                    return t, why
    except Exception as e:
        logger.warning('orchestrate: router LLM raised %s — falling back to keywords', e)

    # Fallback: keyword scoring
    scored = sorted(tools, key=lambda t: -_keyword_score(query, t))
    pick = scored[0]
    return pick, f'Best match for your question based on keywords: {pick["name"]}'


def orchestrate(
    message: str,
    *,
    tools: list[_Tool],
    llm: Callable[[str, str], str],
    lang_directive: Optional[str] = None,
) -> dict:
    """Pick the right tool, then run it. Returns:

        {'tool_key': str, 'tool_name': str, 'why': str, 'response': str}

    `llm` is the app's own _llm(system, user) chain — passed in so each
    app keeps its own retry / fallback / metrics. `lang_directive` is
    appended to the chosen tool's system prompt before running it (apps
    typically pass _lang_directive(lang) from their own per-route code).
    """
    if not tools:
        return {'tool_key': None, 'tool_name': None, 'why': 'No tools configured.', 'response': ''}
    if len(tools) == 1:
        # No routing needed
        t = tools[0]
        system = t['system_prompt']
        if lang_directive:
            system = system + '\n' + lang_directive
        return {
            'tool_key': t['key'],
            'tool_name': t['name'],
            'why': 'Only tool available.',
            'response': llm(system, message),
        }
    chosen, why = _route(message, tools, llm)
    system = chosen['system_prompt']
    if lang_directive:
        system = system + '\n' + lang_directive
    response = llm(system, message)
    return {
        'tool_key': chosen['key'],
        'tool_name': chosen['name'],
        'why': why,
        'response': response,
    }
