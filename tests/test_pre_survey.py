"""
The 10-question pre-survey (2026-11-15; app/utils/pre_survey.py) and its
export (scripts/export_data.py): one page, unchanged names and wording,
participants who answered the 25-question version count as complete, and
scale means use the same questions for old and new records.
"""

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
    "independent_programming", "code_comprehension", "language_proficiency",
    "code_understanding", "concept_understanding", "complex_problems", "self_solving",
    "ai_dependability", "ai_reliability", "ai_explanation",
]
REMOVED = [
    "learn_languages", "identify_improvements", "solution_exploration",
    "ai_dependency", "incorrect_advice", "blind_trust", "learning_hindrance",
    "fundamental_concepts", "data_structures", "oop_principles", "explain_concepts",
    "ai_principles", "ai_use_cases", "ai_risks", "ai_prompting",
]


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


def test_ten_questions_on_one_page_without_free_text():
    assert len(PRE_SURVEY_PAGES) == 1
    assert all_likert_names() == KEPT
    assert not PRE_SURVEY_PAGES[0].get("free_text")


def test_page_shows_the_ten_questions(client):
    html = client.get("/pre_survey").get_data(as_text=True)

    for name in KEPT:
        assert f'name="{name}"' in html
    for name in REMOVED + ["ai_classroom_concerns"]:
        assert f'name="{name}"' not in html
    assert "<textarea" not in html
    assert "Page 1 of" not in html


def test_answering_all_ten_leads_to_the_instructions(client):
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
    assert first_incomplete_page({name: "4" for name in KEPT + REMOVED}) is None
    assert first_incomplete_page({name: "4" for name in REMOVED}) == 1


# --- export ------------------------------------------------------------------------


def test_every_scale_item_is_still_asked():
    assert PRE_SURVEY_ITEMS == KEPT
    for items in PRE_SURVEY_SCALES.values():
        assert set(items) <= set(KEPT)
    assert "self_solving" not in {i for items in PRE_SURVEY_SCALES.values() for i in items}


def test_new_record_exports_current_scales_items_and_blank_retired_scales():
    pre = {name: "6" for name in KEPT}
    pre["self_solving"] = "2"
    row = build_participant_row({"pre_survey_answers": pre}, 1)

    assert set(row) <= set(PARTICIPANT_FIELDS)
    assert row["pre_programming_skill_mean"] == 6.0
    assert row["pre_need_for_cognition_mean"] == 6.0
    assert row["pre_ai_trust_mean"] == 6.0
    assert row["pre_self_solving"] == "2"
    assert row["pre_code_comprehension"] == "6"
    for scale in RETIRED_PRE_SURVEY_SCALES:
        assert row[f"pre_{scale}_mean"] is None


def test_old_record_uses_the_same_questions_for_current_scales():
    # Kept questions answered 7, removed ones 1: a current mean that mixed in
    # a removed question would not be 7.
    pre = {name: "7" for name in KEPT}
    pre.update({name: "1" for name in REMOVED})
    row = build_participant_row({"pre_survey_answers": pre}, 1)

    assert row["pre_programming_skill_mean"] == 7.0
    assert row["pre_need_for_cognition_mean"] == 7.0
    assert row["pre_ai_trust_mean"] == 7.0
    assert row["pre_ai_concerns_mean"] == 1.0
    assert row["pre_programming_self_efficacy_mean"] == 3.0  # 7, 1, 1
    # The pre-2026-11-15 five-item mean: four kept (7) and solution_exploration (1).
    assert row["pre_need_for_cognition_5item_mean"] == 5.8
