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


# ── the per-deal custom rate ────────────────────────────────────────────────
#
# The recurring half of what `implementation_fee` already does for the one-time
# half: a figure agreed on THIS deal, which the catalogue does not and should
# not carry.
#
# A package named "Custom" exists precisely because its price is not in the
# catalogue. Before this, that meant it had no recurring price at all, and a
# custom deal could not state a monthly rate or a term anywhere in the system.
#
# Priced PER UNIT rather than as a flat figure, so the basis survives into the
# customer's document: "$250 per active paying customer, 15 minimum" is
# $3,750/month AND the arithmetic that produced it. A genuinely flat rate is
# simply one unit with no label.

def custom_rate(holder) -> Optional[Dict[str, Any]]:
    """The recurring rate agreed on this deal, or None if there is not one.

    `holder` is whatever carries the agreement — an Opportunity while the deal
    is being worked, a Proposal once it has been quoted. Both expose the same
    four attributes, and the Proposal's are a snapshot: a later change on the
    deal must never rewrite a document the customer has already read.
    """
    if holder is None:
        return None
    unit = _dec(getattr(holder, "custom_unit_price", None))
    if unit is None or unit < 0:
        return None

    units = getattr(holder, "custom_min_units", None)
    try:
        units = int(units) if units is not None else 1
    except (TypeError, ValueError):
        units = 1
    if units < 1:
        units = 1

    term = getattr(holder, "custom_term_months", None)
    try:
        term = int(term) if term else None
    except (TypeError, ValueError):
        term = None
    if term is not None and term < 1:
        term = None

    return {
        "unit_price": unit,
        "unit_label": (getattr(holder, "custom_unit_label", None) or "").strip() or None,
        "min_units": units,
        "monthly_rate": unit * Decimal(units),
        "term_months": term,
    }


def custom_basis(custom: Optional[Dict[str, Any]]) -> Optional[str]:
    """How the monthly figure was arrived at, in words.

    Returned only when there is a unit to name. A flat custom rate has no
    basis to explain, and inventing one ("1 unit") would be noise.
    """
    if not custom or not custom.get("unit_label"):
        return None
    return "%s per %s per month, %d minimum" % (
        _plain_money(custom["unit_price"]), custom["unit_label"],
        custom["min_units"])


def _plain_money(d: Optional[Decimal]) -> str:
    if d is None:
        return ""
    return "$" + ("{:,.0f}".format(d) if d == d.to_integral_value()
                  else "{:,.2f}".format(d))


def has_term_option(pkg, custom: Optional[Dict[str, Any]] = None) -> bool:
    """True only when a REAL contracted rate exists.

    Deliberately not "has a term length". A package with a term but no
    contracted rate has nothing to offer at a lower price, and offering the
    option anyway would ask a customer to commit for thirteen months in
    exchange for the regular rate.

    A per-deal custom rate WITH a term is the exception, and a different kind
    of thing: there is no catalogue rate to be lower than, because the rate and
    the term were negotiated together as one agreement.
    """
    if custom and custom.get("term_months"):
        return True
    if pkg is None or getattr(pkg, "is_custom", False):
        return False
    return _dec(getattr(pkg, "contract_monthly_price", None)) is not None


def monthly_rate(pkg, option: Optional[str],
                 custom: Optional[Dict[str, Any]] = None) -> Optional[Decimal]:
    """The recurring monthly amount for this package on this option.

    A per-deal rate wins over the catalogue on BOTH options. There is only one
    custom rate — it is what was agreed — so there is no second, higher figure
    to quote against it, and pretending otherwise would manufacture a saving
    nobody offered.
    """
    if custom:
        return custom["monthly_rate"]
    if pkg is None:
        return None
    if option == BILLING_TERM_AGREEMENT:
        return _dec(getattr(pkg, "contract_monthly_price", None))
    return _dec(getattr(pkg, "monthly_price", None))


def normalize_option(value: Optional[str], pkg=None,
                     custom: Optional[Dict[str, Any]] = None) -> str:
    """Coerce whatever arrived to a valid option, FAILING CLOSED to
    month-to-month.

    Never the reverse. Guessing "term agreement" would put a customer under a
    thirteen-month obligation because a string was malformed.
    """
    v = (value or "").strip().lower()
    if v not in BILLING_OPTIONS:
        return BILLING_MONTH_TO_MONTH
    if v == BILLING_TERM_AGREEMENT and not has_term_option(pkg, custom):
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
          term_months: Optional[int] = None,
          custom: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Every number ONE option produces, each named for exactly what it is.

    `term_months` overrides the catalogue's term - used when a deal has already
    snapshotted one, so a later catalogue edit cannot rewrite an agreed
    commitment.

    `custom` is the per-deal rate from `custom_rate()`. Passed explicitly rather
    than read off `opp`, because the holder differs by caller: a deal in flight
    quotes from the opportunity, a proposal from its own snapshot.
    """
    opt = normalize_option(option, pkg, custom)
    rate = monthly_rate(pkg, opt, custom)
    setup = implementation_fee(pkg, opp)

    term = None
    if opt == BILLING_TERM_AGREEMENT:
        # A custom agreement's term is part of what was negotiated, so it wins
        # over both the caller's snapshot and the catalogue default.
        if custom and custom.get("term_months"):
            term = int(custom["term_months"])
        else:
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
        # A custom rate has no catalogue rate to be cheaper than, so it reports
        # no saving rather than a fabricated one.
        "savings_per_month": (None if custom else
                              (_f(savings_per_month(pkg))
                               if opt == BILLING_TERM_AGREEMENT else None)),

        # The per-deal agreement, when there is one. `custom_basis` is the
        # sentence a document prints beside the monthly figure so the customer
        # can see the arithmetic rather than being handed a total.
        "is_custom_rate": bool(custom),
        "custom_unit_price": _f(custom["unit_price"]) if custom else None,
        "custom_unit_label": custom["unit_label"] if custom else None,
        "custom_min_units": custom["min_units"] if custom else None,
        "custom_basis": custom_basis(custom),

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


def options_for(pkg, opp=None,
                custom: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The options a customer may choose between, the REGULAR rate first.

    Order is not cosmetic: a list that leads with the contracted rate teaches
    every reader that the discounted number is the price.

    A custom agreement is the exception and returns ONE option. Its rate and
    its term were negotiated together, so there is no second, higher rate to
    choose instead — offering one would invent a price nobody quoted.
    """
    if custom and custom.get("term_months"):
        return [dict(quote(pkg, BILLING_TERM_AGREEMENT, opp, custom=custom),
                     is_default=True)]
    out = [dict(quote(pkg, BILLING_MONTH_TO_MONTH, opp, custom=custom),
                is_default=True)]
    if has_term_option(pkg, custom):
        out.append(dict(quote(pkg, BILLING_TERM_AGREEMENT, opp, custom=custom),
                        is_default=False))
    return out


def package_pricing(pkg, opp=None,
                    custom: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The pricing block every package payload carries.

    Every number is named for what it is. There is deliberately no bare `price`
    key here: an unlabelled price is exactly how a one-time fee ends up read as
    a monthly rate, and a contracted rate as the normal one.
    """
    return {
        "currency": getattr(pkg, "currency", None) or "USD",
        "implementation_fee": _f(implementation_fee(pkg, opp)),
        "implementation_fee_is_one_time": True,
        # The per-deal rate replaces the catalogue's on the screens that quote
        # this deal. `monthly_price` stays the CATALOGUE figure so a reader can
        # still see what the package normally costs, if anything.
        "monthly_price": _f(getattr(pkg, "monthly_price", None)),
        "contract_monthly_price": _f(getattr(pkg, "contract_monthly_price", None)),
        "contract_term_months": (custom["term_months"] if custom and custom.get("term_months")
                                 else (term_months_for(pkg) if has_term_option(pkg) else None)),
        "savings_per_month": (None if custom else _f(savings_per_month(pkg))),
        "has_monthly_pricing": bool(custom) or has_monthly_pricing(pkg),
        "has_term_option": has_term_option(pkg, custom),
        "is_custom": bool(getattr(pkg, "is_custom", False)),

        # The agreed per-deal rate, so a panel can render and edit it without
        # having to know how it was derived.
        "is_custom_rate": bool(custom),
        "custom_unit_price": _f(custom["unit_price"]) if custom else None,
        "custom_unit_label": custom["unit_label"] if custom else None,
        "custom_min_units": custom["min_units"] if custom else None,
        "custom_term_months": custom["term_months"] if custom else None,
        "custom_monthly_rate": _f(custom["monthly_rate"]) if custom else None,
        "custom_basis": custom_basis(custom),

        "options": options_for(pkg, opp, custom),
    }
