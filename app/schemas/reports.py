from datetime import date

from pydantic import BaseModel


class TrialBalanceRow(BaseModel):
    code: str
    name: str
    account_type: str
    debit_total: int
    credit_total: int
    debit_balance: int   # shown in the Dr column when the account's net is a debit
    credit_balance: int  # ... or in the Cr column when it is a credit


class TrialBalance(BaseModel):
    as_of: date | None
    rows: list[TrialBalanceRow]
    total_debit: int
    total_credit: int
    net: int         # total_debit - total_credit; ALWAYS 0 in a correct double-entry ledger
    balanced: bool


class PartyOutstandingRow(BaseModel):
    party_id: int
    name: str
    party_type: str
    gstin: str | None
    invoiced: int
    paid: int
    outstanding: int


class PartyOutstanding(BaseModel):
    rows: list[PartyOutstandingRow]
    total_receivable: int  # customers owe us
    total_payable: int     # we owe suppliers


class StockRow(BaseModel):
    item_id: int
    name: str
    hsn: str
    unit: str
    is_active: bool
    opening: int
    purchased: int
    sold: int
    on_hand: int
    ledger_consistent: bool  # on_hand == SUM(stock_movements)


class GstRateRow(BaseModel):
    invoice_type: str
    gst_rate_bp: int
    line_count: int
    taxable_value: int
    cgst: int
    sgst: int
    igst: int
    total_tax: int


class GstTotals(BaseModel):
    taxable_value: int = 0
    cgst: int = 0
    sgst: int = 0
    igst: int = 0
    total_tax: int = 0


class GstSummary(BaseModel):
    date_from: date
    date_to: date
    rows: list[GstRateRow]
    sales: GstTotals       # output tax collected
    purchases: GstTotals   # input tax credit
    net_tax_payable: int   # sales tax - purchase tax (negative = refundable/carry-forward)
