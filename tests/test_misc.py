"""Small edge cases: listing/filters, error paths, security helpers."""
from app.models import User
from app.security import decode_access_token, hash_password, verify_password
from tests.factories import make_item, make_party, sale
from tests.test_masters import GSTIN_KA, item, party


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_item_list_filter_and_soft_delete(client, headers):
    a, adm = headers("accountant"), headers("admin")
    client.post("/items", json=item(name="Blue Pen"), headers=a)
    iid = client.post("/items", json=item(name="Red Marker"), headers=a).json()["id"]
    assert [x["name"] for x in client.get("/items?q=pen", headers=a).json()] == ["Blue Pen"]
    assert client.delete(f"/items/{iid}", headers=a).status_code == 403
    assert client.delete(f"/items/{iid}", headers=adm).status_code == 204
    assert [x["name"] for x in client.get("/items", headers=a).json()] == ["Blue Pen"]
    assert len(client.get("/items?active_only=false", headers=a).json()) == 2


def test_party_patch_gstin_must_match_state(client, headers):
    a = headers("accountant")
    pid = client.post("/parties", json=party(), headers=a).json()["id"]          # Maharashtra
    assert client.patch(f"/parties/{pid}", json={"gstin": GSTIN_KA}, headers=a).status_code == 409   # Karnataka GSTIN
    assert client.patch(f"/parties/{pid}", json={"gstin": "bad"}, headers=a).status_code == 422
    assert client.patch(f"/parties/{pid}", json={"state_code": "99"}, headers=a).status_code == 422
    r = client.patch(f"/parties/{pid}", json={"state_code": "29", "gstin": GSTIN_KA}, headers=a)
    assert r.status_code == 200 and r.json()["state_code"] == "29"


def test_payment_get_and_list_by_party(client, headers, db):
    party_, item_ = make_party(db), make_item(db)
    inv = sale(db, party_, [(item_, 1)])
    a = headers("accountant")
    pid = client.post("/payments", json={"invoice_id": inv.id, "amount": 100, "mode": "cash"}, headers=a).json()["id"]
    assert client.get(f"/payments/{pid}", headers=a).json()["amount"] == 100
    assert len(client.get(f"/payments?party_id={party_.id}", headers=a).json()) == 1
    assert client.get("/payments/999", headers=a).status_code == 404


def test_inactive_user_cannot_use_token_or_login(client, db, headers):
    h = headers("viewer")
    assert client.get("/auth/me", headers=h).status_code == 200
    user = db.query(User).one()
    user.is_active = False
    db.commit()
    assert client.get("/auth/me", headers=h).status_code == 401
    assert client.post("/auth/login", data={"username": user.email, "password": "password123"}).status_code == 401


def test_password_and_token_helpers():
    hashed = hash_password("s3cret-pass")
    assert verify_password("s3cret-pass", hashed) and not verify_password("other", hashed)
    assert verify_password("x", "not-a-bcrypt-hash") is False
    assert decode_access_token("garbage") is None
