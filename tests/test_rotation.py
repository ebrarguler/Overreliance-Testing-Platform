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
    """8 questions eligible only for CORRECT, 6 eligible for {CONFIDENT_WRONG,
    IDK} — two of each difficulty, so W and IDK can each get one per level."""
    questions = []
    for i in range(8):
        questions.append({
            "id": i,
            "difficulty": "medium",
            "variants": {"correct": _authored(), "confident_wrong": _todo(), "idk": _todo()},
        })
    for i, level in zip(range(8, 14), ["easy", "easy", "medium", "medium", "hard", "hard"]):
        questions.append({
            "id": i,
            "difficulty": level,
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


# ---------------------------------------------------------------------------
# The real question bank under the P/Q orders
# ---------------------------------------------------------------------------

from app.utils.questions import questions as real_questions  # noqa: E402
from app.utils.trial_types import is_placeholder  # noqa: E402


@pytest.mark.parametrize("seq_index", range(len(SEQUENCES)), ids=SEQUENCE_LABELS)
@pytest.mark.parametrize("seed", range(25))
def test_real_bank_gives_14_unique_questions_with_authored_variants(seq_index, seed):
    order, variants, label, _ = build_trial_sequence(seq_index, real_questions, seed=seed)
    assert label == SEQUENCE_LABELS[seq_index]
    assert len(order) == 14 and len(set(order)) == 14
    assert [variants[str(q)] for q in order] == SEQUENCES[seq_index]
    for q in order:
        variant = real_questions[q]["variants"][variants[str(q)]]
        assert not is_placeholder(variant)


@pytest.mark.parametrize("seq_index", range(len(SEQUENCES)), ids=SEQUENCE_LABELS)
def test_w_questions_rotate_across_positions_4_7_13(seq_index):
    """No W question is tied to one position (e.g. always the primary probe
    at 4): across seeds, every question used as W appears at 4, 7 and 13."""
    seen = {}
    for seed in range(200):
        order, variants, _, _ = build_trial_sequence(seq_index, real_questions, seed=seed)
        for position in (4, 7, 13):
            q = order[position - 1]
            assert variants[str(q)] == TrialType.CONFIDENT_WRONG.value
            seen.setdefault(q, set()).add(position)
    assert seen, "no W questions were assigned"
    for q, positions in seen.items():
        assert positions == {4, 7, 13}, f"question {q} only at {positions}"


# ---------------------------------------------------------------------------
# Balancing by difficulty (sequences.ROTATION_SCHEME)
# ---------------------------------------------------------------------------

from collections import Counter  # noqa: E402

from app.utils.sequences import (  # noqa: E402
    DIFFICULTY_LEVELS,
    validate_pool_sufficiency,
)

C, W, I = "correct", "confident_wrong", "idk"


def _levels_by_type(order, variants, bank):
    by_type = {C: Counter(), W: Counter(), I: Counter()}
    for q in order:
        by_type[variants[str(q)]][bank[q]["difficulty"]] += 1
    return by_type


@pytest.mark.parametrize("seq_index", range(len(SEQUENCES)), ids=SEQUENCE_LABELS)
@pytest.mark.parametrize("seed", range(40))
def test_each_participant_gets_one_w_and_one_idk_per_difficulty(seq_index, seed):
    order, variants, _, _ = build_trial_sequence(seq_index, real_questions, seed=seed)
    by_type = _levels_by_type(order, variants, real_questions)
    one_each = {level: 1 for level in DIFFICULTY_LEVELS}
    assert by_type[W] == one_each
    assert by_type[I] == one_each
    assert by_type[C] == {"easy": 2, "medium": 4, "hard": 2}


def test_every_problem_is_shown_as_every_type_across_participants():
    seen = {q: set() for q in range(14)}
    for seed in range(300):
        order, variants, _, _ = build_trial_sequence(seed % 2, real_questions, seed=seed)
        for q in order:
            seen[q].add(variants[str(q)])
    assert all(types == {C, W, I} for types in seen.values()), seen


def test_problems_of_a_level_are_drawn_equally_often():
    # Each hard (and each easy) problem should be W and IDK about a quarter
    # of the time; each medium one about a sixth.
    counts = {q: Counter() for q in range(14)}
    n = 3000
    for seed in range(n):
        _, variants, _, _ = build_trial_sequence(0, real_questions, seed=seed)
        for q, t in variants.items():
            counts[int(q)][t] += 1
    for q, c in counts.items():
        expected = 1 / 6 if real_questions[q]["difficulty"] == "medium" else 1 / 4
        for t in (W, I):
            assert abs(c[t] / n - expected) < 0.04, (q, t, c[t] / n)


def test_rotate_problems_false_picks_the_lowest_index_per_level(monkeypatch):
    monkeypatch.setattr(seq_module, "ROTATE_PROBLEMS", False)
    order, variants, _, seed = build_trial_sequence(0, real_questions)
    assert seed is None
    idk = sorted(int(q) for q, t in variants.items() if t == I)
    wrong = sorted(int(q) for q, t in variants.items() if t == W)
    # Lowest easy/medium/hard for IDK (0, 2, 8), the next ones for W (1, 4, 9).
    assert idk == [0, 2, 8] and wrong == [1, 4, 9]


def test_a_bank_that_cannot_be_balanced_is_rejected(synthetic_questions):
    synthetic_questions[12]["difficulty"] = "medium"  # only one hard problem left
    with pytest.raises(ValueError, match="hard"):
        build_trial_sequence(0, synthetic_questions, seed=1)
    with pytest.raises(ValueError, match="hard"):
        validate_pool_sufficiency(synthetic_questions)


def test_an_unknown_difficulty_label_is_rejected_at_startup(synthetic_questions):
    synthetic_questions[0]["difficulty"] = "very hard"
    with pytest.raises(ValueError, match="very hard"):
        validate_pool_sufficiency(synthetic_questions)
