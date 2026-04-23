"""
Revenue + analytics + SEO helpers shared across all Fresh Sky AI apps.

Three things every app gets for free:
  1. SEO routes — /sitemap.xml and /robots.txt with the app's primary URL
  2. GA4 tag — google analytics 4, gated by env var so dev doesn't pollute
  3. Affiliate cards — contextual partner recommendations rendered alongside
     AI outputs, mapped by app category. Gated by env var so we can A/B test.

All revenue partners are clearly disclosed via the AFFILIATE_DISCLAIMER constant.
"""
from __future__ import annotations

import os
from typing import Optional

from flask import Flask, Response, jsonify, request

# ─── CATEGORIES ───────────────────────────────────────────
# Each app declares its category at install time. Cards are matched on category.
CATEGORIES = {
    'flagship', 'newcomer', 'education', 'legal', 'benefits',
    'civic', 'business', 'housing', 'healthcare', 'financial',
}


# ─── AFFILIATE PARTNERS — manually curated, all real public affiliate programs ─
# Format: {category: [{name, blurb, url, partner_id_env}]}
# url is a placeholder — real referral codes get loaded from the env var named
# in partner_id_env, so secrets never live in code.
PARTNERS = {
    'newcomer': [
        {'name': 'Atticus — Free Lawyer Match', 'blurb': 'Talk to a vetted immigration lawyer for free; pay only if you hire.', 'url_template': 'https://atticus.com/?ref={pid}', 'partner_id_env': 'ATTICUS_PARTNER_ID'},
        {'name': 'TopResume — Free CV Review', 'blurb': 'Free professional resume critique within 48 hours.', 'url_template': 'https://topresume.com/r/{pid}', 'partner_id_env': 'TOPRESUME_PARTNER_ID'},
        {'name': 'Wise — Send Money Home', 'blurb': 'Real exchange rate, low fees on international transfers.', 'url_template': 'https://wise.com/invite/u/{pid}', 'partner_id_env': 'WISE_PARTNER_ID'},
    ],
    'legal': [
        {'name': 'LegalShield — Talk to a Lawyer', 'blurb': '$30/mo membership — unlimited consultations + document review.', 'url_template': 'https://legalshield.com/?aff={pid}', 'partner_id_env': 'LEGALSHIELD_PARTNER_ID'},
        {'name': 'Rocket Lawyer — Free 7-day Trial', 'blurb': 'Custom legal documents + on-call attorneys.', 'url_template': 'https://rocketlawyer.com/?affid={pid}', 'partner_id_env': 'ROCKETLAWYER_PARTNER_ID'},
        {'name': 'Trust & Will — Online Estate Planning', 'blurb': 'Wills + trusts + healthcare directives, state-specific.', 'url_template': 'https://trustandwill.com/?ref={pid}', 'partner_id_env': 'TRUSTANDWILL_PARTNER_ID'},
    ],
    'benefits': [
        {'name': 'GoodRx — Free Rx Discount Card', 'blurb': 'Up to 80% off prescriptions at most pharmacies.', 'url_template': 'https://goodrx.com/?ref={pid}', 'partner_id_env': 'GOODRX_PARTNER_ID'},
        {'name': 'BenefitsCheckUp — NCOA', 'blurb': 'Free 5-minute screen for 2,000+ federal/state/local benefit programs.', 'url_template': 'https://benefitscheckup.org/', 'partner_id_env': ''},
        {'name': 'NeedyMeds — PAP Database', 'blurb': 'Free medication assistance program finder.', 'url_template': 'https://needymeds.org/', 'partner_id_env': ''},
    ],
    'civic': [
        {'name': 'Vote.org — Voter Tools', 'blurb': 'Free non-partisan voter registration + election reminders.', 'url_template': 'https://vote.org/', 'partner_id_env': ''},
        {'name': 'Lemonade — Renters Insurance', 'blurb': 'From $5/mo, instant signup, AI claims.', 'url_template': 'https://lemonade.com/?ref={pid}', 'partner_id_env': 'LEMONADE_PARTNER_ID'},
        {'name': 'Ramsey SmartTax', 'blurb': 'Federal + state tax filing, multi-state ready.', 'url_template': 'https://ramseysolutions.com/?ref={pid}', 'partner_id_env': 'RAMSEYTAX_PARTNER_ID'},
    ],
    'education': [
        {'name': 'Khan Academy — Free K-12 + Test Prep', 'blurb': 'Free SAT/AP/Praxis prep + state standards aligned.', 'url_template': 'https://khanacademy.org/', 'partner_id_env': ''},
        {'name': 'Course Hero — Study Resources', 'blurb': 'Tutoring + practice problems + textbook solutions.', 'url_template': 'https://coursehero.com/?ref={pid}', 'partner_id_env': 'COURSEHERO_PARTNER_ID'},
    ],
    'business': [
        {'name': 'EnergySage — Free Solar Quotes', 'blurb': 'Compare 7+ vetted installers; no obligation.', 'url_template': 'https://energysage.com/p/{pid}', 'partner_id_env': 'ENERGYSAGE_PARTNER_ID'},
        {'name': 'Shopify — 3-Day Free Trial', 'blurb': 'Build an online store in minutes; $1/mo for 3 months.', 'url_template': 'https://shopify.com/?ref={pid}', 'partner_id_env': 'SHOPIFY_PARTNER_ID'},
    ],
    'healthcare': [
        {'name': 'GoodRx — Free Rx Discount Card', 'blurb': 'Up to 80% off prescriptions.', 'url_template': 'https://goodrx.com/?ref={pid}', 'partner_id_env': 'GOODRX_PARTNER_ID'},
        {'name': 'PolicyGenius — Insurance Marketplace', 'blurb': 'Compare health, life, disability quotes free.', 'url_template': 'https://policygenius.com/?ref={pid}', 'partner_id_env': 'POLICYGENIUS_PARTNER_ID'},
    ],
    'financial': [
        {'name': 'Credit Karma — Free Credit Score', 'blurb': 'Free credit score + tax prep + dispute helper.', 'url_template': 'https://creditkarma.com/?ref={pid}', 'partner_id_env': 'CREDITKARMA_PARTNER_ID'},
        {'name': 'Empower (formerly Personal Capital)', 'blurb': 'Free retirement planning tools + advisor option.', 'url_template': 'https://empower.com/?ref={pid}', 'partner_id_env': 'EMPOWER_PARTNER_ID'},
    ],
    'housing': [
        {'name': 'Zillow Rentals', 'blurb': 'Search 5M+ rental listings nationwide.', 'url_template': 'https://zillow.com/rentals/', 'partner_id_env': ''},
        {'name': 'Lemonade — Renters Insurance', 'blurb': 'From $5/mo with instant claims via AI.', 'url_template': 'https://lemonade.com/?ref={pid}', 'partner_id_env': 'LEMONADE_PARTNER_ID'},
    ],
    'flagship': [],  # No affiliates on the flagship to keep premium feel
}


AFFILIATE_DISCLAIMER = (
    'These are partner services we may earn a commission from at no extra cost to you. '
    'We only recommend services we believe genuinely help — never required to use the AI tool above.'
)


# ─── PUBLIC API ───────────────────────────────────────────
def affiliates_for(category: str, limit: int = 3) -> list[dict]:
    """Return rendered affiliate cards for an app's category, with real referral
    codes substituted in from environment. Cards without a partner_id_env are
    always returned (free non-profits like vote.org). Cards with one are only
    returned when the env var is set, so you don't ship dead links in dev."""
    if category not in CATEGORIES:
        return []
    out = []
    for p in PARTNERS.get(category, []):
        env = p.get('partner_id_env', '')
        pid = os.environ.get(env, '') if env else ''
        # If the partner needs a partner_id and we don't have one, skip in
        # production. In dev (FRESHSKY_REVENUE_DEV=1) we show the card anyway
        # with a placeholder so designers can preview the layout.
        if env and not pid:
            if os.environ.get('FRESHSKY_REVENUE_DEV') == '1':
                pid = 'demo'
            else:
                continue
        url = p['url_template'].replace('{pid}', pid) if pid else p['url_template']
        out.append({'name': p['name'], 'blurb': p['blurb'], 'url': url})
        if len(out) >= limit:
            break
    return out


def register_seo_routes(app: Flask, slug: str, brand: str, primary_url: str) -> None:
    """Add /sitemap.xml + /robots.txt + /humans.txt to a Flask app.
    primary_url should be the canonical URL with trailing slash, e.g.
    'https://example.com/'."""
    @app.route('/robots.txt')
    def _robots():
        body = (
            'User-agent: *\n'
            'Allow: /\n'
            'Disallow: /api/\n'
            'Disallow: /metrics\n'
            'Disallow: /health\n'
            f'Sitemap: {primary_url.rstrip("/")}/sitemap.xml\n'
        )
        return Response(body, mimetype='text/plain')

    @app.route('/sitemap.xml')
    def _sitemap():
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'  <url><loc>{primary_url.rstrip("/")}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>\n'
            '</urlset>\n'
        )
        return Response(body, mimetype='application/xml')

    @app.route('/humans.txt')
    def _humans():
        return Response(
            f'/* {brand} */\n'
            'Built by Fresh Sky LLC.\n'
            'Tools that help people; revenue from clearly disclosed partners.\n'
            f'App: {slug}\n',
            mimetype='text/plain',
        )


def register_revenue_routes(app: Flask, category: str) -> None:
    """Expose /api/affiliates so the frontend can render contextual partner cards
    next to AI outputs. Returns disclaimer + list of relevant partners."""
    @app.route('/api/affiliates')
    def _affiliates():
        cards = affiliates_for(category, limit=3)
        return jsonify(disclaimer=AFFILIATE_DISCLAIMER, partners=cards)


def ga4_snippet(measurement_id: Optional[str] = None) -> str:
    """Returns the GA4 HTML snippet ready to inject into <head>. If no
    measurement_id given, reads GA4_MEASUREMENT_ID env var. Returns '' when
    not configured (dev / no analytics)."""
    mid = measurement_id or os.environ.get('GA4_MEASUREMENT_ID', '').strip()
    if not mid:
        return ''
    return (
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={mid}"></script>\n'
        f'<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}'
        f'gtag("js",new Date());gtag("config","{mid}",{{anonymize_ip:true}});</script>\n'
    )


def install(app: Flask, *, slug: str, brand: str, primary_url: str, category: str) -> None:
    """One-call setup: registers SEO + revenue routes for an app. Apps just call:

        from freshsky_common.revenue import install
        install(app, slug='myapp', brand='My App', primary_url='https://myapp.com/', category='legal')
    """
    if category not in CATEGORIES:
        raise ValueError(f'unknown category {category!r}; allowed: {sorted(CATEGORIES)}')
    register_seo_routes(app, slug=slug, brand=brand, primary_url=primary_url)
    register_revenue_routes(app, category)
