import sys
from types import SimpleNamespace

from flask import Flask

from freshsky_common.freemium import register_freemium


def make_app(**freemium_options):
    app = Flask(__name__)
    app.secret_key = "test"

    @app.route("/")
    def index():
        return "ok"

    register_freemium(app, **freemium_options)
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
    assert payload["donate_url"].endswith("/donate")
    assert "pricing_url" not in payload
    assert "is_pro" not in payload


def test_stripe_secret_enables_billing_without_retired_price_ids(monkeypatch):
    created = {}

    def create_portal(**kwargs):
        created.update(kwargs)
        return SimpleNamespace(url="https://billing.stripe.test/session")

    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **kwargs: SimpleNamespace(
                data=[SimpleNamespace(id="cus_supporter")]
            )
        ),
        billing_portal=SimpleNamespace(
            Session=SimpleNamespace(create=create_portal)
        ),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)

    app = make_app(
        stripe_secret_key="sk_test_donations",
        primary_url="https://www.freshskyai.com",
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_email"] = "supporter@example.com"

    status = client.get("/api/user-status").get_json()
    response = client.get("/billing")

    assert status["stripe_enabled"] is True
    assert response.status_code == 302
    assert response.location == "https://billing.stripe.test/session"
    assert created == {
        "customer": "cus_supporter",
        "return_url": "https://www.freshskyai.com",
    }


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
