"""Optional real Chromium checks: pip install playwright; playwright install chromium.

All HTTP requests are bridged to Flask's test client; no network, real DB or AI.
"""
import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest

playwright = pytest.importorskip('playwright.sync_api')
from app.utils import trial_store
from app.utils.measures import get_ai_endorsed_answer
from app.utils.questions import questions
from app.utils.sequences import UZH_DESIGN_VERSION
from tests.test_ai_access import PID
from tests.test_trial_interface import _client
from scripts.export_data import build_trial_rows
from tests.test_uzh_fixed14 import doc_for


@pytest.fixture
def client():
    # Playwright callbacks run in separate greenlets: do not preserve Flask's
    # request context across requests as a `with app.test_client()` would.
    client = _client([0, 1])
    with client.session_transaction() as state:
        state['variant_assignments']['0'] = 'confident_wrong'
    return client


@contextmanager
def _bridged_page(client, follow_redirects=True):
    """A Chromium page whose requests all go to `client`. With
    follow_redirects=False the browser follows redirects itself, so its
    address bar shows the page that was actually reached."""
    client.application.static_folder = str(Path(__file__).resolve().parents[1] / 'app' / 'static')
    with playwright.sync_playwright() as p:
        if not os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE') and not Path(p.chromium.executable_path).exists():
            pytest.skip('Install Chromium with playwright install chromium, or set PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser = p.chromium.launch(executable_path=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def bridge(route):
            request = route.request
            parsed = urlsplit(request.url)
            response = client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                   method=request.method, data=request.post_data,
                                   follow_redirects=follow_redirects,
                                   headers={'Content-Type': request.headers.get('content-type', '')})
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.data)
        page.route('**/*', bridge)
        yield page
        assert errors == []
        browser.close()


@pytest.fixture
def page(client):
    with client.session_transaction() as state:
        state['sequence_design_version'] = UZH_DESIGN_VERSION
    with _bridged_page(client) as page:
        yield page


@pytest.mark.parametrize('path', ['ai_first', 'saved', 'unsure', 'retry'])
def test_browser_single_action_paths_refresh_and_final_edit(client, page, path, tmp_path):
    if path == 'unsure':
        page.set_viewport_size({'width': 390, 'height': 844})
    page.goto('http://study.test/quiz')
    playwright.expect(page.locator('#choose-self-first')).to_be_visible()
    if path == 'ai_first':
        page.locator('#choose-ai-first').click()
    else:
        page.locator('#choose-self-first').click()
        button = page.locator('#step-own button')
        playwright.expect(button).to_have_count(1)
        playwright.expect(button).to_be_enabled()
        playwright.expect(button).to_have_text('I’m not sure — show me the AI’s recommendation.')
        assert page.locator('#skip-to-ai').count() == 0
        page.screenshot(path=str(tmp_path / 'own-step.png'), full_page=True)
        if path in {'saved', 'retry'}:
            options = page.locator('input[name="answer"]')
            options.nth(1).check()
            playwright.expect(button).to_have_text('Save my answer and show the AI’s recommendation.')
            correct = questions[0]['correct']
            page.locator('input[name="answer"]').evaluate_all('(inputs, value) => inputs.find(i => i.value === value).click()', correct)
            assert page.locator('input[name="answer"]:checked').input_value() == correct
        if path == 'retry':
            # Lose the HTTP response after the server saves the initial answer.
            def lose_response(route):
                response = client.post('/get_initial_recommendation', json=route.request.post_data_json)
                assert response.status_code == 200
                page.unroute('**/get_initial_recommendation', lose_response)
                route.abort('failed')
            page.route('**/get_initial_recommendation', lose_response)
        button.click()
        if path == 'retry':
            playwright.expect(page.locator('#step-pending')).to_be_visible(timeout=15000)
            page.locator('#see-ai').click()
    playwright.expect(page.locator('#user-message')).to_be_enabled(timeout=20000)
    access = trial_store.trial_access(PID, 0)
    expected_initial = questions[0]['correct'] if path in {'saved', 'retry'} else None
    assert access['pre_ai_answer'] == expected_initial
    assert access['path'] == ('ai_first' if path == 'ai_first' else 'self_first')
    visible = trial_store.trial_visibility(PID, 0)
    assert visible['question_displayed_at'] and visible['ai_visible_at']
    page.reload()
    playwright.expect(page.locator('#user-message')).to_be_enabled()
    assert trial_store.trial_visibility(PID, 0) == visible
    assert len(trial_store.turns_for(PID)) == 1
    assert page.locator('#chat-messages .bot-message').count() == 1
    wrong = get_ai_endorsed_answer(questions[0], 'confident_wrong')
    page.locator('input[name="answer"]').evaluate_all('(inputs, value) => inputs.find(i => i.value === value).click()', wrong)
    page.locator('#submit-answer').click()
    playwright.expect(page.locator('h2')).to_contain_text('your rating')
    row = build_trial_rows(doc_for(client), 1)[0]
    assert row['pre_ai_answer'] == expected_initial
    assert row['participant_answer'] == wrong
    assert row['ai_access_path'] == ('self_first' if expected_initial else 'ai_first' if path == 'ai_first' else 'self_first_abandoned')
    numeric = page.locator('input[name="helpfulness"][value="7"]')
    alternative = page.locator('#helpfulness_not_needed')
    assert alternative.evaluate('(e) => e.closest(".likert-scale") === null')
    assert numeric.evaluate('(e) => e.closest(".likert-scale") !== null')
    numeric.check()
    numeric.focus()
    page.keyboard.press('ArrowRight')
    assert alternative.is_checked() and not numeric.is_checked()
    assert alternative.evaluate('(e) => getComputedStyle(e.closest("label")).fontWeight') == '700'
    assert alternative.evaluate('''(e) => {
        const option = e.closest(".helpfulness-alternative").getBoundingClientRect();
        const scale = e.closest("fieldset").querySelector(".likert-scale").getBoundingClientRect();
        return innerWidth > 900 ? Math.abs(option.top - scale.top) < 1 : option.top >= scale.bottom;
    }''')
    page.screenshot(path=str(tmp_path / 'rating.png'), full_page=True)


def test_browser_stale_ai_first_tab_restores_original_own_first_route(client, page):
    page.goto('http://study.test/quiz')
    playwright.expect(page.locator('#choose-ai-first')).to_be_visible()
    response = client.post('/choose_path', json={'position': 0, 'path': 'self_first'})
    assert response.status_code == 200
    page.locator('#choose-ai-first').click()
    playwright.expect(page.locator('#step-own')).to_be_visible(timeout=15000)
    playwright.expect(page.locator('#save-and-see-ai')).to_be_enabled()
    assert trial_store.trial_access(PID, 0)['path'] == 'self_first'
    assert trial_store.recommendation_for(PID, 0) is None


# --- Pages after the final survey (2026-11-19) ---------------------------------


@pytest.fixture
def finished(isolated_database):
    """A finished UZH participant (question 4 answered wrongly) in Chromium."""
    from tests.test_review_fixes import _completed_client_with_answers
    client = _completed_client_with_answers(wrong_positions=(3,))
    with _bridged_page(client, follow_redirects=False) as page:
        yield client, page, isolated_database


def test_browser_end_pages_draw_choice_results_and_finish(finished, tmp_path):
    client, page, db = finished
    participants = db.get_collection('participants')
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.goto('http://study.test/completion')
    keep = page.get_by_role('button', name='Yes, use my responses and see my results')
    withdraw = page.get_by_role('button', name='Withdraw my responses')
    playwright.expect(keep).to_be_visible()
    playwright.expect(withdraw).to_be_hidden()  # folded away until asked for
    page.screenshot(path=str(tmp_path / 'completion.png'), full_page=True)

    # Enter in the email field joins the draw and records no data-use choice.
    page.locator('#draw-email').fill('Student@Example.org')
    page.locator('#draw-email').press('Enter')
    playwright.expect(page.locator('.draw-done')).to_contain_text('You’ve joined the prize draw')
    assert page.url.endswith('/completion#prize-draw')
    assert [e['email'] for e in db.get_collection('prize_draw_entries').docs] == ['student@example.org']
    assert participants.find_one({}).get('data_use') is None

    # Withdrawing takes a second, deliberate click; opening it changes nothing.
    page.get_by_text('I don’t want my responses to be used').click()
    playwright.expect(withdraw).to_be_visible()
    assert participants.find_one({}).get('data_use') is None

    keep.click()
    page.wait_for_url('**/results')
    assert participants.find_one({})['data_use'] == 'keep'
    playwright.expect(page.locator('.score-value')).to_have_text('13')
    playwright.expect(page.locator('#q4 pre')).to_be_hidden()
    page.locator('.score-strip a', has_text='4').first.click()
    playwright.expect(page.locator('#q4 pre')).to_be_visible()
    page.get_by_role('button', name='Expand all').click()
    playwright.expect(page.locator('#q14 pre')).to_be_visible()
    page.screenshot(path=str(tmp_path / 'results.png'), full_page=True)

    page.get_by_role('link', name='Finish').click()
    page.wait_for_url('**/thank_you')
    playwright.expect(page.locator('h2')).to_have_text('All done. Thank you!')


def test_browser_withdraw_after_confirming(finished):
    client, page, db = finished
    page.goto('http://study.test/completion')
    page.get_by_text('I don’t want my responses to be used').click()
    page.get_by_role('button', name='Withdraw my responses').click()
    page.wait_for_url('**/results')
    assert db.get_collection('participants').find_one({})['data_use'] == 'withdraw'
    page.goto('http://study.test/completion')
    playwright.expect(page.locator('.end-status')).to_contain_text('You withdrew your responses')


def test_browser_invalid_draw_email_blocks_the_choice(finished):
    client, page, db = finished
    page.goto('http://study.test/completion')
    page.locator('#draw-email').fill('not-an-email')
    page.get_by_role('button', name='Yes, use my responses and see my results').click()
    # The browser refuses the form; nothing is saved and the page stays.
    assert page.locator('#draw-email').evaluate('(e) => !e.checkValidity()')
    assert page.url.endswith('/completion')
    assert db.get_collection('participants').find_one({}).get('data_use') is None
    assert db.get_collection('prize_draw_entries').docs == []


def test_browser_end_pages_fit_a_phone(finished, tmp_path):
    client, page, db = finished
    page.set_viewport_size({'width': 375, 'height': 812})
    for path in ('/completion', '/results', '/thank_you'):
        if path == '/results':
            client.post('/completion', data={'choice': 'keep'})
        page.goto('http://study.test' + path)
        assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth'), path
        page.screenshot(path=str(tmp_path / f'phone{path.replace("/", "-")}.png'), full_page=True)
