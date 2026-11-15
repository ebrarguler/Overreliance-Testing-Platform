"""
The optional "Answer on my own first" path (app/utils/ai_access.py,
routes.choose_path / initial_recommendation / quiz, question.html): the
choice and the answer given before the AI are stored on the server before
the recommendation is sent, first write wins, a reload returns to the same
step, and the trial record carries the path and the switch measures.
"""

import re
import uuid
from pathlib import Path

import pytest
from flask import Flask
from markupsafe import escape

from app.routes import main_bp
from app.utils import ai_access, trial_store
from app.utils.measures import get_ai_endorsed_answer
from app.utils.questions import questions
from tests.helpers import prepare_session, trial_get, trial_post

PID = "test-participant"
W_Q = 0  # shown as a confidently-wrong trial
C_Q = 1  # shown as a correct trial
RATING = {"confidence": "4", "trust": "4", "helpfulness": "4", "difficulty": "4"}


@pytest.fixture
def client():
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent.parent / "app" / "templates"),
    )
    app.secret_key = "test-secret"
    app.config["CSRF_ENABLED"] = False
    app.register_blueprint(main_bp)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["participant_id"] = PID
            prepare_session(sess)
            sess["question_order"] = [W_Q, C_Q]
            sess["question_index"] = 0
            sess["variant_assignments"] = {str(W_Q): "confident_wrong", str(C_Q): "correct"}
            sess["begin"] = 0
        yield c


def _wrong_target(q=W_Q):
    return get_ai_endorsed_answer(questions[q], "confident_wrong")


def _other_wrong(q=W_Q):
    target = _wrong_target(q)
    return next(o for o in questions[q]["options"] if o not in (target, questions[q]["correct"]))


def _choose_self_first(client):
    resp = trial_post(client, "/choose_path", json={"path": "self_first"})
    assert resp.status_code == 200
    assert resp.get_json() == {"path": "self_first"}


def _record(position=0):
    return next(t for t in trial_store.trials_for(PID) if t["position"] == position)["answer"]


def _recommendations():
    return [t for t in trial_store.turns_for(PID) if t["is_initial_recommendation"]]


# --- the page -------------------------------------------------------------------


def test_page_opens_on_two_equal_buttons(client):
    html = client.get("/quiz").get_data(as_text=True)

    assert 'let accessStep = "choose";' in html
    assert "let recommendationReceived = false;" in html
    assert html.count('class="btn btn-path"') == 2
    assert 'id="choose-self-first">Answer on my own first<' in html
    assert 'id="choose-ai-first">See the AI&#39;s recommendation first<' in html
    assert "Get Initial Recommendation" not in html
    # Options stay locked until a button is used.
    assert all("disabled" in tag for tag in re.findall(r'<input\s[^>]*name="answer"[^>]*>', html))


def test_button_side_is_fixed_per_participant_and_random_across(client):
    assert ai_access.self_first_button_on_left(PID) == ai_access.self_first_button_on_left(PID)
    sides = {ai_access.self_first_button_on_left(str(uuid.uuid4())) for _ in range(200)}
    assert sides == {True, False}

    html = client.get("/quiz").get_data(as_text=True)
    self_left = html.index('id="choose-self-first"') < html.index('id="choose-ai-first"')
    assert self_left == ai_access.self_first_button_on_left(PID)


# --- AI first -------------------------------------------------------------------------


def test_ai_first_is_recorded_when_the_recommendation_is_requested_directly(client):
    client.get("/quiz")
    resp = trial_get(client, "/get_initial_recommendation")
    assert set(resp.get_json()) == {"recommendation"}

    trial_post(client, "/quiz", data={"answer": _wrong_target()})
    record = _record()

    assert record["ai_access_path"] == "ai_first"
    assert record["pre_ai"]["answer"] is None
    assert record["pre_ai"]["switched_to_ai"] is None
    assert record["pre_ai"]["own_attempt_s"] is None
    assert record["pre_ai"]["path_choice_s"] >= 0
    assert record["timestamps"]["path_chosen_at"] == _recommendations()[0]["timestamp"]
    assert record["timestamps"]["pre_ai_locked_at"] is None
    assert record["self_first_button_left"] == ai_access.self_first_button_on_left(PID)


def test_own_first_cannot_be_chosen_after_the_ai_was_shown(client):
    client.get("/quiz")
    trial_get(client, "/get_initial_recommendation")

    resp = trial_post(client, "/choose_path", json={"path": "self_first"})

    assert resp.status_code == 409
    assert trial_store.trial_access(PID, 0)["path"] == "ai_first"


def test_choose_path_only_accepts_own_first(client):
    client.get("/quiz")
    resp = trial_post(client, "/choose_path", json={"path": "ai_first"})
    assert resp.status_code == 400
    assert trial_store.trial_access(PID, 0)["path"] is None


# --- own answer first -----------------------------------------------------------


def test_own_first_needs_an_answer_before_the_ai(client):
    client.get("/quiz")
    _choose_self_first(client)

    no_answer = trial_post(client, "/get_initial_recommendation", json={})
    not_an_option = trial_post(client, "/get_initial_recommendation", json={"pre_ai_answer": "42?"})
    chat = trial_post(client, "/chat", json={"message": "What is the answer?"})

    assert no_answer.status_code == 400
    assert not_an_option.status_code == 400
    assert chat.status_code == 409
    assert _recommendations() == []
    access = trial_store.trial_access(PID, 0)
    assert access["path"] == "self_first"  # the AI request did not turn it into ai_first
    assert access["pre_ai_locked_at"] is None


def test_own_answer_is_saved_once_before_the_recommendation(client):
    client.get("/quiz")
    _choose_self_first(client)
    correct = questions[W_Q]["correct"]

    first = trial_post(client, "/get_initial_recommendation",
                       json={"pre_ai_answer": correct, "pre_ai_n_changes": 2})
    again = trial_post(client, "/get_initial_recommendation", json={"pre_ai_answer": _wrong_target()})

    assert first.status_code == 200
    assert set(first.get_json()) == {"recommendation", "pre_ai_answer"}
    assert first.get_json()["pre_ai_answer"] == correct
    assert again.get_json() == first.get_json()
    assert len(_recommendations()) == 1
    access = trial_store.trial_access(PID, 0)
    assert access["pre_ai_answer"] == correct
    assert access["pre_ai_locked_at"] <= _recommendations()[0]["timestamp"]


def test_switch_from_correct_to_the_wrong_ai_answer_is_recorded(client):
    client.get("/quiz")
    _choose_self_first(client)
    correct = questions[W_Q]["correct"]
    trial_post(client, "/get_initial_recommendation", json={"pre_ai_answer": correct, "pre_ai_n_changes": 2})

    trial_post(client, "/quiz", data={"answer": _wrong_target(), "initial_answer": "stale", "n_changes": "1"})
    record = _record()
    pre = record["pre_ai"]

    assert record["ai_access_path"] == "self_first"
    assert pre["answer"] == correct
    assert pre["answer_place"] == questions[W_Q]["options"].index(correct) + 1
    assert pre["is_correct"] is True
    assert pre["agreed_with_ai"] is False
    assert pre["switched_after_ai"] is True
    assert pre["switched_to_ai"] is True
    assert pre["n_changes"] == 2
    assert pre["own_attempt_s"] >= 0
    assert record["reliance_code"] == "ACCEPTED"
    # answer_revisions start from the saved answer, not the form value.
    assert record["answer_revisions"]["initial_answer"] == correct
    assert record["answer_revisions"]["n_changes"] == 1
    assert record["timestamps"]["first_submit_at"] == record["timestamps"]["pre_ai_locked_at"]


def test_keeping_the_own_answer_against_the_ai(client):
    client.get("/quiz")
    _choose_self_first(client)
    correct = questions[W_Q]["correct"]
    trial_post(client, "/get_initial_recommendation", json={"pre_ai_answer": correct})

    trial_post(client, "/quiz", data={"answer": correct})
    pre = _record()["pre_ai"]

    assert pre["switched_after_ai"] is False
    assert pre["switched_to_ai"] is False


def test_switch_to_ai_is_undefined_when_the_own_answer_already_agreed(client):
    client.get("/quiz")
    _choose_self_first(client)
    trial_post(client, "/get_initial_recommendation", json={"pre_ai_answer": _wrong_target()})

    trial_post(client, "/quiz", data={"answer": _wrong_target()})
    pre = _record()["pre_ai"]

    assert pre["agreed_with_ai"] is True
    assert pre["switched_to_ai"] is None
    assert pre["switched_after_ai"] is False


def test_not_sure_is_recorded_as_abandoned(client):
    client.get("/quiz")
    _choose_self_first(client)
    tentative = _other_wrong()

    resp = trial_post(client, "/get_initial_recommendation",
                      json={"abandoned": True, "pre_ai_answer": tentative, "pre_ai_n_changes": 1})
    assert resp.status_code == 200
    assert set(resp.get_json()) == {"recommendation"}

    trial_post(client, "/quiz", data={"answer": questions[W_Q]["correct"]})
    record = _record()

    assert record["ai_access_path"] == "self_first_abandoned"
    assert record["pre_ai"]["answer"] is None
    assert record["pre_ai"]["unsaved_selection"] == tentative
    assert record["pre_ai"]["n_changes"] == 1
    assert record["pre_ai"]["is_correct"] is None
    assert record["pre_ai"]["switched_to_ai"] is None
    assert record["pre_ai"]["own_attempt_s"] >= 0


def test_paths_are_recorded_per_trial(client):
    client.get("/quiz")
    trial_get(client, "/get_initial_recommendation")
    trial_post(client, "/quiz", data={"answer": _wrong_target()})
    trial_post(client, "/post_survey", data=RATING)

    html = client.get("/quiz").get_data(as_text=True)
    assert 'let accessStep = "choose";' in html
    _choose_self_first(client)
    trial_post(client, "/get_initial_recommendation", json={"pre_ai_answer": questions[C_Q]["options"][0]})
    trial_post(client, "/quiz", data={"answer": questions[C_Q]["correct"]})

    assert _record(0)["ai_access_path"] == "ai_first"
    assert _record(1)["ai_access_path"] == "self_first"


# --- reloads ------------------------------------------------------------------------


def test_reload_returns_to_the_own_answer_step(client):
    client.get("/quiz")
    _choose_self_first(client)

    html = client.get("/quiz").get_data(as_text=True)

    assert 'let accessStep = "own";' in html
    assert "let preAiAnswer = null;" in html


def test_reload_after_a_failed_request_offers_the_ai_again(client):
    client.get("/quiz")
    # Path recorded, but the recommendation never logged (request failed).
    trial_store.choose_path(PID, 0, "ai_first", "2026-11-15T10:00:00+00:00")

    html = client.get("/quiz").get_data(as_text=True)

    assert 'let accessStep = "pending";' in html
    assert trial_get(client, "/get_initial_recommendation").status_code == 200


def test_reload_after_the_ai_keeps_the_own_answer_labelled(client):
    client.get("/quiz")
    _choose_self_first(client)
    correct = questions[W_Q]["correct"]
    trial_post(client, "/get_initial_recommendation", json={"pre_ai_answer": correct})

    html = client.get("/quiz").get_data(as_text=True)

    assert 'let accessStep = "advice";' in html
    assert "let recommendationReceived = true;" in html
    assert html.count("Your answer before the AI</span>") == 1
    checked = re.findall(r'<input\s[^>]*name="answer"[^>]*checked[^>]*>', html)
    assert len(checked) == 1
    assert f'value="{escape(correct)}"' in checked[0]


# --- the measures on their own ----------------------------------------------------------


def test_idk_trials_have_no_switch_to_ai():
    access = {"path": "self_first", "path_chosen_at": "2026-11-15T10:00:05+00:00",
              "pre_ai_locked_at": "2026-11-15T10:00:35+00:00", "pre_ai_answer": "a",
              "pre_ai_abandoned": False, "pre_ai_n_changes": 0}
    fields = ai_access.compute_pre_ai_fields(
        access, served_at="2026-11-15T10:00:00+00:00", final_answer="b",
        correct_answer="a", ai_endorsed_answer=None, options=["b", "a"],
    )

    assert fields["ai_access_path"] == "self_first"
    assert fields["pre_ai"]["agreed_with_ai"] is None
    assert fields["pre_ai"]["switched_to_ai"] is None
    assert fields["pre_ai"]["switched_after_ai"] is True
    assert fields["pre_ai"]["answer_place"] == 2
    assert fields["pre_ai"]["path_choice_s"] == 5.0
    assert fields["pre_ai"]["own_attempt_s"] == 30.0


def test_trials_from_before_the_two_buttons_have_no_path():
    fields = ai_access.compute_pre_ai_fields(
        {}, served_at=None, final_answer="a", correct_answer="a",
        ai_endorsed_answer="a", options=["a"],
    )
    assert fields["ai_access_path"] is None
    assert set(v for v in fields["pre_ai"].values()) == {None}


# --- export (scripts/export_data.py) --------------------------------------------------


def test_export_rows_carry_the_path_columns(client):
    from scripts.export_data import TRIAL_FIELDS, build_trial_rows

    client.get("/quiz")
    _choose_self_first(client)
    correct = questions[W_Q]["correct"]
    trial_post(client, "/get_initial_recommendation", json={"pre_ai_answer": correct, "pre_ai_n_changes": 1})
    trial_post(client, "/quiz", data={"answer": _wrong_target()})
    trial_post(client, "/post_survey", data=RATING)
    client.get("/quiz")
    trial_get(client, "/get_initial_recommendation")
    trial_post(client, "/quiz", data={"answer": questions[C_Q]["correct"]})

    order = [W_Q, C_Q]
    doc = {"participant_id": PID, "question_order": order, **trial_store.assemble(PID, order)}
    own, ai = build_trial_rows(doc, 1)

    assert set(own) <= set(TRIAL_FIELDS)
    assert own["ai_access_path"] == "self_first"
    assert own["pre_ai_answer"] == correct
    assert own["pre_ai_is_correct"] is True
    assert own["pre_ai_switched_to_ai"] is True
    assert own["pre_ai_n_changes"] == 1
    assert own["pre_ai_locked_at"] and own["path_chosen_at"]
    assert own["self_first_button_left"] == ai_access.self_first_button_on_left(PID)
    assert ai["ai_access_path"] == "ai_first"
    assert ai["pre_ai_answer"] is None and ai["own_attempt_s"] is None


def test_export_of_records_from_before_the_buttons_leaves_the_columns_blank():
    from scripts.export_data import build_trial_rows, compute_aggregates

    doc = {"participant_id": PID, "question_order": [0],
           "answers": [{"question_index": 0, "answer": "a", "trial_type": "correct"}]}
    row = build_trial_rows(doc, 1)[0]

    assert row["ai_access_path"] == ""
    assert row["pre_ai_answer"] is None
    assert "ai_access" not in compute_aggregates([row])


def _row(trial_type, path, pre_correct=None, final_correct=None, switched_to_ai=None, switched_after=None):
    return {"trial_type": trial_type, "ai_access_path": path, "pre_ai_is_correct": pre_correct,
            "is_correct": final_correct, "pre_ai_switched_to_ai": switched_to_ai,
            "pre_ai_switched_after_ai": switched_after, "reliance_code": "", "n_followup_turns": 0,
            "n_changes": 0, "dwell_time_s": 1.0, "ai_endorsed_answer": None}


def test_path_summary_formulas():
    from scripts.export_data import compute_aggregates

    rows = [
        # confident_wrong: own correct and kept (RSR hit), own correct and
        # followed the AI (RSR miss), AI first, gave up.
        _row("confident_wrong", "self_first", True, True, False, False),
        _row("confident_wrong", "self_first", True, False, True, True),
        _row("confident_wrong", "ai_first", final_correct=False),
        _row("confident_wrong", "self_first_abandoned", final_correct=True),
        # correct: own wrong then took the AI's (RAIR hit), own wrong and kept
        # it (RAIR miss), own right and agreed with the AI (no switch defined).
        _row("correct", "self_first", False, True, True, True),
        _row("correct", "self_first", False, False, False, False),
        _row("correct", "self_first", True, True, None, False),
    ]
    summary = compute_aggregates(rows)["ai_access"]
    cw = summary["by_trial_type"]["confident_wrong"]
    c = summary["by_trial_type"]["correct"]

    assert cw["own_first_rate"] == {"rate": 0.75, "numerator": 3, "denominator": 4}
    assert cw["abandoned_rate"] == {"rate": 0.3333, "numerator": 1, "denominator": 3}
    assert cw["switch_to_ai_rate"] == {"rate": 0.5, "numerator": 1, "denominator": 2}
    assert c["switch_to_ai_rate"] == {"rate": 0.5, "numerator": 1, "denominator": 2}
    assert c["pre_ai_accuracy"] == {"rate": 0.3333, "numerator": 1, "denominator": 3}
    assert summary["relative_self_reliance"] == {"rate": 0.5, "numerator": 1, "denominator": 2}
    assert summary["relative_ai_reliance"] == {"rate": 0.5, "numerator": 1, "denominator": 2}
    assert summary["by_trial_type"]["idk"]["switch_to_ai_rate"]["rate"] is None
