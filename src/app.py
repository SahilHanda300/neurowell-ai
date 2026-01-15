from flask import Flask
from dotenv import load_dotenv
import os


# Load environment variables from .env at import time so the server sees keys on start
load_dotenv()


def create_app():
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("GEMINI_API_KEY"),
    )

    # Register API blueprint if available
    try:
        from src.api.routes import api_bp
        app.register_blueprint(api_bp, url_prefix="/api")
    except Exception:
        pass

    @app.route("/")
    def home():
        return {"status": "ok", "service": "NeuroWell AI"}

    return app
