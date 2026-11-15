"""
How the participant reached the AI's recommendation on a trial (2026-11-15).

Each trial opens with two equal buttons:

  "Answer on my own first"             -> path "self_first"
  "See the AI's recommendation first"  -> path "ai_first"

On the self_first path the options unlock, and the participant saves an
answer before the recommendation is shown ("Save my answer and see the AI's
recommendation"), or gives up ("I'm not sure - show me the AI's
recommendation"), which is recorded as "self_first_abandoned". Either way the
recommendation is shown before the answer can be submitted, as before.

The pre-AI answer is stored on the server (trial_store.lock_pre_ai) before the
recommendation text is sent back, first write wins, so it cannot be changed
after the advice was seen. Designs this combines: the judge-advisor
"initial answer -> advice -> final answer" set-up and the "on demand" and
"update" conditions of Bucinca et al. (2021). See docs/MEASURES.md.
"""

import datetime
import hashlib

SELF_FIRST = "self_first"
AI_FIRST = "ai_first"
SELF_FIRST_ABANDONED = "self_first_abandoned"

# Button texts, kept neutral on purpose: neither path is presented as the
# better one.
PATH_BUTTON_LABELS = {
    SELF_FIRST: "Answer on my own first",
    AI_FIRST: "See the AI's recommendation first",
}


def self_first_button_on_left(participant_id):
    """
    Which side the "Answer on my own first" button is on, fixed for each
    participant across all trials. Derived from the random participant_id,
    so it is random across participants, needs no stored state, and is the
    same after a resume. Recorded on every trial as `self_first_button_left`.
    """
    digest = hashlib.sha256(f"path-buttons:{participant_id}".encode()).digest()
    return digest[0] % 2 == 0


def path_buttons(participant_id):
    """(path, label) pairs in the order shown on the page, left to right."""
    order = [SELF_FIRST, AI_FIRST]
    if not self_first_button_on_left(participant_id):
        order.reverse()
    return [(path, PATH_BUTTON_LABELS[path]) for path in order]


def access_path(access):
    """
    "self_first", "self_first_abandoned", "ai_first", or None (trials
    recorded before this design, or a page from before it).
    """
    path = (access or {}).get("path")
    if path == SELF_FIRST and access.get("pre_ai_abandoned"):
        return SELF_FIRST_ABANDONED
    return path


def _seconds_between(start_iso, end_iso):
    if not start_iso or not end_iso:
        return None
    try:
        start = datetime.datetime.fromisoformat(start_iso)
        end = datetime.datetime.fromisoformat(end_iso)
    except (TypeError, ValueError):
        return None
    return round((end - start).total_seconds(), 3)


def compute_pre_ai_fields(access, *, served_at, final_answer, correct_answer,
                          ai_endorsed_answer, options):
    """
    Trial-record fields describing the path and the answer given before the
    AI's recommendation. `access` is trial_store.trial_access(); `options`
    the options in the order shown.

    `switched_to_ai` follows the switch-fraction definition (Yin et al.
    2019; Zhang et al. 2020): it is defined only when the pre-AI answer
    differed from the AI's endorsed answer. When they already agreed, the
    trial cannot show whether the participant relied on the AI, so it is
    None (as on IDK trials, which endorse no answer).
    """
    access = access or {}
    path = access_path(access)
    own_attempt = path in (SELF_FIRST, SELF_FIRST_ABANDONED)
    pre_answer = access.get("pre_ai_answer") if path == SELF_FIRST else None
    has_pre = pre_answer is not None

    agreed_with_ai = None
    if has_pre and ai_endorsed_answer is not None:
        agreed_with_ai = pre_answer == ai_endorsed_answer

    return {
        "ai_access_path": path,
        "pre_ai": {
            "answer": pre_answer,
            "answer_place": options.index(pre_answer) + 1 if has_pre and pre_answer in options else None,
            "is_correct": pre_answer == correct_answer if has_pre else None,
            "agreed_with_ai": agreed_with_ai,
            "switched_after_ai": final_answer != pre_answer if has_pre else None,
            "switched_to_ai": final_answer == ai_endorsed_answer if agreed_with_ai is False else None,
            # Selection changes made before saving (or giving up).
            "n_changes": access.get("pre_ai_n_changes") if own_attempt else None,
            # On the abandoned path: an option that was selected but not
            # saved when the participant asked for the AI instead.
            "unsaved_selection": access.get("pre_ai_unsaved_selection") if path == SELF_FIRST_ABANDONED else None,
            # Page shown -> either button pressed.
            "path_choice_s": _seconds_between(served_at, access.get("path_chosen_at")),
            # "Answer on my own first" -> answer saved (or gave up).
            "own_attempt_s": (
                _seconds_between(access.get("path_chosen_at"), access.get("pre_ai_locked_at"))
                if own_attempt else None
            ),
        },
    }
