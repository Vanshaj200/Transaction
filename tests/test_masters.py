from app.services.gstin import make_gstin

GSTIN_MH = make_gstin("27", "ABCDE1234F")
GSTIN_KA = make_gstin("29", "PQRSX9876L")


def party(**over):
    body = {"name": "Acme Traders", "party_type": "customer", "gstin": GSTIN_MH, "state_code": "27"}
    body.update(over)
    return body


def item(**over):
    body = {"name": "Pen", "hsn": "9608", "gst_rate_bp": 1800, "selling_price": 1000, "opening_stock": 10}
    body.update(over)
    return body


def test_party_crud(client, headers):
    acc = headers("accountant")
    r = client.post("/parties", json=party(), headers=acc)
    assert r.status_code == 201
    pid = r.json()["id"]
    assert client.get(f"/parties/{pid}", headers=headers("viewer")).json()["name"] == "Acme Traders"
    r = client.patch(f"/parties/{pid}", json={"name": "Acme Ltd"}, headers=acc)
    assert r.json()["name"] == "Acme Ltd"
    assert len(client.get("/parties?party_type=customer&q=acme", headers=acc).json()) == 1


def test_party_validation(client, headers):
    acc = headers("accountant")
    assert client.post("/parties", json=party(gstin="27AAPFU0939F1ZX"), headers=acc).status_code == 422  # check digit
    assert client.post("/parties", json=party(state_code="29"), headers=acc).status_code == 422  # GSTIN/state mismatch
    assert client.post("/parties", json=party(state_code="98", gstin=None), headers=acc).status_code == 422
    assert client.post("/parties", json=party(gstin=None), headers=acc).status_code == 201  # B2C: no GSTIN needed


def test_duplicate_gstin_conflict(client, headers):
    acc = headers("accountant")
    client.post("/parties", json=party(), headers=acc)
    assert client.post("/parties", json=party(name="Other"), headers=acc).status_code == 409


def test_roles_on_masters(client, headers):
    v, a, adm = headers("viewer"), headers("accountant"), headers("admin")
    assert client.post("/parties", json=party(), headers=v).status_code == 403
    pid = client.post("/parties", json=party(), headers=a).json()["id"]
    assert client.delete(f"/parties/{pid}", headers=a).status_code == 403
    assert client.delete(f"/parties/{pid}", headers=adm).status_code == 204
    assert client.get(f"/parties/{pid}", headers=v).json()["is_active"] is False  # soft delete
    assert client.get("/parties", headers=v).json() == []  # hidden from default list


def test_item_create_records_opening_stock_movement(client, headers, db):
    from sqlalchemy import select

    from app.models import StockMovement

    r = client.post("/items", json=item(), headers=headers("accountant"))
    assert r.status_code == 201 and r.json()["stock_qty"] == 10
    mv = db.scalars(select(StockMovement)).all()
    assert [(m.qty_change, m.reason) for m in mv] == [(10, "opening")]


def test_item_validation_and_stock_not_editable(client, headers):
    a = headers("accountant")
    assert client.post("/items", json=item(gst_rate_bp=1700), headers=a).status_code == 422  # not a GST slab
    assert client.post("/items", json=item(selling_price=-5), headers=a).status_code == 422
    assert client.post("/items", json=item(hsn="12"), headers=a).status_code == 422
    iid = client.post("/items", json=item(), headers=a).json()["id"]
    client.patch(f"/items/{iid}", json={"stock_qty": 999, "selling_price": 2000}, headers=a)  # stock_qty ignored
    got = client.get(f"/items/{iid}", headers=a).json()
    assert got["stock_qty"] == 10 and got["selling_price"] == 2000


def test_missing_returns_404(client, headers):
    assert client.get("/items/12345", headers=headers("viewer")).status_code == 404
