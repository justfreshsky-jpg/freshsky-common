import hashlib
import hmac
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from flask import Flask

from freshsky_common.freemium import register_freemium
from freshsky_common import freemium
from freshsky_common.revenue import install_visuals, og_snippet
from freshsky_common.checkout_store import (
    CheckoutStoreUnavailable,
    MemoryCheckoutStore,
)


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
        return SimpleNamespace(
            id="cs_test_monthly_123",
            url="https://checkout.stripe.test/monthly",
        )

    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        Subscription=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
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

    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_email"] = "person@example.com"
        user_session["user_email_verified"] = True
    response = client.get("/subscribe")

    assert response.status_code == 303
    assert response.location == "https://checkout.stripe.test/monthly"
    assert created["mode"] == "subscription"
    assert created["line_items"] == [
        {"price": "price_focus_monthly", "quantity": 1}
    ]
    assert created["allow_promotion_codes"] is True
    assert created["metadata"]["app_host"] == "foia.example"
    assert created["metadata"]["tier"] == "focus"
    assert len(created["metadata"]["checkout_fingerprint"]) == 64
    assert created["customer_email"] == "person@example.com"
    assert created["idempotency_key"].startswith(
        "freshsky-subscription-v2-"
    )
    assert "person@example.com" not in created["idempotency_key"]
    assert created["client_reference_id"] != "person@example.com"
    assert len(created["client_reference_id"]) == 64
    assert created["expires_at"] > 0


def test_existing_freshsky_subscription_prevents_new_checkout(monkeypatch):
    created = []
    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(
                data=[SimpleNamespace(id="cus_existing")]
            ),
        ),
        Subscription=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(
                data=[
                    {
                        "status": "active",
                        "items": {
                            "data": [
                                {
                                    "price": {
                                        "id": "price_focus_monthly",
                                        "product": {
                                            "name": "FreshSky Focus",
                                            "metadata": {},
                                        },
                                    }
                                }
                            ]
                        },
                    }
                ]
            ),
        ),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(
                create=lambda **kwargs: created.append(kwargs),
            )
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
    )
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_email"] = "person@example.com"
        user_session["user_email_verified"] = True

    response = client.get("/subscribe", follow_redirects=False)

    assert response.status_code == 302
    assert response.location == "/billing"
    assert created == []


def test_other_freshsky_tier_product_also_blocks_duplicate(monkeypatch):
    created = []
    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(
                data=[SimpleNamespace(id="cus_existing")]
            ),
        ),
        Subscription=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(
                data=[
                    {
                        "status": "active",
                        "items": {
                            "data": [
                                {
                                    "price": {
                                        "id": "price_civic_elsewhere",
                                        "product": {
                                            "name": "FreshSky Civic",
                                            "metadata": {
                                                "freshsky_tier": "civic"
                                            },
                                        },
                                    }
                                }
                            ]
                        },
                    }
                ]
            ),
        ),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(
                create=lambda **kwargs: created.append(kwargs),
            )
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
    )
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_email"] = "person@example.com"
        user_session["user_email_verified"] = True

    response = client.get("/subscribe", follow_redirects=False)

    assert response.status_code == 302
    assert response.location == "/billing"
    assert created == []


@pytest.mark.parametrize(
    "status",
    ("trialing", "past_due", "unpaid", "incomplete", "paused"),
)
def test_nonterminal_freshsky_subscription_statuses_block_duplicates(
    monkeypatch,
    status,
):
    created = []
    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(
                data=[SimpleNamespace(id="cus_existing")]
            ),
        ),
        Subscription=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(
                data=[
                    {
                        "status": status,
                        "items": {
                            "data": [
                                {
                                    "price": {
                                        "id": "price_focus_monthly",
                                    }
                                }
                            ]
                        },
                    }
                ]
            ),
        ),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(
                create=lambda **kwargs: created.append(kwargs),
            )
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
    )
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_email"] = "person@example.com"
        user_session["user_email_verified"] = True

    response = client.get("/subscribe", follow_redirects=False)

    assert response.status_code == 302
    assert response.location == "/billing"
    assert created == []


def test_subscription_precheck_failure_blocks_checkout(monkeypatch):
    created = []

    def unavailable(**_kwargs):
        raise RuntimeError("upstream customer payload")

    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(list=unavailable),
        Subscription=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(
                create=lambda **kwargs: created.append(kwargs),
            )
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
    )
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_email"] = "person@example.com"
        user_session["user_email_verified"] = True

    response = client.get("/subscribe", follow_redirects=False)

    assert response.status_code == 302
    assert response.location == (
        "https://foia.example/?checkout=unavailable"
    )
    assert created == []


def test_pending_checkout_retry_reuses_session_without_storing_url(monkeypatch):
    created = []
    sessions = {}

    def create_checkout(**kwargs):
        created.append(dict(kwargs))
        checkout = SimpleNamespace(
            id="cs_test_reusable_123",
            url="https://checkout.stripe.test/reusable",
            status="open",
            client_reference_id=kwargs["client_reference_id"],
            metadata=dict(kwargs["metadata"]),
        )
        sessions[checkout.id] = checkout
        return checkout

    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        Subscription=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(
                create=create_checkout,
                retrieve=lambda checkout_id: sessions[checkout_id],
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    store = MemoryCheckoutStore()
    app = make_app(
        stripe_secret_key="sk_test_subscription",
        primary_url="https://foia.example",
        subscriptions_enabled=True,
        subscription_tier="focus",
        subscription_price_id="price_focus_monthly",
        subscription_amount_cents=999,
        pending_checkout_store=store,
    )
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_email"] = "person@example.com"
        user_session["user_email_verified"] = True

    first = client.get("/subscribe", follow_redirects=False)
    retry = client.get("/subscribe", follow_redirects=False)

    assert first.status_code == retry.status_code == 303
    assert first.location == retry.location == (
        "https://checkout.stripe.test/reusable"
    )
    assert len(created) == 1
    assert created[0]["idempotency_key"].startswith(
        "freshsky-subscription-v2-"
    )
    assert "person@example.com" not in created[0]["idempotency_key"]


def test_retry_after_session_binding_failure_uses_same_stripe_key(
    monkeypatch,
):
    class FlakyAttachStore:
        def __init__(self):
            self.backend = MemoryCheckoutStore()
            self.failures = 1

        def reserve(self, *args, **kwargs):
            return self.backend.reserve(*args, **kwargs)

        def attach_session(self, reservation, checkout_session_id):
            if self.failures:
                self.failures -= 1
                raise CheckoutStoreUnavailable("transient Firestore outage")
            return self.backend.attach_session(
                reservation,
                checkout_session_id,
            )

    calls = []
    sessions_by_key = {}

    def create_checkout(**kwargs):
        calls.append(dict(kwargs))
        checkout = sessions_by_key.get(kwargs["idempotency_key"])
        if checkout is None:
            checkout = SimpleNamespace(
                id="cs_test_recovered_123",
                url="https://checkout.stripe.test/recovered",
            )
            sessions_by_key[kwargs["idempotency_key"]] = checkout
        return checkout

    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        Subscription=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(create=create_checkout),
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
        pending_checkout_store=FlakyAttachStore(),
    )
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_email"] = "person@example.com"
        user_session["user_email_verified"] = True

    failed = client.get("/subscribe", follow_redirects=False)
    recovered = client.get("/subscribe", follow_redirects=False)

    assert failed.status_code == 302
    assert failed.location == "https://foia.example/?checkout=unavailable"
    assert recovered.status_code == 303
    assert recovered.location == "https://checkout.stripe.test/recovered"
    assert len(calls) == 2
    assert len({call["idempotency_key"] for call in calls}) == 1
    assert len(sessions_by_key) == 1


def test_concurrent_checkout_retries_share_one_reservation_and_session(
    monkeypatch,
):
    calls = []
    sessions_by_key = {}
    lock = threading.Lock()

    def create_checkout(**kwargs):
        with lock:
            calls.append(dict(kwargs))
            checkout = sessions_by_key.get(kwargs["idempotency_key"])
            if checkout is None:
                checkout = SimpleNamespace(
                    id="cs_test_concurrent_123",
                    url="https://checkout.stripe.test/concurrent",
                    status="open",
                    client_reference_id=kwargs["client_reference_id"],
                    metadata=dict(kwargs["metadata"]),
                )
                sessions_by_key[kwargs["idempotency_key"]] = checkout
            return checkout

    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        Subscription=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(
                create=create_checkout,
                retrieve=lambda _checkout_id: next(
                    iter(sessions_by_key.values())
                ),
            )
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
        pending_checkout_store=MemoryCheckoutStore(),
    )
    clients = [app.test_client() for _ in range(8)]
    for client in clients:
        with client.session_transaction() as user_session:
            user_session["user_email"] = "person@example.com"
            user_session["user_email_verified"] = True

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(
            executor.map(
                lambda client: client.get(
                    "/subscribe",
                    follow_redirects=False,
                ),
                clients,
            )
        )

    assert {response.status_code for response in responses} == {303}
    assert {response.location for response in responses} == {
        "https://checkout.stripe.test/concurrent"
    }
    assert len({call["idempotency_key"] for call in calls}) == 1
    assert len(sessions_by_key) == 1
    assert all(
        "person@example.com" not in call["idempotency_key"]
        for call in calls
    )


def test_one_identity_cannot_open_different_app_checkout(monkeypatch):
    created = []
    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        Subscription=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(
                create=lambda **kwargs: (
                    created.append(dict(kwargs))
                    or SimpleNamespace(
                        id="cs_test_first_app_123",
                        url="https://checkout.stripe.test/first",
                    )
                ),
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    store = MemoryCheckoutStore()
    first_app = make_app(
        stripe_secret_key="sk_test_subscription",
        primary_url="https://funding.example",
        subscriptions_enabled=True,
        subscription_tier="focus",
        subscription_price_id="price_focus_monthly",
        subscription_amount_cents=999,
        workspace_id="funding",
        pending_checkout_store=store,
    )
    other_app = make_app(
        stripe_secret_key="sk_test_subscription",
        primary_url="https://education.example",
        subscriptions_enabled=True,
        subscription_tier="focus",
        subscription_price_id="price_focus_monthly",
        subscription_amount_cents=999,
        workspace_id="education",
        pending_checkout_store=store,
    )
    first = first_app.test_client()
    other = other_app.test_client()
    for client in (first, other):
        with client.session_transaction() as user_session:
            user_session["user_email"] = "person@example.com"
            user_session["user_email_verified"] = True

    first_response = first.get("/subscribe", follow_redirects=False)
    other_response = other.get("/subscribe", follow_redirects=False)

    assert first_response.status_code == 303
    assert other_response.status_code == 302
    assert other_response.location == (
        "https://education.example/?checkout=pending"
    )
    assert len(created) == 1


def test_managed_checkout_fails_closed_when_firestore_is_unavailable(
    monkeypatch,
):
    created = []
    monkeypatch.setenv("K_SERVICE", "foia")
    monkeypatch.setenv("FRESHSKY_USAGE_HMAC_KEY", "dedicated-signing-key")
    monkeypatch.setattr(
        freemium.FirestoreCheckoutStore,
        "from_environment",
        classmethod(
            lambda _cls: (_ for _ in ()).throw(
                CheckoutStoreUnavailable("Firestore unavailable")
            )
        ),
    )
    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        Subscription=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(
                create=lambda **kwargs: created.append(kwargs),
            )
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
    )
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_email"] = "person@example.com"
        user_session["user_email_verified"] = True

    response = client.get("/subscribe", follow_redirects=False)

    assert response.status_code == 302
    assert response.location == (
        "https://foia.example/?checkout=unavailable"
    )
    assert created == []


def test_managed_checkout_fails_closed_without_pseudonym_key(monkeypatch):
    created = []
    monkeypatch.setenv("K_SERVICE", "foia")
    monkeypatch.delenv("FRESHSKY_USAGE_HMAC_KEY", raising=False)
    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        Subscription=SimpleNamespace(
            list=lambda **_kwargs: SimpleNamespace(data=[]),
        ),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(
                create=lambda **kwargs: created.append(kwargs),
            )
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
    )
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_email"] = "person@example.com"
        user_session["user_email_verified"] = True

    response = client.get("/subscribe", follow_redirects=False)

    assert response.status_code == 302
    assert response.location == (
        "https://foia.example/?checkout=unavailable"
    )
    assert created == []


@pytest.mark.parametrize(
    "store",
    (
        MemoryCheckoutStore(),
        SimpleNamespace(
            reserve=lambda *_args, **_kwargs: None,
            attach_session=lambda *_args, **_kwargs: None,
        ),
    ),
)
def test_managed_runtime_rejects_non_firestore_checkout_store(
    monkeypatch,
    store,
):
    monkeypatch.setenv("K_SERVICE", "foia")

    with pytest.raises(
        ValueError,
        match="managed subscription checkout requires FirestoreCheckoutStore",
    ):
        make_app(
            stripe_secret_key="sk_test_subscription",
            primary_url="https://foia.example",
            subscriptions_enabled=True,
            subscription_tier="focus",
            subscription_price_id="price_focus_monthly",
            subscription_amount_cents=999,
            pending_checkout_store=store,
        )


def test_verified_checkout_preserves_matching_oauth_session(monkeypatch):
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
    with client.session_transaction() as user_session:
        user_session["user_email"] = "person@example.com"
        user_session["user_email_verified"] = True

    response = client.get("/subscription/success?session_id=cs_test_123")

    assert response.status_code == 303
    assert response.location == "https://foia.example/?checkout=success"
    with client.session_transaction() as user_session:
        assert user_session["user_email"] == "person@example.com"
        assert user_session["user_email_verified"] is True
        assert user_session["subscription_tier"] == "focus"


def test_checkout_success_link_cannot_create_or_replace_identity(monkeypatch):
    checkout = SimpleNamespace(
        status="complete",
        mode="subscription",
        subscription="sub_123",
        metadata={"app_host": "foia.example", "tier": "focus"},
        customer_details=SimpleNamespace(email="buyer@example.com"),
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
        google_client_id="google-client",
        google_client_secret="google-secret",
        primary_url="https://foia.example",
        subscriptions_enabled=True,
        subscription_tier="focus",
        subscription_price_id="price_focus_monthly",
        subscription_amount_cents=999,
        free_request_limit=3,
    )
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_email"] = "other@example.com"
        user_session["user_email_verified"] = True

    response = client.get(
        "/subscription/success?session_id=cs_test_shared",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.location.startswith("/auth/google?")
    with client.session_transaction() as user_session:
        assert user_session["user_email"] == "other@example.com"
        assert user_session["user_email_verified"] is True
        assert "subscription_tier" not in user_session


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
    assert "link.addEventListener('load'" in bundle_text
    assert bundle.headers["Cache-Control"] == "public, max-age=31536000, immutable"

    compatibility = client.get("/freemium.js")
    assert compatibility.status_code == 200
    assert compatibility.headers["Cache-Control"] == "no-store, max-age=0"


def test_versioned_access_bundle_replaces_any_stable_query_string():
    app = make_app()
    app.view_functions["index"] = lambda: (
        '<html><body><script src="/freemium.js?v=20260726"></script></body></html>'
    )

    body = app.test_client().get("/").get_data(as_text=True)

    assert 'src="/freshsky-access-v061.js"' in body
    assert "/freemium.js?" not in body


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


def test_guest_previews_use_a_true_rolling_thirty_day_window(monkeypatch):
    current = [1_800_000_000.0]
    monkeypatch.setattr(freemium.time, "time", lambda: current[0])
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
    assert client.post("/api/work").status_code == 200
    current[0] += 29 * 24 * 60 * 60
    assert client.post("/api/work").status_code == 200
    current[0] += 12 * 60 * 60
    assert client.post("/api/work").status_code == 200
    assert client.post("/api/work").status_code == 402

    current[0] += (12 * 60 * 60) + 1
    assert client.post("/api/work").status_code == 200
    assert client.post("/api/work").status_code == 402


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
        user_session["user_email_verified"] = True

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
        user_session["user_email_verified"] = True
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
        state["user_email_verified"] = True
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
        state["user_email_verified"] = True

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
        state["user_email_verified"] = True

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
        session["user_email_verified"] = True
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
        session["user_email_verified"] = True

    status = client.get("/api/user-status").get_json()
    response = client.get("/billing")

    assert status["stripe_enabled"] is True
    assert response.status_code == 302
    assert response.location == "https://billing.stripe.test/session"
    assert created == {
        "customer": "cus_customer",
        "return_url": "https://www.freshskyai.com",
    }


def test_billing_rejects_unverified_checkout_identity(monkeypatch):
    customer_calls = []
    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda **kwargs: customer_calls.append(kwargs)
        ),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    app = make_app(
        stripe_secret_key="sk_test_billing",
        google_client_id="google-client",
        google_client_secret="google-secret",
        primary_url="https://www.freshskyai.com",
    )
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_email"] = "legacy@example.com"
        state["user_email_verified"] = False

    response = client.get("/billing", follow_redirects=False)

    assert response.status_code == 302
    assert response.location.startswith("/auth/google?")
    assert customer_calls == []


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
    client = make_app().test_client()
    response = client.get("/freshsky.css?v=0.6.7")

    assert response.status_code == 200
    assert response.mimetype == "text/css"
    stylesheet = response.get_data(as_text=True)
    assert "Fresh Sky 2026 shared visual system" in stylesheet
    assert "main {" in stylesheet
    assert "box-sizing: border-box;" in stylesheet
    assert "margin-inline: auto;" in stylesheet
    assert response.headers["Cache-Control"] == "public, max-age=3600"
    assert 'href="/freshsky.css?v=0.6.7"' in og_snippet(
        "Example", "https://example.com/"
    )

    interface = client.get("/freshsky-interface.js?v=0.6.7")
    assert interface.status_code == 200
    assert interface.mimetype == "text/javascript"
    assert "Skip to main content" in interface.get_data(as_text=True)
    assert interface.headers["Cache-Control"] == "public, max-age=3600"


def test_contrast_guard_is_injected_after_product_styles():
    app = Flask(__name__)

    @app.route("/")
    def product_page():
        return (
            '<html><head><style id="product-styles">'
            '.step-card p{color:#64748b!important}'
            '</style></head><body><main><div class="step-card">'
            "<p>Readable instructions</p></div></main></body></html>"
        )

    install_visuals(app)
    body = app.test_client().get("/").get_data(as_text=True)

    assert 'id="fs-contrast-guard"' in body
    assert body.index('id="product-styles"') < body.index('id="fs-contrast-guard"')
    assert ".step-card p" in body
    assert "color:#cbd5e1!important" in body


def test_contrast_guard_palette_meets_normal_text_threshold():
    def luminance(hex_color):
        channels = [
            int(hex_color[index:index + 2], 16) / 255
            for index in (1, 3, 5)
        ]
        linear = [
            channel / 12.92
            if channel <= 0.03928
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return sum(
            weight * channel
            for weight, channel in zip((0.2126, 0.7152, 0.0722), linear)
        )

    def contrast(foreground, background):
        lighter, darker = sorted(
            (luminance(foreground), luminance(background)),
            reverse=True,
        )
        return (lighter + 0.05) / (darker + 0.05)

    assert contrast("#cbd5e1", "#111a35") >= 4.5
    assert contrast("#f4f7ff", "#080d22") >= 4.5
    assert contrast("#06101f", "#67e8f9") >= 4.5
    assert contrast("#06101f", "#a5b4fc") >= 4.5


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
