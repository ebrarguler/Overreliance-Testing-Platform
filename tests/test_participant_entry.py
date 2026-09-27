"""
Tests for email-based entry, site detection, and participant identity
(app/routes.py index/validate_email/consent/final_survey,
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


class FakeCollection:
    def __init__(self):
        self.docs = []
        self.unique = {}  # index name -> field

    def _matches(self, doc, query):
        return all(doc.get(k) == v for k, v in query.items())

    def create_index(self, field, unique=False, partialFilterExpression=None):
        if unique:
            self.unique[f"{field}_1"] = field

    def index_information(self):
        return {name: {} for name in self.unique}

    def drop_index(self, name):
        del self.unique[name]

    def _check_unique(self, doc):
        for field in self.unique.values():
            if field in doc and any(d.get(field) == doc[field] for d in self.docs):
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


def _enter(client, email):
    client.get("/")
    return client.post("/validate_email", data={"email": email})


def _finish(client):
    with client.session_transaction() as sess:
        sess["answers"] = []
    return client.post("/final_survey", data={})


def test_entry_form_asks_only_for_email(client):
    html = client.get("/").get_data(as_text=True)
    assert 'name="email"' in html
    assert "@uzh.ch" in html and "@ncsu.edu" in html
    for removed in ("id_number", "firstName", "lastName", "classSchool"):
        assert removed not in html


@pytest.mark.parametrize(
    "email,site",
    [
        ("  Student@UZH.ch ", "uzh"),
        ("someone@ifi.uzh.ch", "uzh"),
        ("wolf@NCSU.edu", "ncsu"),
    ],
)
def test_site_is_derived_from_email_domain(client, db, email, site):
    resp = _enter(client, email)
    assert resp.headers["Location"].endswith("/consent")

    (record,) = db["participants"].docs
    assert record["email"] == email.strip().lower()
    assert record["site"] == site
    with client.session_transaction() as sess:
        assert sess["participant_id"] == record["participant_id"]
        assert sess["site"] == site


@pytest.mark.parametrize(
    "email", ["x@gmail.com", "x@evil-uzh.ch", "x@uzh.ch.example.com", "not-an-email", ""]
)
def test_other_domains_are_rejected(client, db, email):
    assert _enter(client, email).headers["Location"].endswith("/email_error")
    assert not db.get("participants") or not db["participants"].docs


def test_consent_shows_site_specific_study_information(client):
    _enter(client, "p@uzh.ch")
    assert "University of Zurich" in client.get("/consent").get_data(as_text=True)

    _enter(client, "p@ncsu.edu")
    html = client.get("/consent").get_data(as_text=True)
    assert "University of Zurich" not in html
    assert "NCSU" in html


def test_consent_requires_an_entered_email(client):
    client.get("/")
    assert client.get("/consent").headers["Location"].endswith("/")


def test_email_is_kept_out_of_research_collections(client, db):
    _enter(client, "p@uzh.ch")
    _finish(client)

    (assignment,) = db["participant_assignments"].docs
    (response,) = db["users"].docs
    for doc in (assignment, response):
        assert "email" not in doc
        assert "p@uzh.ch" not in repr(doc)
    assert response["site"] == "uzh"
    assert response["participant_id"] == db["participants"].docs[0]["participant_id"]


def test_returning_unfinished_email_resumes_same_assignment(client, db):
    _enter(client, "p@uzh.ch")
    with client.session_transaction() as sess:
        first = (sess["participant_id"], sess["question_order"], sess["sequence_label"])

    _enter(client, " P@UZH.CH")
    with client.session_transaction() as sess:
        again = (sess["participant_id"], sess["question_order"], sess["sequence_label"])

    assert again == first
    assert len(db["participants"].docs) == 1
    assert len(db["participant_assignments"].docs) == 1


def test_completed_email_cannot_start_again(client, db):
    _enter(client, "p@uzh.ch")
    _finish(client)
    assert db["participants"].docs[0]["completed_at"] is not None

    resp = _enter(client, "P@uzh.ch ")
    assert resp.headers["Location"].endswith("/thank_you")
    assert len(db["users"].docs) == 1


def test_resubmitting_final_survey_does_not_duplicate_response(client, db):
    _enter(client, "p@uzh.ch")
    _finish(client)
    _finish(client)
    assert len(db["users"].docs) == 1


def test_legacy_email_index_on_assignments_is_dropped(client, db):
    legacy = db.setdefault("participant_assignments", FakeCollection())
    legacy.create_index("email", unique=True)
    _enter(client, "a@uzh.ch")
    _enter(client, "b@uzh.ch")
    assert len(legacy.docs) == 2
