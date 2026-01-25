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
    logger.debug("ENV summary: DATABASE set=%s, GEMINI_API_KEY set=%s", bool(os.getenv("DATABASE_URL") or os.getenv("DATABASE_URI")), bool(os.getenv("GEMINI_API_KEY")))
    logger.debug("App debug=%s, host=%s, port=%s", True, "0.0.0.0", 5000)

    try:
        app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
    except Exception:
        logger.exception("Server crashed on start")
