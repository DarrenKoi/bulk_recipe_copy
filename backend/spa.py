"""Copied from skewnono_v3_nuxt backend/_spa/serving.py (trimmed)."""

from pathlib import Path

from flask import Flask, abort, send_from_directory
from werkzeug.exceptions import NotFound


def register_spa(app: Flask, root: Path) -> None:
    if not root.is_dir():
        app.logger.warning("SPA dir missing at %s; skipping SPA mount", root)
        return

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_spa(path: str):
        if path.startswith("api/"):
            abort(404)
        if path:
            try:
                resp = send_from_directory(root, path)
            except NotFound:
                pass
            else:
                # Nuxt content-hashes _nuxt/, so those files never change in place.
                if path.startswith("_nuxt/"):
                    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                return resp
        return send_from_directory(root, "index.html")
