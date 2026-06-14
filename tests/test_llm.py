import json

from freshsky_common import llm
from freshsky_common.metrics import Metrics


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_openrouter_defaults_to_free_and_denies_collection(monkeypatch):
    payloads = []

    def fake_post(url, headers=None, json=None, timeout=None):
        payloads.append(json)
        return FakeResponse({"choices": [{"message": {"content": "answer"}}]})

    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setattr(llm.requests, "post", fake_post)

    assert llm._via_openrouter("system", "user") == "answer"
    assert payloads[0]["model"] == "openrouter/free"
    assert payloads[0]["provider"]["data_collection"] == "deny"


def test_legacy_batch_secret_names_remain_supported(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json))
        return FakeResponse(
            {"choices": [{"message": {"content": "legacy secret answer"}}]}
        )

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_KEY", "legacy-groq-key")
    monkeypatch.setattr(llm.requests, "post", fake_post)

    assert llm._via_groq("system", "user") == "legacy secret answer"
    assert calls[0][1]["Authorization"] == "Bearer legacy-groq-key"


def test_huggingface_uses_current_chat_router(monkeypatch):
    urls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        urls.append(url)
        return FakeResponse({"choices": [{"message": {"content": "answer"}}]})

    monkeypatch.setenv("HF_API_KEY", "key")
    monkeypatch.setattr(llm.requests, "post", fake_post)

    assert llm._via_huggingface("system", "user") == "answer"
    assert urls == ["https://router.huggingface.co/v1/chat/completions"]


def test_ollama_uses_commercial_model_and_native_response(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json))
        return FakeResponse({"message": {"role": "assistant", "content": "answer"}})

    monkeypatch.setenv("OLLAMA_API_KEY", "key")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setattr(llm.requests, "post", fake_post)

    assert llm._via_ollama("system", "user") == "answer"
    assert calls[0][0] == "https://ollama.com/api/chat"
    assert calls[0][1]["Authorization"] == "Bearer key"
    assert calls[0][2]["model"] == "cogito-2.1:671b"
    assert calls[0][2]["stream"] is False


def test_removed_noncommercial_providers_are_not_configurable(monkeypatch):
    monkeypatch.setenv("NVIDIA_NIM_KEY", "removed")
    monkeypatch.setenv("CODESTRAL_API_KEY", "removed")
    monkeypatch.setenv("LLM7_API_KEY", "removed")

    configured = llm.configured_providers()
    assert "nvidia_nim" not in configured
    assert "codestral" not in configured
    assert "llm7" not in configured
    assert not hasattr(llm, "_via_nvidia")
    assert not hasattr(llm, "_via_codestral")
    assert not hasattr(llm, "_via_llm7")


def test_default_provider_order_prefers_ollama_before_aggregators():
    assert [provider.__name__ for provider in llm.DEFAULT_PROVIDERS] == [
        "_via_groq",
        "_via_cerebras",
        "_via_mistral",
        "_via_sambanova",
        "_via_cloudflare",
        "_via_ollama",
        "_via_openrouter",
        "_via_huggingface",
    ]


def test_provider_uses_reviewed_registry_default(monkeypatch):
    payloads = []

    def fake_post(url, headers=None, json=None, timeout=None):
        payloads.append(json)
        return FakeResponse({"choices": [{"message": {"content": "answer"}}]})

    monkeypatch.setenv("GROQ_API_KEY", "key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setitem(llm._MODEL_DEFAULTS, "groq", "reviewed-model")
    monkeypatch.setattr(llm.requests, "post", fake_post)

    assert llm._via_groq("system", "user") == "answer"
    assert payloads[0]["model"] == "reviewed-model"


def test_environment_model_override_wins_over_registry(monkeypatch):
    monkeypatch.setitem(llm._MODEL_DEFAULTS, "groq", "reviewed-model")
    monkeypatch.setenv("GROQ_MODEL", "operator-model")

    assert llm._model_name("GROQ_MODEL", "groq", "fallback-model") == "operator-model"


def test_provider_telemetry_classifies_rate_limits(monkeypatch):
    monkeypatch.setattr(llm, "_PROVIDER_METRICS", Metrics())
    monkeypatch.setenv("GROQ_API_KEY", "key")
    monkeypatch.setattr(
        llm.requests,
        "post",
        lambda *args, **kwargs: FakeResponse({"error": "limited"}, status_code=429),
    )

    assert llm._via_groq("system", "user") is None
    snapshot = llm.provider_metrics_snapshot()
    assert snapshot["providers"]["groq"] == {
        "attempts": 1,
        "failures": 1,
        "rate_limited": 1,
        "http_429": 1,
    }


def test_chain_telemetry_records_fallback_and_exhaustion(monkeypatch):
    monkeypatch.setattr(llm, "_PROVIDER_METRICS", Metrics())

    def empty_provider(system, user):
        return None

    def working_provider(system, user):
        return "answer"

    assert llm.LLMChain([empty_provider, working_provider]).complete("s", "u") == "answer"
    snapshot = llm.provider_metrics_snapshot()
    assert snapshot["chain"]["calls"] == 1
    assert snapshot["chain"]["successes"] == 1
    assert snapshot["chain"]["fallback_successes"] == 1
    assert snapshot["chain"]["success_depth_2"] == 1
    assert snapshot["providers"]["working_provider"]["selected"] == 1

    monkeypatch.setattr(llm, "_PROVIDER_METRICS", Metrics())
    assert llm.LLMChain([]).complete("s", "u") == ""
    assert llm.provider_metrics_snapshot()["chain"]["exhausted"] == 1


def test_provider_metrics_endpoint_is_private_data_free(monkeypatch):
    from flask import Flask

    monkeypatch.setattr(llm, "_PROVIDER_METRICS", Metrics())
    llm._record("groq", "attempts")
    app = Flask(__name__)
    llm.install_provider_metrics(app)

    response = app.test_client().get("/metrics/providers")
    payload = response.get_json()
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert payload["providers"]["groq"]["attempts"] == 1
    assert set(payload) == {"chain", "configured", "providers", "scope"}
