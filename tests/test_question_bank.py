"""
The question bank after drafting every problem as correct, wrong and IDK
(app/utils/questions.py): what is written, what is served, how the wrong
answer a recommendation endorses is recorded, and how the IDK wording rotates.
"""

import copy
from collections import Counter
from pathlib import Path

import pytest
from flask import Flask

from app.routes import main_bp
from app.utils.measures import get_ai_endorsed_answer
from app.utils.questions import (
    IDK_ABSTENTIONS,
    get_initial_recommendation,
    idk_phrasing_order,
    questions,
)
from app.utils.sequences import build_eligibility_pools
from app.utils.trial_types import (
    is_in_rotation,
    is_pending_review,
    is_placeholder,
)

C, W, I = "correct", "confident_wrong", "idk"
ORIGINAL = {C: set(range(8)), W: set(range(8, 14)), I: {8, 9, 10}}


def test_every_problem_has_all_three_variants_written():
    for q in questions:
        for t in (C, W, I):
            assert not is_placeholder(q["variants"][t]), (q["id"], t)


def test_new_drafts_are_pending_review_and_held_out_of_rotation():
    for q in questions:
        for t in (C, W, I):
            variant = q["variants"][t]
            if q["id"] in ORIGINAL[t]:
                assert is_in_rotation(variant), (q["id"], t)
            else:
                assert is_pending_review(variant), (q["id"], t)
                assert not is_in_rotation(variant), (q["id"], t)


def test_rotation_pools_are_unchanged_until_full_randomization():
    pools = build_eligibility_pools(questions)
    assert {t.value: set(ids) for t, ids in pools.items()} == ORIGINAL


def test_wrong_variants_store_a_wrong_option_that_the_text_states():
    for q in questions:
        variant = q["variants"][W]
        endorsed = variant["endorsed_answer"]
        assert endorsed in q["options"] and endorsed != q["correct"], q["id"]
        assert endorsed in variant["initial_recommendation"], q["id"]


def test_correct_variants_state_the_correct_answer():
    for q in questions:
        assert q["correct"] in q["variants"][C]["initial_recommendation"], q["id"]


def test_drafted_variants_share_the_topic_chat_prompt():
    for q in questions:
        original = q["variants"][q["default_trial_type"].value]["chat_prompt"]
        for t in (C, W):
            assert q["variants"][t]["chat_prompt"] == original, (q["id"], t)


def test_endorsed_answer_is_read_from_the_variant_not_guessed():
    # A text naming two options would be ambiguous for the old text match.
    q = copy.deepcopy(questions[12])
    q["variants"][W]["initial_recommendation"] = "It is 2, not 3."
    assert get_ai_endorsed_answer(q, W) == "2"


def test_old_records_without_endorsed_answer_fall_back_to_the_text():
    q = copy.deepcopy(questions[12])
    del q["variants"][W]["endorsed_answer"]
    assert get_ai_endorsed_answer(q, W) == "2"


def test_idk_wordings_are_distinct_and_carry_no_digits():
    assert len(IDK_ABSTENTIONS) == len(set(IDK_ABSTENTIONS)) == 3
    for text in IDK_ABSTENTIONS:
        assert not any(ch.isdigit() for ch in text)


def test_idk_recommendation_uses_the_requested_wording():
    assert get_initial_recommendation(9, I)["recommendation"] == IDK_ABSTENTIONS[0]
    for k, text in enumerate(IDK_ABSTENTIONS):
        assert get_initial_recommendation(9, I, idk_phrasing=k)["recommendation"] == text
    # The wording only applies to IDK trials.
    assert get_initial_recommendation(0, C, idk_phrasing=2)["recommendation"] == (
        questions[0]["variants"][C]["initial_recommendation"]
    )


def test_idk_phrasing_order_is_a_reproducible_permutation():
    assert idk_phrasing_order(None) == [0, 1, 2]
    for seed in range(50):
        order = idk_phrasing_order(seed)
        assert sorted(order) == [0, 1, 2]
        assert order == idk_phrasing_order(seed)


def test_each_wording_appears_at_each_encounter_across_participants():
    by_encounter = [Counter() for _ in range(3)]
    for seed in range(600):
        for encounter, wording in enumerate(idk_phrasing_order(seed)):
            by_encounter[encounter][wording] += 1
    for counts in by_encounter:
        assert set(counts) == {0, 1, 2}
        assert min(counts.values()) > 150  # roughly a third each


def _client(question_order, variants, phrasing_order):
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent.parent / "app" / "templates"),
    )
    app.secret_key = "test-secret"
    app.register_blueprint(main_bp)
    c = app.test_client()
    with c.session_transaction() as sess:
        sess.update(
            question_order=question_order, question_index=0, begin=0,
            variant_assignments=variants, idk_phrasing_order=phrasing_order,
            answers=[], times=[], chat_history=[], chat_transcript=[],
        )
    return c


def test_route_shows_and_logs_the_participants_wording():
    c = _client([9, 0], {"9": I, "0": C}, [2, 0, 1])
    c.get("/quiz")
    first = c.get("/get_initial_recommendation").get_json()["recommendation"]
    assert first == IDK_ABSTENTIONS[2]
    # A reload returns the text that was shown, without logging it again.
    assert c.get("/get_initial_recommendation").get_json()["recommendation"] == first

    c.post("/quiz", data={"answer": questions[9]["options"][0]})
    with c.session_transaction() as sess:
        (entry,) = [e for e in sess["chat_transcript"] if e["is_initial_recommendation"]]
        record = sess["answers"][9]
    assert entry["idk_phrasing_id"] == 2
    assert record["idk_phrasing_id"] == 2


def test_non_idk_trials_log_no_wording():
    c = _client([0], {"0": C}, [2, 0, 1])
    c.get("/quiz")
    c.get("/get_initial_recommendation")
    c.post("/quiz", data={"answer": questions[0]["options"][0]})
    with c.session_transaction() as sess:
        assert sess["answers"][0]["idk_phrasing_id"] is None
