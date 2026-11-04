import re
from unittest.mock import patch
import pytest

from app.utils import debrief, trial_store
from app.utils.provenance import stated_option
from scripts.export_data import scale_mean, build_trial_rows, write_exports, write_attrition
from tests.test_flow_integrity import client, db, _state, FINAL
from tests.helpers import prepare_session, complete_trials


def test_csrf_blocks_missing_and_foreign_tokens(client):
    client.application.config['CSRF_ENABLED'] = True
    html = client.get('/quiz').get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    assert client.get('/get_initial_recommendation?position=0').status_code == 400
    assert client.get('/get_initial_recommendation?position=0', headers={'X-CSRF-Token': 'wrong'}).status_code == 400
    assert client.get('/get_initial_recommendation?position=0', headers={'X-CSRF-Token': token}).status_code == 200
    assert client.post('/quiz', data={'position': 0, 'answer': 'anything'}).status_code == 400


def test_debrief_requires_final_survey_and_excludes_pending_data(client, db, tmp_path):
    assert client.get('/debrief').status_code == 302
    with client.session_transaction() as sess:
        complete_trials(sess)
    response = client.post('/final_survey', data=FINAL)
    assert response.headers['Location'].endswith('/debrief')
    doc = db.get_collection('users').find_one({})
    assert write_exports([doc], tmp_path)['n_exported'] == 0
    assert 'scripted' in client.get('/debrief').get_data(as_text=True)
    client.post('/debrief', data={'choice': 'keep'})
    doc = db.get_collection('users').find_one({})
    assert write_exports([doc], tmp_path)['n_exported'] == 1
    part = db.get_collection('participants').find_one({})
    code = part['withdrawal_code']
    client.delete_cookie('session')
    client.delete_cookie('participant_id')
    client.post('/withdraw', data={'withdrawal_code': code})
    doc = db.get_collection('users').find_one({})
    assert doc['data_use'] == 'withdraw'
    assert write_exports([doc], tmp_path)['n_exported'] == 0
    assert debrief.choose(part['participant_id'], 'keep') is False


def test_reverse_coding_and_non_numeric_response():
    assert scale_mean({'blind_acceptance': '7', 'questioned_recommendations': '1', 'verified_answers': '1'},
                      ['blind_acceptance', 'questioned_recommendations', 'verified_answers']) == 7
    assert scale_mean({'trust': 'not_needed'}, ['trust']) is None
    assert scale_mean({'trust': '999'}, ['trust']) is None


def test_rating_export_joins_by_position_and_question():
    doc = {'question_order': [1, 0], 'answers': [{'answer': 'a'}, {'answer': 'b'}],
           'post_survey_answers': [{'position': 2, 'question_index': 0, 'trust': '6'},
                                   {'position': 1, 'question_index': 1, 'trust': 'not_needed'}]}
    rows = build_trial_rows(doc, 1)
    assert [r['post_trust'] for r in rows] == ['not_needed', '6']


def test_attrition_includes_dropout_without_identity(client, db, tmp_path):
    client.get('/quiz')
    rows = write_attrition(tmp_path)
    assert len(rows) == 1 and rows[0]['answered'] == 0 and rows[0]['completed'] is False
    assert not {'identity', 'email', 'withdrawal_code'} & set(rows[0])


@pytest.mark.parametrize('text, expected', [
    ('The output is `4`.', '4'), ('4', '4'), ('It is not 4.', None),
    ('The output is 4. The answer is 3.', None), ('Maybe 4 or 3', None),
])
def test_explicit_option_coding_does_not_treat_mentions_as_endorsements(text, expected):
    assert stated_option(text, ['3', '4']) == expected


def test_main_study_rejects_unreviewed_stimuli(monkeypatch):
    monkeypatch.setenv('STUDY_PHASE', 'main')
    from app.utils.readiness import validate_launch
    with pytest.raises(RuntimeError, match='await review'):
        validate_launch()


def test_idk_filter_blocks_observed_indirect_error_hint():
    from app.utils.chat_safety import filter_idk_reply, ABSTENTION
    from app.utils.questions import questions
    reply = 'A local variable must be assigned before it is read; otherwise Python raises an exception.'
    assert filter_idk_reply(questions[9], 'idk', reply) == (ABSTENTION, 'error_option_hint')
    assert filter_idk_reply(questions[9], 'correct', reply) == (reply, None)
    assert filter_idk_reply(questions[9], 'idk', 'Scope determines where names are visible.')[1] is None


def test_survey_store_rejects_changes_after_trial_start(db):
    assert trial_store.save_survey_part('p', 'demographics', {'age_category': '21'})
    trial_store.start_trial('p', 0)
    assert trial_store.save_survey_part('p', 'demographics', {'age_category': '99'}) is False
    assert trial_store.load_surveys('p')['demographics']['age_category'] == '21'


def test_separate_pre_survey_pages_merge_without_erasing_other_pages(db):
    trial_store.save_survey_part('p', 'pre_survey', {'first': '4'})
    trial_store.save_survey_part('p', 'pre_survey', {'second': '5'})
    assert trial_store.load_surveys('p')['pre_survey'] == {'first': '4', 'second': '5'}


def test_idk_route_saves_raw_hint_but_shows_abstention():
    from tests.test_idk_chat_policy import _client_on
    from tests.test_chat_endpoint import _make_completion
    from tests.helpers import trial_get, trial_post
    from app.utils.chat_safety import ABSTENTION
    c = _client_on(9, 'idk')
    trial_get(c, '/get_initial_recommendation')
    raw = 'Reading an uninitialized local variable raises an exception.'
    with patch('app.routes.client.chat.completions.create', return_value=_make_completion(raw)):
        response = trial_post(c, '/chat', json={'message': 'Explain the concept.'})
    assert response.get_json()['response'] == ABSTENTION
    entry = trial_store.turns_for('test-participant')[-1]
    assert entry['raw_completion'] == raw
    assert entry['assistant_message'] == ABSTENTION
    assert entry['policy_filter_reason'] == 'error_option_hint'
