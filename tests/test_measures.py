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
    compute_design_fields,
    section_for_position,
)
from app.utils.trial_types import TrialType
from app.utils.sequences import (
    LEGACY_SEQUENCES,
    OPTION6_DESIGN_VERSION,
    OPTION6_SEQUENCES,
    SEQUENCE_DESIGN_VERSION,
    SEQUENCES,
    SEQUENCE_LABELS,
)


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


@pytest.mark.parametrize("seq", list(LEGACY_SEQUENCES.values()), ids=list(LEGACY_SEQUENCES))
def test_is_post_idk_cw_never_occurs_under_the_legacy_table(seq):
    """No legacy S1-S6 order places idk immediately before confident_wrong
    (docs/MEASURES.md section 5), so legacy records have it always False."""
    for pos in range(1, len(seq)):
        assert compute_covariates(seq, pos)["is_post_idk_cw"] is False


OPTION6_ORDERS = list(OPTION6_SEQUENCES.items())


@pytest.mark.parametrize("label,seq", OPTION6_ORDERS)
def test_is_post_idk_cw_occurs_once_per_option6_order(label, seq):
    hits = [p + 1 for p in range(len(seq)) if compute_covariates(seq, p)["is_post_idk_cw"]]
    assert hits == {"A": [8], "B": [13]}[label]


@pytest.mark.parametrize("label,seq", list(zip(SEQUENCE_LABELS, SEQUENCES)))
def test_is_post_idk_cw_under_blocks_pq(label, seq):
    hits = [p + 1 for p in range(len(seq)) if compute_covariates(seq, p)["is_post_idk_cw"]]
    assert hits == {"P": [13], "Q": [4, 13]}[label]


@pytest.mark.parametrize("label,seq", OPTION6_ORDERS)
def test_trials_since_last_idk_at_option6_probes(label, seq):
    expected = {"A": {8: 1, 13: 2}, "B": {8: 2, 13: 1}}[label]
    for position, value in expected.items():
        cov = compute_covariates(seq, position - 1)
        assert cov["trials_since_last_idk"] == value
        assert cov["n_idk_seen_so_far"] == {8: 2, 13: 3}[position]
    # Before the first IDK (position 4) there is no previous IDK.
    assert compute_covariates(seq, 2)["trials_since_last_idk"] is None


@pytest.mark.parametrize("label,seq", list(zip(SEQUENCE_LABELS, SEQUENCES)))
def test_covariates_at_the_blocks_pq_w_trials(label, seq):
    expected = {  # position: (n_idk_seen_so_far, trials_since_last_idk)
        "P": {4: (0, None), 7: (1, 2), 13: (3, 1)},
        "Q": {4: (1, 1), 7: (1, 4), 13: (3, 1)},
    }[label]
    for position, (n_idk, since) in expected.items():
        cov = compute_covariates(seq, position - 1)
        assert (cov["n_idk_seen_so_far"], cov["trials_since_last_idk"]) == (n_idk, since)
        assert cov["n_cw_seen_so_far"] == {4: 0, 7: 1, 13: 2}[position]


# ---------------------------------------------------------------------------
# compute_design_fields
# ---------------------------------------------------------------------------


def _design_fields(seq, design_version=SEQUENCE_DESIGN_VERSION):
    return [compute_design_fields(seq, p, design_version) for p in range(len(seq))]


@pytest.mark.parametrize("label,expected", [("P", "before_first_idk"), ("Q", "after_first_idk")])
def test_pq_primary_probe_is_the_w_at_position_4(label, expected):
    fields = _design_fields(SEQUENCES[SEQUENCE_LABELS.index(label)])
    probes = {p + 1: f["pq_probe_context"] for p, f in enumerate(fields)
              if f["is_primary_pq_probe"]}
    assert probes == {4: expected}
    for p, f in enumerate(fields, start=1):
        if p != 4:
            assert f["is_primary_pq_probe"] is False and f["pq_probe_context"] is None
        # Option 6 labels never apply to a blocks_pq participant.
        assert f["is_primary_option6_probe"] is False
        assert f["option6_probe_context"] is None


@pytest.mark.parametrize(
    "label,expected",
    [
        ("A", {8: "immediate_after_idk", 13: "one_correct_between"}),
        ("B", {8: "one_correct_between", 13: "immediate_after_idk"}),
    ],
)
def test_option6_records_keep_their_probe_labels(label, expected):
    fields = _design_fields(OPTION6_SEQUENCES[label], OPTION6_DESIGN_VERSION)
    probes = {
        p + 1: f["option6_probe_context"]
        for p, f in enumerate(fields)
        if f["is_primary_option6_probe"]
    }
    assert probes == expected
    # Every other trial, including the W at position 3, is outside the comparison.
    for p, f in enumerate(fields, start=1):
        if p not in expected:
            assert f["is_primary_option6_probe"] is False
            assert f["option6_probe_context"] is None
        assert f["is_primary_pq_probe"] is False


@pytest.mark.parametrize("label,seq", list(zip(SEQUENCE_LABELS, SEQUENCES)))
def test_idk_encounter_numbers(label, seq):
    fields = _design_fields(seq)
    numbers = {p + 1: f["idk_encounter_number"] for p, f in enumerate(fields)}
    idk_positions = {"P": [5, 9, 12], "Q": [3, 9, 12]}[label]
    assert {p: numbers[p] for p in idk_positions} == dict(zip(idk_positions, [1, 2, 3]))
    for p, n in numbers.items():
        if p not in idk_positions:
            assert n is None


@pytest.mark.parametrize("design_version", [None, "some_other_design"])
def test_no_probes_outside_their_own_design(design_version):
    # A participant from another design is never labelled with a probe, even
    # when their order happens to have W at 4, 8 or 13.
    orders = list(LEGACY_SEQUENCES.values()) + list(OPTION6_SEQUENCES.values()) + SEQUENCES
    for seq in orders:
        for f in _design_fields(seq, design_version):
            assert f["is_primary_option6_probe"] is False
            assert f["option6_probe_context"] is None
            assert f["is_primary_pq_probe"] is False
            assert f["pq_probe_context"] is None


def test_legacy_idk_encounter_numbers_come_from_the_saved_order():
    fields = _design_fields(LEGACY_SEQUENCES["S1"], None)
    assert [p + 1 for p, f in enumerate(fields) if f["idk_encounter_number"]] == [4, 9, 13]


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
