from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

STOCK_REASONS = ("opening", "sale", "purchase", "sale_cancel", "purchase_cancel")


class StockMovement(Base):
    """Append-only stock ledger: every change to items.stock_qty has exactly one row here."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint("qty_change <> 0", name="nonzero"),
        CheckConstraint(
            "reason IN ('opening','sale','purchase','sale_cancel','purchase_cancel')", name="reason_valid"
        ),
        # Stock card of one item (history, and reconciling SUM(qty_change) with items.stock_qty).
        Index("ix_stock_movements_item", "item_id"),
        # "Which movements did invoice X cause" (used when cancelling and in audits).
        Index("ix_stock_movements_invoice", "invoice_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty_change: Mapped[int] = mapped_column(Integer)  # negative = stock out
    reason: Mapped[str] = mapped_column(String(20))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))  # NULL for opening stock
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
