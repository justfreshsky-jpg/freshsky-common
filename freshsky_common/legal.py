"""Privacy + Terms boilerplate routes for Fresh Sky AI apps.

Every app that calls revenue.install(...) gets /privacy and /terms
auto-registered with conservative fallback copy, an AI-content disclaimer,
and a contact pointer to the hub. Application-specific practices need review.
"""
from __future__ import annotations

import datetime
import html as _html

from flask import Flask, Response, redirect
from .public_identity import HUB_HOSTS, public_origin


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

<h2>Inputs and AI processing</h2>
<p>Use only public, synthetic or verified non-sensitive information. Do not enter student records, medical records, account numbers, private documents or other sensitive information. Automated checks cannot reliably remove all identifying information.</p>
<p>AI text processing, when enabled, uses Groq only after a current account and privacy review. The provider receives the submitted text and system instructions. Its privacy, retention and abuse-prevention terms apply. If the approved service is unavailable, the tool reports that result; available deterministic and manual functions can still be used.</p>

<h2>Accounts and operational records</h2>
<p>Enabled account features may use cookies and retain account, access, usage, billing or support records. Hosting systems may record request metadata for operation and security. Do not assume that closing the page deletes these records. Application-specific notices describe additional storage and optional services.</p>

<h2>Third parties</h2>
<p>Fresh Sky uses service providers for hosting, domain services and enabled account or payment features. A tool may link to third-party sites, whose own terms apply. We do not claim that anonymous analytics or zero retention applies to every record or third party.</p>

<h2>Children</h2>
<p>These tools are intended for adults. Do not submit children's personal information. Contact us if you believe such information was submitted.</p>

<h2>Questions and requests</h2>
<p>For privacy, correction, export or deletion requests, contact <a href="{hub}/contact">Fresh Sky LLC</a>. The applicable records, legal obligations and technical limits determine what can be provided or removed.</p>

<h2>Changes</h2>
<p>If this policy changes meaningfully, we'll update the date at the top. We'll never quietly start collecting more.</p>

<p style="margin-top:3rem;color:#94a3b8;font-size:.8rem;">© {year} Fresh Sky LLC · <a href="/terms">Terms of Use</a> · <a href="{hub}/#products">Plans</a> · <a href="{hub}">freshskyllc.org</a></p>
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
<p>{brand} is an AI-assisted educational tool from Fresh Sky LLC. AI assistance may be unavailable. Deterministic functions and any enabled AI features are described by the tool. It is <strong>not legal, tax, medical, or professional advice</strong>. It will sometimes be wrong. Verify anything important with the actual agency, an attorney, or a licensed professional before acting on it.</p>

<h2>Permitted use</h2>
<p>Use the tool only for lawful activities, with the necessary rights and permissions. In particular:</p>
<ul>
  <li>Don't try to break it, scrape it at scale, or use it to abuse the AI providers behind it.</li>
  <li>Don't input data that isn't yours (someone else's medical records, private communications, etc.).</li>
  <li>Don't use it for illegal activity, fraud, fabricated evidence, impersonation or unauthorized access.</li>
</ul>

<h2>The output is yours</h2>
<p>We do not claim ownership of your inputs or generated output. Your use must respect applicable law, third-party rights and provider terms. Output may be inaccurate or resemble existing material; review it before sharing or using it.</p>

<h2>No guarantees</h2>
<p>The tool is provided "as is." We make no warranty about uptime, accuracy, completeness, or fitness for any particular purpose. If the tool is wrong, breaks, or is unavailable when you need it, we're not liable for the consequences. To the maximum extent allowed by law, our total liability for free tool access is capped at zero.</p>

<h2>Cost</h2>
<p>Access and existing entitlements depend on the relevant tool and account. New purchases are not available unless explicitly enabled and displayed. Automation services are quoted after workflow review. Availability, provider quotas and anti-abuse controls may restrict AI features; paying for an entitlement does not bypass these controls.</p>

<h2>Changes</h2>
<p>We may update these terms. Continuing to use the tool after a change means you accept the new version. The date at the top tells you when the current version was published.</p>

<h2>Contact</h2>
<p>Questions, concerns, complaints, kudos: <a href="{hub}/contact">freshskyllc.org/contact</a>.</p>

<p style="margin-top:3rem;color:#94a3b8;font-size:.8rem;">© {year} Fresh Sky LLC · <a href="/privacy">Privacy Policy</a> · <a href="{hub}/#products">Plans</a> · <a href="{hub}">freshskyllc.org</a></p>
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
    public_origin()  # Validate configuration at startup.
    # Hub is ONLY the apex/www host — NOT <app>.freshskyai.com sub-apps
    # (which also end in 'freshskyai.com'). Parse the host precisely.
    from urllib.parse import urlparse
    _host = (urlparse(primary_url if '//' in primary_url else f'https://{primary_url}')
             .hostname or '').lower()
    _is_hub = _host in HUB_HOSTS

    if not any(r.rule == '/privacy' for r in app.url_map.iter_rules()):
        if _is_hub:
            @app.route('/privacy')
            def _privacy():
                return Response(
                    _PRIVACY_TEMPLATE.format(brand=safe_brand, primary_url=safe_url, today=today, year=year, hub=_html.escape(public_origin())),
                    mimetype='text/html',
                )
        else:
            @app.route('/privacy')
            def _privacy():
                return redirect(f'{public_origin()}/privacy', code=301)

    if not any(r.rule == '/terms' for r in app.url_map.iter_rules()):
        if _is_hub:
            @app.route('/terms')
            def _terms():
                return Response(
                    _TERMS_TEMPLATE.format(brand=safe_brand, primary_url=safe_url, today=today, year=year, hub=_html.escape(public_origin())),
                    mimetype='text/html',
                )
        else:
            @app.route('/terms')
            def _terms():
                return redirect(f'{public_origin()}/terms', code=301)
