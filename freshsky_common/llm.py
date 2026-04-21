"""Multi-provider LLM fallback chain.

Usage:
    from freshsky_common.llm import LLMChain
    chain = LLMChain()
    text = chain.complete(system="You are an expert.", user="Question?")
"""
from __future__ import annotations

import logging
import os
from typing import Callable, List, Optional

import requests

logger = logging.getLogger(__name__)


def _http_post(url: str, headers: dict, payload: dict, timeout: int = 30) -> Optional[str]:
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if r.status_code >= 400:
            logger.warning("LLM provider %s returned %s: %s", url, r.status_code, r.text[:200])
            return None
        return r.text
    except requests.RequestException as exc:
        logger.warning("LLM provider %s request failed: %s", url, exc)
        return None


def _via_groq(system: str, user: str) -> Optional[str]:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    raw = _http_post(
        "https://api.groq.com/openai/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": 2000,
        },
    )
    if not raw:
        return None
    import json
    try:
        return json.loads(raw)["choices"][0]["message"]["content"]
    except (KeyError, ValueError, IndexError):
        return None


def _via_cerebras(system: str, user: str) -> Optional[str]:
    key = os.environ.get("CEREBRAS_API_KEY")
    if not key:
        return None
    raw = _http_post(
        "https://api.cerebras.ai/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": os.environ.get("CEREBRAS_MODEL", "llama-3.3-70b"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": 2000,
        },
    )
    if not raw:
        return None
    import json
    try:
        return json.loads(raw)["choices"][0]["message"]["content"]
    except (KeyError, ValueError, IndexError):
        return None


def _via_gemini(system: str, user: str) -> Optional[str]:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    raw = _http_post(
        url,
        {"Content-Type": "application/json"},
        {
            "contents": [{"role": "user", "parts": [{"text": f"{system}\n\n{user}"}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2000},
        },
    )
    if not raw:
        return None
    import json
    try:
        return json.loads(raw)["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, ValueError, IndexError):
        return None


def _via_mistral(system: str, user: str) -> Optional[str]:
    key = os.environ.get("MISTRAL_API_KEY")
    if not key:
        return None
    raw = _http_post(
        "https://api.mistral.ai/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": os.environ.get("MISTRAL_MODEL", "mistral-large-latest"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": 2000,
        },
    )
    if not raw:
        return None
    import json
    try:
        return json.loads(raw)["choices"][0]["message"]["content"]
    except (KeyError, ValueError, IndexError):
        return None


def _via_openrouter(system: str, user: str) -> Optional[str]:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    raw = _http_post(
        "https://openrouter.ai/api/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": 2000,
        },
    )
    if not raw:
        return None
    import json
    try:
        return json.loads(raw)["choices"][0]["message"]["content"]
    except (KeyError, ValueError, IndexError):
        return None


def _via_huggingface(system: str, user: str) -> Optional[str]:
    key = os.environ.get("HF_API_KEY") or os.environ.get("HUGGINGFACE_API_KEY")
    if not key:
        return None
    model = os.environ.get("HF_MODEL", "meta-llama/Meta-Llama-3-8B-Instruct")
    raw = _http_post(
        f"https://api-inference.huggingface.co/models/{model}",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {"inputs": f"{system}\n\n{user}", "parameters": {"max_new_tokens": 2000, "temperature": 0.4}},
    )
    if not raw:
        return None
    import json
    try:
        data = json.loads(raw)
        if isinstance(data, list) and data:
            return data[0].get("generated_text", "")
        return None
    except (KeyError, ValueError, IndexError):
        return None


DEFAULT_PROVIDERS: List[Callable[[str, str], Optional[str]]] = [
    _via_groq,
    _via_cerebras,
    _via_gemini,
    _via_mistral,
    _via_openrouter,
    _via_huggingface,
]


class LLMChain:
    """Try providers in order, return first non-empty response."""

    def __init__(self, providers: Optional[List[Callable[[str, str], Optional[str]]]] = None):
        self.providers = providers or DEFAULT_PROVIDERS

    def complete(self, system: str, user: str) -> str:
        for fn in self.providers:
            try:
                text = fn(system, user)
            except Exception:
                logger.exception("Provider %s raised", getattr(fn, "__name__", repr(fn)))
                continue
            if text:
                return text
        return ""
