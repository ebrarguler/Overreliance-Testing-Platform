"""
Tests for order allocation and assignment persistence (app/utils/assignment.py)
against the in-memory fake MongoDB in tests/fake_mongo.py.
"""

import copy
import random
import threading
from unittest.mock import patch

import pytest

from app.utils import assignment as assignment_module
from app.utils.assignment import get_assignment, get_or_create_assignment
from app.utils.questions import questions
from app.utils.sequences import (
    LEGACY_SEQUENCES,
    SEQUENCES,
    SEQUENCE_DESIGN_VERSION,
    SEQUENCE_LABELS,
    build_trial_sequence,
)
from tests.fake_mongo import FakeDB


@pytest.fixture
def db():
    fake = FakeDB()
    with patch("app.utils.assignment.get_collection", fake.get_collection):
        yield fake


def _active_counts(db):
    coll = db.get("sequence_counts")
    return {
        d["label"]: d["count"]
        for d in (coll.docs if coll else [])
        if d.get("design_version") == SEQUENCE_DESIGN_VERSION
    }


def _assign(n, prefix="p"):
    return [get_or_create_assignment(f"{prefix}{i}", questions) for i in range(n)]


# --- what is persisted --------------------------------------------------------


def test_new_assignment_persists_design_metadata(db):
    result = get_or_create_assignment("p1", questions)
    (record,) = db["participant_assignments"].docs

    assert record["sequence_design_version"] == SEQUENCE_DESIGN_VERSION == "option6_v1"
    assert record["sequence_label"] in SEQUENCE_LABELS
    seq_index = SEQUENCE_LABELS.index(record["sequence_label"])
    assert record["type_at_position"] == SEQUENCES[seq_index]
    assert len(record["problem_at_position"]) == 14
    assert [record["variant_assignments"][str(q)] for q in record["problem_at_position"]] == (
        record["type_at_position"]
    )
    assert isinstance(record["seed"], int)

    assert result["sequence_design_version"] == "option6_v1"
    assert result["question_order"] == record["problem_at_position"]
    assert result["type_at_position"] == record["type_at_position"]
    assert result["seed"] == record["seed"]


def test_persisted_seed_reproduces_the_question_mapping(db):
    get_or_create_assignment("p1", questions)
    (record,) = db["participant_assignments"].docs
    seq_index = SEQUENCE_LABELS.index(record["sequence_label"])
    order, variants, _, _ = build_trial_sequence(seq_index, questions, seed=record["seed"])
    assert order == record["problem_at_position"]
    assert variants == record["variant_assignments"]


def test_repeat_request_returns_same_assignment_and_uses_no_slot(db):
    first = get_or_create_assignment("p1", questions)
    for _ in range(5):
        assert get_or_create_assignment("p1", questions) == first
    assert sum(_active_counts(db).values()) == 1
    assert len(db["participant_assignments"].docs) == 1


def test_get_assignment_never_creates_one(db):
    assert get_assignment("nobody") is None
    assert get_assignment(None) is None
    assert not db["participant_assignments"].docs


# --- balance and tie-breaking -------------------------------------------------


def test_allocation_stays_balanced(db):
    for i in range(1, 61):
        get_or_create_assignment(f"p{i}", questions)
        counts = _active_counts(db)
        assert max(counts.values()) - min(counts.values()) <= 1
    assert _active_counts(db) == {"A": 30, "B": 30}

    labels = [d["sequence_label"] for d in db["participant_assignments"].docs]
    assert labels.count("A") == labels.count("B") == 30


def test_counts_are_based_on_assignments_not_completions(db):
    _assign(3)
    # Nobody has completed (no `users` records), yet every assignment counts.
    assert sum(_active_counts(db).values()) == 3


def test_less_filled_order_is_chosen_without_randomness(db, monkeypatch):
    get_or_create_assignment("p0", questions)
    (first,) = [d["sequence_label"] for d in db["participant_assignments"].docs]

    offered = []

    class Recorder:
        def choice(self, options):
            offered.append(list(options))
            return options[0]

    monkeypatch.setattr(assignment_module, "_tie_break_rng", Recorder())
    get_or_create_assignment("p1", questions)
    other = "B" if first == "A" else "A"
    assert offered == [[other]]


def test_ties_are_broken_at_random(monkeypatch):
    first_labels = set()
    offered = []
    for seed in range(30):
        rng = random.Random(seed)

        class Recorder:
            def choice(self, options):
                offered.append(list(options))
                return rng.choice(options)

        fake = FakeDB()
        with patch("app.utils.assignment.get_collection", fake.get_collection):
            monkeypatch.setattr(assignment_module, "_tie_break_rng", Recorder())
            first_labels.add(get_or_create_assignment("p", questions)["sequence_label"])
    # Every first assignment is a tie between both orders...
    assert all(o == ["A", "B"] for o in offered)
    # ...and the draw lands on both, rather than always the same one.
    assert first_labels == {"A", "B"}


# --- legacy counters and assignments ------------------------------------------


def _add_legacy_counters(db, count=0):
    coll = db.get_collection("sequence_counts")
    for label in LEGACY_SEQUENCES:
        coll.docs.append({"label": label, "count": count})
    # An unversioned counter that happens to share a new label must not be
    # read either.
    coll.docs.append({"label": "A", "count": -100})
    return copy.deepcopy(coll.docs)


def test_old_counters_cannot_affect_new_allocations(db):
    before = _add_legacy_counters(db, count=0)
    _assign(10)

    labels = {d["sequence_label"] for d in db["participant_assignments"].docs}
    assert labels <= set(SEQUENCE_LABELS)
    assert _active_counts(db) == {"A": 5, "B": 5}

    legacy_now = [d for d in db["sequence_counts"].docs if "design_version" not in d]
    assert legacy_now == before  # untouched: not reset, not incremented, not deleted


def test_legacy_assignment_is_returned_unchanged_and_never_overwritten(db):
    legacy = {
        "participant_id": "legacy-1",
        "sequence_label": "S3",
        "type_at_position": LEGACY_SEQUENCES["S3"],
        "problem_at_position": list(range(14)),
        "variant_assignments": {str(i): t for i, t in enumerate(LEGACY_SEQUENCES["S3"])},
        "seed": 123,
        "rotate_problems": True,
    }
    db.get_collection("participant_assignments").docs.append(copy.deepcopy(legacy))

    result = get_or_create_assignment("legacy-1", questions)
    assert result["sequence_design_version"] is None
    assert result["sequence_label"] == "S3"
    assert result["question_order"] == legacy["problem_at_position"]
    assert result["variant_assignments"] == legacy["variant_assignments"]

    (stored,) = db["participant_assignments"].docs
    assert stored == legacy
    assert sum(_active_counts(db).values()) == 0


# --- concurrency ----------------------------------------------------------------


def _run_threads(n, target):
    errors = []

    def wrapped(i):
        try:
            target(i)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors


def test_concurrent_new_participants_stay_balanced(db):
    _run_threads(40, lambda i: get_or_create_assignment(f"p{i}", questions))

    assert len(db["participant_assignments"].docs) == 40
    assert _active_counts(db) == {"A": 20, "B": 20}


def test_concurrent_requests_for_one_participant_give_one_assignment(db, monkeypatch):
    """Force every request past the "already assigned?" check before any of
    them inserts, so they all claim a slot and race on the insert."""
    n = 8
    barrier = threading.Barrier(n, timeout=10)
    real_build = assignment_module.build_trial_sequence

    def build_after_everyone_claimed(*args, **kwargs):
        barrier.wait()
        return real_build(*args, **kwargs)

    monkeypatch.setattr(assignment_module, "build_trial_sequence", build_after_everyone_claimed)

    results = [None] * n

    def request(i):
        results[i] = get_or_create_assignment("same-person", questions)

    _run_threads(n, request)

    assert len(db["participant_assignments"].docs) == 1
    assert all(r == results[0] for r in results)
    # Losers gave their claimed slots back.
    assert sum(_active_counts(db).values()) == 1
    stored = db["participant_assignments"].docs[0]
    assert _active_counts(db)[stored["sequence_label"]] == 1
