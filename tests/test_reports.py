from datetime import date

from app.models import Item
from app.services import invoicing, reports
from tests.factories import make_item, make_party, purchase, sale


def test_trial_balance_nets_to_zero_and_lists_every_account(db):
    sale(db, make_party(db, state_code="27"), [(make_item(db, price=10_000), 2)])
    sale(db, make_party(db, state_code="29"), [(make_item(db, price=999, rate_bp=500), 7)])
    purchase(db, make_party(db, "supplier"), [(make_item(db, stock=0), 5, 1_234)])
    tb = reports.trial_balance(db)
    assert tb.net == 0 and tb.balanced and tb.total_debit == tb.total_credit > 0
    assert len(tb.rows) == 12
    assert sum(r.debit_balance for r in tb.rows) == sum(r.credit_balance for r in tb.rows)   # Dr column = Cr column
    by = {r.code: r for r in tb.rows}
    assert by["1100"].debit_balance > 0 and by["4000"].credit_balance > 0 and by["1000"].debit_total == 0


def test_trial_balance_as_of_date(db):
    party, item = make_party(db), make_item(db)
    sale(db, party, [(item, 1)], invoice_date="2026-04-10")
    sale(db, party, [(item, 1)], invoice_date="2026-06-10")
    early = reports.trial_balance(db, date(2026, 5, 1))
    full = reports.trial_balance(db)
    assert early.total_debit * 2 == full.total_debit and early.net == full.net == 0


def test_trial_balance_still_zero_after_cancellation(db):
    inv = sale(db, make_party(db), [(make_item(db), 3)])
    invoicing.cancel_invoice(db, inv.id)
    tb = reports.trial_balance(db)
    assert tb.net == 0 and all(r.debit_balance == r.credit_balance == 0 for r in tb.rows)  # fully reversed


def test_stock_summary(db):
    supplier, customer = make_party(db, "supplier"), make_party(db)
    item = make_item(db, stock=10, name="Bolt")
    purchase(db, supplier, [(item, 20, 100)])
    sale(db, customer, [(item, 12)])
    cancelled = sale(db, customer, [(item, 5)])
    invoicing.cancel_invoice(db, cancelled.id)
    (row,) = reports.stock_summary(db)
    assert (row.opening, row.purchased, row.sold, row.on_hand, row.ledger_consistent) == (10, 20, 12, 18, True)


def test_stock_summary_flags_a_drifted_item(db):
    item = make_item(db, stock=10)
    db.get(Item, item.id).stock_qty = 11   # simulate corruption outside the service layer
    db.commit()
    assert reports.stock_summary(db)[0].ledger_consistent is False


def test_gst_summary_groups_by_rate_and_excludes_cancelled_and_out_of_period(db):
    intra, inter = make_party(db, state_code="27"), make_party(db, state_code="29")
    a18, a5 = make_item(db, price=10_000, rate_bp=1800), make_item(db, price=1_000, rate_bp=500)
    sale(db, intra, [(a18, 1), (a5, 10)], invoice_date="2026-04-05")      # 18%: 10000 -> 900+900 ; 5%: 10000 -> 250+250
    sale(db, inter, [(a18, 2)], invoice_date="2026-04-20")                # 18%: 20000 -> igst 3600
    gone = sale(db, inter, [(a18, 1)], invoice_date="2026-04-21")
    invoicing.cancel_invoice(db, gone.id)
    sale(db, intra, [(a18, 1)], invoice_date="2026-05-02")               # outside the period
    purchase(db, make_party(db, "supplier"), [(make_item(db, stock=0), 10, 1_000)], invoice_date="2026-04-15")  # 18% input 900+900
    g = reports.gst_summary(db, date(2026, 4, 1), date(2026, 4, 30))
    got = {(r.invoice_type, r.gst_rate_bp): (r.taxable_value, r.cgst, r.sgst, r.igst) for r in g.rows}
    assert got == {
        ("sale", 500): (10_000, 250, 250, 0),
        ("sale", 1800): (30_000, 900, 900, 3_600),
        ("purchase", 1800): (10_000, 900, 900, 0),
    }
    assert g.sales.total_tax == 500 + 1_800 + 3_600 and g.purchases.total_tax == 1_800
    assert g.net_tax_payable == g.sales.total_tax - g.purchases.total_tax == 4_100


def test_reports_over_http(client, headers, db):
    sale(db, make_party(db), [(make_item(db), 1)], invoice_date="2026-04-10")
    v = headers("viewer")
    assert client.get("/reports/trial-balance", headers=v).json()["net"] == 0
    assert len(client.get("/reports/party-outstanding", headers=v).json()["rows"]) == 1
    assert client.get("/reports/stock-summary", headers=v).json()[0]["on_hand"] == 99
    g = client.get("/reports/gst-summary?date_from=2026-04-01&date_to=2026-04-30", headers=v).json()
    assert g["sales"]["total_tax"] == 1_800
    assert client.get("/reports/gst-summary?date_from=2026-05-01&date_to=2026-04-01", headers=v).status_code == 422
    assert client.get("/reports/gst-summary", headers=v).status_code == 422
    assert client.get("/reports/trial-balance").status_code == 401
