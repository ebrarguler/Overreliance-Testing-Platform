"""Debrief decisions are stored separately from research answers."""
import datetime
import os
import secrets
from . import db


def withdrawal_code(participant_id):
    coll = db.get_collection("participants")
    part = coll.find_one({"participant_id": participant_id})
    if not part:
        return None
    if not part.get("withdrawal_code"):
        coll.update_one({"participant_id": participant_id, "withdrawal_code": {"$exists": False}},
                        {"$set": {"withdrawal_code": secrets.token_urlsafe(24)}})
    return coll.find_one({"participant_id": participant_id}).get("withdrawal_code")


def choose(participant_id, choice):
    """Withdrawal is irreversible through the participant UI."""
    if choice not in {"keep", "withdraw"}:
        raise ValueError("Invalid data-use choice")
    part = db.get_collection("participants").find_one({"participant_id": participant_id})
    if not part or part.get("data_use") == "withdraw":
        return False
    now = datetime.datetime.now(datetime.timezone.utc)
    result = db.get_collection("participants").update_one(
        {"participant_id": participant_id, "data_use": {"$ne": "withdraw"}},
        {"$set": {"data_use": choice, "data_use_decided_at": now}},
    )
    if not result.matched_count:
        return False
    # Export treats participants as authoritative, including a crash between
    # these writes. The users copy is useful for standalone archived exports.
    db.get_collection("users").update_one({"participant_id": participant_id, "data_use": {"$ne": "withdraw"}},
                                         {"$set": {"data_use": choice}})
    return True


def deadline():
    return os.getenv("WITHDRAWAL_DEADLINE", "")


def withdrawal_open():
    value = deadline()
    if not value:
        return True  # pilot: no arbitrary unannounced cut-off
    return datetime.datetime.now(datetime.timezone.utc).date() <= datetime.date.fromisoformat(value)
