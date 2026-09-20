"""
Per-trial measurement logic for the overreliance experiment.

This module is the single source of truth for every derived value that ends
up in a trial log (app/routes.py's quiz() POST handler) and every aggregate
metric computed from trial logs (scripts/export_data.py). Exact formulas and
denominators are documented in docs/MEASURES.md — keep that file and this
module in sync; nothing here should silently diverge from what's written
there.
"""

import os
from enum import Enum

from .trial_types import TrialType

# ---------------------------------------------------------------------------
# Reliance coding
# ---------------------------------------------------------------------------


class RelianceCode(str, Enum):
    """
    Binary accept/reject reliance coding, extended to be nullable.

    The original study's coding assumed every trial carries a recommendation
    to accept or reject. That assumption breaks on IDK trials — there is no
    endorsed answer to be misled by or to correctly reject — so IDK trials
    get NOT_APPLICABLE rather than being coerced into REJECTED (which would
    misrepresent abstention as a rejection *decision* the participant never
    had to make). See docs/MEASURES.md for how this changes every aggregate
    that used to divide by "all trials".
    """

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


def get_ai_endorsed_answer(question, variant):
    """
    Return the option string the AI's recommendation endorses for this
    variant, or None if there isn't one to endorse.

      "correct"          → the question's ground-truth answer (AI always
                            recommends it)
      "confident_wrong"  → found by checking which option string appears in
                            the confident_wrong recommendation text; None if
                            zero or more than one option matches (ambiguous —
                            see docs/MEASURES.md's "known limitations" section)
      "idk"              → None (no specific recommendation made — this is
                            the defining property of the condition, not a
                            data gap)
    """
    if variant == TrialType.CORRECT.value:
        return question["correct"]
    if variant == TrialType.IDK.value:
        return None
    rec_text = question["variants"][TrialType.CONFIDENT_WRONG.value]["initial_recommendation"]
    matches = [opt for opt in question["options"] if opt in rec_text]
    return matches[0] if len(matches) == 1 else None


def compute_reliance_code(ai_endorsed_answer, participant_final_answer):
    """
    ACCEPTED/REJECTED require an endorsed answer to accept or reject.
    NOT_APPLICABLE covers both IDK trials and the rare ambiguous-match case
    in get_ai_endorsed_answer — both mean "there was nothing well-defined to
    accept or reject," which is exactly what NOT_APPLICABLE is for.
    """
    if ai_endorsed_answer is None:
        return RelianceCode.NOT_APPLICABLE.value
    return (
        RelianceCode.ACCEPTED.value
        if participant_final_answer == ai_endorsed_answer
        else RelianceCode.REJECTED.value
    )


# ---------------------------------------------------------------------------
# Positional covariates
# ---------------------------------------------------------------------------


def compute_covariates(realized_types, position_0idx):
    """
    Positional covariates for the trial at `position_0idx` (0-indexed) in
    this participant's realized type-at-position vector `realized_types`
    (a list of TrialType string values, one per presentation position).

    Computed purely from trial types already shown at earlier positions in
    THIS participant's own sequence — never from other participants, and
    never from positions not yet reached, so this is safe to compute at
    write time (immediately when a trial is answered), not just at export.
    """
    prior = realized_types[:position_0idx]

    n_idk_seen_so_far = sum(1 for t in prior if t == TrialType.IDK.value)
    n_cw_seen_so_far = sum(1 for t in prior if t == TrialType.CONFIDENT_WRONG.value)
    preceding_trial_type = prior[-1] if prior else None

    trials_since_last_idk = None
    for offset, t in enumerate(reversed(prior), start=1):
        if t == TrialType.IDK.value:
            trials_since_last_idk = offset
            break

    current_type = realized_types[position_0idx]
    is_post_idk_cw = (
        current_type == TrialType.CONFIDENT_WRONG.value
        and preceding_trial_type == TrialType.IDK.value
    )

    return {
        "trials_since_last_idk": trials_since_last_idk,
        "n_idk_seen_so_far": n_idk_seen_so_far,
        "n_cw_seen_so_far": n_cw_seen_so_far,
        "preceding_trial_type": preceding_trial_type,
        "is_post_idk_cw": is_post_idk_cw,
    }


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


_SECTION_LABELS = ["early", "middle", "late"]


def section_for_position(position_1idx):
    """Map a 1-indexed trial position to "early"/"middle"/"late" using
    sequences.SECTION_BOUNDARIES, so this always matches the Phase 2
    self-test's own section definition instead of a second hardcoded copy."""
    from .sequences import SECTION_BOUNDARIES  # local import: avoids a cycle at module load

    idx0 = position_1idx - 1
    for label, (start, end) in zip(_SECTION_LABELS, SECTION_BOUNDARIES):
        if start <= idx0 < end:
            return label
    raise ValueError(
        f"position {position_1idx} (0-indexed {idx0}) is not covered by "
        f"SECTION_BOUNDARIES={SECTION_BOUNDARIES}"
    )


# ---------------------------------------------------------------------------
# Config flags
# ---------------------------------------------------------------------------


def trial_confidence_rating_enabled() -> bool:
    """
    Whether to collect a confidence rating directly on the trial page itself
    (in addition to the pre-existing post-trial confidence/trust/helpfulness
    survey in post_survey.html, which is unaffected by this flag).

    Default OFF. Turning this on adds a question to every trial — flagged
    here deliberately, since that is itself a task-design change, not a
    free instrumentation addition. See docs/MEASURES.md.
    """
    return os.getenv("ENABLE_TRIAL_CONFIDENCE_RATING", "false").strip().lower() == "true"
