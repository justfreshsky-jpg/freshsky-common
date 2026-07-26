import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from flask import Flask

from freshsky_common.freemium import register_freemium
from freshsky_common import freemium


def make_app(**freemium_options):
    app = Flask(__name__)
    app.secret_key = "test"

    @app.route("/")
    def index():
        return "ok"

    register_freemium(app, **freemium_options)
    return app


def test_disabled_subscription_routes_return_to_app():
    client = make_app().test_client()
    monthly = client.get("/subscribe")
    yearly = client.get("/subscribe/yearly")
    assert monthly.status_code == 302
    assert monthly.location == "/"
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
    assert payload["free_preview_limit"] is None
    assert "sponsor_url" not in payload
    assert "pricing_url" not in payload
    assert "is_pro" not in payload
    assert payload["plan_tier"] == "guest"
    assert payload["usage_unit_limits"]["rolling_30_day_previews"] == 3
    assert payload["server_saved_projects"] is False
    assert len(payload["workspace_ids"]) == 5


def test_versioned_access_bundle_replaces_stable_script_path():
    app = make_app()
    app.view_functions["index"] = lambda: (
        '<html><body><script src="/freemium.js"></script></body></html>'
    )

    client = app.test_client()
    page = client.get("/")
    assert page.status_code == 200
    assert 'src="/freshsky-access-v061.js"' in page.get_data(as_text=True)
    assert 'src="/freemium.js"' not in page.get_data(as_text=True)

    bundle = client.get("/freshsky-access-v061.js")
    assert bundle.status_code == 200
    bundle_text = bundle.get_data(as_text=True)
    assert "installVisualSystem" in bundle_text
    inner_html_index = bundle_text.index("bar.innerHTML")
    assert inner_html_index < bundle_text.index(
        "syncBarOffset(bar);",
        inner_html_index,
    )
    assert "bar.getBoundingClientRect().height" in bundle_text
    assert "Math.max(54, measuredHeight)" in bundle_text
    assert "new ResizeObserver" in bundle_text
    assert "window.__freemiumBarResizeObserver.observe(bar)" in bundle_text
    assert bundle.headers["Cache-Control"] == "public, max-age=31536000, immutable"

    compatibility = client.get("/freemium.js")
    assert compatibility.status_code == 200
    assert compatibility.headers["Cache-Control"] == "no-store, max-age=0"


def test_versioned_access_bundle_is_injected_when_template_has_no_script():
    app = make_app()
    app.view_functions["index"] = lambda: "<html><body><main>Tool</main></body></html>"

    page = app.test_client().get("/")
    body = page.get_data(as_text=True)
    assert body.count('src="/freshsky-access-v061.js"') == 1
    assert body.index("<main>") < body.index('src="/freshsky-access-v061.js"')


def test_optional_global_post_gate_counts_three_previews():
    app = make_app(
        stripe_secret_key="sk_test_subscription",
        subscriptions_enabled=True,
        subscription_tier="advanced",
        subscription_price_id="price_advanced_monthly",
        subscription_amount_cents=2999,
        free_request_limit=3,
        gate_all_post=True,
    )

    @app.post("/api/work")
    def work():
        return {"ok": True}

    client = app.test_client()
    assert [client.post("/api/work").status_code for _ in range(3)] == [200, 200, 200]
    blocked = client.post("/api/work")
    assert blocked.status_code == 402
    assert blocked.get_json()["price_cents"] == 2999


def test_higher_portfolio_plan_unlocks_lower_tier_app(monkeypatch):
    subscription_list_args = {}
    plus_item = SimpleNamespace(
        price=SimpleNamespace(
            id="price_plus_monthly",
            product="prod_plus",
        )
    )
    active_subscription = SimpleNamespace(
        status="active",
        current_period_end=1_800_000_000,
        items=SimpleNamespace(data=[plus_item]),
    )
    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **kwargs: SimpleNamespace(
                data=[SimpleNamespace(id="cus_portfolio")]
            )
        ),
        Subscription=SimpleNamespace(
            list=lambda **kwargs: (
                subscription_list_args.update(kwargs)
                or SimpleNamespace(data=[active_subscription])
            )
        ),
        Product=SimpleNamespace(
            retrieve=lambda product_id: SimpleNamespace(
                id=product_id,
                name="FreshSky Plus 1999 month",
                metadata={},
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    app = make_app(
        stripe_secret_key="sk_test_subscription",
        subscriptions_enabled=True,
        subscription_tier="focus",
        subscription_price_id="price_focus_monthly",
        subscription_amount_cents=999,
        free_request_limit=3,
    )
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_email"] = "person@example.com"

    response = client.get("/api/user-status")

    assert response.get_json()["full_access"] is True
    assert response.get_json()["subscription_tier"] == "plus"
    assert response.get_json()["paid_daily_limit"] == 60
    assert response.get_json()["paid_monthly_limit"] == 300
    assert response.get_json()["entitlement_expires_at"] == datetime.fromtimestamp(
        1_800_000_000,
        tz=timezone.utc,
    ).isoformat()
    assert subscription_list_args["expand"] == ["data.items.data.price"]


def test_paid_plan_usage_limit_returns_429(monkeypatch):
    monkeypatch.setenv("FRESHSKY_ENFORCE_PAID_LIMITS", "true")
    monkeypatch.setattr(
        "freshsky_common.freemium._consume_paid_allowance",
        lambda email, tier, key, **kwargs: (
            False,
            {
                "daily": 20,
                "monthly": 100,
                "daily_used": 20,
                "monthly_used": 87,
            },
        ),
    )
    limited_app = Flask(__name__)
    limited_app.secret_key = "test"
    check = register_freemium(
        limited_app,
        stripe_secret_key="sk_test_subscription",
        subscriptions_enabled=True,
        subscription_tier="focus",
        subscription_price_id="price_focus_monthly",
        subscription_amount_cents=999,
        free_request_limit=3,
    )

    @limited_app.post("/api/work")
    def limited_work():
        return check() or {"ok": True}

    limited_client = limited_app.test_client()
    with limited_client.session_transaction() as user_session:
        user_session["user_email"] = "person@example.com"
        user_session["subscription_tier"] = "focus"
        user_session["subscription_checked_at"] = 9999999999
    response = limited_client.post("/api/work")

    assert response.status_code == 429
    assert response.get_json()["code"] == "plan_usage_limit_reached"
    assert response.get_json()["daily_limit"] == 20


def test_gate_reserves_weighted_workflow_units(monkeypatch):
    captured = {}

    def consume(email, tier, key, **kwargs):
        captured.update(
            email=email,
            tier=tier,
            key=key,
            **kwargs,
        )
        return True, {
            "daily": 60,
            "monthly": 300,
            "daily_used": 5,
            "monthly_used": 5,
            "required_units": 5,
            "quota_unit": "usage_unit",
        }

    monkeypatch.setenv("FRESHSKY_ENFORCE_PAID_LIMITS", "true")
    monkeypatch.setattr(
        "freshsky_common.freemium._consume_paid_allowance",
        consume,
    )
    app = Flask(__name__)
    app.secret_key = "test"
    check = register_freemium(
        app,
        stripe_secret_key="sk_test_subscription",
        subscriptions_enabled=True,
        subscription_tier="plus",
        subscription_price_id="price_plus_monthly",
        subscription_amount_cents=1999,
        free_request_limit=3,
        workspace_id="education",
    )

    @app.post("/api/work")
    def work():
        return check(workflow_class="standard_agent") or {"ok": True}

    client = app.test_client()
    with client.session_transaction() as state:
        state["user_email"] = "person@example.com"
        state["subscription_tier"] = "plus"
        state["subscription_checked_at"] = 9999999999

    assert client.post("/api/work").status_code == 200
    assert captured["usage_units"] == 5
    assert captured["workflow_class"] == "standard_agent"
    assert captured["workspace_id"] == "education"


def test_workspace_policy_does_not_treat_plus_as_civic_access(monkeypatch):
    plus_item = SimpleNamespace(
        price=SimpleNamespace(id="price_plus_monthly", product="prod_plus")
    )
    active_subscription = SimpleNamespace(
        status="active",
        metadata={},
        items=SimpleNamespace(data=[plus_item]),
    )
    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **kwargs: SimpleNamespace(
                data=[SimpleNamespace(id="cus_portfolio")]
            )
        ),
        Subscription=SimpleNamespace(
            list=lambda **kwargs: SimpleNamespace(data=[active_subscription])
        ),
        Product=SimpleNamespace(
            retrieve=lambda _product_id: SimpleNamespace(
                name="FreshSky Plus",
                metadata={},
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    app = make_app(
        stripe_secret_key="sk_test_subscription",
        subscriptions_enabled=True,
        subscription_tier="civic",
        subscription_price_id="price_civic_monthly",
        subscription_amount_cents=1499,
        free_request_limit=3,
        workspace_id="civic",
    )
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_email"] = "person@example.com"

    payload = client.get("/api/user-status").get_json()

    assert payload["full_access"] is False
    assert payload["workspace_access"] is True  # Guest preview access remains.
    assert payload["workspace_full_access"] is False
    assert payload["plan_tier"] == "guest"


def test_focus_workspace_is_restored_from_verified_subscription_metadata(
    monkeypatch,
):
    focus_item = SimpleNamespace(
        price=SimpleNamespace(
            id="price_focus_monthly",
            product=SimpleNamespace(
                name="FreshSky Focus",
                metadata={"freshsky_tier": "focus"},
            ),
        )
    )
    active_subscription = SimpleNamespace(
        status="active",
        metadata={"workspace_id": "education"},
        items=SimpleNamespace(data=[focus_item]),
    )
    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **kwargs: SimpleNamespace(
                data=[SimpleNamespace(id="cus_focus")]
            )
        ),
        Subscription=SimpleNamespace(
            list=lambda **kwargs: SimpleNamespace(data=[active_subscription])
        ),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    app = make_app(
        stripe_secret_key="sk_test_subscription",
        subscriptions_enabled=True,
        subscription_tier="focus",
        subscription_price_id="price_focus_monthly",
        subscription_amount_cents=999,
        free_request_limit=3,
        workspace_id="education",
    )
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_email"] = "person@example.com"

    payload = client.get("/api/user-status").get_json()

    assert payload["full_access"] is True
    assert payload["plan_tier"] == "focus"
    assert payload["selected_workspace"] == "education"


def test_verified_owner_status_is_finite_and_workspace_complete():
    app = make_app(
        stripe_secret_key="sk_test_subscription",
        subscriptions_enabled=True,
        subscription_tier="civic",
        subscription_price_id="price_civic_monthly",
        subscription_amount_cents=1499,
        free_request_limit=3,
        workspace_id="civic",
    )
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_email"] = "admin@freshskyllc.com"
        state["user_email_verified"] = True

    payload = client.get("/api/user-status").get_json()

    assert payload["plan_tier"] == "owner"
    assert payload["verified_owner"] is True
    assert payload["full_access"] is True
    assert payload["paid_daily_limit"] == 500
    assert payload["paid_monthly_limit"] == 2000
    assert payload["monthly_provider_cost_cap_usd"] == "5.00"
    assert payload["entitlement_expires_at"] is None
    assert len(payload["workspace_ids"]) == 5


def test_subapp_paid_usage_uses_signed_central_meter(monkeypatch):
    captured = {}

    class MeterResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "allowed": True,
                "usage": {
                    "daily": 30,
                    "monthly": 300,
                    "daily_used": 1,
                    "monthly_used": 1,
                },
            }

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return MeterResponse()

    monkeypatch.setenv("K_SERVICE", "foiahelper")
    monkeypatch.setattr("requests.post", post)

    allowed, usage = freemium._consume_paid_allowance(
        "person@example.com", "focus", "sk_test_subscription"
    )

    assert allowed is True
    assert usage["monthly_used"] == 1
    assert captured["url"].endswith("/internal/paid-usage/consume")
    assert b"person@example.com" not in captured["data"]
    assert captured["headers"]["X-FreshSky-Usage-Signature"].startswith("v1=")
    body = json.loads(captured["data"])
    reservation_id = body["reservation_id"]
    assert len(reservation_id) == 32
    assert set(reservation_id) <= set("0123456789abcdef")
    timestamp = captured["headers"]["X-FreshSky-Usage-Timestamp"]
    expected_signature = hmac.new(
        b"sk_test_subscription",
        timestamp.encode("ascii") + b"." + captured["data"],
        hashlib.sha256,
    ).hexdigest()
    assert captured["headers"]["X-FreshSky-Usage-Signature"] == (
        f"v1={expected_signature}"
    )


def test_managed_app_never_reuses_stripe_secret_for_usage_signing(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "workspace-service")
    monkeypatch.delenv("FRESHSKY_USAGE_HMAC_KEY", raising=False)
    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: pytest.fail(
            "meter transport must not start without the dedicated key"
        ),
    )
    app = Flask(__name__)
    app.secret_key = "test"
    gate = register_freemium(
        app,
        stripe_secret_key="sk_test_payment_only",
        subscriptions_enabled=True,
        subscription_tier="advanced",
        subscription_price_id="price_advanced",
        subscription_amount_cents=2999,
        workspace_id="funding",
    )
    with app.test_request_context("/api/work"):
        from flask import session

        session["user_email"] = "person@example.com"
        session["subscription_tier"] = "advanced"
        session["subscription_checked_at"] = freemium.time.time()
        response, status = gate(workflow_class="preview")

    assert status == 503
    assert response.get_json()["code"] == "usage_meter_unavailable"


def test_subapp_usage_reservations_are_unique_within_one_second(monkeypatch):
    bodies = []
    headers = []

    class MeterResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "allowed": True,
                "usage": {
                    "daily": 20,
                    "monthly": 100,
                    "daily_used": len(bodies),
                    "monthly_used": len(bodies),
                },
            }

    def post(_url, **kwargs):
        bodies.append(json.loads(kwargs["data"]))
        headers.append(kwargs["headers"])
        return MeterResponse()

    monkeypatch.setenv("K_SERVICE", "workspace-service")
    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr(freemium.time, "time", lambda: 1_900_000_000)

    freemium._consume_paid_allowance(
        "person@example.com",
        "focus",
        "usage-secret",
    )
    freemium._consume_paid_allowance(
        "person@example.com",
        "focus",
        "usage-secret",
    )

    assert headers[0]["X-FreshSky-Usage-Timestamp"] == headers[1][
        "X-FreshSky-Usage-Timestamp"
    ]
    assert bodies[0]["reservation_id"] != bodies[1]["reservation_id"]
    assert headers[0]["X-FreshSky-Usage-Signature"] != headers[1][
        "X-FreshSky-Usage-Signature"
    ]


def test_subapp_can_reuse_reservation_id_for_a_transport_retry(monkeypatch):
    encoded_bodies = []

    class MeterResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "allowed": True,
                "usage": {
                    "daily": 20,
                    "monthly": 100,
                    "daily_used": 1,
                    "monthly_used": 1,
                },
            }

    def post(_url, **kwargs):
        encoded_bodies.append(kwargs["data"])
        return MeterResponse()

    monkeypatch.setenv("K_SERVICE", "workspace-service")
    monkeypatch.setattr("requests.post", post)
    reservation_id = "d" * 32

    for _ in range(2):
        freemium._consume_paid_allowance(
            "person@example.com",
            "focus",
            "usage-secret",
            reservation_id=reservation_id,
        )

    assert json.loads(encoded_bodies[0])["reservation_id"] == reservation_id
    assert encoded_bodies[0] == encoded_bodies[1]


def test_subapp_rejects_malformed_usage_reservation_id(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "workspace-service")

    with pytest.raises(
        ValueError,
        match="reservation_id must be 32 lowercase hexadecimal characters",
    ):
        freemium._consume_paid_allowance(
            "person@example.com",
            "focus",
            "usage-secret",
            reservation_id="not-random",
        )


def test_weighted_usage_fails_closed_against_legacy_central_meter(monkeypatch):
    class LegacyMeterResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "allowed": True,
                "usage": {
                    "daily": 60,
                    "monthly": 300,
                    "daily_used": 1,
                    "monthly_used": 1,
                },
            }

    monkeypatch.setenv("K_SERVICE", "workspace-service")
    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: LegacyMeterResponse(),
    )

    with pytest.raises(
        RuntimeError,
        match="does not support weighted reservations",
    ):
        freemium._consume_paid_allowance(
            "person@example.com",
            "plus",
            "usage-secret",
            usage_units=5,
            workflow_class="standard_agent",
            workspace_id="education",
        )


def test_stripe_secret_enables_billing_without_retired_price_ids(monkeypatch):
    created = {}

    def create_portal(**kwargs):
        created.update(kwargs)
        return SimpleNamespace(url="https://billing.stripe.test/session")

    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **kwargs: SimpleNamespace(
                data=[SimpleNamespace(id="cus_customer")]
            )
        ),
        billing_portal=SimpleNamespace(
            Session=SimpleNamespace(create=create_portal)
        ),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)

    app = make_app(
        stripe_secret_key="sk_test_billing",
        primary_url="https://www.freshskyai.com",
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_email"] = "customer@example.com"

    status = client.get("/api/user-status").get_json()
    response = client.get("/billing")

    assert status["stripe_enabled"] is True
    assert response.status_code == 302
    assert response.location == "https://billing.stripe.test/session"
    assert created == {
        "customer": "cus_customer",
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
    ).location == "/"


def test_civic_plan_sits_between_focus_and_plus():
    assert freemium.PLAN_RANK["focus"] < freemium.PLAN_RANK["civic"]
    assert freemium.PLAN_RANK["civic"] < freemium.PLAN_RANK["plus"]
    assert freemium.PLAN_LIMITS["civic"] == {"daily": 40, "monthly": 200}


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


def test_google_login_can_use_a_separate_auth_broker_callback_base():
    app = make_app(
        google_client_id="client.apps.googleusercontent.com",
        google_client_secret="secret",
        primary_url="https://workspace.freshskyai.com",
        auth_broker_url="https://auth.freshskyai.com",
    )

    response = app.test_client().get("/auth/google")
    query = parse_qs(urlparse(response.location).query)
    status = app.test_client().get("/api/user-status").get_json()

    assert query["redirect_uri"] == [
        "https://auth.freshskyai.com/auth/google/callback"
    ]
    assert status["auth_broker_enabled"] is True


def test_auth_broker_url_rejects_non_https_remote_hosts():
    with pytest.raises(ValueError, match="must be HTTPS"):
        make_app(
            google_client_id="client.apps.googleusercontent.com",
            google_client_secret="secret",
            primary_url="https://workspace.freshskyai.com",
            auth_broker_url="http://auth.example.com",
        )
