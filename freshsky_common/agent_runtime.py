"""Validated, reusable records for bounded FreshSky agent executions."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlparse

from .runtime_policy import (
    PolicyValidationError,
    WorkflowClass,
    WorkspaceId,
    decimal_usd,
    parse_workflow,
    parse_workspace,
    workflow_budget,
)


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
_ALLOWED_SOURCE_SCHEMES = frozenset({"http", "https", "urn", "gs", "s3"})


class AgentRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    QUOTA_EXHAUSTED = "quota_exhausted"


@dataclass(frozen=True)
class ArtifactRecord:
    """Reference to a generated artifact; artifact content stays elsewhere."""

    artifact_id: str
    artifact_type: str
    created_at: datetime
    uri: str | None = None
    content_hash: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_id(self.artifact_id, "artifact_id")
        _validate_id(self.artifact_type, "artifact_type")
        object.__setattr__(
            self,
            "created_at",
            _parse_datetime(self.created_at, "created_at"),
        )
        if self.uri is not None:
            _validate_source_uri(self.uri)
        if self.content_hash is not None:
            object.__setattr__(
                self,
                "content_hash",
                _normalize_content_hash(self.content_hash),
            )
        object.__setattr__(
            self,
            "metadata",
            _validated_metadata(self.metadata),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactRecord":
        if not isinstance(value, Mapping):
            raise PolicyValidationError("ArtifactRecord input must be a mapping")
        return cls(
            artifact_id=value.get("artifact_id", ""),
            artifact_type=value.get("artifact_type", ""),
            created_at=value.get("created_at"),
            uri=value.get("uri"),
            content_hash=value.get("content_hash"),
            metadata=value.get("metadata") or {},
        )

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "created_at": self.created_at.isoformat(),
            "uri": self.uri,
            "content_hash": self.content_hash,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SourceRecord:
    """A compact citation/provenance record, not a copy of source content."""

    source_id: str
    title: str
    uri: str = ""
    retrieved_at: datetime | None = None
    official_url: str | None = None
    url: str | None = None
    jurisdiction: str | None = None
    effective_date: date | str | None = None
    retrieval_date: date | str | None = None
    content_hash: str | None = None
    license: str | None = None
    next_review_date: date | str | None = None
    reviewer: str | None = None
    publisher: str | None = None
    published_at: datetime | None = None
    excerpt: str | None = None
    sha256: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_id(self.source_id, "source_id")
        _validate_text(self.title, "title", max_length=500)
        uri = str(self.uri or "").strip()
        official_url = str(
            self.official_url or self.url or ""
        ).strip() or None
        if not uri and official_url:
            uri = official_url
        if not uri:
            raise PolicyValidationError("uri or official_url is required")
        _validate_source_uri(uri)
        if official_url is not None:
            _validate_official_url(official_url)
        elif urlparse(uri).scheme.lower() in {"http", "https"}:
            official_url = uri
        retrieved_at = (
            _parse_datetime(self.retrieved_at, "retrieved_at")
            if self.retrieved_at is not None
            else None
        )
        retrieval_date = (
            _parse_date(self.retrieval_date, "retrieval_date")
            if self.retrieval_date is not None
            else None
        )
        if retrieved_at is None and retrieval_date is None:
            raise PolicyValidationError(
                "retrieved_at or retrieval_date is required"
            )
        if retrieved_at is None:
            retrieved_at = datetime.combine(
                retrieval_date,
                time.min,
                tzinfo=timezone.utc,
            )
        if retrieval_date is None:
            retrieval_date = retrieved_at.date()
        if retrieved_at.date() != retrieval_date:
            raise PolicyValidationError(
                "retrieved_at and retrieval_date must identify the same UTC date"
            )
        effective_date = (
            _parse_date(self.effective_date, "effective_date")
            if self.effective_date is not None
            else None
        )
        next_review_date = (
            _parse_date(self.next_review_date, "next_review_date")
            if self.next_review_date is not None
            else None
        )
        if (
            next_review_date is not None
            and next_review_date < retrieval_date
        ):
            raise PolicyValidationError(
                "next_review_date cannot precede retrieval_date"
            )
        content_hash = (
            _normalize_content_hash(self.content_hash)
            if self.content_hash is not None
            else None
        )
        sha256 = self.sha256.lower() if self.sha256 is not None else None
        if sha256 is not None and not _SHA256_RE.fullmatch(sha256):
            raise PolicyValidationError(
                "sha256 must contain exactly 64 hexadecimal characters"
            )
        if content_hash is not None:
            content_sha = content_hash.removeprefix("sha256:")
            if sha256 is not None and sha256 != content_sha:
                raise PolicyValidationError(
                    "content_hash and sha256 must describe the same content"
                )
            sha256 = content_sha
        elif sha256 is not None:
            content_hash = f"sha256:{sha256}"
        for field_name, value, max_length in (
            ("jurisdiction", self.jurisdiction, 200),
            ("license", self.license, 200),
            ("reviewer", self.reviewer, 300),
        ):
            if value is not None:
                _validate_text(value, field_name, max_length=max_length)

        object.__setattr__(self, "uri", uri)
        object.__setattr__(self, "official_url", official_url)
        object.__setattr__(self, "url", official_url)
        object.__setattr__(
            self,
            "retrieved_at",
            retrieved_at,
        )
        object.__setattr__(self, "retrieval_date", retrieval_date)
        object.__setattr__(self, "effective_date", effective_date)
        object.__setattr__(self, "next_review_date", next_review_date)
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "sha256", sha256)
        if self.published_at is not None:
            object.__setattr__(
                self,
                "published_at",
                _parse_datetime(self.published_at, "published_at"),
            )
        if self.publisher is not None:
            _validate_text(self.publisher, "publisher", max_length=300)
        if self.excerpt is not None:
            _validate_text(self.excerpt, "excerpt", max_length=4_000)
        object.__setattr__(
            self,
            "metadata",
            _validated_metadata(self.metadata),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceRecord":
        if not isinstance(value, Mapping):
            raise PolicyValidationError("SourceRecord input must be a mapping")
        return cls(
            source_id=value.get("source_id", ""),
            title=value.get("title", ""),
            uri=value.get("uri") or value.get("official_url") or value.get("url", ""),
            retrieved_at=value.get("retrieved_at"),
            official_url=(
                value.get("official_url")
                or value.get("url")
            ),
            url=value.get("url"),
            jurisdiction=value.get("jurisdiction"),
            effective_date=value.get("effective_date"),
            retrieval_date=value.get("retrieval_date"),
            content_hash=value.get("content_hash"),
            license=value.get("license"),
            next_review_date=value.get("next_review_date"),
            reviewer=value.get("reviewer"),
            publisher=value.get("publisher"),
            published_at=value.get("published_at"),
            excerpt=value.get("excerpt"),
            sha256=value.get("sha256"),
            metadata=value.get("metadata") or {},
        )

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "uri": self.uri,
            "official_url": self.official_url,
            "url": self.official_url,
            "jurisdiction": self.jurisdiction,
            "effective_date": (
                self.effective_date.isoformat() if self.effective_date else None
            ),
            "retrieval_date": self.retrieval_date.isoformat(),
            "content_hash": self.content_hash,
            "license": self.license,
            "next_review_date": (
                self.next_review_date.isoformat()
                if self.next_review_date
                else None
            ),
            "reviewer": self.reviewer,
            "retrieved_at": self.retrieved_at.isoformat(),
            "publisher": self.publisher,
            "published_at": (
                self.published_at.isoformat() if self.published_at else None
            ),
            "excerpt": self.excerpt,
            "sha256": self.sha256,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AgentRun:
    """Validated audit envelope for a single quota-reserved agent workflow.

    Raw prompts and generated output are intentionally absent.  Consumers can
    persist this envelope without turning the shared run log into a prompt or
    document-content store.
    """

    run_id: str
    workspace: WorkspaceId | str
    workflow: WorkflowClass | str
    status: AgentRunStatus | str
    created_at: datetime
    tenant: str = "freshsky"
    agent_type: str | None = None
    approved_claim_references: tuple[str, ...] = ()
    missing_inputs: tuple[str, ...] = ()
    artifacts: tuple[ArtifactRecord, ...] = ()
    required_approval: str | None = None
    updated_at: datetime | None = None
    approval_requested_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    usage_units: int | None = None
    reservation_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    provider_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_seconds: float = 0
    provider_cost_usd: Decimal | str | int | float = Decimal("0")
    sources: tuple[SourceRecord, ...] = ()
    error_code: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_id(self.run_id, "run_id")
        workspace = parse_workspace(self.workspace)
        workflow = parse_workflow(self.workflow)
        status = _parse_status(self.status)
        created_at = _parse_datetime(self.created_at, "created_at")
        _validate_id(self.tenant, "tenant")
        agent_type = self.agent_type or workflow.value
        _validate_id(agent_type, "agent_type")
        started_at = (
            _parse_datetime(self.started_at, "started_at")
            if self.started_at is not None
            else None
        )
        completed_at = (
            _parse_datetime(self.completed_at, "completed_at")
            if self.completed_at is not None
            else None
        )
        updated_at = (
            _parse_datetime(self.updated_at, "updated_at")
            if self.updated_at is not None
            else completed_at or started_at or created_at
        )
        approval_requested_at = (
            _parse_datetime(
                self.approval_requested_at,
                "approval_requested_at",
            )
            if self.approval_requested_at is not None
            else None
        )
        approved_at = (
            _parse_datetime(self.approved_at, "approved_at")
            if self.approved_at is not None
            else None
        )
        required_approval = self.required_approval or "none"
        _validate_id(required_approval, "required_approval")
        if self.approved_by is not None:
            _validate_text(self.approved_by, "approved_by", max_length=300)
        cost = decimal_usd(self.provider_cost_usd, field_name="provider_cost_usd")
        budget = workflow_budget(workflow)
        usage_units = (
            budget.usage_units if self.usage_units is None else self.usage_units
        )
        if usage_units != budget.usage_units:
            raise PolicyValidationError(
                f"usage_units must equal {budget.usage_units} for {workflow.value}"
            )
        if not isinstance(self.provider_calls, int) or isinstance(
            self.provider_calls, bool
        ):
            raise PolicyValidationError("provider_calls must be an integer")
        if (
            not isinstance(self.input_tokens, int)
            or isinstance(self.input_tokens, bool)
            or not isinstance(self.output_tokens, int)
            or isinstance(self.output_tokens, bool)
        ):
            raise PolicyValidationError("token counts must be integers")
        if not isinstance(self.elapsed_seconds, (int, float)) or isinstance(
            self.elapsed_seconds, bool
        ):
            raise PolicyValidationError("elapsed_seconds must be numeric")
        budget.validate_usage(
            provider_calls=self.provider_calls,
            total_tokens=self.input_tokens + self.output_tokens,
            elapsed_seconds=float(self.elapsed_seconds),
            provider_cost_usd=cost,
        )
        _validate_run_timing(status, created_at, started_at, completed_at)
        if self.reservation_id is not None:
            _validate_id(self.reservation_id, "reservation_id")
        if self.error_code is not None:
            _validate_id(self.error_code, "error_code")
        sources = tuple(self.sources)
        if not all(isinstance(source, SourceRecord) for source in sources):
            raise PolicyValidationError("sources must contain SourceRecord values")
        source_ids = [source.source_id for source in sources]
        if len(source_ids) != len(set(source_ids)):
            raise PolicyValidationError("source_id values must be unique per run")
        approved_claim_references = tuple(self.approved_claim_references)
        for reference in approved_claim_references:
            _validate_id(reference, "approved_claim_reference")
        if len(approved_claim_references) != len(
            set(approved_claim_references)
        ):
            raise PolicyValidationError(
                "approved_claim_references must be unique"
            )
        missing_inputs = tuple(self.missing_inputs)
        for missing_input in missing_inputs:
            _validate_text(
                missing_input,
                "missing_input",
                max_length=200,
            )
        if len(missing_inputs) != len(set(missing_inputs)):
            raise PolicyValidationError("missing_inputs must be unique")
        artifacts = tuple(self.artifacts)
        if not all(isinstance(item, ArtifactRecord) for item in artifacts):
            raise PolicyValidationError(
                "artifacts must contain ArtifactRecord values"
            )
        artifact_ids = [item.artifact_id for item in artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise PolicyValidationError(
                "artifact_id values must be unique per run"
            )
        _validate_audit_timing(
            created_at=created_at,
            updated_at=updated_at,
            approval_requested_at=approval_requested_at,
            approved_at=approved_at,
            approved_by=self.approved_by,
        )
        if status is AgentRunStatus.QUOTA_EXHAUSTED and any(
            (
                self.provider_calls,
                self.input_tokens,
                self.output_tokens,
                float(self.elapsed_seconds),
                cost,
            )
        ):
            raise PolicyValidationError(
                "quota-exhausted runs cannot contain provider usage"
            )

        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "workflow", workflow)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "agent_type", agent_type)
        object.__setattr__(self, "required_approval", required_approval)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "completed_at", completed_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(
            self,
            "approval_requested_at",
            approval_requested_at,
        )
        object.__setattr__(self, "approved_at", approved_at)
        object.__setattr__(self, "usage_units", usage_units)
        object.__setattr__(self, "provider_cost_usd", cost)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(
            self,
            "approved_claim_references",
            approved_claim_references,
        )
        object.__setattr__(self, "missing_inputs", missing_inputs)
        object.__setattr__(self, "artifacts", artifacts)
        object.__setattr__(self, "metadata", _validated_metadata(self.metadata))

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentRun":
        if not isinstance(value, Mapping):
            raise PolicyValidationError("AgentRun input must be a mapping")
        raw_sources = value.get("sources") or ()
        if not isinstance(raw_sources, (list, tuple)):
            raise PolicyValidationError("sources must be a list")
        raw_artifacts = value.get("artifacts") or ()
        if not isinstance(raw_artifacts, (list, tuple)):
            raise PolicyValidationError("artifacts must be a list")
        raw_claim_references = value.get("approved_claim_references") or ()
        if not isinstance(raw_claim_references, (list, tuple)):
            raise PolicyValidationError(
                "approved_claim_references must be a list"
            )
        raw_missing_inputs = value.get("missing_inputs") or ()
        if not isinstance(raw_missing_inputs, (list, tuple)):
            raise PolicyValidationError("missing_inputs must be a list")
        return cls(
            run_id=value.get("run_id", ""),
            workspace=value.get("workspace", ""),
            workflow=value.get("workflow", ""),
            status=value.get("status", ""),
            created_at=value.get("created_at"),
            tenant=value.get("tenant") or "freshsky",
            agent_type=value.get("agent_type"),
            approved_claim_references=tuple(raw_claim_references),
            missing_inputs=tuple(raw_missing_inputs),
            artifacts=tuple(
                item
                if isinstance(item, ArtifactRecord)
                else ArtifactRecord.from_dict(item)
                for item in raw_artifacts
            ),
            required_approval=value.get("required_approval"),
            updated_at=value.get("updated_at"),
            approval_requested_at=value.get("approval_requested_at"),
            approved_at=value.get("approved_at"),
            approved_by=value.get("approved_by"),
            usage_units=value.get("usage_units"),
            reservation_id=value.get("reservation_id"),
            started_at=value.get("started_at"),
            completed_at=value.get("completed_at"),
            provider_calls=value.get("provider_calls", 0),
            input_tokens=value.get("input_tokens", 0),
            output_tokens=value.get("output_tokens", 0),
            elapsed_seconds=value.get("elapsed_seconds", 0),
            provider_cost_usd=value.get("provider_cost_usd", "0"),
            sources=tuple(
                source
                if isinstance(source, SourceRecord)
                else SourceRecord.from_dict(source)
                for source in raw_sources
            ),
            error_code=value.get("error_code"),
            metadata=value.get("metadata") or {},
        )

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "workspace": self.workspace.value,
            "workflow": self.workflow.value,
            "status": self.status.value,
            "tenant": self.tenant,
            "agent_type": self.agent_type,
            "approved_claim_references": list(
                self.approved_claim_references
            ),
            "missing_inputs": list(self.missing_inputs),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "required_approval": self.required_approval,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "approval_requested_at": (
                self.approval_requested_at.isoformat()
                if self.approval_requested_at
                else None
            ),
            "approved_at": (
                self.approved_at.isoformat() if self.approved_at else None
            ),
            "approved_by": self.approved_by,
            "audit_timestamps": {
                "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat(),
                "started_at": (
                    self.started_at.isoformat() if self.started_at else None
                ),
                "completed_at": (
                    self.completed_at.isoformat() if self.completed_at else None
                ),
                "approval_requested_at": (
                    self.approval_requested_at.isoformat()
                    if self.approval_requested_at
                    else None
                ),
                "approved_at": (
                    self.approved_at.isoformat() if self.approved_at else None
                ),
            },
            "usage_units": self.usage_units,
            "reservation_id": self.reservation_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "provider_calls": self.provider_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "elapsed_seconds": self.elapsed_seconds,
            "provider_cost_usd": str(self.provider_cost_usd),
            "sources": [source.to_dict() for source in self.sources],
            "error_code": self.error_code,
            "metadata": dict(self.metadata),
        }


def _parse_status(value: AgentRunStatus | str) -> AgentRunStatus:
    if isinstance(value, AgentRunStatus):
        return value
    try:
        return AgentRunStatus(str(value).strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in AgentRunStatus)
        raise PolicyValidationError(
            f"unknown agent run status {value!r}; expected one of: {allowed}"
        ) from exc


def _validate_id(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PolicyValidationError(
            f"{field_name} must match {_ID_RE.pattern}"
        )


def _validate_text(value: Any, field_name: str, *, max_length: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PolicyValidationError(f"{field_name} is required")
    if len(value) > max_length:
        raise PolicyValidationError(
            f"{field_name} must not exceed {max_length} characters"
        )


def _validate_source_uri(value: Any) -> None:
    _validate_text(value, "uri", max_length=2_048)
    parsed = urlparse(value)
    if parsed.scheme.lower() not in _ALLOWED_SOURCE_SCHEMES:
        raise PolicyValidationError(
            "uri must use http, https, urn, gs, or s3"
        )
    if parsed.scheme.lower() in {"http", "https"}:
        if not parsed.netloc:
            raise PolicyValidationError("http(s) uri must include a host")
        if parsed.username or parsed.password:
            raise PolicyValidationError("source uri must not contain credentials")


def _validate_official_url(value: Any) -> None:
    _validate_text(value, "official_url", max_length=2_048)
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise PolicyValidationError(
            "official_url must be an absolute http(s) URL"
        )
    if parsed.username or parsed.password:
        raise PolicyValidationError(
            "official_url must not contain credentials"
        )


def _normalize_content_hash(value: Any) -> str:
    if not isinstance(value, str):
        raise PolicyValidationError(
            "content_hash must be a sha256 hexadecimal string"
        )
    normalized = value.strip().lower()
    digest = normalized.removeprefix("sha256:")
    if not _SHA256_RE.fullmatch(digest):
        raise PolicyValidationError(
            "content_hash must contain a SHA-256 digest"
        )
    return f"sha256:{digest}"


def _parse_datetime(value: Any, field_name: str) -> datetime:
    parsed = value
    if isinstance(value, str):
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise PolicyValidationError(
                f"{field_name} must be an ISO-8601 timestamp"
            ) from exc
    if not isinstance(parsed, datetime):
        raise PolicyValidationError(
            f"{field_name} must be a datetime or ISO-8601 timestamp"
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PolicyValidationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any, field_name: str) -> date:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise PolicyValidationError(
                f"{field_name} must be an ISO-8601 date"
            ) from exc
    if not isinstance(parsed, date) or isinstance(parsed, datetime):
        raise PolicyValidationError(
            f"{field_name} must be a date or ISO-8601 date"
        )
    return parsed


def _validated_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyValidationError("metadata must be a mapping")
    copied = dict(value)
    try:
        encoded = json.dumps(
            copied,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise PolicyValidationError("metadata must contain JSON-safe values") from exc
    if len(encoded.encode("utf-8")) > 16_384:
        raise PolicyValidationError("metadata must not exceed 16 KiB")
    return copied


def _validate_run_timing(
    status: AgentRunStatus,
    created_at: datetime,
    started_at: datetime | None,
    completed_at: datetime | None,
) -> None:
    if started_at is not None and started_at < created_at:
        raise PolicyValidationError("started_at cannot precede created_at")
    if completed_at is not None:
        lower_bound = started_at or created_at
        if completed_at < lower_bound:
            raise PolicyValidationError(
                "completed_at cannot precede the run start"
            )
    if status is AgentRunStatus.QUEUED and (
        started_at is not None or completed_at is not None
    ):
        raise PolicyValidationError("queued runs cannot have start/completion times")
    if status is AgentRunStatus.RUNNING and (
        started_at is None or completed_at is not None
    ):
        raise PolicyValidationError(
            "running runs require started_at and no completed_at"
        )
    if status in {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    } and (started_at is None or completed_at is None):
        raise PolicyValidationError(
            f"{status.value} runs require start and completion times"
        )
    if status is AgentRunStatus.QUOTA_EXHAUSTED and started_at is not None:
        raise PolicyValidationError(
            "quota-exhausted runs must fail before started_at"
        )


def _validate_audit_timing(
    *,
    created_at: datetime,
    updated_at: datetime,
    approval_requested_at: datetime | None,
    approved_at: datetime | None,
    approved_by: str | None,
) -> None:
    if updated_at < created_at:
        raise PolicyValidationError("updated_at cannot precede created_at")
    if (
        approval_requested_at is not None
        and approval_requested_at < created_at
    ):
        raise PolicyValidationError(
            "approval_requested_at cannot precede created_at"
        )
    approval_floor = approval_requested_at or created_at
    if approved_at is not None and approved_at < approval_floor:
        raise PolicyValidationError(
            "approved_at cannot precede the approval request"
        )
    if approved_at is not None and not approved_by:
        raise PolicyValidationError("approved_by is required with approved_at")
    if approved_by and approved_at is None:
        raise PolicyValidationError("approved_at is required with approved_by")
