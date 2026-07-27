from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from freshsky_common.checkout_store import (
    COLLECTION,
    CheckoutStoreConflict,
    CheckoutStoreCorrupt,
    CheckoutStoreUnavailable,
    FirestoreCheckoutStore,
    MemoryCheckoutStore,
    PENDING_LIFETIME,
    PendingCheckout,
    checkout_fingerprint,
)


NOW = datetime(2026, 7, 26, 18, tzinfo=timezone.utc)
SUBJECT = "a" * 64
FINGERPRINT = "b" * 64


class FakeSnapshot:
    def __init__(self, value):
        self._value = copy.deepcopy(value)
        self.exists = value is not None

    def to_dict(self):
        return copy.deepcopy(self._value)


class FakeDocument:
    def __init__(self, client, key):
        self._client = client
        self.key = key

    def get(self, transaction=None):
        del transaction
        return FakeSnapshot(self._client.documents.get(self.key))


class FakeCollection:
    def __init__(self, client, name):
        self._client = client
        self._name = name

    def document(self, document_id):
        return FakeDocument(self._client, (self._name, document_id))


class FakeTransaction:
    def __init__(self, client):
        self._client = client
        self._writes = []

    def set(self, reference, value):
        self._writes.append((reference.key, copy.deepcopy(value)))

    def commit(self):
        for key, value in self._writes:
            self._client.documents[key] = copy.deepcopy(value)


class FakeFirestoreClient:
    def __init__(self):
        self.documents = {}
        self.lock = threading.RLock()

    def collection(self, name):
        return FakeCollection(self, name)

    def transaction(self):
        return FakeTransaction(self)

    def run_transaction(self, transaction, operation):
        with self.lock:
            result = operation(transaction)
            transaction.commit()
            return result


def firestore_store(client):
    return FirestoreCheckoutStore(
        client,
        transaction_runner=client.run_transaction,
    )


def test_fingerprint_binds_exact_app_tier_workspace_and_price():
    baseline = checkout_fingerprint(
        app_host="FOIA.FreshSkyAI.com",
        tier="FOCUS",
        workspace_id="action_packs",
        price_id="price_focus_monthly",
    )
    retry = checkout_fingerprint(
        app_host="foia.freshskyai.com",
        tier="focus",
        workspace_id="action_packs",
        price_id="price_focus_monthly",
    )

    assert baseline == retry
    assert len(baseline) == 64
    for changed in (
        {
            "app_host": "forms.freshskyai.com",
            "tier": "focus",
            "workspace_id": "action_packs",
            "price_id": "price_focus_monthly",
        },
        {
            "app_host": "foia.freshskyai.com",
            "tier": "plus",
            "workspace_id": "action_packs",
            "price_id": "price_focus_monthly",
        },
        {
            "app_host": "foia.freshskyai.com",
            "tier": "focus",
            "workspace_id": "education",
            "price_id": "price_focus_monthly",
        },
        {
            "app_host": "foia.freshskyai.com",
            "tier": "focus",
            "workspace_id": "action_packs",
            "price_id": "price_other_monthly",
        },
    ):
        assert checkout_fingerprint(**changed) != baseline


@pytest.mark.parametrize(
    "overrides",
    (
        {"app_host": ""},
        {"tier": ""},
        {"price_id": "not-a-price"},
        {"workspace_id": "bad\0workspace"},
    ),
)
def test_fingerprint_rejects_noncanonical_inputs(overrides):
    values = {
        "app_host": "foia.freshskyai.com",
        "tier": "focus",
        "workspace_id": "action_packs",
        "price_id": "price_focus_monthly",
    }
    values.update(overrides)

    with pytest.raises(CheckoutStoreCorrupt):
        checkout_fingerprint(**values)


def test_memory_reservation_is_concurrent_and_identity_bound_for_23_hours():
    store = MemoryCheckoutStore()

    with ThreadPoolExecutor(max_workers=8) as executor:
        reservations = list(
            executor.map(
                lambda _: store.reserve(
                    SUBJECT,
                    FINGERPRINT,
                    now=NOW,
                ),
                range(32),
            )
        )

    assert {item.reservation_id for item in reservations} == {
        reservations[0].reservation_id
    }
    assert reservations[0].expires_at - reservations[0].created_at == (
        PENDING_LIFETIME
    )
    replay = store.reserve(
        SUBJECT,
        FINGERPRINT,
        now=NOW + timedelta(hours=22, minutes=59),
    )
    assert replay.reservation_id == reservations[0].reservation_id
    with pytest.raises(CheckoutStoreConflict):
        store.reserve(SUBJECT, "c" * 64, now=NOW)


def test_session_binding_is_idempotent_and_cannot_be_replaced():
    store = MemoryCheckoutStore()
    reservation = store.reserve(SUBJECT, FINGERPRINT, now=NOW)

    first = store.attach_session(reservation, "cs_test_pending_123")
    replay = store.attach_session(reservation, "cs_test_pending_123")

    assert replay == first
    with pytest.raises(CheckoutStoreConflict):
        store.attach_session(reservation, "cs_test_other_456")


def test_expired_reservation_can_be_replaced_safely():
    store = MemoryCheckoutStore()
    original = store.reserve(SUBJECT, FINGERPRINT, now=NOW)
    replacement = store.reserve(
        SUBJECT,
        "c" * 64,
        now=NOW + PENDING_LIFETIME + timedelta(microseconds=1),
    )

    assert replacement.reservation_id != original.reservation_id
    assert replacement.fingerprint == "c" * 64
    with pytest.raises(CheckoutStoreConflict):
        store.attach_session(original, "cs_test_stale_123")


def test_pending_document_contains_no_email_url_or_secret_material():
    reservation = MemoryCheckoutStore().reserve(
        SUBJECT,
        FINGERPRINT,
        now=NOW,
    )
    document = reservation.to_document()
    serialized = repr(document).lower()

    assert set(document) == {
        "schema_version",
        "subject_id",
        "fingerprint",
        "reservation_id",
        "checkout_session_id",
        "created_at",
        "expires_at",
    }
    assert "@" not in serialized
    assert "email" not in serialized
    assert "url" not in serialized
    assert "secret" not in serialized
    assert "stripe" not in serialized


def test_pending_checkout_rejects_invalid_or_overlong_records():
    valid = {
        "subject_id": SUBJECT,
        "fingerprint": FINGERPRINT,
        "reservation_id": "d" * 32,
        "created_at": NOW,
        "expires_at": NOW + PENDING_LIFETIME,
    }
    for update in (
        {"subject_id": "person@example.com"},
        {"fingerprint": "not-a-hash"},
        {"reservation_id": "short"},
        {"created_at": NOW.replace(tzinfo=None)},
        {"expires_at": NOW + timedelta(hours=23, seconds=1)},
        {"checkout_session_id": "https://checkout.stripe.test/secret"},
    ):
        with pytest.raises(CheckoutStoreCorrupt):
            PendingCheckout(**{**valid, **update})


def test_document_schema_and_store_inputs_are_strict():
    with pytest.raises(CheckoutStoreCorrupt):
        PendingCheckout.from_document("not-a-document")
    with pytest.raises(CheckoutStoreCorrupt):
        PendingCheckout.from_document({"schema_version": 99})

    store = MemoryCheckoutStore()
    with pytest.raises(CheckoutStoreCorrupt):
        store.reserve("person@example.com", FINGERPRINT, now=NOW)
    with pytest.raises(CheckoutStoreCorrupt):
        store.reserve(SUBJECT, "short", now=NOW)
    reservation = store.reserve(SUBJECT, FINGERPRINT, now=NOW)
    with pytest.raises(CheckoutStoreCorrupt):
        store.attach_session(reservation, "not-a-session")
    with pytest.raises(CheckoutStoreCorrupt):
        store.attach_session("not-a-reservation", "cs_test_pending_123")


def test_firestore_instances_share_one_pending_reservation_without_email():
    client = FakeFirestoreClient()
    first = firestore_store(client)
    second = firestore_store(client)

    original = first.reserve(SUBJECT, FINGERPRINT, now=NOW)
    replay = second.reserve(
        SUBJECT,
        FINGERPRINT,
        now=NOW + timedelta(hours=22),
    )
    attached = second.attach_session(replay, "cs_live_pending_123")

    assert replay.reservation_id == original.reservation_id
    assert attached.checkout_session_id == "cs_live_pending_123"
    document = client.documents[(COLLECTION, SUBJECT)]
    assert "email" not in document
    assert "url" not in document
    assert "@" not in str(document)


def test_firestore_store_validates_constructor_and_conflicts():
    with pytest.raises(CheckoutStoreUnavailable):
        FirestoreCheckoutStore(None)

    client = FakeFirestoreClient()
    first = firestore_store(client)
    second = firestore_store(client)
    reservation = first.reserve(SUBJECT, FINGERPRINT, now=NOW)
    with pytest.raises(CheckoutStoreConflict):
        second.reserve(SUBJECT, "c" * 64, now=NOW)
    second.attach_session(reservation, "cs_test_pending_123")
    with pytest.raises(CheckoutStoreConflict):
        first.attach_session(reservation, "cs_test_other_456")


def test_firestore_from_environment_accepts_explicit_client():
    client = FakeFirestoreClient()
    runner = client.run_transaction

    store = FirestoreCheckoutStore.from_environment(
        client=client,
        transaction_runner=runner,
        environ={"GOOGLE_CLOUD_PROJECT": "safe-test-project"},
    )

    reservation = store.reserve(SUBJECT, FINGERPRINT, now=NOW)
    assert reservation.subject_id == SUBJECT


def test_firestore_transaction_failure_is_unavailable_not_memory_fallback():
    client = FakeFirestoreClient()

    def broken_runner(_transaction, _operation):
        raise RuntimeError("credential payload that must not escape")

    store = FirestoreCheckoutStore(
        client,
        transaction_runner=broken_runner,
    )

    with pytest.raises(
        CheckoutStoreUnavailable,
        match="pending-checkout reservation is unavailable",
    ) as captured:
        store.reserve(SUBJECT, FINGERPRINT, now=NOW)
    assert "credential payload" not in str(captured.value)
    assert client.documents == {}


def test_firestore_client_setup_failure_is_wrapped_without_payload():
    class BrokenClient:
        def collection(self, _name):
            raise RuntimeError("sensitive client details")

    store = FirestoreCheckoutStore(BrokenClient())

    with pytest.raises(
        CheckoutStoreUnavailable,
        match="pending-checkout reservation is unavailable",
    ) as captured:
        store.reserve(SUBJECT, FINGERPRINT, now=NOW)
    assert "sensitive client details" not in str(captured.value)


def test_firestore_corrupt_record_fails_closed_without_overwrite():
    client = FakeFirestoreClient()
    client.documents[(COLLECTION, SUBJECT)] = {
        "schema_version": 1,
        "subject_id": SUBJECT,
        "fingerprint": FINGERPRINT,
        "reservation_id": "invalid",
        "created_at": NOW,
        "expires_at": NOW + PENDING_LIFETIME,
    }
    original = copy.deepcopy(client.documents)

    with pytest.raises(CheckoutStoreCorrupt):
        firestore_store(client).reserve(SUBJECT, FINGERPRINT, now=NOW)
    assert client.documents == original
