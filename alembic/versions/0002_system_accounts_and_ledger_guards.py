"""system chart of accounts + PostgreSQL ledger guard triggers

* Inserts the fixed chart of accounts the posting code depends on. The list is copied here on
  purpose: a migration must keep producing the same result even if app code changes later.
* On PostgreSQL only (SQLite has no deferred constraint triggers) installs:
    - a DEFERRED constraint trigger: at COMMIT every journal entry touched must have
      SUM(debit) = SUM(credit)  -> rule 4 enforced by the database itself;
    - append-only guards: journal and stock-movement rows cannot be updated or deleted, and
      invoices / invoice lines cannot be deleted (rule 5: "invoices are never deleted").

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ACCOUNTS = [
    ("1000", "Cash", "asset"), ("1010", "Bank", "asset"), ("1100", "Accounts Receivable", "asset"),
    ("1200", "Input CGST", "asset"), ("1210", "Input SGST", "asset"), ("1220", "Input IGST", "asset"),
    ("2000", "Accounts Payable", "liability"), ("2100", "CGST Payable", "liability"),
    ("2110", "SGST Payable", "liability"), ("2120", "IGST Payable", "liability"),
    ("4000", "Sales", "income"), ("5000", "Purchases", "expense"),
]

BALANCE_FN = """
CREATE FUNCTION journal_entry_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    d numeric; c numeric;
BEGIN
    SELECT COALESCE(SUM(debit), 0), COALESCE(SUM(credit), 0) INTO d, c
    FROM journal_lines WHERE entry_id = NEW.entry_id;
    IF d <> c THEN
        RAISE EXCEPTION 'journal entry % is unbalanced: debit % <> credit %', NEW.entry_id, d, c
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END $$;
"""

FORBID_FN = """
CREATE FUNCTION forbid_row_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed (append-only / never deleted)', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END $$;
"""


def upgrade() -> None:
    accounts = sa.table(
        "accounts",
        sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("account_type", sa.String), sa.column("is_system", sa.Boolean),
    )
    op.bulk_insert(accounts, [
        {"code": c, "name": n, "account_type": t, "is_system": True} for c, n, t in ACCOUNTS
    ])

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(BALANCE_FN)
    # DEFERRABLE INITIALLY DEFERRED: the check runs at COMMIT, after all lines of the entry are in.
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_journal_lines_balanced
        AFTER INSERT ON journal_lines DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION journal_entry_balanced()
    """)
    op.execute(FORBID_FN)
    for table in ("journal_lines", "journal_entries", "stock_movements"):
        op.execute(f"""CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table}
                       FOR EACH ROW EXECUTE FUNCTION forbid_row_change()""")
    for table in ("invoices", "invoice_lines"):
        op.execute(f"""CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table}
                       FOR EACH ROW EXECUTE FUNCTION forbid_row_change()""")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in ("journal_lines", "journal_entries", "stock_movements"):
            op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
        for table in ("invoices", "invoice_lines"):
            op.execute(f"DROP TRIGGER trg_{table}_no_delete ON {table}")
        op.execute("DROP FUNCTION forbid_row_change()")
        op.execute("DROP TRIGGER trg_journal_lines_balanced ON journal_lines")
        op.execute("DROP FUNCTION journal_entry_balanced()")
    op.execute("DELETE FROM accounts WHERE is_system")
