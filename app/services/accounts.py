"""The fixed chart of accounts the posting code relies on."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account

# Codes used by the ledger service. Keeping them as constants avoids typos in posting rules.
CASH = "1000"
BANK = "1010"
RECEIVABLES = "1100"
INPUT_CGST = "1200"
INPUT_SGST = "1210"
INPUT_IGST = "1220"
PAYABLES = "2000"
CGST_PAYABLE = "2100"
SGST_PAYABLE = "2110"
IGST_PAYABLE = "2120"
SALES = "4000"
PURCHASES = "5000"

# (code, name, type)
SYSTEM_ACCOUNTS = [
    (CASH, "Cash", "asset"),
    (BANK, "Bank", "asset"),
    (RECEIVABLES, "Accounts Receivable", "asset"),
    (INPUT_CGST, "Input CGST", "asset"),
    (INPUT_SGST, "Input SGST", "asset"),
    (INPUT_IGST, "Input IGST", "asset"),
    (PAYABLES, "Accounts Payable", "liability"),
    (CGST_PAYABLE, "CGST Payable", "liability"),
    (SGST_PAYABLE, "SGST Payable", "liability"),
    (IGST_PAYABLE, "IGST Payable", "liability"),
    (SALES, "Sales", "income"),
    (PURCHASES, "Purchases", "expense"),
]


def seed_system_accounts(db: Session) -> None:
    """Insert any missing system accounts (idempotent). Used by tests on SQLite; on PostgreSQL the
    same rows are inserted by Alembic migration 0002."""
    existing = set(db.scalars(select(Account.code)))
    for code, name, kind in SYSTEM_ACCOUNTS:
        if code not in existing:
            db.add(Account(code=code, name=name, account_type=kind, is_system=True))
    db.flush()
