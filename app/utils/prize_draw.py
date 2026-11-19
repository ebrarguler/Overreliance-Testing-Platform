"""Optional prize-draw entry at the end of the study, kept apart from research data.

Emails go to their own collection, `prize_draw_entries`, as
`{_id: random token, email, site}`: no participant_id and no timestamp (a
random `_id` instead of an ObjectId, which would carry the insertion time).
The participant record only notes `prize_draw_entered: True`, so the page can
confirm the entry and each participant enters once. Research exports never
read this collection. Delete it after the draw.

Joining the draw does not depend on the keep/withdraw choice.
Set PRIZE_DRAW_ENABLED=0 to hide the draw and its consent sentence.
"""
import os
import re
import secrets

from pymongo.errors import DuplicateKeyError

from . import db

MAX_EMAIL_LENGTH = 254
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def enabled():
    return os.getenv("PRIZE_DRAW_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def normalise(email):
    return (email or "").strip().lower()


def is_valid(email):
    return len(email) <= MAX_EMAIL_LENGTH and bool(_EMAIL.match(email))


def _entries():
    coll = db.get_collection("prize_draw_entries")
    coll.create_index("email", unique=True)
    return coll


def enter(participant_id, site, email):
    """Store a normalised, valid email once per participant. An email that is
    already in the draw is not stored twice."""
    participants = db.get_collection("participants")
    if (participants.find_one({"participant_id": participant_id}) or {}).get("prize_draw_entered"):
        return
    try:
        _entries().insert_one({"_id": secrets.token_hex(12), "email": email, "site": site})
    except DuplicateKeyError:
        pass
    participants.update_one({"participant_id": participant_id}, {"$set": {"prize_draw_entered": True}})
