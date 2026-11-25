"""Test harness.

* Default database: a throw-away SQLite file (fast, no setup).
* TEST_DATABASE_URL=postgresql+psycopg://... : the real thing. The schema is then built by running
  the Alembic migrations (so the migrations themselves are tested), and the tests that need row
  locks / triggers / real concurrency (marked `postgres`) run instead of being skipped.
"""
import argparse
import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.database import make_engine
from app.models import Base

ROOT = Path(__file__).resolve().parent.parent
TEST_URL = os.environ.get("TEST_DATABASE_URL")
IS_PG = bool(TEST_URL and TEST_URL.startswith("postgresql"))


def pytest_report_header(config):
    return f"billbook test database: {'PostgreSQL' if IS_PG else 'SQLite (postgres-only tests will be skipped)'}"


def pytest_collection_modifyitems(config, items):
    if IS_PG:
        return
    skip = pytest.mark.skip(reason="needs PostgreSQL: set TEST_DATABASE_URL")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)


def _alembic_upgrade(url: str) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.cmd_opts = argparse.Namespace(x=[f"url={url}"])  # read by alembic/env.py
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def engine():
    if IS_PG:
        eng = make_engine(TEST_URL, pool_size=20, max_overflow=200)
        with eng.begin() as conn:  # start from an empty schema every run
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        _alembic_upgrade(TEST_URL)
    else:
        path = Path(tempfile.mkdtemp()) / "test.sqlite3"
        eng = make_engine(f"sqlite:///{path}")
        Base.metadata.create_all(eng)
        from app.services.accounts import seed_system_accounts  # noqa: PLC0415

        with sessionmaker(eng)() as s:
            seed_system_accounts(s)
            s.commit()
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _clean_db(engine):
    """Wipe all data after each test. The chart of accounts is kept (it is reference data)."""
    yield
    tables = [t.name for t in reversed(Base.metadata.sorted_tables) if t.name != "accounts"]
    with engine.begin() as conn:
        if IS_PG:
            conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
        else:
            for t in tables:
                conn.execute(text(f"DELETE FROM {t}"))
        conn.execute(text("DELETE FROM accounts WHERE is_system = false"))


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session
