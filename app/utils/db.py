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


def find_user(user_email):

    users_collection = init_db()
    users = users_collection.find()

    for user in users:
        # Assign email
        email = user.get("email")

        # Compare email
        if email == user_email:
            return True
  
    return False

def insert_user(user_email):
    users_collection = init_db()

    # Check if user already exists
    if find_user(user_email):
        print("User already exists!")
        return (-1)
    
    # Assign email otherwise
    email = user_email

    data = {
        "email": email
    }

    # Insert a new user into the database
    result = users_collection.insert_one(data)
    print("User successfully added!")
    return result.inserted_id

def update_user(user_email, updated_data):
    users_collection = init_db()
    # Update user data by email
    return users_collection.update_one({"_id": user_email}, {"$set": updated_data})

def delete_user(user_email):
    users_collection = init_db()
    # Delete a user by email
    return users_collection.delete_one({"_id": user_email})

def insert_user_response(responses):

    _id = insert_user(responses["email"])

    # Check if user already exist in db
    if (_id == -1):
        print("User already exist!")
        return

    # responses["uf_id"]
    # responses["question_order"]
    # responses["answers"]
    # responses["post_survey_answers"]
    # responses["final_survey_answers"]
    # responses["chat_history"]

    users_collection = init_db()
    from bson.objectid import ObjectId
   

    query = {"_id": _id}

    update = {
        "$set": {
            "uf_id": responses["uf_id"],
            "participant_id": responses.get("participant_id"),
            "sequence_label": responses["sequence_label"],
            "question_order": responses["question_order"],
            "variant_assignments": responses["variant_assignments"],
            "answers": responses["answers"],
            "pre_survey_answers": responses["pre_survey_answers"],
            "post_survey_answers": responses["post_survey_answers"],
            "final_survey_answers": responses["final_survey_answers"],
            "chat_history": responses["chat_history"],
            "chat_transcript": responses.get("chat_transcript"),
            "firstName" : responses["firstName"],
            "lastName" : responses["lastName"],
            "classSchool" : responses["classSchool"],
            "demographics" : responses["demographics"],
            "timestamp" : datetime.datetime.now(),
            "times" : responses["times"],
        }
    }
    result = users_collection.update_one(query, update)

    if result:
        print("success!")
    else:
        print("failed!")


# Add more functions as needed
