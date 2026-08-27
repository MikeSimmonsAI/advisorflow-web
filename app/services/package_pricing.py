"""What a package costs, split into the components it is actually sold in.

THREE SEPARATE NUMBERS, and mixing them up is the failure this file exists to
prevent:

  IMPLEMENTATION FEE   one-time. Charged once, at the start. Identical under
                       both billing options. Lives on the package as `setup_fee`
                       (or the legacy `price` where setup_fee was never set),
                       and may be overridden PER DEAL.

  MONTHLY RATE         recurring, layered ON TOP of the implementation fee.
                       Two of them:
                         - the month-to-month rate: the package's NORMAL price,
                           what a customer pays with no commitment;
                         - the contracted rate: LOWER, and EARNED by committing
                           to a term agreement.

The contracted rate is not the package's price. Present it as the price and
every customer gets the discount without the commitment; default a deal to it
and customers end up on a thirteen-month obligation nobody agreed to.

WHAT A TERM AGREEMENT IS, EXACTLY:
  - the customer commits to `term_months` months (13 for EvoSys Pro),
  - every one of those months is billed, monthly, at the contracted rate,
  - THERE IS NO FREE MONTH and NO ANNUAL PREPAYMENT. The saving is per-month.

THE COMMERCIAL VALUES, and why they are named separately rather than collapsed
into one "deal value":

  mrr                        the monthly recurring rate
  recurring_contract_value   mrr x term. Only exists for a term agreement -
                             month-to-month has no committed total, and
                             inventing one would overstate the book.
  total_contract_value       recurring_contract_value + implementation fee.
                             The whole commitment.

Savings and totals are COMPUTED here, never stored. A stored total is a number
that can disagree with the parts it came from, and the first time it does, a
customer sees a figure that is not the sum of what they agreed to.
"""
from decimal import Decimal
from typing import Any, Dict, List, Optional

# ── the two options ─────────────────────────────────────────────────────────
BILLING_MONTH_TO_MONTH = "month_to_month"
BILLING_TERM_AGREEMENT = "term_agreement"
BILLING_OPTIONS = (BILLING_MONTH_TO_MONTH, BILLING_TERM_AGREEMENT)

# The month-to-month rate is what a package costs when nothing has been chosen.
# Defaulting to the CONTRACTED rate would quote a commitment nobody made.
DEFAULT_BILLING_OPTION = BILLING_MONTH_TO_MONTH

DEFAULT_TERM_MONTHS = 13


def option_label(option: str, term_months: Optional[int] = None) -> str:
    if option == BILLING_TERM_AGREEMENT:
        return "%d-Month Agreement" % int(term_months or DEFAULT_TERM_MONTHS)
    return "Month-to-Month"


def _dec(v) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _f(v) -> Optional[float]:
    d = _dec(v)
    return None if d is None else float(d)


# ── the one-time component ──────────────────────────────────────────────────

def implementation_fee(pkg, opp=None) -> Optional[Decimal]:
    """The ONE-TIME implementation charge for this package, on this deal.

    Resolution order, most specific first:
      1. the deal's own override, when a customer was quoted a different setup
         figure than the catalogue's,
      2. the package's explicit `setup_fee`,
      3. the legacy `price` column, which is where the existing one-time
         catalogue numbers ($1,497 / $2,495 / $4,995) actually live.

    Step 3 is why this function exists rather than a bare attribute read. Half
    the catalogue predates `setup_fee`, and a reader that only checked the new
    column would report "no implementation fee" for packages that plainly have
    one.
    """
    if opp is not None:
        override = _dec(getattr(opp, "implementation_fee", None))
        if override is not None:
            return override
    if pkg is None:
        return None
    explicit = _dec(getattr(pkg, "setup_fee", None))
    if explicit is not None:
        return explicit
    return _dec(getattr(pkg, "price", None))


def implementation_fee_source(pkg, opp=None) -> Optional[str]:
    """Where the figure above came from. Shown so nobody has to guess whether a
    setup fee is this deal's or the catalogue's."""
    if opp is not None and _dec(getattr(opp, "implementation_fee", None)) is not None:
        return "opportunity_override"
    if pkg is None:
        return None
    if _dec(getattr(pkg, "setup_fee", None)) is not None:
        return "package_setup_fee"
    if _dec(getattr(pkg, "price", None)) is not None:
        return "package_legacy_price"
    return None


# ── the recurring component ─────────────────────────────────────────────────

def term_months_for(pkg) -> int:
    n = getattr(pkg, "contract_term_months", None)
    try:
        n = int(n) if n is not None else 0
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else DEFAULT_TERM_MONTHS


def has_monthly_pricing(pkg) -> bool:
    """True when a recurring platform rate has been configured at all."""
    return pkg is not None and _dec(getattr(pkg, "monthly_price", None)) is not None


def has_term_option(pkg) -> bool:
    """True only when a REAL contracted rate exists.

    Deliberately not "has a term length". A package with a term but no
    contracted rate has nothing to offer at a lower price, and offering the
    option anyway would ask a customer to commit for thirteen months in
    exchange for the regular rate.
    """
    if pkg is None or getattr(pkg, "is_custom", False):
        return False
    return _dec(getattr(pkg, "contract_monthly_price", None)) is not None


def monthly_rate(pkg, option: Optional[str]) -> Optional[Decimal]:
    """The recurring monthly amount for this package on this option."""
    if pkg is None:
        return None
    if option == BILLING_TERM_AGREEMENT:
        return _dec(getattr(pkg, "contract_monthly_price", None))
    return _dec(getattr(pkg, "monthly_price", None))


def normalize_option(value: Optional[str], pkg=None) -> str:
    """Coerce whatever arrived to a valid option, FAILING CLOSED to
    month-to-month.

    Never the reverse. Guessing "term agreement" would put a customer under a
    thirteen-month obligation because a string was malformed.
    """
    v = (value or "").strip().lower()
    if v not in BILLING_OPTIONS:
        return BILLING_MONTH_TO_MONTH
    if v == BILLING_TERM_AGREEMENT and pkg is not None and not has_term_option(pkg):
        return BILLING_MONTH_TO_MONTH
    return v


def savings_per_month(pkg) -> Optional[Decimal]:
    """Regular monthly rate minus contracted monthly rate. Computed, never stored."""
    if not has_term_option(pkg):
        return None
    regular = _dec(getattr(pkg, "monthly_price", None))
    contracted = _dec(getattr(pkg, "contract_monthly_price", None))
    if regular is None or contracted is None:
        return None
    return regular - contracted


# ── the commercial picture ──────────────────────────────────────────────────

def quote(pkg, option: Optional[str] = None, opp=None,
          term_months: Optional[int] = None) -> Dict[str, Any]:
    """Every number ONE option produces, each named for exactly what it is.

    `term_months` overrides the catalogue's term - used when a deal has already
    snapshotted one, so a later catalogue edit cannot rewrite an agreed
    commitment.
    """
    opt = normalize_option(option, pkg)
    rate = monthly_rate(pkg, opt)
    setup = implementation_fee(pkg, opp)

    term = None
    if opt == BILLING_TERM_AGREEMENT:
        term = int(term_months) if term_months else term_months_for(pkg)

    # Only a term agreement has a committed recurring total. Month-to-month has
    # no end date, so any total invented for it would be a guess presented as a
    # commitment.
    rcv = rate * Decimal(term) if (rate is not None and term) else None
    tcv = None
    if rcv is not None:
        tcv = rcv + (setup or Decimal("0"))
    elif opt == BILLING_MONTH_TO_MONTH and setup is not None and rate is None:
        # A package with no recurring rate at all: the implementation fee is
        # the whole of it, and saying so is honest rather than returning None.
        tcv = setup

    return {
        "billing_option": opt,
        "billing_option_label": option_label(opt, term),
        "currency": getattr(pkg, "currency", None) or "USD",

        # one-time
        "implementation_fee": _f(setup),
        "implementation_fee_is_one_time": True,
        "implementation_fee_source": implementation_fee_source(pkg, opp),

        # recurring
        "monthly_rate": _f(rate),
        "mrr": _f(rate),
        "term_months": term,
        "billing_cadence": "monthly",
        "payments_required": term,
        "has_free_month": False,
        "annual_prepayment": False,
        "savings_per_month": (_f(savings_per_month(pkg))
                              if opt == BILLING_TERM_AGREEMENT else None),

        # commercial totals
        "recurring_contract_value": _f(rcv),
        "total_contract_value": _f(tcv),
        # What a screen should lead with. For a fixed term that is the whole
        # commitment; month-to-month has no total, so the monthly rate is the
        # honest headline.
        "primary_value": _f(tcv) if term else _f(rate),
        "primary_value_label": ("Total Contract Value" if term
                                else "Monthly Rate"),
        "is_custom": bool(getattr(pkg, "is_custom", False)),
    }


def options_for(pkg, opp=None) -> List[Dict[str, Any]]:
    """Both options a customer may choose between, the REGULAR rate first.

    Order is not cosmetic: a list that leads with the contracted rate teaches
    every reader that the discounted number is the price.
    """
    out = [dict(quote(pkg, BILLING_MONTH_TO_MONTH, opp), is_default=True)]
    if has_term_option(pkg):
        out.append(dict(quote(pkg, BILLING_TERM_AGREEMENT, opp), is_default=False))
    return out


def package_pricing(pkg, opp=None) -> Dict[str, Any]:
    """The pricing block every package payload carries.

    Every number is named for what it is. There is deliberately no bare `price`
    key here: an unlabelled price is exactly how a one-time fee ends up read as
    a monthly rate, and a contracted rate as the normal one.
    """
    return {
        "currency": getattr(pkg, "currency", None) or "USD",
        "implementation_fee": _f(implementation_fee(pkg, opp)),
        "implementation_fee_is_one_time": True,
        "monthly_price": _f(getattr(pkg, "monthly_price", None)),
        "contract_monthly_price": _f(getattr(pkg, "contract_monthly_price", None)),
        "contract_term_months": term_months_for(pkg) if has_term_option(pkg) else None,
        "savings_per_month": _f(savings_per_month(pkg)),
        "has_monthly_pricing": has_monthly_pricing(pkg),
        "has_term_option": has_term_option(pkg),
        "is_custom": bool(getattr(pkg, "is_custom", False)),
        "options": options_for(pkg, opp),
    }
