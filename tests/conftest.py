"""Test isolation: some tests call db.reset(tmp_path/...) which repoints the module-global
engine. Restore it after every test so later files see the real data/nwis.sqlite again."""
import pytest

from nwis import db


@pytest.fixture(autouse=True)
def _restore_db_engine():
    yield
    db._engine = None
