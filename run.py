from src.app import create_app
import logging
import os


def _setup_logging():
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


app = create_app()


if __name__ == "__main__":
    # Development server (disable the auto-reloader to avoid connection resets)
    _setup_logging()
    logger = logging.getLogger("run")

    logger.debug("Starting NeuroWell AI dev server")
    db_uri = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URI")
    logger.debug("ENV summary: DATABASE set=%s, GEMINI_API_KEY set=%s", bool(db_uri), bool(os.getenv("GEMINI_API_KEY")))
    if db_uri:
        logger.debug("DATABASE_URI starts with: %s", db_uri[:60])
    else:
        logger.error("DATABASE_URI is NOT set — chat_history will not be persisted!")
    logger.debug("App debug=%s, host=%s, port=%s", True, "0.0.0.0", 5000)

    try:
        app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
    except Exception:
        logger.exception("Server crashed on start")
