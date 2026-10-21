"""Sales Do Not Contact — the ONE check every sales contact path asks.

A prospect is suppressed when an in-force row in `sales_contact_suppressions`
for the same brand sales org matches their phone OR their email. Matching is by
contact detail, so every duplicate record of the same person is covered.

Callers:
  · sales_router — refuse follow-up scheduling and call/text logging; strip the
    follow-up from a new duplicate; flag cards
  · sales_scheduling_router — refuse booking a meeting
  · appointment_invites / sales_appointment_reminders / demo_confirmation /
    proposal_service — send nothing (recorded as skipped, never raises)

Nothing here sends anything, and nothing lifts a suppression automatically.
"""
from datetime import datetime
from typing import Iterable, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.sales_suppression_models import SalesContactSuppression

REFUSAL = ("This prospect asked not to be contacted. Their Do Not Contact request "
           "is on file; a sales manager can review it on the lead.")


def norm_phone(phone) -> Optional[str]:
    """Same stored form as the customer-side list (11-digit US)."""
    from app.services.compliance_service import usable_us_phone
    return usable_us_phone(phone)


def norm_email(email) -> Optional[str]:
    e = (email or "").strip().lower()
    return e if "@" in e else None


def _active(db: Session, brand_id: str):
    return db.query(SalesContactSuppression).filter(
        SalesContactSuppression.brand_sales_org_id == brand_id,
        SalesContactSuppression.lifted_at.is_(None))


def find(db: Session, brand_id: Optional[str], phone=None, email=None) -> Optional[SalesContactSuppression]:
    """The in-force row matching this phone or email in this brand, or None."""
    if not brand_id:
        return None
    p, e = norm_phone(phone), norm_email(email)
    conds = []
    if p:
        conds.append(SalesContactSuppression.phone == p)
    if e:
        conds.append(SalesContactSuppression.email == e)
    if not conds:
        return None
    return (_active(db, brand_id).filter(or_(*conds))
            .order_by(SalesContactSuppression.recorded_at.asc()).first())


def for_opportunity(db: Session, opp) -> Optional[SalesContactSuppression]:
    if opp is None:
        return None
    return find(db, opp.brand_sales_org_id, opp.phone, opp.email)


def for_appointment(db: Session, appt) -> Optional[SalesContactSuppression]:
    """A meeting's prospect, by the meeting's own contact details AND its deal's."""
    if appt is None:
        return None
    hit = find(db, appt.brand_sales_org_id, getattr(appt, "prospect_phone", None),
               getattr(appt, "prospect_email", None))
    if hit or not getattr(appt, "opportunity_id", None):
        return hit
    from app.models.sales_models import Opportunity
    opp = db.query(Opportunity).filter(Opportunity.id == appt.opportunity_id).first()
    return for_opportunity(db, opp)


def index(db: Session, brand_ids: Iterable[str]) -> Tuple[set, set]:
    """In-force (brand, phone) and (brand, email) pairs — one query for a list."""
    ids = [b for b in set(brand_ids) if b]
    if not ids:
        return set(), set()
    rows = (db.query(SalesContactSuppression.brand_sales_org_id,
                     SalesContactSuppression.phone, SalesContactSuppression.email)
            .filter(SalesContactSuppression.brand_sales_org_id.in_(ids),
                    SalesContactSuppression.lifted_at.is_(None)).all())
    return ({(b, p) for b, p, _ in rows if p}, {(b, e) for b, _, e in rows if e})


def in_index(idx: Tuple[set, set], opp) -> bool:
    phones, emails = idx
    p, e = norm_phone(opp.phone), norm_email(opp.email)
    return bool((p and (opp.brand_sales_org_id, p) in phones) or
                (e and (opp.brand_sales_org_id, e) in emails))


def record(db: Session, opp, user, reason: str) -> SalesContactSuppression:
    """Record the prospect's request against this deal's phone and email.

    Raises ValueError when the deal has no usable phone or email — there is
    nothing to suppress, and a row matching nothing would be a false comfort.
    Re-recording for a contact already suppressed returns the existing row.
    """
    p, e = norm_phone(opp.phone), norm_email(opp.email)
    if not p and not e:
        raise ValueError("This lead has no usable phone or email to suppress.")
    existing = find(db, opp.brand_sales_org_id, opp.phone, opp.email)
    if existing and (not p or existing.phone == p) and (not e or existing.email == e):
        return existing
    row = SalesContactSuppression(
        brand_sales_org_id=opp.brand_sales_org_id, phone=p, email=e,
        reason=(reason or "").strip() or "Prospect asked not to be contacted.",
        source_opportunity_id=opp.id, recorded_by_user_id=user.id,
        recorded_at=datetime.utcnow())
    db.add(row)
    db.flush()
    return row


def lift(db: Session, row: SalesContactSuppression, user, reason: str) -> SalesContactSuppression:
    if row.lifted_at is not None:
        return row
    r = (reason or "").strip()
    if not r:
        raise ValueError("Give the reason for lifting this Do Not Contact request.")
    row.lifted_at = datetime.utcnow()
    row.lifted_by_user_id = user.id
    row.lift_reason = r
    db.flush()
    return row


def matching_opportunities(db: Session, row: SalesContactSuppression):
    """Every deal in the brand this row covers (the duplicates)."""
    from app.models.sales_models import Opportunity
    q = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == row.brand_sales_org_id)
    out = []
    for o in q.all():
        if (row.phone and norm_phone(o.phone) == row.phone) or (row.email and norm_email(o.email) == row.email):
            out.append(o)
    return out


def history(db: Session, opp) -> list:
    """Every row (in force or lifted) touching this deal's phone or email."""
    p, e = norm_phone(opp.phone), norm_email(opp.email)
    conds = []
    if p:
        conds.append(SalesContactSuppression.phone == p)
    if e:
        conds.append(SalesContactSuppression.email == e)
    if not conds:
        return []
    return (db.query(SalesContactSuppression)
            .filter(SalesContactSuppression.brand_sales_org_id == opp.brand_sales_org_id, or_(*conds))
            .order_by(SalesContactSuppression.recorded_at.desc()).all())
