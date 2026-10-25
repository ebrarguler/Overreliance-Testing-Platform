"""
Response-type sequences for the overreliance experiment.

Each sequence defines the AI-response type at every presentation position.
Independently, each participant gets their own realized problem-to-position
mapping: which concrete question fills each "C"/"W"/"I" slot is rotated per
participant (seeded), so a given question is not always shown at the same
position, decoupling question identity from position.

Active design: blocks with a mirrored first block
(SEQUENCE_DESIGN_VERSION = "blocks_pq_v1")
-------------------------------------------------------------------
Two orders, 14 positions in three blocks of 5, 5 and 4. Every block holds
exactly one W and one IDK; the rest are C. Positions are numbered 1-14 in
the comments and constants below; the lists are 0-indexed.

    Position:  1  2  3  4  5 | 6  7  8  9 10 | 11 12 13 14
    Order P:   C  C  C  W  I | C  W  C  I  C |  C  I  W  C
    Order Q:   C  C  I  W  C | C  W  C  I  C |  C  I  W  C

The orders differ only at positions 3 and 5. Position 4 is the first W for
everyone: in P it comes before any IDK, in Q right after the first IDK. That
W is the primary probe for IDK spillover (H4): same position, same rotated
problem pool, same number of earlier W trials, only prior IDK exposure
differs. From position 6 on, both orders are identical.

To change the trial-type design:
  - Edit SEQUENCES / SEQUENCE_LABELS and bump SEQUENCE_DESIGN_VERSION, so
    records from different designs are never pooled under one label.
  - validate_sequences() runs at import time and raises ValueError
    immediately, listing every violation, if the table is malformed.

To change problem rotation:
  - Toggle ROTATE_PROBLEMS (False fixes problem order for e.g. a pilot).

Sequence assignment (which participant gets P or Q, balanced per site) and
the realized, seeded problem rotation live in app.utils.assignment, which
persists the result per participant and reuses it on resume rather than
reassigning. Participants assigned under an earlier design (Option 6 or the
six-order S1-S6 table, both kept below) keep that assignment.
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

SEQUENCE_DESIGN_VERSION = "blocks_pq_v1"

SEQUENCES = [
    # 1  2  3  4  5    6  7  8  9 10   11 12 13 14
    [C, C, C, W, I,   C, W, C, I, C,   C, I, W, C],  # P
    [C, C, I, W, C,   C, W, C, I, C,   C, I, W, C],  # Q
]

SEQUENCE_LABELS = ["P", "Q"]

# The three blocks (positions 1-5, 6-10, 11-14), as 0-indexed half-open
# (start, end) pairs. Stored per trial as `section` (early/middle/late).
SECTION_BOUNDARIES = [(0, 5), (5, 10), (10, 14)]

# Required exact per-sequence counts, and per block.
REQUIRED_TYPE_COUNTS = {C: 8, W: 3, I: 3}
REQUIRED_PER_BLOCK = {W: 1, I: 1}

N_ORDERS = 2
N_POSITIONS = 14
# Every order opens with this many C trials, so the assistant is seen to be
# right before it is ever wrong or unsure.
N_LEADING_C = 2

# The primary probe: the first W, at the same position in every order.
PQ_PROBE_POSITION = 4
PQ_BEFORE_IDK = "before_first_idk"   # order P
PQ_AFTER_IDK = "after_first_idk"     # order Q

# ---------------------------------------------------------------------------
# Earlier designs, kept so existing assignments are read and labelled
# correctly. Never used for new allocations.
# ---------------------------------------------------------------------------

# Option 6 (sequence_design_version "option6_v1"). Its primary probes were
# the W trials at positions 8 and 13: immediately after an IDK vs. one C
# trial between.
OPTION6_DESIGN_VERSION = "option6_v1"
OPTION6_SEQUENCES = {
    "A": [C, C, W, I, C, C, I, W, C, C, I, C, W, C],
    "B": [C, C, W, I, C, I, C, W, C, C, C, I, W, C],
}
OPTION6_PRIMARY_PROBE_POSITIONS = (8, 13)

PROBE_IMMEDIATE = "immediate_after_idk"      # I -> W
PROBE_ONE_BETWEEN = "one_correct_between"    # I -> C -> W

# The six-order design used before Option 6 (no sequence_design_version).
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


def pq_probe_context(types, position_1idx):
    """
    For a W trial: PQ_AFTER_IDK if any IDK came before it, else
    PQ_BEFORE_IDK. None for any non-W trial.
    """
    idx = position_1idx - 1
    if types[idx] != W:
        return None
    return PQ_AFTER_IDK if I in types[:idx] else PQ_BEFORE_IDK


def probe_context(types, position_1idx):
    """
    Option 6 only. How the W trial at `position_1idx` relates to the most
    recent IDK: PROBE_IMMEDIATE if the previous trial is IDK,
    PROBE_ONE_BETWEEN if the previous trial is C and the one before it is
    IDK, otherwise None. Returns None for any non-W trial.
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
    Check that SEQUENCES matches the blocks_pq design spec. Raises
    ValueError, listing every violation found (not just the first).
    """
    issues = []

    if len(SEQUENCES) != len(SEQUENCE_LABELS):
        raise ValueError(
            f"SEQUENCES has {len(SEQUENCES)} entries but "
            f"SEQUENCE_LABELS has {len(SEQUENCE_LABELS)}"
        )
    if len(SEQUENCES) != N_ORDERS:
        issues.append(f"expected exactly {N_ORDERS} active orders, got {len(SEQUENCES)}")
    if len(set(SEQUENCE_LABELS)) != len(SEQUENCE_LABELS):
        issues.append(f"SEQUENCE_LABELS are not unique: {SEQUENCE_LABELS}")
    if len({tuple(s) for s in SEQUENCES}) != len(SEQUENCES):
        issues.append("two active orders are identical")

    well_formed = []
    for label, seq in zip(SEQUENCE_LABELS, SEQUENCES):
        bad_types = [
            (pos, t) for pos, t in enumerate(seq, start=1) if t not in _VALID_TYPES
        ]
        for pos, t in bad_types:
            issues.append(
                f"{label} position {pos}: unknown trial type {t!r}; "
                f"allowed values are {sorted(_VALID_TYPES)}"
            )
        if len(seq) != N_POSITIONS:
            issues.append(f"{label} has {len(seq)} positions, expected {N_POSITIONS}")
            continue  # positional checks below assume the full length
        if bad_types:
            continue
        well_formed.append((label, seq))

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

        if any(t != C for t in seq[:N_LEADING_C]):
            issues.append(f"{label} does not open with {N_LEADING_C} C trials")

        for block_no, (start, end) in enumerate(SECTION_BOUNDARIES, start=1):
            block = Counter(seq[start:end])
            for trial_type, required in REQUIRED_PER_BLOCK.items():
                if block.get(trial_type, 0) != required:
                    issues.append(
                        f"{label} block {block_no} (positions {start + 1}-{end}) has "
                        f"{block.get(trial_type, 0)} {trial_type!r}, expected {required}"
                    )

        first_w = next((p for p, t in enumerate(seq, start=1) if t == W), None)
        if first_w != PQ_PROBE_POSITION:
            issues.append(
                f"{label} first W is at position {first_w}, expected {PQ_PROBE_POSITION}"
            )

    if len(well_formed) == len(SEQUENCES) == N_ORDERS:
        w_positions = {
            tuple(p for p, t in enumerate(seq, start=1) if t == W) for _, seq in well_formed
        }
        if len(w_positions) != 1:
            issues.append(f"W positions differ between orders: {sorted(w_positions)}")
        block_end = SECTION_BOUNDARIES[0][1]
        if len({tuple(seq[block_end:]) for _, seq in well_formed}) != 1:
            issues.append(f"orders differ after block 1 (position {block_end})")
        contexts = sorted(
            pq_probe_context(seq, PQ_PROBE_POSITION) or "" for _, seq in well_formed
        )
        if contexts != sorted([PQ_BEFORE_IDK, PQ_AFTER_IDK]):
            issues.append(
                f"the W at position {PQ_PROBE_POSITION} has contexts {contexts}; expected "
                f"one order {PQ_BEFORE_IDK!r} and one {PQ_AFTER_IDK!r}"
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
