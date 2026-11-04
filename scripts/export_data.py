#!/usr/bin/env python3
"""
Export participant records from MongoDB to flat CSV files for analysis.

Usage
-----
    # from the project root:
    python scripts/export_data.py
    python scripts/export_data.py --output-dir path/to/dir
    python scripts/export_data.py --design blocks_pq_v1   # current P/Q design only
    python scripts/export_data.py --design option6_v1     # Option 6 only
    python scripts/export_data.py --design legacy_unversioned

Output files (written to exports/ by default)
----------------------------------------------
participants.csv  — one row per participant
    Columns: participant_id, site, prior_participation, participated_before,
             sequence_design_version, design_group, sequence,
             type_at_position, rotation_seed, rotation_scheme, timestamp,
             six pre-survey scale means, four final-survey scale means,
             total_duration_s

trials.csv        — one row per participant × trial (long format)
    See TRIAL_FIELDS below for the full column list. Most trial-level values
    (reliance_code, ai_endorsed_answer, covariates, revisions, chat counts,
    timestamps) are read directly from the trial record written by
    app/routes.py's quiz() POST handler — see docs/MEASURES.md for exact
    definitions and app/utils/measures.py for the code that computes them.
    This script does not recompute them from scratch; it flattens.

chat_messages.csv — one row per chat transcript entry (initial
    recommendation request, follow-up attempt, technical error), with the
    full message text for later coding. No interpretation of what the
    participant was asking for is added.

aggregates.json   — sample-level metrics from docs/MEASURES.md §8, with
    denominators reported alongside every rate so a reader never has to
    guess what was divided by what. Computed separately per design_group
    and never pooled across designs.

Designs
-------
`sequence_design_version` is exported exactly as stored: "blocks_pq_v1"
for the current P/Q design, "option6_v1" for Option 6 records, blank for
records made before design versioning (the six-order S1-S6 design).
`design_group` is the same value with blanks shown as "legacy_unversioned",
so the designs can be filtered and summarised apart. --design restricts
every output file to one design_group.

Privacy
-------
UZH collects no email or name; UF identity is stored separately. Identifying fields found on legacy
records (email, firstName, lastName, uf_id, classSchool) are excluded at the
query level. Participants are numbered 1, 2, 3 … in order of study
completion (sorted by timestamp); the participant_id column is the study's
internal UUID (app/utils/assignment.py), never the numbering or the email.

Schema assumption
------------------
This script assumes trial records were written by the current
app/routes.py (with the rich per-trial schema — reliance_code,
covariates, etc.). Records written before that change won't have these
fields and will show up with blanks; this script does not attempt to
reconstruct them.
"""

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Make the project root importable when the script is run from any directory.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv, find_dotenv  # noqa: E402

load_dotenv(find_dotenv())

from app.utils.db import init_db
from app.utils import db as database
from app.utils import trial_store  # noqa: E402

PQ_DESIGN = "blocks_pq_v1"
LEGACY_DESIGN_GROUP = "legacy_unversioned"

# ---------------------------------------------------------------------------
# Config — edit here to change scale composition.
# ---------------------------------------------------------------------------

# Pre-survey scale definitions.
# Key   → column prefix in participants.csv  (pre_<key>_mean)
# Value → list of item names as stored in pre_survey_answers in MongoDB.
PRE_SURVEY_SCALES = {
    "programming_self_efficacy": [
        "independent_programming",
        "learn_languages",
        "identify_improvements",
    ],
    "ai_trust": [
        "ai_dependability",
        "ai_reliability",
        "ai_explanation",
    ],
    "ai_concerns": [
        "ai_dependency",
        "incorrect_advice",
        "blind_trust",
        "learning_hindrance",
    ],
    "need_for_cognition": [
        "code_understanding",
        "solution_exploration",
        "concept_understanding",
        "self_solving",
        "complex_problems",
    ],
    "programming_literacy": [
        "fundamental_concepts",
        "code_comprehension",
        "data_structures",
        "oop_principles",
        "explain_concepts",
        "language_proficiency",
    ],
    "ai_literacy": [
        "ai_principles",
        "ai_use_cases",
        "ai_risks",
        "ai_prompting",
    ],
}

# Final-survey part 2 (change over the study), exported item by item; not
# part of any scale. Absent (blank) on records made before these items.
FINAL_CHANGE_FIELDS = ["became_more_skeptical", "trust_increased", "idk_changed_use",
                       "idk_more_credible", "usage_change"]

# Final-survey scale definitions.
# Key   → column prefix in participants.csv  (final_<key>_mean)
# Value → list of item names as stored in final_survey_answers in MongoDB.
FINAL_SURVEY_SCALES = {
    "overreliance_behaviour": [
        "blind_acceptance",
        "questioned_recommendations",
        "immediate_usage",
        "verified_answers",
    ],
    "trust_in_chatbot": [
        "reliable_advice",
        "trustworthy_explanations",
        "count_recommendations",
        "depend_solving",
    ],
    "satisfaction_with_ai": [
        "recommendations_helpful",
        "explanations_clear",
        "appropriate_responses",
        "quality_satisfaction",
    ],
    "decision_making": [
        "careful_consideration",
        "knowledge_combination",
        "own_decisions",
        "critical_evaluation",
        "independence",
    ],
}

# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def scale_mean(responses: dict, items: list) -> float | None:
    """
    Compute the mean of the numeric values for the listed item keys.

    Silently skips items that are absent, None, or non-numeric (e.g. free-text).
    Returns None when no valid values are found.
    """
    values = []
    for key in items:
        try:
            value = float(responses[key])
            if not 1 <= value <= 7:
                continue
            values.append(8 - value if key in {"questioned_recommendations", "verified_answers"} else value)
        except (KeyError, TypeError, ValueError):
            pass
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def design_group(doc: dict) -> str:
    """The stored sequence_design_version, or LEGACY_DESIGN_GROUP if absent."""
    return doc.get("sequence_design_version") or LEGACY_DESIGN_GROUP


# ---------------------------------------------------------------------------
# Participant-level row
# ---------------------------------------------------------------------------


def build_participant_row(doc: dict, participant_id: int) -> dict:
    """
    Produce one participants.csv row from a MongoDB document.
    Identifying fields are never read.
    """
    pre = doc.get("pre_survey_answers") or {}
    final = doc.get("final_survey_answers") or {}
    times = doc.get("times") or []

    row: dict = {
        "participant_id": participant_id,
        "participant_uuid": doc.get("participant_id", ""),
        "site": doc.get("site", ""),
        "prior_participation": doc.get("prior_participation", ""),
        "participated_before": doc.get("participated_before", ""),
        "sequence_design_version": doc.get("sequence_design_version", ""),
        "design_group": design_group(doc),
        "sequence": doc.get("sequence_label", ""),
        "type_at_position": " ".join(doc.get("type_at_position") or []),
        "rotation_seed": doc.get("rotation_seed", ""),
        "rotation_scheme": doc.get("rotation_scheme", ""),
        "timestamp": str(doc.get("timestamp", "")),
    }

    for field in DEMOGRAPHIC_FIELDS:
        value = (doc.get("demographics") or {}).get(field, "")
        row[field] = json.dumps(value) if isinstance(value, list) else value
    row["n_resumes"] = doc.get("n_resumes", 0)
    row["runtime"] = json.dumps(doc.get("runtime", {}), sort_keys=True)
    for field in ("trust_this_assistant", "trust_chatgpt", "study_purpose_guess", "assistant_observations", "other_tools",
                  *FINAL_CHANGE_FIELDS):
        row[field] = final.get(field, "")

    for scale, items in PRE_SURVEY_SCALES.items():
        row[f"pre_{scale}_mean"] = scale_mean(pre, items)

    for scale, items in FINAL_SURVEY_SCALES.items():
        row[f"final_{scale}_mean"] = scale_mean(final, items)

    # Sum all non-zero numeric entries; zeros are insert_at_index padding for
    # positions not yet filled (should not occur in a complete record).
    numeric_times = [t for t in times if isinstance(t, (int, float)) and t != 0]
    row["total_duration_s"] = round(sum(numeric_times), 2) if numeric_times else ""

    return row


# ---------------------------------------------------------------------------
# Trial-level rows — flattens the rich per-trial record; does not recompute.
# ---------------------------------------------------------------------------


def build_trial_rows(doc: dict, participant_id: int) -> list[dict]:
    """
    Produce one trials.csv row per trial from a MongoDB document.

    Storage layout (set by routes.py insert_at_index):
      answers[question_index]  — the full per-trial record (keyed by
                                  question index, not presentation position)

    question_order gives presentation order; each entry is a question index
    used to look up the corresponding answers[] record.
    """
    question_order = doc.get("question_order") or []
    answers_list = doc.get("answers") or []
    post_surveys = doc.get("post_survey_answers") or []
    participant_uuid = doc.get("participant_id", "")

    rows = []
    for pos_0, q_idx in enumerate(question_order):
        record = None
        if isinstance(answers_list, list) and q_idx < len(answers_list):
            candidate = answers_list[q_idx]
            if isinstance(candidate, dict):
                record = candidate
        if record is None:
            # Trial wasn't reached / recorded (incomplete session) — skip
            # rather than emit a row of blanks that looks like real data.
            continue

        # New records join on identity; only wholly unlabelled legacy lists
        # use their historical presentation order.
        if any("position" in rating for rating in post_surveys):
            post = next((rating for rating in post_surveys
                         if rating.get("position") == pos_0 + 1 and rating.get("question_index") == q_idx), {})
        else:
            post = post_surveys[pos_0] if pos_0 < len(post_surveys) else {}
        timestamps = record.get("timestamps") or {}
        revisions = record.get("answer_revisions") or {}
        covariates = record.get("covariates") or {}
        chat = record.get("chat") or {}
        clipboard = record.get("clipboard") or {}
        visibility = record.get("visibility") or {}
        runtime = _trial_runtime(record)

        rows.append({
            "participant_id": participant_id,
            "participant_uuid": participant_uuid,
            "sequence_design_version": doc.get("sequence_design_version", ""),
            "design_group": design_group(doc),
            "sequence_id": record.get("sequence_id", doc.get("sequence_label", "")),
            "position": record.get("position", pos_0 + 1),
            "section": record.get("section", ""),
            "problem_id": record.get("problem_id", q_idx),
            "difficulty": record.get("difficulty", ""),
            "trial_type": record.get("trial_type", record.get("variant_shown", "")),
            # Which protocol produced this trial. Records under one design
            # label can differ in problem rotation and chat prompt; never pool
            # them without checking these (blank on records that predate them).
            "rotation_scheme": doc.get("rotation_scheme", ""),
            "protocol_version": runtime.get("protocol_version", ""),
            "study_phase": runtime.get("study_phase", ""),

            "participant_answer": record.get("answer", ""),
            "correct_answer": record.get("correct_answer", ""),
            "is_correct": record.get("is_correct", ""),

            "ai_endorsed_answer": record.get("ai_endorsed_answer"),
            "target_wrong_answer": record.get("target_wrong_answer"),
            "chose_target_wrong_answer": record.get("chose_target_wrong_answer"),
            "aligned_with_ai": record.get("aligned_with_ai"),
            "reliance_code": record.get("reliance_code", ""),

            "served_at": timestamps.get("served_at", ""),
            "first_interaction_at": timestamps.get("first_interaction_at", ""),
            "first_submit_at": timestamps.get("first_submit_at", ""),
            "final_submit_at": timestamps.get("final_submit_at", ""),
            "dwell_time_s": record.get("dwell_time_s", ""),
            "recommendation_delay_ms": record.get("recommendation_delay_ms") or "",
            "idk_phrasing_id": record.get("idk_phrasing_id"),

            "initial_answer": revisions.get("initial_answer", ""),
            "final_answer": revisions.get("final_answer", record.get("answer", "")),
            "n_changes": revisions.get("n_changes", ""),

            "n_followup_turns": chat.get("n_turns", ""),
            "n_followup_turns_attempted": chat.get("n_turns_attempted", ""),
            "n_followup_errors": _count_followup_errors(chat.get("transcript")),
            "n_late_turns": chat.get("n_late_turns", ""),
            "n_messages_sent": chat.get("n_messages_sent", ""),

            "n_hidden": visibility.get("n_hidden", ""),
            "hidden_s": visibility.get("hidden_s", ""),
            "n_blur": visibility.get("n_blur", ""),

            "n_copy": clipboard.get("n_copy", ""),
            "n_paste": clipboard.get("n_paste", ""),
            "pasted_chars": clipboard.get("pasted_chars", ""),

            "trials_since_last_idk": covariates.get("trials_since_last_idk"),
            "n_idk_seen_so_far": covariates.get("n_idk_seen_so_far", ""),
            "n_cw_seen_so_far": covariates.get("n_cw_seen_so_far", ""),
            "preceding_trial_type": covariates.get("preceding_trial_type"),
            "is_post_idk_cw": covariates.get("is_post_idk_cw", ""),

            "idk_encounter_number": record.get("idk_encounter_number", ""),
            "is_primary_pq_probe": record.get("is_primary_pq_probe", ""),
            "pq_probe_context": record.get("pq_probe_context", ""),
            "is_primary_option6_probe": record.get("is_primary_option6_probe", ""),
            "option6_probe_context": record.get("option6_probe_context", ""),

            "confidence_rating": record.get("confidence_rating"),
            "post_confidence": post.get("confidence", ""),
            "post_trust": post.get("trust", ""),
            "post_helpfulness": post.get("helpfulness", ""),
            "post_difficulty": post.get("difficulty", ""),
            "option_order": json.dumps(record.get("option_order", [])),
            "option_order_scheme": record.get("option_order_scheme") or "",
            "answer_option_place": record.get("answer_option_place", ""),
            "correct_option_place": record.get("correct_option_place", ""),
            "target_wrong_option_place": record.get("target_wrong_option_place", ""),
        })

    return rows


def _trial_runtime(record):
    """Runtime metadata (protocol_version, study_phase, …) logged with this
    trial's initial recommendation; {} on records that predate it."""
    for entry in (record.get("chat") or {}).get("transcript") or []:
        if entry.get("is_initial_recommendation"):
            return entry
    return {}


def _count_followup_errors(transcript):
    if not isinstance(transcript, list):
        return ""
    return sum(
        1 for e in transcript
        if not e.get("is_initial_recommendation") and e.get("is_error") is True
    )


# ---------------------------------------------------------------------------
# Chat-message rows — one per transcript entry, text kept verbatim.
# ---------------------------------------------------------------------------


def _entry_type(entry: dict) -> str:
    """Stored entry_type, or the same classification read from the
    is_initial_recommendation flag on records written before entry_type."""
    if entry.get("entry_type"):
        return entry["entry_type"]
    flag = entry.get("is_initial_recommendation")
    if flag is True:
        return "initial_recommendation_request"
    if flag is False:
        return "followup"
    return ""


def build_chat_rows(doc: dict, participant_id: int) -> list[dict]:
    """One row per chat transcript entry, in presentation order of trials."""
    question_order = doc.get("question_order") or []
    answers_list = doc.get("answers") or []
    rows = []
    for pos_0, q_idx in enumerate(question_order):
        record = answers_list[q_idx] if q_idx < len(answers_list) else None
        if not isinstance(record, dict):
            continue
        transcript = (record.get("chat") or {}).get("transcript") or []
        followup_n = 0
        for i, entry in enumerate(transcript, start=1):
            kind = _entry_type(entry)
            is_error = entry.get("is_error")
            if kind == "followup":
                followup_n += 1
            rows.append({
                "participant_id": participant_id,
                "participant_uuid": doc.get("participant_id", ""),
                "sequence_design_version": doc.get("sequence_design_version", ""),
                "design_group": design_group(doc),
                "sequence_id": record.get("sequence_id", doc.get("sequence_label", "")),
                "position": record.get("position", pos_0 + 1),
                "problem_id": record.get("problem_id", q_idx),
                "trial_type": record.get("trial_type", record.get("variant_shown", "")),
                "message_index": i,
                "entry_type": kind,
                "followup_attempt_number": followup_n if kind == "followup" else "",
                "is_successful_ai_reply": (
                    (is_error is False) if kind == "followup" and is_error is not None else ""
                ),
                "is_error": "" if is_error is None else is_error,
                "error_reason": entry.get("error_reason", ""),
                "shown_to_participant": entry.get("shown_to_participant", ""),
                "timestamp": entry.get("timestamp", ""),
                "sent_at": entry.get("sent_at", ""),
                "user_message": entry.get("user_message", ""),
                "assistant_message": entry.get("assistant_message") or "",
                "raw_completion": entry.get("raw_completion") or "",
                **{field: entry.get(field, "") for field in CHAT_METADATA_FIELDS},
                "mentioned_options": json.dumps(entry.get("mentioned_options", [])),
            })
    return rows


# ---------------------------------------------------------------------------
# Aggregates — docs/MEASURES.md §8, formulas and denominators inline.
# ---------------------------------------------------------------------------


def _rate(numerator_count: int, denominator_count: int):
    """Returns (rate, numerator, denominator); rate is None if denominator is 0
    (never silently reported as 0% when there was nothing to divide by)."""
    if denominator_count == 0:
        return None, numerator_count, denominator_count
    return round(numerator_count / denominator_count, 4), numerator_count, denominator_count


def compute_aggregates(all_trial_rows: list[dict]) -> dict:
    """Sample-level metrics from docs/MEASURES.md §8. Every rate is reported
    as {rate, numerator, denominator} so the denominator is never implicit."""
    by_type = {
        "correct": [r for r in all_trial_rows if r["trial_type"] == "correct"],
        "confident_wrong": [r for r in all_trial_rows if r["trial_type"] == "confident_wrong"],
        "idk": [r for r in all_trial_rows if r["trial_type"] == "idk"],
    }

    def named_rate(rows, predicate):
        num = sum(1 for r in rows if predicate(r))
        rate, n, d = _rate(num, len(rows))
        return {"rate": rate, "numerator": n, "denominator": d}

    aggregates = {}

    # §8.1 reliance rate on misleading trials
    aggregates["reliance_rate_confident_wrong"] = named_rate(
        by_type["confident_wrong"], lambda r: r["reliance_code"] == "ACCEPTED"
    )
    # §8.2 appropriate-reliance rate on correct trials
    aggregates["appropriate_reliance_rate_correct"] = named_rate(
        by_type["correct"], lambda r: r["reliance_code"] == "ACCEPTED"
    )
    # §8.3 legacy-style overall accept rate — denominator is correct+cw ONLY, excludes idk
    cw_and_correct = by_type["correct"] + by_type["confident_wrong"]
    aggregates["overall_accept_rate_excl_idk"] = named_rate(
        cw_and_correct, lambda r: r["reliance_code"] == "ACCEPTED"
    )

    # §8.4 per-type outcomes, including idk reported on its own terms
    per_type = {}
    for t, rows in by_type.items():
        accuracy = named_rate(rows, lambda r: r["is_correct"] is True)
        followup = named_rate(rows, lambda r: isinstance(r["n_followup_turns"], int) and r["n_followup_turns"] > 0)
        revision = named_rate(rows, lambda r: isinstance(r["n_changes"], int) and r["n_changes"] > 0)
        dwell_values = [r["dwell_time_s"] for r in rows if isinstance(r["dwell_time_s"], (int, float))]
        mean_dwell = round(sum(dwell_values) / len(dwell_values), 4) if dwell_values else None
        per_type[t] = {
            "n_trials": len(rows),
            "accuracy": accuracy,
            "followup_rate": followup,
            "revision_rate": revision,
            "mean_dwell_time_s": mean_dwell,
        }
    aggregates["by_trial_type"] = per_type

    # §8.5 alignment-with-AI rate, restricted to trials with an endorsed answer
    endorsed = [r for r in all_trial_rows if r.get("ai_endorsed_answer") is not None]
    aggregates["alignment_with_ai_rate"] = named_rate(
        endorsed, lambda r: r.get("aligned_with_ai") is True
    )

    # §5 — is_post_idk_cw incidence. Fixed by each design's table: 0 for
    # legacy S1-S6, one per participant for Option 6 (A8 / B13), and for
    # blocks_pq one in order P (13) and two in order Q (4, 13). Anything
    # else means the computation or the data is off.
    is_post_idk_cw_count = sum(1 for r in all_trial_rows if r.get("is_post_idk_cw") is True)
    aggregates["is_post_idk_cw_incidence"] = {
        "count": is_post_idk_cw_count,
        "note": (
            "Expected 0 for legacy_unversioned (S1-S6), one per participant "
            "for option6_v1, and one per P / two per Q participant for "
            "blocks_pq_v1 — see docs/MEASURES.md section 5."
        ),
    }

    # §8.7 — blocks_pq primary probe (the first W, position 4), by whether
    # an IDK came before it. Present only when such trials exist.
    pq_probes = [r for r in all_trial_rows if r.get("is_primary_pq_probe") is True]
    if pq_probes:
        by_context = {}
        for context in ("before_first_idk", "after_first_idk"):
            rows = [r for r in pq_probes if r.get("pq_probe_context") == context]
            by_context[context] = {
                "n_trials": len(rows),
                "reliance_rate": named_rate(rows, lambda r: r["reliance_code"] == "ACCEPTED"),
                "accuracy": named_rate(rows, lambda r: r["is_correct"] is True),
            }
        aggregates["pq_primary_probe"] = by_context

    # §8.8 — Option 6 primary probes (W at positions 8 and 13), by context.
    # Present only when such trials exist (i.e. option6_v1 records).
    probes = [r for r in all_trial_rows if r.get("is_primary_option6_probe") is True]
    if probes:
        by_context = {}
        for context in ("immediate_after_idk", "one_correct_between"):
            rows = [r for r in probes if r.get("option6_probe_context") == context]
            by_context[context] = {
                "n_trials": len(rows),
                "reliance_rate": named_rate(rows, lambda r: r["reliance_code"] == "ACCEPTED"),
                "accuracy": named_rate(rows, lambda r: r["is_correct"] is True),
            }
        aggregates["option6_primary_probes"] = by_context

    return aggregates


def protocol_label(row: dict) -> str:
    """Rotation scheme and chat protocol of a trial row, e.g.
    "uniform_random_v1 | durable_natural_v1" ("none" where not recorded)."""
    return f"{row.get('rotation_scheme') or 'none'} | {row.get('protocol_version') or 'none'}"


def compute_aggregates_by_design(all_trial_rows: list[dict]) -> dict:
    """compute_aggregates() run separately for each design_group; designs
    are never pooled into one summary.

    One design label can cover several protocols (problem rotation, chat
    prompt). Each summary lists them under "protocols"; when there is more
    than one, it carries a warning and a per-protocol breakdown, so pilot
    records from an earlier protocol are never silently pooled.
    """
    groups: dict[str, list[dict]] = {}
    for row in all_trial_rows:
        groups.setdefault(row["design_group"], []).append(row)
    result = {}
    for group, rows in sorted(groups.items()):
        summary = compute_aggregates(rows)
        by_protocol: dict[str, list[dict]] = {}
        for row in rows:
            by_protocol.setdefault(protocol_label(row), []).append(row)
        summary["protocols"] = sorted(by_protocol)
        if len(by_protocol) > 1:
            summary["protocol_warning"] = (
                "This design group pools several rotation schemes or chat protocols; "
                "use by_protocol or filter trials.csv on rotation_scheme/protocol_version."
            )
            summary["by_protocol"] = {
                label: compute_aggregates(protocol_rows)
                for label, protocol_rows in sorted(by_protocol.items())
            }
        result[group] = summary
    return result


# ---------------------------------------------------------------------------
# CSV column order
# ---------------------------------------------------------------------------

DEMOGRAPHIC_FIELDS = ["age_category", "gender", "ethnicity", "ai_usage", "ai_frequency", "ai_use_type",
                      "academic_level", "major_category", "minor_category", "gpa", "gpa_scale"]
CHAT_METADATA_FIELDS = ["policy_filter_reason", "after_answer", "requested_model", "response_model", "prompt_hash", "app_commit",
                        "app_source_hash", "app_dirty", "study_phase", "protocol_version", "stated_option", "option_coding_status"]
PARTICIPANT_FIELDS = (
    [
        "participant_id", "participant_uuid", "site", "prior_participation",
        "participated_before", "sequence_design_version", "design_group",
        "sequence", "type_at_position", "rotation_seed", "rotation_scheme", "timestamp",
    ]
    + [f"pre_{s}_mean" for s in PRE_SURVEY_SCALES]
    + [f"final_{s}_mean" for s in FINAL_SURVEY_SCALES]
    + ["total_duration_s", "n_resumes", "runtime", "trust_this_assistant", "trust_chatgpt",
       "study_purpose_guess", "assistant_observations", "other_tools", *FINAL_CHANGE_FIELDS] + DEMOGRAPHIC_FIELDS
)

TRIAL_FIELDS = [
    "participant_id",
    "participant_uuid",
    "sequence_design_version",
    "design_group",
    "sequence_id",
    "position",
    "section",
    "problem_id",
    "difficulty",
    "trial_type",
    "participant_answer",
    "correct_answer",
    "is_correct",
    "ai_endorsed_answer", "target_wrong_answer", "chose_target_wrong_answer",
    "aligned_with_ai",
    "reliance_code",
    "served_at",
    "first_interaction_at",
    "first_submit_at",
    "final_submit_at",
    "dwell_time_s",
    "idk_phrasing_id",
    "initial_answer",
    "final_answer",
    "n_changes",
    "n_followup_turns",
    "n_followup_turns_attempted",
    "n_followup_errors",
    "n_copy",
    "n_paste",
    "pasted_chars",
    "trials_since_last_idk",
    "n_idk_seen_so_far",
    "n_cw_seen_so_far",
    "preceding_trial_type",
    "is_post_idk_cw",
    "idk_encounter_number",
    "is_primary_pq_probe",
    "pq_probe_context",
    "is_primary_option6_probe",
    "option6_probe_context",
    "confidence_rating",
    "post_confidence",
    "post_trust",
    "post_helpfulness", "post_difficulty", "option_order",
    "option_order_scheme", "answer_option_place", "correct_option_place", "target_wrong_option_place",
    "rotation_scheme", "protocol_version", "study_phase",
    "n_late_turns", "n_messages_sent", "n_hidden", "hidden_s", "n_blur",
    "recommendation_delay_ms",
]

CHAT_FIELDS = [
    "participant_id",
    "participant_uuid",
    "sequence_design_version",
    "design_group",
    "sequence_id",
    "position",
    "problem_id",
    "trial_type",
    "message_index",
    "entry_type",
    "followup_attempt_number",
    "is_successful_ai_reply",
    "is_error",
    "error_reason",
    "shown_to_participant",
    "timestamp",
    "sent_at",
    "user_message",
    "assistant_message",
    "raw_completion", "mentioned_options",
] + CHAT_METADATA_FIELDS

# Identifying fields some legacy `users` records carry; never exported.
IDENTIFYING_FIELDS_PROJECTION = {
    "email": 0, "firstName": 0, "lastName": 0, "uf_id": 0, "classSchool": 0,
}

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def write_exports(docs: list[dict], output_dir: Path, design: str | None = None) -> dict:
    """
    Write participants.csv, trials.csv, chat_messages.csv and aggregates.json
    for `docs` (already sorted by timestamp). `design` restricts everything
    to one design_group. Returns a summary dict.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    n_exported = 0
    n_skipped = 0
    n_other_design = 0
    participant_rows: list[dict] = []
    all_trial_rows: list[dict] = []
    all_chat_rows: list[dict] = []

    # Numbering covers every record, so a participant keeps the same number
    # whichever --design is selected.
    for participant_id, doc in enumerate(docs, start=1):
        if design is not None and design_group(doc) != design:
            n_other_design += 1
            continue
        if doc.get("data_use", "keep") != "keep":
            n_skipped += 1
            continue
        if not doc.get("question_order"):
            print(
                f"  Skipping record {doc.get('_id')}: no question_order "
                "(study not completed).",
                file=sys.stderr,
            )
            n_skipped += 1
            continue
        try:
            participant_row = build_participant_row(doc, participant_id)
            trial_rows = build_trial_rows(doc, participant_id)
            chat_rows = build_chat_rows(doc, participant_id)
        except Exception as exc:
            print(f"  Error processing record {doc.get('_id')}: {exc}", file=sys.stderr)
            n_skipped += 1
            continue
        participant_rows.append(participant_row)
        all_trial_rows.extend(trial_rows)
        all_chat_rows.extend(chat_rows)
        n_exported += 1

    for name, fields, rows in (
        ("participants.csv", PARTICIPANT_FIELDS, participant_rows),
        ("trials.csv", TRIAL_FIELDS, all_trial_rows),
        ("chat_messages.csv", CHAT_FIELDS, all_chat_rows),
    ):
        with open(output_dir / name, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    aggregates = compute_aggregates_by_design(all_trial_rows)
    with open(output_dir / "aggregates.json", "w", encoding="utf-8") as af:
        json.dump(aggregates, af, indent=2, default=str)

    return {
        "n_exported": n_exported,
        "n_skipped": n_skipped,
        "n_other_design": n_other_design,
        "n_trial_rows": len(all_trial_rows),
        "n_chat_rows": len(all_chat_rows),
        "aggregates": aggregates,
    }



def write_attrition(output_dir, design=None):
    """Export counts for all consented participants, including dropouts.

    No identity, withdrawal codes, answers, or chat text are included.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for part in database.get_collection("participants").find({}, {"identity": 0, "withdrawal_code": 0}):
        pid = part["participant_id"]
        assignment = database.get_collection("participant_assignments").find_one({"participant_id": pid}) or {}
        group = design_group(assignment)
        if design and group != design:
            continue
        records = trial_store.trials_for(pid)
        rows.append({"participant_uuid": pid, "site": part.get("site"), "design_group": group,
                     # Lets dropout be compared between orders (P/Q).
                     "sequence_label": assignment.get("sequence_label", ""),
                     "rotation_scheme": assignment.get("rotation_scheme", ""),
                     "created_at": str(part.get("created_at", "")),
                     "completed_at": str(part.get("completed_at") or ""),
                     "n_resumes": part.get("n_resumes", 0),
                     "answered": len(records), "rated": sum(bool(r.get("rating")) for r in records),
                     "data_use": part.get("data_use", "pending"),
                     "completed": bool(part.get("completed_at"))})
    fields = ["participant_uuid", "site", "design_group", "sequence_label", "rotation_scheme",
              "created_at", "completed_at", "n_resumes", "answered", "rated", "data_use", "completed"]
    with (output_dir / "attrition.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return rows

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-dir",
        default="exports",
        metavar="DIR",
        help="Directory for output files (created if absent; default: exports/)",
    )
    parser.add_argument(
        "--design",
        default=None,
        metavar="GROUP",
        help=(
            f"Export only one design_group, e.g. {PQ_DESIGN} or "
            f"{LEGACY_DESIGN_GROUP} (default: all, summarised separately)"
        ),
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    print("Connecting to MongoDB…", file=sys.stderr)
    collection = init_db()

    # Exclude identifying fields at the query level, sort by completion time.
    docs = list(collection.find({}, IDENTIFYING_FIELDS_PROJECTION).sort("timestamp", 1))
    print(f"Found {len(docs)} record(s).", file=sys.stderr)

    for doc in docs:
        pid = doc.get("participant_id")
        part = database.get_collection("participants").find_one({"participant_id": pid}) or {}
        doc["data_use"] = part.get("data_use", doc.get("data_use", "keep"))
        if pid and trial_store.trials_for(pid):
            doc.update(trial_store.assemble(pid, doc.get("question_order", [])))
    write_attrition(output_dir, design=args.design)

    summary = write_exports(docs, output_dir, design=args.design)

    print(
        f"Exported {summary['n_exported']} participant(s), "
        f"{summary['n_trial_rows']} trial row(s), "
        f"{summary['n_chat_rows']} chat row(s).",
        file=sys.stderr,
    )
    if summary["n_other_design"]:
        print(
            f"  {summary['n_other_design']} record(s) from other designs left out.",
            file=sys.stderr,
        )
    if summary["n_skipped"]:
        print(f"  {summary['n_skipped']} record(s) skipped.", file=sys.stderr)
    for group, agg in summary["aggregates"].items():
        print(
            f"  {group}: is_post_idk_cw occurred "
            f"{agg['is_post_idk_cw_incidence']['count']} time(s).",
            file=sys.stderr,
        )
    print(f"  → {output_dir}/", file=sys.stderr)

if __name__ == "__main__":
    main()
