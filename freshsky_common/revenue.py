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
    {'slug': 'multistatetax',       'brand': 'Multi-State Tax',       'url': 'https://tax.freshskyai.com/',          'category': 'civic'},
    {'slug': 'smallclaimsus',       'brand': 'Small Claims US',       'url': 'https://claims.freshskyai.com/',       'category': 'legal'},
    {'slug': 'govformhelper',       'brand': 'Gov Form Helper',       'url': 'https://forms.freshskyai.com/',        'category': 'newcomer'},
    {'slug': 'medicaidcheck',       'brand': 'Medicaid Check',        'url': 'https://medicaid.freshskyai.com/',     'category': 'healthcare'},
    {'slug': 'statemoverai',        'brand': 'State Mover AI',        'url': 'https://mover.freshskyai.com/',        'category': 'civic'},
    {'slug': 'hoadisputehelp',      'brand': 'HOA Dispute Help',      'url': 'https://hoa.freshskyai.com/',          'category': 'legal'},
    {'slug': 'resumebridgeus',      'brand': 'Resume Bridge US',      'url': 'https://resume.freshskyai.com/',       'category': 'newcomer'},
    {'slug': 'foiahelper',          'brand': 'FOIA Helper',           'url': 'https://foia.freshskyai.com/',         'category': 'legal'},
    {'slug': 'snapcheck',           'brand': 'SNAP Check',            'url': 'https://snap.freshskyai.com/',         'category': 'benefits'},
    {'slug': 'unemploymentappeal',  'brand': 'Unemployment Appeal',   'url': 'https://unemploy.freshskyai.com/',     'category': 'benefits'},
    {'slug': 'uscistimeline',       'brand': 'USCIS Timeline',        'url': 'https://uscis.freshskyai.com/',        'category': 'newcomer'},
    {'slug': 'legalaidfinder',      'brand': 'Legal Aid Finder',      'url': 'https://legalaid.freshskyai.com/',     'category': 'legal'},
    {'slug': 'wageclaimai',         'brand': 'Wage Claim AI',         'url': 'https://wages.freshskyai.com/',        'category': 'legal'},
    {'slug': 'section8nav',         'brand': 'Section 8 Nav',         'url': 'https://section8.freshskyai.com/',     'category': 'housing'},
    {'slug': 'adarequester',        'brand': 'ADA Requester',         'url': 'https://ada.freshskyai.com/',          'category': 'legal'},
    {'slug': 'reentryhelp',         'brand': 'Reentry Help',          'url': 'https://reentry.freshskyai.com/',      'category': 'benefits'},
    {'slug': 'voterregai',          'brand': 'Voter Reg AI',          'url': 'https://vote.freshskyai.com/',         'category': 'civic'},
    {'slug': 'specialedai',         'brand': 'Special Ed AI',         'url': 'https://iep.freshskyai.com/',          'category': 'education'},
    {'slug': 'rxsavingsai',         'brand': 'Rx Savings AI',         'url': 'https://rx.freshskyai.com/',           'category': 'healthcare'},
    {'slug': 'veteransai',          'brand': 'Veterans AI',           'url': 'https://vets.freshskyai.com/',         'category': 'benefits'},
    {'slug': 'evictiondefense',     'brand': 'Eviction Defense',      'url': 'https://eviction.freshskyai.com/',     'category': 'legal'},
    {'slug': 'seniorcareai',        'brand': 'Senior Care AI',        'url': 'https://medicare.freshskyai.com/',     'category': 'healthcare'},
    {'slug': 'solarevincentives',   'brand': 'Solar + EV Incentives', 'url': 'https://solar.freshskyai.com/',        'category': 'business'},
    {'slug': 'safetyplanai',        'brand': 'Safety Plan AI',        'url': 'https://safety.freshskyai.com/',       'category': 'legal'},
    {'slug': 'mentalhealthfinder',  'brand': 'Mental Health Finder',  'url': 'https://mental.freshskyai.com/',       'category': 'healthcare'},
    {'slug': 'studentdebtai',       'brand': 'Student Debt AI',       'url': 'https://studentloan.freshskyai.com/',  'category': 'financial'},
    {'slug': 'estateplanai',        'brand': 'Estate Plan AI',        'url': 'https://estate.freshskyai.com/',       'category': 'legal'},
    {'slug': 'naturalizeprep',      'brand': 'Naturalize Prep',       'url': 'https://naturalize.freshskyai.com/',   'category': 'newcomer'},
    {'slug': 'childcarefinder',     'brand': 'Childcare Finder',      'url': 'https://childcare.freshskyai.com/',    'category': 'benefits'},
    {'slug': 'backgroundcheckhelp', 'brand': 'Background Check Help', 'url': 'https://background.freshskyai.com/',   'category': 'legal'},
    {'slug': 'foodforfamilies',     'brand': 'Food For Families',     'url': 'https://food.freshskyai.com/',         'category': 'benefits'},
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
         'The free tier covers 10 queries per day per session, no sign-in needed. Some apps offer a Pro subscription with unlimited use; the free tier is intentionally generous and not a trial.'),
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
         'Yes for the daily quota; some tools have a Pro tier for unlimited use. The free tier is meaningful, not a teaser.'),
    ],
    'healthcare': [
        ('Is this medical advice?',
         'No. This tool explains how healthcare systems and benefits work; it does not diagnose, treat, or recommend medications. For medical decisions, consult a licensed clinician.'),
        ('Will my health information be stored?',
         'No. We do not store inputs and we do not collect health data. Inputs are sent to AI providers for response generation only.'),
        ('Does this work with my insurance?',
         'The AI uses general knowledge of US insurance frameworks (Medicare, Medicaid, ACA, employer plans) to explain coverage in plain language. Always verify specific coverage decisions with your plan.'),
        ('Is this free to use?',
         'Yes for the daily quota. No sign-in required to get started.'),
    ],
    'education': [
        ('Is this approved by my school district?',
         'No formal approval is required. This is an educational tool that helps parents and teachers understand documents and prepare for meetings. It does not replace any official school process.'),
        ('Is student data stored?',
         'No. The tool does not store any input. We do not collect student PII; the AI is instructed not to ask for any.'),
        ('Does this work for IEP, 504, or ELL situations?',
         'Yes. The tools are designed around K-12 special education and English-learner contexts, with multilingual support so non-English-speaking parents can fully participate.'),
        ('Is this free for teachers and parents?',
         'The free tier is generous (10 queries per day, no sign-in). EduSafe AI offers a Teacher Pro tier ($3.99/mo or $29/yr) for unlimited use.'),
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
         'Yes for the daily quota.'),
    ],
    'business': [
        ('Is this tax or financial advice?',
         'No. This is an educational tool. For binding tax or financial decisions, consult a licensed CPA or financial advisor.'),
        ('How current is the program data?',
         'The tool reflects current federal incentive programs and major state programs. For specific dollar amounts and eligibility cutoffs, verify with the official program page before applying.'),
        ('Is my information stored?',
         'No personal information is stored.'),
        ('Free or paid?',
         'Free daily tier; intentionally generous.'),
    ],
    'financial': [
        ('Is this financial advice?',
         'No. This is an educational tool. For binding financial decisions, consult a licensed financial advisor or accountant.'),
        ('Is my financial information stored?',
         'No. We do not store inputs. Do not enter account numbers, SSN, or other identifiers.'),
        ('Does it cover federal and state programs?',
         'Yes. The tool covers federal frameworks plus major state-level variations where they materially differ.'),
        ('How much does it cost?',
         'Free for the daily quota.'),
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

    Generic about the builder (no name, no employer); slightly tailored
    by category to lead with the most relevant credential. Verifiable in
    spirit — every claim is real (active U.S. K-12 educator, licensed
    attorney with doctorate in law, Google for Education Certified
    Trainer)."""
    if category in ('education', 'flagship'):
        # Lead with educator credential
        body = ('Built by an active U.S. K-12 educator and Google for Education Certified Trainer '
                '(also a licensed attorney with a doctorate in law).')
    elif category == 'legal':
        # Lead with legal credential
        body = ('Built by a licensed attorney with a doctorate in law '
                '(also an active U.S. K-12 educator and Google for Education Certified Trainer).')
    else:
        # General phrasing for benefits/civic/healthcare/housing/financial/business/newcomer
        body = ('Built by a U.S. educator who is also a licensed attorney with a doctorate in law '
                'and Google for Education Certified Trainer credentials.')
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
    consistent social preview without needing per-app image assets."""
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
    )


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
    """One-call setup: registers SEO + revenue routes for an app. Apps just call:

        from freshsky_common.revenue import install
        install(app, slug='myapp', brand='My App',
                primary_url='https://myapp.com/', category='legal',
                description='One-line what the tool does')

    Also registers a Jinja context processor so templates can render:
      {{ ga4_snippet|safe }}   -- GA4 tag (active when GA_MEASUREMENT_ID set)
      {{ og_tags|safe }}       -- Open Graph + Twitter card
      {{ schema_json|safe }}   -- schema.org JSON-LD

    `description` is optional; when provided it's used in OG + schema.org.
    """
    if category not in CATEGORIES:
        raise ValueError(f'unknown category {category!r}; allowed: {sorted(CATEGORIES)}')
    register_seo_routes(app, slug=slug, brand=brand, primary_url=primary_url)
    register_revenue_routes(app, category)

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

    @app.context_processor
    def _inject_analytics():
        return {
            'ga4_snippet': ga4_snippet(),
            'og_tags': og,
            'schema_json': schema,
            'faq_schema': faq_schema,
            'cross_promo': cross_promo,
            'trust_line': trust_line,
            'app_slug': slug,
            'app_brand': brand,
        }
