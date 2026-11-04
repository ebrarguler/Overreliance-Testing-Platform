# Necessary imports
import os
from flask import Flask
from flask_session import Session

# Function to initialize application
def create_app():
    # Imported here, not at module top, so that importing submodules of this
    # package (e.g. `app.utils.sequences` from a test) doesn't force routes.py
    # — and everything it imports, including the question bank's startup
    # validation — to run as a side effect. The app itself still validates
    # fully the moment it's actually created.
    from .routes import main_bp
    from .utils.db import get_client, database_name

    from .utils.readiness import validate_launch
    validate_launch()

    # Initialize application
    app = Flask(__name__)

    # Initialize environment variable
    app.secret_key = os.getenv('APP_SECRET_KEY')
    if not app.secret_key:
        raise RuntimeError('APP_SECRET_KEY must be configured')

    # MongoDB sessions hold assignment/cursor and survey caches. Research
    # answers and chat are written independently by trial_store, so parallel
    # model requests cannot overwrite them through a stale session snapshot.
    app.config["SESSION_TYPE"] = "mongodb"
    app.config["SESSION_MONGODB"] = get_client()
    app.config["SESSION_SERIALIZATION_FORMAT"] = "json"  # avoids an extra msgpack dependency
    app.config["SESSION_MONGODB_DB"] = database_name()
    app.config["SESSION_REFRESH_EACH_REQUEST"] = False
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("STUDY_PHASE", "pilot") == "main"
    Session(app)

    # Load configuration for application
    # app.config.from_object('config.Config')

    # Register blueprints and initialize routes
    app.register_blueprint(main_bp)

    # Return application
    return app