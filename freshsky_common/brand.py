"""Shared Fresh Sky visual-system assets.

Apps keep their product-specific HTML and controls.  This helper exposes a
small, progressively enhanced stylesheet that gives every Fresh Sky surface a
consistent shell without making the app depend on another domain at runtime.
"""
from __future__ import annotations

from importlib import resources

from flask import Flask, Response

BRAND_CSS_VERSION = "0.6.7"


def install_brand_assets(app: Flask) -> None:
    """Expose the shared visual system once per Flask application."""
    if "freshsky_brand_css" not in app.view_functions:
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

        app.add_url_rule(
            "/freshsky.css",
            "freshsky_brand_css",
            _brand_css,
            methods=["GET"],
        )

    if "freshsky_interface_js" not in app.view_functions:
        def _interface_js() -> Response:
            try:
                content = (
                    resources.files("freshsky_common.static")
                    .joinpath("interface.js")
                    .read_text(encoding="utf-8")
                )
            except (OSError, TypeError):
                content = ""
            response = Response(
                content,
                mimetype="text/javascript; charset=utf-8",
            )
            response.headers["Cache-Control"] = "public, max-age=3600"
            return response

        app.add_url_rule(
            "/freshsky-interface.js",
            "freshsky_interface_js",
            _interface_js,
            methods=["GET"],
        )
