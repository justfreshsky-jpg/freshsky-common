"""freshsky_common — shared infrastructure for Fresh Sky LLC apps.

Modules:
    llm        — Multi-provider LLM fallback chain (Groq, Cerebras, Gemini, Mistral, OpenRouter, HF).
    auth       — Google OAuth helpers.
    billing    — Stripe subscription helpers.
    security   — Security headers + sanitization helpers.
    caching    — Simple in-memory response cache.
    rate_limit — Token-bucket / per-IP rate limiter.
    metrics    — Lightweight thread-safe metrics counter.
"""

__version__ = "0.1.0"
