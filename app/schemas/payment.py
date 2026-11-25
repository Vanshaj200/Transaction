from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PaymentCreate(BaseModel):
    invoice_id: int
    amount: int = Field(gt=0, description="Paise. Must not exceed the invoice's outstanding balance.")
    payment_date: date = Field(default_factory=date.today)
    mode: Literal["cash", "bank"]
    reference: str | None = Field(default=None, max_length=100, description="Cheque / UPI / bank reference")


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    invoice_id: int
    amount: int
    payment_date: date
    mode: str
    reference: str | None
    balance_due_after: int | None = None  # filled on creation only
