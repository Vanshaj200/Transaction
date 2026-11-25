"""Double-entry posting: balance rule, posting shapes, atomic rollback, cancellation reversal."""
import random

import pytest
from sqlalchemy import func, select

from app.errors import Conflict, InsufficientStock, UnbalancedEntry
from app.models import Invoice, Item, JournalEntry, StockMovement
from app.services import invoicing, ledger
from app.services.ledger import PostingLine
from tests.factories import make_item, make_party, purchase, sale
from tests.helpers import balance, entries_for, lines_of, totals, unbalanced_entries


# ------------------------------------------------------------------ rule 4: debits == credits
@pytest.mark.parametrize("lines", [
    [PostingLine("1000", debit=100), PostingLine("4000", credit=99)],          # off by one paisa
    [PostingLine("1000", debit=100)],                                         # single line
    [],                                                                       # empty
    [PostingLine("1000", debit=100, credit=100), PostingLine("4000", credit=0, debit=0)],  # both sides / empty line
    [PostingLine("1000", debit=-5), PostingLine("4000", credit=-5)],          # negative
])
def test_post_entry_rejects_bad_entries_and_writes_nothing(db, lines):
    with pytest.raises(UnbalancedEntry):
        ledger.post_entry(db, entry_date="2026-04-01", description="bad", lines=lines)
    assert db.scalar(select(func.count(JournalEntry.id))) == 0


def test_post_entry_accepts_balanced_multi_line_entry(db):
    entry = ledger.post_entry(db, entry_date=__import__("datetime").date(2026, 4, 1), description="ok", lines=[
        PostingLine("1100", debit=118), PostingLine("4000", credit=100),
        PostingLine("2100", credit=9), PostingLine("2110", credit=9),
    ])
    db.commit()
    assert lines_of(db, entry.id) == {"1100": (118, 0), "4000": (0, 100), "2100": (0, 9), "2110": (0, 9)}


def test_missing_account_is_a_ledger_error(db):
    from app.errors import LedgerError
    import datetime
    with pytest.raises(LedgerError):
        ledger.post_entry(db, entry_date=datetime.date(2026, 4, 1), description="x",
                          lines=[PostingLine("0000", debit=1), PostingLine("4000", credit=1)])


# ------------------------------------------------------------------ posting shapes
def test_intra_state_sale_posts_cgst_sgst(db):
    inv = sale(db, make_party(db, state_code="27"), [(make_item(db, price=10_000), 1)])
    (entry,) = entries_for(db, inv.id)
    assert lines_of(db, entry.id) == {
        "1100": (11_800, 0),   # Dr Receivables (grand total)
        "4000": (0, 10_000),   # Cr Sales (taxable)
        "2100": (0, 900),      # Cr CGST payable
        "2110": (0, 900),      # Cr SGST payable
    }


def test_inter_state_sale_posts_igst(db):
    inv = sale(db, make_party(db, state_code="29"), [(make_item(db, price=10_000), 1)])
    (entry,) = entries_for(db, inv.id)
    assert lines_of(db, entry.id) == {"1100": (11_800, 0), "4000": (0, 10_000), "2120": (0, 1_800)}


def test_purchase_posts_input_tax_and_payable(db):
    inv = purchase(db, make_party(db, "supplier", state_code="27"), [(make_item(db, stock=0), 10, 5_000)])
    (entry,) = entries_for(db, inv.id)
    assert lines_of(db, entry.id) == {
        "5000": (50_000, 0), "1200": (4_500, 0), "1210": (4_500, 0), "2000": (0, 59_000),
    }


def test_zero_rated_sale_has_no_tax_lines(db):
    inv = sale(db, make_party(db), [(make_item(db, price=5_000, rate_bp=0), 2)])
    (entry,) = entries_for(db, inv.id)
    assert lines_of(db, entry.id) == {"1100": (10_000, 0), "4000": (0, 10_000)}


def test_zero_value_invoice_rejected(db):
    from app.errors import InvalidInput
    with pytest.raises(InvalidInput):
        sale(db, make_party(db), [(make_item(db, price=0), 1)])


# ------------------------------------------------------------------ ledger always balances
def test_ledger_balances_after_many_random_invoices_and_cancels(db):
    rng = random.Random(7)
    customers = [make_party(db, state_code=rng.choice(["27", "29", "07"])) for _ in range(4)]
    suppliers = [make_party(db, "supplier", state_code=rng.choice(["27", "24"])) for _ in range(2)]
    items = [make_item(db, stock=0, rate_bp=rng.choice([0, 500, 1200, 1800, 2800]), price=rng.randint(1, 99_999)) for _ in range(5)]
    for it in items:
        purchase(db, suppliers[0], [(it, 500, rng.randint(100, 50_000))])
    made = []
    for _ in range(60):
        made.append(sale(db, rng.choice(customers), [(rng.choice(items), rng.randint(1, 5)) for _ in range(rng.randint(1, 3))]))
    for inv in rng.sample(made, 10):
        invoicing.cancel_invoice(db, inv.id, "test")
    d, c = totals(db)
    assert d == c and d > 0
    assert unbalanced_entries(db) == []                     # every single entry balances, not just the total


def test_trial_balance_nets_to_zero_with_expected_account_balances(db):
    inv = sale(db, make_party(db, state_code="29"), [(make_item(db, price=10_000), 3)])  # taxable 30000, igst 5400
    purchase(db, make_party(db, "supplier"), [(make_item(db, stock=0), 4, 2_500)])        # taxable 10000, cgst/sgst 900 each
    assert balance(db, "1100") == inv.grand_total == 35_400    # receivable
    assert balance(db, "4000") == -30_000                       # sales (credit)
    assert balance(db, "2120") == -5_400
    assert balance(db, "5000") == 10_000
    assert balance(db, "2000") == -11_800
    d, c = totals(db)
    assert d - c == 0


# ------------------------------------------------------------------ rule 2: atomicity / rollback
def _counts(db):
    db.expire_all()
    return (
        db.scalar(select(func.count(Invoice.id))), db.scalar(select(func.count(StockMovement.id))),
        db.scalar(select(func.count(JournalEntry.id))),
    )


def test_failure_while_posting_journal_rolls_back_invoice_and_stock(db, monkeypatch):
    party, item = make_party(db), make_item(db, stock=10)
    before = _counts(db)

    def boom(*_a, **_k):
        raise RuntimeError("database fell over while posting")

    monkeypatch.setattr(ledger, "post_invoice", boom)
    with pytest.raises(RuntimeError):
        sale(db, party, [(item, 4)])          # invoice + stock movement were already flushed when this blew up
    assert _counts(db) == before
    assert db.get(Item, item.id).stock_qty == 10


def test_unbalanced_posting_aborts_whole_invoice(db, monkeypatch):
    party, item = make_party(db), make_item(db, stock=10)
    before = _counts(db)
    monkeypatch.setattr(ledger, "invoice_posting_lines", lambda inv: [PostingLine("1100", debit=1), PostingLine("4000", credit=2)])
    with pytest.raises(UnbalancedEntry):
        sale(db, party, [(item, 4)])
    assert _counts(db) == before and db.get(Item, item.id).stock_qty == 10


def test_failed_request_over_http_leaves_no_trace(client, headers, db, monkeypatch):
    party, item = make_party(db), make_item(db, stock=10)
    before = _counts(db)
    monkeypatch.setattr(ledger, "post_invoice", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    c = client.__class__(client.app, raise_server_exceptions=False)
    r = c.post("/invoices", headers=headers("accountant"), json={
        "invoice_type": "sale", "party_id": party.id, "lines": [{"item_id": item.id, "quantity": 1}]})
    assert r.status_code == 500 and _counts(db) == before


# ------------------------------------------------------------------ rule 5: cancel = reverse
def test_cancel_sale_posts_exact_reversal_and_restores_stock(db):
    party, item = make_party(db), make_item(db, stock=10, price=10_000)
    inv = sale(db, party, [(item, 4)])
    assert balance(db, "1100") == 47_200
    invoicing.cancel_invoice(db, inv.id, "customer returned goods")
    db.expire_all()
    inv = db.get(Invoice, inv.id)
    assert inv.status == "cancelled" and inv.cancel_reason == "customer returned goods" and inv.cancelled_at
    assert db.get(Item, item.id).stock_qty == 10
    original, reversal = entries_for(db, inv.id)
    assert reversal.reverses_entry_id == original.id
    orig, rev = lines_of(db, original.id), lines_of(db, reversal.id)
    assert rev == {code: (c, d) for code, (d, c) in orig.items()}   # debits and credits swapped
    assert all(balance(db, code) == 0 for code in orig)             # every account back to zero
    assert db.scalar(select(func.count(Invoice.id))) == 1           # the invoice row still exists


def test_cancel_writes_reversing_stock_movement(db):
    party, item = make_party(db), make_item(db, stock=10)
    inv = sale(db, party, [(item, 4)])
    invoicing.cancel_invoice(db, inv.id)
    rows = db.execute(select(StockMovement.qty_change, StockMovement.reason).where(StockMovement.invoice_id == inv.id).order_by(StockMovement.id)).all()
    assert rows == [(-4, "sale"), (4, "sale_cancel")]


def test_cannot_cancel_twice(db):
    inv = sale(db, make_party(db), [(make_item(db), 1)])
    invoicing.cancel_invoice(db, inv.id)
    with pytest.raises(Conflict):
        invoicing.cancel_invoice(db, inv.id)
    assert len(entries_for(db, inv.id)) == 2            # still just original + one reversal


def test_cancel_purchase_blocked_when_stock_already_sold(db):
    supplier, customer, item = make_party(db, "supplier"), make_party(db), make_item(db, stock=0)
    pur = purchase(db, supplier, [(item, 10, 1_000)])
    sale(db, customer, [(item, 8)])
    with pytest.raises(Conflict, match="Cannot cancel purchase"):
        invoicing.cancel_invoice(db, pur.id)
    db.expire_all()
    assert db.get(Invoice, pur.id).status == "posted" and db.get(Item, item.id).stock_qty == 2


def test_cancel_purchase_reverses_stock_and_ledger(db):
    supplier, item = make_party(db, "supplier"), make_item(db, stock=0)
    pur = purchase(db, supplier, [(item, 10, 1_000)])
    invoicing.cancel_invoice(db, pur.id)
    assert db.get(Item, item.id).stock_qty == 0
    assert balance(db, "5000") == 0 and balance(db, "2000") == 0


def test_cancel_via_api(client, headers, db):
    inv = sale(db, make_party(db), [(make_item(db), 1)])
    acc = headers("accountant")
    r = client.post(f"/invoices/{inv.id}/cancel", json={"reason": "typo"}, headers=acc)
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["balance_due"] == 0
    assert client.post(f"/invoices/{inv.id}/cancel", headers=acc).status_code == 409
    assert client.post(f"/invoices/{inv.id}/cancel", headers=headers("viewer")).status_code == 403
    assert client.post("/invoices/99999/cancel", headers=acc).status_code == 404
