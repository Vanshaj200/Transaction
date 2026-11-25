from sqlalchemy import Boolean, CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ACCOUNT_TYPES = ("asset", "liability", "income", "expense")


class Account(Base):
    """Chart-of-accounts row. Asset/expense accounts normally carry debit balances,
    liability/income accounts credit balances."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("account_type IN ('asset','liability','income','expense')", name="type_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)  # e.g. 1100; unique index used for code lookups
    name: Mapped[str] = mapped_column(String(100), unique=True)
    account_type: Mapped[str] = mapped_column(String(10))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
