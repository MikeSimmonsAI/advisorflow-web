"""COMPENSATION PROJECTION TRUTH - one pure decision, integer cents, no database.

FORECASTING ONLY. Nothing here pays, approves, files or sends anything, and no
function in this module writes. It takes facts the caller has already gathered
(and already tenant-scoped) and decides which of four DIFFERENT things each
dollar is:

    earned      commission that exists as a ledger entry because a payment was
                collected. Split into on_hold (holdback not elapsed), payable
                and paid. This is the only money that is ever "earned".
    pending     forecast commission on a deal whose pricing is awaiting
                approval. Not a promise; never added to the forecast headline.
    forecast    commission the configured plan WOULD pay if an open deal closed
                on today's terms, plus the stage-weighted version of it.
    excluded    deals that cannot be priced or paid truthfully (no plan, no
                rule produced an amount, pricing incomplete, no stage
                probability, malformed). Counted and named, never folded in
                as zero.

WHY ANOTHER MODULE. `pipeline_projection.project` rolls up floats and has no
weighted COMMISSION, no earned/held split and no scope-by-payee; it also
counts a deal whose plan produced no payout as a projected $0, which reads as
"this deal costs nothing" when the truth is "no rate applies". The commission
amounts themselves still come from `compensation.compute` - this module does
not invent rates, milestones or holdbacks; the holdback is whatever stamped
`payable_at` on the entry.

MONEY: integer cents only. Inputs are Decimal / int / numeric strings and are
refused (ProjectionInputError) when malformed, NaN/Infinity, negative,
sub-cent, above MAX_CENTS, or contradictory. Probabilities are hundredths of a
percent (0..10000 basis points), applied with half-up integer rounding.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

MAX_CENTS = 10 ** 13          # $100 billion; anything above is a data error

# Entry states (mirrors compensation_models; duplicated so this stays pure).
STATE_EARNED = "earned"
STATE_PAYABLE = "payable"
STATE_PAID = "paid"
STATE_VOID = "void"

# Comp statuses from compensation.compute.
COMP_PROJECTED = "projected"
COMP_PENDING_APPROVAL = "pending_approval"
COMP_UNCONFIGURED = "unconfigured"

# Exclusion reasons.
EX_NO_PLAN = "no_compensation_plan"
EX_NO_RATE = "no_rate_produced_amount"
EX_PRICING_INCOMPLETE = "pricing_incomplete"
EX_NO_PRICING = "no_priced_value"
EX_PAYEE_NOT_IN_DEAL = "viewer_has_no_payout_on_deal"

EXCLUSION_LABELS = {
    EX_NO_PLAN: "No compensation plan is configured for this brand.",
    EX_NO_RATE: "A plan exists but no rule produced an amount (no rate for "
                "this package, or the rule's basis is unpriced).",
    EX_PRICING_INCOMPLETE: "Pricing is incomplete, so the commission is a "
                           "floor at best and is not counted.",
    EX_NO_PRICING: "The deal has no priced value.",
    EX_PAYEE_NOT_IN_DEAL: "You have no payout on this deal.",
}


class ProjectionInputError(ValueError):
    """The facts handed to the projection are malformed or contradictory."""


def to_cents(value: Any, field: str = "amount", *, allow_none: bool = False
             ) -> Optional[int]:
    """Strictly convert a money value to integer cents, or refuse.

    Accepts int (already cents is NOT assumed - ints are dollars), Decimal and
    numeric strings. Floats are refused: a float has already lost the cent.
    """
    if value is None:
        if allow_none:
            return None
        raise ProjectionInputError("%s is missing" % field)
    if isinstance(value, bool) or isinstance(value, float):
        raise ProjectionInputError("%s must be Decimal, int or string, not %s"
                                   % (field, type(value).__name__))
    try:
        d = Decimal(str(value).strip()) if not isinstance(value, Decimal) else value
    except (InvalidOperation, ValueError):
        raise ProjectionInputError("%s is not a number" % field)
    if not d.is_finite():
        raise ProjectionInputError("%s is not finite" % field)
    if d < 0:
        raise ProjectionInputError("%s is negative" % field)
    cents = d * 100
    if cents != cents.to_integral_value():
        raise ProjectionInputError("%s has sub-cent precision" % field)
    cents = int(cents)
    if cents > MAX_CENTS:
        raise ProjectionInputError("%s exceeds the allowed maximum" % field)
    return cents


def to_basis_points(pct: Any, field: str = "probability") -> int:
    """Percent (0..100, at most 2 decimals) -> hundredths of a percent."""
    if pct is None or isinstance(pct, (bool, float)):
        raise ProjectionInputError("%s must be Decimal, int or string" % field)
    try:
        d = pct if isinstance(pct, Decimal) else Decimal(str(pct).strip())
    except (InvalidOperation, ValueError):
        raise ProjectionInputError("%s is not a number" % field)
    if not d.is_finite() or d < 0 or d > 100:
        raise ProjectionInputError("%s must be between 0 and 100" % field)
    bp = d * 100
    if bp != bp.to_integral_value():
        raise ProjectionInputError("%s has more than 2 decimals" % field)
    return int(bp)


def weigh(cents: int, basis_points: int) -> int:
    """cents x probability, half-up, in integers."""
    return (cents * basis_points + 5000) // 10000


def format_cents(cents: Optional[int]) -> Optional[str]:
    """Deterministic '1,234.56' text (no locale, no float)."""
    if cents is None:
        return None
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return "%s%s.%02d" % (sign, "{:,}".format(cents // 100), cents % 100)


def _money_out(cents: Optional[int]) -> Dict[str, Any]:
    return {"cents": cents, "display": format_cents(cents)}


def _require_id(v: Any, field: str) -> str:
    if not isinstance(v, str) or not v:
        raise ProjectionInputError("%s is required" % field)
    return v


def validate_probabilities(probabilities: Dict[str, Any]) -> Dict[str, int]:
    return {str(stage): to_basis_points(p, "probability[%s]" % stage)
            for stage, p in (probabilities or {}).items()}


def _entry_bucket(state: str, payable_at: Optional[datetime], now: datetime
                  ) -> Optional[str]:
    if state == STATE_VOID:
        return None
    if state == STATE_PAID:
        return "paid"
    if state == STATE_PAYABLE:
        return "payable"
    if state == STATE_EARNED:
        if payable_at is not None and payable_at <= now:
            return "payable"
        # No stored payable_at => we cannot say the holdback elapsed; held.
        return "on_hold"
    raise ProjectionInputError("unknown entry state %r" % (state,))


def decide(*, brand_sales_org_id: str,
           deals: Iterable[Dict[str, Any]],
           entries: Iterable[Dict[str, Any]],
           probabilities: Dict[str, Any],
           now: datetime,
           payee_user_id: Optional[str] = None,
           stage: Optional[str] = None) -> Dict[str, Any]:
    """The authoritative projection for ONE brand.

    deals   open opportunities: opportunity_id, brand_sales_org_id,
            owner_user_id, stage, company_name, fixed_value (Decimal|None),
            pricing_complete (bool), comp_status, plan_configured (bool),
            plan_name, capped, cap_amount, payouts [{payee_user_id,
            payee_kind, amount, rule_id, basis, rate_percent}].
    entries ledger rows: entry_id, brand_sales_org_id, opportunity_id,
            payee_user_id, amount, state, payable_at, collection_reference.
    payee_user_id  narrows to one person's share (seller view). None means the
            whole brand (management view) - the CALLER decides which, from the
            authenticated user, never from a client parameter.
    """
    brand = _require_id(brand_sales_org_id, "brand_sales_org_id")
    if not isinstance(now, datetime):
        raise ProjectionInputError("now must be a datetime")
    probs = validate_probabilities(probabilities)

    # ── earned: ledger entries only ─────────────────────────────────────────
    earned = {"on_hold": 0, "payable": 0, "paid": 0}
    earned_n = {"on_hold": 0, "payable": 0, "paid": 0}
    earned_rows: List[Dict[str, Any]] = []
    seen_entries = set()
    for e in entries:
        eid = _require_id(e.get("entry_id"), "entry_id")
        if e.get("brand_sales_org_id") != brand:
            raise ProjectionInputError("entry %s belongs to another brand" % eid)
        if eid in seen_entries:
            raise ProjectionInputError("duplicate entry %s" % eid)
        seen_entries.add(eid)
        if payee_user_id is not None and e.get("payee_user_id") != payee_user_id:
            continue
        if stage is not None:
            continue            # earned money has no stage; a stage filter is forecast-only
        amt = to_cents(e.get("amount"), "entry %s amount" % eid)
        if not e.get("collection_reference"):
            # An entry with no payment evidence must not be called earned.
            raise ProjectionInputError(
                "entry %s has no collection reference" % eid)
        b = _entry_bucket(e.get("state"), e.get("payable_at"), now)
        if b is None:
            continue
        earned[b] += amt
        earned_n[b] += 1
        earned_rows.append({"entry_id": eid, "opportunity_id": e.get("opportunity_id"),
                            "payee_user_id": e.get("payee_user_id"),
                            "bucket": b, "amount": _money_out(amt),
                            "payable_at": e.get("payable_at"),
                            "collection_reference": e["collection_reference"],
                            "label": {"on_hold": "Earned - on holdback",
                                      "payable": "Earned - holdback elapsed",
                                      "paid": "Paid"}[b]})
    earned_rows.sort(key=lambda r: (r["bucket"], r["entry_id"]))

    # ── forecast: open deals only ───────────────────────────────────────────
    gross = weighted_rev = 0
    comm = weighted_comm = 0
    pending_comm = 0
    pending_n = forecast_n = weighted_n = 0
    weighted_missing = 0
    deal_rows: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    seen_deals = set()

    for d in deals:
        oid = _require_id(d.get("opportunity_id"), "opportunity_id")
        if d.get("brand_sales_org_id") != brand:
            raise ProjectionInputError("deal %s belongs to another brand" % oid)
        if oid in seen_deals:
            raise ProjectionInputError("duplicate deal %s" % oid)
        seen_deals.add(oid)
        if stage is not None and d.get("stage") != stage:
            continue
        if payee_user_id is not None:
            # A seller sees deals where they have a payout or own the deal.
            involved = d.get("owner_user_id") == payee_user_id or any(
                p.get("payee_user_id") == payee_user_id
                for p in d.get("payouts") or [])
            if not involved:
                continue

        def exclude(reason: str) -> None:
            excluded.append({"opportunity_id": oid,
                             "company_name": d.get("company_name"),
                             "stage": d.get("stage"),
                             "reason": reason,
                             "label": EXCLUSION_LABELS[reason]})

        status = d.get("comp_status")
        if status not in (COMP_PROJECTED, COMP_PENDING_APPROVAL, COMP_UNCONFIGURED):
            raise ProjectionInputError("deal %s has unknown comp_status" % oid)
        if status == COMP_UNCONFIGURED or not d.get("plan_configured", True):
            exclude(EX_NO_PLAN); continue
        fixed = to_cents(d.get("fixed_value"), "deal %s fixed_value" % oid,
                         allow_none=True)
        if fixed is None:
            exclude(EX_NO_PRICING); continue

        payouts = d.get("payouts") or []
        total_all = 0
        mine = 0
        for p in payouts:
            amt = to_cents(p.get("amount"), "deal %s payout" % oid)
            total_all += amt
            if payee_user_id is None or p.get("payee_user_id") == payee_user_id:
                mine += amt
        cap = to_cents(d.get("cap_amount"), "deal %s cap" % oid, allow_none=True)
        # Proportional capping quantises each payout to the cent, so the sum
        # may sit up to one cent per payout off the cap; more is contradictory.
        if cap is not None and total_all > cap + len(payouts):
            raise ProjectionInputError(
                "deal %s payouts exceed its package cap" % oid)
        if not payouts:
            exclude(EX_NO_RATE); continue
        if payee_user_id is not None and mine == 0 and not any(
                p.get("payee_user_id") == payee_user_id for p in payouts):
            exclude(EX_PAYEE_NOT_IN_DEAL); continue
        if not d.get("pricing_complete", False):
            exclude(EX_PRICING_INCOMPLETE); continue

        pct_bp = probs.get(d.get("stage"))
        row = {"opportunity_id": oid, "company_name": d.get("company_name"),
               "owner_user_id": d.get("owner_user_id"), "stage": d.get("stage"),
               "plan_name": d.get("plan_name"),
               "capped": bool(d.get("capped")),
               "cap": _money_out(cap),
               "basis": [{"rule_id": p.get("rule_id"), "basis": p.get("basis"),
                          "rate_percent": p.get("rate_percent"),
                          "payee_kind": p.get("payee_kind")} for p in payouts
                         if payee_user_id is None
                         or p.get("payee_user_id") == payee_user_id],
               "gross_sales": _money_out(fixed),
               "commission": _money_out(mine),
               "probability_bp": pct_bp,
               "evidence": "projection_no_payment"}

        if status == COMP_PENDING_APPROVAL:
            # Held out of the headline entirely.
            pending_comm += mine
            pending_n += 1
            row.update(category="pending", evidence="pricing_awaiting_approval",
                       label="Pending approval - not a forecast, not owed")
            deal_rows.append(row)
            continue

        gross += fixed
        comm += mine
        forecast_n += 1
        if pct_bp is None:
            weighted_missing += 1
            row.update(weighted_gross_sales=_money_out(None),
                       weighted_commission=_money_out(None))
        else:
            wr, wc = weigh(fixed, pct_bp), weigh(mine, pct_bp)
            weighted_rev += wr
            weighted_comm += wc
            weighted_n += 1
            row.update(weighted_gross_sales=_money_out(wr),
                       weighted_commission=_money_out(wc))
        row.update(category="forecast",
                   label="Forecast - not earned, not payable")
        deal_rows.append(row)

    deal_rows.sort(key=lambda r: (r["stage"] or "", r["opportunity_id"]))
    excluded.sort(key=lambda r: (r["reason"], r["opportunity_id"]))

    blockers: List[str] = []
    if any(x["reason"] == EX_NO_PLAN for x in excluded):
        blockers.append(EXCLUSION_LABELS[EX_NO_PLAN])
    if weighted_missing:
        blockers.append("%d forecast deal(s) have no configured stage "
                        "probability and are left out of the weighted totals."
                        % weighted_missing)

    return _assemble(brand, payee_user_id, stage, earned, earned_n, earned_rows,
                     pending_comm, pending_n, gross, weighted_rev, comm,
                     weighted_comm, forecast_n, weighted_n, weighted_missing,
                     deal_rows, excluded, blockers)


class ScopeRefused(Exception):
    """The caller may not read this scope. `status` is the HTTP code."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def resolve_scope(*, user_id: str, requested_brand: Optional[str],
                  requested_payee: Optional[str],
                  management_brand_ids: Iterable[str],
                  member_brand_ids: Iterable[str]) -> Dict[str, Optional[str]]:
    """Decide brand and payee scope from AUTHENTICATED authority only.

    management_brand_ids  brands where the caller is god / sales manager /
                          holds sales_comp_view (the router computes this).
    member_brand_ids      brands where the caller sells (rep or manager).
    A brand in neither set fails closed with 403 - the same answer whether the
    brand exists or not, so ids cannot be probed across tenants.
    """
    mgmt = sorted(set(management_brand_ids))
    member = sorted(set(member_brand_ids))
    brand = requested_brand
    if not brand:
        candidates = sorted(set(mgmt) | set(member))
        if len(candidates) != 1:
            raise ScopeRefused(400 if candidates else 403,
                               "brand_sales_org_id is required." if candidates
                               else "No compensation scope.")
        brand = candidates[0]
    if brand in mgmt:
        return {"brand_sales_org_id": brand, "payee_user_id": requested_payee,
                "scope": "management"}
    if brand in member:
        if requested_payee not in (None, user_id):
            raise ScopeRefused(403, "You may only view your own projection.")
        return {"brand_sales_org_id": brand, "payee_user_id": user_id,
                "scope": "seller"}
    raise ScopeRefused(403, "You do not have compensation visibility for "
                            "this brand.")


def _assemble(brand, payee_user_id, stage, earned, earned_n, earned_rows,
              pending_comm, pending_n, gross, weighted_rev, comm,
              weighted_comm, forecast_n, weighted_n, weighted_missing,
              deal_rows, excluded, blockers) -> Dict[str, Any]:
    return {
        "basis": "projection",
        "disclaimer": ("Forecast figures are not earned, not payable and not "
                       "owed. Only the 'earned' section comes from collected "
                       "payments."),
        "brand_sales_org_id": brand,
        "scope": "seller" if payee_user_id is not None else "management",
        "filters": {"payee_user_id": payee_user_id, "stage": stage},
        "earned": {
            "on_hold": {**_money_out(earned["on_hold"]), "count": earned_n["on_hold"]},
            "payable": {**_money_out(earned["payable"]), "count": earned_n["payable"]},
            "paid": {**_money_out(earned["paid"]), "count": earned_n["paid"]},
            "total": _money_out(sum(earned.values())),
            "entries": earned_rows,
        },
        "pending": {"commission": _money_out(pending_comm), "deal_count": pending_n},
        "forecast": {
            "gross_sales": _money_out(gross),
            "weighted_gross_sales": _money_out(weighted_rev if weighted_n else None),
            "commission": _money_out(comm),
            "weighted_commission": _money_out(weighted_comm if weighted_n else None),
            "deal_count": forecast_n,
            "weighted_deal_count": weighted_n,
            "weighted_missing_probability_count": weighted_missing,
        },
        "deals": deal_rows,
        "excluded": excluded,
        "blockers": blockers,
    }
