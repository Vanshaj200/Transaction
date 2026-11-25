from datetime import date, datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Invoice(Base):
    """A posted sale or purchase invoice. Never deleted; cancelling flips status and posts
    a reversing journal entry. All amounts are integer paise."""

    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("invoice_type IN ('sale','purchase')", name="type_valid"),
        CheckConstraint("status IN ('posted','cancelled')", name="status_valid"),
        CheckConstraint("supply_type IN ('intra','inter')", name="supply_valid"),
        # Intra-state supplies carry CGST+SGST only; inter-state carry IGST only.
        CheckConstraint(
            "(supply_type = 'intra' AND igst_total = 0) OR (supply_type = 'inter' AND cgst_total = 0 AND sgst_total = 0)",
            name="tax_matches_supply",
        ),
        CheckConstraint(
            "grand_total = taxable_total + cgst_total + sgst_total + igst_total", name="totals_add_up"
        ),
        # GST summary / list by period: equality on type+status, then a date range scan.
        Index("ix_invoices_type_status_date", "invoice_type", "status", "invoice_date"),
        # Party outstanding and "invoices of this party": filter by party (+ status).
        Index("ix_invoices_party_status", "party_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_no: Mapped[str] = mapped_column(String(30), unique=True)  # unique index: lookup by number
    invoice_type: Mapped[str] = mapped_column(String(10))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    invoice_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="posted")
    place_of_supply: Mapped[str] = mapped_column(String(2))  # party state at the time of invoice
    supply_type: Mapped[str] = mapped_column(String(5))
    taxable_total: Mapped[int] = mapped_column(BigInteger)
    cgst_total: Mapped[int] = mapped_column(BigInteger, default=0)
    sgst_total: Mapped[int] = mapped_column(BigInteger, default=0)
    igst_total: Mapped[int] = mapped_column(BigInteger, default=0)
    grand_total: Mapped[int] = mapped_column(BigInteger)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", order_by="InvoiceLine.position", cascade="save-update"
    )


class InvoiceLine(Base):
    """One line. Item name/HSN/rate are copied (snapshot) so later master-data edits
    never change a historical invoice."""

    __tablename__ = "invoice_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="qty_positive"),
        CheckConstraint("unit_price >= 0", name="price_nonneg"),
        CheckConstraint("taxable_value = quantity * unit_price", name="taxable_is_qty_x_price"),
        CheckConstraint(
            "line_total = taxable_value + cgst + sgst + igst", name="total_adds_up"
        ),
        # Covering index for the GST summary: join from invoices by invoice_id, group by rate,
        # and sum the amounts straight from the index (index-only scan, no heap visits).
        # Also serves "fetch the lines of invoice X" because invoice_id is the leading column.
        Index(
            "ix_invoice_lines_invoice_rate_amounts",
            "invoice_id", "gst_rate_bp", "taxable_value", "cgst", "sgst", "igst",
        ),
        # Postgres does not index foreign keys automatically; needed for "sales of item X".
        Index("ix_invoice_lines_item", "item_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    position: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    description: Mapped[str] = mapped_column(String(200))
    hsn: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[int] = mapped_column(BigInteger)
    gst_rate_bp: Mapped[int] = mapped_column(Integer)
    taxable_value: Mapped[int] = mapped_column(BigInteger)
    cgst: Mapped[int] = mapped_column(BigInteger, default=0)
    sgst: Mapped[int] = mapped_column(BigInteger, default=0)
    igst: Mapped[int] = mapped_column(BigInteger, default=0)
    line_total: Mapped[int] = mapped_column(BigInteger)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
