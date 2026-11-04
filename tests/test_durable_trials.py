"""Regression cases for the actual storage and request concurrency hazards."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from app.utils import trial_store
from tests.helpers import prepare_session, trial_get, trial_post
from tests.test_flow_integrity import client, db, _answer, _state, FINAL, RATING
from tests.test_chat_endpoint import _make_completion


def test_missing_and_stale_positions_are_rejected(client):
    client.get('/quiz')
    assert client.get('/get_initial_recommendation').status_code == 409
    trial_get(client, '/get_initial_recommendation')
    assert client.post('/chat', json={'message': 'why?', 'position': 99}).status_code == 409
    _answer(client)
    trial_post(client, '/post_survey', data=RATING)
    client.get('/quiz')
    trial_get(client, '/get_initial_recommendation')
    assert client.post('/quiz', data={'answer': '2', 'position': 0}).status_code == 409
    assert len(trial_store.trials_for(_state(client)['participant_id'])) == 1


def test_late_reply_cannot_erase_answer_or_write_session(client):
    client.get('/quiz')
    trial_get(client, '/get_initial_recommendation')
    state = _state(client)
    pid = state['participant_id']
    q = state['question_order'][0]

    def delayed_reply(**kwargs):
        # Simulate another request committing an answer while the model runs.
        trial_store.record_answer(pid, 0, {'question_index': q, 'position': 1, 'answer': 'saved'})
        return _make_completion('a late reply')

    with patch('app.routes.client.chat.completions.create', side_effect=delayed_reply):
        response = trial_post(client, '/chat', json={'message': 'why?'})
    assert response.status_code == 200
    assert 'Set-Cookie' not in response.headers
    assert trial_store.trials_for(pid)[0]['answer']['answer'] == 'saved'
    assert trial_store.turns_for(pid)[-1]['after_answer'] is True
    assert client.get('/quiz').headers['Location'].endswith('/post_survey')
    assembled = trial_store.assemble(pid, state['question_order'])
    assert assembled['answers'][q]['chat']['n_turns'] == 0
    assert assembled['answers'][q]['chat']['n_late_turns'] == 1


def test_resume_recovers_answer_waiting_for_rating(client):
    state = _state(client)
    trial_store.save_survey_part(state['participant_id'], 'demographics', state['demographics'])
    trial_store.save_survey_part(state['participant_id'], 'pre_survey', state['pre_survey_data'])
    _answer(client)
    client.delete_cookie('session')
    response = client.get('/start/uzh')
    assert response.headers['Location'].endswith('/post_survey')
    assert _state(client)['question_index'] == 0
    trial_post(client, '/post_survey', data=RATING)
    assert _state(client)['question_index'] == 1


def test_final_completion_requires_records_not_only_cursor(client, db):
    with client.session_transaction() as sess:
        sess['question_index'] = len(sess['question_order'])
    response = client.post('/final_survey', data=FINAL)
    assert response.status_code == 302
    assert db.get_collection('users').count_documents({}) == 0


def test_answer_and_rating_each_first_write_wins(db):
    def answer(i):
        return trial_store.record_answer('p', 0, {'question_index': 0, 'answer': str(i)})
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(answer, range(20))) == 1
    def rating(i):
        return trial_store.record_rating('p', 0, {'question_index': 0, 'position': 1, 'trust': str(i)})
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(rating, range(20))) == 1


def test_no_research_payload_left_in_session(client):
    _answer(client)
    with client.session_transaction() as sess:
        assert not {'answers', 'chat_transcript', 'chat_history', 'post_survey_answers'} & set(sess)


def test_legacy_session_migration_is_idempotent(db):
    state = {'participant_id': 'p', 'question_order': [0],
             'answers': [{'question_index': 0, 'answer': 'kept'}],
             'chat_transcript': [{'question_index': 0, 'is_initial_recommendation': False,
                                  'user_message': 'why', 'assistant_message': 'because'}]}
    trial_store.migrate_session(state)
    trial_store.migrate_session(state)
    assert len(trial_store.trials_for('p')) == len(trial_store.turns_for('p')) == 1


def test_surveys_cannot_change_after_trials_begin(client):
    client.get('/quiz')
    before = _state(client)['demographics']
    assert client.post('/demographics', data={}).status_code == 302
    assert _state(client)['demographics'] == before


@pytest.mark.parametrize('trust', ['1', '7', 'not_needed'])
def test_rating_outside_scale_is_rejected(client, trust):
    _answer(client)
    assert trial_post(client, '/post_survey', data={**RATING, 'confidence': '999'}).status_code == 400
    assert trial_post(client, '/post_survey', data={**RATING, 'trust': trust}).status_code == 302
    pid = _state(client)['participant_id']
    assert trial_store.trials_for(pid)[0]['rating']['trust'] == trust
