"""
Per-participant sequence + rotation assignment (Phase 2).

Two concerns live here, both backed by MongoDB so they're safe across
concurrent requests and multiple gunicorn worker processes (an
in-process lock would not help — each worker has its own memory):

1. Least-filled sequence assignment. Participants are assigned to S1..S6
   by claiming whichever sequence currently has the fewest participants,
   via an atomic `find_one_and_update` (MongoDB executes the find + sort +
   increment as one atomic server-side operation), not a random draw and
   not a plain `count % 6` (which races under concurrent sign-ups since
   completed-participant count only advances at the very end of the study).

2. Persistence + resume. The realized assignment (sequence label,
   type-at-position, problem-at-position, seed) is written once per email
   and never recomputed for that email again — a participant who reloads
   `/` or returns before finishing gets back the exact same assignment,
   never a new one. A unique index on `email` plus catching the resulting
   DuplicateKeyError closes the race where two concurrent first-visits for
   the same brand-new email would otherwise both try to create one.
"""

import datetime
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .db import get_collection
from .sequences import SEQUENCE_LABELS, SEQUENCES, ROTATE_PROBLEMS, build_trial_sequence


def _sequence_counts_collection():
    coll = get_collection("sequence_counts")
    for label in SEQUENCE_LABELS:
        coll.update_one({"label": label}, {"$setOnInsert": {"count": 0}}, upsert=True)
    return coll


def _assignments_collection():
    coll = get_collection("participant_assignments")
    coll.create_index("email", unique=True)
    return coll


def _claim_least_filled_sequence_index():
    """Atomically increment and return the index of the least-filled sequence."""
    coll = _sequence_counts_collection()
    doc = coll.find_one_and_update(
        {},
        {"$inc": {"count": 1}},
        sort=[("count", 1), ("label", 1)],
        return_document=ReturnDocument.AFTER,
    )
    return SEQUENCE_LABELS.index(doc["label"])


def _record_to_result(record):
    return {
        "question_order": record["problem_at_position"],
        "variant_assignments": record["variant_assignments"],
        "sequence_label": record["sequence_label"],
        # Stable, non-PII id for joining trial logs to a participant without
        # keying on email — trial_logs should never need to carry raw email.
        "participant_id": record["participant_id"],
    }


def get_or_create_assignment(email, questions):
    """
    Return this participant's realized assignment, creating and persisting
    one only if none exists yet for their email. Never reassigns an email
    that already has a persisted assignment — this is the "resume" path.

    Returns
    -------
    dict with keys: question_order, variant_assignments, sequence_label
    """
    coll = _assignments_collection()

    existing = coll.find_one({"email": email})
    if existing is not None:
        return _record_to_result(existing)

    seq_index = _claim_least_filled_sequence_index()
    question_order, variant_assignments, seq_label, seed = build_trial_sequence(
        seq_index, questions
    )

    record = {
        "email": email,
        "participant_id": str(uuid.uuid4()),
        "sequence_label": seq_label,
        "type_at_position": list(SEQUENCES[seq_index]),
        "problem_at_position": question_order,
        "variant_assignments": variant_assignments,
        "seed": seed,
        "rotate_problems": ROTATE_PROBLEMS,
        "assigned_at": datetime.datetime.utcnow(),
    }

    try:
        coll.insert_one(record)
    except DuplicateKeyError:
        # Lost a race against a concurrent first-visit for the same email;
        # the winner's assignment is authoritative — use it, discard ours.
        existing = coll.find_one({"email": email})
        return _record_to_result(existing)

    return _record_to_result(record)
