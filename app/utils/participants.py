"""
Participant records.

Each participant gets a random participant_id when they consent; nothing is
written to the database before that. Research data (assignments, responses,
trial logs) is keyed by it. The `participants` collection records the site,
the participant's own answer to the first-participation question (asked on
the consent page), and completion state.

Personal data depends on the site (app.utils.sites):
  - UZH collects none.
  - UF asks for email, UFID, name and class on its start page. They are
    stored here, in an `identity` sub-document, and nowhere else: research
    records and exports never contain them.

Repeat participation is discouraged by:
  - the first-participation question (self-report, stored for analysis);
  - a long-lived browser cookie holding only the participant_id, so the
    same browser resumes an unfinished attempt and is turned away after
    finishing. Clearing cookies or switching devices bypasses it;
  - UF email uniqueness blocks duplicate enrolment but never authenticates
    a different browser. Resume requires the existing participant cookie.
"""

import datetime
import uuid

from pymongo.errors import DuplicateKeyError

from .db import get_collection

PARTICIPANT_COOKIE = "participant_id"
PARTICIPANT_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # one year

# Answers to "Have you previously started or completed this study,
# including a pilot version?" — value stored → label shown. Any answer other
# than "no" sets participated_before, the flag used to exclude earlier
# participants at analysis time.
PRIOR_PARTICIPATION_OPTIONS = {
    "no": "No.",
    "started": "Yes, I started but did not finish.",
    "completed": "Yes, I completed it.",
}


def normalize_email(email):
    return (email or "").strip().lower()


def is_valid_email(email):
    local, sep, domain = email.partition("@")
    return bool(local and sep and "." in domain and " " not in email)


def email_matches_domain(email, domain):
    """True if email's domain is `domain` or a subdomain of it."""
    email_domain = email.rpartition("@")[2]
    return email_domain == domain or email_domain.endswith("." + domain)


def _participants_collection():
    coll = get_collection("participants")
    indexes = coll.index_information()
    # An older version keyed every participant by a plain unique email
    # index. UZH records carry no email, so that index would reject every
    # UZH record after the first (a unique index treats a missing field as
    # null). Replace it with one that only covers records that have one.
    if "email_1" in indexes:
        coll.drop_index("email_1")
    coll.create_index("participant_id", unique=True)
    coll.create_index(
        "identity.email",
        name="identity_email_unique",
        unique=True,
        partialFilterExpression={"identity.email": {"$type": "string"}},
    )
    # One participant per consent form shown: a double-clicked "I Agree"
    # sends two POSTs with the same session nonce, and the second must reuse
    # the first's participant instead of creating (and allocating) another.
    coll.create_index(
        "consent_nonce",
        name="consent_nonce_unique",
        unique=True,
        partialFilterExpression={"consent_nonce": {"$type": "string"}},
    )
    return coll


def participated_before(prior_participation):
    return prior_participation != "no"


def create_participant(site, prior_participation, identity=None, consent_nonce=None):
    """
    Create a participant on consent. `identity` (UF only) is the start-page
    form: {email, uf_id, firstName, lastName, classSchool}. If that email
    already has a record (including a concurrent submit), registration is rejected.

    `consent_nonce` identifies the consent form this browser session was
    shown. A repeated submission of the same form (double click, resent
    POST) returns the participant the first submission created, so it never
    creates a second participant or a second order allocation.
    """
    coll = _participants_collection()
    if consent_nonce:
        existing = coll.find_one({"consent_nonce": consent_nonce})
        if existing is not None:
            return existing

    record = {
        "participant_id": str(uuid.uuid4()),
        "site": site,
        "prior_participation": prior_participation,
        "participated_before": participated_before(prior_participation),
        "created_at": datetime.datetime.now(datetime.timezone.utc),
        "completed_at": None,
    }
    if consent_nonce:
        record["consent_nonce"] = consent_nonce
    if identity:
        record["identity"] = identity

    while True:
        try:
            coll.insert_one(record)
            return record
        except DuplicateKeyError:
            if consent_nonce:
                # A concurrent submission of the same form won the race.
                existing = coll.find_one({"consent_nonce": consent_nonce})
                if existing is not None:
                    return existing
            if identity:
                existing = find_participant_by_email(identity["email"])
                if existing is not None:
                    raise ValueError("An existing email cannot authenticate a new browser")
            # Otherwise a uuid4 collision; vanishingly unlikely.
            record["participant_id"] = str(uuid.uuid4())


def get_participant(participant_id):
    if not participant_id:
        return None
    return _participants_collection().find_one({"participant_id": participant_id})


def find_participant_by_email(email):
    return _participants_collection().find_one({"identity.email": email})


def note_resume(participant_id):
    """Count a return after the session was lost (same browser or, for UF,
    the same email). The participant continues where their stored data
    says they were; the count is kept for reporting."""
    _participants_collection().update_one(
        {"participant_id": participant_id},
        {"$inc": {"n_resumes": 1}},
    )


def mark_completed(participant_id):
    _participants_collection().update_one(
        {"participant_id": participant_id, "completed_at": None},
        {"$set": {"completed_at": datetime.datetime.now(datetime.timezone.utc)}},
    )
