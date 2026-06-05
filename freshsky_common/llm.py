"""Multi-provider LLM fallback chain.

Usage:
    from freshsky_common.llm import LLMChain
    chain = LLMChain()
    text = chain.complete(system="You are an expert.", user="Question?")
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, List, Optional

import requests

logger = logging.getLogger(__name__)

_WATSONX_TOKEN = ""
_WATSONX_TOKEN_EXPIRES_AT = 0.0
_WATSONX_TOKEN_LOCK = threading.Lock()


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
            # Cerebras free tier currently serves llama-3.1-8b + gpt-oss-120b.
            # llama-3.3-70b was removed; using it returns 404 silently. Keep
            # the small fast model as default so the chain doesn't burn a
            # round-trip on every call before falling through.
            "model": os.environ.get("CEREBRAS_MODEL", "llama-3.1-8b"),
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


def _via_nvidia(system: str, user: str) -> Optional[str]:
    # NVIDIA NIM (build.nvidia.com / integrate.api.nvidia.com) — US,
    # OpenAI-compatible. 1k starter credits + 4k on request; ongoing
    # rate-limited free tier of 40 req/min. Hosts Llama 3.3 70B,
    # Mistral, Phi, Gemma, and many others.
    key = os.environ.get("NVIDIA_NIM_KEY")
    if not key:
        return None
    raw = _http_post(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": os.environ.get("NVIDIA_NIM_MODEL", "meta/llama-3.3-70b-instruct"),
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


def _via_codestral(system: str, user: str) -> Optional[str]:
    # Mistral Codestral — separate free tier from La Plateforme: 30 req/min,
    # 2,000 req/day, commercial use EXPLICITLY allowed (the main Mistral
    # free tier requires a data-training opt-in for commercial). Coding-
    # tuned but handles general text fine. EU jurisdiction.
    key = os.environ.get("CODESTRAL_API_KEY")
    if not key:
        return None
    raw = _http_post(
        "https://codestral.mistral.ai/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": os.environ.get("CODESTRAL_MODEL", "codestral-latest"),
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


def _via_sambanova(system: str, user: str) -> Optional[str]:
    # SambaNova Cloud — RDU-accelerated, OpenAI-compatible, persistent free tier.
    # Default model is Meta-Llama-3.3-70B-Instruct (confirmed active May 2026);
    # earlier Llama-3.1-8B/70B/405B-Instruct were deprecated. Override with
    # SAMBANOVA_MODEL for Llama-4-Maverick-17B-128E-Instruct etc.
    key = os.environ.get("SAMBANOVA_API_KEY")
    if not key:
        return None
    raw = _http_post(
        "https://api.sambanova.ai/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": os.environ.get("SAMBANOVA_MODEL", "Meta-Llama-3.3-70B-Instruct"),
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


def _via_cloudflare(system: str, user: str) -> Optional[str]:
    # Cloudflare Workers AI — OpenAI-compatible endpoint, US jurisdiction,
    # permanent free tier of 10,000 neurons/day. Needs both an API token
    # and the account ID (the latter is embedded in the URL). Default
    # model is Llama 3.1 8B Instruct — small, fast, cheap on neurons;
    # override via CLOUDFLARE_MODEL for @cf/meta/llama-3.3-70b-instruct-fp8-fast
    # or @cf/google/gemma-7b-it etc. when the task wants more capability.
    key = os.environ.get("CLOUDFLARE_API_KEY")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not key or not account:
        return None
    raw = _http_post(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": os.environ.get("CLOUDFLARE_MODEL", "@cf/meta/llama-3.1-8b-instruct"),
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


def _watsonx_access_token(api_key: str) -> Optional[str]:
    """Exchange an IBM Cloud API key for a short-lived IAM bearer token."""
    global _WATSONX_TOKEN, _WATSONX_TOKEN_EXPIRES_AT
    now = time.time()
    if _WATSONX_TOKEN and now < (_WATSONX_TOKEN_EXPIRES_AT - 60):
        return _WATSONX_TOKEN
    with _WATSONX_TOKEN_LOCK:
        now = time.time()
        if _WATSONX_TOKEN and now < (_WATSONX_TOKEN_EXPIRES_AT - 60):
            return _WATSONX_TOKEN
        try:
            response = requests.post(
                "https://iam.cloud.ibm.com/identity/token",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                    "apikey": api_key,
                },
                timeout=15,
            )
            if response.status_code >= 400:
                logger.warning(
                    "IBM IAM returned %s: %s",
                    response.status_code,
                    response.text[:200],
                )
                return None
            data = response.json()
            token = data.get("access_token", "")
            if not token:
                return None
            _WATSONX_TOKEN = token
            expires_in = int(data.get("expires_in") or 3600)
            _WATSONX_TOKEN_EXPIRES_AT = now + max(expires_in, 60)
            return token
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning("IBM IAM request failed: %s", exc)
            return None


def _via_watsonx(system: str, user: str) -> Optional[str]:
    # IBM watsonx.ai Runtime Lite includes a monthly foundation-model token
    # allowance and exposes the same REST API as paid plans. Both an API key
    # and project ID are required; absent credentials make this a no-op.
    api_key = os.environ.get("WATSONX_API_KEY") or os.environ.get("IBM_CLOUD_API_KEY")
    project_id = os.environ.get("WATSONX_PROJECT_ID")
    if not api_key or not project_id:
        return None
    token = _watsonx_access_token(api_key)
    if not token:
        return None
    base_url = os.environ.get(
        "WATSONX_URL", "https://us-south.ml.cloud.ibm.com"
    ).rstrip("/")
    raw = _http_post(
        f"{base_url}/ml/v1/text/chat?version=2024-05-31",
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        {
            "model_id": os.environ.get(
                "WATSONX_MODEL", "ibm/granite-4-h-small"
            ),
            "project_id": project_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
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


def _via_llm7(system: str, user: str) -> Optional[str]:
    # LLM7.io — UK-based aggregator, donor-supported free tier (30 rpm
    # anonymous, 120 rpm with token). OpenAI-compatible. The router picks
    # the best available model regardless of what we ask for, so the
    # "model" field is more of a hint than a constraint. No production
    # SLA, but useful as a tail fallback when first-tier providers cap.
    key = os.environ.get("LLM7_API_KEY")
    if not key:
        return None
    raw = _http_post(
        "https://api.llm7.io/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": os.environ.get("LLM7_MODEL", "gpt-4o-mini"),
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
            # Never silently fall onto a billable model. Operators can choose
            # a specific :free model, while the default follows OpenRouter's
            # rotating zero-cost pool.
            "model": os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": 2000,
            "provider": {"data_collection": "deny"},
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
    model = os.environ.get("HF_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
    raw = _http_post(
        "https://router.huggingface.co/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 2000,
            "temperature": 0.4,
        },
    )
    if not raw:
        return None
    import json
    try:
        return json.loads(raw)["choices"][0]["message"]["content"]
    except (KeyError, ValueError, IndexError):
        return None


DEFAULT_PROVIDERS: List[Callable[[str, str], Optional[str]]] = [
    _via_groq,
    _via_cerebras,
    _via_nvidia,
    _via_mistral,
    _via_codestral,
    _via_sambanova,
    _via_cloudflare,
    _via_watsonx,
    _via_openrouter,
    _via_llm7,
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
