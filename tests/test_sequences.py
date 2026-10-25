"""
Self-test for the active block design (orders P and Q in app.utils.sequences),
and proof that validate_sequences() fails loudly on a table that breaks any
of the design rules rather than being a no-op.

Positions in this file are 1-indexed presentation positions, as in the design
spec; the lists themselves are 0-indexed.
"""

from collections import Counter

import pytest

from app.utils import sequences as seq_module
from app.utils.sequences import (
    C,
    W,
    I,
    SEQUENCES,
    SEQUENCE_LABELS,
    SEQUENCE_DESIGN_VERSION,
    SECTION_BOUNDARIES,
    LEGACY_SEQUENCES,
    OPTION6_SEQUENCES,
    PQ_AFTER_IDK,
    PQ_BEFORE_IDK,
    PQ_PROBE_POSITION,
    PROBE_IMMEDIATE,
    PROBE_ONE_BETWEEN,
    pq_probe_context,
    probe_context,
    validate_sequences,
)


def _parse(row):
    return [{"C": C, "W": W, "I": I}[t] for t in row.replace("|", " ").split()]


ORDER_P = _parse("C C C W I | C W C I C | C I W C")
ORDER_Q = _parse("C C I W C | C W C I C | C I W C")


def _positions(seq, trial_type):
    return [p for p, t in enumerate(seq, start=1) if t == trial_type]


def test_validate_sequences_passes_on_the_real_table():
    assert validate_sequences() is True


def test_design_version_is_blocks_pq():
    assert SEQUENCE_DESIGN_VERSION == "blocks_pq_v1"


def test_exactly_orders_p_and_q_are_active():
    assert SEQUENCE_LABELS == ["P", "Q"]
    assert SEQUENCES == [ORDER_P, ORDER_Q]


def test_each_order_has_14_positions_and_8_c_3_w_3_i():
    for seq in SEQUENCES:
        assert len(seq) == 14
        assert Counter(seq) == {C: 8, W: 3, I: 3}


def test_every_block_has_one_w_and_one_idk():
    assert SECTION_BOUNDARIES == [(0, 5), (5, 10), (10, 14)]
    for seq in SEQUENCES:
        for start, end in SECTION_BOUNDARIES:
            block = Counter(seq[start:end])
            assert block[W] == 1 and block[I] == 1


def test_no_consecutive_idks_and_two_correct_trials_first():
    for seq in SEQUENCES:
        assert seq[:2] == [C, C]
        assert not any(seq[i] == seq[i + 1] == I for i in range(13))


def test_w_positions_are_the_same_in_both_orders():
    assert _positions(ORDER_P, W) == _positions(ORDER_Q, W) == [4, 7, 13]


def test_orders_differ_only_at_positions_3_and_5():
    differing = [p for p in range(1, 15) if ORDER_P[p - 1] != ORDER_Q[p - 1]]
    assert differing == [3, 5]


def test_first_w_is_before_idk_in_p_and_after_idk_in_q():
    assert PQ_PROBE_POSITION == 4
    assert pq_probe_context(ORDER_P, 4) == PQ_BEFORE_IDK
    assert pq_probe_context(ORDER_Q, 4) == PQ_AFTER_IDK
    # The later W trials have the same context in both orders.
    for position in (7, 13):
        assert pq_probe_context(ORDER_P, position) == pq_probe_context(ORDER_Q, position)


def test_pq_probe_context_is_none_off_w_trials():
    assert pq_probe_context(ORDER_P, 1) is None
    assert pq_probe_context(ORDER_P, 5) is None


def test_earlier_designs_are_kept_unchanged_for_reference():
    assert sorted(LEGACY_SEQUENCES) == ["S1", "S2", "S3", "S4", "S5", "S6"]
    assert LEGACY_SEQUENCES["S1"] == _parse("C W C I C C W C I C W C I C")
    assert OPTION6_SEQUENCES == {
        "A": _parse("C C W I C C I W C C I C W C"),
        "B": _parse("C C W I C I C W C C C I W C"),
    }
    old_labels = set(LEGACY_SEQUENCES) | set(OPTION6_SEQUENCES)
    assert not old_labels & set(SEQUENCE_LABELS)


@pytest.mark.parametrize(
    "label,position,expected",
    [("A", 8, PROBE_IMMEDIATE), ("A", 13, PROBE_ONE_BETWEEN),
     ("B", 8, PROBE_ONE_BETWEEN), ("B", 13, PROBE_IMMEDIATE)],
)
def test_option6_probe_contexts_are_unchanged(label, position, expected):
    assert probe_context(OPTION6_SEQUENCES[label], position) == expected


_BROKEN_TABLES = {
    "three orders": ([ORDER_P, ORDER_Q, ORDER_P], ["P", "Q", "R"]),
    "one order": ([ORDER_P], ["P"]),
    "15 positions": ([ORDER_P + [C], ORDER_Q], ["P", "Q"]),
    "9 C": ([_parse("C C C W C | C W C I C | C I W C"), ORDER_Q], ["P", "Q"]),
    "consecutive IDKs": ([_parse("C C C W I | I W C C C | C I W C"), ORDER_Q], ["P", "Q"]),
    "block 1 has no IDK": ([_parse("C C C W C | I W C I C | C I W C"), ORDER_Q], ["P", "Q"]),
    "opens with IDK": ([_parse("I C C W C | C W C I C | C I W C"), ORDER_Q], ["P", "Q"]),
    "first W at 3": ([_parse("C C W C I | C W C I C | C I W C"),
                      _parse("C I W C C | C W C I C | C I W C")], ["P", "Q"]),
    "W positions differ": ([ORDER_P, _parse("C C I W C | W C C I C | C I W C")], ["P", "Q"]),
    "differ after block 1": ([ORDER_P, _parse("C C I W C | C W C I C | C I C W")], ["P", "Q"]),
    "both after IDK": ([_parse("C C I W C | C W C I C | C I W C"),
                        _parse("C I C W C | C W C I C | C I W C")], ["P", "Q"]),
    "identical orders": ([ORDER_P, ORDER_P], ["P", "Q"]),
    "unknown type": ([[C, "typo"] + ORDER_P[2:], ORDER_Q], ["P", "Q"]),
}


@pytest.mark.parametrize("name", list(_BROKEN_TABLES))
def test_validate_sequences_fails_loudly_on_a_broken_table(monkeypatch, name):
    broken_sequences, broken_labels = _BROKEN_TABLES[name]
    monkeypatch.setattr(seq_module, "SEQUENCES", broken_sequences)
    monkeypatch.setattr(seq_module, "SEQUENCE_LABELS", broken_labels)
    with pytest.raises(ValueError):
        seq_module.validate_sequences()
