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

Set `GROQ_ZDR_CONFIRMED=true` only after enabling Zero Data Retention on the
Groq account. Set `MISTRAL_TRAINING_OPTOUT_CONFIRMED=true` only after disabling
anonymous improvement data; Mistral remains excluded from the education profile.

## Adoption

The library is already shared across the Fresh Sky hub, foundation apps, civic apps, and active batch apps. Keep app-specific operations in the consuming repos; this package should stay focused on runtime helpers.
