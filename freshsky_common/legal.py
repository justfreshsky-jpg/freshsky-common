"""Privacy + Terms boilerplate routes for Fresh Sky AI apps.

Every app that calls revenue.install(...) gets /privacy and /terms
auto-registered with sensible defaults: no PII storage claim, AI-content
disclaimer, contact pointer to the hub, last-updated stamp.
"""
from __future__ import annotations

import datetime
import html as _html

from flask import Flask, Response, redirect


_PRIVACY_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><title>Privacy — {brand}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Privacy policy for {brand}, part of Fresh Sky AI.">
<meta name="robots" content="index,follow">
<link rel="canonical" href="{primary_url}privacy">
<style>
  body{{font-family:system-ui,-apple-system,sans-serif;max-width:720px;margin:0 auto;padding:32px 20px;line-height:1.6;color:#1e293b;background:#f8fafc;}}
  h1{{font-size:1.6rem;margin-bottom:.4rem;}}
  h2{{font-size:1.1rem;margin-top:1.6rem;color:#334155;}}
  a{{color:#2563eb;}}
  .meta{{color:#64748b;font-size:.85rem;margin-bottom:2rem;}}
  .nav{{margin-bottom:2rem;}}
</style></head><body>
<div class="nav"><a href="/">← Back to {brand}</a></div>
<h1>Privacy Policy</h1>
<p class="meta">Last updated {today}. Applies to {brand} ({primary_url}).</p>

<h2>What we collect</h2>
<p>We try hard to collect as little as possible. Specifically:</p>
<ul>
  <li><strong>What you type into the tool.</strong> Your input is sent to AI providers (Groq, Cerebras, Mistral, OpenRouter, Hugging Face, or other configured fallback providers) so they can generate a response. We do not store this input on our servers, and we do not associate it with you.</li>
  <li><strong>A session cookie.</strong> Used only to remember preferences and sign-in state. It expires when you close your browser.</li>
  <li><strong>Standard server logs.</strong> Cloud Run records request paths, status codes, IP address, and timestamps for security and debugging. These rotate automatically and are never tied to your input or output.</li>
  <li><strong>Anonymous analytics.</strong> Google Analytics 4 tracks page views and basic interactions to help us understand which tools are useful. IPs are anonymized; we do not store any personally identifiable information.</li>
</ul>

<h2>What we don't collect</h2>
<ul>
  <li>No account or sign-in is required for basic use.</li>
  <li>No name, address, phone, SSN, or government ID — never ask for them; the AI is told not to need them.</li>
  <li>No tracking pixels, no advertising cookies, no data sharing with advertisers.</li>
  <li>No record of what you typed or what the AI replied — once the response is sent back to you, it's gone from our side.</li>
</ul>

<h2>What the AI sees</h2>
<p>The AI providers above receive your input to generate a reply. Each has its own privacy, retention, and abuse-prevention terms. Do not enter Social Security numbers, account numbers, student records, medical records, or other sensitive personal information.</p>

<h2>Third parties</h2>
<ul>
  <li><strong>Hosting:</strong> Google Cloud Run (US data centers).</li>
  <li><strong>DNS:</strong> Namecheap.</li>
  <li><strong>Analytics:</strong> Google Analytics 4 (anonymized).</li>
  <li><strong>Affiliate links:</strong> when present, are clearly disclosed and do not set tracking cookies until you click.</li>
</ul>

<h2>Children</h2>
<p>This tool is intended for adults (or the parents/guardians of children). We do not knowingly collect data from anyone under 13. If you believe a child has used the tool, contact us and we'll delete what we can.</p>

<h2>Your rights</h2>
<p>Because we don't collect personal information, there's nothing to delete or export. If you have a question about a specific request you made, or you believe we've made a mistake, contact us via the hub: <a href="https://www.freshskyai.com/contact">freshskyai.com/contact</a>.</p>

<h2>Changes</h2>
<p>If this policy changes meaningfully, we'll update the date at the top. We'll never quietly start collecting more.</p>

<p style="margin-top:3rem;color:#94a3b8;font-size:.8rem;">© {year} Fresh Sky LLC · <a href="/terms">Terms of Use</a> · <a href="https://www.freshskyai.com/donate">Donate</a> · <a href="https://www.freshskyai.com">freshskyai.com</a></p>
</body></html>"""


_TERMS_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><title>Terms — {brand}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Terms of use for {brand}, part of Fresh Sky AI.">
<meta name="robots" content="index,follow">
<link rel="canonical" href="{primary_url}terms">
<style>
  body{{font-family:system-ui,-apple-system,sans-serif;max-width:720px;margin:0 auto;padding:32px 20px;line-height:1.6;color:#1e293b;background:#f8fafc;}}
  h1{{font-size:1.6rem;margin-bottom:.4rem;}}
  h2{{font-size:1.1rem;margin-top:1.6rem;color:#334155;}}
  a{{color:#2563eb;}}
  .meta{{color:#64748b;font-size:.85rem;margin-bottom:2rem;}}
  .nav{{margin-bottom:2rem;}}
</style></head><body>
<div class="nav"><a href="/">← Back to {brand}</a></div>
<h1>Terms of Use</h1>
<p class="meta">Last updated {today}. Applies to {brand} ({primary_url}).</p>

<h2>What this is</h2>
<p>{brand} is an AI-assisted educational tool from Fresh Sky LLC. It generates plain-language answers based on what you type in. It is <strong>not legal, tax, medical, or professional advice</strong>. It will sometimes be wrong. Verify anything important with the actual agency, an attorney, or a licensed professional before acting on it.</p>

<h2>Use it however you want, within reason</h2>
<p>You can use this tool for personal, educational, or commercial purposes — no permission needed. What we ask:</p>
<ul>
  <li>Don't try to break it, scrape it at scale, or use it to abuse the AI providers behind it.</li>
  <li>Don't input data that isn't yours (someone else's medical records, private communications, etc.).</li>
  <li>Don't use it to generate content that's illegal where you are.</li>
</ul>

<h2>The output is yours</h2>
<p>Whatever the AI generates in response to your input, you can use, modify, or share. We don't claim copyright over generated content. We can't promise it's accurate or that it doesn't accidentally resemble copyrighted material — review it before publishing or filing it anywhere.</p>

<h2>No guarantees</h2>
<p>The tool is provided "as is." We make no warranty about uptime, accuracy, completeness, or fitness for any particular purpose. If the tool is wrong, breaks, or is unavailable when you need it, we're not liable for the consequences. To the maximum extent allowed by law, our total liability for free tool access is capped at zero.</p>

<h2>Cost</h2>
<p>Fresh Sky AI tools are available without an access fee. Optional donations help cover infrastructure but do not purchase access, priority, or guaranteed availability. Provider safety controls and anti-abuse protections may still temporarily restrict harmful or automated use. Fresh Sky LLC is not a charity, and payments are not tax-deductible charitable donations.</p>

<h2>Changes</h2>
<p>We may update these terms. Continuing to use the tool after a change means you accept the new version. The date at the top tells you when the current version was published.</p>

<h2>Contact</h2>
<p>Questions, concerns, complaints, kudos: <a href="https://www.freshskyai.com/contact">freshskyai.com/contact</a>.</p>

<p style="margin-top:3rem;color:#94a3b8;font-size:.8rem;">© {year} Fresh Sky LLC · <a href="/privacy">Privacy Policy</a> · <a href="https://www.freshskyai.com/donate">Donate</a> · <a href="https://www.freshskyai.com">freshskyai.com</a></p>
</body></html>"""


def register_legal_routes(app: Flask, *, brand: str, primary_url: str) -> None:
    """Add /privacy and /terms routes that render Fresh Sky AI's standard
    boilerplate, branded with the calling app's name and URL.

    Idempotent — silently skips if the routes are already registered (e.g.
    if the app declares its own /privacy)."""
    today = datetime.date.today().isoformat()
    year = datetime.date.today().year
    safe_brand = _html.escape(brand)
    safe_url = _html.escape(primary_url)

    # Compaction (operator directive): sub-apps must not each render their own
    # full legal boilerplate. They forward to the hub's canonical pages — one
    # source of truth, leaner apps, zero extra cost (HULEC). The hub itself
    # serves /privacy + /terms from its own templates and registers them
    # before this runs, so the idempotency guard below skips it (and the
    # explicit _is_hub check is belt-and-suspenders against a redirect loop).
    _HUB = 'https://www.freshskyai.com'
    # Hub is ONLY the apex/www host — NOT <app>.freshskyai.com sub-apps
    # (which also end in 'freshskyai.com'). Parse the host precisely.
    from urllib.parse import urlparse
    _host = (urlparse(primary_url if '//' in primary_url else f'https://{primary_url}')
             .hostname or '').lower()
    _is_hub = _host in ('www.freshskyai.com', 'freshskyai.com')

    if not any(r.rule == '/privacy' for r in app.url_map.iter_rules()):
        if _is_hub:
            @app.route('/privacy')
            def _privacy():
                return Response(
                    _PRIVACY_TEMPLATE.format(brand=safe_brand, primary_url=safe_url, today=today, year=year),
                    mimetype='text/html',
                )
        else:
            @app.route('/privacy')
            def _privacy():
                return redirect(f'{_HUB}/privacy', code=301)

    if not any(r.rule == '/terms' for r in app.url_map.iter_rules()):
        if _is_hub:
            @app.route('/terms')
            def _terms():
                return Response(
                    _TERMS_TEMPLATE.format(brand=safe_brand, primary_url=safe_url, today=today, year=year),
                    mimetype='text/html',
                )
        else:
            @app.route('/terms')
            def _terms():
                return redirect(f'{_HUB}/terms', code=301)
