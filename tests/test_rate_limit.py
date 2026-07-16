from flask import Flask, jsonify

from freshsky_common.rate_limit import register_global_rate_limits


def make_app(owner_email=""):
    app = Flask(__name__)
    app.secret_key = "test"

    @app.post("/api/generate")
    def generate():
        return jsonify(ok=True)

    register_global_rate_limits(
        app,
        ip_per_hour=1,
        user_per_day=1,
        owner_email=owner_email,
    )
    return app


def test_free_access_still_applies_abuse_protection():
    client = make_app().test_client()
    assert client.post("/api/generate").status_code == 200
    limited = client.post("/api/generate")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0


def test_owner_session_bypasses_optional_abuse_limit():
    client = make_app(owner_email="owner@example.com").test_client()
    with client.session_transaction() as state:
        state["user_email"] = "owner@example.com"
    for _ in range(3):
        assert client.post("/api/generate").status_code == 200
