"""
Analytics + SEO helpers shared across all Fresh Sky AI apps.

Two things every app gets for free:
  1. SEO routes — /sitemap.xml and /robots.txt with the app's primary URL
  2. GA4 tag — google analytics 4, gated by env var so dev doesn't pollute
"""
from __future__ import annotations

import os
from typing import Optional

from flask import Flask, Response, jsonify

from .brand import install_brand_assets

# ─── CATEGORIES ───────────────────────────────────────────
# Each app declares its category at install time. Used by cross-promo,
# FAQ schema, and schema.org typing.
CATEGORIES = {
    'flagship', 'newcomer', 'education', 'legal', 'benefits',
    'civic', 'business', 'housing', 'healthcare', 'financial',
}


def partners_for_category(category: str) -> list[dict]:
    """Return no commercial partner offers.

    The endpoint remains available for compatibility with older templates;
    those templates hide their partner section when this list is empty.
    """
    del category
    return []


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
            'Focused guidance with three free AI previews and predictable monthly plans.\n'
            'Safety information, official-source links, and civic volunteer tools remain free.\n'
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
    """Return no advertising markup; product access is never ad-dependent."""
    del category, client_id
    return ''


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
         'You can try three AI-generated results free. Continued AI use requires the monthly plan shown in the tool: Focus $9.99, Plus $19.99, or Advanced $29.99. Safety information and official-source links remain free.'),
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
         'Yes. Fresh Sky AI civic volunteer tools remain free, with no contracts or upsell.'),
    ],
    'healthcare': [
        ('Is this medical advice?',
         'No. This tool explains how healthcare systems and benefits work; it does not diagnose, treat, or recommend medications. For medical decisions, consult a licensed clinician.'),
        ('Will my health information be stored?',
         'No. We do not store inputs and we do not collect health data. Inputs are sent to AI providers for response generation only.'),
        ('Does this work with my insurance?',
         'The AI uses general knowledge of US insurance frameworks (Medicare, Medicaid, ACA, employer plans) to explain coverage in plain language. Always verify specific coverage decisions with your plan.'),
        ('Is this free to use?',
         'You can try three AI-generated results free. Continued AI use requires the monthly plan shown in the tool: Focus $9.99, Plus $19.99, or Advanced $29.99. Safety information and official-source links remain free.'),
    ],
    'education': [
        ('Is this approved by my school district?',
         'The public tool is for de-identified scenarios. A school must follow district policy and obtain any required authorization or data-processing agreement before using a vendor with FERPA-covered records.'),
        ('Is student data stored?',
         'Do not submit student PII or education records. Education apps reject several common identifier patterns before provider calls, but automated detection is not a substitute for removing all identifying details.'),
        ('Does this work for IEP, 504, or ELL situations?',
         'Yes. The tools are designed around K-12 special education and English-learner contexts, with multilingual support so non-English-speaking parents can fully participate.'),
        ('Is this free for teachers and parents?',
         'Teachers, parents, and other users can try three AI-generated results free. Continued AI use requires the monthly plan shown in the tool: Focus $9.99, Plus $19.99, or Advanced $29.99. Safety information and official-source links remain free.'),
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
         'You can try three AI-generated results free. Continued AI use requires the monthly plan shown in the tool: Focus $9.99, Plus $19.99, or Advanced $29.99. Safety information and official-source links remain free.'),
    ],
    'business': [
        ('Is this tax or financial advice?',
         'No. This is an educational tool. For binding tax, financial, grant, or sponsorship decisions, consult a qualified professional.'),
        ('How current is the program data?',
         'When a tool uses live public data, it labels the source. For specific deadlines, award amounts, eligibility cutoffs, and sponsor obligations, verify with the official program page before acting.'),
        ('Is my information stored?',
         'No personal information is stored.'),
        ('Free or paid?',
         'You can try three AI-generated results free. Continued AI use requires the monthly plan shown in the tool: Focus $9.99, Plus $19.99, or Advanced $29.99. Safety information and official-source links remain free.'),
    ],
    'financial': [
        ('Is this financial advice?',
         'No. This is an educational tool. For binding financial decisions, consult a licensed financial advisor or accountant.'),
        ('Is my financial information stored?',
         'No. We do not store inputs. Do not enter account numbers, SSN, or other identifiers.'),
        ('Does it cover federal and state programs?',
         'Yes. The tool covers federal frameworks plus major state-level variations where they materially differ.'),
        ('How much does it cost?',
         'You can try three AI-generated results free. Continued AI use requires the monthly plan shown in the tool: Focus $9.99, Plus $19.99, or Advanced $29.99. Safety information and official-source links remain free.'),
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
    """Link back to the neutral catalog without naming another service.

    Reusable product patterns may cross repository boundaries; product names,
    customer context, identity, and data do not.
    """
    del current_slug, current_category, count
    return (
        '<section class="fs-catalog-link" style="max-width:720px;margin:32px auto 16px;padding:0 16px;text-align:center">'
        '<a href="https://www.freshskyai.com/hub" target="_blank" rel="noopener" '
        'style="display:inline-flex;min-height:44px;align-items:center;padding:10px 14px;'
        'border:1px solid rgba(94,231,247,.28);border-radius:999px;background:rgba(94,231,247,.08);'
        'text-decoration:none;color:#5ee7f7;font-weight:700;font-size:13px">'
        'Return to the Fresh Sky AI catalog</a>'
        '</section>'
    )


def og_snippet(brand: str, primary_url: str, description: str = '') -> str:
    """Open Graph + Twitter card tags. Picked up by Slack, Twitter, FB, LinkedIn
    link unfurls. Uses the hub's default og-image.png so every app gets a
    consistent social preview without needing per-app image assets.

    NOTE: Also appends the portfolio-wide interface foundation so every batch
    app that renders {{ og_tags|safe }} gets consistent, accessible styling
    without duplicating it in each repository. App-specific styles remain the
    base and this layer only normalizes shared surfaces and controls."""
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
        + _PORTFOLIO_SKIN_CSS
        + '<link rel="stylesheet" href="/freshsky.css">\n'
    )


_PORTFOLIO_SKIN_CSS = """<style id="fs-portfolio-skin">
/* FreshSky Horizon interface. Product-specific layouts remain authoritative;
   this layer supplies a coherent, accessible visual system across the fleet. */
:root{--fs-bg:#050816;--fs-fg:#f4f7ff;--fs-mute:#aab8d0;--fs-card:rgba(15,23,48,.78);--fs-border:rgba(148,176,225,.22);--fs-accent:#5ee7f7;--fs-accent-dark:#7c8cff;--fs-soft:rgba(94,231,247,.11);--fs-shadow:0 20px 65px rgba(0,0,0,.32);--fs-glow:0 0 0 1px rgba(94,231,247,.08),0 18px 55px rgba(36,75,180,.2)}
html{color-scheme:dark;background:var(--fs-bg)!important}
body{background:radial-gradient(circle at 12% -5%,rgba(67,82,219,.28),transparent 32rem),radial-gradient(circle at 88% 12%,rgba(0,206,209,.17),transparent 30rem),linear-gradient(180deg,#070b1d 0,#050816 55%,#080c1b 100%)!important;color:var(--fs-fg)!important;overflow-wrap:anywhere;min-height:100vh}
body::before{content:""!important;display:block!important;position:fixed!important;inset:0!important;pointer-events:none!important;z-index:-1!important;opacity:.28;background-image:linear-gradient(rgba(124,140,255,.06) 1px,transparent 1px),linear-gradient(90deg,rgba(124,140,255,.06) 1px,transparent 1px);background-size:56px 56px;mask-image:linear-gradient(to bottom,#000,transparent 72%)}
body,button,input,select,textarea{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
button{min-height:44px}
img,svg,video,canvas{max-width:100%}
.nav,.topbar,nav.fs-nav{background:rgba(5,8,22,.78)!important;border-bottom:1px solid var(--fs-border)!important;color:var(--fs-fg)!important;box-shadow:0 8px 30px rgba(0,0,0,.2)!important;backdrop-filter:blur(18px) saturate(135%)}
.topbar{padding-inline:max(20px,calc((100vw - 1160px)/2))!important}
.nav a,.topbar a,nav.fs-nav a,.brand,.fs-nav .brand{color:var(--fs-fg)!important}
.nav a.brand,.topbar a.brand,.brand,.fs-nav .brand{font-weight:750!important;letter-spacing:-.02em}
header,.hero,.fs-hero{background:transparent!important;color:var(--fs-fg)!important;position:relative}
header:not(.topbar){width:min(100% - 40px,1160px);margin-inline:auto!important;text-align:left!important}
header h1,.hero h1,.fs-hero h1{color:var(--fs-fg)!important;background:none!important;-webkit-text-fill-color:currentColor!important;letter-spacing:-.035em}
header p,.hero p,.fs-hero p,.sub,.hero-desc{color:var(--fs-mute)!important;max-width:760px}
.hero-pills,.fs-hero-badges,.hero-badges{margin-top:14px}
.pill,.fs-hero-pill,.badge-pill,.hero-pill,.badge{background:var(--fs-soft)!important;color:var(--fs-accent)!important;border:1px solid rgba(94,231,247,.3)!important}
.card,.tool,.feature-card,.step-card,.landing-section{background:var(--fs-card)!important;border:1px solid var(--fs-border)!important;color:var(--fs-fg)!important;box-shadow:var(--fs-shadow)!important;border-radius:16px!important}
.card:hover,.tool:hover,.feature-card:hover,.step-card:hover{border-color:rgba(94,231,247,.42)!important;box-shadow:var(--fs-glow)!important}
.card h1,.card h2,.card h3,.tool h2,.tool h3,.feature-card h3,.step-card h3,.section-title{color:var(--fs-fg)!important}
.muted,.hint,.section-subtitle,.note,.disclaimer,.cta-footnote{color:var(--fs-mute)!important}
input,textarea,select,.input{background:rgba(7,12,29,.9)!important;color:var(--fs-fg)!important;border:1px solid rgba(148,176,225,.32)!important;border-radius:11px!important;min-height:44px}
textarea{min-height:112px}
input:focus,textarea:focus,select:focus,.input:focus{outline:3px solid rgba(94,231,247,.2)!important;outline-offset:1px;border-color:var(--fs-accent)!important;background:#080d22!important}
input::placeholder,textarea::placeholder{color:#8291aa!important;opacity:1}
.btn,.btn-primary,button.primary,.landing-cta,button[type=submit]{min-height:48px;background:linear-gradient(135deg,#5ee7f7,#7c8cff)!important;color:#06101f!important;border:1px solid rgba(255,255,255,.16)!important;box-shadow:0 10px 28px rgba(81,126,235,.27)!important;border-radius:11px!important;font-weight:800!important;transition:filter .15s,box-shadow .15s,transform .15s!important}
.btn:hover,.btn-primary:hover,button.primary:hover,.landing-cta:hover,button[type=submit]:hover{filter:brightness(1.08)!important;box-shadow:0 14px 36px rgba(81,126,235,.38)!important;transform:translateY(-1px)}
.btn:focus-visible,.btn-primary:focus-visible,button:focus-visible,a:focus-visible{outline:3px solid rgba(94,231,247,.42)!important;outline-offset:3px!important}
.btn:disabled,.btn-primary:disabled,button:disabled{opacity:.55!important;cursor:not-allowed!important;transform:none!important}
.output,.result,.result-box,pre{background:rgba(3,7,19,.78)!important;color:var(--fs-fg)!important;border:1px solid var(--fs-border)!important;border-radius:11px!important}
.output.error,.result-box.error{background:rgba(115,23,45,.28)!important;border-color:#c95a73!important;color:#ffd6df!important}
footer,.foot,.footer{background:rgba(3,6,17,.72)!important;border-top:1px solid var(--fs-border)!important;color:var(--fs-mute)!important}
footer a,.foot a,.footer a{color:var(--fs-accent)!important}
table th{background:var(--fs-soft)!important;color:var(--fs-fg)!important}
table td{color:var(--fs-fg)!important;border-color:var(--fs-border)!important}
table tr:hover td{background:rgba(94,231,247,.06)!important}
a{color:var(--fs-accent)}
a:hover{color:#b6f7ff}
.fs-agent-philosophy{font-size:12px;color:var(--fs-mute);margin:4px 0 0;line-height:1.5;font-style:italic}
.tabs button{min-height:44px;background:rgba(8,13,32,.86)!important;color:var(--fs-fg)!important;border:1px solid var(--fs-border)!important}
.tabs button.active{background:linear-gradient(135deg,#5ee7f7,#7c8cff)!important;color:#06101f!important;border-color:transparent!important}
.landing-stats-strip,.stats-row{background:var(--fs-card)!important;border:1px solid var(--fs-border)!important;border-radius:14px!important}
.landing-stat-num,.stat-num{color:var(--fs-fg)!important}
.feature-tag,.cat-badge,.tag{background:var(--fs-soft)!important;color:var(--fs-accent)!important;border:1px solid rgba(94,231,247,.26)!important}
@media (max-width:640px){
  .topbar{padding-inline:16px!important;gap:10px;flex-wrap:wrap}
  .topbar a,.topbar label,.nav a,.fs-nav a{min-height:44px;display:inline-flex;align-items:center}
  header:not(.topbar){width:min(100% - 32px,1160px)}
  .card,.tool,.feature-card,.step-card,.landing-section{border-radius:13px!important}
  button,.btn,.btn-primary,.landing-cta{max-width:100%}
}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition-duration:.01ms!important;animation-duration:.01ms!important;animation-iteration-count:1!important}}
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
            disclosure='',
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

    install_visuals(app, ad_snippet=ad_snippet)


def install_visuals(app: Flask, *, ad_snippet: str = '') -> None:
    """Inject only the shared interface foundation, without adding routes.

    Foundation and civic services can use this when they already own their SEO,
    privacy, terms, and sitemap endpoints.
    """
    install_brand_assets(app)

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
            head_insert += _PORTFOLIO_SKIN_CSS
        if 'href="/freshsky.css"' not in body:
            head_insert += '<link rel="stylesheet" href="/freshsky.css">'
        if not head_insert:
            return response
        new = body.replace('</head>', head_insert + '</head>', 1)
        response.set_data(new)
        return response
