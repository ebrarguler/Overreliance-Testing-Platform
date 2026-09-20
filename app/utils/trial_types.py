"""
Canonical trial-type enum for the overreliance experiment.

Introduced so trial type (which AI-response framing a participant sees on a
given trial) is a first-class value instead of the old implicit binary split
("correct" vs "everything else") that was welded to each question's identity
in questions.py. Welding trial type to question identity confounds question
difficulty with experimental condition once a third condition (IDK /
abstention) exists — a hard question would always be shown misleadingly and
an easy one never would. questions.py now carries all three response
variants per question so any question can be rotated into any condition;
see build_eligibility_pools() in sequences.py for how rotation picks from
whichever variants are actually authored.
"""

import os
import re
from enum import Enum

from dotenv import load_dotenv, find_dotenv

# Loaded defensively here, not just in routes.py: questions.py and
# sequences.py are imported (and run their own module-level validation) by
# routes.py *before* routes.py's own load_dotenv() call executes, so a
# STUDY_VERSION set only in .env would otherwise be missed.
load_dotenv(find_dotenv())


class TrialType(str, Enum):
    """The AI-response framing shown to a participant on a given trial."""

    CORRECT = "correct"
    CONFIDENT_WRONG = "confident_wrong"
    IDK = "idk"


def study_version() -> str:
    """
    Current study version, read fresh on every call (not cached at import
    time) so tests and process-level env changes are picked up correctly.

    Trial rotation (build_eligibility_pools / build_trial_sequence in
    sequences.py) does not currently branch on this value — a question is
    always eligible for a trial type based on what content is actually
    authored for it, regardless of STUDY_VERSION. This flag exists so
    STUDY_VERSION=original consumers can request the legacy binary
    classification via is_misleading() below.
    """
    return os.getenv("STUDY_VERSION", "extension")


def is_original_version() -> bool:
    return study_version() == "original"


def is_misleading(trial_type) -> bool:
    """
    Legacy binary correct/misleading flag, derived from the trial-type enum
    rather than stored separately — kept working for STUDY_VERSION=original
    and any other consumer (e.g. scripts/export_data.py) still built around
    the old two-way correct/misleading classification.

    True only for CONFIDENT_WRONG. IDK is deliberately *not* misleading: it
    gives no directional recommendation for a participant to be misled by.
    """
    return TrialType(trial_type) == TrialType.CONFIDENT_WRONG


_PLACEHOLDER_TEXT = "TODO"


def is_placeholder(variant) -> bool:
    """True if a variant slot has not been authored yet (still a TODO stub)."""
    return variant is None or variant.get("initial_recommendation") == _PLACEHOLDER_TEXT


REVIEW_PENDING = "pending_final_review"


def is_pending_review(variant) -> bool:
    """
    True if an authored variant is still awaiting the study lead's sign-off.

    Distinct from is_placeholder(): a pending-review variant has real, usable
    text (it will render correctly and read naturally to a participant), it
    simply has not been approved as final stimulus wording yet.

    There is deliberately no "approved" value to set. Sign-off is recorded by
    *deleting* the `review_status` key, so nothing in the question bank can be
    flipped to "approved for data collection" by editing a string — and an
    unreviewed variant can never be mistaken for a reviewed one.
    """
    return bool(variant) and variant.get("review_status") == REVIEW_PENDING


_DIGIT_RE = re.compile(r"\d")


def idk_leakage_issues(question) -> list:
    """
    Cheap guard against an IDK variant accidentally leaking the answer.

    Flags:
      - any digit character in IDK-facing text — participant-facing IDK
        copy should never need a number; the entire point of the variant is
        that it states no output and no leaning.
      - any of the question's own option strings appearing verbatim in that
        text — a stronger signal that a specific answer got quoted.

    Checks initial_recommendation and all three chat_prompt fields, since a
    leak in the system prompt would just as easily bias the model's later
    replies as a leak in the participant-facing text. Returns a list of
    human-readable issue strings (empty if clean). No-ops on a
    still-placeholder IDK variant.
    """
    variant = question["variants"][TrialType.IDK.value]
    if is_placeholder(variant):
        return []

    issues = []
    fields = {
        "initial_recommendation": variant["initial_recommendation"],
        "chat_prompt.system_context": variant["chat_prompt"]["system_context"],
        "chat_prompt.question_context": variant["chat_prompt"]["question_context"],
        "chat_prompt.stance": variant["chat_prompt"]["stance"],
    }
    for field_name, text in fields.items():
        if _DIGIT_RE.search(text):
            issues.append(
                f"question {question['id']}: idk.{field_name} contains a digit "
                f"({text!r})"
            )
        for opt in question.get("options", []):
            if opt and opt in text:
                issues.append(
                    f"question {question['id']}: idk.{field_name} contains "
                    f"option text {opt!r} — possible answer leakage ({text!r})"
                )
    return issues
