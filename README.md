# freshsky-common

Shared infrastructure for Fresh Sky LLC apps.

## Modules

- `llm` — Free-only text inference. Unverified accounts, paid providers, quota
  exhaustion and unsupported requests return unavailable; there is no paid fallback.
- `ai_cost_policy` — Exact endpoint/model, current account evidence, credential
  binding, input and output bounds, and privacy checks before network I/O.
- `privacy` — Additional identifier checks for public and education inputs.
  These checks do not establish that arbitrary input is non-sensitive.
- `security` — Security headers + input sanitization + LLM output cleaning.
- `caching` — Bounded LRU response cache with TTL.
- `rate_limit` — Per-IP token-bucket rate limiter (Flask decorator).
- `metrics` — Thread-safe counters.
- `runtime_policy` — Canonical five-workspace IDs plus hard workflow weights
  and provider-call, token, time, and provider-cost ceilings.
- `entitlements` — Server-side plan/workspace resolution, deterministic quota
  decisions, reservation/reconciliation types, and additive
  `/api/user-status` fields.
- `checkout_store` — Transactional, pseudonymous pending Stripe Checkout
  reservations that prevent concurrent or long-lived duplicate subscription
  sessions without storing email addresses or Checkout URLs.
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
    if not text:
        return {"code": "ai_unavailable", "no_usage_charge": True}, 503
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
units/day and 2,000/month. The legacy $5/month provider ceiling remains a
historical entitlement backstop, not spending authority: every current workflow
has a zero-dollar provider-cost reservation and positive charges are rejected.

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

Subscription checkout now reserves one pseudonymous identity for the complete
23-hour Stripe Session lifetime. Managed runtimes require the Firestore
backend and fail closed if it is unavailable; `MemoryCheckoutStore` is for
tests and local development only. Concurrent retries reuse the reservation's
stable Stripe idempotency key. The reservation fingerprint binds the exact
application host, tier, workspace, and server-selected Price ID, while the
stored record contains no raw email address, Checkout URL, or credential.

Managed services should bind the least-privilege live Stripe key through
`STRIPE_RUNTIME_RESTRICTED_KEY`. When present, it must be an unpadded
`rk_live_` value and takes precedence over the legacy argument. A managed
runtime fails closed if `STRIPE_SECRET_KEY` is also present or if an explicit
different key conflicts. Services that have not migrated continue to use the
legacy key as a compatibility fallback; remove that fallback from each
revision only after its restricted-key permissions and request log have been
verified.

`SourceRecord` has explicit official URL, jurisdiction, effective/retrieval
dates, SHA-256 content hash, license, next-review date, and reviewer fields
(while retaining `uri`, `retrieved_at`, and `sha256` aliases). `AgentRun`
explicitly records tenant, agent type, independent approved claim references,
missing inputs, artifact references, required approval, approver, and audit
timestamps. These are validated fields rather than conventions hidden in
`metadata`.

## Free-only inference (0.8.0)

No provider is enabled by default. Existing keys, old provider lists, privacy
flags or a free model name alone do not authorize calls. Only the exact Groq
text endpoint and reviewed `openai/gpt-oss-20b` / `openai/gpt-oss-120b` models
can pass the current gate. Other adapter implementations remain compatibility
code and are blocked before network I/O, including Vertex credential refresh.
Redirects and automatic paid fallback are disabled. A chain makes at most one
approved Groq attempt. Quota exhaustion or an empty response is unavailable,
not a completed AI task; keep deterministic/manual functions available.

Before enabling inference, verify the actual account is on the free plan with
no billable overage, paid credits or automatic upgrade, review commercial and
privacy terms, and enable Zero Data Retention. Set `GROQ_ZDR_CONFIRMED=true`
only after verified account readback. Provide `GROQ_API_KEY` through the existing
secret mechanism and mount a private JSON record outside source control using
`FRESHSKY_FREE_AI_VERIFICATION_FILE` (absolute path). Its fields are:

- `schema_version`: integer 1; `provider`: `groq`; `plan`: `free`.
- `account_id`: the verified provider account identifier.
- `no_billable_overage`, `commercial_use_reviewed`, `privacy_reviewed`,
  `non_sensitive_only`: true; `automatic_upgrade`, `paid_credits`: false.
- `credential_sha256`: digest of the exact configured key, never the key itself.
- `evidence_sha256`: digest of the private dated verification evidence.
- `verified_at`, `expires_at`: timezone-aware timestamps, at most 24 hours apart.
- `models`: a nonempty subset of the two reviewed model IDs above.

This record is an operator attestation, not a provider billing guarantee.
Do not auto-renew it or infer its contents from old evidence. Missing, expired,
malformed, rotated-key or incompatible records fail closed. Never commit a real
record, credential, account export or customer input. Unsupported values of
`FRESHSKY_AI_COST_POLICY` also fail closed; there is no paid-mode override.

Only public, synthetic or independently verified non-sensitive text is eligible:
up to 12,000 combined input characters, 2,000 output tokens, and no native tool
calls, files, images or remote execution. Pattern-based identifier rejection is
an additional safeguard, not anonymization or permission to send private data.
Provider terms and free quotas may change; account evidence must remain current.

These controls cover calls through this package. Consumers must remove direct
SDK/HTTP bypasses and pin the reviewed commit. Source publication alone does not
change running services. Existing records, exports and entitlements remain;
`AgentRun` can still validate bounded historical cost records without authorizing
new spending. Existing hosting/domain/email costs are separate from inference.


## Adoption

The library is already shared across the Fresh Sky hub, foundation apps, civic apps, and active batch apps. Keep app-specific operations in the consuming repos; this package should stay focused on runtime helpers.

## Public domain consolidation (0.7.1)

New shared public links, planner/CSP, central usage metering and billing redirects
default to `https://freshskyllc.org`. This does not rename owner logins, OAuth
clients, store packages, service IDs, or stored entitlements. Consumers must verify
`.org` routing, OAuth and webhook configuration before adopting the new version.
For a bounded deployment rollback only, both `FRESHSKY_PUBLIC_ORIGIN=https://www.freshskyai.com`
and `FRESHSKY_LEGACY_DOMAIN_COMPATIBILITY=true` select the previous central origin.
Other origin overrides fail closed. Exact legacy hub hosts remain recognized to
avoid redirect loops; no wildcard or new cross-origin access is introduced.

The access UI uses `/freshsky-access-v062.js?version=071` so the preceding
baseline still serves it during split traffic; `/freshsky-access-v071.js` is an
alias. All new-revision script responses are `no-store` because their public
origin can differ by request host. An old baseline response may retain legacy
public links in the old-origin cache during the hold; do not route the new .org
origin to an old revision. The unversioned v062 route remains available
for older templates and the response hook upgrades old script references. This
source change is not a deployment or package publication.

During a reviewed legacy hold, that same explicit pair uses canonical public
links only when the actual Flask request host is exactly `freshskyllc.org` or
`www.freshskyllc.org`. Canary/provider/legacy hosts and background work continue
to use the legacy destination. `X-Forwarded-Host` is not read by this package.
This permits a DNS cutover without changing the reviewed environment; it does
not verify DNS or change authentication, callbacks, account ownership or audiences.
