from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from freshsky_common.entitlements import (
    GUEST_PREVIEW_LIMIT,
    OWNER_EMAIL,
    InMemoryQuotaLedger,
    PlanTier,
    QuotaCode,
    QuotaExhausted,
    UsageSnapshot,
    evaluate_quota,
    make_usage_subject,
    resolve_entitlement,
    user_status_fields,
)
from freshsky_common.runtime_policy import (
    ALL_WORKSPACES,
    WORKFLOW_BUDGETS,
    WORKSPACE_LABELS,
    PolicyValidationError,
    WorkflowClass,
    WorkspaceId,
)


NOW = datetime(2026, 7, 26, 16, 30, tzinfo=timezone.utc)


def test_canonical_workspace_ids_and_labels_are_stable():
    assert [workspace.value for workspace in WorkspaceId] == [
        "funding",
        "education",
        "civic",
        "action_packs",
        "utilities",
    ]
    assert list(WORKSPACE_LABELS.values()) == [
        "FreshSky Funding Desk",
        "EduSafe Studio",
        "FreshSky CivicOps",
        "FreshSky Action Packs",
        "FreshSky Private Utility Lab",
    ]


def test_guest_has_one_portfolio_preview_pool_and_no_saved_projects():
    guest = resolve_entitlement()

    assert guest.tier is PlanTier.GUEST
    assert guest.allowed_workspaces == ALL_WORKSPACES
    assert guest.rolling_preview_units == GUEST_PREVIEW_LIMIT == 3
    assert guest.preview_window_days == 30
    assert guest.allowed_workflows == frozenset({WorkflowClass.PREVIEW})
    assert guest.server_saved_projects is False


@pytest.mark.parametrize(
    ("tier", "daily", "monthly", "workspaces"),
    [
        ("civic", 40, 200, {"civic"}),
        (
            "plus",
            60,
            300,
            {"education", "action_packs", "utilities"},
        ),
        (
            "advanced",
            120,
            600,
            {"funding", "education", "civic", "action_packs", "utilities"},
        ),
    ],
)
def test_paid_plan_workspace_and_unit_matrix(
    tier,
    daily,
    monthly,
    workspaces,
):
    entitlement = resolve_entitlement(tier)

    assert entitlement.daily_units == daily
    assert entitlement.monthly_units == monthly
    assert {item.value for item in entitlement.allowed_workspaces} == workspaces


def test_focus_requires_one_selected_non_civic_workspace():
    unresolved = resolve_entitlement("focus")
    selected = resolve_entitlement(
        "focus",
        selected_workspace=WorkspaceId.FUNDING,
    )

    assert unresolved.selection_required is True
    assert unresolved.allowed_workspaces == frozenset()
    assert selected.selection_required is False
    assert selected.allowed_workspaces == frozenset({WorkspaceId.FUNDING})
    assert selected.daily_units == 20
    assert selected.monthly_units == 100
    with pytest.raises(PolicyValidationError):
        resolve_entitlement("focus", selected_workspace="civic")


def test_owner_is_exact_identity_derived_verified_and_finite():
    owner = resolve_entitlement(
        "guest",
        email=OWNER_EMAIL.upper(),
        email_verified=True,
    )
    unverified = resolve_entitlement(
        "advanced",
        email=OWNER_EMAIL,
        email_verified=False,
    )

    assert owner.tier is PlanTier.OWNER
    assert owner.is_verified_owner is True
    assert owner.allowed_workspaces == ALL_WORKSPACES
    assert owner.daily_units == 500
    assert owner.monthly_units == 2_000
    assert owner.monthly_provider_cost_cap_usd == Decimal("5.00")
    assert unverified.tier is PlanTier.ADVANCED
    with pytest.raises(PolicyValidationError):
        resolve_entitlement("owner")


def test_workflow_weights_and_hard_ceilings_are_canonical():
    assert {
        workflow.value: budget.as_dict()
        for workflow, budget in WORKFLOW_BUDGETS.items()
    } == {
        "preview": {
            "usage_units": 1,
            "max_provider_calls": 1,
            "max_fan_out": 1,
            "max_total_tokens": 4_000,
            "max_elapsed_seconds": 30,
            "max_provider_cost_usd": "0.005",
        },
        "standard_agent": {
            "usage_units": 5,
            "max_provider_calls": 4,
            "max_fan_out": 4,
            "max_total_tokens": 12_000,
            "max_elapsed_seconds": 45,
            "max_provider_cost_usd": "0.025",
        },
        "bounded_agent": {
            "usage_units": 10,
            "max_provider_calls": 8,
            "max_fan_out": 8,
            "max_total_tokens": 24_000,
            "max_elapsed_seconds": 90,
            "max_provider_cost_usd": "0.05",
        },
        "full_funding_scan": {
            "usage_units": 20,
            "max_provider_calls": 12,
            "max_fan_out": 12,
            "max_total_tokens": 40_000,
            "max_elapsed_seconds": 120,
            "max_provider_cost_usd": "0.10",
        },
    }


def test_quota_allows_exact_boundary_then_exhausts_deterministically():
    focus = resolve_entitlement("focus", selected_workspace="funding")
    exact = evaluate_quota(
        focus,
        workspace="funding",
        workflow="standard_agent",
        usage=UsageSnapshot(daily_units=15, monthly_units=95),
        now=NOW,
    )
    daily_exhausted = evaluate_quota(
        focus,
        workspace="funding",
        workflow="standard_agent",
        usage=UsageSnapshot(daily_units=16, monthly_units=50),
        now=NOW,
    )
    monthly_exhausted = evaluate_quota(
        focus,
        workspace="funding",
        workflow="standard_agent",
        usage=UsageSnapshot(daily_units=0, monthly_units=96),
        now=NOW,
    )

    assert exact.allowed is True
    assert exact.code is QuotaCode.ALLOWED
    assert daily_exhausted.code is QuotaCode.DAILY_UNITS_EXHAUSTED
    assert daily_exhausted.reset_at == datetime(
        2026, 7, 27, tzinfo=timezone.utc
    )
    assert monthly_exhausted.code is QuotaCode.MONTHLY_UNITS_EXHAUSTED
    assert monthly_exhausted.reset_at == datetime(
        2026, 8, 1, tzinfo=timezone.utc
    )


def test_quota_rejects_workspace_workflow_and_provider_cost_before_start():
    focus = resolve_entitlement("focus", selected_workspace="education")
    owner = resolve_entitlement(
        email=OWNER_EMAIL,
        email_verified=True,
    )

    wrong_workspace = evaluate_quota(
        focus,
        workspace="utilities",
        workflow="preview",
        usage=UsageSnapshot(),
        now=NOW,
    )
    wrong_workflow = evaluate_quota(
        resolve_entitlement(),
        workspace="funding",
        workflow="standard_agent",
        usage=UsageSnapshot(),
        now=NOW,
    )
    wrong_full_scan_workspace = evaluate_quota(
        owner,
        workspace="education",
        workflow="full_funding_scan",
        usage=UsageSnapshot(),
        now=NOW,
    )
    cost_exhausted = evaluate_quota(
        owner,
        workspace="funding",
        workflow="full_funding_scan",
        usage=UsageSnapshot(
            monthly_provider_cost_usd=Decimal("4.91")
        ),
        now=NOW,
    )

    assert wrong_workspace.code is QuotaCode.WORKSPACE_NOT_ENTITLED
    assert wrong_workflow.code is QuotaCode.WORKFLOW_NOT_ALLOWED
    assert wrong_full_scan_workspace.code is QuotaCode.WORKFLOW_NOT_ALLOWED
    assert cost_exhausted.code is QuotaCode.PROVIDER_COST_CAP_EXHAUSTED


def test_guest_ledger_is_shared_across_workspaces_and_rolls_after_30_days():
    guest = resolve_entitlement()
    ledger = InMemoryQuotaLedger()
    subject = "guest-pseudonym"

    ledger.reserve(
        subject, guest, workspace="funding", workflow="preview", now=NOW
    )
    ledger.reserve(
        subject, guest, workspace="civic", workflow="preview", now=NOW
    )
    ledger.reserve(
        subject, guest, workspace="utilities", workflow="preview", now=NOW
    )
    with pytest.raises(QuotaExhausted) as exhausted:
        ledger.reserve(
            subject,
            guest,
            workspace="education",
            workflow="preview",
            now=NOW,
        )
    assert exhausted.value.decision.code is QuotaCode.PREVIEW_QUOTA_EXHAUSTED

    next_reservation = ledger.reserve(
        subject,
        guest,
        workspace="education",
        workflow="preview",
        now=NOW + timedelta(days=30),
    )
    assert next_reservation.usage_units == 1


def test_ledger_reserves_max_cost_then_reconciles_actual_cost_idempotently():
    owner = resolve_entitlement(email=OWNER_EMAIL, email_verified=True)
    ledger = InMemoryQuotaLedger()
    reservation = ledger.reserve(
        "owner-pseudonym",
        owner,
        workspace="funding",
        workflow="bounded_agent",
        now=NOW,
    )

    before = ledger.usage_snapshot("owner-pseudonym", now=NOW)
    first = ledger.reconcile(
        reservation,
        actual_provider_cost_usd="0.012",
    )
    second = ledger.reconcile(
        reservation,
        actual_provider_cost_usd=Decimal("0.012"),
    )
    after = ledger.usage_snapshot("owner-pseudonym", now=NOW)

    assert before.monthly_units == 10
    assert before.monthly_provider_cost_usd == Decimal("0.05")
    assert first == second
    assert first.released_provider_cost_usd == Decimal("0.038")
    assert after.monthly_provider_cost_usd == Decimal("0.012")
    with pytest.raises(PolicyValidationError):
        ledger.reconcile(
            reservation,
            actual_provider_cost_usd="0.051",
        )


def test_user_status_hook_is_additive_and_json_safe():
    plus = resolve_entitlement("plus")
    fields = user_status_fields(
        plus,
        workspace="education",
        usage=UsageSnapshot(
            daily_units=5,
            monthly_units=20,
            monthly_provider_cost_usd=Decimal("0.12"),
        ),
    )

    assert fields["plan_tier"] == "plus"
    assert fields["workspace_access"] is True
    assert fields["usage_unit_limits"] == {
        "daily": 60,
        "monthly": 300,
        "rolling_30_day_previews": None,
    }
    assert fields["usage_units"]["monthly_used"] == 20
    assert fields["workflow_budgets"]["standard_agent"]["usage_units"] == 5


def test_usage_subject_is_pseudonymous_and_stable():
    first = make_usage_subject("Person@Example.com", "secret")
    second = make_usage_subject("person@example.com", "secret")

    assert first == second
    assert len(first) == 64
    assert "person@example.com" not in first
