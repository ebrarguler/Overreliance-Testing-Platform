"""
Tests for site entry links, the first-participation question on the consent
page, and the browser-cookie repeat check (app/routes.py start/consent/final_survey,
app/utils/participants.py, app/utils/sites.py).

MongoDB is replaced by a small in-memory fake that supports only the
operations these code paths use, including unique-index enforcement, so the
duplicate/resume behaviour is exercised without a database.
"""

import copy
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask
from pymongo.errors import DuplicateKeyError

from app.routes import main_bp
from app.utils.participants import PARTICIPANT_COOKIE, PRIOR_PARTICIPATION_OPTIONS


_MISSING = object()


class FakeCollection:
    def __init__(self):
        self.docs = []
        self.unique = {}  # index name -> field

    @staticmethod
    def _get(doc, dotted):
        for part in dotted.split("."):
            if not isinstance(doc, dict) or part not in doc:
                return _MISSING
            doc = doc[part]
        return doc

    def _matches(self, doc, query):
        return all(self._get(doc, k) == v for k, v in query.items())

    def create_index(self, field, unique=False, name=None, partialFilterExpression=None):
        if unique:
            self.unique[name or f"{field}_1"] = field

    def index_information(self):
        return {name: {} for name in self.unique}

    def drop_index(self, name):
        del self.unique[name]

    def _check_unique(self, doc):
        for field in self.unique.values():
            value = self._get(doc, field)
            if value is not _MISSING and any(self._get(d, field) == value for d in self.docs):
                raise DuplicateKeyError(f"dup {field}")

    def find_one(self, query):
        for d in self.docs:
            if self._matches(d, query):
                return copy.deepcopy(d)
        return None

    def find(self, query=None):
        return [copy.deepcopy(d) for d in self.docs if self._matches(d, query or {})]

    def insert_one(self, doc):
        self._check_unique(doc)
        self.docs.append(copy.deepcopy(doc))

    def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if self._matches(d, query):
                d.update(update.get("$set", {}))
                for k, v in update.get("$inc", {}).items():
                    d[k] = d.get(k, 0) + v
                return type("R", (), {"upserted_id": None})()
        if upsert:
            doc = {**query, **update.get("$setOnInsert", {}), **update.get("$set", {})}
            self.insert_one(doc)
            return type("R", (), {"upserted_id": "new"})()
        return type("R", (), {"upserted_id": None})()

    def find_one_and_update(self, query, update, sort, return_document):
        (field, _), (tie, _) = sort
        doc = sorted(self.docs, key=lambda d: (d[field], d[tie]))[0]
        for k, v in update["$inc"].items():
            doc[k] += v
        return copy.deepcopy(doc)


@pytest.fixture
def db():
    collections = {}

    def get_collection(name):
        return collections.setdefault(name, FakeCollection())

    with patch("app.utils.participants.get_collection", get_collection), patch(
        "app.utils.assignment.get_collection", get_collection
    ), patch("app.utils.db.get_collection", get_collection):
        yield collections


@pytest.fixture
def client(db):
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent.parent / "app" / "templates"),
    )
    app.secret_key = "test-secret"
    app.register_blueprint(main_bp)
    return app.test_client()


UF_FORM = {
    "email": "  Gator@UFL.edu ",
    "id_number": "12345678",
    "firstName": "Alex",
    "lastName": "Gator",
    "classSchool": "COP3502",
}


def _enter(client, site="uzh", prior="no", form=None):
    client.get(f"/start/{site}")
    if site == "uf":
        resp = client.post("/validate_email", data=form or UF_FORM)
        if not resp.headers["Location"].endswith("/consent"):
            return resp
    return client.post("/consent", data={"consent": "on", "prior_participation": prior})


def _finish(client):
    with client.session_transaction() as sess:
        sess["answers"] = []
    return client.post("/final_survey", data={})


def _new_browser(client):
    """Same person, fresh browser state: no participant cookie, no session."""
    client.delete_cookie(PARTICIPANT_COOKIE)
    client.delete_cookie("session")


IDENTITY_FIELDS = ('name="email"', 'name="id_number"', 'name="firstName"',
                   'name="lastName"', 'name="classSchool"')


def test_uzh_start_page_asks_no_personal_data(client):
    html = client.get("/start/uzh").get_data(as_text=True)
    for field in IDENTITY_FIELDS + ('name="prior_participation"',):
        assert field not in html


def test_uf_start_page_is_the_main_branch_form(client):
    html = client.get("/start/uf").get_data(as_text=True)
    for field in IDENTITY_FIELDS:
        assert field in html
    assert "UFID" in html
    assert "IRB Protocol #ET00044243" in html


def test_site_specific_study_information(client):
    assert "University of Zurich" in client.get("/start/uzh").get_data(as_text=True)
    assert "University of Zurich" not in client.get("/start/uf").get_data(as_text=True)


def test_unknown_site_and_bare_root_do_not_start_the_study(client, db):
    assert client.get("/start/elsewhere").status_code == 404
    assert "link you received" in client.get("/").get_data(as_text=True)
    resp = client.post("/consent", data={"consent": "on", "prior_participation": "no"})
    assert resp.headers["Location"].endswith("/")
    assert not db.get("participants") or not db["participants"].docs


def test_question_is_on_every_consent_page(client):
    for site in ("uzh", "uf"):
        client.get(f"/start/{site}")
        if site == "uf":
            client.post("/validate_email", data=UF_FORM)
        html = client.get("/consent").get_data(as_text=True)
        assert 'name="prior_participation"' in html
        for label in PRIOR_PARTICIPATION_OPTIONS.values():
            assert label in html


def test_nothing_is_stored_before_consent(client, db):
    client.get("/start/uzh")
    client.get("/consent")
    assert client.get_cookie(PARTICIPANT_COOKIE) is None
    assert not db.get("participants") or not db["participants"].docs


def test_declining_stores_nothing(client, db):
    client.get("/start/uzh")
    resp = client.post("/consent", data={"prior_participation": "no"})
    assert resp.headers["Location"].endswith("/declined")
    assert not db.get("participants") or not db["participants"].docs
    assert not db.get("sequence_counts")


def test_prior_participation_answer_is_required(client, db):
    client.get("/start/uzh")
    resp = client.post("/consent", data={"consent": "on"})
    assert resp.status_code == 400
    assert not db.get("participants") or not db["participants"].docs


@pytest.mark.parametrize(
    "prior,flag", [("no", False), ("started", True), ("completed", True)]
)
def test_every_answer_continues_and_is_recorded(client, db, prior, flag):
    resp = _enter(client, "uf", prior)
    assert resp.headers["Location"].endswith("/demographics")

    (record,) = db["participants"].docs
    assert record["site"] == "uf"
    assert record["prior_participation"] == prior
    assert record["participated_before"] is flag
    assert client.get_cookie(PARTICIPANT_COOKIE).value == record["participant_id"]

    _finish(client)
    (response,) = db["users"].docs
    assert response["prior_participation"] == prior
    assert response["participated_before"] is flag
    assert response["site"] == "uf"
    assert response["participant_id"] == record["participant_id"]


def test_no_personal_data_is_stored_for_uzh(client, db):
    _enter(client)
    _finish(client)
    for name in ("participants", "participant_assignments", "users"):
        for doc in db[name].docs:
            for field in ("email", "firstName", "lastName", "uf_id", "classSchool"):
                assert field not in doc


def test_same_browser_resumes_where_it_left_off(client, db):
    _enter(client)
    client.post("/demographics", data={"age": "18-24"})
    with client.session_transaction() as sess:
        first = (sess["participant_id"], sess["question_order"])

    resp = client.get("/start/uzh")
    assert resp.headers["Location"].endswith("/pre_survey")
    with client.session_transaction() as sess:
        assert (sess["participant_id"], sess["question_order"]) == first
    assert len(db["participants"].docs) == 1


def test_expired_session_keeps_participant_and_assignment(client, db):
    _enter(client, prior="started")
    with client.session_transaction() as sess:
        first = (sess["participant_id"], sess["question_order"], sess["sequence_label"])
    client.delete_cookie("session")  # session lost, participant cookie kept

    resp = client.get("/start/uzh")
    assert resp.headers["Location"].endswith("/demographics")
    with client.session_transaction() as sess:
        assert (sess["participant_id"], sess["question_order"], sess["sequence_label"]) == first
        assert sess["prior_participation"] == "started"
    assert len(db["participants"].docs) == 1
    assert len(db["participant_assignments"].docs) == 1


def test_completed_browser_is_turned_away(client, db):
    _enter(client)
    _finish(client)
    assert db["participants"].docs[0]["completed_at"] is not None

    for url in ("/start/uzh", "/start/uf"):
        html = client.get(url).get_data(as_text=True)
        assert "already been completed" in html
    assert len(db["participants"].docs) == 1


def test_new_browser_gets_a_new_participant(client, db):
    _enter(client)
    _finish(client)
    _new_browser(client)
    assert _enter(client, prior="completed").headers["Location"].endswith("/demographics")
    assert len(db["participants"].docs) == 2


def test_resubmitting_final_survey_does_not_duplicate_response(client, db):
    _enter(client)
    _finish(client)
    _finish(client)
    assert len(db["users"].docs) == 1


def test_back_to_consent_after_agreeing_does_not_create_second_participant(client, db):
    _enter(client)
    assert client.get("/consent").status_code == 302
    client.post("/consent", data={"consent": "on", "prior_participation": "no"})
    assert len(db["participants"].docs) == 1


def test_legacy_unique_email_indexes_are_dropped(client, db):
    for name in ("participants", "participant_assignments"):
        db.setdefault(name, FakeCollection()).create_index("email", unique=True)
    _enter(client)
    _new_browser(client)
    _enter(client)
    assert len(db["participants"].docs) == 2
    assert len(db["participant_assignments"].docs) == 2


def test_each_site_gets_its_own_consent_page(client):
    client.get("/start/uzh")
    uzh = client.get("/consent").get_data(as_text=True)
    assert "Informed Consent" in uzh
    assert "SONA" not in uzh

    client.get("/start/uf")
    client.post("/validate_email", data=UF_FORM)
    uf = client.get("/consent").get_data(as_text=True)
    assert "Informed Consent" in uf
    assert "SONA" in uf


def test_unconsented_session_with_participant_does_not_loop(client, db):
    """Sessions from the earlier flow created the participant before consent."""
    client.get("/start/uzh")
    with client.session_transaction() as sess:
        sess["participant_id"] = "legacy-id"

    resp = client.get("/consent")
    assert resp.status_code == 200
    assert 'name="prior_participation"' in resp.get_data(as_text=True)

    resp = client.post("/consent", data={"consent": "on", "prior_participation": "no"})
    assert resp.headers["Location"].endswith("/demographics")
    assert client.get("/start/uzh").headers["Location"].endswith("/demographics")


# --- UF: start-page identity form -------------------------------------------


def test_uf_identity_is_stored_only_in_participants(client, db):
    _enter(client, "uf")
    _finish(client)

    (record,) = db["participants"].docs
    assert record["identity"] == {
        "email": "gator@ufl.edu",
        "uf_id": "12345678",
        "firstName": "Alex",
        "lastName": "Gator",
        "classSchool": "COP3502",
    }
    for name in ("participant_assignments", "users"):
        for doc in db[name].docs:
            assert "identity" not in doc
            assert "gator@ufl.edu" not in repr(doc)
            assert "Alex" not in repr(doc)
    with client.session_transaction() as sess:
        assert "identity" not in sess


def test_uf_identity_is_not_stored_before_consent(client, db):
    client.get("/start/uf")
    client.post("/validate_email", data=UF_FORM)
    assert not db.get("participants") or not db["participants"].docs
    resp = client.post("/consent", data={"prior_participation": "no"})  # decline
    assert resp.headers["Location"].endswith("/declined")
    assert not db.get("participants") or not db["participants"].docs


def test_uf_consent_requires_the_start_form(client):
    client.get("/start/uf")
    assert client.get("/consent").headers["Location"].endswith("/start/uf")


@pytest.mark.parametrize("email", ["x@gmail.com", "x@evil-ufl.edu", "not-an-email", ""])
def test_uf_rejects_non_ufl_emails(client, db, email):
    resp = _enter(client, "uf", form={**UF_FORM, "email": email})
    assert resp.headers["Location"].endswith("/email_error")
    assert "not a correct UF email" in client.get("/email_error").get_data(as_text=True)


def test_uf_completed_email_is_turned_away_in_any_browser(client, db):
    _enter(client, "uf")
    _finish(client)
    _new_browser(client)

    resp = _enter(client, "uf", form={**UF_FORM, "email": "GATOR@ufl.edu"})
    assert resp.headers["Location"].endswith("/thank_you")
    assert len(db["participants"].docs) == 1


def test_uf_unfinished_email_resumes_in_another_browser(client, db):
    _enter(client, "uf", prior="started")
    with client.session_transaction() as sess:
        first = (sess["participant_id"], sess["question_order"], sess["sequence_label"])
    _new_browser(client)

    resp = _enter(client, "uf", form={**UF_FORM, "email": "gator@ufl.edu "})
    assert resp.headers["Location"].endswith("/demographics")
    with client.session_transaction() as sess:
        assert (sess["participant_id"], sess["question_order"], sess["sequence_label"]) == first
        assert sess["prior_participation"] == "started"
    assert client.get_cookie(PARTICIPANT_COOKIE).value == first[0]
    assert len(db["participants"].docs) == 1


def test_uzh_records_do_not_collide_on_missing_email(client, db):
    for _ in range(3):
        _enter(client)
        _new_browser(client)
    _enter(client, "uf")
    assert len(db["participants"].docs) == 4
