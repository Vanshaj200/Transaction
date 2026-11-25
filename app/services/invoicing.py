"""Invoice creation and queries.

create_invoice() is the heart of the system. It runs as ONE database transaction:
    lock items -> compute GST -> save invoice -> move stock -> post journal -> COMMIT
and any exception rolls the whole thing back.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.errors import Conflict, InsufficientStock, InvalidInput, NotFound
from app.models import Invoice, InvoiceLine, Party, Payment
from app.schemas.invoice import InvoiceCreate, InvoiceOut, InvoiceSummary
from app.services import gst, inventory, ledger, masters

INVOICE_PREFIX = {"sale": "INV", "purchase": "PUR"}
# party type that each invoice type may be raised against
PARTY_FOR = {"sale": "customer", "purchase": "supplier"}
STOCK_REASON = {"sale": "sale", "purchase": "purchase"}


def create_invoice(db: Session, data: InvoiceCreate, user_id: int | None = None) -> Invoice:
    try:
        invoice = _create_invoice(db, data, user_id)
        db.commit()
    except BaseException:
        db.rollback()  # undoes invoice rows, stock changes and journal rows together
        raise
    return invoice


def _create_invoice(db: Session, data: InvoiceCreate, user_id: int | None) -> Invoice:
    party = masters.get_party(db, data.party_id)
    if not party.is_active:
        raise Conflict(f"Party {party.id} is inactive")
    if party.party_type != PARTY_FOR[data.invoice_type]:
        raise InvalidInput(f"A {data.invoice_type} invoice needs a {PARTY_FOR[data.invoice_type]}, not a {party.party_type}")

    supply = gst.supply_type(get_settings().company_state_code, party.state_code)

    # 1. Lock every item we will touch (in id order) before reading any stock figure.
    items = inventory.lock_items(db, (line.item_id for line in data.lines))

    # 2. Build lines and compute GST per line.
    lines = [_build_line(pos, line_in, items[line_in.item_id], data.invoice_type, supply)
             for pos, line_in in enumerate(data.lines, start=1)]

    if sum(ln.line_total for ln in lines) <= 0:
        raise InvalidInput("Invoice total must be greater than zero")

    # 3. Save the invoice. The invoice number is derived from the generated id (see README).
    invoice = Invoice(
        invoice_no=f"TMP-{uuid.uuid4().hex[:24]}",  # unique placeholder, replaced right after flush
        invoice_type=data.invoice_type, party_id=party.id, invoice_date=data.invoice_date,
        place_of_supply=party.state_code, supply_type=supply, created_by=user_id,
        taxable_total=sum(ln.taxable_value for ln in lines),
        cgst_total=sum(ln.cgst for ln in lines),
        sgst_total=sum(ln.sgst for ln in lines),
        igst_total=sum(ln.igst for ln in lines),
        grand_total=sum(ln.line_total for ln in lines),
        lines=lines,
    )
    db.add(invoice)
    db.flush()
    invoice.invoice_no = f"{INVOICE_PREFIX[data.invoice_type]}-{invoice.id:07d}"

    # 4. Move stock (sales take stock out and are refused if it would go negative).
    sign = -1 if data.invoice_type == "sale" else 1
    for line in lines:
        inventory.change_stock(db, items[line.item_id], sign * line.quantity, STOCK_REASON[data.invoice_type], invoice.id)

    # 5. Post the double-entry journal (same transaction: if this raises, steps 1-4 roll back too).
    ledger.post_invoice(db, invoice)
    return invoice


def _build_line(position: int, line_in, item, invoice_type: str, supply: str) -> InvoiceLine:
    if not item.is_active:
        raise Conflict(f"Item {item.id} ({item.name}) is inactive")
    unit_price = line_in.unit_price
    if unit_price is None:
        if invoice_type == "purchase":
            raise InvalidInput(f"unit_price is required on purchase lines (item {item.id})")
        unit_price = item.selling_price
    tax = gst.compute_line_tax(line_in.quantity, unit_price, item.gst_rate_bp, supply)
    return InvoiceLine(
        position=position, item_id=item.id, description=item.name, hsn=item.hsn,
        quantity=line_in.quantity, unit_price=unit_price, gst_rate_bp=item.gst_rate_bp,
        taxable_value=tax.taxable, cgst=tax.cgst, sgst=tax.sgst, igst=tax.igst, line_total=tax.total,
    )


# ------------------------------------------------------------------ queries
def get_invoice(db: Session, invoice_id: int) -> Invoice:
    invoice = db.scalar(select(Invoice).where(Invoice.id == invoice_id).options(selectinload(Invoice.lines)))
    if invoice is None:
        raise NotFound(f"Invoice {invoice_id} not found")
    return invoice


def amount_paid(db: Session, invoice_id: int) -> int:
    # PostgreSQL returns SUM(bigint) as NUMERIC (a Decimal), so convert to int explicitly.
    return int(db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.invoice_id == invoice_id)))


def to_out(db: Session, invoice: Invoice) -> InvoiceOut:
    """ORM invoice -> response, adding the paid / due figures derived from payments."""
    out = InvoiceOut.model_validate(invoice, from_attributes=True)
    paid = amount_paid(db, invoice.id)
    party_name = db.scalar(select(Party.name).where(Party.id == invoice.party_id))
    due = 0 if invoice.status == "cancelled" else invoice.grand_total - paid
    return out.model_copy(update={"amount_paid": paid, "balance_due": due, "party_name": party_name})


def list_invoices(db: Session, *, invoice_type=None, party_id=None, status=None,
                  date_from=None, date_to=None, limit=100, offset=0) -> list[InvoiceSummary]:
    paid = (
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.invoice_id == Invoice.id).correlate(Invoice).scalar_subquery()
    )
    stmt = select(Invoice, Party.name, paid).join(Party, Party.id == Invoice.party_id)
    for column, value in ((Invoice.invoice_type, invoice_type), (Invoice.party_id, party_id), (Invoice.status, status)):
        if value is not None:
            stmt = stmt.where(column == value)
    if date_from:
        stmt = stmt.where(Invoice.invoice_date >= date_from)
    if date_to:
        stmt = stmt.where(Invoice.invoice_date <= date_to)
    stmt = stmt.order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).limit(limit).offset(offset)
    result = []
    for inv, party_name, paid_raw in db.execute(stmt):
        paid_amt = int(paid_raw)  # NUMERIC -> int on PostgreSQL
        summary = InvoiceSummary.model_validate(inv, from_attributes=True)
        due = 0 if inv.status == "cancelled" else inv.grand_total - paid_amt
        result.append(summary.model_copy(update={"party_name": party_name, "amount_paid": paid_amt, "balance_due": due}))
    return result


# ------------------------------------------------------------------ cancellation
def cancel_invoice(db: Session, invoice_id: int, reason: str | None = None) -> Invoice:
    """Invoices are never deleted. Cancelling = status flip + stock put back + a REVERSING journal entry."""
    try:
        invoice = _cancel_invoice(db, invoice_id, reason)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return invoice


def _cancel_invoice(db: Session, invoice_id: int, reason: str | None) -> Invoice:
    # Lock the invoice row: two simultaneous cancels (or a payment racing a cancel) queue up here,
    # and the loser then sees the new status instead of acting on stale data.
    invoice = db.scalar(
        select(Invoice).where(Invoice.id == invoice_id).with_for_update()
        .execution_options(populate_existing=True).options(selectinload(Invoice.lines))
    )
    if invoice is None:
        raise NotFound(f"Invoice {invoice_id} not found")
    if invoice.status == "cancelled":
        raise Conflict(f"Invoice {invoice.invoice_no} is already cancelled")
    if amount_paid(db, invoice.id) > 0:
        raise Conflict(f"Invoice {invoice.invoice_no} has payments recorded and cannot be cancelled")

    items = inventory.lock_items(db, (line.item_id for line in invoice.lines))
    # Undo the stock effect. A sale gives stock back; cancelling a purchase takes it out again,
    # which is refused if that stock has already been sold.
    sign, stock_reason = (1, "sale_cancel") if invoice.invoice_type == "sale" else (-1, "purchase_cancel")
    for line in invoice.lines:
        try:
            inventory.change_stock(db, items[line.item_id], sign * line.quantity, stock_reason, invoice.id)
        except InsufficientStock as exc:
            raise Conflict(f"Cannot cancel purchase {invoice.invoice_no}: {exc.message}") from exc

    ledger.reverse_invoice_entry(db, invoice, entry_date=date.today())
    invoice.status = "cancelled"
    invoice.cancelled_at = datetime.now(timezone.utc)
    invoice.cancel_reason = reason
    return invoice
