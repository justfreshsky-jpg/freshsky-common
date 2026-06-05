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


def test_legacy_subscribe_routes_redirect_to_sponsor():
    client = make_app().test_client()
    for path in ("/subscribe", "/subscribe/yearly"):
        response = client.get(path)
        assert response.status_code == 302
        assert response.location == "https://www.freshskyai.com/sponsor"


def test_user_status_reports_free_access():
    response = make_app().test_client().get("/api/user-status")
    payload = response.get_json()
    assert payload["free_access"] is True
    assert payload["sponsor_url"].endswith("/sponsor")
    assert "pricing_url" not in payload
