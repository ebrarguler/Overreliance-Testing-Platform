"""
Self-test for the active Option 6 sequence table (app.utils.sequences), and
proof that validate_sequences() fails loudly on a table that breaks any of
the design rules rather than being a no-op.

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
    LEGACY_SEQUENCES,
    PROBE_IMMEDIATE,
    PROBE_ONE_BETWEEN,
    probe_context,
    validate_sequences,
)


def _parse(row):
    return [{"C": C, "W": W, "I": I}[t] for t in row.split()]


ORDER_A = _parse("C C W I C C I W C C I C W C")
ORDER_B = _parse("C C W I C I C W C C C I W C")


def _at(seq, position_1idx):
    return seq[position_1idx - 1]


def _prior_idks(seq, position_1idx):
    return sum(1 for t in seq[: position_1idx - 1] if t == I)


def test_validate_sequences_passes_on_the_real_table():
    assert validate_sequences() is True


def test_design_version_is_option6():
    assert SEQUENCE_DESIGN_VERSION == "option6_v1"


def test_exactly_the_two_option6_orders_are_active():
    assert SEQUENCE_LABELS == ["A", "B"]
    assert SEQUENCES == [ORDER_A, ORDER_B]


def test_each_order_has_14_positions_and_8_c_3_w_3_i():
    for seq in SEQUENCES:
        assert len(seq) == 14
        assert Counter(seq) == {C: 8, W: 3, I: 3}


def test_no_consecutive_idks():
    for seq in SEQUENCES:
        for pos in range(2, 15):
            assert not (_at(seq, pos) == I and _at(seq, pos - 1) == I)


def test_w_is_at_positions_3_8_13_only():
    for seq in SEQUENCES:
        assert [p for p in range(1, 15) if _at(seq, p) == W] == [3, 8, 13]


def test_first_idk_is_at_position_4():
    for seq in SEQUENCES:
        assert [p for p in range(1, 15) if _at(seq, p) == I][0] == 4


def test_previous_idk_counts_at_primary_probes():
    for seq in SEQUENCES:
        assert _prior_idks(seq, 8) == 2
        assert _prior_idks(seq, 13) == 3


@pytest.mark.parametrize(
    "seq,position,expected",
    [
        (ORDER_A, 8, PROBE_IMMEDIATE),
        (ORDER_A, 13, PROBE_ONE_BETWEEN),
        (ORDER_B, 8, PROBE_ONE_BETWEEN),
        (ORDER_B, 13, PROBE_IMMEDIATE),
    ],
    ids=["A8", "A13", "B8", "B13"],
)
def test_primary_probe_contexts(seq, position, expected):
    assert probe_context(seq, position) == expected


def test_each_order_has_one_probe_of_each_context():
    for seq in SEQUENCES:
        assert sorted([probe_context(seq, 8), probe_context(seq, 13)]) == sorted(
            [PROBE_IMMEDIATE, PROBE_ONE_BETWEEN]
        )


def test_position_3_is_a_pre_idk_w_with_no_probe_context():
    for seq in SEQUENCES:
        assert _at(seq, 3) == W
        assert _prior_idks(seq, 3) == 0
        assert probe_context(seq, 3) is None


def test_legacy_table_is_kept_unchanged_for_reference():
    assert sorted(LEGACY_SEQUENCES) == ["S1", "S2", "S3", "S4", "S5", "S6"]
    assert LEGACY_SEQUENCES["S1"] == _parse("C W C I C C W C I C W C I C")
    assert not set(LEGACY_SEQUENCES) & set(SEQUENCE_LABELS)


_BROKEN_TABLES = {
    "three orders": ([ORDER_A, ORDER_B, ORDER_A], ["A", "B", "C"]),
    "one order": ([ORDER_A], ["A"]),
    "15 positions": ([ORDER_A + [C], ORDER_B], ["A", "B"]),
    "9 C": ([_parse("C C W C C C I W C C I C W C"), ORDER_B], ["A", "B"]),
    "consecutive IDKs": ([_parse("C C W I I C C W C C I C W C"), ORDER_B], ["A", "B"]),
    "W moved off 13": ([_parse("C C W I C C I W C C I W C C"), ORDER_B], ["A", "B"]),
    "first IDK at 5": ([_parse("C C W C I C I W C C I C W C"), ORDER_B], ["A", "B"]),
    # Both probes immediate: 8 and 13 each preceded directly by IDK.
    "two immediate probes": ([_parse("C C W I C C I W C C C I W C"), ORDER_B], ["A", "B"]),
    "identical orders": ([ORDER_A, ORDER_A], ["A", "B"]),
    "unknown type": ([[C, "typo"] + ORDER_A[2:], ORDER_B], ["A", "B"]),
}


@pytest.mark.parametrize("name", list(_BROKEN_TABLES))
def test_validate_sequences_fails_loudly_on_a_broken_table(monkeypatch, name):
    broken_sequences, broken_labels = _BROKEN_TABLES[name]
    monkeypatch.setattr(seq_module, "SEQUENCES", broken_sequences)
    monkeypatch.setattr(seq_module, "SEQUENCE_LABELS", broken_labels)
    with pytest.raises(ValueError):
        seq_module.validate_sequences()
