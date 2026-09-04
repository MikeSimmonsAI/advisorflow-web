"""Money conversion — the ONE place Decimal dollars become Stripe minor units.

WHY THIS FILE EXISTS

The platform quotes in `Numeric(12, 2)`: `brand_packages.monthly_price`,
`opportunities.implementation_fee`, `implementations.recurring_amount`. Stripe
speaks integer minor units. Every conversion between the two is a chance to
lose or invent a cent, and a conversion written twice is a conversion that will
eventually disagree with itself.

So it is written once, here, and `scripts/probe_billing_integrity.py` asserts
that no other billing module does its own arithmetic on money.

ROUNDING IS HALF-UP, NOT BANKER'S. Python's default rounding for Decimal is
ROUND_HALF_EVEN, which rounds 0.125 to 0.12 and 0.135 to 0.14. That is correct
for statistics and wrong for invoicing: a customer reading a line item expects
the arithmetic they were taught at school, and an auditor expects every half
cent to go the same direction. ROUND_HALF_UP is what invoices use.

FLOATS NEVER APPEAR. `float("0.1") + float("0.2")` is 0.30000000000000004, and
a billing system that lets that near an amount will eventually charge it.
`to_cents` refuses a float outright rather than silently accepting one that is
already wrong by the time it arrives.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Optional, Union

# What a currency's minor unit is worth. Stripe expects the smallest unit of
# the currency, and three-decimal currencies are not hypothetical - a JOD
# amount sent as if it were USD is out by a factor of ten.
_EXPONENT = {
    "USD": 2, "CAD": 2, "EUR": 2, "GBP": 2, "AUD": 2, "NZD": 2, "MXN": 2,
    "JPY": 0, "KRW": 0,
    "BHD": 3, "JOD": 3, "KWD": 3, "OMR": 3, "TND": 3,
}
DEFAULT_CURRENCY = "USD"

Amount = Union[Decimal, int, str]


class MoneyError(ValueError):
    """A money value that cannot be converted safely."""


def minor_unit_exponent(currency: Optional[str]) -> int:
    return _EXPONENT.get((currency or DEFAULT_CURRENCY).upper(), 2)


def to_cents(amount: Optional[Amount], currency: str = DEFAULT_CURRENCY) -> Optional[int]:
    """Decimal dollars -> integer minor units. None passes through as None.

    A float is REFUSED rather than converted. By the time a float reaches this
    function the value may already be wrong, and accepting it would launder a
    rounding error into a charge.
    """
    if amount is None:
        return None
    if isinstance(amount, float):
        raise MoneyError(
            "float is not an acceptable money type (%r). Use Decimal or str - "
            "a float amount may already be inexact before it arrives here."
            % amount)
    if isinstance(amount, bool):
        raise MoneyError("bool is not a money value")
    try:
        d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except (InvalidOperation, TypeError) as exc:
        raise MoneyError("not a usable money value: %r" % (amount,)) from exc
    if not d.is_finite():
        raise MoneyError("money value must be finite: %r" % (amount,))

    scale = Decimal(10) ** minor_unit_exponent(currency)
    scaled = (d * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(scaled)


def from_cents(cents: Optional[int], currency: str = DEFAULT_CURRENCY) -> Optional[Decimal]:
    """Integer minor units -> Decimal. The exact inverse of `to_cents`."""
    if cents is None:
        return None
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise MoneyError("minor units must be an int, got %r" % (cents,))
    exp = minor_unit_exponent(currency)
    return (Decimal(cents) / (Decimal(10) ** exp)).quantize(
        Decimal(1).scaleb(-exp), rounding=ROUND_HALF_UP)


def format_money(cents: Optional[int], currency: str = DEFAULT_CURRENCY) -> str:
    """For display and log lines. Never for arithmetic."""
    if cents is None:
        return "—"
    d = from_cents(cents, currency)
    return "%s %s" % ((currency or DEFAULT_CURRENCY).upper(),
                      "{:,.{p}f}".format(d, p=minor_unit_exponent(currency)))
