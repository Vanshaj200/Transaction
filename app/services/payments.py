"""Recording payments against invoices."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import Conflict, NotFound
from app.models import Invoice, Payment
from app.schemas.payment import PaymentCreate
from app.services import ledger
from app.services.invoicing import amount_paid


def record_payment(db: Session, data: PaymentCreate, user_id: int | None = None) -> tuple[Payment, int]:
    """Returns (payment, balance still due). One transaction: payment row + journal entry."""
    try:
        result = _record_payment(db, data, user_id)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return result


def _record_payment(db: Session, data: PaymentCreate, user_id: int | None) -> tuple[Payment, int]:
    # Lock the invoice so two concurrent payments cannot both pass the "not more than outstanding"
    # check and together overpay, and so a payment cannot slip in while the invoice is being cancelled.
    invoice = db.scalar(
        select(Invoice).where(Invoice.id == data.invoice_id).with_for_update().execution_options(populate_existing=True)
    )
    if invoice is None:
        raise NotFound(f"Invoice {data.invoice_id} not found")
    if invoice.status != "posted":
        raise Conflict(f"Invoice {invoice.invoice_no} is {invoice.status}; payments are only accepted on posted invoices")

    due = invoice.grand_total - amount_paid(db, invoice.id)
    if data.amount > due:
        raise Conflict(f"Payment {data.amount} exceeds the outstanding balance {due} of invoice {invoice.invoice_no}")

    payment = Payment(
        invoice_id=invoice.id, amount=data.amount, payment_date=data.payment_date,
        mode=data.mode, reference=data.reference, created_by=user_id,
    )
    db.add(payment)
    db.flush()
    ledger.post_entry(
        db, entry_date=data.payment_date, description=f"Payment {payment.id} against {invoice.invoice_no}",
        lines=ledger.payment_posting_lines(invoice.invoice_type, data.mode, data.amount), payment_id=payment.id,
    )
    return payment, due - data.amount


def list_payments(db: Session, invoice_id: int | None, party_id: int | None, limit: int, offset: int) -> list[Payment]:
    stmt = select(Payment).order_by(Payment.payment_date.desc(), Payment.id.desc()).limit(limit).offset(offset)
    if invoice_id is not None:
        stmt = stmt.where(Payment.invoice_id == invoice_id)
    if party_id is not None:
        stmt = stmt.join(Invoice, Invoice.id == Payment.invoice_id).where(Invoice.party_id == party_id)
    return list(db.scalars(stmt))


def get_payment(db: Session, payment_id: int) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise NotFound(f"Payment {payment_id} not found")
    return payment
