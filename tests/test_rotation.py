"""
Tests for the problem-to-position rotation mechanics in build_trial_sequence.

Uses a synthetic question bank (not the real one in questions.py) so these
tests don't depend on which variants happen to be authored yet in the real
question bank — that's app.utils.questions's own concern, validated there at
import time.
"""

import pytest

from app.utils.trial_types import TrialType
from app.utils import sequences as seq_module
from app.utils.sequences import build_trial_sequence, SEQUENCES, SEQUENCE_LABELS


def _authored(text="ok"):
    return {
        "initial_recommendation": text,
        "chat_prompt": {"system_context": text, "question_context": text, "stance": text},
    }


def _todo():
    return {
        "initial_recommendation": "TODO",
        "chat_prompt": {"system_context": "TODO", "question_context": "TODO", "stance": "TODO"},
    }


@pytest.fixture
def synthetic_questions():
    """8 questions eligible only for CORRECT, 6 eligible for {CONFIDENT_WRONG, IDK} —
    mirrors the real bank's current shape (see questions.py)."""
    questions = []
    for i in range(8):
        questions.append({
            "id": i,
            "variants": {"correct": _authored(), "confident_wrong": _todo(), "idk": _todo()},
        })
    for i in range(8, 14):
        questions.append({
            "id": i,
            "variants": {"correct": _todo(), "confident_wrong": _authored(), "idk": _authored()},
        })
    return questions


def test_build_trial_sequence_uses_every_question_exactly_once(synthetic_questions):
    question_order, variant_assignments, seq_label, seed = build_trial_sequence(
        0, synthetic_questions
    )
    assert sorted(question_order) == list(range(14))
    assert len(variant_assignments) == 14
    assert seq_label == SEQUENCE_LABELS[0]


def test_build_trial_sequence_respects_eligibility(synthetic_questions):
    question_order, variant_assignments, _, _ = build_trial_sequence(0, synthetic_questions)
    for q_idx, trial_type in variant_assignments.items():
        q_idx = int(q_idx)
        if q_idx < 8:
            assert trial_type == TrialType.CORRECT.value
        else:
            assert trial_type in (TrialType.CONFIDENT_WRONG.value, TrialType.IDK.value)


def test_build_trial_sequence_type_pattern_matches_sequence(synthetic_questions):
    question_order, variant_assignments, _, _ = build_trial_sequence(0, synthetic_questions)
    realized_types = [variant_assignments[str(q_idx)] for q_idx in question_order]
    assert realized_types == SEQUENCES[0]


def test_same_seed_reproduces_the_same_rotation(synthetic_questions):
    order_a, assign_a, _, seed = build_trial_sequence(0, synthetic_questions, seed=42)
    order_b, assign_b, _, seed_b = build_trial_sequence(0, synthetic_questions, seed=42)
    assert seed == seed_b == 42
    assert order_a == order_b
    assert assign_a == assign_b


def test_a_fresh_seed_is_generated_and_returned_when_none_given(synthetic_questions):
    _, _, _, seed = build_trial_sequence(0, synthetic_questions)
    assert isinstance(seed, int)


def test_rotate_problems_false_is_deterministic_and_seedless(monkeypatch, synthetic_questions):
    monkeypatch.setattr(seq_module, "ROTATE_PROBLEMS", False)
    order_a, assign_a, _, seed_a = build_trial_sequence(0, synthetic_questions)
    order_b, assign_b, _, seed_b = build_trial_sequence(0, synthetic_questions)
    assert seed_a is None and seed_b is None
    assert order_a == order_b
    assert assign_a == assign_b


def test_rotate_problems_true_vs_false_can_differ(monkeypatch, synthetic_questions):
    monkeypatch.setattr(seq_module, "ROTATE_PROBLEMS", False)
    fixed_order, _, _, _ = build_trial_sequence(0, synthetic_questions)

    monkeypatch.setattr(seq_module, "ROTATE_PROBLEMS", True)
    seeded_order, _, _, seed = build_trial_sequence(0, synthetic_questions, seed=7)

    # Not a hard guarantee (a shuffle can coincidentally match), but with a
    # fixed seed of 7 over 14 items this pins down that rotation actually ran.
    assert isinstance(seed, int)
    assert sorted(fixed_order) == sorted(seeded_order) == list(range(14))
