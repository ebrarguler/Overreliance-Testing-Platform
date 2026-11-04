"""Explicit helpers for the position-bound trial API and persisted research state."""
from app.utils import trial_store


def research(sess):
    state = dict(sess)
    pid = state.get("participant_id")
    if pid:
        state.update(trial_store.assemble(pid, state.get("question_order", [])))
        state["chat_history"] = trial_store.history_for(pid)
    return state


def trial_get(client, path, **kwargs):
    with client.session_transaction() as sess:
        position = sess.get("question_index", 0)
    kwargs.setdefault("query_string", {"position": position})
    return client.get(path, **kwargs)


def trial_post(client, path, **kwargs):
    with client.session_transaction() as sess:
        position = sess.get("question_index", 0)
    field = "json" if "json" in kwargs else "data"
    kwargs[field] = {"position": position, **kwargs.get(field, {})}
    return client.post(path, **kwargs)


def complete_trials(sess):
    """Seed completed trials for tests whose subject is final submission."""
    from app.utils.questions import questions
    for position, q in enumerate(sess["question_order"]):
        trial_store.record_answer(sess["participant_id"], position, {
            "question_index": q, "position": position + 1, "answer": questions[q]["correct"],
        })
        trial_store.record_rating(sess["participant_id"], position, {
            "question_index": q, "position": position + 1, "confidence": "4", "trust": "4", "helpfulness": "4",
        })


def prepare_session(sess):
    from app.utils.pre_survey import PRE_SURVEY_PAGES, page_likert_names
    sess['site'] = 'uzh'
    sess['consented'] = True
    sess['demographics'] = {'age_category': '21'}
    sess['pre_survey_data'] = {name: '4' for page in PRE_SURVEY_PAGES for name in page_likert_names(page)}
