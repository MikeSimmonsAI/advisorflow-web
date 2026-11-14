"""DEAL BUILDER — commission rules, computed on the server, shown by role.

ONE SET OF RULES
----------------
`RULES` below is the single, versioned statement of the EvoSys Pro sales
launch rates. It drives:
  * every Deal Builder estimate (salesperson, team leader and owner views),
  * the full executive page (the server injects RULES into that page when it
    serves it - the page carries no rate table of its own), and
  * the saved God Mode plans (`evosys_comp_seed`), which must say the same.

WHO SEES WHAT (decided on the server, never in the browser)
-----------------------------------------------------------
  full  God Mode and the sales program owners: every layer, EvoSys's share
        and the unclaimed amounts.
  sevp  The Sr Executive Vice President: rep + Area Sales Manager + his own.
  asm   An Area Sales Manager: rep + his own.
  rep   A rep: his own commission only.
A view never receives a number for a layer above it.

THE LAUNCH RATES (version 2026-11-13)
-------------------------------------
Packages - setup is a fixed dollar amount per role:
    Basic         rep $500    ASM $200   Sr EVP $300
    Growth        rep $1,000  ASM $300   Sr EVP $600
    Professional  rep $2,000  ASM $400   Sr EVP $900
  Monthly platform fee: rep 15%, ASM 10%, Sr EVP 15%, EvoSys 60%.
Custom SaaS - setup AND every monthly payment while the contract is active:
  rep 20%, ASM 10%, Sr EVP 15%, EvoSys 55%.
Add-ons follow the deal they are on: package-deal add-ons at 15/10/15/60,
SaaS add-ons at 20/10/15/55, on both their setup and monthly amounts. There is
no separate add-on rate.
À la carte (no package): not decided by the owners yet - estimated with the
package add-on rates and flagged as such.

Earned only on money actually collected. A partial setup payment is prorated
across the setup lines in proportion to their size, unless the receipt is
explicitly allocated to particular lines.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("100000000")
D = Decimal

RULES_VERSION = "2026-11-13"
ROLES = ("rep", "asm", "sevp")
ROLE_LABELS = {"rep": "Salesperson", "asm": "Area Sales Manager",
               "sevp": "Sr Executive Vice President", "evosys": "EvoSys"}

# Views, lowest to highest. Each view sees its own layer and everything below.
VIEW_REP, VIEW_ASM, VIEW_SEVP, VIEW_FULL = "rep", "asm", "sevp", "full"
VIEW_LAYERS = {
    VIEW_REP: ("rep",),
    VIEW_ASM: ("rep", "asm"),
    VIEW_SEVP: ("rep", "asm", "sevp"),
    VIEW_FULL: ("rep", "asm", "sevp"),
}

PLANS: Dict[str, Dict[str, Any]] = {
    "alacarte":     {"name": "À la carte",   "kind": "alacarte", "setup": D("0")},
    "basic":        {"name": "Basic",        "kind": "package",  "setup": D("1600"),
                     "payout": {"rep": D("500"), "asm": D("200"), "sevp": D("300")}},
    "growth":       {"name": "Growth",       "kind": "package",  "setup": D("3200"),
                     "payout": {"rep": D("1000"), "asm": D("300"), "sevp": D("600")}},
    "professional": {"name": "Professional", "kind": "package",  "setup": D("5900"),
                     "payout": {"rep": D("2000"), "asm": D("400"), "sevp": D("900")}},
    "custom_saas":  {"name": "Custom SaaS",  "kind": "saas",     "setup": None},
}
PACKAGE_PCT = {"rep": D("15"), "asm": D("10"), "sevp": D("15")}   # EvoSys 60
SAAS_PCT    = {"rep": D("20"), "asm": D("10"), "sevp": D("15")}   # EvoSys 55

# What the executive page receives (and the saved-plan seed writes). Plain
# numbers, ordered [rep, asm, sevp].
RULES: Dict[str, Any] = {
    "version": RULES_VERSION,
    "package_setup": {PLANS[k]["name"]: [float(PLANS[k]["payout"][r]) for r in ROLES]
                      for k in ("basic", "growth", "professional")},
    "package_pct": [float(PACKAGE_PCT[r]) for r in ROLES],
    "saas_pct": [float(SAAS_PCT[r]) for r in ROLES],
}


class DealInputError(ValueError):
    pass


def _amount(raw, field: str) -> Decimal:
    if raw is None or raw == "":
        return D("0")
    try:
        d = D(str(raw))
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


def _ratio(part: Decimal, whole: Decimal) -> Decimal:
    if whole <= 0:
        return D("0")
    return min(D("1"), part / whole)


def split(deal: Dict[str, Any], filled: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    """Every layer's share of one configured deal. FULL DATA - callers must
    pass the result through `view()` before it leaves the server.

    deal keys: plan, saas_setup, addon_setup, monthly, basis ("quote"|"paid"),
    collected_setup, collected_monthly, and optionally allocation
    {"package": x, "addons": y} for a receipt applied to specific lines.
    `filled` marks which upper seats are filled; an empty seat's share is
    reported as unclaimed (EvoSys keeps it) and never raises anyone else's pay.
    """
    plan_key = str(deal.get("plan") or "").strip().lower()
    if plan_key not in PLANS:
        raise DealInputError("Unknown package")
    plan = PLANS[plan_key]
    kind = plan["kind"]
    addon_setup = _amount(deal.get("addon_setup"), "Add-on setup")
    monthly = _amount(deal.get("monthly"), "Monthly service")
    basis = "paid" if deal.get("basis") == "paid" else "quote"
    filled = {"rep": True, "asm": True, "sevp": True, **(filled or {})}

    plan_setup = (_amount(deal.get("saas_setup"), "Custom SaaS setup")
                  if kind == "saas" else plan["setup"])
    setup_total = plan_setup + addon_setup
    pct = SAAS_PCT if kind == "saas" else PACKAGE_PCT

    # How much of each setup line counts.
    alloc = deal.get("allocation") if basis == "paid" else None
    if basis == "quote":
        f_pkg = D("1") if plan_setup > 0 else D("0")
        f_add = D("1") if addon_setup > 0 else D("0")
        counted_setup = setup_total
        counted_monthly = monthly
    else:
        counted_monthly = min(monthly, _amount(deal.get("collected_monthly"), "Monthly collected"))
        if isinstance(alloc, dict) and alloc:
            a_pkg = min(plan_setup, _amount(alloc.get("package"), "Package allocation"))
            a_add = min(addon_setup, _amount(alloc.get("addons"), "Add-on allocation"))
            f_pkg, f_add = _ratio(a_pkg, plan_setup), _ratio(a_add, addon_setup)
            counted_setup = a_pkg + a_add
        else:
            counted_setup = min(setup_total, _amount(deal.get("collected_setup"), "Setup collected"))
            f_pkg = f_add = _ratio(counted_setup, setup_total)

    layers: Dict[str, Dict[str, Any]] = {}
    unclaimed = {"setup": D("0"), "monthly": D("0"), "reasons": []}
    paid_setup = paid_month = D("0")
    for role in ROLES:
        if kind == "package":
            pkg_part = plan["payout"][role] * f_pkg
        else:
            pkg_part = plan_setup * pct[role] / 100 * f_pkg
        add_part = addon_setup * pct[role] / 100 * f_add
        month_part = counted_monthly * pct[role] / 100
        entry = {
            "role": role, "label": ROLE_LABELS[role],
            "one_time_package": pkg_part, "one_time_addons": add_part,
            "one_time": pkg_part + add_part, "monthly": month_part,
            "monthly_rate": float(pct[role]),
            "setup_rule": (("Fixed %s setup payout" % plan["name"]) if kind == "package"
                           else ("%s%% of setup" % pct[role]) if kind == "saas"
                           else "No package setup"),
            "filled": bool(filled.get(role)),
        }
        if entry["filled"]:
            paid_setup += entry["one_time"]
            paid_month += month_part
        else:
            unclaimed["setup"] += entry["one_time"]
            unclaimed["monthly"] += month_part
            unclaimed["reasons"].append({"role": role, "label": ROLE_LABELS[role],
                                         "reason": "seat empty"})
        layers[role] = entry

    evosys_setup = counted_setup - paid_setup        # includes unclaimed
    evosys_month = counted_monthly - paid_month

    return {
        "rules_version": RULES_VERSION,
        "kind": kind, "plan_name": plan["name"], "basis": basis,
        "basis_label": ("Collected payment example" if basis == "paid"
                        else "Quote estimate — not earned yet"),
        "setup_total": setup_total, "setup_counted": counted_setup,
        "monthly_total": monthly, "monthly_counted": counted_monthly,
        "layers": layers,
        "evosys": {"one_time": evosys_setup, "monthly": evosys_month},
        "unclaimed": unclaimed,
        "alacarte_estimate": kind == "alacarte" and addon_setup + monthly > 0,
    }


def view(full: Dict[str, Any], view_name: str) -> Dict[str, Any]:
    """Cut a full split down to what `view_name` may see. The returned dict is
    built fresh from allowed fields only - nothing is deleted from a copy, so a
    field added to `split()` later cannot leak by default."""
    if view_name not in VIEW_LAYERS:
        raise ValueError("unknown view")
    visible = VIEW_LAYERS[view_name]

    def layer_out(e):
        return {"role": e["role"], "label": e["label"],
                "one_time_package": _c(e["one_time_package"]),
                "one_time_addons": _c(e["one_time_addons"]),
                "one_time": _c(e["one_time"]), "monthly": _c(e["monthly"]),
                "monthly_rate": e["monthly_rate"], "setup_rule": e["setup_rule"]}

    out = {
        "rules_version": full["rules_version"], "view": view_name,
        "kind": full["kind"], "plan_name": full["plan_name"],
        "basis": full["basis"], "basis_label": full["basis_label"],
        "setup_total": _c(full["setup_total"]), "setup_counted": _c(full["setup_counted"]),
        "monthly_total": _c(full["monthly_total"]),
        "monthly_counted": _c(full["monthly_counted"]),
        "layers": [layer_out(full["layers"][r]) for r in visible],
        "alacarte_estimate": full["alacarte_estimate"],
    }
    out["own"] = out["layers"][-1] if view_name != VIEW_FULL else None
    if view_name == VIEW_FULL:
        out["evosys"] = {"one_time": _c(full["evosys"]["one_time"]),
                         "monthly": _c(full["evosys"]["monthly"])}
        out["unclaimed"] = {"one_time": _c(full["unclaimed"]["setup"]),
                            "monthly": _c(full["unclaimed"]["monthly"]),
                            "reasons": full["unclaimed"]["reasons"]}
    return out


def seller_commission(deal: Dict[str, Any]) -> Dict[str, Any]:
    """Backward-compatible shape for the original salesperson endpoint: the
    rep's own figures only."""
    v = view(split(deal), VIEW_REP)
    own = v["own"]
    addon_status = "included" if own["one_time_addons"] > 0 else "none"
    return {
        "rules_version": v["rules_version"],
        "basis": v["basis"], "basis_label": v["basis_label"],
        "setup_total": v["setup_total"], "setup_counted": v["setup_counted"],
        "monthly_counted": v["monthly_counted"],
        "one_time": {
            "package": own["one_time_package"], "package_rule": own["setup_rule"],
            "addon": own["one_time_addons"],
            "addon_rule": "%g%% of add-on setup (add-ons follow the deal)" % own["monthly_rate"],
            "addon_status": addon_status,
            "total": own["one_time"],
        },
        "recurring": {"monthly": own["monthly"],
                      "rule": "%g%% of eligible monthly service collected" % own["monthly_rate"]},
    }
