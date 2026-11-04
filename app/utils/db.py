# Libraries
import os
import datetime
import threading

# Imports
from dotenv import load_dotenv, find_dotenv
from pymongo import MongoClient
from urllib.parse import quote_plus

# One client per process. MongoClient is thread-safe and keeps its own
# connection pool; creating a new one on every call (as before) opened
# several pools per request and never closed them.
_client = None
_client_lock = threading.Lock()


# Functions for interacting with MongoDB
def _get_client():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                # Load .env variables
                load_dotenv(find_dotenv())

                # Variables
                username = os.getenv('MONGODB_UID')
                cluster = os.getenv('MONGODB_CLUSTER_NAME')
                authSource = os.getenv('MONGODB_AUTH')
                raw_password = os.getenv('MONGODB_PWD')
                if not all((username, cluster, authSource, raw_password)):
                    raise RuntimeError('MongoDB connection settings are incomplete')
                password = quote_plus(raw_password)

                # URI string
                uri = username + ':' + password + '@' + cluster + authSource

                _client = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    return _client


def get_client():
    """Expose a MongoClient for uses outside this module that need direct
    access (e.g. Flask-Session's MongoDB backend in app/__init__.py)."""
    return _get_client()


def database_name():
    """MONGODB_DB, default "test". A separate database per study phase
    (e.g. pilot vs main) keeps their records apart."""
    return os.getenv('MONGODB_DB', 'test')


def get_collection(name):
    """Return a collection from the study database by name (lazy
    connection — no connection is made at import time)."""
    client = _get_client()
    return client[database_name()][name]


def init_db():
    return get_collection('users')


def get_participant_count():
    """Return the number of completed participant records in the collection."""
    users_collection = init_db()
    return users_collection.count_documents({})


def insert_user_response(responses):
    """
    Write one participant's research record, keyed by participant_id.
    Carries no email or other identifying fields — email lives only in the
    `participants` collection (app.utils.participants). A repeat submit for
    the same participant_id is a no-op, so the first write wins.
    """
    users_collection = init_db()
    # Partial index: legacy records written before participant_id existed
    # lack the field and must not collide with each other as nulls.
    users_collection.create_index(
        "participant_id",
        unique=True,
        partialFilterExpression={"participant_id": {"$type": "string"}},
    )

    record = {
        "participant_id": responses["participant_id"],
        "site": responses["site"],
        "prior_participation": responses.get("prior_participation"),
        "participated_before": responses.get("participated_before"),
        "sequence_label": responses["sequence_label"],
        "question_order": responses["question_order"],
        "variant_assignments": responses["variant_assignments"],
        "answers": responses["answers"],
        "pre_survey_answers": responses["pre_survey_answers"],
        "post_survey_answers": responses["post_survey_answers"],
        "final_survey_answers": responses["final_survey_answers"],
        "chat_history": responses["chat_history"],
        "chat_transcript": responses.get("chat_transcript"),
        "demographics": responses["demographics"],
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "times": responses["times"],
        # How many trials were answered and rated, out of how many, as
        # assembled from the per-trial records (app.utils.trial_store).
        "data_integrity": responses.get("data_integrity"),
        "n_resumes": responses.get("n_resumes", 0),
        "runtime": responses.get("runtime", {}),
        "data_use": responses.get("data_use", "keep"),
    }
    # Design metadata from the persisted assignment (app.utils.assignment).
    # Written only when known, so legacy records are never given a value
    # they did not have.
    for key in ("sequence_design_version", "type_at_position", "rotation_seed",
                "rotation_scheme"):
        if key in responses:
            record[key] = responses[key]
    result = users_collection.update_one(
        {"participant_id": record["participant_id"]},
        {"$setOnInsert": record},
        upsert=True,
    )
    if result.upserted_id is None:
        print("Response already recorded for this participant.")
    else:
        print("success!")


# Add more functions as needed
