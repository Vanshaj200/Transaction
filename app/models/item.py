from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Item(Base):
    """A stock item. Money is integer paise; GST rate is integer basis points (1800 = 18%)."""

    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint("gst_rate_bp >= 0 AND gst_rate_bp <= 10000", name="gst_rate_range"),
        CheckConstraint("selling_price >= 0", name="price_nonneg"),
        # Last line of defence against overselling: even a bug in the app cannot make stock negative.
        CheckConstraint("stock_qty >= 0", name="stock_nonneg"),
        # Item search by name in the UI / list endpoint.
        Index("ix_items_name", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    hsn: Mapped[str] = mapped_column(String(8))
    gst_rate_bp: Mapped[int] = mapped_column(Integer)
    unit: Mapped[str] = mapped_column(String(10), default="NOS")
    selling_price: Mapped[int] = mapped_column(BigInteger, default=0)  # paise per unit
    # Denormalised running total. Invariant: stock_qty == SUM(stock_movements.qty_change).
    stock_qty: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # soft delete
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
