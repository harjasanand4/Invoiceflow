"""InvoiceFlow: automated invoice processing with human-in-the-loop review."""
from __future__ import annotations

import hmac
import logging

from flask import Flask, Response, jsonify, request, send_from_directory

from app.config import BASE_DIR, Settings
from app.db import Session, create_tables, init_engine
from app.extraction import build_extractor
from app.storage import build_storage

WEB_DIST = BASE_DIR / "web" / "dist"


def create_app(settings: Settings | None = None, extractor=None, storage=None) -> Flask:
    settings = settings or Settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = Flask(__name__, static_folder=None)
    app.config["SETTINGS"] = settings
    app.config["MAX_CONTENT_LENGTH"] = settings.max_upload_mb * 1024 * 1024

    init_engine(settings.database_url)
    create_tables()
    app.config["STORAGE"] = storage or build_storage(settings)
    app.config["EXTRACTOR"] = extractor or build_extractor(settings)

    from app.api import api

    app.register_blueprint(api)

    if settings.basic_auth_user and settings.basic_auth_password:
        user, password = settings.basic_auth_user, settings.basic_auth_password

        @app.before_request
        def require_login():
            if request.path == "/api/health":  # load balancers need this without a password
                return None
            auth = request.authorization
            if (
                auth
                and hmac.compare_digest(auth.username or "", user)
                and hmac.compare_digest(auth.password or "", password)
            ):
                return None
            return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="InvoiceFlow"'})

    @app.teardown_appcontext
    def remove_session(exc=None):  # noqa: ARG001
        Session.remove()

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({"error": f"File too large (max {settings.max_upload_mb} MB)"}), 413

    # Serve the built React app (web/dist) for every non-API path.
    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def spa(path: str):
        if not WEB_DIST.exists():
            return (
                "<h3>InvoiceFlow API is running.</h3>"
                "<p>Build the UI with <code>cd web && npm install && npm run build</code>, "
                "or run <code>npm run dev</code> for development.</p>"
            )
        target = WEB_DIST / path
        if path and target.is_file() and WEB_DIST.resolve() in target.resolve().parents:
            return send_from_directory(WEB_DIST, path)
        return send_from_directory(WEB_DIST, "index.html")

    return app
