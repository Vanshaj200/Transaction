"""Database-level guarantees: constraints must reject bad rows even if the app code is bypassed."""
import pytest
from sqlalchemy.exc import IntegrityError

from datetime import date

from app.models import Account, Invoice, Item, JournalEntry, JournalLine, Party


def _party(db):
    p = Party(name="Acme", party_type="customer", state_code="27")
    db.add(p)
    db.flush()
    return p


def test_negative_stock_rejected(db):
    db.add(Item(name="Pen", hsn="9608", gst_rate_bp=1800, selling_price=1000, stock_qty=-1))
    with pytest.raises(IntegrityError):
        db.flush()


def test_party_type_must_be_valid(db):
    db.add(Party(name="X", party_type="alien", state_code="27"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_invoice_totals_must_add_up(db):
    p = _party(db)
    db.add(Invoice(
        invoice_no="T-1", invoice_type="sale", party_id=p.id, invoice_date=date(2026, 4, 1),
        place_of_supply="27", supply_type="intra", taxable_total=100, cgst_total=9, sgst_total=9,
        igst_total=0, grand_total=999,  # should be 118
    ))
    with pytest.raises(IntegrityError):
        db.flush()


def test_intra_state_invoice_cannot_carry_igst(db):
    p = _party(db)
    db.add(Invoice(
        invoice_no="T-2", invoice_type="sale", party_id=p.id, invoice_date=date(2026, 4, 1),
        place_of_supply="27", supply_type="intra", taxable_total=100, cgst_total=0, sgst_total=0,
        igst_total=18, grand_total=118,
    ))
    with pytest.raises(IntegrityError):
        db.flush()


def test_journal_line_must_be_exactly_one_sided(db):
    acc = Account(code="9999", name="Test acct", account_type="asset", is_system=False)
    entry = JournalEntry(entry_date=date(2026, 4, 1), description="t")
    db.add_all([acc, entry])
    db.flush()
    db.add(JournalLine(entry_id=entry.id, account_id=acc.id, debit=10, credit=10))
    with pytest.raises(IntegrityError):
        db.flush()
