"""GST arithmetic. Pure functions, integers only, no database access: easy to test and explain.

Units: money = paise (1 rupee = 100 paise); rates = basis points (1800 bp = 18%).
"""
from dataclasses import dataclass

# Statutory GST slabs, in basis points (0.25% is the rate on rough diamonds).
ALLOWED_RATES_BP = (0, 25, 300, 500, 1200, 1800, 2800)


def round_div(numerator: int, denominator: int) -> int:
    """Integer division rounded half-up (e.g. 49.5 -> 50). Operands are non-negative, so no
    float and no banker's rounding is involved."""
    return (2 * numerator + denominator) // (2 * denominator)


def supply_type(company_state: str, party_state: str) -> str:
    """Same state -> 'intra' (CGST + SGST); different state -> 'inter' (IGST)."""
    return "intra" if company_state == party_state else "inter"


@dataclass(frozen=True)
class LineTax:
    taxable: int
    cgst: int
    sgst: int
    igst: int

    @property
    def tax(self) -> int:
        return self.cgst + self.sgst + self.igst

    @property
    def total(self) -> int:
        return self.taxable + self.tax


def compute_line_tax(quantity: int, unit_price: int, rate_bp: int, supply: str) -> LineTax:
    """Tax for ONE invoice line, rounded on that line.

    taxable = quantity x unit price (exact integer).
    Intra-state: half the rate goes to CGST and half to SGST; each is rounded separately, so
    CGST == SGST always. Inter-state: the whole rate goes to IGST.
    """
    taxable = quantity * unit_price
    if supply == "intra":
        half = round_div(taxable * rate_bp, 20_000)  # rate_bp/2 as a percentage of taxable: /10_000/2
        return LineTax(taxable, cgst=half, sgst=half, igst=0)
    return LineTax(taxable, cgst=0, sgst=0, igst=round_div(taxable * rate_bp, 10_000))
