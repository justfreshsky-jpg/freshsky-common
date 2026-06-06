from flask import Flask, jsonify, session

from freshsky_common.rate_limit import register_global_rate_limits


def make_app():
    app = Flask(__name__)
    app.secret_key = "test"

    @app.post("/api/generate")
    def generate():
        return jsonify(ok=True)

    register_global_rate_limits(
        app,
        ip_per_hour=1,
        user_per_day=1,
        owner_email="",
        pro_bypass=lambda: bool(session.get("is_pro")),
    )
    return app


def test_free_user_is_limited():
    client = make_app().test_client()
    assert client.post("/api/generate").status_code == 200
    assert client.post("/api/generate").status_code == 429


def test_pro_user_bypasses_free_limit():
    client = make_app().test_client()
    with client.session_transaction() as state:
        state["user_email"] = "pro@example.com"
        state["is_pro"] = True
    for _ in range(3):
        assert client.post("/api/generate").status_code == 200
