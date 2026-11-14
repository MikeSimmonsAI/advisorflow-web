"""Earned commission for EvoSys Pro Sales deals.

ONE RULE SET. The Deal Builder estimate, and the commission recorded when
money is actually collected, both come from
`app/services/deal_builder_commission.py` (RULES_VERSION). This module only
decides *which money* a collection is and *who* is credited, then asks that
module for the split. It never invents a rate.

  Who is credited      Snapshotted once per deal (SalesDealCredit) when the
                       customer accepts the proposal, or at the first
                       collection if that comes first. Moving a rep later
                       does not move past deals to the new manager.
  Setup vs monthly     Each collection is classified once (SalesCollectionSplit):
                       money goes to setup until setup is fully collected, the
                       rest is monthly service. Partial setup payments are
                       prorated.
  Empty / departed     A seat nobody holds, or held by someone who left the
                       company before the money arrived, is recorded in
                       SalesUnclaimedShare. EvoSys keeps it; nobody else's pay
                       goes up. Suspension is not departure.
  Idempotent           Re-running the same collection returns what exists.
  Holdback             14 days (payable_at = collected_at + 14 days).
  Refunds              Unchanged: recorded on the payment, never rewritten
                       into paid commission. Open cases are listed for owners.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.compensation_models import COMP_EARNED, CompensationEntry
from app.models.models import User
from app.models.sales_models import ROLE_SALES_MANAGER, Opportunity
from app.models.sales_team_models import (SalesCollectionSplit, SalesDealCredit,
                                          SalesDeparture, SalesUnclaimedShare)
from app.services import deal_builder_commission as dbc
from app.services import sales_team

HOLDBACK_DAYS = 14
BASIS = "evosys_launch_rules"
LEVEL = {"rep": 0, "asm": 1, "sevp": 2}
CENT = Decimal("0.01")

# Back-office package key -> launch-rules plan.
PACKAGE_TO_PLAN = {
    "starter": "basic", "basic": "basic",
    "growth": "growth",
    "professional": "professional", "pro": "professional",
    "multi_tenant": "custom_saas", "custom_saas": "custom_saas", "saas": "custom_saas",
}


class NotEarnable(Exception):
    pass


def _d(v) -> Decimal:
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def applies(db: Session, opp: Opportunity) -> bool:
    brand = sales_team.program_brand(db)
    return brand is not None and opp.brand_sales_org_id == brand.id


# ── Who is credited ──────────────────────────────────────────────────────────

def _chain(db: Session, brand_id: str, rep_id: Optional[str]) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {"rep": rep_id, "asm": None, "sevp": None}
    if not rep_id:
        return out
    seen = {rep_id}
    cur = rep_id
    for _ in range(10):
        u = db.query(User).filter(User.id == cur).first()
        m = sales_team.membership(db, u, brand_id) if u else None
        nxt = m.reports_to_user_id if m else None
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        up = db.query(User).filter(User.id == nxt).first()
        if up is None:
            break
        if sales_team._has(db, up, sales_team.CAP_EXECUTIVE, brand_id):
            out["sevp"] = nxt
            break
        um = sales_team.membership(db, up, brand_id)
        if out["asm"] is None and um is not None and um.role == ROLE_SALES_MANAGER:
            out["asm"] = nxt
        cur = nxt
    return out


def snapshot_credit(db: Session, opp: Opportunity,
                    accepted_at: Optional[datetime] = None) -> Optional[SalesDealCredit]:
    """The credited team for this deal. Written once; never changed later."""
    if not applies(db, opp):
        return None
    row = db.query(SalesDealCredit).filter(SalesDealCredit.opportunity_id == opp.id).first()
    if row is not None:
        return row
    chain = _chain(db, opp.brand_sales_org_id, opp.owner_user_id)
    row = SalesDealCredit(opportunity_id=opp.id, brand_sales_org_id=opp.brand_sales_org_id,
                          rules_version=dbc.RULES_VERSION,
                          rep_user_id=chain["rep"], asm_user_id=chain["asm"],
                          sevp_user_id=chain["sevp"],
                          accepted_at=accepted_at or datetime.utcnow())
    db.add(row)
    db.flush()
    return row


# ── Which money ──────────────────────────────────────────────────────────────

def plan_key_for(econ: Dict[str, Any]) -> str:
    pkg = econ.get("package")
    key = (getattr(pkg, "key", None) or "").strip().lower()
    if econ.get("deal_kind") == "custom" and key not in PACKAGE_TO_PLAN:
        return "custom_saas"
    plan = PACKAGE_TO_PLAN.get(key)
    if plan is None:
        raise NotEarnable("No launch commission rule for package %r." % (key or "none"))
    return plan


def classify(db: Session, opp: Opportunity, ref: str, amount: Decimal,
             setup_total: Decimal) -> SalesCollectionSplit:
    row = (db.query(SalesCollectionSplit)
           .filter(SalesCollectionSplit.opportunity_id == opp.id,
                   SalesCollectionSplit.collection_reference == ref).first())
    if row is not None:
        return row
    prior = _d(db.query(func.coalesce(func.sum(SalesCollectionSplit.setup_part), 0))
               .filter(SalesCollectionSplit.opportunity_id == opp.id).scalar())
    remaining = max(Decimal("0"), setup_total - prior)
    setup_part = min(amount, remaining)
    row = SalesCollectionSplit(opportunity_id=opp.id, collection_reference=ref,
                               collected_amount=amount, setup_part=setup_part,
                               monthly_part=amount - setup_part,
                               rules_version=dbc.RULES_VERSION)
    db.add(row)
    db.flush()
    return row


def _departed_before(db: Session, brand_id: str, user_id: str, when: datetime) -> bool:
    d = (db.query(SalesDeparture)
         .filter(SalesDeparture.brand_sales_org_id == brand_id,
                 SalesDeparture.user_id == user_id).first())
    return d is not None and d.departed_at <= when


# ── Earn ─────────────────────────────────────────────────────────────────────

def earn(db: Session, opp: Opportunity, *, collection_reference: str,
         collected_amount: Any, collected_at: Optional[datetime] = None,
         commit: bool = True) -> List[CompensationEntry]:
    from app.services import compensation as comp

    if (opp.status or "").lower() != "won":
        raise comp.NotEarnable("Opportunity %s is not Won; compensation cannot be earned." % opp.id)
    ref = (collection_reference or "").strip()
    if not ref:
        raise comp.NotEarnable("A collection reference is required.")
    amount = _d(collected_amount)
    if amount <= 0:
        raise comp.NotEarnable("A positive collected amount is required.")
    collected_at = collected_at or datetime.utcnow()

    econ = comp.deal_economics(db, opp)
    try:
        plan_key = plan_key_for(econ)
    except NotEarnable as exc:
        raise comp.NotEarnable(str(exc))
    plan = dbc.PLANS[plan_key]
    fee = _d(econ.get("implementation_fee"))
    mrr = _d(econ.get("mrr"))

    deal: Dict[str, Any] = {"plan": plan_key, "basis": "paid", "addon_setup": "0"}
    if plan["kind"] == "package":
        pkg_setup = min(fee, plan["setup"]) if fee > 0 else Decimal("0")
        deal["package_setup"] = str(pkg_setup)
        deal["addon_setup"] = str(max(Decimal("0"), fee - plan["setup"]))
    else:
        deal["saas_setup"] = str(fee)
    setup_total = fee

    credit = snapshot_credit(db, opp)
    split_row = classify(db, opp, ref, amount, setup_total)
    deal["collected_setup"] = str(split_row.setup_part)
    deal["monthly"] = str(split_row.monthly_part)
    deal["collected_monthly"] = str(split_row.monthly_part)

    payees = {"rep": credit.rep_user_id, "asm": credit.asm_user_id, "sevp": credit.sevp_user_id}
    reasons: Dict[str, str] = {}
    for role, uid in payees.items():
        if not uid:
            reasons[role] = "seat empty"
        elif _departed_before(db, opp.brand_sales_org_id, uid, collected_at):
            reasons[role] = "left the company"
    full = dbc.split(deal, filled={r: r not in reasons for r in dbc.ROLES})

    existing = {(e.payee_user_id, e.override_level): e for e in db.query(CompensationEntry).filter(
        CompensationEntry.opportunity_id == opp.id,
        CompensationEntry.collection_reference == ref).all()}
    have_unclaimed = {u.role for u in db.query(SalesUnclaimedShare).filter(
        SalesUnclaimedShare.opportunity_id == opp.id,
        SalesUnclaimedShare.collection_reference == ref).all()}

    made: List[CompensationEntry] = []
    for role in dbc.ROLES:
        layer = full["layers"][role]
        amt = _q(_d(layer["one_time"]) + _d(layer["monthly"]))
        if amt <= 0:
            continue
        if role in reasons:
            if role not in have_unclaimed:
                db.add(SalesUnclaimedShare(
                    brand_sales_org_id=opp.brand_sales_org_id, opportunity_id=opp.id,
                    collection_reference=ref, collected_at=collected_at, role=role,
                    user_id=payees[role], reason=reasons[role], amount=amt,
                    rules_version=dbc.RULES_VERSION))
            continue
        key = (payees[role], LEVEL[role])
        if key in existing:
            made.append(existing[key])
            continue
        e = CompensationEntry(
            brand_sales_org_id=opp.brand_sales_org_id, opportunity_id=opp.id,
            payee_user_id=payees[role], payee_kind=role, override_level=LEVEL[role],
            plan_id=None, rule_id=None,
            basis="%s:%s" % (BASIS, dbc.RULES_VERSION),
            rate_percent=Decimal(str(layer["monthly_rate"])) if split_row.monthly_part > 0 else None,
            rate_amount=(plan["payout"][role] if plan["kind"] == "package" else None),
            basis_implementation_fee=fee, basis_mrr=mrr,
            basis_term_months=econ.get("term_months"), basis_tcv=_d(econ.get("tcv")) or None,
            basis_collected_amount=amount, amount=amt,
            currency=econ.get("currency") or "USD", state=COMP_EARNED,
            collection_reference=ref, collected_at=collected_at, earned_at=datetime.utcnow(),
            payable_at=collected_at + timedelta(days=HOLDBACK_DAYS))
        db.add(e)
        made.append(e)
    if commit:
        db.commit()
    else:
        db.flush()
    return made


# ── Owner report ─────────────────────────────────────────────────────────────

def unclaimed_report(db: Session) -> Dict[str, Any]:
    from app.models.billing_models import BillingPayment
    brand = sales_team.program_brand(db)
    if brand is None:
        return {"rows": [], "total": 0.0, "refund_cases": []}
    rows = (db.query(SalesUnclaimedShare)
            .filter(SalesUnclaimedShare.brand_sales_org_id == brand.id)
            .order_by(SalesUnclaimedShare.collected_at.desc()).all())
    names = {}
    out = []
    for r in rows:
        if r.user_id and r.user_id not in names:
            u = db.query(User).filter(User.id == r.user_id).first()
            names[r.user_id] = (u.full_name or u.email) if u else None
        opp = db.query(Opportunity).filter(Opportunity.id == r.opportunity_id).first()
        out.append({"deal": getattr(opp, "company_name", None) or r.opportunity_id,
                    "opportunity_id": r.opportunity_id,
                    "collection": r.collection_reference,
                    "collected_at": r.collected_at.isoformat(),
                    "role": dbc.ROLE_LABELS.get(r.role, r.role),
                    "person": names.get(r.user_id), "reason": r.reason,
                    "amount": float(r.amount), "reversed": bool(r.reversed)})
    total = float(sum((_d(r.amount) for r in rows if not r.reversed), Decimal("0")))

    cases = []
    opp_ids = [o for (o,) in db.query(Opportunity.id)
               .filter(Opportunity.brand_sales_org_id == brand.id).all()]
    if opp_ids:
        pays = (db.query(BillingPayment)
                .filter(BillingPayment.opportunity_id.in_(opp_ids),
                        BillingPayment.refunded_cents > 0).all())
        for p in pays:
            paid_out = (db.query(func.coalesce(func.sum(CompensationEntry.amount), 0))
                        .filter(CompensationEntry.opportunity_id == p.opportunity_id,
                                CompensationEntry.collection_reference == p.collection_reference)
                        .scalar())
            if _d(paid_out) > 0:
                cases.append({"opportunity_id": p.opportunity_id,
                              "collection": p.collection_reference,
                              "refunded": float(Decimal(int(p.refunded_cents)) / 100),
                              "commission_on_this_payment": float(_d(paid_out)),
                              "status": "Not settled — refund policy decides recovery"})
    return {"rules_version": dbc.RULES_VERSION, "rows": out, "total": total,
            "refund_cases": cases}


# ── Projection (what the God Mode pricing views show) ────────────────────────

def project(db: Session, opp: Opportunity) -> Dict[str, Any]:
    """Same shape as compensation.compute(): setup payouts plus one month of
    monthly service, from the launch rules. Writes nothing."""
    from app.services import compensation as comp
    econ = comp.deal_economics(db, opp)
    base = {"plan_id": None, "plan_name": "EvoSys launch rules %s" % dbc.RULES_VERSION,
            "holdback_days": HOLDBACK_DAYS, "currency": econ.get("currency") or "USD",
            "capped": False, "cap_amount": None, "economics": econ.get("quote"),
            "deal_kind": econ.get("deal_kind")}
    try:
        plan_key = plan_key_for(econ)
    except NotEarnable:
        return {**base, "status": comp.PROJ_UNCONFIGURED, "payouts": [],
                "seller_total": None, "override_total": None, "total": None}
    plan = dbc.PLANS[plan_key]
    fee = _d(econ.get("implementation_fee"))
    deal: Dict[str, Any] = {"plan": plan_key, "basis": "quote", "monthly": str(_d(econ.get("mrr")))}
    if plan["kind"] == "package":
        deal["package_setup"] = str(min(fee, plan["setup"]))
        deal["addon_setup"] = str(max(Decimal("0"), fee - plan["setup"]))
    else:
        deal["saas_setup"] = str(fee)
    credit = db.query(SalesDealCredit).filter(SalesDealCredit.opportunity_id == opp.id).first()
    chain = ({"rep": credit.rep_user_id, "asm": credit.asm_user_id, "sevp": credit.sevp_user_id}
             if credit else _chain(db, opp.brand_sales_org_id, opp.owner_user_id))
    full = dbc.split(deal, filled={r: bool(chain.get(r)) for r in dbc.ROLES})
    payouts = []
    for role in dbc.ROLES:
        if not chain.get(role):
            continue
        layer = full["layers"][role]
        amt = _q(_d(layer["one_time"]) + _d(layer["monthly"]))
        payouts.append({"payee_user_id": chain[role],
                        "payee_kind": comp.PAYEE_SELLER if role == "rep" else comp.PAYEE_OVERRIDE,
                        "override_level": None if role == "rep" else LEVEL[role],
                        "rule_id": None, "basis": "%s:%s" % (BASIS, dbc.RULES_VERSION),
                        "rate_percent": layer["monthly_rate"],
                        "rate_amount": float(plan["payout"][role]) if plan["kind"] == "package" else None,
                        "amount": amt})
    seller = sum((p["amount"] for p in payouts if p["payee_kind"] == comp.PAYEE_SELLER), Decimal("0"))
    total = sum((p["amount"] for p in payouts), Decimal("0"))
    status = comp.PROJ_PENDING_APPROVAL if comp.has_pending_pricing_approval(db, opp) else comp.PROJ_PROJECTED
    return {**base, "status": status, "payouts": payouts, "seller_total": seller,
            "override_total": total - seller, "total": total}
