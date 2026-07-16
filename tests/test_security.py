from flask import Flask

from freshsky_common.security import install_security_headers


def test_private_routes_are_no_store_and_noindex():
    app = Flask(__name__)

    @app.get("/result")
    def result():
        return "private"

    install_security_headers(app, no_store_paths=("/result",))
    response = app.test_client().get("/result")
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
    assert response.headers["X-Frame-Options"] == "DENY"
