"""Audit the database. Exit code 1 if any check fails.

    python -m scripts.check_integrity
"""
import sys

from sqlalchemy import text

from app.database import engine

CHECKS = [
    ("journal entries whose debits != credits",
     """SELECT count(*) FROM (SELECT entry_id FROM journal_lines GROUP BY entry_id
                            HAVING sum(debit) <> sum(credit)) t"""),
    ("trial balance net (total debits - total credits)",
     "SELECT coalesce(sum(debit),0) - coalesce(sum(credit),0) FROM journal_lines"),
    ("journal lines that are not exactly one-sided",
     "SELECT count(*) FROM journal_lines WHERE NOT ((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0))"),
    ("invoices without exactly one original journal entry",
     """SELECT count(*) FROM invoices i WHERE
        (SELECT count(*) FROM journal_entries e WHERE e.invoice_id = i.id AND e.reverses_entry_id IS NULL) <> 1"""),
    ("cancelled invoices without a reversal entry (or posted ones with one)",
     """SELECT count(*) FROM invoices i WHERE
        (i.status = 'cancelled') <> EXISTS (SELECT 1 FROM journal_entries e WHERE e.invoice_id = i.id AND e.reverses_entry_id IS NOT NULL)"""),
    ("invoice header totals != sum of their lines",
     """SELECT count(*) FROM invoices i JOIN (SELECT invoice_id, sum(taxable_value) t, sum(cgst) c, sum(sgst) s, sum(igst) g, sum(line_total) l
        FROM invoice_lines GROUP BY invoice_id) x ON x.invoice_id = i.id
        WHERE i.taxable_total <> x.t OR i.cgst_total <> x.c OR i.sgst_total <> x.s OR i.igst_total <> x.g OR i.grand_total <> x.l"""),
    ("receivables control account - sum of customer outstanding",
     """SELECT coalesce((SELECT sum(jl.debit - jl.credit) FROM journal_lines jl JOIN accounts a ON a.id = jl.account_id WHERE a.code = '1100'), 0)
        - coalesce((SELECT sum(i.grand_total) FROM invoices i WHERE i.status = 'posted' AND i.invoice_type = 'sale'), 0)
        + coalesce((SELECT sum(p.amount) FROM payments p JOIN invoices i ON i.id = p.invoice_id WHERE i.status = 'posted' AND i.invoice_type = 'sale'), 0)"""),
    ("payables control account - sum of supplier outstanding",
     """SELECT coalesce((SELECT sum(jl.credit - jl.debit) FROM journal_lines jl JOIN accounts a ON a.id = jl.account_id WHERE a.code = '2000'), 0)
        - coalesce((SELECT sum(i.grand_total) FROM invoices i WHERE i.status = 'posted' AND i.invoice_type = 'purchase'), 0)
        + coalesce((SELECT sum(p.amount) FROM payments p JOIN invoices i ON i.id = p.invoice_id WHERE i.status = 'posted' AND i.invoice_type = 'purchase'), 0)"""),
    ("items whose stock_qty != sum of their stock movements",
     """SELECT count(*) FROM items it WHERE it.stock_qty <> coalesce((SELECT sum(qty_change) FROM stock_movements m WHERE m.item_id = it.id), 0)"""),
    ("items with negative stock", "SELECT count(*) FROM items WHERE stock_qty < 0"),
    ("invoices paid more than their total",
     """SELECT count(*) FROM invoices i WHERE (SELECT coalesce(sum(amount),0) FROM payments p WHERE p.invoice_id = i.id) > i.grand_total"""),
]

SUMMARY = [
    ("invoices", "SELECT count(*) FROM invoices"),
    ("  of which cancelled", "SELECT count(*) FROM invoices WHERE status = 'cancelled'"),
    ("invoice lines", "SELECT count(*) FROM invoice_lines"),
    ("payments", "SELECT count(*) FROM payments"),
    ("journal entries", "SELECT count(*) FROM journal_entries"),
    ("journal lines", "SELECT count(*) FROM journal_lines"),
    ("stock movements", "SELECT count(*) FROM stock_movements"),
    ("total debits (paise)", "SELECT coalesce(sum(debit),0) FROM journal_lines"),
    ("total credits (paise)", "SELECT coalesce(sum(credit),0) FROM journal_lines"),
]


def main() -> int:
    failed = 0
    with engine.connect() as conn:
        print("== data size ==")
        for label, sql in SUMMARY:
            print(f"  {label:<28}{int(conn.execute(text(sql)).scalar()):>16,}")
        print("== checks (every value must be 0) ==")
        for label, sql in CHECKS:
            value = int(conn.execute(text(sql)).scalar())
            failed += value != 0
            print(f"  [{'OK' if value == 0 else 'FAIL'}] {label}: {value}")
    print("RESULT:", "ALL CHECKS PASSED" if not failed else f"{failed} CHECK(S) FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
