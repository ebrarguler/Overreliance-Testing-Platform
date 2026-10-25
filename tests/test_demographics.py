"""
Demographics page (app/routes.py collect_demographics): one study-level list
for every site, and each site's own grade scale stored with the answer.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

from app.routes import main_bp
from app.utils.demographics import GRADE_SCALES, STUDY_LEVEL_OPTIONS
from tests.fake_mongo import FakeDB

UF_FORM = {
    "email": "gator@ufl.edu",
    "id_number": "12345678",
    "firstName": "Alex",
    "lastName": "Gator",
    "classSchool": "COP3502",
}


@pytest.fixture
def client():
    fake = FakeDB()
    with patch("app.utils.participants.get_collection", fake.get_collection), patch(
        "app.utils.assignment.get_collection", fake.get_collection
    ), patch("app.utils.db.get_collection", fake.get_collection):
        app = Flask(
            __name__,
            template_folder=str(Path(__file__).resolve().parent.parent / "app" / "templates"),
        )
        app.secret_key = "test-secret"
        app.register_blueprint(main_bp)
        yield app.test_client()


def _enter(client, site):
    client.get(f"/start/{site}")
    if site == "uf":
        client.post("/validate_email", data=UF_FORM)
    client.post("/consent", data={"consent": "on", "prior_participation": "no"})


def _demographics(client):
    with client.session_transaction() as sess:
        return sess.get("demographics")


@pytest.mark.parametrize("site, scale, absent", [
    ("uzh", "ch_6", "3.75 - 4.00"),
    ("uf", "us_gpa_4", "No grades yet"),
])
def test_each_site_shows_its_own_grade_scale(client, site, scale, absent):
    _enter(client, site)
    html = client.get("/demographics").get_data(as_text=True)

    assert GRADE_SCALES[scale]["label"] in html
    for label in GRADE_SCALES[scale]["options"].values():
        assert label in html
    assert absent not in html


@pytest.mark.parametrize("site", ["uzh", "uf"])
def test_every_site_uses_the_same_study_levels(client, site):
    _enter(client, site)
    html = client.get("/demographics").get_data(as_text=True)

    assert "Year in College" not in html
    for label in STUDY_LEVEL_OPTIONS.values():
        assert label.replace("'", "&#39;") in html


def test_uzh_answer_is_stored_with_the_swiss_scale(client):
    _enter(client, "uzh")
    resp = client.post(
        "/demographics", data={"age": "22", "study_level": "master", "gpa": "5.5-6.0"}
    )

    assert resp.headers["Location"].endswith("/pre_survey")
    stored = _demographics(client)
    assert stored["academic_level"] == "master"
    assert (stored["gpa"], stored["gpa_scale"]) == ("5.5-6.0", "ch_6")


def test_uf_answer_is_stored_with_the_us_scale(client):
    _enter(client, "uf")
    client.post("/demographics", data={"age": "19", "study_level": "ba1", "gpa": "3.75"})

    stored = _demographics(client)
    assert (stored["gpa"], stored["gpa_scale"]) == ("3.75", "us_gpa_4")


@pytest.mark.parametrize("form", [
    {"study_level": "ba1", "gpa": "3.75"},          # a US GPA band at UZH
    {"study_level": "2", "gpa": "5.0-5.49"},        # an old "year in college" value
    {"gpa": "5.0-5.49"},                            # study level missing
])
def test_answers_outside_the_site_options_are_rejected(client, form):
    _enter(client, "uzh")
    resp = client.post("/demographics", data={"age": "22", **form})

    assert resp.status_code == 400
    assert "Please choose your level of study" in resp.get_data(as_text=True)
    assert not _demographics(client)
