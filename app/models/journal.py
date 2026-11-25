from datetime import date, datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, String, UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class JournalEntry(Base):
    """Header of a double-entry posting. Invariant (enforced in app/services/ledger.py and by a
    deferred trigger on PostgreSQL): SUM(debit) == SUM(credit) over its lines."""

    __tablename__ = "journal_entries"
    __table_args__ = (
        CheckConstraint("NOT (invoice_id IS NOT NULL AND payment_id IS NOT NULL)", name="single_source"),
        # An entry can be reversed at most once (also stops two concurrent cancels both posting).
        UniqueConstraint("reverses_entry_id"),
        # One posting entry per payment.
        UniqueConstraint("payment_id"),
        # Exactly one ORIGINAL (non-reversal) entry per invoice; reversals are excluded by the predicate.
        Index(
            "uq_journal_entries_original_per_invoice", "invoice_id", unique=True,
            postgresql_where=text("reverses_entry_id IS NULL AND invoice_id IS NOT NULL"),
            sqlite_where=text("reverses_entry_id IS NULL AND invoice_id IS NOT NULL"),
        ),
        # Find the entries (original + reversal) of an invoice, e.g. when cancelling.
        Index("ix_journal_entries_invoice", "invoice_id"),
        # Trial balance "as of date" filters on entry_date.
        Index("ix_journal_entries_date", "entry_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(255))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"))
    reverses_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list["JournalLine"]] = relationship(back_populates="entry", cascade="save-update")


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("debit >= 0 AND credit >= 0", name="nonneg"),
        # A line is either a debit or a credit, never both and never empty.
        CheckConstraint("(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)", name="one_side"),
        # Fetch / sum the lines of one entry (balance check, reversal).
        Index("ix_journal_lines_entry", "entry_id"),
        # Trial balance: GROUP BY account summing debit & credit; including both amounts makes it
        # an index-only scan instead of reading every heap row.
        Index("ix_journal_lines_account_amounts", "account_id", "debit", "credit"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    debit: Mapped[int] = mapped_column(BigInteger, default=0)
    credit: Mapped[int] = mapped_column(BigInteger, default=0)

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
