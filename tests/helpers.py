"""Query helpers shared by the accounting tests."""
from sqlalchemy import func, select, text

from app.models import Account, JournalEntry, JournalLine


def totals(db) -> tuple[int, int]:
    """(total debits, total credits) over the whole journal."""
    d, c = db.execute(select(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))).one()
    return int(d), int(c)


def unbalanced_entries(db) -> list:
    """Entries whose own debits != credits (must always be empty)."""
    stmt = (
        select(JournalLine.entry_id)
        .group_by(JournalLine.entry_id)
        .having(func.sum(JournalLine.debit) != func.sum(JournalLine.credit))
    )
    return list(db.scalars(stmt))


def balance(db, code: str) -> int:
    """debit - credit on one account."""
    stmt = (
        select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
        .join(Account, Account.id == JournalLine.account_id).where(Account.code == code)
    )
    return int(db.scalar(stmt))


def entries_for(db, invoice_id: int) -> list[JournalEntry]:
    return list(db.scalars(select(JournalEntry).where(JournalEntry.invoice_id == invoice_id).order_by(JournalEntry.id)))


def lines_of(db, entry_id: int) -> dict[str, tuple[int, int]]:
    rows = db.execute(
        select(Account.code, JournalLine.debit, JournalLine.credit)
        .join(Account, Account.id == JournalLine.account_id).where(JournalLine.entry_id == entry_id)
    ).all()
    return {code: (int(d), int(c)) for code, d, c in rows}
