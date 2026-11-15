"""
The 11-question pre-survey (2026-11-15; app/utils/pre_survey.py) and its
export (scripts/export_data.py): one page, unchanged names and wording
except two reworded questions under new names, participants who answered
the 25-question version count as complete, and scale means use the same
questions for old and new records.
"""

import re
from pathlib import Path

import pytest
from flask import Flask

from app.routes import main_bp
from app.utils import trial_store
from app.utils.pre_survey import PRE_SURVEY_PAGES, all_likert_names, first_incomplete_page
from scripts.export_data import (
    PARTICIPANT_FIELDS, PRE_SURVEY_ITEMS, PRE_SURVEY_SCALES, RETIRED_PRE_SURVEY_SCALES,
    build_participant_row,
)

KEPT = [
    "program_without_ai", "code_comprehension", "language_proficiency",
    "code_understanding", "concept_understanding", "complex_problems", "self_solving",
    "ai_dependability", "ai_reliability", "ai_explanation",
    "hesitant_without_checking",
]
# Reworded questions: new name -> name of the old wording.
OLD_NAMES = {"program_without_ai": "independent_programming", "hesitant_without_checking": "blind_trust"}
REMOVED = [
    "learn_languages", "identify_improvements", "solution_exploration",
    "ai_dependency", "incorrect_advice", "blind_trust", "learning_hindrance",
    "fundamental_concepts", "data_structures", "oop_principles", "explain_concepts",
    "ai_principles", "ai_use_cases", "ai_risks", "ai_prompting",
]
# The 25-question version (before 2026-11-15): the kept questions with the
# old wordings of the reworded ones, plus the removed ones.
OLD_VERSION = sorted({OLD_NAMES.get(n, n) for n in KEPT} | set(REMOVED))


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
            sess["participant_id"] = "p1"
            sess["site"] = "uzh"
            sess["consented"] = True
            sess["demographics"] = {"age_category": "21"}
            sess["pre_survey_data"] = {}
        yield c


def test_eleven_questions_on_one_page_without_free_text():
    assert len(PRE_SURVEY_PAGES) == 1
    assert all_likert_names() == KEPT
    assert not PRE_SURVEY_PAGES[0].get("free_text")


def test_page_shows_the_questions(client):
    html = client.get("/pre_survey").get_data(as_text=True)

    for name in KEPT:
        assert f'name="{name}"' in html
    for name in REMOVED + ["ai_classroom_concerns", "independent_programming"]:
        assert f'name="{name}"' not in html
    assert "I am confident in my ability to program without help from AI tools" in html
    assert "program independently" not in html
    assert "I am hesitant to rely on AI suggestions without checking them" in html
    assert "blindly" not in html
    # The caution question comes after the trust questions.
    assert html.index('name="ai_explanation"') < html.index('name="hesitant_without_checking"')
    assert "<textarea" not in html
    assert "Page 1 of" not in html


def test_answering_all_questions_leads_to_the_instructions(client):
    resp = client.post("/pre_survey", data={name: "5" for name in KEPT})

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/instructions")
    assert trial_store.load_surveys("p1")["pre_survey"] == {name: "5" for name in KEPT}


def test_a_missing_answer_is_refused(client):
    answers = {name: "5" for name in KEPT[:-1]}
    assert client.post("/pre_survey", data=answers).status_code == 400


def test_old_page_links_return_to_the_survey(client):
    resp = client.get("/pre_survey/3")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/pre_survey")


def test_answers_to_the_25_question_version_count_as_complete():
    # Including the old wording of the reworded question, so nobody who
    # answered before the change is sent back (or stuck, once trials began).
    assert first_incomplete_page({name: "4" for name in OLD_VERSION}) is None
    assert first_incomplete_page({name: "4" for name in REMOVED}) == 1
    for new_name in OLD_NAMES:
        assert first_incomplete_page({name: "4" for name in KEPT if name != new_name}) == 1


def test_participant_mid_quiz_with_old_answers_is_not_sent_back(client):
    with client.session_transaction() as sess:
        sess["pre_survey_data"] = {name: "4" for name in OLD_VERSION}
        sess["question_order"] = [0, 1]
        sess["question_index"] = 0
        sess["variant_assignments"] = {"0": "correct", "1": "correct"}
    trial_store.start_trial("p1", 0)

    resp = client.get("/quiz")

    assert resp.status_code == 200


# --- export ------------------------------------------------------------------------


def test_every_scale_item_is_still_asked():
    # Plus the old wordings' columns, for older records.
    assert PRE_SURVEY_ITEMS == KEPT + ["independent_programming", "blind_trust"]
    for items in PRE_SURVEY_SCALES.values():
        assert set(items) <= set(KEPT)
    in_scales = {i for items in PRE_SURVEY_SCALES.values() for i in items}
    assert "self_solving" not in in_scales
    assert "hesitant_without_checking" not in in_scales


def test_new_record_exports_current_scales_items_and_blank_retired_scales():
    pre = {name: "6" for name in KEPT}
    pre["self_solving"] = "2"
    pre["hesitant_without_checking"] = "1"  # a single question, not in the trust mean
    row = build_participant_row({"pre_survey_answers": pre}, 1)

    assert set(row) <= set(PARTICIPANT_FIELDS)
    assert row["pre_programming_skill_mean"] == 6.0
    assert row["pre_need_for_cognition_mean"] == 6.0
    assert row["pre_ai_trust_mean"] == 6.0
    assert row["pre_self_solving"] == "2"
    assert row["pre_code_comprehension"] == "6"
    assert row["pre_hesitant_without_checking"] == "1"
    assert row["pre_blind_trust"] == ""
    for scale in RETIRED_PRE_SURVEY_SCALES:
        assert row[f"pre_{scale}_mean"] is None


def test_old_record_uses_the_same_questions_for_current_scales():
    # Kept questions answered 7, removed ones 1: a current mean that mixed in
    # a removed question would not be 7.
    pre = {name: "7" for name in OLD_VERSION if name not in REMOVED}
    pre.update({name: "1" for name in REMOVED})
    row = build_participant_row({"pre_survey_answers": pre}, 1)

    # The old wording is not mixed in: programming_skill is the mean of the
    # two unchanged questions, and the old answer has its own column.
    assert row["pre_programming_skill_mean"] == 7.0
    assert row["pre_independent_programming"] == "7"
    assert row["pre_program_without_ai"] == ""
    assert row["pre_blind_trust"] == "1"
    assert row["pre_hesitant_without_checking"] == ""
    assert row["pre_need_for_cognition_mean"] == 7.0
    assert row["pre_ai_trust_mean"] == 7.0
    assert row["pre_ai_concerns_mean"] == 1.0
    assert row["pre_programming_self_efficacy_mean"] == 3.0  # 7, 1, 1
    # The pre-2026-11-15 five-item mean: four kept (7) and solution_exploration (1).
    assert row["pre_need_for_cognition_5item_mean"] == 5.8


# --- layout (shared with the post-trial and final surveys) ----------------------


def test_pre_survey_uses_the_shared_rating_layout(client):
    html = client.get("/pre_survey").get_data(as_text=True)

    # One fieldset per question, each option a label around its radio button,
    # which the stylesheet turns into a vertical list on phones.
    assert html.count('<fieldset class="form-group">') == len(KEPT)
    assert html.count('class="likert-option"') == 7 * len(KEPT)
    assert 'class="likert-group"' not in html
    # No "I didn't need AI assistance" option in the pre-survey.
    assert "not_needed" not in html


def test_saved_answers_are_shown_as_selected(client):
    with client.session_transaction() as sess:
        sess["pre_survey_data"] = {"code_comprehension": "6", "ai_reliability": "2"}
    html = client.get("/pre_survey").get_data(as_text=True)

    checked = re.findall(r'<input type="radio" id="([a-z_]+_\d)"[^>]*checked', html)
    assert sorted(checked) == ["ai_reliability_2", "code_comprehension_6"]
