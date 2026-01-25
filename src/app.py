from flask import Flask
from dotenv import load_dotenv
from flask_cors import CORS
import os
import logging
from flask import send_from_directory
from pathlib import Path
from authlib.integrations.flask_client import OAuth
from flask_session import Session


# Load environment variables from .env at import time so the server sees keys on start
load_dotenv()


def create_app():
    app = Flask(__name__)

    # Prefer an explicit secret for session signing. Check common env vars,
    # fall back to GEMINI_API_KEY for compatibility, and finally to a
    # development placeholder (not for production).
    secret = os.getenv('SECRET_KEY') or os.getenv('FLASK_SECRET') or os.getenv('GEMINI_API_KEY')
    if not secret:
        # Use a stable dev secret to avoid session loss during local dev restarts.
        # Warn the user to set a proper secret in production.
        secret = 'dev-secret-please-set'
        logging.getLogger('src.app').warning('No SECRET_KEY/FLASK_SECRET/GEMINI_API_KEY found — using insecure dev secret. Set SECRET_KEY in production.')

    app.config.from_mapping(SECRET_KEY=secret)
    # Session cookie settings helpful during local development:
    # - `SESSION_COOKIE_SAMESITE='Lax'` allows the session cookie to be
    #   sent on the OAuth provider's top-level redirect back to our app.
    # - `SESSION_COOKIE_SECURE=False` keeps cookies working on plain HTTP
    #   localhost during development. Set True in production when using HTTPS.
    app.config.update(
        SESSION_COOKIE_SAMESITE=os.getenv('SESSION_COOKIE_SAMESITE', 'Lax'),
        SESSION_COOKIE_SECURE=(os.getenv('SESSION_COOKIE_SECURE', 'False') == 'True'),
    )
    # Enable server-side session storage (filesystem) for local development
    # to avoid losing OAuth "state" when the browser does not send the
    # client-side cookie or the server restarts. This is optional and can
    # be overridden via env vars in production (use Redis, Memcached, etc.).
    session_dir = str(Path(__file__).resolve().parents[1] / '.flask_session')
    app.config.update(SESSION_TYPE=os.getenv('SESSION_TYPE', 'filesystem'), SESSION_FILE_DIR=session_dir)
    Session(app)

    # Register API blueprint if available
    try:
        from src.api.routes import api_bp
        app.register_blueprint(api_bp, url_prefix="/api")
        # Log registered routes for debugging
        logger = logging.getLogger("src.app")
        for rule in app.url_map.iter_rules():
            logger.debug("Registered route: %s %s", rule, list(rule.methods))
    except Exception:
        import traceback

        logger = logging.getLogger("src.app")
        logger.exception("Failed to import/register API blueprint:\n%s", traceback.format_exc())


    @app.route("/")
    def home():
        return {"status": "ok", "service": "NeuroWell AI"}

    # Initialize OAuth (Google OpenID Connect) if credentials are present
    oauth = OAuth(app)
    google_client_id = os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if google_client_id and google_client_secret:
        oauth.register(
            name="google",
            client_id=google_client_id,
            client_secret=google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        # attach to app for use by blueprints
        app.oauth = oauth

    # Serve frontend static files from repository `frontend/` folder
    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"

    from flask import session, redirect, url_for

    @app.route("/frontend/")
    def serve_frontend_index():
        # Public landing page: serve the frontend index without requiring
        # authentication so visitors can see the landing/CTA. Client JS will
        # check `/auth/me` to decide whether to prompt for sign-in.
        return send_from_directory(str(frontend_dir), "index.html")

    @app.route("/frontend/<path:filename>")
    def serve_frontend_file(filename):
        # Serve frontend static files publicly (landing assets). Authentication
        # is handled client-side for the landing experience.
        return send_from_directory(str(frontend_dir), filename)

    @app.route("/logged_out")
    def serve_logged_out():
        # Public signed-out page; do not require authentication so the
        # user can land here immediately after logout.
        return send_from_directory(str(frontend_dir), "logged_out.html")
    # Register auth blueprint if available
    try:
        from src.auth.routes import auth_bp
        app.register_blueprint(auth_bp)
    except Exception:
        logging.getLogger('src.app').exception('Failed to register auth blueprint')

    return app
