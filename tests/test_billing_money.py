"""Money conversion. The one place dollars become Stripe minor units.

These are the values that break naive implementations: a half cent, a float
that is already inexact before it arrives, a zero-decimal currency, and a
three-decimal one.
"""
from decimal import Decimal

import pytest

from app.services.money import (MoneyError, format_money, from_cents,
                                minor_unit_exponent, to_cents)


def test_whole_dollars():
    assert to_cents(Decimal("1997.00")) == 199700
    assert to_cents(Decimal("497")) == 49700
    assert to_cents(Decimal("0")) == 0


def test_none_passes_through():
    assert to_cents(None) is None
    assert from_cents(None) is None


def test_round_trip_is_exact():
    for v in ("0.01", "1.99", "1497.00", "2495.50", "4995.99"):
        cents = to_cents(Decimal(v))
        assert from_cents(cents) == Decimal(v).quantize(Decimal("0.01"))


def test_half_cent_rounds_half_up_not_bankers():
    """ROUND_HALF_EVEN would give 12 and 14. An invoice uses school rounding,
    and every half cent must go the same direction."""
    assert to_cents(Decimal("0.125")) == 13
    assert to_cents(Decimal("0.135")) == 14


def test_float_is_refused_rather_than_laundered():
    """0.1 + 0.2 is 0.30000000000000004 in binary floating point. A float that
    reaches here may already be wrong, so it is refused rather than converted."""
    with pytest.raises(MoneyError):
        to_cents(0.1 + 0.2)
    with pytest.raises(MoneyError):
        to_cents(1997.00)


def test_string_amounts_are_accepted_exactly():
    assert to_cents("0.1") == 10
    assert to_cents("1997.00") == 199700


def test_bool_is_not_money():
    with pytest.raises(MoneyError):
        to_cents(True)
    with pytest.raises(MoneyError):
        from_cents(True)


def test_garbage_raises_rather_than_returning_zero():
    with pytest.raises(MoneyError):
        to_cents("not a number")


def test_zero_decimal_currency():
    """JPY has no minor unit. Sending 1000 yen as 100000 would be 100x."""
    assert minor_unit_exponent("JPY") == 0
    assert to_cents(Decimal("1000"), "JPY") == 1000
    assert from_cents(1000, "JPY") == Decimal("1000")


def test_three_decimal_currency():
    assert minor_unit_exponent("JOD") == 3
    assert to_cents(Decimal("1.234"), "JOD") == 1234


def test_unknown_currency_defaults_to_two_decimals():
    assert minor_unit_exponent("XYZ") == 2


def test_format_is_display_only_and_never_used_for_arithmetic():
    assert format_money(199700) == "USD 1,997.00"
    assert format_money(None) == "—"
    assert format_money(1000, "JPY") == "JPY 1,000"
