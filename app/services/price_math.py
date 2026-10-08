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


def to_cents(value):
    """-> int cents (exact, via the Decimal rule above) or None. The wire form
    the UI formats money from, so no float ever carries a total."""
    d = parse_money(value)
    return None if d is None else int((d * 100).to_integral_value())


def check_stale(current_stamp, expected_stamp):
    """Optimistic concurrency for a proposal edit.

    expected_stamp is the `updated_at` the caller loaded. Omitted keeps the
    legacy unguarded behaviour: this adds a guard, never a new power. An
    unparseable stamp is refused, not ignored: a malformed guard must not read
    as "no guard". Returns {"ok", "error"}; the router maps a refusal to 409.
    """
    from datetime import datetime, timezone
    if expected_stamp is None or expected_stamp == "":
        return {"ok": True, "error": None}
    try:
        exp = datetime.fromisoformat(str(expected_stamp).strip().replace("Z", "+00:00"))
    except ValueError:
        return {"ok": False, "error": "The proposal version marker was not understood. "
                                      "Reload the proposal and try again."}
    if exp.tzinfo is not None:
        exp = exp.astimezone(timezone.utc).replace(tzinfo=None)
    if current_stamp is None or current_stamp != exp:
        return {"ok": False, "error": "This proposal changed since you loaded it. "
                                      "Reload to see the current price, then re-apply your change."}
    return {"ok": True, "error": None}
