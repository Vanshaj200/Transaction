from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

InvoiceType = Literal["sale", "purchase"]


class InvoiceLineIn(BaseModel):
    item_id: int
    quantity: int = Field(gt=0, le=1_000_000, description="Whole units")
    unit_price: int | None = Field(
        default=None, ge=0,
        description="Paise per unit. Optional for sales (defaults to the item's selling price); required for purchases.",
    )


class InvoiceCreate(BaseModel):
    invoice_type: InvoiceType
    party_id: int
    invoice_date: date = Field(default_factory=date.today)
    lines: list[InvoiceLineIn] = Field(min_length=1, max_length=200)


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class InvoiceLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    position: int
    item_id: int
    description: str
    hsn: str
    quantity: int
    unit_price: int
    gst_rate_bp: int
    taxable_value: int
    cgst: int
    sgst: int
    igst: int
    line_total: int


class InvoiceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    invoice_no: str
    invoice_type: InvoiceType
    party_id: int
    party_name: str | None = None
    invoice_date: date
    status: Literal["posted", "cancelled"]
    supply_type: Literal["intra", "inter"]
    taxable_total: int
    cgst_total: int
    sgst_total: int
    igst_total: int
    grand_total: int
    amount_paid: int = 0
    balance_due: int = 0


class InvoiceOut(InvoiceSummary):
    place_of_supply: str
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None
    lines: list[InvoiceLineOut]
