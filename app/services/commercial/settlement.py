"""COLLECTIONS, AND THE ARITHMETIC THIS PLATFORM WILL AND WILL NOT DO.

THE ONE RULE
------------
UNKNOWN COLLECTIONS DO NOT BECOME $0.

A period with no authoritative record is a period this platform cannot settle,
and it says so. It does not sum an empty list to zero and report that everybody
is owed nothing — which is arithmetically true and commercially a lie, and is
the exact failure mode that makes an automated settlement engine dangerous.

WHERE A FIGURE IS ALLOWED TO COME FROM
--------------------------------------
  STRIPE            payments this platform already recorded in `billing_payments`
                    under T2. Read, summed, and written into a record that still
                    has to be APPROVED by a person. Nothing is duplicated: the
                    payment rows stay T2's.
  MANUAL            typed by a named person, approved by another.

Every other source an agreement may NAME — a connected accounting system, an
external biller, an import, a future integration — is modelled so the agreement
can state where the truth lives, and produces no records here. Naming a source
this platform cannot read is honest; pretending to read it would not be.

WHAT THIS MODULE WILL NEVER DO
------------------------------
Move money. `distribute()` exists to REFUSE, loudly and in the audit log,
because the question "can the system pay a party its share" has to have an
unambiguous answer in the code rather than in somebody's memory of a
conversation. There is no payout rail behind a custom commercial agreement, and
until there is one that has been reviewed, a settlement is a statement of what
is owed and nothing more.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.commercial_models import (
    ATTR_UNKNOWN_REVIEW, ATTRIBUTION_RECORD_STATES, COLLECTION_APPROVED,
    COLLECTION_DRAFT, COLLECTION_REJECTED, COLLECTION_SOURCES,
    COLLECTION_SUBMITTED, CommercialAgreement, CommercialCollectionRecord,
    CommercialSettlement, SETTLE_APPROVED, SETTLE_CALCULATED,
    SETTLE_REVIEW_REQUIRED, SOURCE_MANUAL_APPROVED, SOURCE_STRIPE,
)
from app.models.models import User
from app.services.commercial import audit as caudit
from app.services.commercial import revenue_share as rs
from app.services.commercial import terms as t

# There is no payout rail. Stated once, read everywhere, so that turning this
# on is a deliberate change to a named constant and not an accident.
PAYOUT_EXECUTION_SUPPORTED = False

# Which share bases need adjustments to be STATED before a basis can be formed.
_BASES_NEEDING_ADJUSTMENTS = ("net_collections",
                              "collections_after_defined_adjustments")


# ════════════════════════════════════════════════════════════════════════════
# COLLECTION RECORDS
# ════════════════════════════════════════════════════════════════════════════


def records(db: Session, agreement: CommercialAgreement, *,
            period_start: Optional[date] = None,
            period_end: Optional[date] = None,
            statuses: Optional[tuple] = None) -> List[CommercialCollectionRecord]:
    q = (db.query(CommercialCollectionRecord)
         .filter(CommercialCollectionRecord.agreement_id == agreement.id))
    if period_start is not None:
        q = q.filter(CommercialCollectionRecord.period_end >= period_start)
    if period_end is not None:
        q = q.filter(CommercialCollectionRecord.period_start <= period_end)
    if statuses:
        q = q.filter(CommercialCollectionRecord.status.in_(list(statuses)))
    return q.order_by(CommercialCollectionRecord.period_start,
                      CommercialCollectionRecord.created_at).all()


def record_collection(db: Session, agreement: CommercialAgreement, actor: User, *,
                      period_start: date, period_end: date,
                      source: str, gross_cents: int,
                      adjustments_cents: Optional[int] = None,
                      adjustments_note: Optional[str] = None,
                      attribution_state: str = ATTR_UNKNOWN_REVIEW,
                      source_reference: Optional[str] = None,
                      note: Optional[str] = None,
                      submit: bool = True) -> CommercialCollectionRecord:
    """Record what was collected. Approval is somebody else's act."""
    if source not in COLLECTION_SOURCES:
        raise HTTPException(status_code=400,
                            detail="Unknown collections source '%s'." % source)
    if attribution_state not in ATTRIBUTION_RECORD_STATES:
        raise HTTPException(status_code=400,
                            detail="Unknown attribution state '%s'." % attribution_state)
    if period_end < period_start:
        raise HTTPException(status_code=400,
                            detail="The period ends before it starts.")
    try:
        gross = int(gross_cents)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400,
                            detail="Collections must be a whole number of cents.")
    if gross < 0:
        raise HTTPException(status_code=400,
                            detail="Collections cannot be negative. Record a "
                                   "refund as an adjustment.")

    row = CommercialCollectionRecord(
        agreement_id=agreement.id,
        organization_id=agreement.organization_id,
        period_start=period_start, period_end=period_end,
        source=source, source_reference=(source_reference or None),
        currency=agreement.currency,
        gross_cents=gross,
        adjustments_cents=(int(adjustments_cents)
                           if adjustments_cents is not None else None),
        adjustments_note=(adjustments_note or None),
        attribution_state=attribution_state,
        status=(COLLECTION_SUBMITTED if submit else COLLECTION_DRAFT),
        submitted_by_user_id=(getattr(actor, "id", None) if submit else None),
        submitted_at=(datetime.utcnow() if submit else None),
        note=(note or None),
        created_by_user_id=getattr(actor, "id", None),
    )
    db.add(row)
    db.flush()

    caudit.record(db, agreement, actor, caudit.A_COLLECTION_RECORDED,
                  target_type="commercial_collection_record", target_id=row.id,
                  after={"period_start": str(period_start),
                         "period_end": str(period_end),
                         "source": source, "gross_cents": gross,
                         "adjustments_cents": row.adjustments_cents,
                         "attribution_state": attribution_state,
                         "status": row.status})
    return row


def import_from_stripe(db: Session, agreement: CommercialAgreement, actor: User, *,
                       period_start: date, period_end: date,
                       attribution_state: str = ATTR_UNKNOWN_REVIEW,
                       note: Optional[str] = None) -> CommercialCollectionRecord:
    """Build a record from payments T2 ALREADY HOLDS.

    This reads `billing_payments` — the rows the Stripe webhook writes — and
    does not create, mirror or reconcile anything. Refunds recorded against
    those payments are carried across as a stated adjustment, so a period whose
    refunds are known arrives with them known.

    The result is SUBMITTED, never approved. A machine summing a table is not
    the same as a person accepting the figure.
    """
    from app.models.billing_models import BillingPayment

    if not agreement.organization_id:
        raise HTTPException(
            status_code=409,
            detail="This agreement is not yet attached to a customer, so there "
                   "are no payments to read.")

    start_dt = datetime.combine(period_start, datetime.min.time())
    end_dt = datetime.combine(period_end, datetime.max.time())

    rows = (db.query(BillingPayment)
            .filter(BillingPayment.organization_id == agreement.organization_id,
                    BillingPayment.collected_at >= start_dt,
                    BillingPayment.collected_at <= end_dt)
            .all())

    gross = sum(int(r.amount_cents or 0) for r in rows)
    refunded = sum(int(r.refunded_cents or 0) for r in rows)

    return record_collection(
        db, agreement, actor,
        period_start=period_start, period_end=period_end,
        source=SOURCE_STRIPE,
        gross_cents=gross,
        adjustments_cents=refunded,
        adjustments_note=("Refunds recorded against the payments in this period."
                          if refunded else "No refunds recorded in this period."),
        attribution_state=attribution_state,
        source_reference="billing_payments:%d row(s)" % len(rows),
        note=note, submit=True)


def approve_collection(db: Session, agreement: CommercialAgreement, actor: User,
                       record_id: str,
                       attribution_state: Optional[str] = None,
                       note: Optional[str] = None) -> CommercialCollectionRecord:
    row = (db.query(CommercialCollectionRecord)
           .filter(CommercialCollectionRecord.id == record_id,
                   CommercialCollectionRecord.agreement_id == agreement.id)
           .first())
    if row is None:
        raise HTTPException(status_code=404,
                            detail="That collection record is not on this agreement.")
    if row.status == COLLECTION_APPROVED:
        return row

    if attribution_state is not None:
        if attribution_state not in ATTRIBUTION_RECORD_STATES:
            raise HTTPException(status_code=400,
                                detail="Unknown attribution state '%s'."
                                       % attribution_state)
        row.attribution_state = attribution_state

    before = {"status": row.status, "attribution_state": row.attribution_state}
    row.status = COLLECTION_APPROVED
    row.approved_by_user_id = getattr(actor, "id", None)
    row.approved_at = datetime.utcnow()
    if note:
        row.note = note

    caudit.record(db, agreement, actor, caudit.A_COLLECTION_APPROVED,
                  target_type="commercial_collection_record", target_id=row.id,
                  before=before,
                  after={"status": row.status,
                         "attribution_state": row.attribution_state,
                         "gross_cents": row.gross_cents})
    db.flush()
    return row


def reject_collection(db: Session, agreement: CommercialAgreement, actor: User,
                      record_id: str, reason: str) -> CommercialCollectionRecord:
    if not (reason or "").strip():
        raise HTTPException(status_code=400,
                            detail="Rejecting a collection record requires a reason.")
    row = (db.query(CommercialCollectionRecord)
           .filter(CommercialCollectionRecord.id == record_id,
                   CommercialCollectionRecord.agreement_id == agreement.id)
           .first())
    if row is None:
        raise HTTPException(status_code=404,
                            detail="That collection record is not on this agreement.")
    before = {"status": row.status}
    row.status = COLLECTION_REJECTED
    row.rejected_reason = reason.strip()
    caudit.record(db, agreement, actor, caudit.A_COLLECTION_REJECTED,
                  target_type="commercial_collection_record", target_id=row.id,
                  before=before, after={"status": row.status}, note=reason.strip())
    db.flush()
    return row


# ════════════════════════════════════════════════════════════════════════════
# READINESS AND PREVIEW
# ════════════════════════════════════════════════════════════════════════════


def coverage(db: Session, agreement: CommercialAgreement,
             period_start: date, period_end: date) -> Dict[str, Any]:
    """What authoritative data exists for this period, and what is wrong with it.

    An empty `approved` list is the case this whole module exists to handle
    correctly: it produces a REFUSAL with a reason, never a basis of zero.
    """
    approved = records(db, agreement, period_start=period_start,
                       period_end=period_end, statuses=(COLLECTION_APPROVED,))
    pending = records(db, agreement, period_start=period_start,
                      period_end=period_end,
                      statuses=(COLLECTION_DRAFT, COLLECTION_SUBMITTED))

    reasons: List[str] = []
    if not approved:
        if pending:
            reasons.append(
                "%d collection record(s) for this period have been recorded but "
                "not approved. A settlement is calculated only from approved "
                "records." % len(pending))
        else:
            reasons.append(
                "No authoritative collection figures have been recorded for "
                "this period. The period cannot be settled, and it is not "
                "treated as zero.")

    basis_term = t.value(db, agreement, "share_basis")
    if basis_term in _BASES_NEEDING_ADJUSTMENTS:
        unstated = [r for r in approved if r.adjustments_cents is None]
        if unstated:
            reasons.append(
                "This agreement settles on collections after adjustments, and "
                "%d approved record(s) for this period do not state their "
                "adjustments." % len(unstated))

    unresolved = [r for r in approved
                  if r.attribution_state == ATTR_UNKNOWN_REVIEW]
    if unresolved:
        reasons.append(
            "%d approved record(s) for this period have no resolved "
            "attribution. Which collections are inside the share has to be "
            "established before a share can be calculated." % len(unresolved))

    currencies = {r.currency for r in approved}
    if len(currencies) > 1:
        reasons.append("The approved records for this period are in more than "
                       "one currency (%s)." % ", ".join(sorted(currencies)))

    gross = sum(int(r.gross_cents or 0) for r in approved) if approved else None
    adjustments = (sum(int(r.adjustments_cents or 0) for r in approved)
                   if approved and all(r.adjustments_cents is not None
                                       for r in approved)
                   else None)

    return {
        "period_start": period_start,
        "period_end": period_end,
        "approved_count": len(approved),
        "pending_count": len(pending),
        "approved_records": approved,
        "gross_cents": gross,
        "adjustments_cents": adjustments,
        "adjustments_known": adjustments is not None,
        "currencies": sorted(currencies),
        "reasons": reasons,
        "usable": not reasons,
    }


def readiness(db: Session, agreement: CommercialAgreement,
              period_start: Optional[date] = None,
              period_end: Optional[date] = None) -> Dict[str, Any]:
    """Can this agreement be settled at all, and for this period specifically?

    Two layers, reported separately, because they are fixed by different people:
    the term-level layer is a commercial question for the deal owner, the
    period layer is an operational one for whoever records collections.
    """
    block = t.blocking(db, agreement)
    term_reasons = block[t.ACTION_CALCULATE]["reasons"]

    out = {
        "agreement_ready": not term_reasons,
        "agreement_reasons": term_reasons,
        "payout_execution_supported": PAYOUT_EXECUTION_SUPPORTED,
        "period": None,
    }
    if period_start and period_end:
        cov = coverage(db, agreement, period_start, period_end)
        out["period"] = {
            "period_start": period_start, "period_end": period_end,
            "usable": cov["usable"], "reasons": cov["reasons"],
            "approved_count": cov["approved_count"],
            "pending_count": cov["pending_count"],
            "gross_cents": cov["gross_cents"],
            "adjustments_cents": cov["adjustments_cents"],
            "adjustments_known": cov["adjustments_known"],
        }
        out["can_calculate"] = bool(not term_reasons and cov["usable"])
    else:
        out["can_calculate"] = False
    return out


def _basis_from(agreement: CommercialAgreement, cov: Dict[str, Any],
                basis_term: Any) -> Optional[int]:
    """The figure the split applies to, or None if it cannot be established."""
    if cov["gross_cents"] is None:
        return None
    if basis_term in _BASES_NEEDING_ADJUSTMENTS:
        if not cov["adjustments_known"]:
            return None
        return int(cov["gross_cents"]) - int(cov["adjustments_cents"] or 0)
    # gross_collections, or a basis the agreement describes in words: the
    # figure used is the gross, and the statement says so explicitly.
    return int(cov["gross_cents"])


def preview(db: Session, agreement: CommercialAgreement, actor: User, *,
            period_start: date, period_end: date,
            persist: bool = True) -> Dict[str, Any]:
    """Calculate what each party would be owed for a period — or refuse.

    A PREVIEW IS NOT A PAYOUT and the status vocabulary says so: the best
    outcome available here is CALCULATED. Approval is a separate act by a
    separate authority, and distribution is not available at all.
    """
    if period_end < period_start:
        raise HTTPException(status_code=400,
                            detail="The period ends before it starts.")

    block = t.blocking(db, agreement)
    reasons = list(block[t.ACTION_CALCULATE]["reasons"])
    cov = coverage(db, agreement, period_start, period_end)
    reasons.extend(cov["reasons"])

    basis_term = t.value(db, agreement, "share_basis")
    rule = t.value(db, agreement, "allocation_rule")
    verdict = rs.validate(db, agreement, rule)
    basis = None if reasons else _basis_from(agreement, cov, basis_term)

    if basis is None and not reasons:
        reasons.append("The basis for this period could not be established "
                       "from the approved records.")

    row = (db.query(CommercialSettlement)
           .filter(CommercialSettlement.agreement_id == agreement.id,
                   CommercialSettlement.period_start == period_start,
                   CommercialSettlement.period_end == period_end)
           .first())
    if row is None and persist:
        row = CommercialSettlement(
            agreement_id=agreement.id,
            organization_id=agreement.organization_id,
            period_start=period_start, period_end=period_end,
            currency=agreement.currency)
        db.add(row)
        db.flush()

    if row is not None and row.status in (SETTLE_APPROVED,):
        raise HTTPException(
            status_code=409,
            detail="This period has already been approved. Recalculating an "
                   "approved settlement would change a figure somebody signed "
                   "for.")

    if reasons:
        result = {
            "status": SETTLE_REVIEW_REQUIRED,
            "period_start": period_start, "period_end": period_end,
            "currency": agreement.currency,
            "basis_cents": None, "gross_cents": cov["gross_cents"],
            "adjustments_cents": cov["adjustments_cents"],
            "lines": [],
            "blocked_reasons": reasons,
            "data_source": _data_source(cov),
        }
        if row is not None and persist:
            row.status = SETTLE_REVIEW_REQUIRED
            row.basis_cents = None
            row.gross_cents = cov["gross_cents"]
            row.adjustments_cents = cov["adjustments_cents"]
            row.calculation_json = None
            row.data_source_json = result["data_source"]
            row.blocked_reasons_json = reasons
            row.calculated_by_user_id = getattr(actor, "id", None)
            row.calculated_at = datetime.utcnow()
            caudit.record(db, agreement, actor, caudit.A_SETTLEMENT_BLOCKED,
                          target_type="commercial_settlement", target_id=row.id,
                          details={"period_start": str(period_start),
                                   "period_end": str(period_end),
                                   "reasons": reasons})
            db.flush()
            result["id"] = row.id
        return result

    rounding = t.value(db, agreement, "rounding_policy") or rs.ROUND_LARGEST_REMAINDER
    calc = rs.split(int(basis), verdict, rounding)

    result = {
        "status": SETTLE_CALCULATED,
        "period_start": period_start, "period_end": period_end,
        "currency": agreement.currency,
        "basis_cents": int(basis),
        "gross_cents": cov["gross_cents"],
        "adjustments_cents": cov["adjustments_cents"],
        "share_basis": basis_term,
        "rounding_policy": calc["rounding_policy"],
        "lines": calc["lines"],
        "allocated_cents": calc["allocated_cents"],
        "unallocated_cents": calc["unallocated_cents"],
        "fixed_exceeds_basis": calc["fixed_exceeds_basis"],
        "blocked_reasons": [],
        "data_source": _data_source(cov),
        "is_payout": False,
        "payout_execution_supported": PAYOUT_EXECUTION_SUPPORTED,
    }

    if row is not None and persist:
        row.status = SETTLE_CALCULATED
        row.basis_cents = int(basis)
        row.gross_cents = cov["gross_cents"]
        row.adjustments_cents = cov["adjustments_cents"]
        row.calculation_json = {"lines": calc["lines"],
                                "rounding_policy": calc["rounding_policy"],
                                "share_basis": basis_term}
        row.data_source_json = result["data_source"]
        row.blocked_reasons_json = None
        row.calculated_by_user_id = getattr(actor, "id", None)
        row.calculated_at = datetime.utcnow()
        caudit.record(db, agreement, actor, caudit.A_SETTLEMENT_CALCULATED,
                      target_type="commercial_settlement", target_id=row.id,
                      after={"basis_cents": int(basis),
                             "lines": calc["lines"]},
                      details={"period_start": str(period_start),
                               "period_end": str(period_end),
                               "is_payout": False})
        db.flush()
        result["id"] = row.id

    return result


def _data_source(cov: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "approved_record_count": cov["approved_count"],
        "records": [
            {"id": r.id, "source": r.source, "source_reference": r.source_reference,
             "period_start": str(r.period_start), "period_end": str(r.period_end),
             "gross_cents": r.gross_cents, "adjustments_cents": r.adjustments_cents,
             "attribution_state": r.attribution_state,
             "approved_by_user_id": r.approved_by_user_id,
             "approved_at": (r.approved_at.isoformat() if r.approved_at else None)}
            for r in cov["approved_records"]
        ],
    }


def approve_settlement(db: Session, agreement: CommercialAgreement, actor: User,
                       settlement_id: str,
                       note: Optional[str] = None) -> CommercialSettlement:
    """Accept a calculated statement. Still not a payment."""
    row = (db.query(CommercialSettlement)
           .filter(CommercialSettlement.id == settlement_id,
                   CommercialSettlement.agreement_id == agreement.id)
           .first())
    if row is None:
        raise HTTPException(status_code=404,
                            detail="That settlement is not on this agreement.")
    if row.status != SETTLE_CALCULATED:
        raise HTTPException(
            status_code=409,
            detail="Only a calculated settlement can be approved. This one is "
                   "'%s'." % row.status)

    before = {"status": row.status}
    row.status = SETTLE_APPROVED
    row.approved_by_user_id = getattr(actor, "id", None)
    row.approved_at = datetime.utcnow()
    if note:
        row.note = note

    caudit.record(db, agreement, actor, caudit.A_SETTLEMENT_APPROVED,
                  target_type="commercial_settlement", target_id=row.id,
                  before=before,
                  after={"status": row.status, "basis_cents": row.basis_cents},
                  details={"moves_money": False}, note=note)
    db.flush()
    return row


def distribute(db: Session, agreement: CommercialAgreement, actor: User,
               settlement_id: str) -> None:
    """REFUSES. Always. And leaves a record that somebody asked.

    This is not a stub waiting to be filled in by whoever needs it next. A
    payout against a custom commercial agreement needs a rail, a reconciliation
    and a review that do not exist, and the safe behaviour while they do not
    exist is a refusal that is impossible to miss.
    """
    caudit.record(db, agreement, actor,
                  caudit.A_SETTLEMENT_DISTRIBUTION_REFUSED,
                  target_type="commercial_settlement", target_id=settlement_id,
                  details={"payout_execution_supported": PAYOUT_EXECUTION_SUPPORTED})
    db.flush()
    raise HTTPException(
        status_code=409,
        detail="This platform does not distribute funds on a custom commercial "
               "agreement. The settlement statement is the record of what is "
               "owed; payment is made outside the platform and recorded here.")
