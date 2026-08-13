"""Durable, privacy-limited reservations for Stripe subscription Checkout.

The store closes the race between checking for an existing subscription and
creating a Stripe Checkout Session.  A verified identity can hold only one
pending FreshSky subscription Checkout across the portfolio.  Managed
runtimes use a Firestore transaction; local development and tests use an
isolated, thread-safe in-memory backend.

Only pseudonymous identifiers, hashes, timestamps, and the opaque Stripe
Checkout Session ID are persisted.  Raw email addresses, Checkout URLs, and
Stripe credentials are never stored.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol


COLLECTION = "freshsky_pending_checkouts_v1"
SCHEMA_VERSION = 1
PENDING_LIFETIME = timedelta(hours=23)
_PSEUDONYM_RE = re.compile(r"^[a-f0-9]{64}$")
_FINGERPRINT_RE = re.compile(r"^[a-f0-9]{64}$")
_RESERVATION_RE = re.compile(r"^[a-f0-9]{32}$")
_CHECKOUT_SESSION_RE = re.compile(r"^cs_[A-Za-z0-9_]{8,}$")
_PRICE_RE = re.compile(r"^price_[A-Za-z0-9_]{4,}$")


class CheckoutStoreError(RuntimeError):
    """Base error for pending-checkout persistence."""


class CheckoutStoreUnavailable(CheckoutStoreError):
    """The durable pending-checkout backend is unavailable."""


class CheckoutStoreConflict(CheckoutStoreError):
    """A different checkout is already pending for this identity."""


class CheckoutStoreCorrupt(CheckoutStoreError):
    """A stored checkout reservation failed strict validation."""


@dataclass(frozen=True)
class PendingCheckout:
    """One pseudonymous, identity-bound pending Checkout reservation."""

    subject_id: str
    fingerprint: str
    reservation_id: str
    created_at: datetime
    expires_at: datetime
    checkout_session_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.subject_id, str)
            or not _PSEUDONYM_RE.fullmatch(self.subject_id)
        ):
            raise CheckoutStoreCorrupt("pending checkout subject is invalid")
        if (
            not isinstance(self.fingerprint, str)
            or not _FINGERPRINT_RE.fullmatch(self.fingerprint)
        ):
            raise CheckoutStoreCorrupt("pending checkout fingerprint is invalid")
        if (
            not isinstance(self.reservation_id, str)
            or not _RESERVATION_RE.fullmatch(self.reservation_id)
        ):
            raise CheckoutStoreCorrupt("pending checkout reservation is invalid")
        created = _utc(self.created_at, "created_at")
        expires = _utc(self.expires_at, "expires_at")
        if expires <= created or expires - created > PENDING_LIFETIME:
            raise CheckoutStoreCorrupt("pending checkout lifetime is invalid")
        if (
            self.checkout_session_id is not None
            and (
                not isinstance(self.checkout_session_id, str)
                or not _CHECKOUT_SESSION_RE.fullmatch(
                    self.checkout_session_id
                )
            )
        ):
            raise CheckoutStoreCorrupt("pending checkout session is invalid")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "expires_at", expires)

    def to_document(self) -> dict[str, Any]:
        """Return the complete privacy-limited Firestore representation."""
        return {
            "schema_version": SCHEMA_VERSION,
            "subject_id": self.subject_id,
            "fingerprint": self.fingerprint,
            "reservation_id": self.reservation_id,
            "checkout_session_id": self.checkout_session_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> "PendingCheckout":
        if not isinstance(value, Mapping):
            raise CheckoutStoreCorrupt("pending checkout record is invalid")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise CheckoutStoreCorrupt("pending checkout schema is invalid")
        return cls(
            subject_id=str(value.get("subject_id") or ""),
            fingerprint=str(value.get("fingerprint") or ""),
            reservation_id=str(value.get("reservation_id") or ""),
            checkout_session_id=(
                str(value.get("checkout_session_id"))
                if value.get("checkout_session_id")
                else None
            ),
            created_at=value.get("created_at"),
            expires_at=value.get("expires_at"),
        )


class CheckoutStore(Protocol):
    """Persistence contract used by the shared checkout route."""

    def reserve(
        self,
        subject_id: str,
        fingerprint: str,
        *,
        now: datetime | None = None,
    ) -> PendingCheckout:
        """Atomically create or reuse one pending checkout."""

    def attach_session(
        self,
        reservation: PendingCheckout,
        checkout_session_id: str,
    ) -> PendingCheckout:
        """Idempotently bind the reservation to its Stripe Session."""


def checkout_fingerprint(
    *,
    app_host: str,
    tier: str,
    workspace_id: str,
    price_id: str,
) -> str:
    """Hash the exact app, tier, workspace, and server-selected Stripe price."""
    components = {
        "app_host": str(app_host or "").strip().lower(),
        "tier": str(tier or "").strip().lower(),
        "workspace_id": str(workspace_id or "").strip().lower(),
        "price_id": str(price_id or "").strip(),
    }
    if (
        not components["app_host"]
        or len(components["app_host"]) > 253
        or not components["tier"]
        or len(components["tier"]) > 32
        or len(components["workspace_id"]) > 64
        or not _PRICE_RE.fullmatch(components["price_id"])
        or any(
            "\0" in value or any(ord(character) < 32 for character in value)
            for value in components.values()
        )
    ):
        raise CheckoutStoreCorrupt(
            "pending checkout fingerprint inputs are invalid"
        )
    canonical = "\0".join(
        (
            "freshsky-subscription-v2",
            components["app_host"],
            components["tier"],
            components["workspace_id"],
            components["price_id"],
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MemoryCheckoutStore:
    """Thread-safe process-local store for tests and local development."""

    def __init__(self) -> None:
        self._records: dict[str, PendingCheckout] = {}
        self._lock = threading.RLock()

    def reserve(
        self,
        subject_id: str,
        fingerprint: str,
        *,
        now: datetime | None = None,
    ) -> PendingCheckout:
        timestamp = _utc(now or datetime.now(timezone.utc), "now")
        _validate_subject_and_fingerprint(subject_id, fingerprint)
        with self._lock:
            existing = self._records.get(subject_id)
            if existing is not None and existing.expires_at > timestamp:
                if existing.fingerprint != fingerprint:
                    raise CheckoutStoreConflict(
                        "another subscription checkout is already pending"
                    )
                return copy.deepcopy(existing)
            created = PendingCheckout(
                subject_id=subject_id,
                fingerprint=fingerprint,
                reservation_id=secrets.token_hex(16),
                created_at=timestamp,
                expires_at=timestamp + PENDING_LIFETIME,
            )
            self._records[subject_id] = created
            return copy.deepcopy(created)

    def attach_session(
        self,
        reservation: PendingCheckout,
        checkout_session_id: str,
    ) -> PendingCheckout:
        if not isinstance(reservation, PendingCheckout):
            raise CheckoutStoreCorrupt("pending checkout reservation is invalid")
        if (
            not isinstance(checkout_session_id, str)
            or not _CHECKOUT_SESSION_RE.fullmatch(checkout_session_id)
        ):
            raise CheckoutStoreCorrupt("Stripe Checkout session ID is invalid")
        with self._lock:
            existing = self._records.get(reservation.subject_id)
            _assert_same_reservation(existing, reservation)
            if existing.checkout_session_id not in {
                None,
                checkout_session_id,
            }:
                raise CheckoutStoreConflict(
                    "pending checkout is bound to another Stripe session"
                )
            updated = replace(
                existing,
                checkout_session_id=checkout_session_id,
            )
            self._records[reservation.subject_id] = updated
            return copy.deepcopy(updated)


TransactionRunner = Callable[[Any, Callable[[Any], Any]], Any]


class FirestoreCheckoutStore:
    """Transactional Firestore implementation for managed runtimes."""

    def __init__(
        self,
        client: Any,
        *,
        transaction_runner: TransactionRunner | None = None,
    ) -> None:
        if client is None:
            raise CheckoutStoreUnavailable("Firestore client is required")
        self._client = client
        self._transaction_runner = (
            transaction_runner or _google_transaction_runner
        )

    @classmethod
    def from_environment(
        cls,
        *,
        client: Any | None = None,
        transaction_runner: TransactionRunner | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "FirestoreCheckoutStore":
        environment = os.environ if environ is None else environ
        try:
            if client is None:
                from google.cloud import firestore

                project = (
                    environment.get("GOOGLE_CLOUD_PROJECT")
                    or environment.get("GCP_PROJECT")
                    or None
                )
                client = firestore.Client(project=project)
            return cls(
                client,
                transaction_runner=transaction_runner,
            )
        except CheckoutStoreError:
            raise
        except Exception as exc:
            raise CheckoutStoreUnavailable(
                "pending-checkout Firestore client is unavailable"
            ) from exc

    def reserve(
        self,
        subject_id: str,
        fingerprint: str,
        *,
        now: datetime | None = None,
    ) -> PendingCheckout:
        timestamp = _utc(now or datetime.now(timezone.utc), "now")
        _validate_subject_and_fingerprint(subject_id, fingerprint)
        try:
            reference = self._client.collection(COLLECTION).document(
                subject_id
            )
            transaction = self._client.transaction()
        except Exception as exc:
            raise CheckoutStoreUnavailable(
                "pending-checkout reservation is unavailable"
            ) from exc

        def operation(txn: Any) -> PendingCheckout:
            snapshot = reference.get(transaction=txn)
            existing = (
                PendingCheckout.from_document(snapshot.to_dict())
                if snapshot.exists
                else None
            )
            if existing is not None and existing.expires_at > timestamp:
                if existing.fingerprint != fingerprint:
                    raise CheckoutStoreConflict(
                        "another subscription checkout is already pending"
                    )
                return existing
            created = PendingCheckout(
                subject_id=subject_id,
                fingerprint=fingerprint,
                reservation_id=secrets.token_hex(16),
                created_at=timestamp,
                expires_at=timestamp + PENDING_LIFETIME,
            )
            txn.set(reference, created.to_document())
            return created

        try:
            return self._transaction_runner(transaction, operation)
        except (CheckoutStoreConflict, CheckoutStoreCorrupt):
            raise
        except Exception as exc:
            raise CheckoutStoreUnavailable(
                "pending-checkout reservation is unavailable"
            ) from exc

    def attach_session(
        self,
        reservation: PendingCheckout,
        checkout_session_id: str,
    ) -> PendingCheckout:
        if not isinstance(reservation, PendingCheckout):
            raise CheckoutStoreCorrupt("pending checkout reservation is invalid")
        if (
            not isinstance(checkout_session_id, str)
            or not _CHECKOUT_SESSION_RE.fullmatch(checkout_session_id)
        ):
            raise CheckoutStoreCorrupt("Stripe Checkout session ID is invalid")
        try:
            reference = self._client.collection(COLLECTION).document(
                reservation.subject_id
            )
            transaction = self._client.transaction()
        except Exception as exc:
            raise CheckoutStoreUnavailable(
                "pending-checkout session binding is unavailable"
            ) from exc

        def operation(txn: Any) -> PendingCheckout:
            snapshot = reference.get(transaction=txn)
            existing = (
                PendingCheckout.from_document(snapshot.to_dict())
                if snapshot.exists
                else None
            )
            _assert_same_reservation(existing, reservation)
            if existing.checkout_session_id not in {
                None,
                checkout_session_id,
            }:
                raise CheckoutStoreConflict(
                    "pending checkout is bound to another Stripe session"
                )
            updated = replace(
                existing,
                checkout_session_id=checkout_session_id,
            )
            txn.set(reference, updated.to_document())
            return updated

        try:
            return self._transaction_runner(transaction, operation)
        except (CheckoutStoreConflict, CheckoutStoreCorrupt):
            raise
        except Exception as exc:
            raise CheckoutStoreUnavailable(
                "pending-checkout session binding is unavailable"
            ) from exc


def _google_transaction_runner(
    transaction: Any,
    operation: Callable[[Any], Any],
) -> Any:
    from google.cloud import firestore

    return firestore.transactional(operation)(transaction)


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise CheckoutStoreCorrupt(f"pending checkout {field} is invalid")
    if value.tzinfo is None:
        raise CheckoutStoreCorrupt(
            f"pending checkout {field} must include a timezone"
        )
    return value.astimezone(timezone.utc)


def _validate_subject_and_fingerprint(
    subject_id: str,
    fingerprint: str,
) -> None:
    if not isinstance(subject_id, str) or not _PSEUDONYM_RE.fullmatch(
        subject_id
    ):
        raise CheckoutStoreCorrupt("pending checkout subject is invalid")
    if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.fullmatch(
        fingerprint
    ):
        raise CheckoutStoreCorrupt("pending checkout fingerprint is invalid")


def _assert_same_reservation(
    existing: PendingCheckout | None,
    expected: PendingCheckout,
) -> None:
    if existing is None:
        raise CheckoutStoreConflict("pending checkout reservation is missing")
    if (
        existing.subject_id != expected.subject_id
        or existing.fingerprint != expected.fingerprint
        or existing.reservation_id != expected.reservation_id
    ):
        raise CheckoutStoreConflict("pending checkout reservation changed")
