#!/usr/bin/env python3
"""Opt-in integration check using a temporary local mongod, never the configured database."""
import os
import sys
import tempfile
import shutil
import socket
import uuid
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from pymongo import MongoClient

def main():
    binary = shutil.which('mongod')
    if not binary:
        raise SystemExit('Install mongod to run this optional integration check.')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='study-mongo-') as directory:
        process = subprocess.Popen([binary, '--dbpath', directory, '--port', str(port),
                                    '--bind_ip', '127.0.0.1', '--logpath', directory + '/mongo.log', '--quiet'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mongo = MongoClient(f'mongodb://127.0.0.1:{port}', serverSelectionTimeoutMS=5000)
        try:
            mongo.admin.command('ping')
            from app.utils import db, trial_store
            from app import create_app
            from app.utils.pre_survey import PRE_SURVEY_PAGES, page_likert_names
            from app.utils.questions import questions
            db._client = mongo
            os.environ['MONGODB_DB'] = 'isolated_study_' + uuid.uuid4().hex
            os.environ['STUDY_PHASE'] = 'pilot'
            app = create_app()
            app.config.update(TESTING=True, CSRF_ENABLED=False)
            c = app.test_client()
            c.get('/')
            assert c.post('/consent', data={'consent':'on', 'adult':'yes','prior_participation':'no'}).status_code == 302
            with c.session_transaction() as sess:
                pid = sess['participant_id']
                q = sess['question_order'][0]
                demos = {'age_category':'21'}
                pre = {name:'4' for page in PRE_SURVEY_PAGES for name in page_likert_names(page)}
                trial_store.save_survey_part(pid, 'demographics', demos)
                trial_store.save_survey_part(pid, 'pre_survey', pre)
            assert c.get('/quiz').status_code == 200
            assert c.get('/get_initial_recommendation?position=0').status_code == 200
            cookie = c.get_cookie(app.config['SESSION_COOKIE_NAME']).value
            started, release = threading.Event(), threading.Event()
            result = {}
            def reply(**kwargs):
                started.set()
                assert release.wait(10)
                return SimpleNamespace(model='test-model', choices=[SimpleNamespace(
                    message=SimpleNamespace(content='The output is 4.'), finish_reason='stop')], usage=None)
            def request_chat():
                other = app.test_client()
                other.set_cookie(app.config['SESSION_COOKIE_NAME'], cookie)
                result['response'] = other.post('/chat', json={'position':0, 'message':'why?'})
            with patch('app.routes.client.chat.completions.create', side_effect=reply):
                thread = threading.Thread(target=request_chat)
                thread.start()
                assert started.wait(10)
                response = c.post('/quiz', data={'position':0, 'answer':questions[q]['correct']})
                assert response.status_code == 302
                release.set()
                thread.join(10)
                assert not thread.is_alive()
            assert result['response'].status_code == 200
            assert 'Set-Cookie' not in result['response'].headers
            assert trial_store.trials_for(pid)[0]['answer']['answer'] == questions[q]['correct']
            assert trial_store.turns_for(pid)[-1]['after_answer']
            assert c.get('/quiz').headers['Location'].endswith('/post_survey')
            c.delete_cookie(app.config['SESSION_COOKIE_NAME'])
            assert c.get('/').headers['Location'].endswith('/post_survey')
            with ThreadPoolExecutor(max_workers=8) as pool:
                writes = list(pool.map(lambda i: trial_store.record_answer('race', 0, {'question_index':0,'answer':str(i)}), range(20)))
            assert sum(writes) == 1
            print('PASS: real MongoDB indexes, threaded answer/chat race, no stale session write, lost-session resume')
        finally:
            mongo.close()
            process.terminate()
            process.wait(timeout=10)

if __name__ == '__main__':
    main()
