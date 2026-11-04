from tests.helpers import prepare_session, research, trial_get, trial_post, complete_trials
from app.utils import trial_store
"""
Regression tests for reloading a question page after the initial
recommendation was shown (app/routes.py quiz/initial_recommendation): the
page must come back unlocked, and a repeat request must not log a second
exposure.
"""

from pathlib import Path

import pytest
from flask import Flask

from app.routes import main_bp
from app.utils.questions import questions


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
            sess["participant_id"] = "test-participant"
            prepare_session(sess)
            sess["question_order"] = [0, 1]
            sess["question_index"] = 0
            sess["variant_assignments"] = {"0": "correct", "1": "correct"}
            sess["begin"] = 0
            sess["answers"] = []
            sess["times"] = []
        yield c


def _initial_entries(client):
    with client.session_transaction() as sess:
        return [e for e in research(sess).get("chat_transcript", []) if e["is_initial_recommendation"]]


def test_fresh_question_starts_locked(client):
    html = client.get("/quiz").get_data(as_text=True)
    assert "let recommendationReceived = false;" in html


def test_reload_after_recommendation_starts_unlocked(client):
    client.get("/quiz")
    trial_get(client, "/get_initial_recommendation")

    html = client.get("/quiz").get_data(as_text=True)
    assert "let recommendationReceived = true;" in html
    assert "What&#39;s your recommended answer" in html  # restored exchange


def test_repeat_request_does_not_log_second_exposure(client):
    first = trial_get(client, "/get_initial_recommendation").get_json()
    again = trial_get(client, "/get_initial_recommendation").get_json()

    assert again == first
    assert len(_initial_entries(client)) == 1
    with client.session_transaction() as sess:
        assert len(research(sess)["chat_history"]) == 2


def test_next_question_starts_locked_again(client):
    client.get("/quiz")
    trial_get(client, "/get_initial_recommendation")
    trial_post(client, "/quiz", data={"answer": questions[0]["options"][0]})
    trial_post(client, "/post_survey", data={"confidence": "4", "trust": "4", "helpfulness": "4", "difficulty": "4"})

    html = client.get("/quiz").get_data(as_text=True)
    assert "let recommendationReceived = false;" in html


def test_first_interaction_falls_back_to_recommendation_time(client):
    client.get("/quiz")
    trial_get(client, "/get_initial_recommendation")
    (entry,) = _initial_entries(client)

    # Reloaded page: the client-side first_interaction_at was lost.
    trial_post(client, "/quiz", data={"answer": questions[0]["options"][0], "first_interaction_at": ""}
    )
    with client.session_transaction() as sess:
        record = research(sess)["answers"][0]
    assert record["timestamps"]["first_interaction_at"] == entry["timestamp"]
