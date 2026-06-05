import json

from freshsky_common import llm


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_watsonx_uses_iam_token_and_chat_endpoint(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, data=None, timeout=None):
        calls.append((url, headers, json, data, timeout))
        if url == "https://iam.cloud.ibm.com/identity/token":
            return FakeResponse({"access_token": "token", "expires_in": 3600})
        return FakeResponse(
            {"choices": [{"message": {"content": "watsonx answer"}}]}
        )

    monkeypatch.setenv("WATSONX_API_KEY", "api-key")
    monkeypatch.setenv("WATSONX_PROJECT_ID", "project-id")
    monkeypatch.setattr(llm.requests, "post", fake_post)
    monkeypatch.setattr(llm, "_WATSONX_TOKEN", "")
    monkeypatch.setattr(llm, "_WATSONX_TOKEN_EXPIRES_AT", 0.0)

    assert llm._via_watsonx("system", "user") == "watsonx answer"
    assert calls[1][0].endswith("/ml/v1/text/chat?version=2024-05-31")
    assert calls[1][2]["project_id"] == "project-id"
    assert calls[1][2]["model_id"] == "ibm/granite-4-h-small"


def test_watsonx_skips_without_project(monkeypatch):
    monkeypatch.setenv("WATSONX_API_KEY", "api-key")
    monkeypatch.delenv("WATSONX_PROJECT_ID", raising=False)
    assert llm._via_watsonx("system", "user") is None


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
