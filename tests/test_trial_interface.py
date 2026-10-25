"""
Trial-page interface (app/templates/question.html, post_survey.html): the
progress indicator, the multi-line chat box, copying allowed and counted,
and a submit button that cannot be confused with Send.
"""

import re
from pathlib import Path

import pytest
from flask import Flask

from app.routes import main_bp
from app.utils.questions import questions
from scripts.export_data import TRIAL_FIELDS, build_trial_rows

RATING = {"confidence": "4", "trust": "4", "helpfulness": "4"}


def _client(question_order):
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent.parent / "app" / "templates"),
    )
    app.secret_key = "test-secret"
    app.register_blueprint(main_bp)
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["question_order"] = question_order
        sess["question_index"] = 0
        sess["variant_assignments"] = {str(q): "correct" for q in question_order}
        sess["begin"] = 0
        sess["answers"] = []
        sess["times"] = []
        sess["chat_history"] = []
        sess["chat_transcript"] = []
    return c


@pytest.fixture
def client():
    return _client(list(range(14)))


def _answer(client, **extra):
    client.get("/quiz")
    client.get("/get_initial_recommendation")
    with client.session_transaction() as sess:
        q_index = sess["question_order"][sess["question_index"]]
    client.post("/quiz", data={"answer": questions[q_index]["options"][0], **extra})
    with client.session_transaction() as sess:
        return sess["answers"][q_index]


def test_question_page_shows_progress_out_of_the_total(client):
    html = client.get("/quiz").get_data(as_text=True)
    assert "Question 1 of 14" in html
    assert 'role="progressbar"' in html
    assert 'aria-valuenow="1"' in html and 'aria-valuemax="14"' in html


def test_chat_box_is_multiline_and_code_can_be_copied(client):
    html = client.get("/quiz").get_data(as_text=True)
    assert re.search(r'<textarea[^>]*id="user-message"', html)
    assert "no-select" not in html
    assert "Shift+Enter" in html


def test_submit_and_send_buttons_are_distinct(client):
    html = client.get("/quiz").get_data(as_text=True)
    submit = re.search(r'<button[^>]*id="submit-answer"[^>]*>\s*([^<]+?)\s*<', html)
    send = re.search(r'<button[^>]*id="send-message"[^>]*>', html)
    assert submit.group(1) == "Submit answer"
    assert "btn-submit" in submit.group(0) and "disabled" in submit.group(0)
    assert "btn-send" in send.group(0)


def test_restored_chat_labels_the_participant_as_you(client):
    client.get("/quiz")
    client.get("/get_initial_recommendation")
    html = client.get("/quiz").get_data(as_text=True)
    assert "<strong>You:</strong>" in html
    assert "<strong>User:</strong>" not in html


def test_copy_and_paste_counts_are_stored_with_the_answer(client):
    record = _answer(client, n_copy="2", n_paste="1", pasted_chars="120")
    assert record["clipboard"] == {"n_copy": 2, "n_paste": 1, "pasted_chars": 120}


def test_malformed_counts_are_stored_as_zero(client):
    record = _answer(client, n_copy="abc", n_paste="-3")
    assert record["clipboard"] == {"n_copy": 0, "n_paste": 0, "pasted_chars": 0}


def test_post_survey_shows_progress_and_next_question_button(client):
    _answer(client)
    html = client.get("/post_survey").get_data(as_text=True)
    assert "Question 1 of 14" in html
    assert "Next question" in html
    assert "Continue to final survey" not in html


def test_last_post_survey_leads_to_the_final_survey():
    client = _client([0])
    _answer(client)
    html = client.get("/post_survey").get_data(as_text=True)
    assert "Question 1 of 1" in html
    assert "Continue to final survey" in html


def test_export_has_clipboard_columns(client):
    record = _answer(client, n_copy="1", n_paste="2", pasted_chars="80")
    with client.session_transaction() as sess:
        doc = {"question_order": sess["question_order"], "answers": sess["answers"]}

    (row,) = build_trial_rows(doc, participant_id=1)
    assert {"n_copy", "n_paste", "pasted_chars"} <= set(TRIAL_FIELDS)
    assert (row["n_copy"], row["n_paste"], row["pasted_chars"]) == (1, 2, 80)
    assert record["clipboard"]["n_copy"] == 1
