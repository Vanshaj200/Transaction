"""Money display helpers. Storage is always integer paise; formatting happens only at the edge."""


def format_inr(paise: int) -> str:
    """12345678 -> '1,23,456.78' (Indian digit grouping: last three digits, then pairs)."""
    sign = "-" if paise < 0 else ""
    rupees, ps = divmod(abs(paise), 100)
    digits = str(rupees)
    head, tail = digits[:-3], digits[-3:]
    if head:
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        pairs.insert(0, head)
        tail = ",".join(pairs) + "," + tail
    return f"{sign}{tail}.{ps:02d}"


def format_rate(rate_bp: int) -> str:
    """1800 -> '18%', 25 -> '0.25%', 500 -> '5%'."""
    pct = rate_bp / 100
    return f"{pct:g}%"
