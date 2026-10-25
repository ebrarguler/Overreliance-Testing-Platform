"""
Per-participant sequence + rotation assignment.

Two concerns live here, both backed by MongoDB so they're safe across
concurrent requests and multiple gunicorn worker processes (an
in-process lock would not help — each worker has its own memory):

1. Least-filled order assignment, per site. New participants are assigned
   to one of the active orders (sequences.SEQUENCE_LABELS, currently P/Q)
   by claiming whichever has the fewest assignments so far at their site,
   choosing at random when they are tied. Balancing within each site keeps
   UZH balanced on its own even while UF collects data at the same time.
   Counts live in `sequence_counts`, one document per (design_version,
   site, label), incremented when an assignment is made — not when a
   participant completes. The claim is a compare-and-swap: the increment
   only succeeds if the counter still holds the value that was read, so
   the counter claimed is always a true minimum at claim time. Under
   contention a claim simply retries.

   Only counters for the active SEQUENCE_DESIGN_VERSION, the participant's
   site and the active labels are ever read or incremented. Counters from
   earlier designs (Option 6 A/B, which had no site, and the six-order
   S1..S6 design with no design_version) are left untouched and can never
   be selected.

2. Persistence + resume. The realized assignment (design version, label,
   type-at-position, problem-at-position, variants, seed) is written once
   per participant_id and never recomputed for it again — a returning
   participant gets back the exact same assignment, whatever design was
   active when it was made. A unique index on `participant_id` closes the
   race where two concurrent first-visits for the same participant both try
   to create one: the loser returns the winner's record and gives back the
   counter slot it claimed, so no allocation slot is permanently consumed.
   Email is never stored here — see app.utils.participants.

Records created before design versioning carry no `sequence_design_version`
field; they are legacy (S1..S6) assignments and are returned as-is, with
`sequence_design_version` None.
"""

import datetime
import random

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .db import get_collection
from .sequences import (
    SEQUENCE_DESIGN_VERSION,
    SEQUENCE_LABELS,
    SEQUENCES,
    ROTATE_PROBLEMS,
    build_trial_sequence,
)

# Tie-breaking randomness. Module-level so tests can substitute a seeded one.
_tie_break_rng = random.SystemRandom()

# Upper bound on compare-and-swap retries; each retry means another request
# claimed a slot in between, so this is only reached under extreme load.
_MAX_CLAIM_ATTEMPTS = 50


def _counter_key(site, label):
    return {"design_version": SEQUENCE_DESIGN_VERSION, "site": site, "label": label}


def _active_counter_filter(site):
    return {
        "design_version": SEQUENCE_DESIGN_VERSION,
        "site": site,
        "label": {"$in": list(SEQUENCE_LABELS)},
    }


def _sequence_counts_collection(site):
    coll = get_collection("sequence_counts")
    # Counters used to be unique per (design_version, label). Per-site
    # counters share those two fields across sites, so that index would
    # reject the second site's counter.
    if "design_version_label_unique" in coll.index_information():
        coll.drop_index("design_version_label_unique")
    # Partial so legacy counter documents (no design_version) are not
    # covered and cannot make the index build fail. Option 6 counters have
    # no site and stay unique as (version, null, label).
    coll.create_index(
        [("design_version", 1), ("site", 1), ("label", 1)],
        name="design_version_site_label_unique",
        unique=True,
        partialFilterExpression={"design_version": {"$type": "string"}},
    )
    for label in SEQUENCE_LABELS:
        try:
            coll.update_one(
                _counter_key(site, label),
                {"$setOnInsert": {"count": 0}},
                upsert=True,
            )
        except DuplicateKeyError:
            pass  # a concurrent request created it first
    return coll


def _assignments_collection():
    coll = get_collection("participant_assignments")
    # Assignments used to be keyed by email under a unique index. New records
    # carry no email, so that index would reject every record after the first
    # (a unique index treats a missing field as null). Drop it if present.
    if "email_1" in coll.index_information():
        coll.drop_index("email_1")
    coll.create_index("participant_id", unique=True)
    return coll


def _claim_least_filled_label(site):
    """
    Atomically claim one slot in the least-filled active order at this site
    and return its label. Ties are broken at random.
    """
    coll = _sequence_counts_collection(site)
    for _ in range(_MAX_CLAIM_ATTEMPTS):
        counts = {
            doc["label"]: doc["count"]
            for doc in coll.find(_active_counter_filter(site))
        }
        missing = [label for label in SEQUENCE_LABELS if label not in counts]
        if missing:
            raise RuntimeError(f"sequence_counts is missing counters for {missing}")
        lowest = min(counts.values())
        label = _tie_break_rng.choice(
            [label for label in SEQUENCE_LABELS if counts[label] == lowest]
        )
        claimed = coll.find_one_and_update(
            {**_counter_key(site, label), "count": lowest},
            {"$inc": {"count": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if claimed is not None:
            return label
    raise RuntimeError("could not claim a sequence slot; too much contention")


def _release_label(site, label):
    """Give back a slot claimed by a request that lost the assignment race."""
    _sequence_counts_collection(site).update_one(
        _counter_key(site, label),
        {"$inc": {"count": -1}},
    )


def _record_to_result(record):
    return {
        "participant_id": record["participant_id"],
        # None for legacy (pre-versioning, S1..S6) assignments.
        "sequence_design_version": record.get("sequence_design_version"),
        "sequence_label": record["sequence_label"],
        "type_at_position": record.get("type_at_position"),
        "question_order": record["problem_at_position"],
        "variant_assignments": record["variant_assignments"],
        "seed": record.get("seed"),
    }


def get_assignment(participant_id):
    """This participant's persisted assignment, or None. Never creates one."""
    if not participant_id:
        return None
    record = _assignments_collection().find_one({"participant_id": participant_id})
    return _record_to_result(record) if record is not None else None


def get_or_create_assignment(participant_id, questions, site):
    """
    Return this participant's realized assignment, creating and persisting
    one only if none exists yet for their participant_id. Never reassigns a
    participant who already has a persisted assignment — legacy or current.
    A new assignment is balanced among participants of the same `site`.

    Returns
    -------
    dict with keys: participant_id, sequence_design_version, sequence_label,
    type_at_position, question_order, variant_assignments, seed
    """
    coll = _assignments_collection()

    existing = coll.find_one({"participant_id": participant_id})
    if existing is not None:
        return _record_to_result(existing)

    label = _claim_least_filled_label(site)
    seq_index = SEQUENCE_LABELS.index(label)
    try:
        question_order, variant_assignments, seq_label, seed = build_trial_sequence(
            seq_index, questions
        )
    except Exception:
        _release_label(site, label)
        raise

    record = {
        "participant_id": participant_id,
        "site": site,
        "sequence_design_version": SEQUENCE_DESIGN_VERSION,
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
        # Lost a race against a concurrent first-visit for the same
        # participant; the winner's assignment is authoritative. Give back
        # the slot this request claimed so the counts stay equal to the
        # number of assignments actually made.
        _release_label(site, label)
        existing = coll.find_one({"participant_id": participant_id})
        return _record_to_result(existing)

    return _record_to_result(record)
