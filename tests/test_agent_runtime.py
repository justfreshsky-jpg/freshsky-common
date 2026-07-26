from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from freshsky_common.agent_runtime import (
    AgentRun,
    AgentRunStatus,
    ArtifactRecord,
    SourceRecord,
)
from freshsky_common.runtime_policy import PolicyValidationError


NOW = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)


def source(**changes):
    values = {
        "source_id": "nj-dcf-licensing",
        "title": "New Jersey child care licensing",
        "uri": "https://www.nj.gov/dcf/providers/licensing/",
        "retrieved_at": NOW,
        "jurisdiction": "New Jersey",
        "effective_date": date(2026, 1, 1),
        "retrieval_date": date(2026, 7, 26),
        "license": "Public government record",
        "next_review_date": date(2026, 10, 26),
        "reviewer": "FreshSky source review",
        "publisher": "New Jersey Department of Children and Families",
        "sha256": "a" * 64,
        "metadata": {"official": True},
    }
    values.update(changes)
    return SourceRecord(**values)


def test_source_record_round_trips_with_timezone_and_provenance():
    record = source()
    restored = SourceRecord.from_dict(record.to_dict())

    assert restored == record
    assert restored.retrieved_at.tzinfo == timezone.utc
    assert restored.official_url == record.uri
    assert restored.retrieval_date == date(2026, 7, 26)
    assert restored.content_hash == f"sha256:{'a' * 64}"
    assert restored.jurisdiction == "New Jersey"
    assert restored.next_review_date == date(2026, 10, 26)
    assert restored.reviewer == "FreshSky source review"
    assert restored.metadata == {"official": True}


def test_source_record_accepts_official_url_and_retrieval_date_aliases():
    record = SourceRecord.from_dict(
        {
            "source_id": "official-source",
            "title": "Official source",
            "url": "https://agency.example/policy",
            "retrieval_date": "2026-07-26",
            "content_hash": f"sha256:{'b' * 64}",
            "jurisdiction": "US",
        }
    )

    assert record.uri == "https://agency.example/policy"
    assert record.official_url == record.uri
    assert record.retrieved_at == NOW.replace(hour=0)
    assert record.sha256 == "b" * 64


@pytest.mark.parametrize(
    "changes",
    [
        {"source_id": "contains spaces"},
        {"uri": "javascript:alert(1)"},
        {"uri": "https://user:secret@example.com/private"},
        {"retrieved_at": datetime(2026, 7, 26)},
        {"sha256": "short"},
        {
            "content_hash": f"sha256:{'b' * 64}",
            "sha256": "a" * 64,
        },
        {"next_review_date": date(2026, 7, 25)},
        {"metadata": {"bad": float("nan")}},
    ],
)
def test_source_record_rejects_unsafe_or_ambiguous_values(changes):
    with pytest.raises(PolicyValidationError):
        source(**changes)


def test_agent_run_round_trips_and_derives_workflow_usage_units():
    artifact = ArtifactRecord(
        artifact_id="action-pack-1",
        artifact_type="action_pack",
        created_at=NOW + timedelta(seconds=18),
        uri="urn:freshsky:artifact:action-pack-1",
        content_hash=f"sha256:{'c' * 64}",
    )
    run = AgentRun(
        run_id="run-123",
        reservation_id="reservation-123",
        workspace="funding",
        workflow="standard_agent",
        status=AgentRunStatus.SUCCEEDED,
        created_at=NOW,
        tenant="freshsky-llc",
        agent_type="funding_research",
        approved_claim_references=("claim-001",),
        missing_inputs=("municipal zoning letter",),
        artifacts=(artifact,),
        required_approval="operator",
        updated_at=NOW + timedelta(seconds=21),
        approval_requested_at=NOW + timedelta(seconds=20),
        approved_at=NOW + timedelta(seconds=21),
        approved_by="admin@freshskyllc.com",
        started_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=20),
        provider_calls=4,
        input_tokens=6_000,
        output_tokens=5_000,
        elapsed_seconds=19,
        provider_cost_usd=Decimal("0.02"),
        sources=(source(),),
        metadata={"route": "funding_scan"},
    )
    restored = AgentRun.from_dict(run.to_dict())

    assert run.usage_units == 5
    assert run.total_tokens == 11_000
    assert run.tenant == "freshsky-llc"
    assert run.agent_type == "funding_research"
    assert run.approved_claim_references == ("claim-001",)
    assert run.artifacts == (artifact,)
    assert run.required_approval == "operator"
    assert run.to_dict()["audit_timestamps"]["approved_at"].endswith("+00:00")
    assert restored == run
    assert "prompt" not in run.to_dict()
    assert "response" not in run.to_dict()


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_calls": 5},
        {"input_tokens": 12_001},
        {"elapsed_seconds": 46},
        {"provider_cost_usd": "0.026"},
        {"usage_units": 1},
    ],
)
def test_agent_run_enforces_standard_agent_hard_ceilings(changes):
    values = {
        "run_id": "run-123",
        "workspace": "education",
        "workflow": "standard_agent",
        "status": "running",
        "created_at": NOW,
        "started_at": NOW,
    }
    values.update(changes)

    with pytest.raises(PolicyValidationError):
        AgentRun(**values)


def test_agent_run_rejects_duplicate_sources_and_invalid_timing():
    with pytest.raises(PolicyValidationError):
        AgentRun(
            run_id="run-123",
            workspace="civic",
            workflow="preview",
            status="succeeded",
            created_at=NOW,
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=1),
            sources=(source(), source()),
        )

    with pytest.raises(PolicyValidationError):
        AgentRun(
            run_id="run-123",
            workspace="civic",
            workflow="preview",
            status="running",
            created_at=NOW,
        )

    independent_claim = AgentRun(
        run_id="run-claim",
        workspace="civic",
        workflow="preview",
        status="queued",
        created_at=NOW,
        sources=(source(),),
        approved_claim_references=("claim-independent-001",),
    )
    assert independent_claim.approved_claim_references == (
        "claim-independent-001",
    )


def test_agent_run_requires_coherent_approval_audit_fields():
    with pytest.raises(PolicyValidationError, match="approved_by"):
        AgentRun(
            run_id="run-approval",
            workspace="action_packs",
            workflow="preview",
            status="queued",
            created_at=NOW,
            approved_at=NOW + timedelta(seconds=1),
        )


def test_quota_exhausted_run_cannot_record_provider_usage():
    clean = AgentRun(
        run_id="run-quota",
        workspace="utilities",
        workflow="preview",
        status="quota_exhausted",
        created_at=NOW,
        error_code="preview_quota_exhausted",
    )
    assert clean.status is AgentRunStatus.QUOTA_EXHAUSTED

    with pytest.raises(PolicyValidationError):
        AgentRun(
            run_id="run-quota",
            workspace="utilities",
            workflow="preview",
            status="quota_exhausted",
            created_at=NOW,
            provider_calls=1,
        )
