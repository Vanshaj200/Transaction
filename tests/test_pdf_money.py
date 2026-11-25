import pytest

from app.services.money import format_inr, format_rate
from tests.factories import make_item, make_party, sale


@pytest.mark.parametrize("paise,text", [
    (0, "0.00"), (5, "0.05"), (99_999, "999.99"), (100_000, "1,000.00"), (12_345_678, "1,23,456.78"),
    (123_456_789_00, "12,34,56,789.00"), (-250, "-2.50"),
])
def test_format_inr(paise, text):
    assert format_inr(paise) == text


def test_format_rate():
    assert [format_rate(x) for x in (0, 25, 500, 1800)] == ["0%", "0.25%", "5%", "18%"]


def test_pdf_download(client, headers, db):
    inv = sale(db, make_party(db, state_code="29"), [(make_item(db, name="Steel Bolt"), 3)])
    r = client.get(f"/invoices/{inv.id}/pdf", headers=headers("viewer"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and inv.invoice_no in r.headers["content-disposition"]
    assert client.get(f"/invoices/{inv.id}/pdf").status_code == 401
    assert client.get("/invoices/99999/pdf", headers=headers("viewer")).status_code == 404


def test_pdf_for_cancelled_intra_state_invoice_renders(client, headers, db):
    from app.services import invoicing
    inv = sale(db, make_party(db, state_code="27"), [(make_item(db), 1)])
    invoicing.cancel_invoice(db, inv.id)
    r = client.get(f"/invoices/{inv.id}/pdf", headers=headers("accountant"))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
