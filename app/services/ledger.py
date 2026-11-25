"""Double-entry posting.

Rule 4 of the spec: every journal entry must have total debits == total credits.
It is enforced in three places:
  1. post_entry() -> validate_lines()   (application code, runs before anything is written)
  2. a deferred constraint trigger on PostgreSQL (migration 0002) re-checks at COMMIT
  3. tests/test_ledger.py
All amounts are integer paise, so equality is exact (no float tolerance anywhere).
"""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.errors import LedgerError, UnbalancedEntry
from app.models import Account, Invoice, JournalEntry, JournalLine
from app.services import accounts as acc


@dataclass(frozen=True)
class PostingLine:
    account_code: str
    debit: int = 0
    credit: int = 0


def validate_lines(lines: list[PostingLine]) -> None:
    if len(lines) < 2:
        raise UnbalancedEntry("A journal entry needs at least two lines")
    for ln in lines:
        if ln.debit < 0 or ln.credit < 0:
            raise UnbalancedEntry(f"Negative amount on account {ln.account_code}")
        if (ln.debit > 0) == (ln.credit > 0):  # both set, or both zero
            raise UnbalancedEntry(f"Line on account {ln.account_code} must be exactly one of debit or credit")
    debit, credit = sum(ln.debit for ln in lines), sum(ln.credit for ln in lines)
    if debit != credit:
        raise UnbalancedEntry(f"Unbalanced entry: debits {debit} != credits {credit}")


def post_entry(
    db: Session, *, entry_date: date, description: str, lines: list[PostingLine],
    invoice_id: int | None = None, payment_id: int | None = None, reverses_entry_id: int | None = None,
) -> JournalEntry:
    """Validate and insert one balanced journal entry (in the caller's transaction)."""
    validate_lines(lines)
    codes = {ln.account_code for ln in lines}
    accounts = {a.code: a.id for a in db.scalars(select(Account).where(Account.code.in_(codes)))}
    if codes - accounts.keys():
        raise LedgerError(f"Missing chart-of-accounts rows: {sorted(codes - accounts.keys())}")
    entry = JournalEntry(
        entry_date=entry_date, description=description, invoice_id=invoice_id,
        payment_id=payment_id, reverses_entry_id=reverses_entry_id,
        lines=[JournalLine(account_id=accounts[ln.account_code], debit=ln.debit, credit=ln.credit) for ln in lines],
    )
    db.add(entry)
    db.flush()
    return entry


# ------------------------------------------------------------------ posting rules
def invoice_posting_lines(inv: Invoice) -> list[PostingLine]:
    """
    SALE                                   PURCHASE
      Dr Receivables   grand_total           Dr Purchases       taxable
      Cr Sales         taxable               Dr Input CGST/SGST/IGST  (each, if non-zero)
      Cr CGST/SGST/IGST Payable (if > 0)     Cr Payables        grand_total
    Because grand_total = taxable + cgst + sgst + igst (a DB CHECK), both sides always match.
    """
    if inv.invoice_type == "sale":
        lines = [
            PostingLine(acc.RECEIVABLES, debit=inv.grand_total),
            PostingLine(acc.SALES, credit=inv.taxable_total),
            PostingLine(acc.CGST_PAYABLE, credit=inv.cgst_total),
            PostingLine(acc.SGST_PAYABLE, credit=inv.sgst_total),
            PostingLine(acc.IGST_PAYABLE, credit=inv.igst_total),
        ]
    else:
        lines = [
            PostingLine(acc.PURCHASES, debit=inv.taxable_total),
            PostingLine(acc.INPUT_CGST, debit=inv.cgst_total),
            PostingLine(acc.INPUT_SGST, debit=inv.sgst_total),
            PostingLine(acc.INPUT_IGST, debit=inv.igst_total),
            PostingLine(acc.PAYABLES, credit=inv.grand_total),
        ]
    return [ln for ln in lines if ln.debit or ln.credit]  # skip zero tax lines (a line must be non-zero)


def post_invoice(db: Session, inv: Invoice) -> JournalEntry:
    return post_entry(
        db, entry_date=inv.invoice_date, description=f"{inv.invoice_type.title()} invoice {inv.invoice_no}",
        lines=invoice_posting_lines(inv), invoice_id=inv.id,
    )


def payment_posting_lines(invoice_type: str, mode: str, amount: int) -> list[PostingLine]:
    """Sale payment:     Dr Cash/Bank, Cr Receivables.   Purchase payment: Dr Payables, Cr Cash/Bank."""
    money = acc.CASH if mode == "cash" else acc.BANK
    if invoice_type == "sale":
        return [PostingLine(money, debit=amount), PostingLine(acc.RECEIVABLES, credit=amount)]
    return [PostingLine(acc.PAYABLES, debit=amount), PostingLine(money, credit=amount)]


# ------------------------------------------------------------------ reversal (cancellation)
def reverse_invoice_entry(db: Session, inv: Invoice, entry_date: date) -> JournalEntry:
    """Post the mirror image of the invoice's original entry: every debit becomes a credit and
    vice versa. The original is never edited or deleted, so the audit trail stays intact."""
    original = db.scalar(
        select(JournalEntry)
        .where(JournalEntry.invoice_id == inv.id, JournalEntry.reverses_entry_id.is_(None))
        .options(selectinload(JournalEntry.lines))
    )
    if original is None:
        raise LedgerError(f"Invoice {inv.invoice_no} has no journal entry to reverse")
    codes = {a.id: a.code for a in db.scalars(select(Account))}
    mirrored = [PostingLine(codes[ln.account_id], debit=ln.credit, credit=ln.debit) for ln in original.lines]
    return post_entry(
        db, entry_date=entry_date, description=f"Reversal of {inv.invoice_no} (cancelled)",
        lines=mirrored, invoice_id=inv.id, reverses_entry_id=original.id,
    )
