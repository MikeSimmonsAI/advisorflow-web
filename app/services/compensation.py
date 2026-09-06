"""WHAT A DEAL PAYS, COMPUTED ONE WAY.

THE PROJECTION AND THE PAYOUT ARE THE SAME FUNCTION. `compute()` answers "what
would this deal pay" for an open opportunity and "what does this deal pay" for a
won and collected one. A separate projection formula is how a pipeline forecast
and a payroll run end up disagreeing about the same deal, with nobody able to
say which is wrong.

THE ECONOMICS COME FROM ONE PLACE TOO. Every figure this engine multiplies
comes out of `package_pricing.quote()` - the same call the proposal, the deal
screen and the customer's document use. A custom deal therefore feeds
compensation automatically, because it already feeds the quote; there is no
second path for negotiated pricing to travel down.

NOTHING HERE INVENTS A RATE. An unconfigured plan pays zero and reports
`unconfigured`, so a screen says "no plan configured" rather than showing a
confident $0 that looks like a decision.

PROJECTION IS NOT A LIABILITY
-----------------------------
`compute()` returns numbers. It writes nothing. Rows only appear through
`earn()`, which refuses to run without a collected payment - so an open
pipeline cannot produce a payroll record no matter how it is called.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.compensation_models import (
    BASIS_FIXED, BASIS_PCT_COLLECTED, BASIS_PCT_MRR, BASIS_PCT_SETUP,
    BASIS_PCT_TCV, COMP_EARNED, COMP_PAID, COMP_PAYABLE, DEAL_CUSTOM,
    DEAL_STANDARD, DEFAULT_HOLDBACK_DAYS, PAYEE_OVERRIDE, PAYEE_SELLER,
    PROJ_PENDING_APPROVAL, PROJ_PROJECTED, PROJ_UNCONFIGURED,
    CompensationEntry, CompensationPackageCap, CompensationPlan,
    CompensationRule,
)
from app.models.sales_models import (
    APPROVAL_PENDING, SCOPE_BRAND_SALES_ORG, BrandPackage, Membership,
    Opportunity, PricingApprovalRequest,
)
from app.services import package_pricing as pp

log = logging.getLogger(__name__)

ZERO = Decimal("0")


def _dec(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _money(v: Optional[Decimal]) -> Optional[float]:
    return None if v is None else float(v.quantize(Decimal("0.01")))


# ── Which plan applies ───────────────────────────────────────────────────────

def resolve_plan(db: Session, brand_sales_org_id: Optional[str],
                 on_date: Optional[date] = None) -> Optional[CompensationPlan]:
    """The plan in force for this brand on this date.

    A brand's own plan beats the platform default. Among plans of equal
    specificity the LATEST effective_from wins, which is what makes a new plan
    supersede an old one by being dated rather than by deleting it.
    """
    on_date = on_date or date.today()
    rows = (db.query(CompensationPlan)
            .filter(CompensationPlan.is_active.is_(True),
                    CompensationPlan.effective_from <= on_date,
                    or_(CompensationPlan.effective_to.is_(None),
                        CompensationPlan.effective_to >= on_date),
                    or_(CompensationPlan.brand_sales_org_id == brand_sales_org_id,
                        CompensationPlan.brand_sales_org_id.is_(None)))
            .all())
    if not rows:
        return None
    rows.sort(key=lambda p: (1 if p.brand_sales_org_id else 0, p.effective_from),
              reverse=True)
    return rows[0]


def rules_for(db: Session, plan: CompensationPlan, package_id: Optional[str],
              deal_kind: str) -> List[CompensationRule]:
    """Active rules matching this package and deal kind.

    A rule naming the package beats a catch-all rule for the same payee slot.
    Without that, a general "% of setup" line would pay ON TOP of the specific
    "$500 on Starter" line and every Starter deal would pay twice.
    """
    rows = (db.query(CompensationRule)
            .filter(CompensationRule.plan_id == plan.id,
                    CompensationRule.is_active.is_(True))
            .order_by(CompensationRule.sort_order.asc())
            .all())

    def applies(r: CompensationRule) -> bool:
        if r.package_id is not None and r.package_id != package_id:
            return False
        if r.deal_kind is not None and r.deal_kind != deal_kind:
            return False
        return True

    candidates = [r for r in rows if applies(r)]

    # One rule per payee slot: (payee_kind, override_level). Most specific wins.
    best: Dict[Any, CompensationRule] = {}
    for r in candidates:
        slot = (r.payee_kind, r.override_level)
        incumbent = best.get(slot)
        if incumbent is None:
            best[slot] = r
            continue
        if (r.package_id is not None) and (incumbent.package_id is None):
            best[slot] = r
        elif (r.deal_kind is not None) and (incumbent.deal_kind is None) \
                and (r.package_id is not None) == (incumbent.package_id is not None):
            best[slot] = r
    return sorted(best.values(), key=lambda r: (r.sort_order, r.override_level or 0))


# ── The org chart ────────────────────────────────────────────────────────────

def upline(db: Session, user_id: Optional[str], brand_sales_org_id: str,
           max_levels: int) -> List[str]:
    """Everyone above this person in this brand, nearest first.

    Returns only people who ACTUALLY EXIST as a manager relationship. A level
    with nobody in it yields no entry rather than a placeholder, because a
    phantom override is expense the business does not owe to anybody and would
    still show up in a projected payroll total.

    Cycle-guarded: a reporting loop entered by hand would otherwise walk until
    max_levels every time, silently paying whoever it met twice.
    """
    chain: List[str] = []
    seen = {user_id}
    current = user_id
    for _ in range(max(0, int(max_levels or 0))):
        if not current:
            break
        m = (db.query(Membership)
             .filter(Membership.user_id == current,
                     Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                     Membership.scope_id == brand_sales_org_id,
                     Membership.is_active.is_(True))
             .first())
        nxt = getattr(m, "reports_to_user_id", None) if m else None
        if not nxt or nxt in seen:
            break
        chain.append(nxt)
        seen.add(nxt)
        current = nxt
    return chain


# ── The deal's economics ─────────────────────────────────────────────────────

def deal_economics(db: Session, opp: Opportunity) -> Dict[str, Any]:
    """The numbers compensation multiplies, from the same quote everything else
    reads. Never a second derivation."""
    pkg = None
    if opp.selected_package_id:
        pkg = (db.query(BrandPackage)
               .filter(BrandPackage.id == opp.selected_package_id).first())
    custom = pp.custom_rate(opp)
    q = pp.quote(pkg, opp.billing_option, opp=opp,
                 term_months=opp.contract_term_months, custom=custom)
    return {
        "package": pkg,
        "quote": q,
        "deal_kind": DEAL_CUSTOM if custom else DEAL_STANDARD,
        "implementation_fee": _dec(q.get("implementation_fee")),
        "mrr": _dec(q.get("mrr")),
        "term_months": q.get("term_months"),
        "tcv": _dec(q.get("total_contract_value")),
        "currency": q.get("currency") or "USD",
    }


def _rule_amount(rule: CompensationRule, econ: Dict[str, Any],
                 collected_amount: Optional[Decimal]) -> Optional[Decimal]:
    """What one rule pays on these economics, or None when it cannot say.

    None is not zero. A percentage-of-MRR rule on a deal with no recurring rate
    has nothing to compute against, and returning zero would report a confident
    "pays nothing" where the truth is "this rule does not apply here".
    """
    basis = rule.basis
    if basis == BASIS_FIXED:
        return _dec(rule.amount)

    pct = _dec(rule.percent)
    if pct is None:
        return None
    factor = pct / Decimal(100)

    if basis == BASIS_PCT_SETUP:
        base = econ["implementation_fee"]
        return None if base is None else base * factor

    if basis == BASIS_PCT_MRR:
        mrr = econ["mrr"]
        if mrr is None:
            return None
        # How many months this pays on. An explicit number wins; otherwise the
        # committed term. A month-to-month deal has no committed term, so it
        # pays on ONE month rather than on a contract length that does not
        # exist - the same refusal to invent a total that package_pricing makes.
        months = rule.recurring_months
        if months is None:
            months = econ["term_months"] or 1
        return mrr * Decimal(int(months)) * factor

    if basis == BASIS_PCT_TCV:
        tcv = econ["tcv"]
        return None if tcv is None else tcv * factor

    if basis == BASIS_PCT_COLLECTED:
        # Only meaningful against real money. Projections say so rather than
        # guessing what will be collected.
        return None if collected_amount is None else collected_amount * factor

    return None


def _cap_for(db: Session, plan: CompensationPlan,
             package_id: Optional[str]) -> Optional[Decimal]:
    if not package_id:
        return None
    row = (db.query(CompensationPackageCap)
           .filter(CompensationPackageCap.plan_id == plan.id,
                   CompensationPackageCap.package_id == package_id).first())
    return _dec(row.max_total_payout) if row else None


def has_pending_pricing_approval(db: Session, opp: Opportunity) -> bool:
    return (db.query(PricingApprovalRequest)
            .filter(PricingApprovalRequest.opportunity_id == opp.id,
                    PricingApprovalRequest.status == APPROVAL_PENDING)
            .first()) is not None


# ── The one computation ──────────────────────────────────────────────────────

def compute(db: Session, opp: Opportunity, *,
            collected_amount: Optional[Any] = None,
            on_date: Optional[date] = None) -> Dict[str, Any]:
    """What this deal pays, and to whom. WRITES NOTHING.

    `collected_amount` is supplied when earning against a real payment and left
    out when projecting. Rules that price off collected funds therefore return
    a figure at earn time and decline to guess at projection time.
    """
    econ = deal_economics(db, opp)
    plan = resolve_plan(db, opp.brand_sales_org_id, on_date)
    collected = _dec(collected_amount)

    if plan is None:
        return {
            "status": PROJ_UNCONFIGURED,
            "plan_id": None, "plan_name": None,
            "currency": econ["currency"],
            "payouts": [], "seller_total": None, "override_total": None,
            "total": None, "capped": False, "cap_amount": None,
            "economics": econ["quote"], "deal_kind": econ["deal_kind"],
        }

    pkg_id = opp.selected_package_id
    rules = rules_for(db, plan, pkg_id, econ["deal_kind"])
    chain = upline(db, opp.owner_user_id, opp.brand_sales_org_id,
                   plan.max_override_levels)

    payouts: List[Dict[str, Any]] = []
    for rule in rules:
        if rule.payee_kind == PAYEE_SELLER:
            payee = opp.owner_user_id
            level = None
        else:
            level = int(rule.override_level or 1)
            # NO PHANTOM OVERRIDES. A level nobody occupies pays nobody.
            if level < 1 or level > len(chain):
                continue
            payee = chain[level - 1]
        if not payee:
            continue

        amount = _rule_amount(rule, econ, collected)
        if amount is None:
            continue
        if amount < ZERO:
            amount = ZERO
        rule_cap = _dec(rule.max_amount)
        if rule_cap is not None and amount > rule_cap:
            amount = rule_cap

        payouts.append({
            "payee_user_id": payee,
            "payee_kind": rule.payee_kind,
            "override_level": level,
            "rule_id": rule.id,
            "basis": rule.basis,
            "rate_percent": _money(_dec(rule.percent)),
            "rate_amount": _money(_dec(rule.amount)),
            "amount": amount,
        })

    total = sum((p["amount"] for p in payouts), ZERO)

    # THE PACKAGE CAP APPLIES TO THE WHOLE DEAL, NOT TO ONE CHEQUE.
    # Reduced proportionally so no single payee absorbs the whole shortfall,
    # which is what happens when a cap is applied by truncating the last rule
    # in the list and makes the payout depend on rule ordering.
    cap = _cap_for(db, plan, pkg_id)
    capped = False
    if cap is not None and total > cap and total > ZERO:
        capped = True
        scale = cap / total
        for p in payouts:
            p["capped_from_amount"] = p["amount"]
            p["amount"] = (p["amount"] * scale)
        total = cap

    for p in payouts:
        p["amount"] = p["amount"].quantize(Decimal("0.01"))
        if "capped_from_amount" in p:
            p["capped_from_amount"] = p["capped_from_amount"].quantize(Decimal("0.01"))

    seller_total = sum((p["amount"] for p in payouts
                        if p["payee_kind"] == PAYEE_SELLER), ZERO)
    override_total = sum((p["amount"] for p in payouts
                          if p["payee_kind"] == PAYEE_OVERRIDE), ZERO)

    status = PROJ_PROJECTED
    if has_pending_pricing_approval(db, opp):
        status = PROJ_PENDING_APPROVAL

    return {
        "status": status,
        "plan_id": plan.id,
        "plan_name": plan.name,
        "holdback_days": plan.holdback_days,
        "currency": econ["currency"],
        "payouts": payouts,
        "seller_total": seller_total,
        "override_total": override_total,
        "total": sum((p["amount"] for p in payouts), ZERO),
        "capped": capped,
        "cap_amount": cap,
        "economics": econ["quote"],
        "deal_kind": econ["deal_kind"],
    }


def compute_public(db: Session, opp: Opportunity, **kw) -> Dict[str, Any]:
    """`compute()` with Decimals flattened for JSON."""
    r = compute(db, opp, **kw)
    out = dict(r)
    out["payouts"] = [
        {**p, "amount": _money(p["amount"]),
         "capped_from_amount": _money(p.get("capped_from_amount"))}
        for p in r["payouts"]
    ]
    for k in ("seller_total", "override_total", "total", "cap_amount"):
        out[k] = _money(r[k]) if r[k] is not None else None
    return out


# ── Earning: the only path that writes ───────────────────────────────────────

class NotEarnable(Exception):
    """Refusing to create a payout. Never caught into a silent no-op."""


def earn(db: Session, opp: Opportunity, *, collection_reference: str,
         collected_amount: Any, collected_at: Optional[datetime] = None,
         commit: bool = True) -> List[CompensationEntry]:
    """Turn a projection into money owed, because funds actually arrived.

    THE TWO CONDITIONS ARE BOTH REQUIRED and are checked here rather than by
    the caller: the deal must be Won, and a payment must have been collected.
    Marking an opportunity Won does not pay anybody, which is the entire reason
    this function will not accept a deal without a collection reference.

    Idempotent by the unique constraint on (opportunity, payee, level,
    collection_reference): re-running against the same collected payment
    returns the rows that already exist instead of paying twice.
    """
    if (opp.status or "").lower() != "won":
        raise NotEarnable("Opportunity %s is not Won; compensation cannot be "
                          "earned." % opp.id)
    ref = (collection_reference or "").strip()
    if not ref:
        raise NotEarnable("A collection reference is required — compensation is "
                          "earned on collected funds, not on a stage change.")
    amount = _dec(collected_amount)
    if amount is None or amount <= ZERO:
        raise NotEarnable("A positive collected amount is required.")

    collected_at = collected_at or datetime.utcnow()
    result = compute(db, opp, collected_amount=amount)
    if result["status"] == PROJ_UNCONFIGURED:
        raise NotEarnable("No compensation plan is configured for this sales "
                          "organization; nothing can be earned.")

    existing = {
        (e.payee_user_id, e.override_level): e
        for e in db.query(CompensationEntry).filter(
            CompensationEntry.opportunity_id == opp.id,
            CompensationEntry.collection_reference == ref).all()
    }

    econ = result["economics"]
    holdback = int(result.get("holdback_days") or DEFAULT_HOLDBACK_DAYS)
    made: List[CompensationEntry] = []
    for p in result["payouts"]:
        key = (p["payee_user_id"], p["override_level"])
        if key in existing:
            made.append(existing[key])
            continue
        entry = CompensationEntry(
            brand_sales_org_id=opp.brand_sales_org_id,
            opportunity_id=opp.id,
            payee_user_id=p["payee_user_id"],
            payee_kind=p["payee_kind"],
            override_level=p["override_level"],
            plan_id=result["plan_id"],
            rule_id=p["rule_id"],
            basis=p["basis"],
            rate_percent=p["rate_percent"],
            rate_amount=p["rate_amount"],
            basis_implementation_fee=_dec(econ.get("implementation_fee")),
            basis_mrr=_dec(econ.get("mrr")),
            basis_term_months=econ.get("term_months"),
            basis_tcv=_dec(econ.get("total_contract_value")),
            basis_collected_amount=amount,
            amount=p["amount"],
            currency=result["currency"],
            capped_from_amount=p.get("capped_from_amount"),
            state=COMP_EARNED,
            collection_reference=ref,
            collected_at=collected_at,
            earned_at=datetime.utcnow(),
            # Stored, not derived on read: a later change to the plan's holdback
            # must not move a payout date somebody was already promised.
            payable_at=collected_at + timedelta(days=holdback),
        )
        db.add(entry)
        made.append(entry)

    if commit:
        db.commit()
    return made


def promote_due_to_payable(db: Session, now: Optional[datetime] = None,
                           commit: bool = True) -> int:
    """EARNED rows whose holdback has elapsed become PAYABLE. Nothing else."""
    now = now or datetime.utcnow()
    rows = (db.query(CompensationEntry)
            .filter(CompensationEntry.state == COMP_EARNED,
                    CompensationEntry.payable_at.isnot(None),
                    CompensationEntry.payable_at <= now)
            .all())
    for r in rows:
        r.state = COMP_PAYABLE
    if commit and rows:
        db.commit()
    return len(rows)


def mark_paid(db: Session, entry: CompensationEntry, *, payment_reference: str,
              paid_at: Optional[datetime] = None, commit: bool = True) -> CompensationEntry:
    """Only a PAYABLE entry can be paid. Paying an EARNED one would skip the
    holdback the business runs on."""
    if entry.state != COMP_PAYABLE:
        raise NotEarnable("Only a payable entry can be paid; this one is %r."
                          % entry.state)
    ref = (payment_reference or "").strip()
    if not ref:
        raise NotEarnable("A payment reference is required.")
    entry.state = COMP_PAID
    entry.payment_reference = ref
    entry.paid_at = paid_at or datetime.utcnow()
    if commit:
        db.commit()
    return entry
