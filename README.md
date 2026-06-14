# freshsky-common

Shared infrastructure for Fresh Sky LLC apps.

## Modules

- `llm` — Multi-provider LLM fallback chain: Groq, Cerebras, Mistral, SambaNova, Cloudflare Workers AI, Ollama Cloud, OpenRouter, and Hugging Face. Providers that lack free commercial-use terms or require card-backed billing are excluded, as is Gemini's free tier because free-tier prompts may be used to improve Google products.
- `security` — Security headers + input sanitization + LLM output cleaning.
- `caching` — Bounded LRU response cache with TTL.
- `rate_limit` — Per-IP token-bucket rate limiter (Flask decorator).
- `metrics` — Thread-safe counters.
- `/metrics/providers` — Process-local provider attempts, successes, failure
  classes, fallback depth, and chain exhaustion. No prompts or responses are
  recorded.

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

## Adoption

The library is already shared across the Fresh Sky hub, foundation apps, civic apps, and active batch apps. Keep app-specific operations in the consuming repos; this package should stay focused on runtime helpers.
