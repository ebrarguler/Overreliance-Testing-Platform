"""
Phase 2 self-test: verifies the loaded SEQUENCES table itself (not question
content — app.utils.sequences has no dependency on questions.py), and that
validate_sequences() actually fails loudly on a broken table rather than
being a no-op.
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
    SECTION_BOUNDARIES,
    REQUIRED_TYPE_COUNTS,
    validate_sequences,
)


def test_validate_sequences_passes_on_the_real_table():
    assert validate_sequences() is True


def test_every_sequence_has_exactly_8_c_3_w_3_i():
    for label, seq in zip(SEQUENCE_LABELS, SEQUENCES):
        counts = Counter(seq)
        for trial_type, required in REQUIRED_TYPE_COUNTS.items():
            assert counts.get(trial_type, 0) == required, (
                f"{label}: expected {required} {trial_type!r}, got {counts.get(trial_type, 0)}"
            )


def test_every_critical_slot_is_w_in_exactly_3_and_i_in_exactly_3():
    critical_positions = [pos for pos, t in enumerate(SEQUENCES[0]) if t != C]
    assert len(critical_positions) == 6

    for pos in critical_positions:
        tally = Counter(seq[pos] for seq in SEQUENCES)
        assert tally.get(W, 0) == 3, f"position {pos}: expected 3 W, got {tally.get(W, 0)}"
        assert tally.get(I, 0) == 3, f"position {pos}: expected 3 I, got {tally.get(I, 0)}"


def test_every_section_has_exactly_one_w_and_one_i_per_sequence():
    assert SECTION_BOUNDARIES == [(0, 5), (5, 10), (10, 14)]

    for label, seq in zip(SEQUENCE_LABELS, SEQUENCES):
        for start, end in SECTION_BOUNDARIES:
            block = Counter(seq[start:end])
            assert block.get(W, 0) == 1, (
                f"{label} section [{start + 1}-{end}]: expected 1 W, got {block.get(W, 0)}"
            )
            assert block.get(I, 0) == 1, (
                f"{label} section [{start + 1}-{end}]: expected 1 I, got {block.get(I, 0)}"
            )


@pytest.mark.parametrize(
    "broken_sequences,broken_labels",
    [
        # Wrong type counts: 9 C instead of 8.
        ([[C, W, C, I, C, C, W, C, I, C, W, C, I, C, C]], ["Sbroken"]),
        # A section with two W's and zero I's.
        ([[C, W, W, C, C, C, W, C, I, C, W, C, I, C]], ["Sbroken"]),
        # An unrecognised trial type.
        ([[C, "typo", C, I, C, C, W, C, I, C, W, C, I, C]], ["Sbroken"]),
    ],
)
def test_validate_sequences_fails_loudly_on_a_broken_table(
    monkeypatch, broken_sequences, broken_labels
):
    monkeypatch.setattr(seq_module, "SEQUENCES", broken_sequences)
    monkeypatch.setattr(seq_module, "SEQUENCE_LABELS", broken_labels)
    with pytest.raises(ValueError):
        seq_module.validate_sequences()
