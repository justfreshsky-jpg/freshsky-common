from flask import Flask

from freshsky_common.revenue import adsense_snippet, install, partners_for_category


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
