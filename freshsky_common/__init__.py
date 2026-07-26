"""freshsky_common — shared infrastructure for Fresh Sky LLC apps.

Modules:
    llm        — Multi-provider LLM fallback chain; no direct Gemini provider.
    auth       — Google OAuth helpers.
    freemium   — Preview access, OAuth, subscription billing, and email capture.
    entitlements — Workspace access and deterministic usage-unit quotas.
    agent_runtime — Validated AgentRun and SourceRecord audit envelopes.
    security   — Security headers + sanitization helpers.
    caching    — Simple in-memory response cache.
    rate_limit — Token-bucket / per-IP rate limiter.
    metrics    — Lightweight thread-safe metrics counter.
    revenue    — GA4 + SEO routes (sitemap.xml, robots.txt) and portfolio links.
"""

from .agent_runtime import AgentRun, AgentRunStatus, ArtifactRecord, SourceRecord
from .entitlements import (
    InMemoryQuotaLedger,
    PlanEntitlement,
    PlanTier,
    QuotaDecision,
    QuotaExhausted,
    QuotaReservation,
    UsageSnapshot,
    evaluate_quota,
    resolve_entitlement,
    user_status_fields,
)
from .runtime_policy import (
    WORKFLOW_BUDGETS,
    WorkflowBudget,
    WorkflowClass,
    WorkspaceId,
)


__version__ = "0.6.1"

__all__ = [
    "AgentRun",
    "AgentRunStatus",
    "ArtifactRecord",
    "InMemoryQuotaLedger",
    "PlanEntitlement",
    "PlanTier",
    "QuotaDecision",
    "QuotaExhausted",
    "QuotaReservation",
    "SourceRecord",
    "UsageSnapshot",
    "WORKFLOW_BUDGETS",
    "WorkflowBudget",
    "WorkflowClass",
    "WorkspaceId",
    "evaluate_quota",
    "resolve_entitlement",
    "user_status_fields",
]
