import pytest
from sqlalchemy import func, select

from app.errors import Conflict, InsufficientStock, InvalidInput
from app.models import Invoice, Item, StockMovement
from tests.factories import make_item, make_party, purchase, sale


def stock_of(db, item):
    db.expire_all()
    return db.get(Item, item.id).stock_qty


def test_intra_state_sale_gets_cgst_sgst_and_reduces_stock(db):
    party = make_party(db, state_code="27")            # company state is 27 -> intra
    item = make_item(db, stock=10, price=10_000)
    inv = sale(db, party, [(item, 3)])
    assert inv.supply_type == "intra"
    assert (inv.taxable_total, inv.cgst_total, inv.sgst_total, inv.igst_total, inv.grand_total) == (30_000, 2_700, 2_700, 0, 35_400)
    assert inv.invoice_no.startswith("INV-") and inv.status == "posted"
    assert stock_of(db, item) == 7


def test_inter_state_sale_gets_igst(db):
    party = make_party(db, state_code="29")
    item = make_item(db, price=10_000)
    inv = sale(db, party, [(item, 2)])
    assert inv.supply_type == "inter" and inv.place_of_supply == "29"
    assert (inv.cgst_total, inv.sgst_total, inv.igst_total, inv.grand_total) == (0, 0, 3_600, 23_600)


def test_invoice_totals_are_sum_of_rounded_lines(db):
    party = make_party(db, state_code="29")
    a = make_item(db, price=33, rate_bp=500)
    inv = sale(db, party, [(a, 1), (a, 1), (a, 1)])    # each line: 33 paise @5% = 1.65 -> 2
    assert [ln.igst for ln in inv.lines] == [2, 2, 2] and inv.igst_total == 6


def test_stock_movement_row_written_per_line(db):
    party = make_party(db)
    item = make_item(db, stock=10)
    inv = sale(db, party, [(item, 4)])
    rows = db.execute(select(StockMovement.qty_change, StockMovement.reason, StockMovement.invoice_id)
                      .where(StockMovement.invoice_id == inv.id)).all()
    assert rows == [(-4, "sale", inv.id)]


def test_stock_ledger_always_matches_item_stock(db):
    party, supplier = make_party(db), make_party(db, "supplier")
    item = make_item(db, stock=5)
    purchase(db, supplier, [(item, 20, 5_000)])
    sale(db, party, [(item, 7)])
    total = db.scalar(select(func.sum(StockMovement.qty_change)).where(StockMovement.item_id == item.id))
    assert total == stock_of(db, item) == 18


def test_oversell_is_rejected_and_nothing_is_saved(db):
    party = make_party(db)
    item = make_item(db, stock=5)
    with pytest.raises(InsufficientStock):
        sale(db, party, [(item, 6)])
    assert stock_of(db, item) == 5
    assert db.scalar(select(func.count(Invoice.id))) == 0


def test_oversell_across_duplicate_lines_is_caught(db):
    party = make_party(db)
    item = make_item(db, stock=5)
    with pytest.raises(InsufficientStock):
        sale(db, party, [(item, 3), (item, 3)])  # 3 + 3 > 5 although each line alone is fine
    assert stock_of(db, item) == 5


def test_second_line_failing_rolls_back_the_first(db):
    party = make_party(db)
    ok, short = make_item(db, stock=10), make_item(db, stock=1)
    with pytest.raises(InsufficientStock):
        sale(db, party, [(ok, 5), (short, 2)])
    assert stock_of(db, ok) == 10 and stock_of(db, short) == 1
    assert db.scalar(select(func.count(StockMovement.id))) == 2  # only the two opening movements


def test_purchase_adds_stock_and_needs_supplier_and_price(db):
    supplier, customer = make_party(db, "supplier"), make_party(db, "customer")
    item = make_item(db, stock=0)
    inv = purchase(db, supplier, [(item, 10, 4_000)])
    assert inv.invoice_no.startswith("PUR-") and stock_of(db, item) == 10
    with pytest.raises(InvalidInput):
        purchase(db, supplier, [(item, 1)])               # no unit price
    with pytest.raises(InvalidInput):
        purchase(db, customer, [(item, 1, 4_000)])        # wrong party type
    with pytest.raises(InvalidInput):
        sale(db, supplier, [(item, 1)])


def test_inactive_party_or_item_rejected(db):
    from app.schemas.item import ItemUpdate
    from app.schemas.party import PartyUpdate
    from app.services import masters

    party, item = make_party(db), make_item(db)
    masters.update_item(db, item.id, ItemUpdate(is_active=False))
    with pytest.raises(Conflict):
        sale(db, party, [(item, 1)])
    item2 = make_item(db)
    masters.update_party(db, party.id, PartyUpdate(is_active=False))
    with pytest.raises(Conflict):
        sale(db, party, [(item2, 1)])


def test_invoice_keeps_price_and_name_snapshot(db):
    from app.schemas.item import ItemUpdate
    from app.services import masters

    party = make_party(db)
    item = make_item(db, price=10_000, name="Widget")
    inv = sale(db, party, [(item, 1)])
    masters.update_item(db, item.id, ItemUpdate(selling_price=99_999, name="Renamed"))
    db.expire_all()
    line = db.get(Invoice, inv.id).lines[0]
    assert (line.unit_price, line.description) == (10_000, "Widget")


# ---------------------------------------------------------------- through the HTTP API
def test_api_create_get_list(client, headers, db):
    party, item = make_party(db), make_item(db, price=10_000)
    acc = headers("accountant")
    body = {"invoice_type": "sale", "party_id": party.id, "invoice_date": "2026-04-10",
            "lines": [{"item_id": item.id, "quantity": 2}]}
    r = client.post("/invoices", json=body, headers=acc)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["grand_total"] == 23_600 and inv["balance_due"] == 23_600 and inv["lines"][0]["cgst"] == 1_800
    assert client.get(f"/invoices/{inv['id']}", headers=headers("viewer")).json()["invoice_no"] == inv["invoice_no"]
    rows = client.get("/invoices?invoice_type=sale&date_from=2026-04-01", headers=acc).json()
    assert [x["id"] for x in rows] == [inv["id"]] and rows[0]["party_name"] == party.name


def test_api_insufficient_stock_is_409_and_viewer_cannot_create(client, headers, db):
    party, item = make_party(db), make_item(db, stock=1)
    body = {"invoice_type": "sale", "party_id": party.id, "lines": [{"item_id": item.id, "quantity": 2}]}
    r = client.post("/invoices", json=body, headers=headers("accountant"))
    assert r.status_code == 409 and "Insufficient stock" in r.json()["detail"]
    assert client.post("/invoices", json=body, headers=headers("viewer")).status_code == 403


def test_api_validation(client, headers, db):
    acc = headers("accountant")
    party, item = make_party(db), make_item(db)
    base = {"invoice_type": "sale", "party_id": party.id}
    assert client.post("/invoices", json={**base, "lines": []}, headers=acc).status_code == 422
    assert client.post("/invoices", json={**base, "lines": [{"item_id": item.id, "quantity": 0}]}, headers=acc).status_code == 422
    assert client.post("/invoices", json={**base, "lines": [{"item_id": 9999, "quantity": 1}]}, headers=acc).status_code == 404
    assert client.get("/invoices/9999", headers=acc).status_code == 404
