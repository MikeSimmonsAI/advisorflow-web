"""THE SPLIT: who gets what share of a basis, and whether the split is sane.

TWO PARTIES IS A CASE, NOT A SHAPE
----------------------------------
The first arrangement this serves is 75/25. Nothing in this module knows that.
An allocation is N rows; 70/20/10 and 80/20 and a single party at 100 are the
same code path, and a third party added to a live agreement is a row, not a
migration.

WHAT IT REFUSES
---------------
An allocation that does not reconcile is REFUSED rather than normalised. The
temptation with a split that adds to 97% is to scale it, or to give the missing
3% to somebody, and both of those invent a commercial term. The allocation is
reported invalid, activation blocks on it, and a human fixes the number.

A party whose percentage is NULL is UNKNOWN. It is not zero, it does not
receive nothing, and a settlement will not calculate around it. The one
structure where a missing percentage is legitimate is `residual_to_party`,
where exactly one party is defined AS the remainder — and even there the
remainder is computed and shown, never left implicit.

ROUNDING
--------
Largest remainder by default: every cent of the basis is allocated, and the odd
cents go to the parties with the largest fractional remainders, deterministically
(ties break on the allocation's declared position, so the same inputs always
produce the same statement). The alternative policy rounds each party
independently and is offered because some agreements are written that way — it
can leave the allocated total a cent or two off the basis, and the calculation
reports that difference instead of hiding it.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.commercial_models import (
    CommercialAgreement, CommercialAllocation, CommercialParty,
)

RULE_FULL       = "full_allocation"
RULE_RESIDUAL   = "residual_to_party"
RULE_CUSTOM     = "custom"

ROUND_LARGEST_REMAINDER = "largest_remainder"
ROUND_HALF_UP_EACH      = "round_half_up"

_HUNDRED = Decimal("100")
_Q = Decimal("0.000001")


def _d(value) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def parties(db: Session, agreement: CommercialAgreement) -> List[CommercialParty]:
    return (db.query(CommercialParty)
            .filter(CommercialParty.agreement_id == agreement.id)
            .order_by(CommercialParty.position, CommercialParty.created_at)
            .all())


def allocations(db: Session,
                agreement: CommercialAgreement) -> List[CommercialAllocation]:
    return (db.query(CommercialAllocation)
            .filter(CommercialAllocation.agreement_id == agreement.id)
            .order_by(CommercialAllocation.position, CommercialAllocation.created_at)
            .all())


def rows(db: Session, agreement: CommercialAgreement) -> List[Dict[str, Any]]:
    """Parties joined to their allocation, in display order.

    A party with no allocation row appears with `percent: None` — which reads
    as UNKNOWN everywhere downstream, exactly as a NULL percent does. A named
    party with no stated share is precisely the situation the validator has to
    refuse.
    """
    by_party = {a.party_id: a for a in allocations(db, agreement)}
    out = []
    for p in parties(db, agreement):
        a = by_party.get(p.id)
        out.append({
            "party_id": p.id,
            "party_key": p.party_key,
            "display_name": p.display_name,
            "party_type": p.party_type,
            "organization_id": p.organization_id,
            "brand_sales_org_id": p.brand_sales_org_id,
            "external_reference": p.external_reference,
            "is_payee": bool(p.is_payee),
            "position": int(p.position or 0),
            "allocation_id": getattr(a, "id", None),
            "percent": _d(getattr(a, "percent", None)),
            "fixed_amount_cents": getattr(a, "fixed_amount_cents", None),
            "tier_json": getattr(a, "tier_json", None),
            "note": getattr(a, "note", None) or getattr(p, "note", None),
        })
    return out


def validate(db: Session, agreement: CommercialAgreement,
             allocation_rule: Optional[str]) -> Dict[str, Any]:
    """Does this allocation reconcile under its own rule?

    Returns a verdict rather than raising, because "not yet" is the normal
    state of an agreement in TERMS_REQUIRED and the screens need to SHOW why
    rather than crash on it.
    """
    detail = rows(db, agreement)
    reasons: List[str] = []

    if not detail:
        return {"valid": False, "rule": allocation_rule, "parties": [],
                "total_percent": None, "residual_percent": None,
                "unknown_party_ids": [],
                "reasons": ["No parties have been added to this agreement."]}

    known = [r for r in detail if r["percent"] is not None]
    unknown = [r for r in detail if r["percent"] is None]
    total = sum((r["percent"] for r in known), Decimal("0")).quantize(_Q)

    for r in known:
        if r["percent"] < 0 or r["percent"] > _HUNDRED:
            reasons.append("%s has a share of %s%%, which is outside 0–100."
                           % (r["display_name"], r["percent"].normalize()))

    residual = None

    if allocation_rule == RULE_RESIDUAL:
        if len(unknown) != 1:
            reasons.append(
                "This agreement allocates a remainder, so exactly one party "
                "must be left without a stated percentage. %d are."
                % len(unknown))
        if total > _HUNDRED:
            reasons.append("The stated percentages total %s%%, which leaves no "
                           "remainder to allocate." % total.normalize())
        elif len(unknown) == 1:
            residual = (_HUNDRED - total).quantize(_Q)

    elif allocation_rule == RULE_CUSTOM:
        # A custom structure is legitimate and is NOT auto-validated. It is
        # recorded, shown, and refused by the settlement engine until somebody
        # states percentages the engine can actually calculate from.
        if unknown:
            reasons.append(
                "%d part%s of this custom structure ha%s no stated percentage."
                % (len(unknown), "y" if len(unknown) == 1 else "ies",
                   "s" if len(unknown) == 1 else "ve"))
        elif total != _HUNDRED:
            reasons.append("The stated percentages total %s%%, not 100%%."
                           % total.normalize())

    else:
        # full_allocation, and the default when the rule itself is unanswered:
        # the strictest reading, so an unanswered rule never loosens anything.
        if unknown:
            reasons.append(
                "%s ha%s no stated percentage."
                % (", ".join(r["display_name"] for r in unknown),
                   "s" if len(unknown) == 1 else "ve"))
        elif total != _HUNDRED:
            reasons.append("The percentages total %s%%, not 100%%."
                           % total.normalize())

    if allocation_rule is None:
        reasons.append("The allocation rule for this agreement has not been "
                       "answered, so the split is checked as a full allocation.")

    return {
        "valid": not reasons,
        "rule": allocation_rule,
        "parties": detail,
        "total_percent": total,
        "residual_percent": residual,
        "unknown_party_ids": [r["party_id"] for r in unknown],
        "reasons": reasons,
    }


def effective_percents(verdict: Dict[str, Any]) -> Dict[str, Decimal]:
    """Party id → the percentage actually applied, residual resolved.

    Only meaningful on a verdict that validated. A caller that hands an invalid
    verdict in gets an empty map, which is what makes it impossible to settle
    against a split that does not reconcile by forgetting one `if`.
    """
    if not verdict.get("valid"):
        return {}
    out: Dict[str, Decimal] = {}
    residual = verdict.get("residual_percent")
    for r in verdict["parties"]:
        if r["percent"] is not None:
            out[r["party_id"]] = r["percent"]
        elif residual is not None:
            out[r["party_id"]] = residual
    return out


def split(basis_cents: int, verdict: Dict[str, Any],
          rounding_policy: Optional[str] = None) -> Dict[str, Any]:
    """Apply a validated allocation to a basis.

    `basis_cents` is an integer because money is. The caller has already
    established that the basis is KNOWN — this function has no opinion about
    where it came from and will happily split zero, which is correct: a period
    that genuinely collected nothing settles at nothing for everybody. What it
    will not do is split an unknown, because it is never handed one.
    """
    percents = effective_percents(verdict)
    if not percents:
        return {"basis_cents": basis_cents, "lines": [], "allocated_cents": 0,
                "unallocated_cents": basis_cents,
                "rounding_policy": rounding_policy or ROUND_LARGEST_REMAINDER}

    policy = rounding_policy or ROUND_LARGEST_REMAINDER
    detail = {r["party_id"]: r for r in verdict["parties"]}

    # Fixed amounts come off the top before anything is split, and a fixed
    # total larger than the basis is reported rather than absorbed.
    fixed_total = sum(int(r["fixed_amount_cents"] or 0)
                      for r in verdict["parties"])
    remaining = basis_cents - fixed_total
    shortfall = remaining < 0
    if shortfall:
        remaining = 0

    ordered = sorted(percents.items(),
                     key=lambda kv: (int(detail[kv[0]].get("position") or 0),
                                     kv[0]))

    exact = {pid: (Decimal(remaining) * pct / _HUNDRED)
             for pid, pct in ordered}

    if policy == ROUND_HALF_UP_EACH:
        amounts = {pid: int(v.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                   for pid, v in exact.items()}
    else:
        floors = {pid: int(v // 1) for pid, v in exact.items()}
        short = remaining - sum(floors.values())
        remainders = sorted(
            ((exact[pid] - Decimal(floors[pid]), int(detail[pid].get("position") or 0), pid)
             for pid, _ in ordered),
            key=lambda t: (-t[0], t[1], t[2]))
        amounts = dict(floors)
        for i in range(max(0, short)):
            amounts[remainders[i % len(remainders)][2]] += 1

    lines = []
    for pid, pct in ordered:
        row = detail[pid]
        fixed = int(row["fixed_amount_cents"] or 0)
        lines.append({
            "party_id": pid,
            "party_key": row["party_key"],
            "display_name": row["display_name"],
            "is_payee": row["is_payee"],
            "percent": str(pct.normalize()),
            "fixed_amount_cents": fixed or None,
            "share_cents": amounts[pid],
            "amount_cents": amounts[pid] + fixed,
        })

    allocated = sum(line["amount_cents"] for line in lines)
    return {
        "basis_cents": basis_cents,
        "lines": lines,
        "allocated_cents": allocated,
        "unallocated_cents": basis_cents - allocated,
        "fixed_total_cents": fixed_total or None,
        "fixed_exceeds_basis": shortfall,
        "rounding_policy": policy,
    }
