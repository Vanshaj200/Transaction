from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Party(Base):
    """A customer or a supplier. state_code (2-digit GST state code) drives CGST/SGST vs IGST."""

    __tablename__ = "parties"
    __table_args__ = (
        CheckConstraint("party_type IN ('customer','supplier')", name="party_type_valid"),
        CheckConstraint("length(state_code) = 2", name="state_code_len"),
        # Party list screens filter by type and search/sort by name.
        Index("ix_parties_type_name", "party_type", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    party_type: Mapped[str] = mapped_column(String(10))
    # Nullable: unregistered (B2C) customers have no GSTIN. UNIQUE (NULLs allowed many times)
    # also gives the index used to look a party up by GSTIN.
    gstin: Mapped[str | None] = mapped_column(String(15), unique=True)
    state_code: Mapped[str] = mapped_column(String(2))
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # soft delete
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
