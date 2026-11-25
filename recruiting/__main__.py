import os

from . import create_app

app = create_app()

if __name__ == "__main__":
    debug_enabled = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host="127.0.0.1", port=8080, debug=debug_enabled)
