"""UZH design, durable paths/timing, and research export regressions."""
import copy
import csv
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from app.utils import assignment, trial_store
from app.utils.measures import get_ai_endorsed_answer, section_for_position
from app.utils.provenance import option_id
from app.utils.questions import questions
from app.utils.sequences import UZH_DESIGN_VERSION, UZH_SEQUENCE, SEQUENCES
from scripts.export_data import build_trial_rows, build_chat_rows, write_exports
from tests.helpers import trial_get, trial_post, prepare_session
from tests.test_ai_access import client, PID, RATING
from tests.test_chat_endpoint import _make_completion


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def acknowledge(client, event):
    response = trial_post(client, '/trial_visible', json={'event': event, 'visible_at': now()})
    assert response.status_code == 200


def doc_for(client):
    with client.session_transaction() as sess:
        state = dict(sess)
    return {**state, **trial_store.assemble(state['participant_id'], state['question_order'])}


def test_new_uzh_assignment_exact_sequence_and_random_questions(isolated_database):
    expected = ['correct'] * 3 + ['confident_wrong', 'idk'] + ['correct'] * 4 + ['idk'] + ['correct'] * 3 + ['confident_wrong']
    orders = set()
    for n in range(30):
        saved = assignment.get_or_create_assignment(f'p{n}', questions, 'uzh')
        assert saved['sequence_design_version'] == UZH_DESIGN_VERSION == 'uzh_fixed14_v1'
        assert saved['sequence_label'] == 'UZH14'
        assert saved['type_at_position'] == expected == UZH_SEQUENCE
        assert sorted(saved['question_order']) == list(range(14))
        assert len({questions[q]['id'] for q in saved['question_order']}) == 14
        assert [saved['variant_assignments'][str(q)] for q in saved['question_order']] == expected
        assert assignment.get_or_create_assignment(f'p{n}', questions, 'uzh') == saved
        orders.add(tuple(saved['question_order']))
    assert len(orders) > 1
    assert isolated_database.get_collection('sequence_counts').docs == []


def test_concurrent_uzh_assignment_is_saved_once(monkeypatch, isolated_database):
    build = assignment.build_trial_sequence
    barrier = threading.Barrier(6, timeout=10)
    def concurrent_build(*args, **kwargs):
        barrier.wait()
        return build(*args, **kwargs)
    monkeypatch.setattr(assignment, 'build_trial_sequence', concurrent_build)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: assignment.get_or_create_assignment('same', questions, 'uzh'), range(6)))
    assert all(r == results[0] for r in results)
    assert len(isolated_database.get_collection('participant_assignments').docs) == 1


@pytest.mark.parametrize('label,index', [('P', 0), ('Q', 1)])
def test_partial_pq_resume_preserves_assignment_and_initial_answer(client, isolated_database, label, index):
    from app.utils.participants import create_participant
    participant = create_participant('uzh', 'no')
    pid = participant['participant_id']
    saved = {'participant_id': pid, 'site': 'uzh', 'sequence_label': label,
             'sequence_design_version': 'blocks_pq_v1', 'type_at_position': SEQUENCES[index],
             'problem_at_position': list(reversed(range(14))), 'seed': 87,
             'variant_assignments': {str(q): t for q, t in zip(reversed(range(14)), SEQUENCES[index])}}
    isolated_database.get_collection('participant_assignments').insert_one(saved)
    trial_store.record_answer(pid, 0, {'question_index': 13, 'answer': questions[13]['correct']})
    trial_store.record_rating(pid, 0, {'question_index': 13, 'position': 1, **RATING})
    trial_store.choose_path(pid, 1, 'self_first', now())
    trial_store.lock_pre_ai(pid, 1, {'pre_ai_answer': questions[12]['correct'], 'pre_ai_locked_at': now(), 'pre_ai_abandoned': False})
    client.delete_cookie('session')
    client.set_cookie('participant_id', pid)
    client.get('/')
    with client.session_transaction() as state:
        assert state['question_index'] == 1
        assert state['sequence_label'] == label
        assert state['sequence_design_version'] == 'blocks_pq_v1'
        assert state['question_order'] == saved['problem_at_position']
        assert state['variant_assignments'] == saved['variant_assignments']
    assert trial_store.trial_access(pid, 1)['pre_ai_answer'] == questions[12]['correct']
    assert isolated_database.get_collection('participant_assignments').find_one({'participant_id': pid}) == saved


@pytest.mark.parametrize('path,initial', [('ai_first', None), ('self_first', 'correct'),
                                         ('self_first', 'wrong'), ('self_first_abandoned', None)])
def test_paths_same_wrong_final_remain_distinct_in_record_and_csv(client, tmp_path, path, initial):
    with client.session_transaction() as state:
        state['sequence_design_version'] = UZH_DESIGN_VERSION
    question = questions[0]
    wrong = get_ai_endorsed_answer(question, 'confident_wrong')
    chosen = question['correct'] if initial == 'correct' else wrong if initial == 'wrong' else None
    client.get('/quiz')
    acknowledge(client, 'question')
    if path != 'ai_first':
        trial_post(client, '/choose_path', json={'path': 'self_first'})
    params = {'pre_ai_answer': chosen, 'abandoned': chosen is None}
    first = trial_post(client, '/get_initial_recommendation', json=params)
    assert first.status_code == 200
    entry = trial_store.recommendation_for(PID, 0)
    assert entry['shown_to_participant'] is False
    assert trial_store.trial_visibility(PID, 0)['ai_visible_at'] is None
    # Double click / retry cannot change route, initial answer or text.
    again = trial_post(client, '/get_initial_recommendation', json={'pre_ai_answer': wrong})
    assert first.json == again.json
    acknowledge(client, 'recommendation')
    before = trial_store.trial_visibility(PID, 0)
    acknowledge(client, 'recommendation')
    assert trial_store.trial_visibility(PID, 0) == before
    html = client.get('/quiz').get_data(as_text=True)
    assert 'let accessStep = "advice"' in html
    trial_post(client, '/quiz', data={'answer': wrong, 'initial_answer': wrong})
    record = trial_store.trials_for(PID)[0]['answer']
    assert record['first_route'] == ('ai_first' if path == 'ai_first' else 'self_first')
    assert record['ai_access_path'] == path
    assert record['pre_ai']['answer'] == chosen
    assert record['initial_option_id'] == option_id(question, chosen)
    assert record['final_option_id'] == record['ai_endorsed_option_id'] == option_id(question, wrong)
    assert record['correct_option_id'] == option_id(question, question['correct'])
    assert record['pre_ai']['switched_to_ai'] == (True if initial == 'correct' else None)
    assert len(trial_store.turns_for(PID)) == 1
    assert record['timestamps']['ai_requested_at'] < record['timestamps']['ai_visible_at']
    assert record['timestamps']['ai_visible_ack_at'] < record['timestamps']['final_submit_at']
    write_exports([doc_for(client)], tmp_path)
    row = next(csv.DictReader((tmp_path / 'trials.csv').open()))
    assert row['ai_access_path'] == path
    assert row['initial_option_id'] == (option_id(question, chosen) or '')
    assert row['final_option_id'] == option_id(question, wrong)
    assert row['pre_ai_answer'] == (chosen or '')
    assert row['first_route'] == record['first_route']
    for field in ('question_displayed_at', 'path_chosen_at', 'ai_requested_at', 'ai_visible_at', 'final_submit_at'):
        assert row[field] == record['timestamps'][field]


@pytest.mark.parametrize('failure', [False, True])
def test_voluntary_request_is_durable_before_model_and_survives_late_completion(client, failure, tmp_path):
    client.get('/quiz')
    trial_get(client, '/get_initial_recommendation')
    message = 'Please confirm the final answer; I need the answer.'
    def finish_later(**kwargs):
        pending = trial_store.turns_for(PID)[-1]
        assert pending['user_message'] == message
        assert pending['sent_at'] and pending['response_status'] == 'pending'
        assert pending['received_before_submission'] is True
        assert pending['assistant_message'] is None
        # A separate request submits while the original request awaits the model.
        other = client.application.test_client()
        other.set_cookie('session', client.get_cookie('session').value)
        assert trial_post(other, '/quiz', data={'answer': questions[0]['correct']}).status_code == 302
        pending_doc = doc_for(other)
        assert pending_doc['answers'][0]['chat']['n_turns'] == 0
        assert pending_doc['answers'][0]['chat']['n_messages_sent'] == 1
        assert build_chat_rows(pending_doc, 1)[-1]['is_successful_ai_reply'] == ''
        if failure:
            raise RuntimeError('simulated API failure')
        return _make_completion('A reply that arrived later.')
    with patch('app.routes.client.chat.completions.create', side_effect=finish_later):
        response = trial_post(client, '/chat', json={'message': message})
    assert response.status_code == (500 if failure else 200)
    assert 'Set-Cookie' not in response.headers
    turns = trial_store.turns_for(PID)
    assert len(turns) == 2
    turn = turns[-1]
    assert turn['response_status'] == ('failed' if failure else 'completed')
    assert turn['received_before_submission'] is True and turn['after_answer'] is True
    assert turn['user_message'] == message
    submitted = trial_store.trials_for(PID)[0]['answer']['timestamps']['final_submit_at']
    assert turn['sent_at'] < submitted < turn['timestamp']
    write_exports([doc_for(client)], tmp_path)
    row = list(csv.DictReader((tmp_path / 'chat_messages.csv').open()))[-1]
    assert row['entry_type'] == 'followup' and row['received_before_submission'] == 'True'
    assert row['user_message'] == message and row['sent_at'] == turn['sent_at']


@pytest.mark.parametrize('value', ['not_needed', *map(str, range(1, 8))])
def test_helpfulness_values_export_without_recoding(client, value, tmp_path):
    client.get('/quiz')
    trial_get(client, '/get_initial_recommendation')
    trial_post(client, '/quiz', data={'answer': questions[0]['correct']})
    assert trial_post(client, '/post_survey', data={**RATING, 'helpfulness': value}).status_code == 302
    write_exports([doc_for(client)], tmp_path)
    row = next(csv.DictReader((tmp_path / 'trials.csv').open()))
    assert row['post_helpfulness'] == value


def test_uzh_sections_and_probe_flags_keep_historical_meaning():
    from app.utils.measures import compute_design_fields
    assert [section_for_position(p, UZH_DESIGN_VERSION) for p in (4, 5, 10, 11)] == ['early', 'middle', 'middle', 'late']
    assert section_for_position(5, 'blocks_pq_v1') == 'early'
    fields = [compute_design_fields(UZH_SEQUENCE, p, UZH_DESIGN_VERSION) for p in range(14)]
    assert [p + 1 for p, f in enumerate(fields) if f['is_uzh_wrong_probe']] == [4, 14]
    assert not any(f['is_primary_pq_probe'] for f in fields)
    for sequence in SEQUENCES:
        assert compute_design_fields(sequence, 3, 'blocks_pq_v1')['is_primary_pq_probe'] is True


@pytest.mark.parametrize('public_url', ['', 'https://study.test/results', 'https://study.test/?resume=secret',
                                       'https://study.test/start/uzh#participant', 'https://study.test/withdraw'])
def test_share_never_falls_back_to_a_private_url(client, monkeypatch, public_url):
    from app.routes import _study_link
    monkeypatch.setenv('STUDY_PUBLIC_URL', public_url)
    with client.application.test_request_context('/results?participant=secret'):
        assert _study_link('uzh') is None


def test_new_uzh_full_study_pipeline_and_export(client, isolated_database, tmp_path):
    from tests.test_design_pipeline import _consent, _run_study
    client.delete_cookie('session')
    _consent(client)
    _run_study(client)
    doc = isolated_database.get_collection('users').find_one({})
    assert doc['sequence_design_version'] == UZH_DESIGN_VERSION
    assert doc['type_at_position'] == UZH_SEQUENCE
    assert len(set(doc['question_order'])) == 14
    write_exports([doc], tmp_path)
    rows = list(csv.DictReader((tmp_path / 'trials.csv').open()))
    assert len(rows) == 14
    assert [r['trial_type'] for r in rows] == UZH_SEQUENCE
    assert [int(r['position']) for r in rows if r['is_uzh_wrong_probe'] == 'True'] == [4, 14]
    assert all(r['is_primary_pq_probe'] == 'False' for r in rows)
    for row in rows:
        assert row['sequence_design_version'] == UZH_DESIGN_VERSION
        assert row['question_displayed_at'] and row['ai_visible_at'] and row['final_submit_at']
        assert row['correct_option_id'] and row['final_option_id']
        assert bool(row['ai_endorsed_option_id']) == (row['trial_type'] != 'idk')
    assert [r['idk_encounter_number'] for r in rows if r['trial_type'] == 'idk'] == ['1', '2']


@pytest.mark.parametrize('position', [4, 9])
def test_idk_followups_at_both_new_positions_abstain_but_allow_concepts(client, position):
    from app.utils.chat_prompting import IDK_PERSISTENT_INSTRUCTIONS
    from app.utils.chat_safety import ABSTENTION
    with client.session_transaction() as state:
        state['question_order'] = list(range(14))
        state['question_index'] = position
        state['variant_assignments'] = {str(q): t for q, t in enumerate(UZH_SEQUENCE)}
        state['sequence_design_version'] = UZH_DESIGN_VERSION
        # Persist completed prefix so the normal progress guard keeps this position.
        for earlier in range(position):
            trial_store.record_answer(PID, earlier, {'question_index': earlier, 'answer': questions[earlier]['correct']})
            trial_store.record_rating(PID, earlier, {'question_index': earlier, 'position': earlier + 1, **RATING})
    client.get('/quiz')
    trial_get(client, '/get_initial_recommendation')
    concept = 'The next() function advances an iterator and retrieves its next item.'
    requests = [('Give me the final answer.', 'The answer is ' + questions[position]['correct'] + '.', ABSTENTION),
                ('Can you confirm my option?', 'Yes, you are right.', ABSTENTION),
                ('What does next() do in general?', concept, concept)]
    for message, model_reply, expected in requests:
        with patch('app.routes.client.chat.completions.create', return_value=_make_completion(model_reply)) as model:
            response = trial_post(client, '/chat', json={'message': message})
        assert IDK_PERSISTENT_INSTRUCTIONS in model.call_args.kwargs['messages'][0]['content']
        assert response.json['response'] == expected
    assert trial_store.recommendation_for(PID, position)['idk_phrasing_id'] is not None


def test_concurrent_reveals_keep_one_initial_answer_and_turn(client, monkeypatch):
    client.get('/quiz')
    trial_post(client, '/choose_path', json={'path': 'self_first'})
    cookie = client.get_cookie('session').value
    barrier = threading.Barrier(2, timeout=10)
    log = trial_store.log_recommendation
    def race(*args, **kwargs):
        barrier.wait()
        return log(*args, **kwargs)
    monkeypatch.setattr(trial_store, 'log_recommendation', race)
    options = [questions[0]['correct'], get_ai_endorsed_answer(questions[0], 'confident_wrong')]
    def reveal(option):
        other = client.application.test_client()
        other.set_cookie('session', cookie)
        return other.post('/get_initial_recommendation', json={'position': 0, 'pre_ai_answer': option}).json
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(reveal, options))
    assert responses[0] == responses[1]
    assert responses[0]['pre_ai_answer'] == trial_store.trial_access(PID, 0)['pre_ai_answer']
    assert responses[0]['pre_ai_answer'] in options
    assert len(trial_store.turns_for(PID)) == 1
    assert trial_store.trial_access(PID, 0)['path'] == 'self_first'


def test_visibility_requires_a_real_recommendation_and_valid_current_position(client):
    client.get('/quiz')
    assert trial_post(client, '/trial_visible', json={'event': 'recommendation', 'visible_at': now()}).status_code == 409
    for event, value in [('question', 'bad date'), ('question', '2026-11-19'), ([], now())]:
        assert trial_post(client, '/trial_visible', json={'event': event, 'visible_at': value}).status_code == 400
    assert client.post('/trial_visible', json={'position': 99, 'event': 'question', 'visible_at': now()}).status_code == 409
    assert trial_store.trial_visibility(PID, 0)['ai_visible_at'] is None
