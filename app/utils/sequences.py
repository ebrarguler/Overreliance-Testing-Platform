"""
Counterbalancing sequences for the overreliance experiment.

Each sequence defines the AI-response type at every trial position (Phase 1).
Independently, each participant gets their own realized problem-to-position
mapping (Phase 2): which concrete question fills each "C"/"W"/"I" slot is
rotated per participant so a given question is not always the confident-wrong
one, decoupling question difficulty from experimental condition.

To change the trial-type design:
  - Edit SEQUENCES / SEQUENCE_LABELS / SECTION_BOUNDARIES.
  - validate_sequences() runs at import time and raises ValueError
    immediately, listing every violation, if the table is malformed.

To change problem rotation:
  - Toggle ROTATE_PROBLEMS (False fixes problem order for e.g. a pilot).

Sequence assignment (which participant gets S1..S6) and the realized,
seeded problem rotation live in app.utils.assignment, which persists the
result per participant and reuses it on resume rather than reassigning.
"""

import random
from collections import Counter

from .trial_types import TrialType, is_placeholder

# Trial-type constants — values must match response_type in questions.py.
C = TrialType.CORRECT.value
W = TrialType.CONFIDENT_WRONG.value
I = TrialType.IDK.value

# ---------------------------------------------------------------------------
# Config — edit here only.
# ---------------------------------------------------------------------------

SEQUENCES = [
    [C, W, C, I, C, C, W, C, I, C, W, C, I, C],  # S1
    [C, I, C, W, C, C, I, C, W, C, I, C, W, C],  # S2
    [C, W, C, I, C, C, I, C, W, C, W, C, I, C],  # S3
    [C, I, C, W, C, C, W, C, I, C, I, C, W, C],  # S4
    [C, W, C, I, C, C, W, C, I, C, I, C, W, C],  # S5
    [C, I, C, W, C, C, I, C, W, C, W, C, I, C],  # S6
]

SEQUENCE_LABELS = ["S1", "S2", "S3", "S4", "S5", "S6"]

# 1-indexed [start, end] boundaries (inclusive) for the three sections each
# sequence is checked against: 1-5, 6-10, 11-14. Expressed here as 0-indexed
# half-open (start, end) pairs.
SECTION_BOUNDARIES = [(0, 5), (5, 10), (10, 14)]

# Required exact per-sequence counts (Phase 2 self-test target).
REQUIRED_TYPE_COUNTS = {C: 8, W: 3, I: 3}

# False fixes the problem-to-position mapping to a deterministic, unshuffled
# order (still respecting eligibility) — e.g. for a pilot where you want
# every participant to see the same question in the same condition.
ROTATE_PROBLEMS = True

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_VALID_TYPES = {t.value for t in TrialType}


def validate_sequences():
    """
    Check that SEQUENCES is internally consistent and matches the Phase 2
    design spec. Raises ValueError, listing every violation found (not just
    the first), rather than failing silently or one-error-at-a-time.
    """
    issues = []

    if not SEQUENCES:
        raise ValueError("SEQUENCES is empty")
    if len(SEQUENCES) != len(SEQUENCE_LABELS):
        raise ValueError(
            f"SEQUENCES has {len(SEQUENCES)} entries but "
            f"SEQUENCE_LABELS has {len(SEQUENCE_LABELS)}"
        )

    ref_label = SEQUENCE_LABELS[0]
    ref_length = len(SEQUENCES[0])
    ref_counts = Counter(SEQUENCES[0])

    for idx, seq in enumerate(SEQUENCES):
        label = SEQUENCE_LABELS[idx]

        for pos, trial_type in enumerate(seq):
            if trial_type not in _VALID_TYPES:
                issues.append(
                    f"{label} position {pos}: unknown trial type {trial_type!r}; "
                    f"allowed values are {sorted(_VALID_TYPES)}"
                )

        if len(seq) != ref_length:
            issues.append(
                f"{label} has length {len(seq)}, but {ref_label} has length {ref_length}"
            )
            continue  # further per-sequence checks assume the reference length

        counts = Counter(seq)
        if counts != ref_counts:
            issues.append(
                f"{label} type counts {dict(counts)} differ from "
                f"{ref_label} counts {dict(ref_counts)}"
            )

        for trial_type, required in REQUIRED_TYPE_COUNTS.items():
            actual = counts.get(trial_type, 0)
            if actual != required:
                issues.append(
                    f"{label} has {actual} {trial_type!r} trial(s), expected exactly {required}"
                )

        for start, end in SECTION_BOUNDARIES:
            block = Counter(seq[start:end])
            if block.get(W, 0) != 1 or block.get(I, 0) != 1:
                issues.append(
                    f"{label} section [{start + 1}-{end}] has "
                    f"{block.get(W, 0)} {W!r} and {block.get(I, 0)} {I!r}; "
                    f"expected exactly 1 of each"
                )

    if len(SEQUENCES) == len(SEQUENCE_LABELS) and all(len(s) == ref_length for s in SEQUENCES):
        # Every critical (non-correct) position must be W in exactly 3
        # sequences and I in exactly 3, across all six sequences.
        critical_positions = [pos for pos, t in enumerate(SEQUENCES[0]) if t != C]
        for pos in critical_positions:
            tally = Counter(seq[pos] for seq in SEQUENCES)
            if tally.get(W, 0) != 3 or tally.get(I, 0) != 3:
                issues.append(
                    f"position {pos} is {W!r} in {tally.get(W, 0)} sequence(s) and "
                    f"{I!r} in {tally.get(I, 0)} sequence(s); expected exactly 3 and 3"
                )

    if issues:
        raise ValueError(
            "SEQUENCES failed validation:\n  " + "\n  ".join(issues)
        )

    return True


# Run at import time so a misconfigured sequences.py is caught on startup.
validate_sequences()

# ---------------------------------------------------------------------------
# Eligibility pools — which questions can fill which trial type right now.
# ---------------------------------------------------------------------------


def eligible_trial_types(question):
    """Trial types `question` can legitimately be rotated into right now —
    i.e. every TrialType it has real (non-placeholder) content authored for."""
    return {
        t for t in TrialType
        if not is_placeholder(question["variants"][t.value])
    }


def build_eligibility_pools(questions):
    """Map each TrialType to the list of question indices eligible for it."""
    pools = {t: [] for t in TrialType}
    for i, q in enumerate(questions):
        for t in eligible_trial_types(q):
            pools[t].append(i)
    return pools


def validate_pool_sufficiency(questions):
    """
    Check that every configured sequence can be fully populated given which
    trial types each question actually has authored content for.

    Raises ValueError, listing every shortfall plus every still-TODO variant
    that would close a gap, instead of a live participant being served a
    'TODO' placeholder or a mid-session StopIteration/ValueError.
    """
    pools = build_eligibility_pools(questions)
    shortfalls = []
    for label, seq in zip(SEQUENCE_LABELS, SEQUENCES):
        needed = Counter(seq)
        for raw_type, count in needed.items():
            t = TrialType(raw_type)
            available = len(pools[t])
            if available < count:
                shortfalls.append(
                    f"{label} needs {count} {t.value!r} trial(s) but only "
                    f"{available} question(s) currently have authored "
                    f"{t.value!r} content"
                )

    if not shortfalls:
        return

    todo_by_type = sorted(
        f"question {q['id']} ({t.value})"
        for q in questions
        for t in TrialType
        if t not in eligible_trial_types(q)
    )
    raise ValueError(
        "Question bank cannot satisfy the configured SEQUENCES:\n  "
        + "\n  ".join(shortfalls)
        + "\n\nStill-TODO variants that would close the gap if authored:\n  "
        + "\n  ".join(todo_by_type)
    )


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


def build_trial_sequence(seq_index, questions, seed=None):
    """
    Build one participant's realized trial sequence: which question fills
    each position, and which trial type it's shown under.

    Parameters
    ----------
    seq_index : int
        Index into SEQUENCES (caller reduces modulo len(SEQUENCES)).
    questions : list
        The questions list from questions.py.
    seed : int, optional
        RNG seed for the problem-to-position rotation. If None and
        ROTATE_PROBLEMS is True, a fresh seed is generated and returned so
        the caller can persist it for exact reproducibility later. Ignored
        (and returned as None) when ROTATE_PROBLEMS is False, since no
        randomness is used in that mode.

    Returns
    -------
    question_order : list[int]
        Question indices in presentation order, one per trial position.
    variant_assignments : dict[str, str]
        Maps str(question_index) → trial type shown to this participant.
    seq_label : str
        Human-readable label e.g. "S1".
    seed : int or None
        The seed actually used (None if ROTATE_PROBLEMS is False).

    Raises
    ------
    ValueError
        If the sequence demands more questions of a type than are eligible.
        validate_pool_sufficiency() should catch this at startup instead —
        this is a defense-in-depth check, not the primary guard.
    """
    sequence = SEQUENCES[seq_index]
    seq_label = SEQUENCE_LABELS[seq_index]

    pools = build_eligibility_pools(questions)

    if ROTATE_PROBLEMS:
        if seed is None:
            seed = random.SystemRandom().getrandbits(63)
        rng = random.Random(seed)
        for t in pools:
            rng.shuffle(pools[t])
    else:
        seed = None
        for t in pools:
            pools[t] = sorted(pools[t], reverse=True)  # deterministic; popped ascending below

    needed = Counter(sequence)

    # Claim scarcest-eligibility types first so a question eligible for more
    # than one type isn't grabbed by the wrong one, starving a later type.
    claimed = set()
    assigned_to = {t: [] for t in TrialType}
    for raw_type in sorted(needed, key=lambda rt: len(pools[TrialType(rt)])):
        t = TrialType(raw_type)
        count = needed[raw_type]
        for q_idx in pools[t]:
            if q_idx in claimed:
                continue
            claimed.add(q_idx)
            assigned_to[t].append(q_idx)
            if len(assigned_to[t]) == count:
                break
        if len(assigned_to[t]) < count:
            raise ValueError(
                f"Sequence {seq_label} requests {count} {t.value!r} trial(s) "
                f"but only {len(assigned_to[t])} unclaimed question(s) are "
                f"eligible. validate_pool_sufficiency() should have caught "
                f"this at startup — check questions.py and sequences.py "
                f"stayed in sync."
            )

    if ROTATE_PROBLEMS:
        for t in assigned_to:
            rng.shuffle(assigned_to[t])
    # else: leave in the deterministic order already established above.

    question_order = []
    variant_assignments = {}
    for trial_type in sequence:
        t = TrialType(trial_type)
        q_idx = assigned_to[t].pop()
        question_order.append(q_idx)
        variant_assignments[str(q_idx)] = t.value

    return question_order, variant_assignments, seq_label, seed
