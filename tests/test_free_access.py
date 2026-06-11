from flask import Flask

from freshsky_common.freemium import register_freemium


def make_app():
    app = Flask(__name__)
    app.secret_key = "test"

    @app.route("/")
    def index():
        return "ok"

    register_freemium(app, free_daily_limit=-1)
    return app


def test_legacy_subscribe_routes_redirect_to_donate():
    client = make_app().test_client()
    monthly = client.get("/subscribe")
    yearly = client.get("/subscribe/yearly")
    assert monthly.status_code == 302
    assert monthly.location == "https://www.freshskyai.com/donate"
    assert yearly.status_code == 302
    assert yearly.location == "https://www.freshskyai.com/donate"


def test_user_status_reports_full_free_access():
    response = make_app().test_client().get("/api/user-status")
    payload = response.get_json()
    assert payload["free_access"] is True
    assert payload["full_access"] is True
    assert payload["daily_limit"] is None
    assert payload["is_pro"] is False
    assert payload["donate_url"].endswith("/donate")
    assert "pricing_url" not in payload


def test_civic_host_has_the_same_full_access():
    client = make_app().test_client()
    response = client.get(
        "/api/user-status",
        headers={"Host": "nfirs.freshskyai.com"},
    )
    payload = response.get_json()
    assert payload["community_mode"] is True
    assert "pricing_url" not in payload
    assert client.get(
        "/subscribe",
        headers={"Host": "nfirs.freshskyai.com"},
    ).location == "https://www.freshskyai.com/donate"
