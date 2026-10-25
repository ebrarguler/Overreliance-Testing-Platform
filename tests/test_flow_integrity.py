"""
Server-side checks on the trial flow (app/routes.py quiz / post_survey /
final_survey / chat / initial_recommendation). Each test covers a way the
browser alone could corrupt the research data: skipping a question with the
Back button, answering before the recommendation, saving an unfinished run
as complete, or reading the trial type from a response.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

from app.routes import FINAL_SURVEY_FIELDS, main_bp
from app.utils.pre_survey import PRE_SURVEY_PAGES, page_likert_names
from app.utils.questions import questions
from tests.fake_mongo import FakeDB

pytestmark = pytest.mark.filterwarnings("ignore:The 'session' cookie is too large")

RATING = {"confidence": "4", "trust": "4", "helpfulness": "4"}
FINAL = {name: "4" for name in FINAL_SURVEY_FIELDS}
TRIAL_TYPE_VALUES = ("correct", "confident_wrong", "idk")


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
    c = app.test_client()
    c.get("/start/uzh")
    c.post("/consent", data={"consent": "on", "prior_participation": "no"})
    # Demographics and pre-survey done, so the resume logic points at the quiz.
    with c.session_transaction() as sess:
        sess["demographics"] = {"age_category": "21"}
        sess["pre_survey_data"] = {
            name: "4" for page in PRE_SURVEY_PAGES for name in page_likert_names(page)
        }
    return c


def _state(client):
    with client.session_transaction() as sess:
        return dict(sess)


def _current_question(client):
    state = _state(client)
    return questions[state["question_order"][state["question_index"]]]


def _answer(client, option=0):
    client.get("/quiz")
    client.get("/get_initial_recommendation")
    question = _current_question(client)
    client.post("/quiz", data={"answer": question["options"][option]})
    return question


def test_recommendation_response_does_not_reveal_the_trial_type(client):
    client.get("/quiz")
    resp = client.get("/get_initial_recommendation")
    assert set(resp.get_json()) == {"recommendation"}
    body = resp.get_data(as_text=True)
    assert not any(value in body for value in TRIAL_TYPE_VALUES)

    # A repeat request (page reload) goes through the early-return path.
    again = client.get("/get_initial_recommendation")
    assert set(again.get_json()) == {"recommendation"}


def test_resubmitting_the_post_survey_does_not_skip_a_question(client):
    _answer(client)
    client.post("/post_survey", data=RATING)
    # Back to the rating page and submit it again.
    resp = client.post("/post_survey", data=RATING)

    state = _state(client)
    assert state["question_index"] == 1
    assert len(state["post_survey_answers"]) == 1
    assert resp.headers["Location"].endswith("/quiz")


def test_going_back_to_an_answered_question_keeps_the_recorded_answer(client):
    question = _answer(client, option=0)
    q_index = _state(client)["question_order"][0]

    assert client.get("/quiz").headers["Location"].endswith("/post_survey")
    client.post("/quiz", data={"answer": question["options"][1]})

    assert _state(client)["answers"][q_index]["answer"] == question["options"][0]


def test_answer_before_the_recommendation_is_not_recorded(client):
    client.get("/quiz")
    question = _current_question(client)
    client.post("/quiz", data={"answer": question["options"][0]})

    state = _state(client)
    assert state["answers"] == []
    assert state["question_index"] == 0
    assert client.get("/post_survey").headers["Location"].endswith("/quiz")


def test_answer_that_is_not_one_of_the_options_is_not_recorded(client):
    client.get("/quiz")
    client.get("/get_initial_recommendation")
    client.post("/quiz", data={"answer": "not an option"})

    assert _state(client)["answers"] == []


def test_post_survey_rating_is_stored_with_its_question(client):
    _answer(client)
    client.post("/post_survey", data=RATING)

    state = _state(client)
    q_index = state["question_order"][0]
    (rating,) = state["post_survey_answers"]
    assert rating["position"] == 1
    assert rating["question_index"] == q_index
    assert rating["problem_id"] == questions[q_index]["id"]
    assert rating["trial_type"] == state["variant_assignments"][str(q_index)]
    assert rating["confidence"] == "4"


def test_incomplete_post_survey_is_rejected(client):
    _answer(client)
    resp = client.post("/post_survey", data={"confidence": "4"})

    assert resp.status_code == 400
    assert _state(client)["question_index"] == 0


def test_final_survey_before_the_last_question_saves_nothing(client, db):
    _answer(client)
    client.post("/post_survey", data=RATING)

    resp = client.post("/final_survey", data=FINAL)

    assert resp.headers["Location"].endswith("/quiz")
    assert db.get_collection("users").count_documents({}) == 0
    participant = db.get_collection("participants").find_one({})
    assert participant["completed_at"] is None


def test_final_survey_with_missing_answers_is_rejected(client, db):
    with client.session_transaction() as sess:
        sess["question_index"] = len(sess["question_order"])

    partial = dict(FINAL)
    partial.pop("independence")
    resp = client.post("/final_survey", data=partial)

    assert resp.status_code == 400
    assert "Please answer every statement." in resp.get_data(as_text=True)
    assert db.get_collection("users").count_documents({}) == 0


def test_chat_and_recommendation_are_closed_once_the_question_is_answered(client):
    _answer(client)
    transcript_before = list(_state(client)["chat_transcript"])

    with patch("app.routes.client.chat.completions.create") as mock_create:
        resp = client.post("/chat", json={"message": "and now?"})
    mock_create.assert_not_called()
    assert resp.status_code == 409
    assert client.get("/get_initial_recommendation").status_code == 409
    assert _state(client)["chat_transcript"] == transcript_before


def test_chat_error_details_stay_on_the_server(client):
    client.get("/quiz")
    client.get("/get_initial_recommendation")
    with patch(
        "app.routes.client.chat.completions.create",
        side_effect=RuntimeError("secret upstream detail"),
    ):
        resp = client.post("/chat", json={"message": "why?"})

    assert resp.status_code == 500
    assert "secret upstream detail" not in resp.get_data(as_text=True)
