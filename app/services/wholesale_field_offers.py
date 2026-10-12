"""FIELD OFFERS - build list #14 (the field calculator with your numbers
locked, and the offer limit enforced) and #20 (offers over the limit come to
you to approve or decline).

What a field login sees: the most they may offer, nothing else. The
investor percentage, the fee and the formula's own MAO stay on the server.

    max they may offer = round down to $500 of ( MAO x field limit % )
    MAO = the workspace's own formula (wholesale_analysis.calculate_offer)

Every offer is recorded. Inside the limit it stands ("within"). Above it, it
waits ("pending") and the workspace admins get a bell notification; the admin's
answer goes back to the rep the same way. Nothing here texts or emails anyone.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.wholesale_ops_models import WholesaleFieldOffer, WholesaleFieldRules
from app.services import wholesale_appointments as A
from app.services import wholesale_team as T

STEP = 500


def _int(v, what: str, *, required: bool = True) -> Optional[int]:
    if v in (None, ""):
        if required:
            raise HTTPException(422, "Enter the %s." % what)
        return None
    try:
        n = float(str(v).replace("$", "").replace(",", "").strip())
    except ValueError:
        raise HTTPException(422, "The %s must be a number." % what)
    if n < 0 or n > 50_000_000:
        raise HTTPException(422, "That %s does not look right." % what)
    return int(round(n))


def rules(db: Session, org_id: str) -> WholesaleFieldRules:
    row = db.query(WholesaleFieldRules).filter(WholesaleFieldRules.organization_id == org_id).first()
    if row is None:
        row = WholesaleFieldRules(organization_id=org_id, offer_limit_pct=100)
        db.add(row)
        db.flush()
    return row


def rules_json(row: WholesaleFieldRules) -> Dict[str, Any]:
    return {"offer_limit_pct": row.offer_limit_pct,
            "explained": "A rep can offer up to %d%% of the max offer from your formula without asking you."
                         % row.offer_limit_pct}


def set_rules(db: Session, org_id: str, user: User, pct) -> Dict[str, Any]:
    p = _int(pct, "limit")
    if p < 50 or p > 110:
        raise HTTPException(422, "Pick between 50% and 110% of the max offer.")
    row = rules(db, org_id)
    row.offer_limit_pct = p
    row.updated_by_id = user.id
    db.commit()
    return rules_json(row)


def _mao(db: Session, org_id: str, arv: int, repairs: int) -> Optional[int]:
    from app.services import wholesale_analysis as WA
    from app.services import wholesale_service as svc
    st = svc.resolve_settings(db, org_id, commit=False)
    calc = WA.calculate_offer(arv, repairs, st.investor_percentage, st.default_wholesale_fee,
                              getattr(st, "transaction_cost_percent", 0), getattr(st, "transaction_cost_flat", 0))
    return int(calc["mao"]) if calc.get("mao") is not None else None


def _limit(db: Session, org_id: str, mao: Optional[int]) -> Optional[int]:
    if mao is None:
        return None
    raw = mao * rules(db, org_id).offer_limit_pct / 100.0
    return int(raw // STEP * STEP) if raw > 0 else 0


def is_decider(db: Session, user: User) -> bool:
    return A._is_admin_here(db, user)


def quote(db: Session, org_id: str, user: User, appt_id: str, arv, repairs) -> Dict[str, Any]:
    A.get_visible(db, org_id, user, appt_id)
    arv_i, rep_i = _int(arv, "ARV"), _int(repairs, "repairs", required=False) or 0
    mao = _mao(db, org_id, arv_i, rep_i)
    out = {"arv": arv_i, "repairs": rep_i, "max_offer": _limit(db, org_id, mao)}
    if not T.is_field(db, user):
        out["mao"] = mao
        out["offer_limit_pct"] = rules(db, org_id).offer_limit_pct
    db.commit()
    return out


def offer_json(db: Session, o: WholesaleFieldOffer, *, field: bool) -> Dict[str, Any]:
    out = {"id": o.id, "appointment_id": o.appointment_id, "arv": o.arv, "repairs": o.repairs,
           "max_offer": o.offer_limit, "offer": o.offer, "note": o.note, "status": o.status,
           "requested_by_name": A._name(db, o.requested_by_id), "created_at": A._iso(o.created_at),
           "decided_by_name": A._name(db, o.decided_by_id), "decided_at": A._iso(o.decided_at),
           "decision_note": o.decision_note}
    if not field:
        out["mao"] = o.mao
    return out


def submit(db: Session, org_id: str, user: User, appt_id: str, arv, repairs, offer, note=None) -> Dict[str, Any]:
    appt = A.get_visible(db, org_id, user, appt_id)
    field = T.is_field(db, user)
    if field and appt.assigned_to_id != user.id:
        raise HTTPException(404, "Appointment not found")
    arv_i, rep_i, off_i = _int(arv, "ARV"), _int(repairs, "repairs", required=False) or 0, _int(offer, "offer")
    mao = _mao(db, org_id, arv_i, rep_i)
    lim = _limit(db, org_id, mao)
    # An admin's own offer stands; a rep's offer above the limit waits for one.
    over = lim is None or off_i > lim
    status = "pending" if (over and not is_decider(db, user)) else "within"
    o = WholesaleFieldOffer(organization_id=org_id, appointment_id=appt.id, requested_by_id=user.id,
                            arv=arv_i, repairs=rep_i, mao=mao, offer_limit=lim, offer=off_i,
                            note=(note or "").strip()[:2000] or None, status=status,
                            created_at=datetime.utcnow())
    db.add(o)
    db.flush()
    if status == "pending":
        from app.services import wholesale_notify as N
        from app.models.models import NotificationType
        N.notify(db, org_id, kind=NotificationType.WHOLESALE_OFFER,
                 message="%s wants to offer $%s at %s (their limit is %s). Approve or decline."
                         % (A._name(db, user.id) or "A rep", "{:,}".format(off_i), appt.address or "a property",
                            "$" + "{:,}".format(lim) if lim is not None else "not set - no ARV formula"),
                 link="/wholesale/appointments?approvals=1")
    db.commit()
    return offer_json(db, o, field=field)


def for_appointment(db: Session, org_id: str, user: User, appt_id: str) -> List[Dict[str, Any]]:
    appt = A.get_visible(db, org_id, user, appt_id)
    field = T.is_field(db, user)
    rows = (db.query(WholesaleFieldOffer).filter(WholesaleFieldOffer.organization_id == org_id,
                                                 WholesaleFieldOffer.appointment_id == appt.id)
            .order_by(WholesaleFieldOffer.created_at.desc()).all())
    return [offer_json(db, o, field=field) for o in rows]


def pending(db: Session, org_id: str, user: User) -> List[Dict[str, Any]]:
    if not is_decider(db, user):
        raise HTTPException(403, "Only an admin approves offers.")
    from app.models.wholesale_ops_models import WholesaleFieldAppointment as Appt
    rows = (db.query(WholesaleFieldOffer, Appt).join(Appt, Appt.id == WholesaleFieldOffer.appointment_id)
            .filter(WholesaleFieldOffer.organization_id == org_id, WholesaleFieldOffer.status == "pending")
            .order_by(WholesaleFieldOffer.created_at.asc()).all())
    out = []
    for o, a in rows:
        item = offer_json(db, o, field=False)
        item.update({"address": a.address, "local_time": A.appt_json(db, a)["local_time"],
                     "evosense_property_id": a.evosense_property_id})
        out.append(item)
    return out


def decide(db: Session, org_id: str, user: User, offer_id: str, approve: bool, note=None) -> Dict[str, Any]:
    if not is_decider(db, user):
        raise HTTPException(403, "Only an admin approves offers.")
    o = db.query(WholesaleFieldOffer).filter(WholesaleFieldOffer.id == offer_id,
                                             WholesaleFieldOffer.organization_id == org_id).first()
    if o is None:
        raise HTTPException(404, "Offer not found")
    if o.status != "pending":
        raise HTTPException(409, "That offer was already %s." % o.status)
    o.status = "approved" if approve else "declined"
    o.decided_by_id, o.decided_at = user.id, datetime.utcnow()
    o.decision_note = (note or "").strip()[:2000] or None
    if o.requested_by_id:
        from app.services import wholesale_notify as N
        from app.models.models import NotificationType
        N.notify(db, org_id, kind=NotificationType.WHOLESALE_OFFER, assignee_id=o.requested_by_id,
                 message="Your $%s offer was %s%s" % ("{:,}".format(o.offer), o.status,
                                                     (": " + o.decision_note) if o.decision_note else "."),
                 link="/wholesale/appointments")
    db.commit()
    return offer_json(db, o, field=False)
