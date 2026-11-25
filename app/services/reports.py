"""Report queries. Each report has a *_stmt builder (also used by scripts/bench_indexes.py to run
EXPLAIN ANALYZE on exactly the SQL the API runs) and a function that shapes the result."""
from datetime import date

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from app.errors import InvalidInput
from app.models import Account, Invoice, InvoiceLine, Item, JournalEntry, JournalLine, Party, Payment, StockMovement
from app.schemas.reports import (
    GstRateRow, GstSummary, GstTotals, PartyOutstanding, PartyOutstandingRow,
    StockRow, TrialBalance, TrialBalanceRow,
)

# NB: PostgreSQL returns SUM(bigint) as NUMERIC (Decimal). Every aggregate below goes through int().


# ------------------------------------------------------------------ trial balance
def trial_balance_stmt(as_of: date | None = None) -> Select:
    lines = select(JournalLine.account_id, JournalLine.debit, JournalLine.credit)
    if as_of:
        lines = lines.join(JournalEntry, JournalEntry.id == JournalLine.entry_id).where(JournalEntry.entry_date <= as_of)
    sub = lines.subquery()
    return (
        select(
            Account.code, Account.name, Account.account_type,
            func.coalesce(func.sum(sub.c.debit), 0), func.coalesce(func.sum(sub.c.credit), 0),
        )
        .outerjoin(sub, sub.c.account_id == Account.id)
        .group_by(Account.id).order_by(Account.code)
    )


def trial_balance(db: Session, as_of: date | None = None) -> TrialBalance:
    rows = []
    for code, name, kind, debit, credit in db.execute(trial_balance_stmt(as_of)):
        debit, credit = int(debit), int(credit)
        net = debit - credit
        rows.append(TrialBalanceRow(
            code=code, name=name, account_type=kind, debit_total=debit, credit_total=credit,
            debit_balance=max(net, 0), credit_balance=max(-net, 0),
        ))
    total_debit, total_credit = sum(r.debit_total for r in rows), sum(r.credit_total for r in rows)
    return TrialBalance(
        as_of=as_of, rows=rows, total_debit=total_debit, total_credit=total_credit,
        net=total_debit - total_credit, balanced=total_debit == total_credit,
    )


# ------------------------------------------------------------------ party outstanding
def party_outstanding_stmt(party_type: str | None = None, include_settled: bool = False) -> Select:
    invoiced = (
        select(Invoice.party_id, func.sum(Invoice.grand_total).label("total"))
        .where(Invoice.status == "posted").group_by(Invoice.party_id).subquery()
    )
    paid = (
        select(Invoice.party_id, func.sum(Payment.amount).label("total"))
        .join(Payment, Payment.invoice_id == Invoice.id)
        .where(Invoice.status == "posted").group_by(Invoice.party_id).subquery()
    )
    inv_total = func.coalesce(invoiced.c.total, 0)
    paid_total = func.coalesce(paid.c.total, 0)
    stmt = (
        select(Party.id, Party.name, Party.party_type, Party.gstin, inv_total, paid_total)
        .join(invoiced, invoiced.c.party_id == Party.id)          # only parties that have invoices
        .outerjoin(paid, paid.c.party_id == Party.id)
        .order_by(Party.name, Party.id)
    )
    if party_type:
        stmt = stmt.where(Party.party_type == party_type)
    if not include_settled:
        stmt = stmt.where(inv_total != paid_total)
    return stmt


def party_outstanding(db: Session, party_type: str | None = None, include_settled: bool = False) -> PartyOutstanding:
    rows = [
        PartyOutstandingRow(party_id=pid, name=name, party_type=ptype, gstin=gstin,
                            invoiced=int(inv), paid=int(pd), outstanding=int(inv) - int(pd))
        for pid, name, ptype, gstin, inv, pd in db.execute(party_outstanding_stmt(party_type, include_settled))
    ]
    return PartyOutstanding(
        rows=rows,
        total_receivable=sum(r.outstanding for r in rows if r.party_type == "customer"),
        total_payable=sum(r.outstanding for r in rows if r.party_type == "supplier"),
    )


# ------------------------------------------------------------------ stock summary
def stock_summary(db: Session) -> list[StockRow]:
    def qty_where(*reasons: str):
        return func.coalesce(func.sum(case((StockMovement.reason.in_(reasons), StockMovement.qty_change), else_=0)), 0)

    moves = (
        select(
            StockMovement.item_id,
            qty_where("opening").label("opening"),
            qty_where("purchase", "purchase_cancel").label("purchased"),
            qty_where("sale", "sale_cancel").label("sold"),      # negative: stock going out
        )
        .group_by(StockMovement.item_id).subquery()
    )
    stmt = (
        select(Item, moves.c.opening, moves.c.purchased, moves.c.sold)
        .outerjoin(moves, moves.c.item_id == Item.id).order_by(Item.name, Item.id)
    )
    out = []
    for item, opening, purchased, sold in db.execute(stmt):
        opening, purchased, sold = int(opening or 0), int(purchased or 0), int(sold or 0)
        out.append(StockRow(
            item_id=item.id, name=item.name, hsn=item.hsn, unit=item.unit, is_active=item.is_active,
            opening=opening, purchased=purchased, sold=-sold, on_hand=item.stock_qty,
            ledger_consistent=item.stock_qty == opening + purchased + sold,
        ))
    return out


# ------------------------------------------------------------------ GST summary
def gst_summary_stmt(date_from: date, date_to: date) -> Select:
    return (
        select(
            Invoice.invoice_type, InvoiceLine.gst_rate_bp, func.count(),
            func.sum(InvoiceLine.taxable_value), func.sum(InvoiceLine.cgst),
            func.sum(InvoiceLine.sgst), func.sum(InvoiceLine.igst),
        )
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(Invoice.status == "posted", Invoice.invoice_date >= date_from, Invoice.invoice_date <= date_to)
        .group_by(Invoice.invoice_type, InvoiceLine.gst_rate_bp)
        .order_by(Invoice.invoice_type.desc(), InvoiceLine.gst_rate_bp)   # sales first
    )


def gst_summary(db: Session, date_from: date, date_to: date) -> GstSummary:
    if date_to < date_from:
        raise InvalidInput("date_to must not be before date_from")
    rows, totals = [], {"sale": GstTotals(), "purchase": GstTotals()}
    for kind, rate, count, taxable, cgst, sgst, igst in db.execute(gst_summary_stmt(date_from, date_to)):
        taxable, cgst, sgst, igst = int(taxable), int(cgst), int(sgst), int(igst)
        rows.append(GstRateRow(invoice_type=kind, gst_rate_bp=rate, line_count=count, taxable_value=taxable,
                               cgst=cgst, sgst=sgst, igst=igst, total_tax=cgst + sgst + igst))
        t = totals[kind]
        t.taxable_value += taxable; t.cgst += cgst; t.sgst += sgst; t.igst += igst  # noqa: E702
        t.total_tax += cgst + sgst + igst
    return GstSummary(
        date_from=date_from, date_to=date_to, rows=rows, sales=totals["sale"], purchases=totals["purchase"],
        net_tax_payable=totals["sale"].total_tax - totals["purchase"].total_tax,
    )
