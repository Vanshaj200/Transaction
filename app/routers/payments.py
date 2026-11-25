from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import any_user, writer
from app.models import User
from app.schemas.payment import PaymentCreate, PaymentOut
from app.services import payments

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("", response_model=PaymentOut, status_code=201)
def record_payment(data: PaymentCreate, db: Session = Depends(get_db), user: User = Depends(writer)):
    """Record money received (sale) or paid (purchase). Reduces the party's outstanding and posts a journal entry."""
    payment, due = payments.record_payment(db, data, user.id)
    return PaymentOut.model_validate(payment, from_attributes=True).model_copy(update={"balance_due_after": due})


@router.get("", response_model=list[PaymentOut], dependencies=[Depends(any_user)])
def list_payments(
    invoice_id: int | None = None, party_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100, offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
):
    return payments.list_payments(db, invoice_id, party_id, limit, offset)


@router.get("/{payment_id}", response_model=PaymentOut, dependencies=[Depends(any_user)])
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    return payments.get_payment(db, payment_id)
