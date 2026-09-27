# Libraries
import os
import pprint
import datetime

# Imports
from dotenv import load_dotenv, find_dotenv
from pymongo import MongoClient
from urllib.parse import quote_plus


# Functions for interacting with MongoDB
def _get_client():
    # Load .env variables
    load_dotenv(find_dotenv())

    # Variables
    username = os.getenv('MONGODB_UID')
    cluster = os.getenv('MONGODB_CLUSTER_NAME')
    authSource = os.getenv('MONGODB_AUTH')
    password = quote_plus(os.getenv('MONGODB_PWD'))

    # URI string
    uri = username + ':' + password + '@' + cluster + authSource

    # Initialize MongoDB client
    return MongoClient(uri)


def get_client():
    """Expose a MongoClient for uses outside this module that need direct
    access (e.g. Flask-Session's MongoDB backend in app/__init__.py)."""
    return _get_client()


def get_collection(name):
    """Return a collection from the `test` database by name (lazy connection,
    matching init_db()'s behavior — no connection is made at import time)."""
    # Update later in production environment ---------------------------------- <<<
    return _get_client().test[name]
    # ------------------------------------------------------------------------- <<<


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
        "timestamp": datetime.datetime.now(),
        "times": responses["times"],
    }
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
