"""
Analytics + SEO helpers shared across all Fresh Sky AI apps.

Two things every app gets for free:
  1. SEO routes — /sitemap.xml and /robots.txt with the app's primary URL
  2. GA4 tag — google analytics 4, gated by env var so dev doesn't pollute
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import threading
import time as _time
import urllib.request
from typing import Optional

from flask import Flask, Response, jsonify

_log = logging.getLogger(__name__)

# ─── CATEGORIES ───────────────────────────────────────────
# Each app declares its category at install time. Used by cross-promo,
# FAQ schema, and schema.org typing.
CATEGORIES = {
    'flagship', 'newcomer', 'education', 'legal', 'benefits',
    'civic', 'business', 'housing', 'healthcare', 'financial',
}


_PARTNERS_PATH = pathlib.Path(__file__).parent / 'partners.json'
# When set (e.g. https://storage.googleapis.com/fresh-sky-config/partners.json),
# the lib pulls the JSON from this URL with a TTL refresh — so affiliate-card
# updates ship to the whole portfolio without bulk-redeploying every app.
# Falls back to the bundled file on any fetch error.
_PARTNERS_URL = os.environ.get('PARTNERS_URL', '').strip()
_PARTNERS_TTL = int(os.environ.get('PARTNERS_TTL_SECONDS', '3600'))  # 1 hr default


def _load_partners_local() -> dict:
    try:
        with _PARTNERS_PATH.open() as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _load_partners_remote(url: str) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'freshsky-common/1.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode('utf-8'))
            if isinstance(data, dict):
                return data
            return None
    except Exception as exc:
        _log.info('partners remote fetch failed (%s); using bundled fallback', exc)
        return None


_PARTNERS_CACHE = _load_partners_local()
_PARTNERS_CACHE_TS = 0.0
_PARTNERS_LOCK = threading.Lock()


def _maybe_refresh_partners() -> None:
    global _PARTNERS_CACHE, _PARTNERS_CACHE_TS
    if not _PARTNERS_URL:
        return
    now = _time.time()
    if (now - _PARTNERS_CACHE_TS) < _PARTNERS_TTL:
        return
    with _PARTNERS_LOCK:
        if (_time.time() - _PARTNERS_CACHE_TS) < _PARTNERS_TTL:
            return
        remote = _load_partners_remote(_PARTNERS_URL)
        if remote is not None:
            _PARTNERS_CACHE = remote
        _PARTNERS_CACHE_TS = _time.time()


def partners_for_category(category: str) -> list[dict]:
    """Returns a list of {name, url, blurb} dicts for the given category, or
    an empty list if no partners are configured (in which case the consuming
    template's JS hides the 'Helpful services' section entirely)."""
    _maybe_refresh_partners()
    return _PARTNERS_CACHE.get('by_category', {}).get(category, []) or []


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

    # IndexNow key file — published at the path /<key>.txt so Bing/Yandex
    # can verify domain ownership before accepting indexing pings. Same key
    # is used across the entire Fresh Sky portfolio (cross-host indexing is
    # explicitly supported by the spec). Rotate by changing the constant
    # below and re-deploying all apps.
    _INDEXNOW_KEY = '45938110f3800b6fc6e260f67d9cd34d'

    @app.route(f'/{_INDEXNOW_KEY}.txt')
    def _indexnow_key():
        return Response(_INDEXNOW_KEY, mimetype='text/plain')


def ga4_snippet(measurement_id: Optional[str] = None) -> str:
    """Returns the GA4 HTML snippet ready to inject into <head>.

    Reads `GA_MEASUREMENT_ID` first (matches the convention the foundation
    apps freshskyai/EduSafeAI/USALivingGuide/teachercerts already use) and
    falls back to `GA4_MEASUREMENT_ID`. Returns '' when neither is set so
    dev/pre-launch deploys are silent.
    """
    mid = (
        measurement_id
        or os.environ.get('GA_MEASUREMENT_ID', '').strip()
        or os.environ.get('GA4_MEASUREMENT_ID', '').strip()
    )
    if not mid:
        return ''
    return (
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={mid}"></script>\n'
        f'<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}'
        f'gtag("js",new Date());gtag("config","{mid}",{{anonymize_ip:true}});</script>\n'
    )


def adsense_snippet(category: str = '', client_id: Optional[str] = None) -> str:
    """Return the Google AdSense Auto Ads tag when configured.

    Ads are env-driven so turning them on only requires setting the publisher
    id on Cloud Run. Civic apps intentionally stay ad-free by default.
    """
    if category == 'civic':
        return ''
    cid = (
        client_id
        or os.environ.get('ADSENSE_CLIENT_ID', '').strip()
        or os.environ.get('GOOGLE_ADSENSE_CLIENT_ID', '').strip()
        or os.environ.get('ADSENSE_PUBLISHER_ID', '').strip()
    )
    if not cid:
        return ''
    if cid.startswith('pub-'):
        cid = f'ca-{cid}'
    if not cid.startswith('ca-pub-'):
        return ''
    return (
        '<script async '
        'src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js'
        f'?client={cid}" crossorigin="anonymous"></script>\n'
    )


_CATEGORY_APP_TYPE = {
    'legal': 'LegalService', 'benefits': 'GovernmentService',
    'civic': 'GovernmentService', 'housing': 'RealEstateAgent',
    'healthcare': 'MedicalWebPage', 'education': 'EducationalOrganization',
    'newcomer': 'GovernmentService', 'financial': 'FinancialService',
    'business': 'ProfessionalService', 'flagship': 'WebApplication',
}


# ─── PORTFOLIO MAP — used by cross_promo for in-portfolio cross-linking ────
# Update when a new batch app launches or an existing one retires/renames.
PORTFOLIO = [
    {'slug': 'eslparentadvocate',   'brand': 'ESL Parent Advocate',   'url': 'https://esl.freshskyai.com/',          'category': 'education'},
    {'slug': 'smallclaimsus',       'brand': 'Small Claims US',       'url': 'https://claims.freshskyai.com/',       'category': 'legal'},
    {'slug': 'govformhelper',       'brand': 'Gov Form Helper',       'url': 'https://forms.freshskyai.com/',        'category': 'newcomer'},
    {'slug': 'medicaidcheck',       'brand': 'Medicaid Check',        'url': 'https://medicaid.freshskyai.com/',     'category': 'healthcare'},
    {'slug': 'statemoverai',        'brand': 'State Mover AI',        'url': 'https://mover.freshskyai.com/',        'category': 'civic'},
    {'slug': 'hoadisputehelp',      'brand': 'HOA Dispute Help',      'url': 'https://hoa.freshskyai.com/',          'category': 'legal'},
    {'slug': 'foiahelper',          'brand': 'FOIA Helper',           'url': 'https://foia.freshskyai.com/',         'category': 'legal'},
    {'slug': 'snapcheck',           'brand': 'SNAP Check',            'url': 'https://snap.freshskyai.com/',         'category': 'benefits'},
    {'slug': 'unemploymentappeal',  'brand': 'Unemployment Appeal',   'url': 'https://unemploy.freshskyai.com/',     'category': 'benefits'},
    {'slug': 'wageclaimai',         'brand': 'Wage Claim AI',         'url': 'https://wages.freshskyai.com/',        'category': 'legal'},
    {'slug': 'section8nav',         'brand': 'Section 8 Nav',         'url': 'https://section8.freshskyai.com/',     'category': 'housing'},
    {'slug': 'adarequester',        'brand': 'ADA Requester',         'url': 'https://ada.freshskyai.com/',          'category': 'legal'},
    {'slug': 'reentryhelp',         'brand': 'Reentry Help',          'url': 'https://reentry.freshskyai.com/',      'category': 'benefits'},
    {'slug': 'specialedai',         'brand': 'Special Ed AI',         'url': 'https://iep.freshskyai.com/',          'category': 'education'},
    {'slug': 'evictiondefense',     'brand': 'Eviction Defense',      'url': 'https://eviction.freshskyai.com/',     'category': 'legal'},
    {'slug': 'safetyplanai',        'brand': 'Safety Plan AI',        'url': 'https://safety.freshskyai.com/',       'category': 'legal'},
    {'slug': 'estateplanai',        'brand': 'Estate Plan AI',        'url': 'https://estate.freshskyai.com/',       'category': 'legal'},
    {'slug': 'childcarefinder',     'brand': 'Childcare Finder',      'url': 'https://childcare.freshskyai.com/',    'category': 'benefits'},
    {'slug': 'seniorbenefits',      'brand': 'Senior Benefits AI',    'url': 'https://seniorbenefits.freshskyai.com/', 'category': 'benefits'},
    {'slug': 'overdrafthelp',       'brand': 'Overdraft Help',        'url': 'https://overdraft.freshskyai.com/',    'category': 'financial'},
    {'slug': 'probatewalk',         'brand': 'Probate Walk',          'url': 'https://probate.freshskyai.com/',      'category': 'legal'},
    {'slug': 'vinhistory',          'brand': 'VIN History AI',        'url': 'https://carhistory.freshskyai.com/',   'category': 'financial'},
    {'slug': 'grantsponsorai',      'brand': 'Grant Sponsor AI',      'url': 'https://grants.freshskyai.com/',       'category': 'business'},
]

# When the current app's category has fewer than 3 other apps, fall back to
# the most-related neighbor category for cross-promo.
_SIBLING_CATEGORY = {
    'housing': 'benefits', 'business': 'financial', 'financial': 'benefits',
    'education': 'newcomer', 'civic': 'newcomer',
}


# ─── FAQ DATA — per-HULEC-category, used by faq_schema_html ────────────────
# 4 questions per category, each with a plain-language answer. Real Google
# rich-result eligibility requires actual user-relevant questions, not SEO
# bait. These are written from the standpoint of a first-time visitor.
_FAQ_BY_CATEGORY = {
    'legal': [
        ('Is this legal advice?',
         'No. This is an educational tool that explains how the law works in plain language. It is not a substitute for an attorney. For anything that affects your rights, money, or freedom, consult a licensed attorney in your state.'),
        ('Is my information stored?',
         'No personal information is stored on our servers. The text you type is sent to AI providers to generate a response, then discarded. We keep no record of what you asked or what the AI replied.'),
        ('Does this work in my state?',
         'Yes. Every legal tool is state-aware: the AI is told which state you are in (or works from the federal default) and tailors its guidance to that state’s statutes, court rules, and procedural quirks.'),
        ('How much does this cost?',
         'It is free to use, subject to temporary fair-use limits that protect shared infrastructure. Optional sponsorship helps keep access open but does not buy higher limits.'),
    ],
    'benefits': [
        ('Is this affiliated with the government?',
         'No. This is an independent tool that helps you understand and apply for public benefits. We have no formal relationship with any federal or state agency.'),
        ('Will using this affect my benefits?',
         'No. We do not communicate with any agency on your behalf. The tool generates plain-language explanations and draft documents that you submit yourself, the same way you would on your own.'),
        ('Is my application data stored?',
         'No personal information is stored. We do not ask for SSN, ID, or address; the AI is instructed not to need them.'),
        ('Does it cover my state?',
         'Yes. Every benefits tool is built around all 50 US states and territories where applicable, with eligibility rules that vary by state baked in.'),
    ],
    'civic': [
        ('Is this an official government tool?',
         'No. This is an independent guide that explains civic processes (voting, immigration, public records) in plain language. Always confirm specific deadlines and requirements with the relevant agency.'),
        ('Is my information stored?',
         'No personal information is stored. Inputs go to AI providers for response generation and are not retained by us.'),
        ('How current is the information?',
         'The AI is grounded in publicly available federal and state information. For time-sensitive items (deadlines, election dates), verify with the relevant agency before acting.'),
        ('Is this free?',
         'Yes. It is free to use, subject to temporary fair-use limits that protect shared infrastructure.'),
    ],
    'healthcare': [
        ('Is this medical advice?',
         'No. This tool explains how healthcare systems and benefits work; it does not diagnose, treat, or recommend medications. For medical decisions, consult a licensed clinician.'),
        ('Will my health information be stored?',
         'No. We do not store inputs and we do not collect health data. Inputs are sent to AI providers for response generation only.'),
        ('Does this work with my insurance?',
         'The AI uses general knowledge of US insurance frameworks (Medicare, Medicaid, ACA, employer plans) to explain coverage in plain language. Always verify specific coverage decisions with your plan.'),
        ('Is this free to use?',
         'Yes. It is free to use, subject to temporary fair-use limits that protect shared infrastructure.'),
    ],
    'education': [
        ('Is this approved by my school district?',
         'No formal approval is required. This is an educational tool that helps parents and teachers understand documents and prepare for meetings. It does not replace any official school process.'),
        ('Is student data stored?',
         'No. The tool does not store any input. We do not collect student PII; the AI is instructed not to ask for any.'),
        ('Does this work for IEP, 504, or ELL situations?',
         'Yes. The tools are designed around K-12 special education and English-learner contexts, with multilingual support so non-English-speaking parents can fully participate.'),
        ('Is this free for teachers and parents?',
         'Yes. It is free for teachers and parents, subject to temporary fair-use limits that protect shared infrastructure.'),
    ],
    'newcomer': [
        ('Will this affect my immigration status?',
         'No. This tool helps you understand US documents and forms in plain language; it does not communicate with any agency or alter any record. For binding immigration decisions, consult a licensed immigration attorney.'),
        ('Is my data shared with any agency?',
         'No. We do not share data with any government agency. We do not store your inputs at all.'),
        ('Does it work in my native language?',
         'Yes. Every newcomer tool supports 12 languages: English, Spanish, Chinese, Arabic, Vietnamese, Tagalog, Korean, Russian, Portuguese, Hindi, French, and Turkish.'),
        ('Is this an official US government service?',
         'No. This is an independent educational tool. Always verify specific requirements and deadlines on the relevant official website (.gov).'),
    ],
    'housing': [
        ('Is this legal advice for my housing situation?',
         'No. This explains housing law and benefit programs in plain language. For an active dispute or eviction, contact a licensed attorney or your local legal aid organization.'),
        ('Is my address or landlord information stored?',
         'No personal information is stored. The AI does not need it; the tool answers questions based on what you describe in general terms.'),
        ('Does it cover my state and city?',
         'Yes. Housing rules are state and city-specific, and the tool is designed to ask which state you’re in and tailor guidance accordingly.'),
        ('Is it free?',
         'Yes. It is free to use, subject to temporary fair-use limits that protect shared infrastructure.'),
    ],
    'business': [
        ('Is this tax or financial advice?',
         'No. This is an educational tool. For binding tax, financial, grant, or sponsorship decisions, consult a qualified professional.'),
        ('How current is the program data?',
         'When a tool uses live public data, it labels the source. For specific deadlines, award amounts, eligibility cutoffs, and sponsor obligations, verify with the official program page before acting.'),
        ('Is my information stored?',
         'No personal information is stored.'),
        ('Free or paid?',
         'Free. Temporary fair-use limits protect shared infrastructure, and optional sponsorship does not purchase higher limits.'),
    ],
    'financial': [
        ('Is this financial advice?',
         'No. This is an educational tool. For binding financial decisions, consult a licensed financial advisor or accountant.'),
        ('Is my financial information stored?',
         'No. We do not store inputs. Do not enter account numbers, SSN, or other identifiers.'),
        ('Does it cover federal and state programs?',
         'Yes. The tool covers federal frameworks plus major state-level variations where they materially differ.'),
        ('How much does it cost?',
         'It is free to use, subject to temporary fair-use limits that protect shared infrastructure.'),
    ],
}


def faq_schema_html(category: str) -> str:
    """Render schema.org FAQPage JSON-LD for the app's HULEC category.
    Boosts Google rich-result eligibility (FAQ accordions in search
    results) and gives the AI-overview surface real Q&A to quote from.
    Returns '' if the category has no FAQ data."""
    import json as _json
    qs = _FAQ_BY_CATEGORY.get(category)
    if not qs:
        return ''
    data = {
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        'mainEntity': [
            {'@type': 'Question', 'name': q,
             'acceptedAnswer': {'@type': 'Answer', 'text': a}}
            for q, a in qs
        ],
    }
    return f'<script type="application/ld+json">{_json.dumps(data, separators=(",", ":"))}</script>\n'


def trust_line_html(category: str) -> str:
    """One-line credibility blurb shown in the footer of every app.

    Identity-neutral by design — no personal credentials, names, or
    employer references. Conveys US-jurisdiction, multi-domain rigor,
    and the educational-only disclaimer that the legal HULEC pillar
    requires. The `category` arg is currently unused but kept in the
    signature so callers can opt into category tailoring later without
    a portfolio-wide rebuild."""
    del category  # reserved for future per-category tailoring
    body = (
        'Built and operated in the U.S. with subject-matter rigor '
        'across legal, education, healthcare, civic, and benefits — '
        'always educational guidance, never legal/medical/tax advice. '
        'For decisions that affect your rights, money, or freedom, '
        'consult a licensed professional in your jurisdiction.'
    )
    return (
        '<p style="text-align:center;color:#94a3b8;font-size:11.5px;'
        'padding:0 24px 4px;max-width:680px;margin:0 auto;line-height:1.6;">'
        f'{body}</p>'
    )


def cross_promo_html(current_slug: str, current_category: str, count: int = 3) -> str:
    """Render an HTML snippet recommending up to `count` related apps from
    the same HULEC category. Excludes the current app. If the category has
    too few peers, falls back to a sibling category. Returns '' if nothing
    sensible to recommend (e.g. unknown slug)."""
    same = [p for p in PORTFOLIO if p['category'] == current_category and p['slug'] != current_slug]
    if len(same) < count:
        sibling = _SIBLING_CATEGORY.get(current_category)
        if sibling:
            same += [p for p in PORTFOLIO if p['category'] == sibling and p['slug'] != current_slug and p not in same]
    picks = same[:count]
    if not picks:
        return ''

    cards = []
    for p in picks:
        cards.append(
            f'<a href="{p["url"]}" target="_blank" rel="noopener" '
            f'style="display:block;padding:12px 14px;border:1px solid #e2e8f0;border-radius:10px;'
            f'background:#fff;text-decoration:none;color:#1e293b;transition:border-color .15s">'
            f'<div style="font-weight:600;font-size:14px;margin-bottom:2px">{p["brand"]}</div>'
            f'<div style="color:#64748b;font-size:12px">{p["url"].replace("https://","").rstrip("/")}</div>'
            f'</a>'
        )
    return (
        '<section style="max-width:720px;margin:32px auto 16px;padding:0 16px">'
        '<h3 style="font-size:13px;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px;text-align:center">'
        'Other Fresh Sky AI tools you might need'
        '</h3>'
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px">'
        + ''.join(cards) +
        '</div>'
        '</section>'
    )


def og_snippet(brand: str, primary_url: str, description: str = '') -> str:
    """Open Graph + Twitter card tags. Picked up by Slack, Twitter, FB, LinkedIn
    link unfurls. Uses the hub's default og-image.png so every app gets a
    consistent social preview without needing per-app image assets.

    NOTE: Also appends the portfolio-wide futuristic dark skin CSS so every
    batch app that renders {{ og_tags|safe }} gets the modernized look
    without per-app template edits. The skin overrides existing styles via
    CSS specificity, so individual app stylesheets are preserved as a base."""
    desc = description or f'Part of the Fresh Sky AI portfolio — AI tools built under the HULEC rule.'
    url = primary_url.rstrip('/')
    return (
        f'<meta property="og:title" content="{brand}">\n'
        f'<meta property="og:description" content="{desc}">\n'
        f'<meta property="og:url" content="{url}">\n'
        f'<meta property="og:type" content="website">\n'
        f'<meta property="og:site_name" content="Fresh Sky AI">\n'
        f'<meta property="og:image" content="https://freshskyai.com/og-image.png">\n'
        f'<meta name="twitter:card" content="summary_large_image">\n'
        f'<meta name="twitter:title" content="{brand}">\n'
        f'<meta name="twitter:description" content="{desc}">\n'
        f'<meta name="twitter:image" content="https://freshskyai.com/og-image.png">\n'
        + _FUTURISTIC_SKIN_CSS
    )


_FUTURISTIC_SKIN_CSS = """<style id="fs-portfolio-skin">
/* Fresh Sky AI portfolio skin — futuristic dark theme overlay. Injected
   via freshsky_common.revenue.og_snippet so every batch app picks it up
   without per-app template edits. Overrides via CSS specificity. */
:root{--fs-bg:#06091a;--fs-fg:#e2e8f0;--fs-mute:#94a3b8;--fs-card:rgba(255,255,255,0.04);--fs-border:rgba(255,255,255,0.08);--fs-accent:#6366f1;--fs-accent2:#8b5cf6;--fs-accent3:#22d3ee}
html,body{background:var(--fs-bg)!important;color:var(--fs-fg)!important}
body{background-image:radial-gradient(at 15% 8%,rgba(99,102,241,0.10) 0%,transparent 45%),radial-gradient(at 85% 30%,rgba(6,182,212,0.08) 0%,transparent 45%),radial-gradient(at 50% 95%,rgba(139,92,246,0.10) 0%,transparent 45%)!important}
body::before{content:'';position:fixed;inset:0;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.55'/%3E%3C/svg%3E");opacity:0.025;pointer-events:none;z-index:9997;mix-blend-mode:overlay}
/* Nav / brand */
.nav,.topbar,nav,.fs-nav{background:rgba(6,9,26,0.78)!important;-webkit-backdrop-filter:blur(16px) saturate(180%);backdrop-filter:blur(16px) saturate(180%);border-bottom:1px solid var(--fs-border)!important;color:var(--fs-fg)!important}
.nav a,nav a,.brand,.fs-nav a{color:var(--fs-fg)!important}
.nav a.brand,nav a.brand,.brand,.fs-nav .brand{color:#fff!important;font-weight:700}
/* Hero */
header,.hero,.fs-hero{background:transparent!important;color:var(--fs-fg)!important;position:relative}
.hero h1,header h1,.fs-hero h1{color:#fff!important;background:linear-gradient(135deg,#fff 0%,#c7d2fe 100%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.hero p,header p,.fs-hero p,.sub,.hero-desc{color:var(--fs-mute)!important}
.hero-pills,.fs-hero-badges,.hero-badges{margin-top:14px}
.pill,.fs-hero-pill,.badge-pill,.hero-pill,.badge{background:rgba(255,255,255,0.06)!important;color:var(--fs-fg)!important;border:1px solid var(--fs-border)!important;-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px)}
/* Cards */
.card,.tool,.feature-card,.step-card,main section,.landing-section{background:var(--fs-card)!important;border:1px solid var(--fs-border)!important;color:var(--fs-fg)!important;-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);border-radius:14px!important}
.card h1,.card h2,.card h3,.tool h2,.tool h3{color:#fff!important}
.tool h2,.tool h3,.feature-card h3,.step-card h3,.section-title{color:#fff!important}
.muted,.hint,.section-subtitle,.note{color:var(--fs-mute)!important}
/* Inputs */
input,textarea,select,.input{background:rgba(255,255,255,0.05)!important;color:var(--fs-fg)!important;border:1px solid var(--fs-border)!important}
input:focus,textarea:focus,select:focus,.input:focus{outline:2px solid var(--fs-accent)!important;border-color:var(--fs-accent)!important;background:rgba(255,255,255,0.07)!important}
input::placeholder,textarea::placeholder{color:#64748b!important}
/* Buttons */
.btn,.btn-primary,button.primary,.landing-cta,button[type=submit]{background:linear-gradient(135deg,var(--fs-accent) 0%,var(--fs-accent2) 100%)!important;color:#fff!important;border:none!important;box-shadow:0 4px 20px rgba(99,102,241,0.3)!important;border-radius:10px!important;font-weight:600!important;transition:transform .15s,box-shadow .15s!important}
.btn:hover,.btn-primary:hover,button.primary:hover,.landing-cta:hover{transform:translateY(-2px)!important;box-shadow:0 8px 28px rgba(99,102,241,0.5),0 0 32px rgba(139,92,246,0.3)!important}
.btn:disabled,.btn-primary:disabled,button:disabled{opacity:.5!important;cursor:not-allowed!important;transform:none!important}
/* Output / result */
.output,.result,.result-box,pre{background:rgba(255,255,255,0.03)!important;color:var(--fs-fg)!important;border:1px solid var(--fs-border)!important;border-radius:10px!important}
.output.error,.result-box.error{background:rgba(239,68,68,0.08)!important;border-color:rgba(239,68,68,0.3)!important;color:#fca5a5!important}
/* Footer */
footer,.foot,.footer{background:rgba(0,0,0,0.3)!important;border-top:1px solid var(--fs-border)!important;color:var(--fs-mute)!important}
footer a,.foot a,.footer a{color:var(--fs-fg)!important}
/* Tables */
table th{background:rgba(99,102,241,0.15)!important;color:#fff!important}
table td{color:var(--fs-fg)!important;border-color:var(--fs-border)!important}
table tr:hover td{background:rgba(255,255,255,0.04)!important}
/* Links */
a{color:#a78bfa}
a:hover{color:#c4b5fd}
.disclaimer,.cta-footnote{color:var(--fs-mute)!important}
/* Agent widget philosophy second-line — sits under the tagline */
.fs-agent-philosophy{font-size:11px;color:#94a3b8;margin:4px 0 0;line-height:1.45;font-style:italic;letter-spacing:.01em}
/* Tabs (EduSafeAI-style) */
.tabs button{background:rgba(255,255,255,0.04)!important;color:var(--fs-fg)!important;border:1px solid var(--fs-border)!important}
.tabs button.active{background:linear-gradient(135deg,var(--fs-accent),var(--fs-accent2))!important;color:#fff!important;border-color:transparent!important}
/* Stats strip */
.landing-stats-strip,.stats-row{background:rgba(255,255,255,0.04)!important;border:1px solid var(--fs-border)!important;border-radius:14px!important}
.landing-stat-num,.stat-num{color:#fff!important}
/* Feature tags / category badges */
.feature-tag,.cat-badge,.tag{background:rgba(99,102,241,0.15)!important;color:#a5b4fc!important;border:1px solid rgba(99,102,241,0.25)!important}
</style>
"""


def schema_snippet(brand: str, primary_url: str, category: str, description: str = '') -> str:
    """JSON-LD structured data (schema.org). Tells Google this is a
    WebApplication / legal service / etc., improves rich-result eligibility."""
    import json as _json
    app_type = _CATEGORY_APP_TYPE.get(category, 'WebApplication')
    data = {
        '@context': 'https://schema.org',
        '@type': app_type,
        'name': brand,
        'url': primary_url.rstrip('/'),
        'description': description or f'AI tool in the Fresh Sky AI portfolio, built under the HULEC rule.',
        'provider': {
            '@type': 'Organization',
            'name': 'Fresh Sky LLC',
            'url': 'https://www.freshskyai.com',
        },
        'inLanguage': ['en', 'es', 'zh', 'ar', 'vi', 'tl', 'ko', 'ru', 'pt', 'hi', 'fr', 'tr'],
    }
    if app_type == 'WebApplication':
        data['applicationCategory'] = 'Utilities'
        data['operatingSystem'] = 'Web'
        data['offers'] = {'@type': 'Offer', 'price': '0', 'priceCurrency': 'USD'}
    return f'<script type="application/ld+json">{_json.dumps(data, separators=(",", ":"))}</script>\n'


def install(app: Flask, *, slug: str, brand: str, primary_url: str, category: str,
            description: str = '') -> None:
    """One-call setup: registers SEO routes + analytics/SEO context processor.

        from freshsky_common.revenue import install
        install(app, slug='myapp', brand='My App',
                primary_url='https://myapp.com/', category='legal',
                description='One-line what the tool does')

    Registers a Jinja context processor so templates can render:
      {{ ga4_snippet|safe }}   -- GA4 tag (active when GA_MEASUREMENT_ID set)
      {{ og_tags|safe }}       -- Open Graph + Twitter card
      {{ schema_json|safe }}   -- schema.org JSON-LD

    `description` is optional; when provided it's used in OG + schema.org.
    """
    if category not in CATEGORIES:
        raise ValueError(f'unknown category {category!r}; allowed: {sorted(CATEGORIES)}')
    register_seo_routes(app, slug=slug, brand=brand, primary_url=primary_url)

    @app.route('/api/affiliates')
    def _affiliates():
        return jsonify(
            partners=partners_for_category(category),
            disclosure=_PARTNERS_CACHE.get('_disclosure_text', ''),
        )

    # Privacy + Terms: auto-registered with shared boilerplate. Apps can
    # override by declaring their own /privacy or /terms route before
    # calling install() — register_legal_routes is idempotent.
    from .legal import register_legal_routes
    register_legal_routes(app, brand=brand, primary_url=primary_url)

    og = og_snippet(brand, primary_url, description)
    schema = schema_snippet(brand, primary_url, category, description)
    cross_promo = cross_promo_html(slug, category)
    trust_line = trust_line_html(category)
    faq_schema = faq_schema_html(category)
    ad_snippet = adsense_snippet(category)

    @app.context_processor
    def _inject_analytics():
        return {
            'ga4_snippet': ga4_snippet(),
            'adsense_snippet': ad_snippet,
            'og_tags': og,
            'schema_json': schema,
            'faq_schema': faq_schema,
            'cross_promo': cross_promo,
            'trust_line': trust_line,
            'app_slug': slug,
            'app_brand': brand,
        }

    # Inject the futuristic dark skin into every HTML response, regardless
    # of whether the template renders {{ og_tags|safe }}. Idempotent: skips
    # if the skin marker is already in the body (e.g., when og_tags is rendered).
    @app.after_request
    def _inject_skin(response):
        ct = (response.content_type or '').lower()
        if 'text/html' not in ct:
            return response
        if getattr(response, 'direct_passthrough', False):
            return response  # streamed response; don't touch
        try:
            body = response.get_data(as_text=True)
        except Exception:
            return response
        if '</head>' not in body:
            return response
        head_insert = ''
        if ad_snippet and 'pagead2.googlesyndication.com/pagead/js/adsbygoogle.js' not in body:
            head_insert += ad_snippet
        if 'fs-portfolio-skin' not in body:
            head_insert += _FUTURISTIC_SKIN_CSS
        if not head_insert:
            return response
        new = body.replace('</head>', head_insert + '</head>', 1)
        response.set_data(new)
        return response
