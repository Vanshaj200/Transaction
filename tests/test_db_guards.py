"""PostgreSQL-only guarantees installed by migration 0002, plus migration/model drift check.
These bypass the application code on purpose: they prove the DATABASE refuses bad data."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.factories import make_item, make_party, sale

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parent.parent


def _two_account_ids(db):
    return [r[0] for r in db.execute(text("SELECT id FROM accounts WHERE code IN ('1000','4000') ORDER BY code"))]


def test_deferred_trigger_rejects_unbalanced_entry_at_commit(db):
    cash, sales = _two_account_ids(db)
    entry_id = db.execute(text("INSERT INTO journal_entries (entry_date, description) VALUES ('2026-04-01','raw') RETURNING id")).scalar()
    db.execute(text("INSERT INTO journal_lines (entry_id, account_id, debit, credit) VALUES (:e,:a,100,0)"), {"e": entry_id, "a": cash})
    db.execute(text("INSERT INTO journal_lines (entry_id, account_id, debit, credit) VALUES (:e,:a,0,99)"), {"e": entry_id, "a": sales})
    with pytest.raises(IntegrityError, match="unbalanced"):   # fires at COMMIT, not at the INSERT
        db.commit()
    db.rollback()


def test_deferred_trigger_allows_a_balanced_entry(db):
    cash, sales = _two_account_ids(db)
    entry_id = db.execute(text("INSERT INTO journal_entries (entry_date, description) VALUES ('2026-04-01','raw') RETURNING id")).scalar()
    db.execute(text("INSERT INTO journal_lines (entry_id, account_id, debit, credit) VALUES (:e,:a,100,0)"), {"e": entry_id, "a": cash})
    db.execute(text("INSERT INTO journal_lines (entry_id, account_id, debit, credit) VALUES (:e,:a,0,100)"), {"e": entry_id, "a": sales})
    db.commit()


def test_journal_rows_cannot_be_updated_or_deleted(db):
    sale(db, make_party(db), [(make_item(db), 1)])
    for sql in ("UPDATE journal_lines SET debit = debit + 1", "DELETE FROM journal_lines",
                "UPDATE journal_entries SET description = 'tampered'", "DELETE FROM journal_entries",
                "UPDATE stock_movements SET qty_change = 0", "DELETE FROM stock_movements"):
        with pytest.raises(DBAPIError, match="not allowed"):
            db.execute(text(sql))
        db.rollback()


def test_invoices_can_never_be_deleted(db):
    sale(db, make_party(db), [(make_item(db), 1)])
    for sql in ("DELETE FROM invoices", "DELETE FROM invoice_lines"):
        with pytest.raises(DBAPIError, match="never deleted"):
            db.execute(text(sql))
        db.rollback()


def test_an_entry_can_be_reversed_only_once(db):
    from app.services import invoicing
    inv = sale(db, make_party(db), [(make_item(db), 1)])
    invoicing.cancel_invoice(db, inv.id)
    original_id = db.execute(text("SELECT id FROM journal_entries WHERE reverses_entry_id IS NULL")).scalar()
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO journal_entries (entry_date, description, invoice_id, reverses_entry_id) "
                        "VALUES ('2026-04-01','dup reversal', :i, :o)"), {"i": inv.id, "o": original_id})
        db.flush()
    db.rollback()


def test_models_and_migrations_have_not_drifted(engine):
    env = {**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    out = subprocess.run([sys.executable, "-m", "alembic", "check"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
