"""Multi-provider LLM fallback chain.

Usage:
    from freshsky_common.llm import LLMChain
    chain = LLMChain()
    text = chain.complete(system="You are an expert.", user="Question?")
"""
from __future__ import annotations

import json
import logging
import os
import re
from importlib import resources
from typing import Callable, List, Optional

import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest
import requests

from .metrics import Metrics
from .privacy import (
    EDUCATION_PRIVACY_PROFILE,
    US_PUBLIC_PRIVACY_PROFILE,
    SensitiveDataError,
    enforce_deidentified_education_input,
    enforce_deidentified_public_input,
)

logger = logging.getLogger(__name__)
_PROVIDER_METRICS = Metrics()


_PROVIDER_ENV_REQUIREMENTS = {
    "groq": (("GROQ_API_KEY", "GROQ_KEY"),),
    "cerebras": (("CEREBRAS_API_KEY", "CEREBRAS_KEY"),),
    "mistral": (("MISTRAL_API_KEY", "MISTRAL_KEY"),),
    "sambanova": (("SAMBANOVA_API_KEY", "SAMBANOVA_KEY"),),
    "cloudflare": (
        ("CLOUDFLARE_API_KEY", "CLOUDFLARE_AI_TOKEN"),
        ("CLOUDFLARE_ACCOUNT_ID",),
    ),
    "ollama": (("OLLAMA_API_KEY",),),
    "openrouter": (("OPENROUTER_API_KEY", "OPENROUTER_KEY"),),
    "huggingface": (
        ("HF_API_KEY", "HUGGINGFACE_API_KEY", "HF_KEY", "HUGGINGFACE_KEY"),
    ),
}


def _load_model_defaults() -> dict[str, str]:
    """Load reviewed model defaults from the packaged registry."""
    try:
        import json

        registry = json.loads(
            resources.files("freshsky_common")
            .joinpath("models.json")
            .read_text(encoding="utf-8")
        )
        return {
            name: details["current"]
            for name, details in registry.get("providers", {}).items()
            if isinstance(details, dict) and details.get("current")
        }
    except (OSError, TypeError, ValueError, KeyError):
        logger.exception("Unable to load packaged LLM model registry")
        return {}


_MODEL_DEFAULTS = _load_model_defaults()

_VERTEX_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_VERTEX_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value
    return ""


def _model_name(env_var: str, provider: str, fallback: str) -> str:
    return os.environ.get(env_var) or _MODEL_DEFAULTS.get(provider) or fallback


def _privacy_profile(value: Optional[str] = None) -> Optional[str]:
    profile = (value if value is not None else os.environ.get("LLM_PRIVACY_PROFILE", "")).strip()
    if not profile:
        return None
    if profile not in {EDUCATION_PRIVACY_PROFILE, US_PUBLIC_PRIVACY_PROFILE}:
        raise ValueError(f"Unknown LLM privacy profile: {profile}")
    return profile


def configured_providers(privacy_profile: Optional[str] = None) -> list[str]:
    """Return configured provider names without exposing credentials."""
    configured = [
        provider
        for provider, requirements in _PROVIDER_ENV_REQUIREMENTS.items()
        if all(any(os.environ.get(name) for name in aliases) for aliases in requirements)
    ]
    if _vertex_configured():
        configured.insert(0, "vertex")
    profile = _privacy_profile(privacy_profile)
    if profile in {EDUCATION_PRIVACY_PROFILE, US_PUBLIC_PRIVACY_PROFILE}:
        allowed = {"vertex", "cloudflare", "ollama", "cerebras", "sambanova"}
        if _env_enabled("GROQ_ZDR_CONFIRMED"):
            allowed.add("groq")
        return [provider for provider in configured if provider in allowed]
    if not _env_enabled("MISTRAL_TRAINING_OPTOUT_CONFIRMED"):
        configured = [provider for provider in configured if provider != "mistral"]
    return configured


def provider_metrics_snapshot() -> dict:
    """Return process-local, privacy-safe LLM provider counters."""
    raw = _PROVIDER_METRICS.snapshot()
    return {
        "configured": configured_providers(),
        "chain": raw.pop("_chain", {}),
        "providers": raw,
        "scope": "current_process",
    }


def install_provider_metrics(app, path: str = "/metrics/providers") -> None:
    """Expose process-local provider telemetry on a Flask application."""
    endpoint = "freshsky_provider_metrics"
    if endpoint in app.view_functions:
        return

    def _provider_metrics_response():
        from flask import jsonify

        response = jsonify(provider_metrics_snapshot())
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    app.add_url_rule(path, endpoint, _provider_metrics_response, methods=["GET"])


def _record(provider: str, event: str) -> None:
    _PROVIDER_METRICS.incr(provider, event)


def _failure_category(status_code: int) -> str:
    if status_code == 429:
        return "rate_limited"
    if status_code in (401, 403):
        return "authentication"
    if status_code == 404:
        return "model_or_endpoint_missing"
    if status_code == 408:
        return "timeout"
    if status_code >= 500:
        return "provider_unavailable"
    return "http_error"


def _http_post(
    provider: str,
    url: str,
    headers: dict,
    payload: dict,
    timeout: int = 30,
) -> Optional[str]:
    _record(provider, "attempts")
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if r.status_code >= 400:
            category = _failure_category(r.status_code)
            _record(provider, "failures")
            _record(provider, category)
            _record(provider, f"http_{r.status_code}")
            logger.warning(
                "llm_provider_failure provider=%s category=%s status=%s",
                provider,
                category,
                r.status_code,
            )
            return None
        return r.text
    except requests.RequestException as exc:
        category = "timeout" if isinstance(exc, requests.Timeout) else "network_error"
        _record(provider, "failures")
        _record(provider, category)
        logger.warning(
            "llm_provider_failure provider=%s category=%s exception=%s",
            provider,
            category,
            type(exc).__name__,
        )
        return None


def _openai_content(provider: str, raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        text = json.loads(raw)["choices"][0]["message"]["content"]
    except (KeyError, TypeError, ValueError, IndexError):
        _record(provider, "failures")
        _record(provider, "invalid_response")
        logger.warning(
            "llm_provider_failure provider=%s category=invalid_response",
            provider,
        )
        return None
    if not isinstance(text, str) or not text.strip():
        _record(provider, "failures")
        _record(provider, "empty_response")
        return None
    _record(provider, "successes")
    return text


def _vertex_configured() -> bool:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.environ.get("VERTEX_AI_LOCATION", "us-central1").strip()
    model = os.environ.get("VERTEX_AI_MODEL", "gemini-2.5-flash-lite").strip()
    return bool(
        _env_enabled("VERTEX_AI_ENABLED")
        and _VERTEX_RESOURCE_RE.fullmatch(project)
        and _VERTEX_RESOURCE_RE.fullmatch(location)
        and _VERTEX_MODEL_RE.fullmatch(model)
    )


def _vertex_content(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        parts = json.loads(raw)["candidates"][0]["content"]["parts"]
        text = "".join(
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict)
        ).strip()
    except (KeyError, TypeError, ValueError, IndexError):
        _record("vertex", "failures")
        _record("vertex", "invalid_response")
        logger.warning(
            "llm_provider_failure provider=vertex category=invalid_response"
        )
        return None
    if not text:
        _record("vertex", "failures")
        _record("vertex", "empty_response")
        return None
    _record("vertex", "successes")
    return text


def _via_vertex(system: str, user: str) -> Optional[str]:
    """Use IAM-authenticated Vertex AI when explicitly enabled."""
    if not _vertex_configured():
        return None
    project = os.environ["GOOGLE_CLOUD_PROJECT"].strip()
    location = os.environ.get("VERTEX_AI_LOCATION", "us-central1").strip()
    model = os.environ.get("VERTEX_AI_MODEL", "gemini-2.5-flash-lite").strip()
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(GoogleAuthRequest())
    except Exception as exc:
        _record("vertex", "attempts")
        _record("vertex", "failures")
        _record("vertex", "authentication")
        logger.warning(
            "llm_provider_failure provider=vertex category=authentication exception=%s",
            type(exc).__name__,
        )
        return None
    raw = _http_post(
        "vertex",
        (
            f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
            f"/locations/{location}/publishers/google/models/{model}:generateContent"
        ),
        {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        },
        {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 2000,
            },
        },
    )
    return _vertex_content(raw)


def _via_groq(system: str, user: str) -> Optional[str]:
    key = _first_env("GROQ_API_KEY", "GROQ_KEY")
    if not key:
        return None
    raw = _http_post(
        "groq",
        "https://api.groq.com/openai/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": _model_name("GROQ_MODEL", "groq", "openai/gpt-oss-120b"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": 2000,
        },
    )
    return _openai_content("groq", raw)


def _via_cerebras(system: str, user: str) -> Optional[str]:
    key = _first_env("CEREBRAS_API_KEY", "CEREBRAS_KEY")
    if not key:
        return None
    raw = _http_post(
        "cerebras",
        "https://api.cerebras.ai/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            # Cerebras free tier currently serves llama-3.1-8b + gpt-oss-120b.
            # llama-3.3-70b was removed; using it returns 404 silently. Keep
            # the small fast model as default so the chain doesn't burn a
            # round-trip on every call before falling through.
            "model": _model_name("CEREBRAS_MODEL", "cerebras", "llama-3.1-8b"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": 2000,
        },
    )
    return _openai_content("cerebras", raw)


def _via_mistral(system: str, user: str) -> Optional[str]:
    if not _env_enabled("MISTRAL_TRAINING_OPTOUT_CONFIRMED"):
        return None
    key = _first_env("MISTRAL_API_KEY", "MISTRAL_KEY")
    if not key:
        return None
    raw = _http_post(
        "mistral",
        "https://api.mistral.ai/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": _model_name("MISTRAL_MODEL", "mistral", "mistral-large-latest"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": 2000,
        },
    )
    return _openai_content("mistral", raw)


def _via_sambanova(system: str, user: str) -> Optional[str]:
    # SambaNova Cloud — RDU-accelerated, OpenAI-compatible, persistent free tier.
    # Default model is Meta-Llama-3.3-70B-Instruct (confirmed active May 2026);
    # earlier Llama-3.1-8B/70B/405B-Instruct were deprecated. Override with
    # SAMBANOVA_MODEL for Llama-4-Maverick-17B-128E-Instruct etc.
    key = _first_env("SAMBANOVA_API_KEY", "SAMBANOVA_KEY")
    if not key:
        return None
    raw = _http_post(
        "sambanova",
        "https://api.sambanova.ai/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": _model_name(
                "SAMBANOVA_MODEL", "sambanova", "Meta-Llama-3.3-70B-Instruct"
            ),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": 2000,
        },
    )
    return _openai_content("sambanova", raw)


def _via_cloudflare(system: str, user: str) -> Optional[str]:
    # Cloudflare Workers AI — OpenAI-compatible endpoint, US jurisdiction,
    # permanent free tier of 10,000 neurons/day. Needs both an API token
    # and the account ID (the latter is embedded in the URL). Default
    # model is Llama 3.1 8B Instruct — small, fast, cheap on neurons;
    # override via CLOUDFLARE_MODEL for @cf/meta/llama-3.3-70b-instruct-fp8-fast
    # or @cf/google/gemma-7b-it etc. when the task wants more capability.
    key = _first_env("CLOUDFLARE_API_KEY", "CLOUDFLARE_AI_TOKEN")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not key or not account:
        return None
    raw = _http_post(
        "cloudflare",
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": _model_name(
                "CLOUDFLARE_MODEL", "cloudflare", "@cf/meta/llama-3.1-8b-instruct"
            ),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": 2000,
        },
    )
    return _openai_content("cloudflare", raw)


def _ollama_content(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        text = json.loads(raw)["message"]["content"]
    except (KeyError, TypeError, ValueError):
        _record("ollama", "failures")
        _record("ollama", "invalid_response")
        logger.warning(
            "llm_provider_failure provider=ollama category=invalid_response"
        )
        return None
    if not isinstance(text, str) or not text.strip():
        _record("ollama", "failures")
        _record("ollama", "empty_response")
        return None
    _record("ollama", "successes")
    return text


def _via_ollama(system: str, user: str) -> Optional[str]:
    # Ollama Cloud has a permanent free plan and does not retain prompts or
    # train on them. Cogito 2.1 is MIT-licensed for commercial use.
    key = os.environ.get("OLLAMA_API_KEY")
    if not key:
        return None
    raw = _http_post(
        "ollama",
        "https://ollama.com/api/chat",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": _model_name("OLLAMA_MODEL", "ollama", "cogito-2.1:671b"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": 0.4, "num_predict": 2000},
        },
    )
    return _ollama_content(raw)


def _via_openrouter(system: str, user: str) -> Optional[str]:
    key = _first_env("OPENROUTER_API_KEY", "OPENROUTER_KEY")
    if not key:
        return None
    raw = _http_post(
        "openrouter",
        "https://openrouter.ai/api/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            # Never silently fall onto a billable model. Operators can choose
            # a specific :free model, while the default follows OpenRouter's
            # rotating zero-cost pool.
            "model": _model_name("OPENROUTER_MODEL", "openrouter", "openrouter/free"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": 2000,
            "provider": {"data_collection": "deny", "zdr": True},
        },
    )
    return _openai_content("openrouter", raw)


def _via_huggingface(system: str, user: str) -> Optional[str]:
    key = _first_env("HF_API_KEY", "HUGGINGFACE_API_KEY", "HF_KEY", "HUGGINGFACE_KEY")
    if not key:
        return None
    model = _model_name(
        "HF_MODEL", "huggingface", "meta-llama/Llama-3.1-8B-Instruct"
    )
    raw = _http_post(
        "huggingface",
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
    return _openai_content("huggingface", raw)


DEFAULT_PROVIDERS: List[Callable[[str, str], Optional[str]]] = [
    _via_vertex,
    _via_groq,
    _via_cerebras,
    _via_mistral,
    _via_sambanova,
    _via_cloudflare,
    _via_ollama,
    _via_openrouter,
    _via_huggingface,
]


def _via_groq_with_confirmed_zdr(system: str, user: str) -> Optional[str]:
    if not _env_enabled("GROQ_ZDR_CONFIRMED"):
        return None
    return _via_groq(system, user)


_via_groq_with_confirmed_zdr._provider_name = "groq"  # type: ignore[attr-defined]


US_RESTRICTED_PROVIDERS: List[Callable[[str, str], Optional[str]]] = [
    _via_vertex,
    _via_cloudflare,
    _via_ollama,
    _via_cerebras,
    _via_groq_with_confirmed_zdr,
    _via_sambanova,
]
EDUCATION_PROVIDERS = US_RESTRICTED_PROVIDERS


class LLMChain:
    """Try providers in order, return first non-empty response."""

    def __init__(
        self,
        providers: Optional[List[Callable[[str, str], Optional[str]]]] = None,
        *,
        privacy_profile: Optional[str] = None,
    ):
        self.privacy_profile = _privacy_profile(privacy_profile)
        if providers is not None:
            self.providers = providers
        elif self.privacy_profile in {EDUCATION_PRIVACY_PROFILE, US_PUBLIC_PRIVACY_PROFILE}:
            self.providers = US_RESTRICTED_PROVIDERS
        else:
            self.providers = DEFAULT_PROVIDERS

    def complete(self, system: str, user: str) -> str:
        if self.privacy_profile == EDUCATION_PRIVACY_PROFILE:
            enforce_deidentified_education_input(user)
        elif self.privacy_profile == US_PUBLIC_PRIVACY_PROFILE:
            enforce_deidentified_public_input(user)
        _record("_chain", "calls")
        attempted = []
        for index, fn in enumerate(self.providers):
            provider = getattr(
                fn,
                "_provider_name",
                getattr(fn, "__name__", repr(fn)).removeprefix("_via_"),
            )
            attempted.append(provider)
            try:
                text = fn(system, user)
            except Exception:
                _record(provider, "chain_exceptions")
                logger.exception("LLM provider %s raised", provider)
                continue
            if text:
                _record(provider, "selected")
                _record("_chain", "successes")
                _record("_chain", f"success_depth_{index + 1}")
                if index:
                    _record("_chain", "fallback_successes")
                return text
            _record(provider, "chain_empty")
        _record("_chain", "exhausted")
        logger.error("llm_chain_exhausted providers=%s", ",".join(attempted))
        return ""
