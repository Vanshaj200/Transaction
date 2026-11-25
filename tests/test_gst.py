"""Pure GST maths: intra vs inter-state split and per-line rounding."""
import pytest

from app.services.gst import compute_line_tax, round_div, supply_type


def test_same_state_is_intra_different_is_inter():
    assert supply_type("27", "27") == "intra"
    assert supply_type("27", "29") == "inter"


def test_intra_state_splits_into_cgst_and_sgst():
    # Rs 100.00 @ 18%  ->  CGST 9.00 + SGST 9.00
    t = compute_line_tax(quantity=1, unit_price=10_000, rate_bp=1800, supply="intra")
    assert (t.taxable, t.cgst, t.sgst, t.igst, t.total) == (10_000, 900, 900, 0, 11_800)


def test_inter_state_is_igst_only():
    t = compute_line_tax(quantity=1, unit_price=10_000, rate_bp=1800, supply="inter")
    assert (t.cgst, t.sgst, t.igst, t.total) == (0, 0, 1_800, 11_800)


def test_quantity_multiplies_taxable_value():
    t = compute_line_tax(quantity=3, unit_price=2_550, rate_bp=1200, supply="inter")
    assert t.taxable == 7_650 and t.igst == 918  # 7650 * 12% = 918.00


@pytest.mark.parametrize("n,d,expected", [(1, 2, 1), (49, 100, 0), (50, 100, 1), (149, 100, 1), (150, 100, 2), (0, 7, 0)])
def test_round_div_is_half_up(n, d, expected):
    assert round_div(n, d) == expected


def test_igst_rounds_half_up_to_the_paisa():
    # 1999 paise @ 5% = 99.95 paise -> 100
    assert compute_line_tax(1, 1_999, 500, "inter").igst == 100
    # 1001 paise @ 5% = 50.05 -> 50
    assert compute_line_tax(1, 1_001, 500, "inter").igst == 50


def test_cgst_and_sgst_are_each_rounded_and_always_equal():
    # 1999 @ 2.5% each = 49.975 -> 50 each
    t = compute_line_tax(1, 1_999, 500, "intra")
    assert t.cgst == t.sgst == 50
    # 1010 @ 2.5% each = 25.25 -> 25 each (total 50) while IGST on the same value is 50.5 -> 51:
    # the two regimes may differ by a paisa because each component is rounded on its own.
    assert compute_line_tax(1, 1_010, 500, "intra").tax == 50
    assert compute_line_tax(1, 1_010, 500, "inter").tax == 51


def test_zero_rate_has_no_tax():
    t = compute_line_tax(5, 1_000, 0, "intra")
    assert t.tax == 0 and t.total == 5_000


def test_rounding_happens_per_line_not_on_the_invoice_total():
    # Three lines of 33 paise @ 5% inter: each 1.65 paise -> 2, so 6 total.
    # Rounding the grand total instead (99 * 5% = 4.95 -> 5) would give a different answer.
    per_line = sum(compute_line_tax(1, 33, 500, "inter").igst for _ in range(3))
    assert per_line == 6
    assert compute_line_tax(1, 99, 500, "inter").igst == 5
