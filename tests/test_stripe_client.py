from __future__ import annotations

from types import SimpleNamespace

import stripe

from freshsky_common.stripe_client import API_VERSION, build_stripe_api


class RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def list(self, *args: object):
        self.calls.append(("list", args))
        return args

    def retrieve(self, *args: object):
        self.calls.append(("retrieve", args))
        return args

    def create(self, *args: object):
        self.calls.append(("create", args))
        return args


class RecordingClient:
    latest: "RecordingClient | None" = None

    def __init__(self, secret: str, **options: object) -> None:
        self.secret = secret
        self.options = options
        self.customers = RecordingService()
        self.subscriptions = RecordingService()
        self.products = RecordingService()
        self.checkout_sessions = RecordingService()
        self.portal_sessions = RecordingService()
        self.v1 = SimpleNamespace(
            customers=self.customers,
            subscriptions=self.subscriptions,
            products=self.products,
            checkout=SimpleNamespace(sessions=self.checkout_sessions),
            billing_portal=SimpleNamespace(sessions=self.portal_sessions),
        )
        RecordingClient.latest = self


def _native_module() -> SimpleNamespace:
    native_customer = type("Customer", (), {})
    native_customer.__module__ = "stripe._customer"
    return SimpleNamespace(
        StripeClient=RecordingClient,
        Customer=native_customer,
        Webhook=object(),
        api_key="unchanged",
    )


def test_real_sdk_shape_uses_instance_bound_version_pinned_client() -> None:
    module = _native_module()
    api = build_stripe_api(module, "rk_live_redacted")

    client = RecordingClient.latest
    assert client is not None
    assert client.secret == "rk_live_redacted"
    assert client.options == {
        "stripe_version": API_VERSION,
        "max_network_retries": 2,
    }
    assert module.api_key == "unchanged"
    assert api.Webhook is module.Webhook


def test_idempotency_key_is_a_request_option() -> None:
    api = build_stripe_api(_native_module(), "rk_live_redacted")
    api.checkout.Session.create(
        mode="subscription",
        idempotency_key="checkout-123",
    )

    client = RecordingClient.latest
    assert client is not None
    assert client.checkout_sessions.calls == [
        (
            "create",
            (
                {"mode": "subscription"},
                {"idempotency_key": "checkout-123"},
            ),
        )
    ]


def test_provider_free_fake_keeps_legacy_injection_surface() -> None:
    fake = SimpleNamespace(api_key=None, Customer=SimpleNamespace())
    assert build_stripe_api(fake, "sk_test_redacted") is fake
    assert fake.api_key == "sk_test_redacted"


def test_pinned_stripe_dependency_exposes_required_client_resources() -> None:
    previous = stripe.api_key
    try:
        stripe.api_key = None
        api = build_stripe_api(stripe, "rk_test_redacted")
        assert stripe.api_key is None
        assert api.client is not None
        assert api.Customer is not None
        assert api.Subscription is not None
        assert api.Product is not None
        assert api.checkout.Session is not None
        assert api.billing_portal.Session is not None
    finally:
        stripe.api_key = previous
