"""
Fixes from the pre-data-collection review (2026-11-04): per-participant option
order, idempotent consent, printed-output forms in the IDK filter, chat send
times and tab visibility, protocol labels in exports, and the debrief answer
setting.
"""

import re
from collections import Counter
from pathlib import Path

import pytest
from markupsafe import escape
from flask import Flask

from app.routes import main_bp
from app.utils.assignment import get_or_create_assignment
from app.utils.chat_prompting import build_system_message
from app.utils.questions import questions, get_chat_prompt
from app.utils.sequences import OPTION_ORDER_SCHEME, build_option_orders, build_trial_sequence
from tests.helpers import prepare_session, research, trial_get, trial_post


def _app():
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent.parent / "app" / "templates"),
    )
    app.secret_key = "test-secret"
    app.config["CSRF_ENABLED"] = False
    app.register_blueprint(main_bp)
    return app


def _trial_client(option_orders=None, variant="correct"):
    c = _app().test_client()
    with c.session_transaction() as sess:
        sess["participant_id"] = "review-fixes-participant"
        prepare_session(sess)
        sess["question_order"] = list(range(14))
        sess["question_index"] = 0
        sess["variant_assignments"] = {str(q): variant for q in range(14)}
        sess["begin"] = 0
        if option_orders is not None:
            sess["option_orders"] = option_orders
            sess["option_order_scheme"] = OPTION_ORDER_SCHEME
    return c


def _rendered_options(html):
    return re.findall(r'name="answer"\s+value="([^"]*)"', html)


# --- Option order -------------------------------------------------------------


def test_option_orders_are_permutations_reproducible_and_seed_specific():
    a = build_option_orders(questions, 12345)
    assert a == build_option_orders(questions, 12345)
    assert a != build_option_orders(questions, 54321)
    for q_idx, question in enumerate(questions):
        assert sorted(a[str(q_idx)]) == sorted(question["options"])


def test_without_a_seed_options_keep_the_bank_order():
    orders = build_option_orders(questions, None)
    assert orders == {str(i): list(q["options"]) for i, q in enumerate(questions)}


def test_option_shuffle_does_not_change_the_problem_draw():
    # The option order uses its own RNG stream; a seed's problem mapping is
    # what it was before options were shuffled.
    first = build_trial_sequence(0, questions, seed=777)
    build_option_orders(questions, 777)
    assert build_trial_sequence(0, questions, seed=777) == first


def test_correct_and_wrong_target_are_equally_likely_at_each_place():
    correct_place, target_place = Counter(), Counter()
    n = 4000
    for seed in range(n):
        orders = build_option_orders(questions, seed)
        for q_idx, question in enumerate(questions):
            order = orders[str(q_idx)]
            correct_place[order.index(question["correct"])] += 1
            target = question["variants"]["confident_wrong"]["endorsed_answer"]
            target_place[order.index(target)] += 1
    total = n * len(questions)
    for counts in (correct_place, target_place):
        for place in range(4):
            assert abs(counts[place] / total - 0.25) < 0.01


def test_new_assignment_stores_option_orders(isolated_database):
    assignment = get_or_create_assignment("pid-options", questions, "uzh")
    assert assignment["option_order_scheme"] == OPTION_ORDER_SCHEME
    assert assignment["option_orders"] == build_option_orders(questions, assignment["seed"])
    # A returning participant sees the same order.
    again = get_or_create_assignment("pid-options", questions, "uzh")
    assert again["option_orders"] == assignment["option_orders"]


def test_legacy_assignment_without_orders_shows_bank_order(isolated_database):
    isolated_database.get_collection("participant_assignments").insert_one({
        "participant_id": "legacy-pid", "sequence_label": "P",
        "problem_at_position": list(range(14)),
        "variant_assignments": {str(q): "correct" for q in range(14)},
    })
    assignment = get_or_create_assignment("legacy-pid", questions, "uzh")
    assert assignment["option_orders"] is None
    client = _trial_client(option_orders=None)
    assert _rendered_options(client.get("/quiz").get_data(as_text=True)) == questions[0]["options"]


def test_quiz_page_and_answer_record_use_the_displayed_order():
    orders = build_option_orders(questions, 99)
    displayed = orders["0"]
    assert displayed != questions[0]["options"]  # this seed reorders question 0
    client = _trial_client(option_orders=orders)
    html = client.get("/quiz").get_data(as_text=True)
    assert _rendered_options(html) == [o.replace('"', "&#34;") for o in displayed]

    trial_get(client, "/get_initial_recommendation")
    trial_post(client, "/quiz", data={"answer": questions[0]["correct"]})
    with client.session_transaction() as sess:
        answer = research(sess)["answers"][0]
    assert answer["option_order"] == displayed
    assert answer["option_order_scheme"] == OPTION_ORDER_SCHEME
    assert answer["answer_option_place"] == displayed.index(questions[0]["correct"]) + 1
    assert answer["correct_option_place"] == displayed.index(questions[0]["correct"]) + 1
    target = questions[0]["variants"]["confident_wrong"]["endorsed_answer"]
    assert answer["target_wrong_option_place"] == displayed.index(target) + 1


def test_chat_prompt_lists_options_in_the_displayed_order():
    displayed = list(reversed(questions[3]["options"]))
    for variant in ("correct", "idk"):
        message = build_system_message(questions[3], get_chat_prompt(3, variant), variant, options=displayed)
        listed = re.findall(r"^- (.*)$", message, flags=re.M)
        assert listed[: len(displayed)] == displayed


# --- Consent submitted twice ---------------------------------------------------

UF_FORM = {
    "email": "gator@ufl.edu", "id_number": "12345678",
    "firstName": "Alex", "lastName": "Gator", "classSchool": "COP3502",
}
CONSENT = {"consent": "on", "adult": "yes", "prior_participation": "no"}


def _double_consent(client, site):
    """Two submissions of the same consent form, both sent before either
    response arrived (each request sees the pre-consent session)."""
    client.get("/" if site == "uzh" else f"/start/{site}")
    if site == "uf":
        client.post("/validate_email", data=UF_FORM)
    client.get("/consent")
    with client.session_transaction() as sess:
        before = dict(sess)
    first = client.post("/consent", data=CONSENT)
    with client.session_transaction() as sess:
        first_pid = sess["participant_id"]
        sess.clear()
        sess.update(before)
    second = client.post("/consent", data=CONSENT)
    with client.session_transaction() as sess:
        second_pid = sess["participant_id"]
    return first, second, first_pid, second_pid


@pytest.mark.parametrize("site", ["uzh", "uf"])
def test_double_submitted_consent_creates_one_participant_and_one_allocation(isolated_database, monkeypatch, site):
    monkeypatch.setenv("UF_PROTOCOL_APPROVED", "true")
    client = _app().test_client()
    first, second, first_pid, second_pid = _double_consent(client, site)
    assert first.status_code == 302 and second.status_code == 302
    assert first_pid == second_pid
    assert len(isolated_database.get_collection("participants").docs) == 1
    assert len(isolated_database.get_collection("participant_assignments").docs) == 1
    counts = isolated_database.get_collection("sequence_counts").docs
    assert sum(doc["count"] for doc in counts if doc.get("site") == site) == (0 if site == "uzh" else 1)


def test_a_new_session_still_gets_a_new_participant(isolated_database):
    app = _app()
    for _ in range(2):
        client = app.test_client()
        client.get("/")
        client.post("/consent", data=CONSENT)
    assert len(isolated_database.get_collection("participants").docs) == 2


# --- Printed output forms in the IDK filter and option coding --------------------

from app.utils.chat_safety import ABSTENTION, filter_idk_reply
from app.utils.provenance import option_mentions, stated_option


@pytest.mark.parametrize("reply", [
    "It would print 4 3.",
    "The output is `4 3`.",
    "I think you get 3 3 here.",
])
def test_idk_filter_catches_the_printed_form_of_an_option(reply):
    filtered, reason = filter_idk_reply(questions[0], "idk", reply)
    assert filtered == ABSTENTION
    assert reason == "answer_option_mentioned"


def test_printed_forms_do_not_change_correct_or_wrong_trials():
    assert filter_idk_reply(questions[0], "correct", "It prints 4 3.") == ("It prints 4 3.", None)


def test_option_coding_maps_the_printed_form_to_its_option():
    forms = questions[0]["printed_forms"]
    assert option_mentions("Python prints 4 3 here.", questions[0]["options"], forms) == ["4, 3"]
    assert stated_option("The output is `4 3`.", questions[0]["options"], forms) == "4, 3"
    assert stated_option("4 3", questions[0]["options"], forms) == "4, 3"
    # Without the forms, the old behaviour: no match on the printed form.
    assert stated_option("The output is `4 3`.", questions[0]["options"]) is None


def test_printed_forms_name_real_options():
    for question in questions:
        for option in question.get("printed_forms", {}):
            assert option in question["options"]


# --- Chat send time, tab visibility, export labels --------------------------------

from unittest.mock import patch

from scripts.export_data import (
    TRIAL_FIELDS, CHAT_FIELDS, build_chat_rows, build_trial_rows,
    compute_aggregates_by_design, write_attrition,
)
from tests.test_chat_endpoint import _make_completion


def _answered_trial_with_chat(variant="correct", reply="Lists keep duplicates."):
    client = _trial_client(option_orders=build_option_orders(questions, 99), variant=variant)
    client.get("/quiz")
    trial_get(client, "/get_initial_recommendation")
    with patch("app.routes.client.chat.completions.create", return_value=_make_completion(reply)):
        trial_post(client, "/chat", json={"message": "What does + do to lists?"})
    trial_post(client, "/quiz", data={
        "answer": questions[0]["correct"], "n_hidden": "2", "hidden_ms": "4500", "n_blur": "3",
    })
    with client.session_transaction() as sess:
        state = research(sess)
    return state


def test_chat_turn_records_when_the_message_was_sent():
    state = _answered_trial_with_chat()
    followup = [t for t in state["answers"][0]["chat"]["transcript"] if not t["is_initial_recommendation"]]
    assert len(followup) == 1
    assert followup[0]["sent_at"] <= followup[0]["timestamp"]
    assert state["answers"][0]["chat"]["n_messages_sent"] == 1


def test_answer_records_leaving_the_page():
    state = _answered_trial_with_chat()
    assert state["answers"][0]["visibility"] == {"n_hidden": 2, "hidden_s": 4.5, "n_blur": 3}


def test_trial_page_counts_hidden_tab_and_focus_loss():
    html = _trial_client().get("/quiz").get_data(as_text=True)
    for name in ("n_hidden", "hidden_ms", "n_blur"):
        assert f'name="{name}"' in html
    assert "visibilitychange" in html and '"blur"' in html


def _doc(state, rotation_scheme="uniform_random_v1"):
    return {
        "participant_id": "review-fixes-participant",
        "sequence_design_version": "blocks_pq_v1",
        "sequence_label": "P",
        "rotation_scheme": rotation_scheme,
        "question_order": [0],
        "answers": state["answers"],
        "post_survey_answers": [],
    }


def test_trial_and_chat_rows_carry_protocol_and_new_fields():
    state = _answered_trial_with_chat()
    row = build_trial_rows(_doc(state), 1)[0]
    assert set(row) <= set(TRIAL_FIELDS)
    assert row["rotation_scheme"] == "uniform_random_v1"
    assert row["protocol_version"] == "durable_natural_v1"
    assert row["study_phase"] == "pilot"
    assert row["option_order_scheme"] == OPTION_ORDER_SCHEME
    assert row["correct_option_place"] == row["answer_option_place"]
    assert row["n_messages_sent"] == 1 and row["n_late_turns"] == 0
    assert (row["n_hidden"], row["hidden_s"], row["n_blur"]) == (2, 4.5, 3)
    chat_rows = build_chat_rows(_doc(state), 1)
    assert set(chat_rows[0]) <= set(CHAT_FIELDS)
    assert [bool(r["sent_at"]) for r in chat_rows] == [False, True]  # recommendation, follow-up


def test_aggregates_flag_a_design_group_that_mixes_protocols():
    state = _answered_trial_with_chat()
    rows = build_trial_rows(_doc(state), 1) + build_trial_rows(_doc(state, "difficulty_balanced_v1"), 2)
    summary = compute_aggregates_by_design(rows)["blocks_pq_v1"]
    assert summary["protocols"] == [
        "difficulty_balanced_v1 | durable_natural_v1", "uniform_random_v1 | durable_natural_v1",
    ]
    assert "protocol_warning" in summary
    assert set(summary["by_protocol"]) == set(summary["protocols"])
    single = compute_aggregates_by_design(build_trial_rows(_doc(state), 1))["blocks_pq_v1"]
    assert "protocol_warning" not in single and "by_protocol" not in single


def test_attrition_reports_the_assigned_order(isolated_database, tmp_path):
    client = _app().test_client()
    client.get("/")
    client.post("/consent", data=CONSENT)
    rows = write_attrition(tmp_path)
    assert len(rows) == 1
    assert rows[0]["sequence_label"] == "UZH14"
    assert rows[0]["rotation_scheme"] == "uniform_random_v1"


# --- Debrief answer list ---------------------------------------------------------

from app.routes import FINAL_SURVEY_FIELDS
from tests.helpers import complete_trials


def _completed_client():
    client = _app().test_client()
    client.get("/")
    client.post("/consent", data=CONSENT)
    with client.session_transaction() as sess:
        prepare_session(sess)
        complete_trials(sess)
        sess["question_index"] = len(sess["question_order"])
    response = client.post("/final_survey", data={name: "4" for name in FINAL_SURVEY_FIELDS})
    assert response.headers["Location"].endswith("/completion")
    return client


def test_study_explanation_shows_no_answers_and_asks_not_to_share():
    client = _completed_client()
    # UZH's explanation lives at /completion; /debrief sends UZH there.
    assert client.get("/debrief").headers["Location"].endswith("/completion")
    html = client.get("/completion").get_data(as_text=True)
    assert "prepared in advance" in html and "Wrong on purpose" in html
    assert "Please keep this to yourself" in html
    assert "Correct answer" not in html
    # One way forward: the choice. No shortcut to the results before it.
    assert 'name="choice" value="keep"' in html and 'name="choice" value="withdraw"' in html
    assert 'href="/results"' not in html


# --- Results page (2026-11-15) -------------------------------------------------------


def _completed_client_with_answers(wrong_positions=()):
    """Like _completed_client, with wrong answers at the given positions."""
    from app.utils.questions import questions
    from app.utils import trial_store
    client = _app().test_client()
    client.get("/")
    client.post("/consent", data=CONSENT)
    with client.session_transaction() as sess:
        prepare_session(sess)
        for position in wrong_positions:
            q = sess["question_order"][position]
            wrong = next(o for o in questions[q]["options"] if o != questions[q]["correct"])
            trial_store.record_answer(sess["participant_id"], position, {
                "question_index": q, "position": position + 1, "answer": wrong,
            })
        complete_trials(sess)  # first write wins, so the wrong answers stay
        sess["question_index"] = len(sess["question_order"])
    client.post("/final_survey", data={name: "4" for name in FINAL_SURVEY_FIELDS})
    return client


def test_uzh_results_wait_for_the_data_use_choice(isolated_database):
    """Results, the withdrawal code and the debrief cannot be skipped: a
    record left "pending" would be dropped from the export."""
    client = _completed_client()
    assert client.get("/results").headers["Location"].endswith("/completion")
    client.post("/completion", data={"choice": "keep"})
    resp = client.get("/results")
    assert resp.status_code == 200
    assert "Your results" in resp.get_data(as_text=True)


def test_uzh_completion_refresh_and_revisit_preserve_pending_choice(isolated_database):
    client = _completed_client()
    code = isolated_database.get_collection("participants").find_one({})["withdrawal_code"]
    for _ in range(2):
        html = client.get("/completion").get_data(as_text=True)
        assert "You’ve completed the study" in html
        assert code in html and 'href="/withdraw"' in html
        assert 'class="score-card"' not in html
        assert 'type="email"' in html  # the optional prize draw
        # Withdrawing is folded away below the main button, and Enter in the
        # email field uses the first submit button: "Join the draw".
        assert html.index('value="join_draw"') < html.index('value="keep"') < html.index('<details class="withdraw-option">') < html.index('value="withdraw"')
    assert client.get("/final_survey").headers["Location"].endswith("/completion")
    assert client.post("/final_survey", data={}).headers["Location"].endswith("/completion")
    client.get("/results")
    doc = isolated_database.get_collection("users").find_one({})
    assert doc["data_use"] == "pending"
    assert isolated_database.get_collection("participants").find_one({}).get("data_use") is None
    client.delete_cookie("session")
    # The existing participant cookie also supports revisiting after session expiry.
    assert client.get("/completion").status_code == 200
    assert client.get("/results").headers["Location"].endswith("/completion")


def test_uzh_choice_without_an_option_shows_the_page_again():
    client = _completed_client()
    resp = client.post("/completion", data={})
    assert resp.status_code == 400
    assert "Please choose whether we may use your responses." in resp.get_data(as_text=True)


def test_resubmitting_the_saved_choice_keeps_its_decision_time(isolated_database):
    client = _completed_client()
    client.post("/completion", data={"choice": "keep"})
    participants = isolated_database.get_collection("participants")
    decided_at = participants.find_one({})["data_use_decided_at"]
    html = client.get("/completion").get_data(as_text=True)
    assert "You have allowed us to use your responses" in html
    assert client.post("/completion", data={"choice": "keep"}).headers["Location"].endswith("/results")
    assert participants.find_one({})["data_use_decided_at"] == decided_at


def test_withdrawn_participant_sees_status_instead_of_the_form():
    client = _completed_client()
    client.post("/completion", data={"choice": "withdraw"})
    html = client.get("/completion").get_data(as_text=True)
    assert "You withdrew your responses" in html
    assert 'name="choice"' not in html and 'href="/results"' in html


# --- Prize draw (2026-11-19) ------------------------------------------------------


def _draw_entries(db):
    return db.get_collection("prize_draw_entries").docs


def test_joining_the_draw_stores_the_email_apart_from_research_data(isolated_database, tmp_path):
    from scripts.export_data import write_exports
    client = _completed_client()
    resp = client.post("/completion", data={"action": "join_draw", "draw_email": "  Student@Example.org "})
    assert resp.headers["Location"].endswith("/completion#prize-draw")
    entries = _draw_entries(isolated_database)
    assert len(entries) == 1
    assert entries[0]["email"] == "student@example.org"
    assert isinstance(entries[0]["_id"], str)  # no ObjectId, so no insertion time
    assert set(entries[0]) == {"_id", "email", "site"}  # no participant_id
    participant = isolated_database.get_collection("participants").find_one({})
    assert participant["prize_draw_entered"] is True
    assert participant.get("data_use") is None  # joining is not a data-use choice
    html = client.get("/completion").get_data(as_text=True)
    assert "You’ve joined the prize draw" in html and 'type="email"' not in html
    # Only one entry per participant.
    client.post("/completion", data={"action": "join_draw", "draw_email": "other@example.org"})
    assert len(_draw_entries(isolated_database)) == 1
    # The research export never sees the email.
    client.post("/completion", data={"choice": "keep"})
    doc = isolated_database.get_collection("users").find_one({})
    doc["data_use"] = "keep"
    write_exports([doc], tmp_path)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert "student@example.org" not in path.read_text(errors="ignore")


def test_email_typed_before_the_choice_is_saved_whatever_the_choice(isolated_database):
    client = _completed_client()
    resp = client.post("/completion", data={"choice": "withdraw", "draw_email": "student@example.org"})
    assert resp.headers["Location"].endswith("/results")
    assert [e["email"] for e in _draw_entries(isolated_database)] == ["student@example.org"]
    assert isolated_database.get_collection("participants").find_one({})["data_use"] == "withdraw"


@pytest.mark.parametrize("form,message", [
    ({"choice": "keep", "draw_email": "not-an-email"}, "Please enter a valid email address"),
    ({"action": "join_draw", "draw_email": ""}, "Please enter your email address to join the draw"),
])
def test_invalid_draw_entry_saves_nothing(isolated_database, form, message):
    client = _completed_client()
    resp = client.post("/completion", data=form)
    assert resp.status_code == 400
    html = resp.get_data(as_text=True)
    assert message in html and f'value="{form["draw_email"]}"' in html
    assert _draw_entries(isolated_database) == []
    assert isolated_database.get_collection("participants").find_one({}).get("data_use") is None


def test_prize_draw_can_be_switched_off(isolated_database, monkeypatch):
    monkeypatch.setenv("PRIZE_DRAW_ENABLED", "0")
    client = _completed_client()
    assert 'type="email"' not in client.get("/completion").get_data(as_text=True)
    client.post("/completion", data={"choice": "keep", "draw_email": "student@example.org"})
    assert _draw_entries(isolated_database) == []
    fresh = _app().test_client()
    fresh.get("/")
    assert "UZH does not ask for your name, email, or student ID." in fresh.get("/consent").get_data(as_text=True)


def test_consent_mentions_the_optional_draw():
    client = _app().test_client()
    client.get("/")
    html = client.get("/consent").get_data(as_text=True)
    assert "leave your email address for a prize draw" in html


def test_completion_requires_a_finished_study():
    client = _app().test_client()
    assert client.get("/completion").headers["Location"].endswith("/")
    client.get("/")
    client.post("/consent", data=CONSENT)
    assert client.get("/completion").headers["Location"].endswith("/")


def test_uf_retains_debrief_before_results():
    client = _app().test_client()
    client.get("/start/uf")
    client.post("/validate_email", data=UF_FORM)
    client.post("/consent", data=CONSENT)
    with client.session_transaction() as sess:
        prepare_session(sess)
        sess["site"] = "uf"
        complete_trials(sess)
        sess["question_index"] = len(sess["question_order"])
    response = client.post("/final_survey", data={name: "4" for name in FINAL_SURVEY_FIELDS})
    assert response.headers["Location"].endswith("/debrief")
    assert client.get("/completion").headers["Location"].endswith("/debrief")
    assert client.get("/results").headers["Location"].endswith("/debrief")
    html = client.get("/debrief").get_data(as_text=True)  # the same page as UZH's /completion
    assert 'name="choice" value="keep"' in html and "w.pitts@ufl.edu" in html
    assert client.post("/debrief", data={"choice": "keep"}).headers["Location"].endswith("/results")
    assert 'href="/completion"' not in client.get("/results").get_data(as_text=True)


def test_results_are_not_shown_before_the_study_is_finished():
    client = _app().test_client()
    client.get("/")
    client.post("/consent", data=CONSENT)
    assert client.get("/results").headers["Location"].endswith("/")


@pytest.mark.parametrize("choice", ["keep", "withdraw"])
def test_choice_leads_to_score_and_answers(choice):
    from app.utils.questions import questions
    client = _completed_client_with_answers(wrong_positions=(0, 3))
    resp = client.post("/completion", data={"choice": choice})
    assert resp.headers["Location"].endswith("/results")

    html = client.get("/results").get_data(as_text=True)
    assert '<span class="score-value">12</span><span class="score-total">/ 14</span>' in html
    assert html.count('class="result-item is-correct"') == 12
    assert html.count('class="result-item is-incorrect"') == 2
    # The correct answer is listed under the wrong ones.
    assert html.count("<dt>Correct answer</dt>") == 2
    with client.session_transaction() as sess:
        first = questions[sess["question_order"][0]]
    assert f'<dd class="is-right">{escape(first["correct"])}</dd>' in html
    assert "not a measure of your programming ability" in html
    assert 'href="/thank_you"' in html


def test_results_discourage_copying_and_offer_the_study_link(monkeypatch):
    monkeypatch.delenv("STUDY_PUBLIC_URL", raising=False)
    client = _completed_client()
    client.post("/completion", data={"choice": "keep"})
    html = client.get("/results", headers={"X-Forwarded-Proto": "https"}).get_data(as_text=True)

    assert 'id="results-list"' in html and "js/end_pages.js" in html
    script = (Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "end_pages.js").read_text()
    assert '"copy", "cut", "contextmenu", "dragstart"' in script  # copying is blocked on the list
    assert "Please do not save, copy or share" in html
    assert 'id="study-link"' not in html  # no configured public entry URL
    assert 'href="/completion"' in html


def test_study_link_can_be_set_explicitly(monkeypatch):
    monkeypatch.setenv("STUDY_PUBLIC_URL", "https://study.example.org/")
    client = _completed_client()
    client.post("/completion", data={"choice": "keep"})
    assert 'value="https://study.example.org/"' in client.get("/results").get_data(as_text=True)


def url_for_thank_you():
    return 'href="/thank_you"'


# --- 2026-11-05: UZH entry, tool instruction, delay, final survey part 2, demographics ---

from app.utils.surveys import FINAL_CHANGE_ITEMS, FINAL_CHANGE_TEXT_ITEMS, FINAL_ITEMS, FINAL_TEXT_ITEMS
from scripts.export_data import PARTICIPANT_FIELDS, build_participant_row


def test_instructions_and_trial_page_ask_not_to_use_other_tools(isolated_database):
    client = _trial_client()
    assert "Do not run the code" in client.get("/instructions").get_data(as_text=True)
    assert "do not run the code or use other tools" in client.get("/quiz").get_data(as_text=True)


def test_trial_page_delays_the_scripted_recommendation():
    html = _trial_client().get("/quiz").get_data(as_text=True)
    assert "RECOMMENDATION_DELAY_MIN_MS = 1500" in html
    assert "RECOMMENDATION_DELAY_MAX_MS = 3500" in html
    assert 'name="recommendation_delay_ms"' in html
    assert "Thinking…" in html


@pytest.mark.parametrize("posted, stored", [("2380", 2380), ("", None)])
def test_answer_records_the_recommendation_delay(posted, stored):
    client = _trial_client()
    client.get("/quiz")
    trial_get(client, "/get_initial_recommendation")
    trial_post(client, "/quiz", data={"answer": questions[0]["correct"], "recommendation_delay_ms": posted})
    with client.session_transaction() as sess:
        assert research(sess)["answers"][0]["recommendation_delay_ms"] == stored


def _final_page_client():
    client = _app().test_client()
    client.get("/")
    client.post("/consent", data=CONSENT)
    with client.session_transaction() as sess:
        prepare_session(sess)
        complete_trials(sess)
        sess["question_index"] = len(sess["question_order"])
    return client


def test_final_survey_part_two_is_hidden_until_part_one(isolated_database):
    html = _final_page_client().get("/final_survey").get_data(as_text=True)
    part1, part2 = html.split('<section id="final-part-2" hidden>')
    # The study-purpose and unusual-behaviour questions come before anything
    # that mentions "I don't know"; part 2 holds the change items.
    for name in list(FINAL_ITEMS) + list(FINAL_TEXT_ITEMS):
        assert f'name="{name}"' in part1
    for name in list(FINAL_CHANGE_ITEMS) + list(FINAL_CHANGE_TEXT_ITEMS):
        assert f'name="{name}"' in part2 and f'name="{name}"' not in part1
    assert "did not know" not in part1
    assert "Did the way you used the assistant change during the study?" in part2


def test_final_survey_requires_and_stores_the_change_items(isolated_database):
    client = _final_page_client()
    part1_only = {name: "4" for name in FINAL_SURVEY_FIELDS if name not in FINAL_CHANGE_ITEMS
                  and name not in FINAL_CHANGE_TEXT_ITEMS}
    assert client.post("/final_survey", data=part1_only).status_code == 400

    answers = {name: "4" for name in FINAL_SURVEY_FIELDS}
    answers["usage_change"] = "I checked its answers more after it was wrong."
    assert client.post("/final_survey", data=answers).headers["Location"].endswith("/completion")
    doc = isolated_database.get_collection("users").find_one({})
    assert doc["final_survey_answers"]["idk_more_skeptical"] == "4"
    row = build_participant_row(doc, 1)
    assert set(row) <= set(PARTICIPANT_FIELDS)
    assert row["usage_change"] == "I checked its answers more after it was wrong."
    assert (row["idk_more_skeptical"], row["idk_more_trust"]) == ("4", "4")


def test_final_survey_has_the_reduced_item_set():
    # 17 original statements + the unusual-behaviour question in part 1;
    # two closed questions and one open question in part 2.
    assert len(FINAL_ITEMS) == 17
    assert list(FINAL_TEXT_ITEMS) == ["assistant_observations"]
    assert list(FINAL_CHANGE_ITEMS) == ["idk_more_skeptical", "idk_more_trust"]
    assert list(FINAL_CHANGE_TEXT_ITEMS) == ["usage_change"]
    for retired in ("trust_this_assistant", "trust_chatgpt", "study_purpose_guess", "other_tools", "idk_changed_use",
                    "became_more_skeptical", "trust_increased", "idk_more_credible"):
        assert retired not in FINAL_SURVEY_FIELDS
    html = _final_page_client().get("/final_survey").get_data(as_text=True)
    assert "ChatGPT" not in html


def test_uzh_demographics_store_no_ethnicity(isolated_database):
    client = _app().test_client()
    client.get("/")
    client.post("/consent", data=CONSENT)
    html = client.get("/demographics").get_data(as_text=True)
    assert 'name="ethnicity"' not in html
    assert 'min="18"' in html
    client.post("/demographics", data={"age": "22", "study_level": "master", "gpa": "5.5-6.0",
                                       "ethnicity": "white"})
    with client.session_transaction() as sess:
        assert "ethnicity" not in sess["demographics"]
    stored = isolated_database.get_collection("survey_responses").find_one({})
    assert "ethnicity" not in stored["demographics"]
