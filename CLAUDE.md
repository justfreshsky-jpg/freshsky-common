# freshsky-common — shared Python lib

Pip-installable from git (`freshsky-common @ git+https://github.com/justfreshsky-jpg/freshsky-common.git@main`). Used by every Fresh Sky AI app (hub, foundation, all 32 batch apps). Changes here ripple to the whole portfolio on next Cloud Run deploy.

## Modules
- **`security.py`** — `install_security_headers(app)`: CSP, HSTS, X-Frame-Options, Referrer-Policy, etc.
- **`rate_limit.py`** — simple in-memory rate limiter (per-IP).
- **`llm.py`** — unified 5-provider fallback chain (Groq → Cerebras → Gemini → Mistral → OpenRouter → HuggingFace). Reads keys from env.
- **`caching.py`** — memoization helpers for prompt + response caching.
- **`metrics.py`** — thread-safe in-memory counters (per-app, non-persistent).
- **`revenue.py`** — SEO (robots, sitemap, humans), affiliate cards, GA4 + OG + schema.org context processor, category-gated for HULEC.

## `revenue.install(app, slug, brand, primary_url, category, description='')`
One-call app wiring. Registers:
- SEO routes (`/robots.txt`, `/sitemap.xml`, `/humans.txt`)
- Revenue routes (`/api/affiliates`)
- Jinja context processor injecting into every `render_template`:
  - `{{ ga4_snippet|safe }}` — GA4 tag (activates when `GA_MEASUREMENT_ID` env var set)
  - `{{ og_tags|safe }}` — Open Graph + Twitter card
  - `{{ schema_json|safe }}` — schema.org JSON-LD (auto-typed from `category`)
  - `app_slug`, `app_brand` — template globals

HULEC categories: `legal · benefits · civic · housing · healthcare · education · newcomer · financial · business · flagship`

## Conventions
- **Env var name for GA4 is `GA_MEASUREMENT_ID`** (with `GA4_MEASUREMENT_ID` as legacy fallback). Matches what the 4 foundation apps already read directly.
- **Don't add ops/infra helpers here** — runtime lib only. Ops scripts belong in notes/memory.
- **Python 3.10+** target (PEP 604 `A | B` types are used in some modules).
