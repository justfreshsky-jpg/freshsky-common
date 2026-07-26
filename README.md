# freshsky-common

Shared infrastructure for Fresh Sky LLC apps.

## Modules

- `llm` — Multi-provider fallback. Mistral requires a confirmed training opt-out, and OpenRouter requests both no data collection and zero retention.
- `privacy` — Fail-closed education controls. `LLMChain(privacy_profile="education_deidentified")` rejects likely student identifiers before network calls and permits only Cloudflare, Ollama, Cerebras, confirmed-ZDR Groq, and SambaNova.
- `us_public` — Portfolio-wide public profile. `LLMChain(privacy_profile="us_public")` uses the same restricted U.S. provider pool and rejects likely personal identifiers before network calls.
- `security` — Security headers + input sanitization + LLM output cleaning.
- `caching` — Bounded LRU response cache with TTL.
- `rate_limit` — Per-IP token-bucket rate limiter (Flask decorator).
- `metrics` — Thread-safe counters.
- `runtime_policy` — Canonical five-workspace IDs plus hard workflow weights
  and provider-call, token, time, and provider-cost ceilings.
- `entitlements` — Server-side plan/workspace resolution, deterministic quota
  decisions, reservation/reconciliation types, and additive
  `/api/user-status` fields.
- `agent_runtime` — Validated `AgentRun`, `SourceRecord`, and `ArtifactRecord`
  audit envelopes that intentionally omit raw prompts and generated content.
- `/metrics/providers` — Process-local provider attempts, successes, failure
  classes, fallback depth, and chain exhaustion. No prompts or responses are
  recorded.
- Rate-limit responses include `Retry-After`; routes passed through
  `no_store_paths` also receive private/no-store and noindex controls.

## Install (editable, local)

```bash
pip install -e /path/to/freshsky-common
```

## Usage

```python
from freshsky_common.llm import LLMChain
from freshsky_common.security import install_security_headers, sanitize_user_input, clean_ai_text
from freshsky_common.caching import ResponseCache
from freshsky_common.rate_limit import RateLimiter
from freshsky_common.metrics import Metrics

chain = LLMChain()
cache = ResponseCache(max_entries=500, ttl_seconds=3600)
limiter = RateLimiter(max_requests=30, window_seconds=60)
metrics = Metrics()

# In a Flask app:
install_security_headers(app)

@app.route("/ask", methods=["POST"])
@limiter.guard
def ask():
    user = sanitize_user_input(request.json.get("question", ""))
    key = ResponseCache.make_key("ask", user)
    cached = cache.get(key)
    if cached:
        return {"result": cached}
    text = chain.complete(system="You are an expert.", user=user)
    out = clean_ai_text(text)
    cache.set(key, out)
    metrics.incr("requests", "ask")
    return {"result": out}
```

Education-facing applications must use the strict profile and must not cache
user prompts or model responses:

```python
chain = LLMChain(privacy_profile="education_deidentified")
```

Other public applications use the general de-identified U.S. profile:

```python
chain = LLMChain(privacy_profile="us_public")
```

## Workspace entitlement and agent quota policy

Resolve access only from server-verified plan and identity facts:

```python
from freshsky_common.entitlements import resolve_entitlement

entitlement = resolve_entitlement(
    verified_subscription_tier,
    selected_workspace=server_saved_focus_workspace,
    email=verified_email,
    email_verified=verified_email_claim,
)
if not entitlement.can_access("education"):
    abort(403)
```

Guest preview access is one portfolio-wide three-preview pool rolling over 30
days and cannot run agent workflows or save server-side projects. Focus covers
one server-saved non-civic workspace; Civic covers CivicOps; Plus covers
Education, Action Packs, and Utilities; Advanced covers all five. The exact
verified owner identity has all five workspaces but remains finite at 500
units/day, 2,000/month, and $5/month of AI-provider cost.

Reserve the entire workflow before starting it and reconcile the maximum
provider-cost reservation to actual cost afterward:

```python
reservation = quota_ledger.reserve(
    pseudonymous_subject,
    entitlement,
    workspace="funding",
    workflow="bounded_agent",
)
actual_provider_cost_usd = "0"
try:
    result = run_bounded_agent()
    actual_provider_cost_usd = result.provider_cost_usd
finally:
    quota_ledger.reconcile(
        reservation,
        actual_provider_cost_usd=actual_provider_cost_usd,
    )
```

`InMemoryQuotaLedger` is a thread-safe reference implementation for tests and
single-process development only. Production services must use an atomic shared
ledger or the central FreshSky meter. They must fail closed when weighted
reservations are unsupported; a process-local ledger cannot enforce a
portfolio-wide allowance across Cloud Run instances.

Existing `register_freemium` consumers remain source-compatible. Passing
`workspace_id=` opts a service into workspace-aware status/access behavior, and
the returned gate accepts `check(workflow_class="standard_agent")`. The
existing `/api/user-status` fields remain while new plan, workspace, quota,
workflow-budget, and verified-owner fields are added.

`auth_broker_url=` (or `FRESHSKY_AUTH_BROKER_URL`) separates the Google OAuth
callback base from the workspace's canonical `primary_url`. Cross-host use
still requires the broker deployment to preserve/relay OAuth state and the
resulting authenticated session; changing the callback URL alone does not
create a cross-host session handoff.

`SourceRecord` has explicit official URL, jurisdiction, effective/retrieval
dates, SHA-256 content hash, license, next-review date, and reviewer fields
(while retaining `uri`, `retrieved_at`, and `sha256` aliases). `AgentRun`
explicitly records tenant, agent type, independent approved claim references,
missing inputs, artifact references, required approval, approver, and audit
timestamps. These are validated fields rather than conventions hidden in
`metadata`.

Set `GROQ_ZDR_CONFIRMED=true` only after enabling Zero Data Retention on the
Groq account. Set `MISTRAL_TRAINING_OPTOUT_CONFIRMED=true` only after disabling
anonymous improvement data; Mistral remains excluded from the education profile.

## Adoption

The library is already shared across the Fresh Sky hub, foundation apps, civic apps, and active batch apps. Keep app-specific operations in the consuming repos; this package should stay focused on runtime helpers.
