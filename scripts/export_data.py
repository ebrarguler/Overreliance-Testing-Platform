#!/usr/bin/env python3
"""
Export participant records from MongoDB to flat CSV files for analysis.

Usage
-----
    # from the project root:
    python scripts/export_data.py
    python scripts/export_data.py --output-dir path/to/dir

Output files (written to exports/ by default)
----------------------------------------------
participants.csv  — one row per participant
    Columns: participant_id, sequence, timestamp,
             six pre-survey scale means, four final-survey scale means,
             total_duration_s

trials.csv        — one row per participant × trial (long format)
    See TRIAL_FIELDS below for the full column list. Most trial-level values
    (reliance_code, ai_endorsed_answer, covariates, revisions, chat counts,
    timestamps) are read directly from the trial record written by
    app/routes.py's quiz() POST handler — see docs/MEASURES.md for exact
    definitions and app/utils/measures.py for the code that computes them.
    This script does not recompute them from scratch; it flattens.

aggregates.json   — sample-level metrics from docs/MEASURES.md §8, with
    denominators reported alongside every rate so a reader never has to
    guess what was divided by what.

Privacy
-------
Identifying fields (email, firstName, lastName, uf_id) are excluded from
all outputs. Participants are numbered 1, 2, 3 … in order of study
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

from app.utils.db import init_db  # noqa: E402

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
            values.append(float(responses[key]))
        except (KeyError, TypeError, ValueError):
            pass
    if not values:
        return None
    return round(sum(values) / len(values), 4)


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
        "sequence": doc.get("sequence_label", ""),
        "timestamp": str(doc.get("timestamp", "")),
    }

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

        post = post_surveys[pos_0] if pos_0 < len(post_surveys) else {}
        timestamps = record.get("timestamps") or {}
        revisions = record.get("answer_revisions") or {}
        covariates = record.get("covariates") or {}
        chat = record.get("chat") or {}

        rows.append({
            "participant_id": participant_id,
            "participant_uuid": participant_uuid,
            "sequence_id": record.get("sequence_id", doc.get("sequence_label", "")),
            "position": record.get("position", pos_0 + 1),
            "section": record.get("section", ""),
            "problem_id": record.get("problem_id", q_idx),
            "trial_type": record.get("trial_type", record.get("variant_shown", "")),

            "participant_answer": record.get("answer", ""),
            "correct_answer": record.get("correct_answer", ""),
            "is_correct": record.get("is_correct", ""),

            "ai_endorsed_answer": record.get("ai_endorsed_answer"),
            "aligned_with_ai": record.get("aligned_with_ai"),
            "reliance_code": record.get("reliance_code", ""),

            "served_at": timestamps.get("served_at", ""),
            "first_interaction_at": timestamps.get("first_interaction_at", ""),
            "first_submit_at": timestamps.get("first_submit_at", ""),
            "final_submit_at": timestamps.get("final_submit_at", ""),
            "dwell_time_s": record.get("dwell_time_s", ""),

            "initial_answer": revisions.get("initial_answer", ""),
            "final_answer": revisions.get("final_answer", record.get("answer", "")),
            "n_changes": revisions.get("n_changes", ""),

            "n_followup_turns": chat.get("n_turns", ""),
            "n_followup_turns_attempted": chat.get("n_turns_attempted", ""),

            "trials_since_last_idk": covariates.get("trials_since_last_idk"),
            "n_idk_seen_so_far": covariates.get("n_idk_seen_so_far", ""),
            "n_cw_seen_so_far": covariates.get("n_cw_seen_so_far", ""),
            "preceding_trial_type": covariates.get("preceding_trial_type"),
            "is_post_idk_cw": covariates.get("is_post_idk_cw", ""),

            "confidence_rating": record.get("confidence_rating"),
            "post_confidence": post.get("confidence", ""),
            "post_trust": post.get("trust", ""),
            "post_helpfulness": post.get("helpfulness", ""),
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

    # §5 — is_post_idk_cw incidence check. This SHOULD be 0/0 or 0/N under
    # the current S1-S6 sequence table (see docs/MEASURES.md §5) — a nonzero
    # numerator here means either the sequence table changed, or something
    # upstream is miscomputing this covariate. Either way, look at it.
    is_post_idk_cw_count = sum(1 for r in all_trial_rows if r.get("is_post_idk_cw") is True)
    aggregates["is_post_idk_cw_incidence"] = {
        "count": is_post_idk_cw_count,
        "note": (
            "Expected to be 0 under the current S1-S6 sequence table — see "
            "docs/MEASURES.md section 5. A nonzero count here means the "
            "sequence table or this computation changed; re-read that "
            "section before trusting any analysis built on this predictor."
        ),
    }

    return aggregates


# ---------------------------------------------------------------------------
# CSV column order
# ---------------------------------------------------------------------------

PARTICIPANT_FIELDS = (
    ["participant_id", "participant_uuid", "sequence", "timestamp"]
    + [f"pre_{s}_mean" for s in PRE_SURVEY_SCALES]
    + [f"final_{s}_mean" for s in FINAL_SURVEY_SCALES]
    + ["total_duration_s"]
)

TRIAL_FIELDS = [
    "participant_id",
    "participant_uuid",
    "sequence_id",
    "position",
    "section",
    "problem_id",
    "trial_type",
    "participant_answer",
    "correct_answer",
    "is_correct",
    "ai_endorsed_answer",
    "aligned_with_ai",
    "reliance_code",
    "served_at",
    "first_interaction_at",
    "first_submit_at",
    "final_submit_at",
    "dwell_time_s",
    "initial_answer",
    "final_answer",
    "n_changes",
    "n_followup_turns",
    "trials_since_last_idk",
    "n_idk_seen_so_far",
    "n_cw_seen_so_far",
    "preceding_trial_type",
    "is_post_idk_cw",
    "confidence_rating",
    "post_confidence",
    "post_trust",
    "post_helpfulness",
]

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
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    participants_path = output_dir / "participants.csv"
    trials_path = output_dir / "trials.csv"
    aggregates_path = output_dir / "aggregates.json"

    print("Connecting to MongoDB…", file=sys.stderr)
    collection = init_db()

    # Exclude identifying fields at the query level, sort by completion time.
    projection = {"email": 0, "firstName": 0, "lastName": 0, "uf_id": 0}
    docs = list(collection.find({}, projection).sort("timestamp", 1))
    print(f"Found {len(docs)} record(s).", file=sys.stderr)

    if not docs:
        print("Nothing to export.", file=sys.stderr)
        return

    n_exported = 0
    n_skipped = 0
    all_trial_rows: list[dict] = []

    with open(participants_path, "w", newline="", encoding="utf-8") as pf:
        p_writer = csv.DictWriter(pf, fieldnames=PARTICIPANT_FIELDS, extrasaction="ignore")
        p_writer.writeheader()

        for participant_id, doc in enumerate(docs, start=1):
            if not doc.get("question_order"):
                print(
                    f"  Skipping record {doc.get('_id')}: no question_order "
                    "(study not completed).",
                    file=sys.stderr,
                )
                n_skipped += 1
                continue

            try:
                p_writer.writerow(build_participant_row(doc, participant_id))
                all_trial_rows.extend(build_trial_rows(doc, participant_id))
                n_exported += 1
            except Exception as exc:
                print(
                    f"  Error processing record {doc.get('_id')}: {exc}",
                    file=sys.stderr,
                )
                n_skipped += 1

    with open(trials_path, "w", newline="", encoding="utf-8") as tf:
        t_writer = csv.DictWriter(tf, fieldnames=TRIAL_FIELDS, extrasaction="ignore")
        t_writer.writeheader()
        t_writer.writerows(all_trial_rows)

    aggregates = compute_aggregates(all_trial_rows)
    with open(aggregates_path, "w", encoding="utf-8") as af:
        json.dump(aggregates, af, indent=2, default=str)

    print(
        f"Exported {n_exported} participant(s), {len(all_trial_rows)} trial row(s).",
        file=sys.stderr,
    )
    if n_skipped:
        print(f"  {n_skipped} record(s) skipped.", file=sys.stderr)
    print(f"  → {participants_path}", file=sys.stderr)
    print(f"  → {trials_path}", file=sys.stderr)
    print(f"  → {aggregates_path}", file=sys.stderr)
    if aggregates["is_post_idk_cw_incidence"]["count"] > 0:
        print(
            "  NOTE: is_post_idk_cw occurred "
            f"{aggregates['is_post_idk_cw_incidence']['count']} time(s) — "
            "see docs/MEASURES.md section 5.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
