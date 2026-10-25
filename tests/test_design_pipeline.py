"""
End-to-end test of the current block design (orders P/Q) through the real
routes: consent → assignment → 14 trials (initial recommendation, follow-up
chat, answer, post-trial survey) → final survey → stored `users` record →
export files. Also checks that participants assigned under earlier designs
(Option 6, S1-S6) keep their own design and labels.

MongoDB is the in-memory fake in tests/fake_mongo.py and every OpenAI call is
mocked, so nothing touches a real database or a paid API.
"""

import copy
import csv
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from app.routes import FINAL_SURVEY_FIELDS, main_bp
from app.utils import assignment as assignment_module
from app.utils.participants import PARTICIPANT_COOKIE
from app.utils.questions import IDK_ABSTENTIONS, questions, get_chat_prompt
from app.utils.sequences import (
    LEGACY_SEQUENCES,
    OPTION6_SEQUENCES,
    SEQUENCE_DESIGN_VERSION,
    SEQUENCES,
    SEQUENCE_LABELS,
)
from scripts.export_data import write_exports
from tests.fake_mongo import FakeDB

C, W, I = "correct", "confident_wrong", "idk"

# The bare test app keeps the session in a signed cookie, which a full
# 14-trial session outgrows; production stores sessions server-side
# (SESSION_TYPE = "mongodb" in app/__init__.py), so the warning is irrelevant.
pytestmark = pytest.mark.filterwarnings("ignore:The 'session' cookie is too large")


@pytest.fixture
def db():
    fake = FakeDB()
    with patch("app.utils.participants.get_collection", fake.get_collection), patch(
        "app.utils.assignment.get_collection", fake.get_collection
    ), patch("app.utils.db.get_collection", fake.get_collection):
        yield fake


@pytest.fixture
def client(db):
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent.parent / "app" / "templates"),
    )
    app.secret_key = "test-secret"
    app.register_blueprint(main_bp)
    return app.test_client()


def _completion(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(
            prompt_tokens=10, completion_tokens=5, total_tokens=15,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )


def _consent(client):
    client.get("/start/uzh")
    client.post("/consent", data={"consent": "on", "prior_participation": "no"})


def _session(client):
    with client.session_transaction() as sess:
        return dict(sess)


# Design identifiers that would reveal the manipulation if they reached a
# participant or the model. ("sequence" alone is not one: question 10's IDK
# context legitimately talks about a generator's sequence.)
_DESIGN_TERMS = ("option6", "blocks_pq", "sequence_label", "sequence_design",
                 "immediate_after_idk", "one_correct_between", "before_first_idk",
                 "after_first_idk", "pq_probe", "confident_wrong", "trial_type")


def _run_study(client):
    """Complete all 14 trials. Returns the list of OpenAI request kwargs."""
    sent = []
    state = _session(client)
    order, variants = state["question_order"], state["variant_assignments"]
    idk_wordings = iter(state["idk_phrasing_order"])

    for position, q_idx in enumerate(order, start=1):
        variant = variants[str(q_idx)]

        html = client.get("/quiz").get_data(as_text=True)
        for term in _DESIGN_TERMS:
            assert term not in html, f"{term!r} shown to participant at position {position}"
        # A refresh shows the same question.
        assert client.get("/quiz").get_data(as_text=True) == html

        rec = client.get("/get_initial_recommendation").get_json()
        if variant == I:
            # The participant's next IDK wording, in their own order.
            assert rec["recommendation"] == IDK_ABSTENTIONS[next(idk_wordings)]
        else:
            assert rec["recommendation"] == (
                questions[q_idx]["variants"][variant]["initial_recommendation"]
            )

        def reply(**kwargs):
            sent.append({"position": position, "q_idx": q_idx, "variant": variant, **kwargs})
            return _completion(f"reply for position {position}")

        with patch("app.routes.client.chat.completions.create", side_effect=reply):
            client.post("/chat", json={"message": f"why? (position {position})"})

        if position == 5:
            # A technical error: kept in the transcript, never sent as history.
            with patch("app.routes.client.chat.completions.create",
                       side_effect=RuntimeError("network down")):
                resp = client.post("/chat", json={"message": "retry me"})
            assert resp.status_code == 500

        answer = questions[q_idx]["correct"] if position % 2 else questions[q_idx]["options"][0]
        client.post("/quiz", data={"answer": answer, "n_changes": "0"})
        client.post("/post_survey", data={"confidence": "4", "trust": "4", "helpfulness": "4"})

    client.post("/final_survey", data={name: "4" for name in FINAL_SURVEY_FIELDS})
    return sent


@pytest.mark.parametrize("forced_label", ["P", "Q"])
def test_full_route_storage_export_path(client, db, tmp_path, monkeypatch, forced_label):
    class ForceLabel:  # the first assignment is a P/Q tie; pick each in turn
        def choice(self, options):
            return forced_label

    monkeypatch.setattr(assignment_module, "_tie_break_rng", ForceLabel())
    _consent(client)
    state = _session(client)
    label = state["sequence_label"]
    assert label == forced_label
    assert state["sequence_design_version"] == SEQUENCE_DESIGN_VERSION == "blocks_pq_v1"
    expected_types = SEQUENCES[SEQUENCE_LABELS.index(label)]

    sent = _run_study(client)

    # --- follow-up prompts used the assigned variant, with no design data -----
    for call in sent:
        system = call["messages"][0]["content"]
        assert get_chat_prompt(call["q_idx"], call["variant"])["stance"] in system
        all_text = " ".join(m["content"] for m in call["messages"])
        for term in _DESIGN_TERMS:
            assert term not in all_text
        # No other trial's conversation is included.
        for other in range(1, 15):
            if other != call["position"]:
                assert f"position {other})" not in all_text
                assert f"reply for position {other}" not in all_text
    assert "retry me" not in " ".join(
        m["content"] for c in sent for m in c["messages"]
    )

    # --- stored research record -----------------------------------------------
    (doc,) = db["users"].docs
    (assignment,) = db["participant_assignments"].docs
    assert doc["sequence_design_version"] == "blocks_pq_v1"
    assert doc["sequence_label"] == label
    assert doc["type_at_position"] == expected_types == assignment["type_at_position"]
    assert doc["rotation_seed"] == assignment["seed"]
    assert doc["question_order"] == assignment["problem_at_position"]

    trials = [doc["answers"][q] for q in doc["question_order"]]
    assert [t["position"] for t in trials] == list(range(1, 15))
    assert [t["trial_type"] for t in trials] == expected_types
    assert len({t["problem_id"] for t in trials}) == 14
    for t in trials:
        assert t["sequence_design_version"] == "blocks_pq_v1"
        if t["trial_type"] == I:
            assert t["ai_endorsed_answer"] is None
            assert t["reliance_code"] == "NOT_APPLICABLE"
        else:
            assert t["reliance_code"] in ("ACCEPTED", "REJECTED")

    probes = {t["position"]: t["pq_probe_context"] for t in trials
              if t["is_primary_pq_probe"]}
    assert probes == {4: {"P": "before_first_idk", "Q": "after_first_idk"}[label]}
    assert not any(t["is_primary_option6_probe"] for t in trials)
    assert [t["idk_encounter_number"] for t in trials if t["trial_type"] == I] == [1, 2, 3]
    # Each of the three IDK wordings is shown once, in this participant's order.
    idk_ids = [t["idk_phrasing_id"] for t in trials if t["trial_type"] == I]
    assert idk_ids == state["idk_phrasing_order"] and sorted(idk_ids) == [0, 1, 2]
    assert all(t["idk_phrasing_id"] is None for t in trials if t["trial_type"] != I)

    errored = trials[4]["chat"]
    assert errored["n_turns"] == 1 and errored["n_turns_attempted"] == 2
    entry_types = [e["entry_type"] for e in errored["transcript"]]
    assert entry_types == ["initial_recommendation_request", "followup", "followup"]
    assert errored["transcript"][-1]["error_reason"] == "api_exception"
    assert errored["transcript"][-1]["user_message"] == "retry me"

    # --- export -------------------------------------------------------------
    summary = write_exports([copy.deepcopy(doc)], tmp_path)
    assert summary["n_exported"] == 1

    with open(tmp_path / "participants.csv") as f:
        (p_row,) = list(csv.DictReader(f))
    assert p_row["sequence_design_version"] == "blocks_pq_v1"
    assert p_row["design_group"] == "blocks_pq_v1"
    assert p_row["sequence"] == label
    assert p_row["participant_uuid"] == doc["participant_id"]
    assert p_row["rotation_seed"] == str(assignment["seed"])

    with open(tmp_path / "trials.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 14
    for row, t in zip(rows, trials):
        assert row["participant_uuid"] == doc["participant_id"]
        assert row["sequence_design_version"] == "blocks_pq_v1"
        assert row["sequence_id"] == label
        assert row["position"] == str(t["position"])
        assert row["problem_id"] == str(t["problem_id"])
        assert row["trial_type"] == t["trial_type"]
        assert row["is_primary_pq_probe"] == str(t["is_primary_pq_probe"])
        assert row["pq_probe_context"] == (t["pq_probe_context"] or "")
        assert row["is_primary_option6_probe"] == str(t["is_primary_option6_probe"])
        assert row["option6_probe_context"] == (t["option6_probe_context"] or "")
        assert row["idk_encounter_number"] == (
            "" if t["idk_encounter_number"] is None else str(t["idk_encounter_number"])
        )
        for key in ("preceding_trial_type", "n_idk_seen_so_far", "n_cw_seen_so_far",
                    "trials_since_last_idk", "is_post_idk_cw"):
            value = t["covariates"][key]
            assert row[key] == ("" if value is None else str(value))
    assert rows[4]["n_followup_turns"] == "1"
    assert rows[4]["n_followup_turns_attempted"] == "2"
    assert rows[4]["n_followup_errors"] == "1"

    with open(tmp_path / "chat_messages.csv") as f:
        chat_rows = list(csv.DictReader(f))
    assert len(chat_rows) == 14 * 2 + 1
    assert sum(r["entry_type"] == "initial_recommendation_request" for r in chat_rows) == 14
    err = [r for r in chat_rows if r["error_reason"] == "api_exception"]
    assert len(err) == 1 and err[0]["user_message"] == "retry me"
    assert err[0]["is_successful_ai_reply"] == "False"

    aggregates = json.loads((tmp_path / "aggregates.json").read_text())
    assert list(aggregates) == ["blocks_pq_v1"]
    summary = aggregates["blocks_pq_v1"]
    primary = summary["pq_primary_probe"]
    context = {"P": "before_first_idk", "Q": "after_first_idk"}[label]
    other = {"P": "after_first_idk", "Q": "before_first_idk"}[label]
    assert primary[context]["n_trials"] == 1 and primary[other]["n_trials"] == 0
    assert "option6_primary_probes" not in summary
    assert summary["is_post_idk_cw_incidence"]["count"] == {"P": 1, "Q": 2}[label]


def test_assignment_and_mapping_survive_refresh_and_resume(client, db):
    _consent(client)
    first = _session(client)
    keys = ("participant_id", "question_order", "variant_assignments",
            "sequence_label", "sequence_design_version")

    # Same browser, live session.
    client.get("/start/uzh")
    assert {k: _session(client)[k] for k in keys} == {k: first[k] for k in keys}

    # Session lost, participant cookie kept.
    client.delete_cookie("session")
    client.get("/start/uzh")
    assert {k: _session(client)[k] for k in keys} == {k: first[k] for k in keys}

    assert len(db["participant_assignments"].docs) == 1
    counts = [d["count"] for d in db["sequence_counts"].docs]
    assert sum(counts) == 1


def test_legacy_participant_resumes_with_their_saved_design(client, db):
    """A participant assigned S2 before Option 6 keeps S2 and gets no
    Option 6 probe labels on trials answered after the upgrade."""
    _consent(client)
    pid = _session(client)["participant_id"]
    (record,) = db["participant_assignments"].docs
    # Turn this into a pre-versioning S2 assignment.
    types = LEGACY_SEQUENCES["S2"]
    pools = {C: list(range(8)), W: [11, 12, 13], I: [8, 9, 10]}
    order = [pools[t].pop(0) for t in types]
    record.clear()
    record.update({
        "participant_id": pid,
        "sequence_label": "S2",
        "type_at_position": types,
        "problem_at_position": order,
        "variant_assignments": {str(q): t for q, t in zip(order, types)},
        "seed": 7,
        "rotate_problems": True,
    })
    legacy_record = copy.deepcopy(record)

    client.delete_cookie("session")
    client.get("/start/uzh")
    state = _session(client)
    assert state["sequence_label"] == "S2"
    assert state["sequence_design_version"] is None
    assert state["question_order"] == order

    _run_study(client)

    assert db["participant_assignments"].docs == [legacy_record]
    (doc,) = db["users"].docs
    assert doc["sequence_label"] == "S2"
    assert "sequence_design_version" not in doc
    assert doc["type_at_position"] == types and doc["rotation_seed"] == 7
    trials = [doc["answers"][q] for q in doc["question_order"]]
    assert [t["trial_type"] for t in trials] == types
    assert not any(t["is_primary_option6_probe"] for t in trials)
    assert not any(t["is_primary_pq_probe"] for t in trials)
    # S2 has W at 13 but it is not an Option 6 probe.
    assert trials[12]["trial_type"] == W and trials[12]["option6_probe_context"] is None


def test_option6_participant_resumes_with_their_saved_design(client, db):
    """A participant assigned order A under Option 6 keeps it after the
    switch to P/Q, and their trials keep the Option 6 probe labels."""
    _consent(client)
    pid = _session(client)["participant_id"]
    (record,) = db["participant_assignments"].docs
    types = OPTION6_SEQUENCES["A"]
    pools = {C: list(range(8)), W: [11, 12, 13], I: [8, 9, 10]}
    order = [pools[t].pop(0) for t in types]
    record.clear()
    record.update({
        "participant_id": pid,
        "sequence_design_version": "option6_v1",
        "sequence_label": "A",
        "type_at_position": types,
        "problem_at_position": order,
        "variant_assignments": {str(q): t for q, t in zip(order, types)},
        "seed": 11,
        "rotate_problems": True,
    })
    option6_record = copy.deepcopy(record)

    client.delete_cookie("session")
    client.get("/start/uzh")
    state = _session(client)
    assert (state["sequence_label"], state["sequence_design_version"]) == ("A", "option6_v1")

    _run_study(client)

    assert db["participant_assignments"].docs == [option6_record]
    (doc,) = db["users"].docs
    assert doc["sequence_design_version"] == "option6_v1"
    trials = [doc["answers"][q] for q in doc["question_order"]]
    assert [t["trial_type"] for t in trials] == types
    probes = {t["position"]: t["option6_probe_context"] for t in trials
              if t["is_primary_option6_probe"]}
    assert probes == {8: "immediate_after_idk", 13: "one_correct_between"}
    assert not any(t["is_primary_pq_probe"] for t in trials)


def _legacy_users_doc():
    """A `users` record as written before this change: no design version, no
    design fields on trials, no entry_type on transcript entries."""
    types = LEGACY_SEQUENCES["S1"]
    order = list(range(14))
    answers = []
    for pos, (q, t) in enumerate(zip(order, types), start=1):
        answers.append({
            "question_index": q, "problem_id": q, "position": pos, "section": "early",
            "sequence_id": "S1", "trial_type": t, "variant_shown": t,
            "answer": "x", "correct_answer": "y", "is_correct": False,
            "ai_endorsed_answer": None, "aligned_with_ai": None,
            "reliance_code": "NOT_APPLICABLE",
            "covariates": {"trials_since_last_idk": None, "n_idk_seen_so_far": 0,
                           "n_cw_seen_so_far": 0, "preceding_trial_type": None,
                           "is_post_idk_cw": False},
            "chat": {"n_turns": 0, "n_turns_attempted": 0, "transcript": [{
                "question_index": q, "is_initial_recommendation": True, "is_error": False,
                "user_message": "What's your recommended answer to this question?",
                "assistant_message": "rec",
            }]},
        })
    return {
        "participant_id": "legacy-uuid", "site": "uzh", "sequence_label": "S1",
        "question_order": order, "answers": answers, "times": [1.0] * 14,
        "post_survey_answers": [{}] * 14, "pre_survey_answers": {},
        "final_survey_answers": {}, "timestamp": "2026-01-01",
    }


def test_legacy_records_export_unchanged_and_are_not_pooled(client, db, tmp_path):
    _consent(client)
    _run_study(client)
    (new_doc,) = db["users"].docs
    legacy = _legacy_users_doc()
    legacy_before = copy.deepcopy(legacy)

    write_exports([legacy, copy.deepcopy(new_doc)], tmp_path)
    assert legacy == legacy_before  # export does not modify records

    with open(tmp_path / "participants.csv") as f:
        p_rows = list(csv.DictReader(f))
    assert [r["design_group"] for r in p_rows] == ["legacy_unversioned", "blocks_pq_v1"]
    assert p_rows[0]["sequence_design_version"] == ""
    assert p_rows[0]["type_at_position"] == "" and p_rows[0]["rotation_seed"] == ""

    with open(tmp_path / "trials.csv") as f:
        rows = list(csv.DictReader(f))
    legacy_rows = [r for r in rows if r["design_group"] == "legacy_unversioned"]
    assert len(legacy_rows) == 14
    for r in legacy_rows:
        # Not fabricated for historical records.
        assert r["is_primary_option6_probe"] == ""
        assert r["option6_probe_context"] == ""
        assert r["is_primary_pq_probe"] == ""
        assert r["pq_probe_context"] == ""
        assert r["idk_encounter_number"] == ""
        assert r["sequence_id"] == "S1"

    with open(tmp_path / "chat_messages.csv") as f:
        chat = [r for r in csv.DictReader(f) if r["design_group"] == "legacy_unversioned"]
    assert {r["entry_type"] for r in chat} == {"initial_recommendation_request"}

    aggregates = json.loads((tmp_path / "aggregates.json").read_text())
    assert sorted(aggregates) == ["blocks_pq_v1", "legacy_unversioned"]
    assert "option6_primary_probes" not in aggregates["legacy_unversioned"]
    assert "pq_primary_probe" not in aggregates["legacy_unversioned"]
    assert aggregates["legacy_unversioned"]["by_trial_type"]["idk"]["n_trials"] == 3
    assert aggregates["blocks_pq_v1"]["by_trial_type"]["idk"]["n_trials"] == 3

    # Selecting one design leaves the other out of every file.
    only = tmp_path / "only"
    summary = write_exports([legacy, copy.deepcopy(new_doc)], only, design="blocks_pq_v1")
    assert summary["n_exported"] == 1 and summary["n_other_design"] == 1
    with open(only / "trials.csv") as f:
        assert {r["design_group"] for r in csv.DictReader(f)} == {"blocks_pq_v1"}
    with open(only / "participants.csv") as f:
        # Participant numbering is the same as in the unfiltered export.
        assert [r["participant_id"] for r in csv.DictReader(f)] == ["2"]
