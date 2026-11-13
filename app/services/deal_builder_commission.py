"""DEAL BUILDER — salesperson commission, computed on the server.

WHY THIS LIVES HERE AND NOT IN THE PAGE
---------------------------------------
The Team Pricing Deal Builder used to be one public HTML file that carried the
whole compensation picture: every role's percentage, every role's payout, the
company's retained share. Anyone with the link could read it.

The salesperson version of the builder gets ONE thing from this module: that
salesperson's own estimated commission. The rates and payouts for the Area
Sales Manager, the Sr Executive Vice President and EvoSys are not in this
module's sales output at all, and they are not in the salesperson page.

Customer prices are public (the /team-prices presentation already shows them)
and stay in the shared browser file `team-prices/deal-core.js`. Only the
salesperson's commission rules are here.

THE RULES (keep in step with the executive builder; a parity test runs both)
---------------------------------------------------------------------------
  Standard package setup        fixed payout: Basic $500, Growth $1,000,
                                Professional $2,000 (on $1,600 / $3,200 /
                                $5,900 setup).
  Custom SaaS setup             20% of the customer's setup price.
  Recurring monthly service     20% of the eligible monthly service amount.
  À la carte add-on setup       15% — a PROPOSED rate that has not been
                                approved as policy. Reported separately and
                                labelled as such; never presented as final.
  Usage, renewals, pass-through not commissionable in the current draft.

  Collected example             setup commission is prorated by the share of
                                the quoted setup balance actually collected;
                                recurring commission is on the eligible monthly
                                amount actually collected.

The customer's 15%-of-setup default monthly fee for Custom SaaS is a PRICING
rule, handled in the page. Commission here always uses the actual monthly
amount sent, which includes any manual override.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("100000000")  # $100M — anything above is a typo, not a deal

# Standard package setup prices and the salesperson's fixed setup payout.
STANDARD_PLANS: Dict[str, Dict[str, Any]] = {
    "alacarte":     {"name": "À la carte",   "setup": Decimal("0"),    "seller_setup": Decimal("0")},
    "basic":        {"name": "Basic",        "setup": Decimal("1600"), "seller_setup": Decimal("500")},
    "growth":       {"name": "Growth",       "setup": Decimal("3200"), "seller_setup": Decimal("1000")},
    "professional": {"name": "Professional", "setup": Decimal("5900"), "seller_setup": Decimal("2000")},
    "custom_saas":  {"name": "Custom SaaS",  "setup": None,            "seller_setup": None},
}

SAAS_SETUP_RATE = Decimal("20")        # % of custom SaaS setup — established
RECURRING_RATE = Decimal("20")         # % of eligible monthly service — established
ADDON_SETUP_RATE_PROPOSED = Decimal("15")  # % of add-on setup — PROPOSED, unresolved


class DealInputError(ValueError):
    pass


def _amount(raw, field: str) -> Decimal:
    if raw is None or raw == "":
        return Decimal("0")
    try:
        d = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise DealInputError("%s must be a number" % field)
    if not d.is_finite():
        raise DealInputError("%s must be a number" % field)
    if d < 0:
        raise DealInputError("%s cannot be negative" % field)
    if d > MAX_AMOUNT:
        raise DealInputError("%s is too large" % field)
    return d


def _c(d: Decimal) -> float:
    return float(d.quantize(CENT, rounding=ROUND_HALF_UP))


def seller_commission(deal: Dict[str, Any]) -> Dict[str, Any]:
    """The salesperson's OWN estimated commission for one configured deal.

    `deal` keys: plan, saas_setup, addon_setup, monthly, basis ("quote" or
    "paid"), collected_setup, collected_monthly.

    Returns only the salesperson's figures. Nothing about any other role.
    """
    plan_key = str(deal.get("plan") or "").strip().lower()
    if plan_key not in STANDARD_PLANS:
        raise DealInputError("Unknown package")
    plan = STANDARD_PLANS[plan_key]
    addon_setup = _amount(deal.get("addon_setup"), "Add-on setup")
    monthly = _amount(deal.get("monthly"), "Monthly service")
    basis = "paid" if deal.get("basis") == "paid" else "quote"

    if plan_key == "custom_saas":
        plan_setup = _amount(deal.get("saas_setup"), "Custom SaaS setup")
        package_payout = (plan_setup * SAAS_SETUP_RATE / 100).quantize(CENT, rounding=ROUND_HALF_UP)
        package_rule = "20% of the custom SaaS setup price"
    else:
        plan_setup = plan["setup"]
        package_payout = plan["seller_setup"]
        package_rule = ("Fixed %s setup payout" % plan["name"]) if package_payout else "No package"

    setup_total = plan_setup + addon_setup
    if basis == "paid":
        collected_setup = min(setup_total, _amount(deal.get("collected_setup"), "Setup collected"))
        collected_monthly = min(monthly, _amount(deal.get("collected_monthly"), "Monthly collected"))
    else:
        collected_setup = setup_total
        collected_monthly = monthly

    factor = (collected_setup / setup_total) if setup_total > 0 else Decimal("0")
    package_part = package_payout * factor
    addon_part = addon_setup * ADDON_SETUP_RATE_PROPOSED / 100 * factor
    recurring = collected_monthly * RECURRING_RATE / 100

    return {
        "basis": basis,
        "basis_label": ("Collected payment example" if basis == "paid"
                        else "Quote estimate — not earned yet"),
        "setup_total": _c(setup_total),
        "setup_counted": _c(collected_setup),
        "monthly_counted": _c(collected_monthly),
        "one_time": {
            "package": _c(package_part),
            "package_rule": package_rule,
            "addon": _c(addon_part),
            "addon_rule": "15% of add-on setup — proposed rate, not final",
            "addon_status": "proposed" if addon_setup > 0 else "none",
            "total": _c(package_part + addon_part),
        },
        "recurring": {
            "monthly": _c(recurring),
            "rule": "20% of eligible monthly service collected",
        },
    }
