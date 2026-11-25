import pytest

from app.errors import Conflict
from app.schemas.payment import PaymentCreate
from app.services import invoicing, payments, reports
from tests.factories import make_item, make_party, purchase, sale
from tests.helpers import balance, totals, unbalanced_entries


def pay(db, inv, amount, mode="bank"):
    return payments.record_payment(db, PaymentCreate(invoice_id=inv.id, amount=amount, mode=mode))


def test_sale_payment_reduces_outstanding_and_posts_journal(db):
    party = make_party(db)
    inv = sale(db, party, [(make_item(db, price=10_000), 1)])          # grand total 11,800
    _, due = pay(db, inv, 5_000, "bank")
    assert due == 6_800
    assert balance(db, "1010") == 5_000          # Dr Bank
    assert balance(db, "1100") == 11_800 - 5_000  # receivable shrank
    out = reports.party_outstanding(db)
    assert [(r.party_id, r.invoiced, r.paid, r.outstanding) for r in out.rows] == [(party.id, 11_800, 5_000, 6_800)]
    pay(db, inv, 6_800, "cash")
    assert balance(db, "1000") == 6_800 and balance(db, "1100") == 0
    assert reports.party_outstanding(db).rows == []                    # settled parties are hidden...
    assert len(reports.party_outstanding(db, include_settled=True).rows) == 1  # ...unless asked for


def test_purchase_payment_posts_payables_debit(db):
    supplier = make_party(db, "supplier")
    inv = purchase(db, supplier, [(make_item(db, stock=0), 10, 1_000)])    # 10,000 + 1,800 = 11,800
    pay(db, inv, 4_000, "cash")
    assert balance(db, "2000") == -(11_800 - 4_000)   # payable (credit balance) reduced
    assert balance(db, "1000") == -4_000              # cash went out
    out = reports.party_outstanding(db, "supplier")
    assert out.total_payable == 7_800 and out.total_receivable == 0


def test_overpayment_rejected(db):
    inv = sale(db, make_party(db), [(make_item(db, price=10_000), 1)])
    with pytest.raises(Conflict, match="exceeds the outstanding"):
        pay(db, inv, 11_801)
    pay(db, inv, 11_800)
    with pytest.raises(Conflict):
        pay(db, inv, 1)                      # fully paid now
    d, c = totals(db)
    assert d == c


def test_cannot_pay_a_cancelled_invoice_or_cancel_a_paid_one(db):
    a = sale(db, make_party(db), [(make_item(db), 1)])
    invoicing.cancel_invoice(db, a.id)
    with pytest.raises(Conflict, match="cancelled"):
        pay(db, a, 100)
    b = sale(db, make_party(db), [(make_item(db), 1)])
    pay(db, b, 100)
    with pytest.raises(Conflict, match="payments recorded"):
        invoicing.cancel_invoice(db, b.id)


def test_ledger_still_balances_and_receivable_ties_to_party_report(db):
    p1, p2 = make_party(db), make_party(db, state_code="29")
    item = make_item(db, stock=100, price=7_777)
    i1, i2 = sale(db, p1, [(item, 3)]), sale(db, p2, [(item, 5)])
    pay(db, i1, 1_000)
    pay(db, i2, i2.grand_total)
    assert unbalanced_entries(db) == []
    # control account (ledger) must equal the sum of party balances (sub-ledger)
    assert balance(db, "1100") == reports.party_outstanding(db).total_receivable


def test_payment_api(client, headers, db):
    inv = sale(db, make_party(db), [(make_item(db, price=10_000), 1)])
    acc = headers("accountant")
    r = client.post("/payments", json={"invoice_id": inv.id, "amount": 3_000, "mode": "bank", "reference": "UPI123"}, headers=acc)
    assert r.status_code == 201 and r.json()["balance_due_after"] == 8_800
    assert client.get(f"/invoices/{inv.id}", headers=acc).json()["balance_due"] == 8_800
    assert len(client.get(f"/payments?invoice_id={inv.id}", headers=headers("viewer")).json()) == 1
    assert client.post("/payments", json={"invoice_id": inv.id, "amount": 99_999, "mode": "cash"}, headers=acc).status_code == 409
    assert client.post("/payments", json={"invoice_id": inv.id, "amount": 0, "mode": "cash"}, headers=acc).status_code == 422
    assert client.post("/payments", json={"invoice_id": inv.id, "amount": 1, "mode": "cash"}, headers=headers("viewer")).status_code == 403
    assert client.post("/payments", json={"invoice_id": 9999, "amount": 1, "mode": "cash"}, headers=acc).status_code == 404
