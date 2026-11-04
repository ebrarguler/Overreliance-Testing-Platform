import sys
from pathlib import Path

# Make the project root importable as `app.*` regardless of invocation
# directory, matching scripts/export_data.py's own defensive sys.path setup.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from tests.fake_mongo import FakeDB


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    """Never let route tests access an actual research database."""
    fake = FakeDB()
    monkeypatch.setattr("app.utils.db.get_collection", fake.get_collection)
    monkeypatch.setattr("app.utils.participants.get_collection", fake.get_collection)
    monkeypatch.setattr("app.utils.assignment.get_collection", fake.get_collection)
    return fake
