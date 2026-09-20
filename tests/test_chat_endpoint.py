"""
Regression tests for the /chat endpoint's OpenAI response handling
(app/routes.py:chat). All OpenAI calls are mocked — no network access,
no real API key usage, no MongoDB dependency (a bare Flask app with the
default signed-cookie session is used instead of the mongodb-backed
session configured by create_app()).
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from app.routes import main_bp


def _make_app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(main_bp)
    return app


@pytest.fixture
def client():
    app = _make_app()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["question_order"] = [0]
            sess["question_index"] = 0
            sess["variant_assignments"] = {"0": "correct"}
        yield c


def _make_completion(content, finish_reason="stop", reasoning_tokens=0,
                      prompt_tokens=10, completion_tokens=5):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            completion_tokens_details=SimpleNamespace(
                reasoning_tokens=reasoning_tokens
            ),
        ),
    )


def test_successful_reply_uses_max_completion_tokens_param():
    """The migration must call the API with max_completion_tokens, not
    max_tokens (the old parameter is rejected by reasoning models)."""
    app = _make_app()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["question_order"] = [0]
            sess["question_index"] = 0
            sess["variant_assignments"] = {"0": "correct"}

        with patch("app.routes.client.chat.completions.create") as mock_create:
            mock_create.return_value = _make_completion("Here's a concise answer.")
            resp = c.post("/chat", json={"message": "What's the output?"})

    assert resp.status_code == 200
    assert resp.get_json()["response"] == "Here's a concise answer."

    _, kwargs = mock_create.call_args
    assert "max_completion_tokens" in kwargs
    assert "max_tokens" not in kwargs

    from app.routes import OPENAI_MODEL

    assert kwargs["model"] == OPENAI_MODEL


def test_successful_reply_is_saved_and_records_reasoning_tokens(client):
    with patch("app.routes.client.chat.completions.create") as mock_create:
        mock_create.return_value = _make_completion(
            "This prints 4, 3.", finish_reason="stop", reasoning_tokens=12
        )
        resp = client.post("/chat", json={"message": "What's the output?"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["response"] == "This prints 4, 3."

    with client.session_transaction() as sess:
        assert sess["chat_history"][-2] == ("User", "What's the output?")
        assert sess["chat_history"][-1] == ("Assistant", "This prints 4, 3.")

        transcript_entry = sess["chat_transcript"][-1]
        assert transcript_entry["is_error"] is False
        assert transcript_entry["assistant_message"] == "This prints 4, 3."
        assert transcript_entry["reasoning_tokens"] == 12
        assert transcript_entry["total_tokens"] == 15


def test_empty_completion_is_not_saved_as_successful_reply(client):
    """finish_reason=length with no visible content (budget fully consumed
    by reasoning tokens) must not be stored as a successful reply."""
    with patch("app.routes.client.chat.completions.create") as mock_create:
        mock_create.return_value = _make_completion(
            "", finish_reason="length", reasoning_tokens=512,
            completion_tokens=512,
        )
        with patch("app.routes.logger") as mock_logger:
            resp = client.post("/chat", json={"message": "What's the output?"})
            assert mock_logger.error.called

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["response"]  # a neutral message is returned, not the raw empty string

    with client.session_transaction() as sess:
        transcript_entry = sess["chat_transcript"][-1]
        assert transcript_entry["is_error"] is True
        assert transcript_entry["error_reason"] == "empty_completion"
        assert transcript_entry["shown_to_participant"] is False
        assert transcript_entry["assistant_message"] != ""
        assert transcript_entry["assistant_message"] is not None
        assert transcript_entry["finish_reason"] == "length"
        assert transcript_entry["reasoning_tokens"] == 512
        assert transcript_entry["raw_completion"] is None

        # The neutral message shown to the user is what's recorded — never
        # the literal empty completion.
        assert sess["chat_history"][-1][0] == "Assistant"
        assert sess["chat_history"][-1][1] != ""


def test_whitespace_only_completion_is_treated_as_empty(client):
    with patch("app.routes.client.chat.completions.create") as mock_create:
        mock_create.return_value = _make_completion("   \n  ", finish_reason="stop")
        resp = client.post("/chat", json={"message": "hi"})

    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess["chat_transcript"][-1]["is_error"] is True


def test_truncated_nonempty_reply_is_treated_as_error_not_shown(client):
    """A non-empty but cut-off (finish_reason=length) completion must NOT be
    displayed as a normal answer: the participant gets the neutral retry
    message, and the partial text is preserved only in the research log,
    flagged as not shown."""
    with patch("app.routes.client.chat.completions.create") as mock_create:
        mock_create.return_value = _make_completion(
            "This is a truncated reply that got cut", finish_reason="length",
            reasoning_tokens=0,
        )
        with patch("app.routes.logger") as mock_logger:
            resp = client.post("/chat", json={"message": "explain"})
            assert mock_logger.error.called

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["response"] != "This is a truncated reply that got cut"

    with client.session_transaction() as sess:
        transcript_entry = sess["chat_transcript"][-1]
        assert transcript_entry["is_error"] is True
        assert transcript_entry["error_reason"] == "truncated"
        assert transcript_entry["shown_to_participant"] is False
        assert transcript_entry["finish_reason"] == "length"
        assert transcript_entry["raw_completion"] == "This is a truncated reply that got cut"
        assert transcript_entry["assistant_message"] != "This is a truncated reply that got cut"

        # What's actually shown/stored as the visible reply is the neutral
        # message, never the truncated text.
        assert sess["chat_history"][-1] == ("Assistant", transcript_entry["assistant_message"])


def test_trial_specific_isolation_uses_current_question_only(client):
    """The system prompt sent to the API must come from the trial currently
    in progress (session['question_index']), not any other trial."""
    with client.session_transaction() as sess:
        sess["question_order"] = [0, 8]
        sess["question_index"] = 1
        sess["variant_assignments"] = {"0": "correct", "8": "confident_wrong"}

    with patch("app.routes.client.chat.completions.create") as mock_create:
        mock_create.return_value = _make_completion("ok")
        client.post("/chat", json={"message": "hi"})

    _, kwargs = mock_create.call_args
    system_content = kwargs["messages"][0]["content"]

    from app.utils.questions import get_chat_prompt

    expected_prompt = get_chat_prompt(8, "confident_wrong")
    assert expected_prompt["system_context"] in system_content
    assert expected_prompt["stance"] in system_content

    other_prompt = get_chat_prompt(0, "correct")
    if other_prompt["system_context"] != expected_prompt["system_context"]:
        assert other_prompt["system_context"] not in system_content


def test_q1_to_q2_transition_isolates_context_and_retains_q2_history():
    """Full same-session flow: Q1 chat -> submit answer -> post_survey ->
    Q2. Verifies Q2 shows no Q1 messages/recommendation, Q1's research
    transcript survives the transition untouched, Q2's model request
    carries no Q1 context, and a second Q2 follow-up retains the first."""
    from app.utils.questions import get_initial_recommendation, get_chat_prompt, questions

    app = _make_app()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["question_order"] = [0, 8]
            sess["question_index"] = 0
            sess["variant_assignments"] = {"0": "correct", "8": "confident_wrong"}
            sess["answers"] = []
            sess["times"] = []
            sess["post_survey_answers"] = []
            sess["chat_history"] = []
            sess["chat_transcript"] = []
            sess["begin"] = 0

        # --- Q1: load, get recommendation, one follow-up chat turn -------
        c.get("/quiz")
        c.get("/get_initial_recommendation")

        with patch("app.routes.client.chat.completions.create") as mock_create:
            mock_create.return_value = _make_completion("Q1 follow-up answer.")
            resp = c.post("/chat", json={"message": "Why is that the output?"})
        assert resp.get_json()["response"] == "Q1 follow-up answer."

        with c.session_transaction() as sess:
            q1_transcript_before = list(sess["chat_transcript"])
            assert len(q1_transcript_before) == 2  # initial rec + 1 follow-up

        # --- Submit Q1's answer, move through post_survey to Q2 ----------
        c.post("/quiz", data={"answer": questions[0]["correct"], "n_changes": "0"})
        c.post("/post_survey", data={"confidence": "5", "trust": "5", "helpfulness": "5"})

        # --- Q2 page load must show no Q1 content -------------------------
        q2_page = c.get("/quiz")
        page_text = q2_page.get_data(as_text=True)
        q1_initial_rec = get_initial_recommendation(0, "correct")["recommendation"]
        assert q1_initial_rec not in page_text
        assert "Q1 follow-up answer." not in page_text
        assert "Why is that the output?" not in page_text
        assert "End of Question 1" not in page_text  # Q1's own divider text

        with c.session_transaction() as sess:
            # Q1's research log is untouched by the transition to Q2
            q1_transcript_after = [
                e for e in sess["chat_transcript"] if e["question_index"] == 0
            ]
            assert q1_transcript_after == q1_transcript_before

        c.get("/get_initial_recommendation")  # Q2's own scripted recommendation

        # --- Q2 follow-up #1: model request must carry no Q1 context -----
        with patch("app.routes.client.chat.completions.create") as mock_create:
            mock_create.return_value = _make_completion("Q2 first follow-up.")
            c.post("/chat", json={"message": "Q2 question one"})

        _, kwargs = mock_create.call_args
        sent_messages = kwargs["messages"]
        sent_text = " ".join(m["content"] for m in sent_messages)
        assert q1_initial_rec not in sent_text
        assert "Q1 follow-up answer." not in sent_text
        assert "Why is that the output?" not in sent_text

        expected_q2_prompt = get_chat_prompt(8, "confident_wrong")
        assert expected_q2_prompt["stance"] in sent_messages[0]["content"]

        q1_chat_prompt = get_chat_prompt(0, "correct")
        if q1_chat_prompt["system_context"] != expected_q2_prompt["system_context"]:
            assert q1_chat_prompt["system_context"] not in sent_text

        # Q2's own initial recommendation turn is retained as history
        q2_initial_rec = get_initial_recommendation(8, "confident_wrong")["recommendation"]
        assert any(m["content"] == q2_initial_rec for m in sent_messages)

        # --- Q2 follow-up #2: must retain Q2's first follow-up turn -------
        with patch("app.routes.client.chat.completions.create") as mock_create:
            mock_create.return_value = _make_completion("Q2 second follow-up.")
            c.post("/chat", json={"message": "Q2 question two"})

        _, kwargs = mock_create.call_args
        sent_messages = kwargs["messages"]
        assert any(m["content"] == "Q2 question one" for m in sent_messages)
        assert any(m["content"] == "Q2 first follow-up." for m in sent_messages)

        # Still no Q1 leakage once Q2 has its own multi-turn history
        sent_text = " ".join(m["content"] for m in sent_messages)
        assert q1_initial_rec not in sent_text
        assert "Q1 follow-up answer." not in sent_text


def test_quiz_submission_excludes_error_turns_from_n_turns():
    """The per-trial log's chat.n_turns (used by followup_rate/
    idk_followup_rate) must count only successful follow-up replies —
    failed turns still count toward chat.n_turns_attempted, but must not
    inflate the "successful AI reply" measure."""
    from app.utils.questions import questions

    app = _make_app()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["question_order"] = [0]
            sess["question_index"] = 0
            sess["variant_assignments"] = {"0": "correct"}
            sess["answers"] = []
            sess["times"] = []
            sess["chat_history"] = []
            sess["begin"] = 0
            sess["chat_transcript"] = [
                {  # scripted initial recommendation — never counted in n_turns
                    "question_index": 0, "is_initial_recommendation": True,
                    "is_error": False, "user_message": "u0", "assistant_message": "a0",
                },
                {  # one successful follow-up
                    "question_index": 0, "is_initial_recommendation": False,
                    "is_error": False, "user_message": "u1", "assistant_message": "a1",
                },
                {  # one failed follow-up (empty completion, neutral message shown)
                    "question_index": 0, "is_initial_recommendation": False,
                    "is_error": True, "user_message": "u2", "assistant_message": "neutral",
                },
                {  # another failed follow-up (truncated)
                    "question_index": 0, "is_initial_recommendation": False,
                    "is_error": True, "user_message": "u3", "assistant_message": "neutral",
                },
            ]

        c.get("/quiz")
        c.post("/quiz", data={"answer": questions[0]["correct"], "n_changes": "0"})

        with c.session_transaction() as sess:
            chat_measures = sess["answers"][0]["chat"]
            assert chat_measures["n_turns"] == 1  # only the successful follow-up
            assert chat_measures["n_turns_attempted"] == 3  # all 3 non-scripted attempts
            assert len(chat_measures["transcript"]) == 4  # full record, errors included


def test_missing_active_question_returns_400():
    app = _make_app()
    with app.test_client() as c:
        resp = c.post("/chat", json={"message": "hi"})
    assert resp.status_code == 400


def test_missing_message_returns_400(client):
    resp = client.post("/chat", json={})
    assert resp.status_code == 400
