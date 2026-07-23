import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

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
    assert yearly.location == "/subscribe"


def test_monthly_subscription_checkout_is_opt_in_and_server_priced(monkeypatch):
    created = {}

    def create_checkout(**kwargs):
        created.update(kwargs)
        return SimpleNamespace(url="https://checkout.stripe.test/monthly")

    fake_stripe = SimpleNamespace(
        api_key=None,
        checkout=SimpleNamespace(Session=SimpleNamespace(create=create_checkout)),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    app = make_app(
        stripe_secret_key="sk_test_subscription",
        primary_url="https://foia.example",
        subscriptions_enabled=True,
        subscription_tier="focus",
        subscription_price_id="price_focus_monthly",
        subscription_amount_cents=999,
        free_request_limit=3,
    )

    response = app.test_client().get("/subscribe")

    assert response.status_code == 303
    assert response.location == "https://checkout.stripe.test/monthly"
    assert created["mode"] == "subscription"
    assert created["line_items"] == [
        {"price": "price_focus_monthly", "quantity": 1}
    ]
    assert created["allow_promotion_codes"] is True
    assert created["metadata"] == {"app_host": "foia.example", "tier": "focus"}


def test_verified_checkout_creates_email_session(monkeypatch):
    checkout = SimpleNamespace(
        status="complete",
        mode="subscription",
        subscription="sub_123",
        metadata={"app_host": "foia.example", "tier": "focus"},
        customer_details=SimpleNamespace(email="Person@Example.com"),
    )
    fake_stripe = SimpleNamespace(
        api_key=None,
        checkout=SimpleNamespace(
            Session=SimpleNamespace(retrieve=lambda _session_id: checkout)
        ),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    app = make_app(
        stripe_secret_key="sk_test_subscription",
        primary_url="https://foia.example",
        subscriptions_enabled=True,
        subscription_tier="focus",
        subscription_price_id="price_focus_monthly",
        subscription_amount_cents=999,
        free_request_limit=3,
    )
    client = app.test_client()

    response = client.get("/subscription/success?session_id=cs_test_123")

    assert response.status_code == 303
    assert response.location == "https://foia.example/?checkout=success"
    with client.session_transaction() as user_session:
        assert user_session["user_email"] == "person@example.com"
        assert user_session["subscription_tier"] == "focus"


def test_user_status_reports_full_free_access():
    response = make_app().test_client().get("/api/user-status")
    payload = response.get_json()
    assert payload["free_access"] is True
    assert payload["full_access"] is True
    assert payload["daily_limit"] is None
    assert payload["donate_url"].endswith("/donate")
    assert "pricing_url" not in payload
    assert "is_pro" not in payload


def test_versioned_access_bundle_replaces_stable_script_path():
    app = make_app()
    app.view_functions["index"] = lambda: (
        '<html><body><script src="/freemium.js"></script></body></html>'
    )

    client = app.test_client()
    page = client.get("/")
    assert page.status_code == 200
    assert 'src="/freshsky-access-v051.js"' in page.get_data(as_text=True)
    assert 'src="/freemium.js"' not in page.get_data(as_text=True)

    bundle = client.get("/freshsky-access-v051.js")
    assert bundle.status_code == 200
    assert "installVisualSystem" in bundle.get_data(as_text=True)
    assert bundle.headers["Cache-Control"] == "public, max-age=31536000, immutable"

    compatibility = client.get("/freemium.js")
    assert compatibility.status_code == 200
    assert compatibility.headers["Cache-Control"] == "no-store, max-age=0"


def test_versioned_access_bundle_is_injected_when_template_has_no_script():
    app = make_app()
    app.view_functions["index"] = lambda: "<html><body><main>Tool</main></body></html>"

    page = app.test_client().get("/")
    body = page.get_data(as_text=True)
    assert body.count('src="/freshsky-access-v051.js"') == 1
    assert body.index("<main>") < body.index('src="/freshsky-access-v051.js"')


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


def test_optional_public_routes_are_disabled_by_default():
    client = make_app().test_client()
    assert client.post("/api/notify", json={"email": "person@example.com"}).status_code == 404
    assert client.get("/metrics/providers").status_code == 404


def test_shared_visual_system_is_local_and_cacheable():
    response = make_app().test_client().get("/freshsky.css")

    assert response.status_code == 200
    assert response.mimetype == "text/css"
    assert "Fresh Sky 2026 shared visual system" in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "public, max-age=3600"


def test_google_login_uses_fixed_callback_and_nonce():
    app = make_app(
        google_client_id="client.apps.googleusercontent.com",
        google_client_secret="secret",
        primary_url="https://www.freshskyai.com",
    )
    response = app.test_client().get(
        "/auth/google?next=/billing",
        headers={"Host": "attacker.example"},
    )
    query = parse_qs(urlparse(response.location).query)
    assert query["redirect_uri"] == [
        "https://www.freshskyai.com/auth/google/callback"
    ]
    assert query["nonce"][0]
    assert query["state"][0]
