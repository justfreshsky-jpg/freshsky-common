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


def test_subscribe_routes_redirect_to_pricing():
    client = make_app().test_client()
    monthly = client.get("/subscribe")
    yearly = client.get("/subscribe/yearly")
    assert monthly.status_code == 302
    assert monthly.location == "https://www.freshskyai.com/pricing?plan=monthly"
    assert yearly.status_code == 302
    assert yearly.location == "https://www.freshskyai.com/pricing?plan=yearly"


def test_user_status_reports_free_and_pro_options():
    response = make_app().test_client().get("/api/user-status")
    payload = response.get_json()
    assert payload["free_access"] is True
    assert payload["sponsor_url"].endswith("/sponsor")
    assert payload["pricing_url"].endswith("/pricing")


def test_civic_host_suppresses_pricing():
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
    ).location == "/"
