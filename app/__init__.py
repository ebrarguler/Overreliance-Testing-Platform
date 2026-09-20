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
    from .utils.db import get_client

    # Initialize application
    app = Flask(__name__)

    # Initialize environment variable
    app.secret_key = os.getenv('APP_SECRET_KEY')

    # Server-side sessions: the per-trial data now stored in session["answers"]
    # (timestamps, chat transcripts, covariates — see app.utils.measures) is
    # far too large for Flask's default signed-cookie session, which caps out
    # around 4KB and fails *silently* past that (the browser just drops the
    # cookie update, corrupting the participant's progress mid-study). Session
    # data itself lives server-side in MongoDB; the cookie only holds a session id.
    app.config["SESSION_TYPE"] = "mongodb"
    app.config["SESSION_MONGODB"] = get_client()
    app.config["SESSION_SERIALIZATION_FORMAT"] = "json"  # avoids an extra msgpack dependency
    Session(app)

    # Load configuration for application
    # app.config.from_object('config.Config')

    # Register blueprints and initialize routes
    app.register_blueprint(main_bp)

    # Return application
    return app