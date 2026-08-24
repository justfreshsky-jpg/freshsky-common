"""Instance-bound Stripe client surface used by shared FreshSky billing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable


API_VERSION = "2026-07-29.dahlia"


def _options(params: dict[str, Any]) -> dict[str, Any] | None:
    idempotency_key = params.pop("idempotency_key", None)
    return (
        {"idempotency_key": idempotency_key}
        if idempotency_key
        else None
    )

class _Resource:
    def __init__(self, service: Any) -> None:
        self._service = service

    def list(self, **params: Any) -> Any:
        return self._service.list(params or None, _options(params))

    def retrieve(self, resource_id: str, **params: Any) -> Any:
        return self._service.retrieve(
            resource_id,
            params or None,
            _options(params),
        )

    def create(self, **params: Any) -> Any:
        return self._service.create(params or None, _options(params))


def _legacy_resources_are_native(stripe_module: Any) -> bool:
    """Distinguish the real SDK from provider-free monkeypatched resources."""

    customer = getattr(stripe_module, "Customer", None)
    module_name = str(getattr(customer, "__module__", ""))
    return module_name.startswith("stripe.")


def build_stripe_api(stripe_module: Any, secret: str) -> Any:
    """Return the shared legacy-shaped API backed by ``StripeClient``."""

    if not secret:
        raise ValueError("Stripe credential is unavailable")
    factory: Callable[..., Any] | None = getattr(
        stripe_module,
        "StripeClient",
        None,
    )
    if not callable(factory) or not _legacy_resources_are_native(stripe_module):
        # Compatibility is limited to provider-free fakes. The package pins a
        # Stripe version with StripeClient for every installed runtime.
        setattr(stripe_module, "api_key", secret)
        return stripe_module

    client = factory(
        secret,
        stripe_version=API_VERSION,
        max_network_retries=2,
    )
    v1 = client.v1
    return SimpleNamespace(
        Customer=_Resource(v1.customers),
        Subscription=_Resource(v1.subscriptions),
        Product=_Resource(v1.products),
        checkout=SimpleNamespace(Session=_Resource(v1.checkout.sessions)),
        billing_portal=SimpleNamespace(
            Session=_Resource(v1.billing_portal.sessions),
        ),
        Webhook=stripe_module.Webhook,
        client=client,
    )
