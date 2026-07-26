"""Server-side workspace entitlements and deterministic usage-unit quotas.

The policy functions in this module are pure and deterministic.  The included
in-memory ledger is useful for tests and single-process development; production
services should implement :class:`QuotaLedger` with an atomic shared store (or
use the FreshSky central meter) so parallel Cloud Run instances share one
portfolio-wide balance.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Final, Protocol

from .runtime_policy import (
    ALL_WORKSPACES,
    NON_CIVIC_WORKSPACES,
    WORKFLOW_BUDGETS,
    WORKSPACE_LABELS,
    PolicyValidationError,
    WorkflowClass,
    WorkspaceId,
    decimal_usd,
    parse_workflow,
    parse_workspace,
    workflow_budget,
)


QUOTA_POLICY_VERSION: Final[str] = "2026-07-26"
OWNER_EMAIL: Final[str] = "admin@freshskyllc.com"
GUEST_PREVIEW_LIMIT: Final[int] = 3
GUEST_PREVIEW_WINDOW_DAYS: Final[int] = 30


class PlanTier(str, Enum):
    GUEST = "guest"
    FOCUS = "focus"
    CIVIC = "civic"
    PLUS = "plus"
    ADVANCED = "advanced"
    OWNER = "owner"


@dataclass(frozen=True)
class PlanEntitlement:
    tier: PlanTier
    allowed_workspaces: frozenset[WorkspaceId]
    daily_units: int | None
    monthly_units: int | None
    allowed_workflows: frozenset[WorkflowClass]
    selected_workspace: WorkspaceId | None = None
    selection_required: bool = False
    rolling_preview_units: int | None = None
    preview_window_days: int | None = None
    monthly_provider_cost_cap_usd: Decimal | None = None
    server_saved_projects: bool = True
    is_verified_owner: bool = False

    def __post_init__(self) -> None:
        for field_name in ("daily_units", "monthly_units", "rolling_preview_units"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise PolicyValidationError(
                    f"{field_name} must be a non-negative integer"
                )
        if self.preview_window_days is not None and (
            not isinstance(self.preview_window_days, int)
            or isinstance(self.preview_window_days, bool)
            or self.preview_window_days <= 0
        ):
            raise PolicyValidationError(
                "preview_window_days must be a positive integer"
            )
        if self.monthly_provider_cost_cap_usd is not None:
            parsed_cap = decimal_usd(
                self.monthly_provider_cost_cap_usd,
                field_name="monthly_provider_cost_cap_usd",
            )
            object.__setattr__(
                self,
                "monthly_provider_cost_cap_usd",
                parsed_cap,
            )

    def can_access(self, workspace: WorkspaceId | str) -> bool:
        return parse_workspace(workspace) in self.allowed_workspaces


_PAID_PLAN_LIMITS: Final[dict[PlanTier, tuple[int, int]]] = {
    PlanTier.FOCUS: (20, 100),
    PlanTier.CIVIC: (40, 200),
    PlanTier.PLUS: (60, 300),
    PlanTier.ADVANCED: (120, 600),
}
_ALL_WORKFLOWS: Final[frozenset[WorkflowClass]] = frozenset(WorkflowClass)


def parse_plan_tier(value: PlanTier | str | None) -> PlanTier:
    if isinstance(value, PlanTier):
        return value
    normalized = str(value or PlanTier.GUEST.value).strip().lower()
    try:
        return PlanTier(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in PlanTier if item is not PlanTier.OWNER)
        raise PolicyValidationError(
            f"unknown plan tier {value!r}; expected one of: {allowed}"
        ) from exc


def is_verified_owner(email: str | None, email_verified: bool) -> bool:
    """Return true only for the one exact, verified owner identity."""
    normalized = str(email or "").strip().lower()
    return bool(email_verified) and hmac.compare_digest(normalized, OWNER_EMAIL)


def resolve_entitlement(
    plan_tier: PlanTier | str | None = PlanTier.GUEST,
    *,
    selected_workspace: WorkspaceId | str | None = None,
    email: str | None = None,
    email_verified: bool = False,
) -> PlanEntitlement:
    """Resolve immutable access policy from server-verified account facts.

    ``owner`` is never trusted as a supplied plan value.  It is derived only
    from the exact verified owner email.  A Focus account without a selection
    is represented safely with no allowed workspaces and
    ``selection_required=True``; callers can render a selector but cannot grant
    workspace access before the choice is persisted server-side.
    """
    if is_verified_owner(email, email_verified):
        return PlanEntitlement(
            tier=PlanTier.OWNER,
            allowed_workspaces=ALL_WORKSPACES,
            daily_units=500,
            monthly_units=2_000,
            allowed_workflows=_ALL_WORKFLOWS,
            monthly_provider_cost_cap_usd=Decimal("5.00"),
            is_verified_owner=True,
        )

    requested_tier = parse_plan_tier(plan_tier)
    if requested_tier is PlanTier.OWNER:
        raise PolicyValidationError(
            "owner entitlement is identity-derived and requires verified owner email"
        )
    if requested_tier is PlanTier.GUEST:
        return PlanEntitlement(
            tier=PlanTier.GUEST,
            allowed_workspaces=ALL_WORKSPACES,
            daily_units=None,
            monthly_units=None,
            allowed_workflows=frozenset({WorkflowClass.PREVIEW}),
            rolling_preview_units=GUEST_PREVIEW_LIMIT,
            preview_window_days=GUEST_PREVIEW_WINDOW_DAYS,
            server_saved_projects=False,
        )
    if requested_tier is PlanTier.FOCUS:
        selected = (
            parse_workspace(selected_workspace)
            if selected_workspace is not None and str(selected_workspace).strip()
            else None
        )
        if selected is WorkspaceId.CIVIC:
            raise PolicyValidationError(
                "Focus selection must be one non-civic workspace"
            )
        if selected is not None and selected not in NON_CIVIC_WORKSPACES:
            raise PolicyValidationError(
                "Focus selection must be one non-civic workspace"
            )
        daily, monthly = _PAID_PLAN_LIMITS[requested_tier]
        return PlanEntitlement(
            tier=requested_tier,
            allowed_workspaces=frozenset({selected}) if selected else frozenset(),
            daily_units=daily,
            monthly_units=monthly,
            allowed_workflows=_ALL_WORKFLOWS,
            selected_workspace=selected,
            selection_required=selected is None,
        )
    if requested_tier is PlanTier.CIVIC:
        allowed = frozenset({WorkspaceId.CIVIC})
    elif requested_tier is PlanTier.PLUS:
        allowed = frozenset(
            {
                WorkspaceId.EDUCATION,
                WorkspaceId.ACTION_PACKS,
                WorkspaceId.UTILITIES,
            }
        )
    else:
        allowed = ALL_WORKSPACES
    daily, monthly = _PAID_PLAN_LIMITS[requested_tier]
    return PlanEntitlement(
        tier=requested_tier,
        allowed_workspaces=allowed,
        daily_units=daily,
        monthly_units=monthly,
        allowed_workflows=_ALL_WORKFLOWS,
    )


class QuotaCode(str, Enum):
    ALLOWED = "allowed"
    FOCUS_SELECTION_REQUIRED = "focus_selection_required"
    WORKSPACE_NOT_ENTITLED = "workspace_not_entitled"
    WORKFLOW_NOT_ALLOWED = "workflow_not_allowed"
    PREVIEW_QUOTA_EXHAUSTED = "preview_quota_exhausted"
    DAILY_UNITS_EXHAUSTED = "daily_units_exhausted"
    MONTHLY_UNITS_EXHAUSTED = "monthly_units_exhausted"
    PROVIDER_COST_CAP_EXHAUSTED = "provider_cost_cap_exhausted"


@dataclass(frozen=True)
class UsageSnapshot:
    daily_units: int = 0
    monthly_units: int = 0
    rolling_preview_units: int = 0
    monthly_provider_cost_usd: Decimal = Decimal("0")
    preview_reset_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "daily_units",
            "monthly_units",
            "rolling_preview_units",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise PolicyValidationError(
                    f"{field_name} must be a non-negative integer"
                )
        parsed_cost = decimal_usd(
            self.monthly_provider_cost_usd,
            field_name="monthly_provider_cost_usd",
        )
        object.__setattr__(
            self,
            "monthly_provider_cost_usd",
            parsed_cost,
        )
        if self.preview_reset_at is not None:
            _aware_utc(self.preview_reset_at, "preview_reset_at")


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    code: QuotaCode
    required_units: int
    reserved_provider_cost_usd: Decimal
    daily_remaining: int | None
    monthly_remaining: int | None
    preview_remaining: int | None
    provider_cost_remaining_usd: Decimal | None
    reset_at: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "code": self.code.value,
            "required_units": self.required_units,
            "reserved_provider_cost_usd": str(self.reserved_provider_cost_usd),
            "daily_remaining": self.daily_remaining,
            "monthly_remaining": self.monthly_remaining,
            "preview_remaining": self.preview_remaining,
            "provider_cost_remaining_usd": (
                str(self.provider_cost_remaining_usd)
                if self.provider_cost_remaining_usd is not None
                else None
            ),
            "reset_at": self.reset_at.isoformat() if self.reset_at else None,
        }


def evaluate_quota(
    entitlement: PlanEntitlement,
    *,
    workspace: WorkspaceId | str,
    workflow: WorkflowClass | str,
    usage: UsageSnapshot,
    now: datetime | None = None,
) -> QuotaDecision:
    """Return the same decision for the same policy, usage, and UTC time.

    The function reserves the workflow's maximum provider cost when testing a
    capped account.  This guarantees that work fails before it starts if the
    complete run cannot fit; callers reconcile the reservation to actual cost
    after the run.
    """
    current = _aware_utc(now or datetime.now(timezone.utc), "now")
    workspace_id = parse_workspace(workspace)
    workflow_id = parse_workflow(workflow)
    budget = workflow_budget(workflow_id)
    daily_remaining = _remaining(entitlement.daily_units, usage.daily_units)
    monthly_remaining = _remaining(entitlement.monthly_units, usage.monthly_units)
    preview_remaining = _remaining(
        entitlement.rolling_preview_units,
        usage.rolling_preview_units,
    )
    provider_remaining = _remaining_decimal(
        entitlement.monthly_provider_cost_cap_usd,
        usage.monthly_provider_cost_usd,
    )

    def decision(
        allowed: bool,
        code: QuotaCode,
        reset_at: datetime | None = None,
    ) -> QuotaDecision:
        return QuotaDecision(
            allowed=allowed,
            code=code,
            required_units=budget.usage_units,
            reserved_provider_cost_usd=budget.max_provider_cost_usd,
            daily_remaining=daily_remaining,
            monthly_remaining=monthly_remaining,
            preview_remaining=preview_remaining,
            provider_cost_remaining_usd=provider_remaining,
            reset_at=reset_at,
        )

    if entitlement.selection_required:
        return decision(False, QuotaCode.FOCUS_SELECTION_REQUIRED)
    if workspace_id not in entitlement.allowed_workspaces:
        return decision(False, QuotaCode.WORKSPACE_NOT_ENTITLED)
    if workflow_id not in entitlement.allowed_workflows:
        return decision(False, QuotaCode.WORKFLOW_NOT_ALLOWED)
    if (
        workflow_id is WorkflowClass.FULL_FUNDING_SCAN
        and workspace_id is not WorkspaceId.FUNDING
    ):
        return decision(False, QuotaCode.WORKFLOW_NOT_ALLOWED)
    if (
        entitlement.rolling_preview_units is not None
        and usage.rolling_preview_units + budget.usage_units
        > entitlement.rolling_preview_units
    ):
        return decision(
            False,
            QuotaCode.PREVIEW_QUOTA_EXHAUSTED,
            usage.preview_reset_at,
        )
    if (
        entitlement.daily_units is not None
        and usage.daily_units + budget.usage_units > entitlement.daily_units
    ):
        return decision(
            False,
            QuotaCode.DAILY_UNITS_EXHAUSTED,
            _next_utc_day(current),
        )
    if (
        entitlement.monthly_units is not None
        and usage.monthly_units + budget.usage_units > entitlement.monthly_units
    ):
        return decision(
            False,
            QuotaCode.MONTHLY_UNITS_EXHAUSTED,
            _next_utc_month(current),
        )
    if (
        entitlement.monthly_provider_cost_cap_usd is not None
        and usage.monthly_provider_cost_usd + budget.max_provider_cost_usd
        > entitlement.monthly_provider_cost_cap_usd
    ):
        return decision(
            False,
            QuotaCode.PROVIDER_COST_CAP_EXHAUSTED,
            _next_utc_month(current),
        )
    return decision(True, QuotaCode.ALLOWED)


class QuotaExhausted(RuntimeError):
    """Raised before a run begins when its complete reservation cannot fit."""

    def __init__(self, decision: QuotaDecision):
        super().__init__(decision.code.value)
        self.decision = decision


@dataclass(frozen=True)
class QuotaReservation:
    reservation_id: str
    subject_id: str
    tier: PlanTier
    workspace: WorkspaceId
    workflow: WorkflowClass
    usage_units: int
    reserved_provider_cost_usd: Decimal
    created_at: datetime


@dataclass(frozen=True)
class CostReconciliation:
    reservation_id: str
    reserved_provider_cost_usd: Decimal
    actual_provider_cost_usd: Decimal
    released_provider_cost_usd: Decimal


class QuotaLedger(Protocol):
    """Atomic backing-store contract used by server request handlers."""

    def reserve(
        self,
        subject_id: str,
        entitlement: PlanEntitlement,
        *,
        workspace: WorkspaceId | str,
        workflow: WorkflowClass | str,
        now: datetime | None = None,
    ) -> QuotaReservation:
        ...

    def reconcile(
        self,
        reservation: QuotaReservation,
        *,
        actual_provider_cost_usd: Decimal | str | int | float,
    ) -> CostReconciliation:
        ...


@dataclass
class _ReservationState:
    reservation: QuotaReservation
    actual_provider_cost_usd: Decimal | None = None


@dataclass
class _SubjectState:
    daily_units: dict[str, int] = field(default_factory=dict)
    monthly_units: dict[str, int] = field(default_factory=dict)
    preview_events: list[datetime] = field(default_factory=list)
    provider_cost_usd: dict[str, Decimal] = field(default_factory=dict)


class InMemoryQuotaLedger:
    """Thread-safe reference ledger for tests and single-process development."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subjects: dict[str, _SubjectState] = {}
        self._reservations: dict[str, _ReservationState] = {}

    def reserve(
        self,
        subject_id: str,
        entitlement: PlanEntitlement,
        *,
        workspace: WorkspaceId | str,
        workflow: WorkflowClass | str,
        now: datetime | None = None,
    ) -> QuotaReservation:
        subject = _validate_subject_id(subject_id)
        current = _aware_utc(now or datetime.now(timezone.utc), "now")
        workspace_id = parse_workspace(workspace)
        workflow_id = parse_workflow(workflow)
        budget = workflow_budget(workflow_id)
        day_key = current.strftime("%Y%m%d")
        month_key = current.strftime("%Y%m")

        with self._lock:
            state = self._subjects.setdefault(subject, _SubjectState())
            cutoff = current - timedelta(days=GUEST_PREVIEW_WINDOW_DAYS)
            state.preview_events[:] = [
                event for event in state.preview_events if event > cutoff
            ]
            preview_reset_at = (
                min(state.preview_events) + timedelta(days=GUEST_PREVIEW_WINDOW_DAYS)
                if state.preview_events
                else None
            )
            usage = UsageSnapshot(
                daily_units=state.daily_units.get(day_key, 0),
                monthly_units=state.monthly_units.get(month_key, 0),
                rolling_preview_units=len(state.preview_events),
                monthly_provider_cost_usd=state.provider_cost_usd.get(
                    month_key, Decimal("0")
                ),
                preview_reset_at=preview_reset_at,
            )
            quota = evaluate_quota(
                entitlement,
                workspace=workspace_id,
                workflow=workflow_id,
                usage=usage,
                now=current,
            )
            if not quota.allowed:
                raise QuotaExhausted(quota)

            state.daily_units[day_key] = usage.daily_units + budget.usage_units
            state.monthly_units[month_key] = (
                usage.monthly_units + budget.usage_units
            )
            if entitlement.tier is PlanTier.GUEST:
                state.preview_events.extend(
                    current for _ in range(budget.usage_units)
                )
            state.provider_cost_usd[month_key] = (
                usage.monthly_provider_cost_usd + budget.max_provider_cost_usd
            )
            reservation = QuotaReservation(
                reservation_id=uuid.uuid4().hex,
                subject_id=subject,
                tier=entitlement.tier,
                workspace=workspace_id,
                workflow=workflow_id,
                usage_units=budget.usage_units,
                reserved_provider_cost_usd=budget.max_provider_cost_usd,
                created_at=current,
            )
            self._reservations[reservation.reservation_id] = _ReservationState(
                reservation=reservation
            )
            return reservation

    def reconcile(
        self,
        reservation: QuotaReservation,
        *,
        actual_provider_cost_usd: Decimal | str | int | float,
    ) -> CostReconciliation:
        actual = decimal_usd(
            actual_provider_cost_usd,
            field_name="actual_provider_cost_usd",
        )
        if actual > reservation.reserved_provider_cost_usd:
            raise PolicyValidationError(
                "actual provider cost exceeds the workflow reservation"
            )
        with self._lock:
            stored = self._reservations.get(reservation.reservation_id)
            if stored is None or stored.reservation != reservation:
                raise PolicyValidationError("unknown quota reservation")
            if stored.actual_provider_cost_usd is not None:
                if stored.actual_provider_cost_usd != actual:
                    raise PolicyValidationError(
                        "quota reservation was already reconciled differently"
                    )
                released = reservation.reserved_provider_cost_usd - actual
                return CostReconciliation(
                    reservation_id=reservation.reservation_id,
                    reserved_provider_cost_usd=reservation.reserved_provider_cost_usd,
                    actual_provider_cost_usd=actual,
                    released_provider_cost_usd=released,
                )

            month_key = reservation.created_at.strftime("%Y%m")
            state = self._subjects[reservation.subject_id]
            reserved_total = state.provider_cost_usd.get(month_key, Decimal("0"))
            released = reservation.reserved_provider_cost_usd - actual
            state.provider_cost_usd[month_key] = max(
                Decimal("0"), reserved_total - released
            )
            stored.actual_provider_cost_usd = actual
            return CostReconciliation(
                reservation_id=reservation.reservation_id,
                reserved_provider_cost_usd=reservation.reserved_provider_cost_usd,
                actual_provider_cost_usd=actual,
                released_provider_cost_usd=released,
            )

    def usage_snapshot(
        self,
        subject_id: str,
        *,
        now: datetime | None = None,
    ) -> UsageSnapshot:
        """Read current counters for status/testing without mutating quota."""
        subject = _validate_subject_id(subject_id)
        current = _aware_utc(now or datetime.now(timezone.utc), "now")
        day_key = current.strftime("%Y%m%d")
        month_key = current.strftime("%Y%m")
        with self._lock:
            state = self._subjects.get(subject, _SubjectState())
            cutoff = current - timedelta(days=GUEST_PREVIEW_WINDOW_DAYS)
            preview_events = [
                event for event in state.preview_events if event > cutoff
            ]
            return UsageSnapshot(
                daily_units=state.daily_units.get(day_key, 0),
                monthly_units=state.monthly_units.get(month_key, 0),
                rolling_preview_units=len(preview_events),
                monthly_provider_cost_usd=state.provider_cost_usd.get(
                    month_key, Decimal("0")
                ),
                preview_reset_at=(
                    min(preview_events)
                    + timedelta(days=GUEST_PREVIEW_WINDOW_DAYS)
                    if preview_events
                    else None
                ),
            )


def make_usage_subject(identity: str, signing_key: str) -> str:
    """Pseudonymize a server-verified identity before quota persistence."""
    identity_value = str(identity or "").strip().lower()
    key = str(signing_key or "")
    if not identity_value:
        raise PolicyValidationError("identity is required")
    if not key:
        raise PolicyValidationError("signing_key is required")
    return hmac.new(
        key.encode("utf-8"),
        identity_value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def user_status_fields(
    entitlement: PlanEntitlement,
    *,
    workspace: WorkspaceId | str | None = None,
    usage: UsageSnapshot | None = None,
) -> dict:
    """Return additive fields suitable for the existing ``/api/user-status``."""
    workspace_id = parse_workspace(workspace) if workspace is not None else None
    status = {
        "quota_policy_version": QUOTA_POLICY_VERSION,
        "plan_tier": entitlement.tier.value,
        "quota_unit": "usage_unit",
        "workspace_entitlements": [
            {
                "id": item.value,
                "name": WORKSPACE_LABELS[item],
            }
            for item in WorkspaceId
            if item in entitlement.allowed_workspaces
        ],
        "workspace_ids": [
            item.value
            for item in WorkspaceId
            if item in entitlement.allowed_workspaces
        ],
        "selected_workspace": (
            entitlement.selected_workspace.value
            if entitlement.selected_workspace
            else None
        ),
        "workspace_selection_required": entitlement.selection_required,
        "server_saved_projects": entitlement.server_saved_projects,
        "usage_unit_limits": {
            "daily": entitlement.daily_units,
            "monthly": entitlement.monthly_units,
            "rolling_30_day_previews": entitlement.rolling_preview_units,
        },
        "preview_window_days": entitlement.preview_window_days,
        "monthly_provider_cost_cap_usd": (
            str(entitlement.monthly_provider_cost_cap_usd)
            if entitlement.monthly_provider_cost_cap_usd is not None
            else None
        ),
        "verified_owner": entitlement.is_verified_owner,
        "workflow_budgets": {
            workflow.value: budget.as_dict()
            for workflow, budget in WORKFLOW_BUDGETS.items()
            if workflow in entitlement.allowed_workflows
        },
    }
    if workspace_id is not None:
        status["workspace_id"] = workspace_id.value
        status["workspace_access"] = workspace_id in entitlement.allowed_workspaces
        status["workspace_full_access"] = bool(
            workspace_id in entitlement.allowed_workspaces
            and entitlement.tier is not PlanTier.GUEST
        )
    if usage is not None:
        status["usage_units"] = {
            "daily_used": usage.daily_units,
            "monthly_used": usage.monthly_units,
            "rolling_30_day_previews_used": usage.rolling_preview_units,
            "monthly_provider_cost_usd": str(
                usage.monthly_provider_cost_usd
            ),
        }
    return status


def _remaining(limit: int | None, used: int) -> int | None:
    return None if limit is None else max(0, limit - used)


def _remaining_decimal(
    limit: Decimal | None,
    used: Decimal,
) -> Decimal | None:
    return None if limit is None else max(Decimal("0"), limit - used)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PolicyValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _next_utc_day(value: datetime) -> datetime:
    return (value + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _next_utc_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(
            year=value.year + 1,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    return value.replace(
        month=value.month + 1,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _validate_subject_id(value: str) -> str:
    subject = str(value or "").strip()
    if not subject or len(subject) > 256:
        raise PolicyValidationError("subject_id must contain 1-256 characters")
    return subject
