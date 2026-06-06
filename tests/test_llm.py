import json

from freshsky_common import llm


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
