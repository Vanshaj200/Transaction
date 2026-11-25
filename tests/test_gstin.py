from app.services.gstin import check_char, is_valid_gstin, make_gstin


def test_known_valid_gstin():
    assert is_valid_gstin("27AAPFU0939F1ZV")


def test_bad_check_digit_rejected():
    assert not is_valid_gstin("27AAPFU0939F1ZX")


def test_bad_format_and_state_rejected():
    assert not is_valid_gstin("27AAPFU0939F1Z")        # too short
    assert not is_valid_gstin("99AAPFU0939F1ZV")       # no such state prefix
    assert not is_valid_gstin("27aapfu0939f1zv")       # must be upper-case


def test_make_gstin_roundtrip():
    g = make_gstin("29", "ABCDE1234F")
    assert g.startswith("29ABCDE1234F1Z") and is_valid_gstin(g)
    assert check_char(g[:14]) == g[14]
