"""
Unit tests for app/utils/measures.py — the reliance-coding and positional
covariate logic documented in docs/MEASURES.md. These must stay in exact
sync with that document; if a formula changes here, update the doc too.
"""

import pytest

from app.utils.measures import (
    RelianceCode,
    get_ai_endorsed_answer,
    compute_reliance_code,
    compute_covariates,
    section_for_position,
)
from app.utils.trial_types import TrialType
from app.utils.sequences import SEQUENCES, SEQUENCE_LABELS


# ---------------------------------------------------------------------------
# get_ai_endorsed_answer / compute_reliance_code
# ---------------------------------------------------------------------------


def _question(correct="2", options=("1", "2", "3"), cw_text="This outputs 3."):
    return {
        "correct": correct,
        "options": list(options),
        "variants": {"confident_wrong": {"initial_recommendation": cw_text}},
    }


def test_correct_variant_endorses_ground_truth():
    q = _question(correct="2")
    assert get_ai_endorsed_answer(q, "correct") == "2"


def test_idk_variant_endorses_nothing():
    q = _question()
    assert get_ai_endorsed_answer(q, "idk") is None


def test_confident_wrong_extracts_the_referenced_option():
    q = _question(options=("1", "2", "3"), cw_text="This outputs 3, because...")
    assert get_ai_endorsed_answer(q, "confident_wrong") == "3"


def test_confident_wrong_ambiguous_match_returns_none():
    # Both "1" and "2" appear as substrings — ambiguous, must not guess.
    q = _question(options=("1", "2"), cw_text="Either 1 or 2 could be argued.")
    assert get_ai_endorsed_answer(q, "confident_wrong") is None


def test_confident_wrong_zero_match_returns_none():
    q = _question(options=("7", "8"), cw_text="This outputs something else entirely.")
    assert get_ai_endorsed_answer(q, "confident_wrong") is None


def test_reliance_code_not_applicable_when_no_endorsed_answer():
    assert compute_reliance_code(None, "anything") == RelianceCode.NOT_APPLICABLE.value


def test_reliance_code_accepted_when_answer_matches_endorsement():
    assert compute_reliance_code("3", "3") == RelianceCode.ACCEPTED.value


def test_reliance_code_rejected_when_answer_differs_from_endorsement():
    assert compute_reliance_code("3", "7") == RelianceCode.REJECTED.value


# ---------------------------------------------------------------------------
# compute_covariates
# ---------------------------------------------------------------------------

C, W, I = TrialType.CORRECT.value, TrialType.CONFIDENT_WRONG.value, TrialType.IDK.value


def test_covariates_at_first_position_are_all_null_or_zero():
    types = [C, W, C, I, C]
    cov = compute_covariates(types, 0)
    assert cov["preceding_trial_type"] is None
    assert cov["trials_since_last_idk"] is None
    assert cov["n_idk_seen_so_far"] == 0
    assert cov["n_cw_seen_so_far"] == 0
    assert cov["is_post_idk_cw"] is False


def test_trials_since_last_idk_counts_from_most_recent_idk():
    types = [C, I, C, C, W]
    cov = compute_covariates(types, 4)  # the W at index 4
    assert cov["trials_since_last_idk"] == 3  # positions 2,3,4 since idk at index1
    assert cov["preceding_trial_type"] == C
    assert cov["n_idk_seen_so_far"] == 1
    assert cov["n_cw_seen_so_far"] == 0


def test_is_post_idk_cw_true_only_when_immediately_preceded_by_idk():
    types = [C, I, W]
    cov = compute_covariates(types, 2)
    assert cov["preceding_trial_type"] == I
    assert cov["is_post_idk_cw"] is True


def test_is_post_idk_cw_false_when_preceding_is_not_idk_even_if_current_is_cw():
    types = [C, C, W]
    cov = compute_covariates(types, 2)
    assert cov["is_post_idk_cw"] is False


def test_is_post_idk_cw_false_when_preceded_by_idk_but_current_is_not_cw():
    types = [I, C]
    cov = compute_covariates(types, 1)
    assert cov["is_post_idk_cw"] is False


@pytest.mark.parametrize("seq", SEQUENCES, ids=SEQUENCE_LABELS)
def test_is_post_idk_cw_never_occurs_under_the_real_sequence_table(seq):
    """
    Documents the Phase 2 design finding in docs/MEASURES.md section 5:
    no sequence in S1-S6 ever places idk immediately before confident_wrong,
    so is_post_idk_cw is structurally always False under this table. If this
    test ever fails, the sequence table changed — go update MEASURES.md
    section 5, don't just delete this test.
    """
    for pos in range(1, len(seq)):
        cov = compute_covariates(seq, pos)
        assert cov["is_post_idk_cw"] is False


# ---------------------------------------------------------------------------
# section_for_position
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("position,expected", [
    (1, "early"), (5, "early"),
    (6, "middle"), (10, "middle"),
    (11, "late"), (14, "late"),
])
def test_section_for_position(position, expected):
    assert section_for_position(position) == expected


def test_section_for_position_out_of_range_raises():
    with pytest.raises(ValueError):
        section_for_position(15)
