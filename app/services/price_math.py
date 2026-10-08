"""Pure money rules for a proposal price. No app imports, so it runs anywhere.

ONE decision drives the server write (proposal_service.apply_pricing) and the
message a rep reads. Amounts are Decimal, whole cents, finite and bounded.

Why not Decimal(str(x)) alone: it accepts "NaN" and "Infinity" (a float NaN
from a JSON body is a valid pydantic float), and it keeps sub-cent digits, so
a 100.005 adjustment produced a total no invoice could ever show.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("1000000000")      # 1e9 per proposal is a typo, not a deal


def parse_money(value):
    """-> Decimal rounded to cents, or None when absent/blank/not a usable amount."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite() or abs(d) > MAX_AMOUNT:
        return None
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def price_total(base, adjustment):
    """The one total rule.

    No base AND no adjustment means "not priced" (total None), never $0.
    Returns {"ok", "total", "error"}.
    """
    b, a = parse_money(base), parse_money(adjustment)
    if b is None and a is None:
        return {"ok": True, "total": None, "error": None}
    total = (b or Decimal("0")) + (a or Decimal("0"))
    if total < 0:
        return {"ok": False, "total": None,
                "error": "That adjustment would make the total negative."}
    return {"ok": True, "total": total, "error": None}
