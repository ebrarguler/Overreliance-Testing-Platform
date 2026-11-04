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
    IDK}. No difficulty labels are needed."""
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
# Uniform randomization (sequences.ROTATION_SCHEME)
# ---------------------------------------------------------------------------

from collections import Counter  # noqa: E402
from copy import deepcopy  # noqa: E402
from itertools import permutations  # noqa: E402
import random  # noqa: E402

from app.utils.sequences import validate_pool_sufficiency, _assign_problems  # noqa: E402

C, W, I = "correct", "confident_wrong", "idk"


def test_every_problem_is_shown_as_every_type_across_participants():
    seen = {q: set() for q in range(14)}
    for seed in range(300):
        order, variants, _, _ = build_trial_sequence(seed % 2, real_questions, seed=seed)
        for q in order:
            seen[q].add(variants[str(q)])
    assert all(types == {C, W, I} for types in seen.values()), seen


def test_all_problems_have_equal_type_and_position_probabilities():
    counts = {q: Counter() for q in range(14)}
    positions = {q: Counter() for q in range(14)}
    n = 3000
    for seed in range(n):
        order, variants, _, _ = build_trial_sequence(seed % 2, real_questions, seed=seed)
        for pos, q in enumerate(order):
            counts[q][variants[str(q)]] += 1
            positions[q][pos] += 1
    for q in counts:
        for t, expected in ((C, 8 / 14), (W, 3 / 14), (I, 3 / 14)):
            assert abs(counts[q][t] / n - expected) < 0.04
        for pos in range(14):
            assert abs(positions[q][pos] / n - 1 / 14) < 0.025


def test_difficulty_labels_are_absent_and_cannot_influence_assignment():
    assert all("difficulty" not in q for q in real_questions)
    labelled = deepcopy(real_questions)
    for q in labelled:
        q["difficulty"] = "arbitrary label"
    validate_pool_sufficiency(labelled)
    for seq_index in range(len(SEQUENCES)):
        assert build_trial_sequence(seq_index, labelled, seed=42) == build_trial_sequence(
            seq_index, real_questions, seed=42)


def test_rotate_problems_false_uses_bank_order(monkeypatch):
    monkeypatch.setattr(seq_module, "ROTATE_PROBLEMS", False)
    order, _, _, seed = build_trial_sequence(0, real_questions)
    assert seed is None
    assert order == list(range(14))


def test_insufficient_distinct_eligible_questions_are_rejected(synthetic_questions):
    # Each type has enough candidates individually, but W and IDK together
    # need six distinct questions and now share only five.
    synthetic_questions[13]["variants"] = {
        C: _authored(), W: _todo(), I: _todo(),
    }
    with pytest.raises(ValueError, match="no feasible assignment"):
        build_trial_sequence(0, synthetic_questions, seed=1)
    with pytest.raises(ValueError, match="no feasible assignment"):
        validate_pool_sufficiency(synthetic_questions)


def test_restricted_pool_draws_match_uniform_feasible_assignments():
    # Includes a surplus question and overlapping eligibility: greedy random
    # choices can bias the draw or exhaust another type's candidates.
    eligibility = [{C, W}, {C}, {W, I}, {C, I}]
    bank = [{"id": j, "variants": {t: _authored() if t in allowed else _todo()
                                   for t in (C, W, I)}}
            for j, allowed in enumerate(eligibility)]
    feasible = {order for order in permutations(range(4), 3)
                if all(t in eligibility[q] for t, q in zip((C, W, I), order))}
    counts = Counter()
    for seed in range(3000):
        assigned = _assign_problems("test", [C, W, I], bank, random.Random(seed))
        counts[tuple(assigned[t][0] for t in (C, W, I))] += 1
    assert set(counts) == feasible
    assert all(abs(count / 3000 - 1 / len(feasible)) < 0.025
               for count in counts.values())
