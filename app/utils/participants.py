"""
Participant identity store — the only place email is kept.

Research data (assignments, responses, trial logs) is keyed by the random
participant_id and never carries email. The `participants` collection holds
the email ↔ participant_id link, the recruitment site, and completion state,
which is what lets a returning email resume an unfinished attempt or be
turned away after finishing. Standard exports read only the research
collections, so email never reaches them.
"""

import datetime
import uuid

from pymongo.errors import DuplicateKeyError

from .db import get_collection


def normalize_email(email):
    return (email or "").strip().lower()


def is_valid_email(email):
    local, sep, domain = email.partition("@")
    return bool(local and sep and "." in domain and " " not in email)


def email_matches_domain(email, domain):
    """True if email's domain is `domain` or a subdomain of it."""
    domain = domain.lower().lstrip("@")
    email_domain = email.rpartition("@")[2]
    return email_domain == domain or email_domain.endswith("." + domain)


def _participants_collection():
    coll = get_collection("participants")
    coll.create_index("email", unique=True)
    coll.create_index("participant_id", unique=True)
    return coll


def get_or_create_participant(email, site):
    """
    Return the participant record for a normalized email, creating one with
    a fresh participant_id if this email hasn't been seen. A returning email
    keeps its original participant_id and site, whichever link it used now.
    """
    coll = _participants_collection()

    existing = coll.find_one({"email": email})
    if existing is not None:
        return existing

    record = {
        "email": email,
        "participant_id": str(uuid.uuid4()),
        "site": site,
        "created_at": datetime.datetime.utcnow(),
        "completed_at": None,
    }
    try:
        coll.insert_one(record)
    except DuplicateKeyError:
        # Lost a race against a concurrent first visit with the same email.
        return coll.find_one({"email": email})
    return record


def mark_completed(participant_id):
    _participants_collection().update_one(
        {"participant_id": participant_id, "completed_at": None},
        {"$set": {"completed_at": datetime.datetime.utcnow()}},
    )
