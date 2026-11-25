from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import any_user
from app.schemas.party import PartyType
from app.schemas.reports import GstSummary, PartyOutstanding, StockRow, TrialBalance
from app.services import reports

router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(any_user)])


@router.get("/trial-balance", response_model=TrialBalance)
def trial_balance(as_of: date | None = None, db: Session = Depends(get_db)):
    """Debit/credit totals per account. `net` must always be 0."""
    return reports.trial_balance(db, as_of)


@router.get("/party-outstanding", response_model=PartyOutstanding)
def party_outstanding(party_type: PartyType | None = None, include_settled: bool = False, db: Session = Depends(get_db)):
    """Invoiced minus paid, per party (posted invoices only)."""
    return reports.party_outstanding(db, party_type, include_settled)


@router.get("/stock-summary", response_model=list[StockRow])
def stock_summary(db: Session = Depends(get_db)):
    return reports.stock_summary(db)


@router.get("/gst-summary", response_model=GstSummary)
def gst_summary(date_from: date, date_to: date, db: Session = Depends(get_db)):
    """Taxable value and CGST/SGST/IGST grouped by GST rate and invoice type for a period (cancelled invoices excluded)."""
    return reports.gst_summary(db, date_from, date_to)
