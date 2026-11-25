from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import any_user, writer
from app.models import User
from app.schemas.invoice import CancelRequest, InvoiceCreate, InvoiceOut, InvoiceSummary, InvoiceType
from app.services import invoicing

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.post("", response_model=InvoiceOut, status_code=201)
def create_invoice(data: InvoiceCreate, db: Session = Depends(get_db), user: User = Depends(writer)):
    """Create a sale or purchase invoice: saves it, moves stock and posts the journal in ONE transaction."""
    return invoicing.to_out(db, invoicing.create_invoice(db, data, user.id))


@router.get("", response_model=list[InvoiceSummary], dependencies=[Depends(any_user)])
def list_invoices(
    invoice_type: InvoiceType | None = None,
    party_id: int | None = None,
    status: Annotated[str | None, Query(pattern="^(posted|cancelled)$")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
):
    return invoicing.list_invoices(
        db, invoice_type=invoice_type, party_id=party_id, status=status,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset,
    )


@router.get("/{invoice_id}", response_model=InvoiceOut, dependencies=[Depends(any_user)])
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    return invoicing.to_out(db, invoicing.get_invoice(db, invoice_id))


@router.post("/{invoice_id}/cancel", response_model=InvoiceOut)
def cancel_invoice(
    invoice_id: int, body: CancelRequest | None = None,
    db: Session = Depends(get_db), user: User = Depends(writer),
):
    """Cancel = reverse, never delete: stock is restored and a mirror-image journal entry is posted."""
    return invoicing.to_out(db, invoicing.cancel_invoice(db, invoice_id, body.reason if body else None))
