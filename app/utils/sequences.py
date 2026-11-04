"""
Response-type sequences for the overreliance experiment.

Each sequence defines the AI-response type at every presentation position.
Independently, each participant gets their own realized problem-to-position
mapping: which concrete question fills each "C"/"W"/"I" slot is rotated per
participant (seeded), so a given question is not always shown at the same
position, decoupling question identity from position.

Active design: blocks with a mirrored first block
(PQ_DESIGN_VERSION = "blocks_pq_v1"
SEQUENCE_DESIGN_VERSION = PQ_DESIGN_VERSION)
-------------------------------------------------------------------
Two orders, 14 positions in three blocks of 5, 5 and 4. Every block holds
exactly one W and one IDK; the rest are C. Positions are numbered 1-14 in
the comments and constants below; the lists are 0-indexed.

    Position:  1  2  3  4  5 | 6  7  8  9 10 | 11 12 13 14
    Order P:   C  C  C  W  I | C  W  C  I  C |  C  I  W  C
    Order Q:   C  C  I  W  C | C  W  C  I  C |  C  I  W  C

The orders differ only at positions 3 and 5. Position 4 is the first W for
everyone: in P it comes before any IDK, in Q right after the first IDK. That
W is the primary probe for IDK spillover (H2): same position, same rotated
problem pool, same number of earlier W trials. What differs is position 3:
a correct recommendation in P, an IDK in Q. So Q has seen one IDK and one
correct recommendation fewer, and the IDK comes right before the probe.
From position 6 on, both orders are identical.

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
from functools import lru_cache

from .trial_types import TrialType, is_in_rotation, is_placeholder

# Trial-type constants — values must match the variant keys in questions.py.
C = TrialType.CORRECT.value
W = TrialType.CONFIDENT_WRONG.value
I = TrialType.IDK.value

# ---------------------------------------------------------------------------
# Config — edit here only.
# ---------------------------------------------------------------------------

PQ_DESIGN_VERSION = "blocks_pq_v1"
SEQUENCE_DESIGN_VERSION = PQ_DESIGN_VERSION

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

# Uniform question randomization without author-assigned difficulty labels.
# The P/Q response-type positions and counts remain fixed. Stored with each
# assignment so earlier difficulty-balanced draws can be identified.
ROTATION_SCHEME = "uniform_random_v1"

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
    every TrialType it has real (non-placeholder) content authored for, and
    that is not held out of rotation (trial_types.is_in_rotation)."""
    return {
        t for t in TrialType
        if not is_placeholder(question["variants"][t.value])
        and is_in_rotation(question["variants"][t.value])
    }


def build_eligibility_pools(questions):
    """Map each TrialType to the list of question indices eligible for it."""
    pools = {t: [] for t in TrialType}
    for i, q in enumerate(questions):
        for t in eligible_trial_types(q):
            pools[t].append(i)
    return pools


def _assign_problems(seq_label, sequence, questions, rng):
    """
    Draw uniformly from feasible question-to-type assignments, without
    difficulty strata. Each question is used at most once. With all variants
    available (the current bank), this is a simple uniform shuffle.

    Restricted banks use completion counts to weight each choice, preventing
    both greedy dead ends and bias toward assignments found first. rng=None
    chooses the first feasible assignment for validation/deterministic pilots.
    """
    needed = Counter(sequence)
    pools = build_eligibility_pools(questions)
    assigned_to = {t: [] for t in TrialType}
    n = len(questions)
    types = tuple(TrialType)
    if n >= len(sequence) and all(len(pools[t]) == n for t in types if needed[t]):
        order = list(range(n))
        if rng:
            rng.shuffle(order)
        for q_idx, raw_type in zip(order, sequence):
            assigned_to[TrialType(raw_type)].append(q_idx)
        return assigned_to

    eligible = [eligible_trial_types(q) for q in questions]

    def choices(q_idx, remaining):
        for j, t in enumerate(types):
            if remaining[j] and t in eligible[q_idx]:
                reduced = list(remaining)
                reduced[j] -= 1
                yield t, tuple(reduced)
        if n - q_idx > sum(remaining):
            yield None, remaining  # surplus questions may be left unused

    @lru_cache(maxsize=None)
    def completions(q_idx, remaining):
        if not any(remaining):
            return 1
        if n - q_idx < sum(remaining):
            return 0
        return sum(completions(q_idx + 1, rest)
                   for _, rest in choices(q_idx, remaining))

    remaining = tuple(needed[t] for t in types)
    if not completions(0, remaining):
        raise ValueError(f"Sequence {seq_label}: no feasible assignment of distinct eligible questions")
    for q_idx in range(n):
        if not any(remaining):
            break
        draw = rng.randrange(completions(q_idx, remaining)) if rng else 0
        for t, rest in choices(q_idx, remaining):
            weight = completions(q_idx + 1, rest)
            if draw < weight:
                if t is not None:
                    assigned_to[t].append(q_idx)
                remaining = rest
                break
            draw -= weight

    return assigned_to


def validate_pool_sufficiency(questions):
    """
    Check that every configured sequence can be filled with distinct
    questions and available variants. No difficulty labels are required.

    Raises ValueError, listing every problem found, instead of a live
    participant being served a 'TODO' placeholder or hitting an error
    mid-session.
    """
    shortfalls = []
    for label, seq in zip(SEQUENCE_LABELS, SEQUENCES):
        try:
            _assign_problems(label, seq, questions, rng=None)
        except ValueError as exc:
            shortfalls.append(str(exc))

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
        + "\n\nVariants not available for rotation (TODO or held out):\n  "
        + "\n  ".join(todo_by_type or ["none"])
    )


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


def build_trial_sequence(seq_index, questions, seed=None):
    """
    Build one participant's realized trial sequence: which question fills
    each position, and which trial type it's shown under.

    Problems are assigned uniformly to eligible trial types without
    difficulty labels (ROTATION_SCHEME; see _assign_problems), then shuffled
    within each type across that type's positions.

    Parameters
    ----------
    seq_index : int
        Index into SEQUENCES (caller reduces modulo len(SEQUENCES)).
    questions : list
        The questions list from questions.py.
    seed : int, optional
        RNG seed for the draw and the shuffle. If None and ROTATE_PROBLEMS is
        True, a fresh seed is generated and returned so the caller can
        persist it for exact reproducibility later. Ignored (and returned as
        None) when ROTATE_PROBLEMS is False, since no randomness is used in
        that mode.

    Returns
    -------
    question_order : list[int]
        Question indices in presentation order, one per trial position.
    variant_assignments : dict[str, str]
        Maps str(question_index) → trial type shown to this participant.
    seq_label : str
        Human-readable label e.g. "P".
    seed : int or None
        The seed actually used (None if ROTATE_PROBLEMS is False).

    Raises
    ------
    ValueError
        If the bank cannot fill the sequence. validate_pool_sufficiency()
        should catch this at startup instead — this is a defense-in-depth
        check, not the primary guard.
    """
    sequence = SEQUENCES[seq_index]
    seq_label = SEQUENCE_LABELS[seq_index]

    if ROTATE_PROBLEMS:
        if seed is None:
            seed = random.SystemRandom().getrandbits(63)
        rng = random.Random(seed)
    else:
        seed = None
        rng = None

    assigned_to = _assign_problems(seq_label, sequence, questions, rng)
    if rng:
        for t in TrialType:
            rng.shuffle(assigned_to[t])
    # else: keep the deterministic ascending order.

    question_order = []
    variant_assignments = {}
    for trial_type in sequence:
        t = TrialType(trial_type)
        q_idx = assigned_to[t].pop(0)
        question_order.append(q_idx)
        variant_assignments[str(q_idx)] = t.value

    return question_order, variant_assignments, seq_label, seed


# ---------------------------------------------------------------------------
# Answer-option order
# ---------------------------------------------------------------------------

# New assignments show each question's options in a per-participant random
# order. In the bank the wrong target is option A for 8 of 14 problems, so a
# fixed order would mix "followed the AI" with "picked the first option".
# Assignments made before this have no stored order and show the bank order.
OPTION_ORDER_SCHEME = "seeded_shuffle_v1"


def build_option_orders(questions, seed):
    """
    Display order of every question's options for one participant, keyed by
    str(question index).

    Uses its own RNG stream derived from the assignment seed, so the problem
    draw for a given seed is unchanged. Each option is equally likely at each
    place. Without a seed (ROTATE_PROBLEMS off) every question keeps its bank
    order.
    """
    rng = random.Random(f"option-order:{seed}") if seed is not None else None
    orders = {}
    for q_idx, question in enumerate(questions):
        order = list(question["options"])
        if rng is not None:
            rng.shuffle(order)
        orders[str(q_idx)] = order
    return orders
