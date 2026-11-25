"""Local GSTIN / state-code validation. This only checks FORMAT and the check digit; it does not
(and cannot) verify the number against the GST portal."""
import re

STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman & Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# 2 state digits, 10-char PAN, 1 entity number, 'Z', 1 check character
_GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def check_char(first14: str) -> str:
    """GSTIN check character: weights alternate 1,2; each product is folded in base 36."""
    total = 0
    for i, ch in enumerate(first14):
        product = _ALPHABET.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def is_valid_gstin(gstin: str) -> bool:
    return bool(_GSTIN_RE.match(gstin)) and gstin[:2] in STATE_CODES and check_char(gstin[:14]) == gstin[14]


def make_gstin(state_code: str, pan: str, entity: str = "1") -> str:
    """Build a structurally valid GSTIN (used by the seed script to create sample parties)."""
    first14 = f"{state_code}{pan}{entity}Z"
    return first14 + check_char(first14)
