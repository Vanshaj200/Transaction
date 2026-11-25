"""Small helpers that create master data directly through the service layer."""
from app.schemas.invoice import InvoiceCreate
from app.schemas.item import ItemCreate
from app.schemas.party import PartyCreate
from app.services import invoicing, masters
from app.services.gstin import make_gstin

_counter = {"n": 0}


def _n() -> int:
    _counter["n"] += 1
    return _counter["n"]


def make_party(db, party_type="customer", state_code="27", name=None, gstin=True):
    n = _n()
    pan = f"ABCDE{1000 + n % 9000}F"
    return masters.create_party(db, PartyCreate(
        name=name or f"{party_type.title()} {n}", party_type=party_type, state_code=state_code,
        gstin=make_gstin(state_code, pan) if gstin else None,
    ))


def make_item(db, stock=100, price=10000, rate_bp=1800, name=None):
    n = _n()
    return masters.create_item(db, ItemCreate(
        name=name or f"Item {n}", hsn="8471", gst_rate_bp=rate_bp, selling_price=price, opening_stock=stock,
    ))


def sale(db, party, lines, invoice_date=None, user_id=None):
    """lines = [(item, qty)] or [(item, qty, unit_price)]"""
    return invoicing.create_invoice(db, _payload("sale", party, lines, invoice_date), user_id)


def purchase(db, party, lines, invoice_date=None, user_id=None):
    return invoicing.create_invoice(db, _payload("purchase", party, lines, invoice_date), user_id)


def _payload(kind, party, lines, invoice_date):
    body = {
        "invoice_type": kind, "party_id": party.id,
        "lines": [
            {"item_id": ln[0].id, "quantity": ln[1], **({"unit_price": ln[2]} if len(ln) > 2 else {})}
            for ln in lines
        ],
    }
    if invoice_date:
        body["invoice_date"] = invoice_date
    return InvoiceCreate(**body)
