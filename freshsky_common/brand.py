"""Shared Fresh Sky visual-system assets.

Apps keep their product-specific HTML and controls.  This helper exposes a
small, progressively enhanced stylesheet that gives every Fresh Sky surface a
consistent shell without making the app depend on another domain at runtime.
"""
from __future__ import annotations

from importlib import resources

from flask import Flask, Response


def install_brand_assets(app: Flask) -> None:
    """Expose the shared visual system once per Flask application."""
    endpoint = "freshsky_brand_css"
    if endpoint in app.view_functions:
        return

    def _brand_css() -> Response:
        try:
            content = (
                resources.files("freshsky_common.static")
                .joinpath("freshsky.css")
                .read_text(encoding="utf-8")
            )
        except (OSError, TypeError):
            content = ""
        response = Response(content, mimetype="text/css; charset=utf-8")
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    app.add_url_rule("/freshsky.css", endpoint, _brand_css, methods=["GET"])
