"""freshsky_common — shared infrastructure for Fresh Sky LLC apps.

Modules:
    llm        — Multi-provider LLM fallback chain; no direct Gemini provider.
    auth       — Google OAuth helpers.
    freemium   — Free-access UI, OAuth, donation billing, and email capture.
    security   — Security headers + sanitization helpers.
    caching    — Simple in-memory response cache.
    rate_limit — Token-bucket / per-IP rate limiter.
    metrics    — Lightweight thread-safe metrics counter.
    revenue    — Affiliate cards + GA4 + SEO routes (sitemap.xml, robots.txt).
"""

__version__ = "0.4.0"
