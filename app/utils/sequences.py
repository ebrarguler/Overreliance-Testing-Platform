"""
Response-type sequences for the overreliance experiment.

Each sequence defines the AI-response type at every presentation position.
Independently, each participant gets their own realized problem-to-position
mapping: which concrete question fills each "C"/"W"/"I" slot is rotated per
participant (seeded), so a given question is not always shown at the same
position, decoupling question identity from position.

Active design: Option 6 (SEQUENCE_DESIGN_VERSION = "option6_v1")
------------------------------------------------------------------
Two orders, 14 positions each, 8 C / 3 W / 3 I. Positions are numbered 1-14
in the comments and in OPTION6_* constants below; the lists are 0-indexed.

    Position:  1  2  3  4  5  6  7  8  9 10 11 12 13 14
    Order A:   C  C  W  I  C  C  I  W  C  C  I  C  W  C
    Order B:   C  C  W  I  C  I  C  W  C  C  C  I  W  C

The primary comparison is between the W trials at positions 8 and 13:
    A8  = immediately after IDK          (I -> W)
    A13 = one correct trial between      (I -> C -> W)
    B8  = one correct trial between      (I -> C -> W)
    B13 = immediately after IDK          (I -> W)
so every participant experiences both arrangements once. Position 3 is a
shared pre-IDK W observation and is NOT part of the primary comparison.
This design tests recent IDK placement; it is not a comparison of IDK
exposure against no IDK exposure, and it is not a balanced Latin square.

To change the trial-type design:
  - Edit SEQUENCES / SEQUENCE_LABELS and bump SEQUENCE_DESIGN_VERSION, so
    records from different designs are never pooled under one label.
  - validate_sequences() runs at import time and raises ValueError
    immediately, listing every violation, if the table is malformed.

To change problem rotation:
  - Toggle ROTATE_PROBLEMS (False fixes problem order for e.g. a pilot).

Sequence assignment (which participant gets A or B) and the realized,
seeded problem rotation live in app.utils.assignment, which persists the
result per participant and reuses it on resume rather than reassigning.
Participants assigned under the earlier six-order design (LEGACY_SEQUENCES)
keep that assignment.
"""

import random
from collections import Counter

from .trial_types import TrialType, is_placeholder

# Trial-type constants — values must match the variant keys in questions.py.
C = TrialType.CORRECT.value
W = TrialType.CONFIDENT_WRONG.value
I = TrialType.IDK.value

# ---------------------------------------------------------------------------
# Config — edit here only.
# ---------------------------------------------------------------------------

SEQUENCE_DESIGN_VERSION = "option6_v1"

SEQUENCES = [
    # 1  2  3  4  5  6  7  8  9 10 11 12 13 14
    [C, C, W, I, C, C, I, W, C, C, I, C, W, C],  # A
    [C, C, W, I, C, I, C, W, C, C, C, I, W, C],  # B
]

SEQUENCE_LABELS = ["A", "B"]

# Descriptive only (stored per trial as `section`): positions 1-5, 6-10,
# 11-14, as 0-indexed half-open (start, end) pairs. Not a design constraint.
SECTION_BOUNDARIES = [(0, 5), (5, 10), (10, 14)]

# Required exact per-sequence counts.
REQUIRED_TYPE_COUNTS = {C: 8, W: 3, I: 3}

# Option 6 structure, 1-indexed presentation positions.
OPTION6_N_ORDERS = 2
OPTION6_N_POSITIONS = 14
OPTION6_W_POSITIONS = (3, 8, 13)
OPTION6_PRIMARY_PROBE_POSITIONS = (8, 13)
OPTION6_FIRST_IDK_POSITION = 4
# Number of IDK trials strictly before each primary probe position.
OPTION6_PRIOR_IDKS_AT_PROBE = {8: 2, 13: 3}

PROBE_IMMEDIATE = "immediate_after_idk"      # I -> W
PROBE_ONE_BETWEEN = "one_correct_between"    # I -> C -> W

# The six-order design used before Option 6. Kept as a historical record so
# legacy assignments (sequence_design_version absent) can be read; never
# used for new allocations.
LEGACY_SEQUENCES = {
    "S1": [C, W, C, I, C, C, W, C, I, C, W, C, I, C],
    "S2": [C, I, C, W, C, C, I, C, W, C, I, C, W, C],
    "S3": [C, W, C, I, C, C, I, C, W, C, W, C, I, C],
    "S4": [C, I, C, W, C, C, W, C, I, C, I, C, W, C],
    "S5": [C, W, C, I, C, C, W, C, I, C, I, C, W, C],
    "S6": [C, I, C, W, C, C, I, C, W, C, W, C, I, C],
}

# False fixes the problem-to-position mapping to a deterministic, unshuffled
# order (still respecting eligibility) — e.g. for a pilot where you want
# every participant to see the same question in the same condition.
ROTATE_PROBLEMS = True

# ---------------------------------------------------------------------------
# Probe classification — shared by validation and per-trial measures.
# ---------------------------------------------------------------------------


def probe_context(types, position_1idx):
    """
    How the W trial at `position_1idx` relates to the most recent IDK:
    PROBE_IMMEDIATE if the previous trial is IDK, PROBE_ONE_BETWEEN if the
    previous trial is C and the one before it is IDK, otherwise None.
    Returns None for any non-W trial.
    """
    idx = position_1idx - 1
    if types[idx] != W:
        return None
    if idx >= 1 and types[idx - 1] == I:
        return PROBE_IMMEDIATE
    if idx >= 2 and types[idx - 1] == C and types[idx - 2] == I:
        return PROBE_ONE_BETWEEN
    return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_VALID_TYPES = {t.value for t in TrialType}


def validate_sequences():
    """
    Check that SEQUENCES matches the Option 6 design spec. Raises ValueError,
    listing every violation found (not just the first).
    """
    issues = []

    if len(SEQUENCES) != len(SEQUENCE_LABELS):
        raise ValueError(
            f"SEQUENCES has {len(SEQUENCES)} entries but "
            f"SEQUENCE_LABELS has {len(SEQUENCE_LABELS)}"
        )
    if len(SEQUENCES) != OPTION6_N_ORDERS:
        issues.append(
            f"expected exactly {OPTION6_N_ORDERS} active orders, got {len(SEQUENCES)}"
        )
    if len(set(SEQUENCE_LABELS)) != len(SEQUENCE_LABELS):
        issues.append(f"SEQUENCE_LABELS are not unique: {SEQUENCE_LABELS}")
    if len({tuple(s) for s in SEQUENCES}) != len(SEQUENCES):
        issues.append("two active orders are identical")

    contexts_required = {PROBE_IMMEDIATE, PROBE_ONE_BETWEEN}

    for label, seq in zip(SEQUENCE_LABELS, SEQUENCES):
        bad_types = [
            (pos, t) for pos, t in enumerate(seq, start=1) if t not in _VALID_TYPES
        ]
        for pos, t in bad_types:
            issues.append(
                f"{label} position {pos}: unknown trial type {t!r}; "
                f"allowed values are {sorted(_VALID_TYPES)}"
            )
        if len(seq) != OPTION6_N_POSITIONS:
            issues.append(
                f"{label} has {len(seq)} positions, expected {OPTION6_N_POSITIONS}"
            )
            continue  # positional checks below assume the full length
        if bad_types:
            continue

        counts = Counter(seq)
        for trial_type, required in REQUIRED_TYPE_COUNTS.items():
            actual = counts.get(trial_type, 0)
            if actual != required:
                issues.append(
                    f"{label} has {actual} {trial_type!r} trial(s), expected exactly {required}"
                )

        for pos in range(2, len(seq) + 1):
            if seq[pos - 1] == I and seq[pos - 2] == I:
                issues.append(f"{label} has consecutive IDKs at positions {pos - 1}-{pos}")

        w_positions = tuple(p for p, t in enumerate(seq, start=1) if t == W)
        if w_positions != OPTION6_W_POSITIONS:
            issues.append(
                f"{label} has W at positions {list(w_positions)}, expected "
                f"{list(OPTION6_W_POSITIONS)}"
            )

        idk_positions = [p for p, t in enumerate(seq, start=1) if t == I]
        if not idk_positions or idk_positions[0] != OPTION6_FIRST_IDK_POSITION:
            issues.append(
                f"{label} first IDK is at position "
                f"{idk_positions[0] if idk_positions else None}, expected "
                f"{OPTION6_FIRST_IDK_POSITION}"
            )

        for probe_pos, required in OPTION6_PRIOR_IDKS_AT_PROBE.items():
            prior = sum(1 for t in seq[: probe_pos - 1] if t == I)
            if prior != required:
                issues.append(
                    f"{label} has {prior} IDK(s) before position {probe_pos}, "
                    f"expected {required}"
                )

        contexts = [probe_context(seq, p) for p in OPTION6_PRIMARY_PROBE_POSITIONS]
        if sorted(c or "" for c in contexts) != sorted(contexts_required):
            issues.append(
                f"{label} primary probes at positions "
                f"{list(OPTION6_PRIMARY_PROBE_POSITIONS)} have contexts {contexts}; "
                f"expected exactly one {PROBE_IMMEDIATE!r} and one {PROBE_ONE_BETWEEN!r}"
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
        Human-readable label e.g. "A".
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
