"""Measure report speed with and without the performance indexes (EXPLAIN ANALYZE, PostgreSQL only).

    python -m scripts.seed --reset --invoices 10000
    python -m scripts.bench_indexes [--random-page-cost 1.1]

For each report it drops the performance indexes, ANALYZEs, times the exact SQL the API runs, then
re-creates the indexes (they are always restored, even on error) and times it again.
Numbers are the median of RUNS executions of EXPLAIN (ANALYZE): server-side execution time.
"""
import json
import statistics
import sys
import time
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql

from app.database import SessionLocal, engine
from app.models import Base, Invoice, InvoiceLine, Payment
from app.services import reports

RUNS = 9
RANDOM_PAGE_COST = None  # set by --random-page-cost (default: leave PostgreSQL's default of 4.0)
PERF_INDEXES = [  # (table, index name): the secondary indexes added purely for speed
    ("invoices", "ix_invoices_type_status_date"),
    ("invoices", "ix_invoices_party_status"),
    ("invoice_lines", "ix_invoice_lines_invoice"),
    ("payments", "ix_payments_invoice"),
]
QUERIES = {
    "GST summary, 1 month": lambda: reports.gst_summary_stmt(date(2025, 10, 1), date(2025, 10, 31)),
    "GST summary, full year": lambda: reports.gst_summary_stmt(date(2025, 4, 1), date(2026, 3, 31)),
    "Party outstanding": lambda: reports.party_outstanding_stmt(),
    "Trial balance": lambda: reports.trial_balance_stmt(),
    "Trial balance as of 30-Sep": lambda: reports.trial_balance_stmt(date(2025, 9, 30)),
}


def point_queries(conn) -> dict:
    """Single-row lookups the API does constantly (invoice detail, paid amount, a party's invoices).
    Ids are picked from the data: a mid-table invoice that has payments, and the busiest customer."""
    inv_id = conn.execute(text("SELECT invoice_id FROM payments ORDER BY invoice_id OFFSET (SELECT count(*)/2 FROM payments) LIMIT 1")).scalar()
    party_id = conn.execute(text("SELECT party_id FROM invoices WHERE invoice_type='sale' GROUP BY party_id ORDER BY count(*) DESC LIMIT 1")).scalar()
    return {
        "Lines of one invoice": lambda: select(InvoiceLine).where(InvoiceLine.invoice_id == inv_id),
        "Paid amount of one invoice": lambda: select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.invoice_id == inv_id),
        "Newest 100 invoices of a party": lambda: (
            select(Invoice).where(Invoice.party_id == party_id, Invoice.status == "posted")
            .order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).limit(100)),
        "Newest 100 sales in one month": lambda: (
            select(Invoice).where(Invoice.invoice_type == "sale", Invoice.status == "posted",
                                  Invoice.invoice_date >= date(2025, 10, 1), Invoice.invoice_date <= date(2025, 10, 31))
            .order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).limit(100)),
    }


def index_obj(table: str, name: str):
    return next(i for i in Base.metadata.tables[table].indexes if i.name == name)


def sql_of(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def explain(conn, sql: str):
    plan = conn.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON) {sql}")).scalar()
    if isinstance(plan, str):
        plan = json.loads(plan)
    return plan[0]["Execution Time"], plan[0]["Plan"]


def node_types(plan) -> set[str]:
    found = {plan["Node Type"]}
    for child in plan.get("Plans", []):
        found |= node_types(child)
    return found


def vacuum() -> None:
    """VACUUM ANALYZE: fresh planner statistics AND an up-to-date visibility map (index-only scans need it)."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM ANALYZE"))


def measure() -> dict[str, tuple[float, float, str]]:
    result = {}
    vacuum()
    with engine.connect() as conn:
        if RANDOM_PAGE_COST:
            conn.execute(text(f"SET random_page_cost = {float(RANDOM_PAGE_COST)}"))
        for label, build in {**QUERIES, **point_queries(conn)}.items():
            sql = sql_of(build())
            explain(conn, sql)                                       # warm-up (cache)
            times, plan = [], None
            for _ in range(RUNS):
                ms, plan = explain(conn, sql)
                times.append(ms)
            wall = []
            for _ in range(RUNS):
                with SessionLocal() as db:
                    if RANDOM_PAGE_COST:
                        db.execute(text(f"SET random_page_cost = {float(RANDOM_PAGE_COST)}"))
                    t0 = time.perf_counter()
                    db.execute(text(sql)).all()
                    wall.append((time.perf_counter() - t0) * 1000)
            scans = ", ".join(sorted(n for n in node_types(plan) if "Scan" in n))
            result[label] = (statistics.median(times), statistics.median(wall), scans)
    return result


def main() -> int:
    global RANDOM_PAGE_COST
    if "--random-page-cost" in sys.argv:       # e.g. 1.1 for SSD storage; default PostgreSQL value is 4.0
        RANDOM_PAGE_COST = sys.argv[sys.argv.index("--random-page-cost") + 1]
    print("random_page_cost:", RANDOM_PAGE_COST or "PostgreSQL default (4.0)")
    if engine.dialect.name != "postgresql":
        print("PostgreSQL only")
        return 1
    with engine.connect() as conn:
        rows = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in
                ("invoices", "invoice_lines", "journal_entries", "journal_lines", "payments")}
    print("rows:", rows)
    after = measure()                                                # indexes currently present
    try:
        with engine.begin() as conn:
            for table, name in PERF_INDEXES:
                conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
        before = measure()
    finally:
        with engine.begin() as conn:                                 # always restore the indexes
            for table, name in PERF_INDEXES:
                index_obj(table, name).create(conn, checkfirst=True)
        vacuum()
    print(f"\n{'query':<32}{'before (ms)':>12}{'after (ms)':>12}{'speed-up':>10}   plan after")
    for label in before:
        b, a = before[label], after[label]
        print(f"{label:<32}{b[0]:>12.3f}{a[0]:>12.3f}{b[0] / a[0]:>9.1f}x   {a[2]}   (before: {b[2]})")
    print("\n(median of", RUNS, "runs of EXPLAIN ANALYZE execution time; wall-clock incl. fetch:)")
    for label in before:
        print(f"  {label:<32} before {before[label][1]:>8.2f} ms   after {after[label][1]:>8.2f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
