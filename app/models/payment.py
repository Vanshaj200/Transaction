from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Payment(Base):
    """Money received (sale) or paid (purchase) against one invoice."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("mode IN ('cash','bank')", name="mode_valid"),
        # Outstanding of an invoice = grand_total - SUM(amount); leading invoice_id plus amount
        # lets the SUM be answered from the index alone.
        Index("ix_payments_invoice_amount", "invoice_id", "amount"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    amount: Mapped[int] = mapped_column(BigInteger)  # paise
    payment_date: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(10))
    reference: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
