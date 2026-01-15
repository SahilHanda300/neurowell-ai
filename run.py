from src.app import create_app


app = create_app()


if __name__ == "__main__":
    # Development server (disable the auto-reloader to avoid connection resets)
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
