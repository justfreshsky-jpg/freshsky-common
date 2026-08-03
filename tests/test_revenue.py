from flask import Flask

from freshsky_common.revenue import (
    adsense_snippet,
    cross_promo_html,
    install,
    install_visuals,
    og_snippet,
    partners_for_category,
    schema_snippet,
    trust_line_html,
)


def test_commercial_revenue_paths_stay_disabled(monkeypatch):
    monkeypatch.setenv("ADSENSE_CLIENT_ID", "ca-pub-123456789")
    monkeypatch.setenv("PARTNERS_URL", "https://example.com/partners.json")

    assert adsense_snippet("business") == ""
    assert partners_for_category("business") == []


def test_affiliate_compatibility_endpoint_is_empty():
    app = Flask(__name__)
    install(
        app,
        slug="test-app",
        brand="Test App",
        primary_url="https://example.com/",
        category="business",
    )

    response = app.test_client().get("/api/affiliates")

    assert response.status_code == 200
    assert response.get_json() == {"disclosure": "", "partners": []}
    humans = app.test_client().get("/humans.txt").text
    assert "three free AI previews" in humans
    assert "civic volunteer tools remain free" in humans


def test_portfolio_skin_is_future_ready_accessible_and_bounded():
    snippet = og_snippet("Test App", "https://example.com/")

    assert snippet.count('id="fs-portfolio-skin"') == 1
    assert "--fs-bg:#050816" in snippet
    assert "color-scheme:dark" in snippet
    assert "linear-gradient(135deg,#5ee7f7,#7c8cff)" in snippet
    assert "main section" not in snippet
    assert "min-height:44px" in snippet
    assert "button{min-height:44px}" in snippet
    assert "min-height:48px" in snippet
    assert "prefers-reduced-motion:reduce" in snippet


def test_install_injects_portfolio_skin_once_for_plain_html():
    app = Flask(__name__)

    @app.route("/")
    def index():
        return "<html><head><title>Test</title></head><body>Ready</body></html>"

    install(
        app,
        slug="test-app",
        brand="Test App",
        primary_url="https://example.com/",
        category="business",
    )

    response = app.test_client().get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert body.count('id="fs-portfolio-skin"') == 1
    assert "--fs-bg:#050816" in body


def test_visual_only_install_adds_no_routes_or_named_cross_promotion():
    app = Flask(__name__)

    @app.route("/")
    def index():
        return "<html><head></head><body>Ready</body></html>"

    install_visuals(app)

    assert "--fs-bg:#050816" in app.test_client().get("/").text
    assert app.test_client().get("/robots.txt").status_code == 404
    promo = cross_promo_html("foia", "legal")
    assert "Fresh Sky AI catalog" in promo
    assert "Small Claims" not in promo


def test_trust_line_exposes_shared_privacy_terms_and_contact_paths():
    line = trust_line_html("legal")

    assert 'href="/privacy"' in line
    assert 'href="/terms"' in line
    assert 'href="https://www.freshskyai.com/contact"' in line
    assert 'target="_blank" rel="noopener"' in line


def test_trust_line_tailors_high_stakes_decision_boundaries():
    expected = {
        "legal": "Not legal advice",
        "financial": "Not financial or investment advice",
        "healthcare": "Not medical advice",
        "benefits": "not an eligibility determination",
        "housing": "not an eligibility or legal determination",
        "education": "de-identified scenarios only",
        "newcomer": "Not immigration advice",
        "civic": "not affiliated with a public agency",
        "business": "No guarantee of funding",
    }

    for category, phrase in expected.items():
        assert phrase in trust_line_html(category)


def test_trust_line_uses_exact_focus_or_civic_monthly_access_contract():
    focus = trust_line_html("legal", "eviction")
    civic = trust_line_html("civic", "capr")

    assert "Three previews are included" in focus
    assert "Focus is $9.99 per month" in focus
    assert "Civic" not in focus
    assert "Three previews are included" in civic
    assert "Civic is $14.99 per month" in civic
    assert "Focus" not in civic
    assert "no automatic overage charges" in focus.lower()
    assert "no automatic overage charges" in civic.lower()


def test_schema_uses_the_assigned_monthly_plan_instead_of_a_false_free_offer():
    focus = schema_snippet("Eviction Help", "https://eviction.freshskyai.com", "legal", slug="eviction")
    civic = schema_snippet("CAPR Search", "https://capr.freshskyai.com", "civic", slug="capr")

    assert '"price":"9.99"' in focus
    assert "Focus monthly access after previews" in focus
    assert '"price":"14.99"' in civic
    assert "Civic monthly access after previews" in civic
    assert '"price":"0"' not in focus + civic
