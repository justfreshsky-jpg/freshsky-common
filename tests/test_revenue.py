from flask import Flask

from freshsky_common.revenue import adsense_snippet, install, og_snippet, partners_for_category


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
    assert "Optional donations support the portfolio" in app.test_client().get("/humans.txt").text


def test_portfolio_skin_is_light_accessible_and_bounded():
    snippet = og_snippet("Test App", "https://example.com/")

    assert snippet.count('id="fs-portfolio-skin"') == 1
    assert "--fs-bg:#f4f7f8" in snippet
    assert "main section" not in snippet
    assert "min-height:44px" in snippet
    assert "min-height:48px" in snippet
    assert "prefers-reduced-motion:reduce" in snippet
    assert "futuristic dark" not in snippet


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
    assert "--fs-bg:#f4f7f8" in body
