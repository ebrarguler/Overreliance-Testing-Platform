"""
Per-trial research records, written to MongoDB the moment they happen.

Earlier versions kept every answer, rating and chat turn in the Flask session
and wrote them to `users` only after the final survey. That lost everything a
participant had done if they stopped early, and it let a slow request (a chat
reply waiting on the model) write its stale copy of the session back when it
finished, erasing an answer submitted in the meantime. Now:

  trials            one document per (participant_id, position): the answer
                    record from /quiz and, once given, the post-trial rating.
                    A unique index makes the first answer for a position win.
  chat_turns        one document per recommendation exposure or follow-up
                    turn, insert-only. At most one recommendation exposure
                    per (participant_id, question_index).
  survey_responses  one document per participant with the demographics and
                    pre-survey answers, so a participant who comes back does
                    not repeat them.

The session keeps only small pointers (question_index, awaiting_post_survey)
and copies of the static assignment. The final `users` record is assembled
from these collections (routes.final_survey).
"""

import time

from pymongo.errors import DuplicateKeyError

# Imported as a module (not `from .db import get_collection`) so a test that
# patches app.utils.db.get_collection also covers this module.
from . import db

_INTERNAL_FIELDS = ("_id", "participant_id", "seq")

# Indexes already created in this process, by collection full name. The fake
# collections used in tests have no full_name and are indexed on every call.
_indexed = set()


def _ensure_index(coll, keys, **kwargs):
    key = (getattr(coll, "full_name", None), kwargs.get("name"))
    if key[0] is not None and key in _indexed:
        return
    coll.create_index(keys, **kwargs)
    if key[0] is not None:
        _indexed.add(key)


def _clean(doc):
    return {k: v for k, v in doc.items() if k not in _INTERNAL_FIELDS}


def _trials():
    coll = db.get_collection("trials")
    _ensure_index(
        coll, [("participant_id", 1), ("position", 1)],
        name="participant_position_unique", unique=True,
    )
    return coll


def _chat_turns():
    coll = db.get_collection("chat_turns")
    # Lookup index; its key differs from the unique one below because
    # MongoDB may reject two indexes on the same key with different options.
    _ensure_index(coll, [("participant_id", 1), ("question_index", 1), ("seq", 1)],
                  name="participant_question_seq")
    _ensure_index(
        coll, [("participant_id", 1), ("question_index", 1)],
        name="one_recommendation_per_question", unique=True,
        partialFilterExpression={"is_initial_recommendation": True},
    )
    return coll


def _surveys():
    coll = db.get_collection("survey_responses")
    _ensure_index(coll, "participant_id", name="participant_unique", unique=True)
    return coll


# --- trials ------------------------------------------------------------------


def record_answer(participant_id, position, answer):
    """
    Store the answer record for this position. Returns False if one is
    already stored (e.g. submitted from a second tab): the first one stands.
    """
    try:
        _trials().insert_one({
            "participant_id": participant_id,
            "position": position,
            "question_index": answer["question_index"],
            "answer": answer,
            "rating": None,
        })
        return True
    except DuplicateKeyError:
        return False


def record_rating(participant_id, position, rating):
    """
    Store the post-trial rating for an answered position, once. Returns
    False if the position has no answer yet or is already rated.
    """
    doc = _trials().find_one_and_update(
        {"participant_id": participant_id, "position": position, "rating": None},
        {"$set": {"rating": rating}},
    )
    return doc is not None


def trials_for(participant_id):
    """This participant's trial documents, in presentation order."""
    docs = [_clean(d) for d in _trials().find({"participant_id": participant_id})]
    docs.sort(key=lambda d: d["position"])
    return docs


# --- chat turns ----------------------------------------------------------------


def _turn_doc(participant_id, position, entry):
    doc = dict(entry)
    doc.update(participant_id=participant_id, position=position, seq=time.time_ns())
    return doc


def log_turn(participant_id, position, entry):
    """Append one follow-up chat turn (successful or failed)."""
    entry = dict(entry)
    entry["after_answer"] = _trials().find_one({"participant_id": participant_id, "position": position}) is not None
    _chat_turns().insert_one(_turn_doc(participant_id, position, entry))


def log_recommendation(participant_id, position, entry):
    """
    Record that the initial recommendation was shown for this question.
    Returns the stored entry: the one already there if two requests raced
    (e.g. a double click), so the participant always sees one text.
    """
    try:
        _chat_turns().insert_one(_turn_doc(participant_id, position, entry))
        return entry
    except DuplicateKeyError:
        return recommendation_for(participant_id, entry["question_index"])


def turns_for(participant_id, question_index=None):
    """Chat turns in the order they happened, for one question or all."""
    query = {"participant_id": participant_id}
    if question_index is not None:
        query["question_index"] = question_index
    docs = list(_chat_turns().find(query))
    docs.sort(key=lambda d: d.get("seq", 0))
    return [_clean(d) for d in docs]


def recommendation_for(participant_id, question_index):
    """The initial-recommendation entry for this question, if it was shown."""
    for entry in turns_for(participant_id, question_index):
        if entry.get("is_initial_recommendation"):
            return entry
    return None


# --- surveys before the trials ----------------------------------------------------


def save_survey_part(participant_id, field, value):
    if field not in {"demographics", "pre_survey", "final_survey"}:
        raise ValueError("Unknown survey part")
    query = {"participant_id": participant_id}
    if field != "final_survey":
        query["locked"] = {"$ne": True}
    update = ({f"pre_survey.{key}": item for key, item in value.items()}
              if field == "pre_survey" else {field: value})
    try:
        _surveys().update_one(query, {"$set": update}, upsert=True)
        return True
    except DuplicateKeyError:
        # A concurrent first trial locked the survey document.
        return False


def load_surveys(participant_id):
    doc = _surveys().find_one({"participant_id": participant_id}) or {}
    return {"demographics": doc.get("demographics"), "pre_survey": doc.get("pre_survey")}


def history_for(participant_id, question_index=None):
    history = []
    for turn in turns_for(participant_id, question_index):
        if turn.get("assistant_message") is not None:
            history.extend([("User", turn["user_message"]), ("Assistant", turn["assistant_message"])])
    return history


def start_trial(participant_id, position):
    from datetime import datetime, timezone
    _surveys().update_one({"participant_id": participant_id}, {"$set": {"locked": True}}, upsert=True)
    coll = db.get_collection("trial_starts")
    _ensure_index(coll, [("participant_id", 1), ("position", 1)], name="start_unique", unique=True)
    query = {"participant_id": participant_id, "position": position}
    try:
        coll.update_one(query, {"$setOnInsert": {
            "started_at": time.time(), "served_at": datetime.now(timezone.utc).isoformat(),
        }}, upsert=True)
    except DuplicateKeyError:
        pass
    return coll.find_one(query)


def has_started(participant_id):
    return bool(db.get_collection("trial_starts").find_one({"participant_id": participant_id})
                or _trials().find_one({"participant_id": participant_id}))


def progress(participant_id, order):
    """First incomplete position, verified against the persisted assignment."""
    records = {r["position"]: r for r in trials_for(participant_id)}
    for position, q_index in enumerate(order):
        trial = records.get(position)
        if not trial or trial["question_index"] != q_index:
            return position, False
        rating = trial.get("rating")
        if not rating or rating.get("question_index") != q_index or rating.get("position") != position + 1:
            return position, True
    return len(order), False


def assemble(participant_id, order):
    """Preserve the legacy export shape, sourcing research data from durable logs."""
    answers = [0] * (max(order, default=-1) + 1)
    times = [0] * len(answers)
    ratings, history = [], []
    turns = turns_for(participant_id)
    records = trials_for(participant_id)
    for record in records:
        q = record["question_index"]
        answer = dict(record["answer"])
        transcript = [t for t in turns if t["question_index"] == q]
        followups = [t for t in transcript if not t["is_initial_recommendation"]]
        answer["chat"] = {
            "transcript": transcript,
            "n_turns": sum(not t.get("is_error", False) and not t.get("after_answer", False) for t in followups),
            "n_turns_attempted": sum(not t.get("after_answer", False) for t in followups),
            "n_late_turns": sum(t.get("after_answer", False) for t in followups),
            # Every message the participant sent on this trial, including
            # failed and late-reply turns. /chat refuses messages once the
            # answer is recorded, so all of them were sent before answering.
            "n_messages_sent": len(followups),
        }
        answers[q] = answer
        times[q] = answer.get("dwell_time_s", 0)
        if record.get("rating"):
            ratings.append(record["rating"])
        history.extend(history_for(participant_id, q))
        history.append(("Divider", f"――――――― End of Question {record['position'] + 1} ―――――――"))
    return {"answers": answers, "times": times, "post_survey_answers": ratings,
            "chat_history": history, "chat_transcript": turns,
            "data_integrity": {"expected": len(order), "answered": len(records), "rated": len(ratings),
                               "complete": progress(participant_id, order)[0] == len(order)}}


def migrate_session(state):
    """Idempotently preserve pre-migration session data before clearing it.

    Old parallel requests can each hold the old payload; deterministic legacy
    keys prevent duplicate follow-up turns when those requests race.
    """
    import hashlib
    import json
    pid = state.get("participant_id")
    if not pid:
        return
    order = state.get("question_order", [])
    for position, q in enumerate(order):
        answers = state.get("answers") or []
        if q < len(answers) and isinstance(answers[q], dict):
            record_answer(pid, position, answers[q])
    for rating in state.get("post_survey_answers") or []:
        if "position" in rating:
            record_rating(pid, rating["position"] - 1, rating)
    coll = _chat_turns()
    _ensure_index(coll, [("participant_id", 1), ("legacy_key", 1)], name="legacy_turn_unique", unique=True,
                  partialFilterExpression={"legacy_key": {"$type": "string"}})
    for index, turn in enumerate(state.get("chat_transcript") or []):
        if turn.get("question_index") not in order:
            continue
        doc = _turn_doc(pid, order.index(turn["question_index"]), turn)
        doc["legacy_key"] = hashlib.sha256(json.dumps([index, turn], sort_keys=True).encode()).hexdigest()
        try:
            coll.insert_one(doc)
        except DuplicateKeyError:
            pass
    saved = load_surveys(pid)
    for key, field in (("demographics", "demographics"), ("pre_survey_data", "pre_survey")):
        if saved[field] is None and state.get(key):
            save_survey_part(pid, field, state[key])
