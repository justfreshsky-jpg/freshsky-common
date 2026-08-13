"""Canonical FreshSky workspace and agent-workflow runtime policy.

This module is deliberately free of Flask and storage dependencies.  Apps can
use the same enums and immutable budgets at request validation, orchestration,
and persistence boundaries without duplicating policy values.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Final, Mapping


class PolicyValidationError(ValueError):
    """Raised when an unknown workspace/workflow or invalid usage is supplied."""


class WorkspaceId(str, Enum):
    FUNDING = "funding"
    EDUCATION = "education"
    CIVIC = "civic"
    ACTION_PACKS = "action_packs"
    UTILITIES = "utilities"


WORKSPACE_LABELS: Final[Mapping[WorkspaceId, str]] = MappingProxyType(
    {
        WorkspaceId.FUNDING: "FreshSky Funding Desk",
        WorkspaceId.EDUCATION: "EduSafe Studio",
        WorkspaceId.CIVIC: "FreshSky CivicOps",
        WorkspaceId.ACTION_PACKS: "FreshSky Action Packs",
        WorkspaceId.UTILITIES: "FreshSky Private Utility Lab",
    }
)
ALL_WORKSPACES: Final[frozenset[WorkspaceId]] = frozenset(WorkspaceId)
NON_CIVIC_WORKSPACES: Final[frozenset[WorkspaceId]] = frozenset(
    workspace for workspace in WorkspaceId if workspace is not WorkspaceId.CIVIC
)


class WorkflowClass(str, Enum):
    PREVIEW = "preview"
    STANDARD_AGENT = "standard_agent"
    BOUNDED_AGENT = "bounded_agent"
    FULL_FUNDING_SCAN = "full_funding_scan"


def decimal_usd(
    value: Decimal | str | int | float,
    *,
    field_name: str,
) -> Decimal:
    """Parse a non-negative finite USD amount without float arithmetic."""
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise PolicyValidationError(f"{field_name} must be a USD number") from exc
    if not parsed.is_finite() or parsed < 0:
        raise PolicyValidationError(f"{field_name} must be non-negative and finite")
    return parsed


@dataclass(frozen=True)
class WorkflowBudget:
    """Hard reservation and execution ceiling for one workflow class."""

    usage_units: int
    max_provider_calls: int
    max_total_tokens: int
    max_elapsed_seconds: int
    max_provider_cost_usd: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            "usage_units",
            "max_provider_calls",
            "max_total_tokens",
            "max_elapsed_seconds",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                raise PolicyValidationError(
                    f"{field_name} must be a positive integer"
                )
        parsed_cost = decimal_usd(
            self.max_provider_cost_usd,
            field_name="max_provider_cost_usd",
        )
        if parsed_cost <= 0:
            raise PolicyValidationError("max_provider_cost_usd must be positive")
        object.__setattr__(self, "max_provider_cost_usd", parsed_cost)

    @property
    def max_fan_out(self) -> int:
        """Compatibility name for orchestration code that calls fan-out calls."""
        return self.max_provider_calls

    def as_dict(self) -> dict:
        return {
            "usage_units": self.usage_units,
            "max_provider_calls": self.max_provider_calls,
            "max_fan_out": self.max_fan_out,
            "max_total_tokens": self.max_total_tokens,
            "max_elapsed_seconds": self.max_elapsed_seconds,
            "max_provider_cost_usd": str(self.max_provider_cost_usd),
        }

    def validate_usage(
        self,
        *,
        provider_calls: int,
        total_tokens: int,
        elapsed_seconds: float,
        provider_cost_usd: Decimal | str | int | float,
    ) -> None:
        """Reject a completed/in-progress run that crossed a hard ceiling."""
        if (
            not isinstance(provider_calls, int)
            or isinstance(provider_calls, bool)
            or provider_calls < 0
            or provider_calls > self.max_provider_calls
        ):
            raise PolicyValidationError(
                f"provider_calls exceeds {self.max_provider_calls}"
            )
        if (
            not isinstance(total_tokens, int)
            or isinstance(total_tokens, bool)
            or total_tokens < 0
            or total_tokens > self.max_total_tokens
        ):
            raise PolicyValidationError(
                f"total_tokens exceeds {self.max_total_tokens}"
            )
        if (
            not isinstance(elapsed_seconds, (int, float))
            or isinstance(elapsed_seconds, bool)
            or not isfinite(float(elapsed_seconds))
            or elapsed_seconds < 0
            or elapsed_seconds > self.max_elapsed_seconds
        ):
            raise PolicyValidationError(
                f"elapsed_seconds exceeds {self.max_elapsed_seconds}"
            )
        cost = decimal_usd(provider_cost_usd, field_name="provider_cost_usd")
        if cost > self.max_provider_cost_usd:
            raise PolicyValidationError(
                f"provider_cost_usd exceeds {self.max_provider_cost_usd}"
            )


WORKFLOW_BUDGETS: Final[Mapping[WorkflowClass, WorkflowBudget]] = MappingProxyType(
    {
        WorkflowClass.PREVIEW: WorkflowBudget(
            usage_units=1,
            max_provider_calls=1,
            max_total_tokens=4_000,
            max_elapsed_seconds=30,
            max_provider_cost_usd=Decimal("0.005"),
        ),
        WorkflowClass.STANDARD_AGENT: WorkflowBudget(
            usage_units=5,
            max_provider_calls=4,
            max_total_tokens=12_000,
            max_elapsed_seconds=45,
            max_provider_cost_usd=Decimal("0.025"),
        ),
        WorkflowClass.BOUNDED_AGENT: WorkflowBudget(
            usage_units=10,
            max_provider_calls=8,
            max_total_tokens=24_000,
            max_elapsed_seconds=90,
            max_provider_cost_usd=Decimal("0.05"),
        ),
        WorkflowClass.FULL_FUNDING_SCAN: WorkflowBudget(
            usage_units=20,
            max_provider_calls=12,
            max_total_tokens=40_000,
            max_elapsed_seconds=120,
            max_provider_cost_usd=Decimal("0.10"),
        ),
    }
)


def parse_workspace(value: WorkspaceId | str) -> WorkspaceId:
    if isinstance(value, WorkspaceId):
        return value
    try:
        return WorkspaceId(str(value).strip().lower())
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in WorkspaceId)
        raise PolicyValidationError(
            f"unknown workspace {value!r}; expected one of: {allowed}"
        ) from exc


def parse_workflow(value: WorkflowClass | str) -> WorkflowClass:
    if isinstance(value, WorkflowClass):
        return value
    try:
        return WorkflowClass(str(value).strip().lower())
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in WorkflowClass)
        raise PolicyValidationError(
            f"unknown workflow {value!r}; expected one of: {allowed}"
        ) from exc


def workflow_budget(value: WorkflowClass | str) -> WorkflowBudget:
    return WORKFLOW_BUDGETS[parse_workflow(value)]
