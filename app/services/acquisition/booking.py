"""P4 - walkthrough booking for the managed acquisition pilot.

NOT A SECOND SCHEDULER. It uses the platform's own pieces:
  * BookingLink rows (the same table every EvoSys booking lives in),
  * calendar_router.compute_day_slots - THE slot engine: office hours, the
    booker's existing bookings, and their connected calendar, failing closed,
  * the calendar provider registry (Microsoft / Google) to write the event.
What it leaves out on purpose is the funeral-home appointment flow around them
(confirmation texts, advisor SMS, case files, review-request texts): a prospect
business found by research never gets a text.

REAL AVAILABILITY ONLY. The booker must have a Microsoft or Google calendar
connected; with none, no link can be made and no time is offered. Every time
offered is re-checked against the slot engine when it is picked.

WHO IS THE BOOKER:
  * real prospects - the client's booking user (profile.booking_user_id), a
    user of the client's own organization;
  * practice prospects - the test calendar the owner picked
    (profile.test_booking_user_id).
"""
from __future__ import annotations

import secrets
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.acquisition_models import AcquisitionOpportunity, AcquisitionProfile
from app.models.models import BookingLink, Lead, Organization, User
from app.services.acquisition import common as C

LINK_DAYS = 14
LABEL = "Cleaning walkthrough"
BOOKABLE = (C.INTERESTED,)


def _label(org) -> str:
    """The appointment name for THIS client's trade (multi-industry, #452)."""
    from app.services.acquisition import messaging as M
    return M.visit_label(org)


def _profile(db: Session, org_id: str) -> Optional[AcquisitionProfile]:
    return db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == org_id).first()


def calendar_connected(db: Session, user: Optional[User]) -> bool:
    if user is None:
        return False
    from app.services import calendar_providers as reg
    try:
        return reg.is_external_calendar(reg.resolve_provider_key(db, user))
    except Exception:  # noqa: BLE001
        return False


def booker_for(db: Session, org: Organization, opp: AcquisitionOpportunity, actor: User) -> User:
    if opp.is_practice:
        # The owner chooses which test calendar practice walkthroughs land on;
        # never whoever happens to be signed in.
        p = _profile(db, org.id)
        uid = getattr(p, "test_booking_user_id", None) if p else None
        user = db.query(User).filter(User.id == uid, User.is_active.isnot(False)).first() if uid else None
        if user is None:
            raise HTTPException(409, "No test calendar is set for practice walkthroughs yet")
    else:
        p = _profile(db, org.id)
        uid = getattr(p, "booking_user_id", None) if p else None
        user = (db.query(User).filter(User.id == uid, User.organization_id == org.id,
                                      User.is_active.isnot(False)).first() if uid else None)
        if user is None:
            raise HTTPException(409, "No booking calendar is set for the client yet")
    if not calendar_connected(db, user):
        raise HTTPException(409, "%s has no Microsoft or Google calendar connected, so no real "
                                 "times can be offered" % (user.full_name or user.email))
    return user


def public_url(db: Session, org_id: str, token: str) -> str:
    base = None
    try:
        from app.services import public_identity as PI
        base = PI._public_base_or_none(db, org_id, PI.ENV_PUBLIC_BASE_URL)
    except Exception:  # noqa: BLE001
        base = None
    return "%s/walkthrough/%s" % ((base or "https://app.evosyspro.live").rstrip("/"), token)


def make_link(db: Session, org: Organization, opp: AcquisitionOpportunity, actor: User) -> Dict[str, Any]:
    """One booking link for an interested prospect (reused while still open)."""
    from app.services.acquisition import engine as E
    if opp.stage not in BOOKABLE:
        raise HTTPException(409, "Only an interested prospect can be offered a walkthrough")
    lead = db.query(Lead).filter(Lead.id == opp.lead_id, Lead.organization_id == org.id).first()
    if lead is None:
        raise HTTPException(404, "Prospect not found")
    if lead.status == "dnc":
        raise HTTPException(409, "This prospect asked not to be contacted")
    booker = booker_for(db, org, opp, actor)
    p = _profile(db, org.id)
    minutes = (p.appointment_minutes if p else None) or C.DEFAULT_APPOINTMENT_MINUTES
    now = datetime.utcnow()
    link = (db.query(BookingLink).filter(BookingLink.lead_id == lead.id, BookingLink.user_id == booker.id,
                                         BookingLink.status == "pending", BookingLink.expires_at > now)
            .order_by(BookingLink.created_at.desc()).first())
    if link is None:
        link = BookingLink(lead_id=lead.id, user_id=booker.id, status="pending",
                           token="aq" + secrets.token_urlsafe(18), appt_label=_label(org), appt_duration=minutes,
                           expires_at=now + timedelta(days=LINK_DAYS),
                           # The funeral-home appointment flow never texts a prospect business.
                           confirmation_sent=True, reminder_24hr_sent=True, reminder_1hr_sent=True)
        db.add(link)
        db.flush()
        E._event(db, org.id, opp.id, "booking_link", actor.id,
                 note="Walkthrough link made on %s's calendar" % (booker.full_name or booker.email))
        db.commit()
    return {"token": link.token, "url": public_url(db, org.id, link.token), "booker": booker.full_name or booker.email,
            "minutes": link.appt_duration, "expires_at": C.iso(link.expires_at)}


# ── the public page ─────────────────────────────────────────────────────────
def _load(db: Session, token: str):
    link = db.query(BookingLink).filter(BookingLink.token == token).first()
    if link is None or not str(token).startswith("aq"):
        raise HTTPException(404, "This link isn't valid")
    opp = db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.lead_id == link.lead_id).first()
    if opp is None:
        raise HTTPException(404, "This link isn't valid")
    org = db.query(Organization).filter(Organization.id == opp.organization_id).first()
    booker = db.query(User).filter(User.id == link.user_id).first()
    lead = db.query(Lead).filter(Lead.id == link.lead_id).first()
    return link, opp, org, booker, lead


def _client_name(db: Session, org: Organization) -> str:
    p = _profile(db, org.id)
    return ((p.business_name if p else None) or org.name or "").strip()


def page(db: Session, token: str) -> Dict[str, Any]:
    from app.services.acquisition import acq_time as T
    link, opp, org, booker, lead = _load(db, token)
    tz = T.company_tz(_profile(db, org.id))
    f = C.jload(lead.custom_fields, {}) if lead else {}
    expired = link.expires_at is not None and link.expires_at < datetime.utcnow()
    return {"company": _client_name(db, org), "for": (f or {}).get("company"), "label": link.appt_label or LABEL,
            "minutes": link.appt_duration, "status": "expired" if (expired and link.status == "pending") else link.status,
            "booked_time": (T.from_engine(link.booked_time, tz).strftime("%Y-%m-%dT%H:%M:00")
                            if link.booked_time else None),
            "days": LINK_DAYS, "timezone": tz}


def slots(db: Session, token: str, day: str) -> Dict[str, Any]:
    link, opp, org, booker, lead = _load(db, token)
    if link.status != "pending":
        return {"date": day, "slots": [], "reason": "Already booked"}
    try:
        target = date_cls.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Use a YYYY-MM-DD date")
    if not calendar_connected(db, booker):
        return {"date": day, "slots": [], "reason": "The calendar can't be read right now. Please try again later."}
    # Hour-long holds on the hour, the whole hour free, in the company's zone.
    from app.services.acquisition import acq_time as T
    res = T.slots_for(db, booker, _profile(db, org.id), target, minutes=60)
    res.pop("calendar_error", None)
    return res


def book(db: Session, token: str, slot: str) -> Dict[str, Any]:
    from app.services.acquisition import engine as E
    link, opp, org, booker, lead = _load(db, token)
    if link.status in ("booked", "confirmed"):
        raise HTTPException(409, "This walkthrough is already booked")
    if opp.stage != C.INTERESTED:
        raise HTTPException(409, "This link is no longer active")
    if link.status != "pending" or (link.expires_at and link.expires_at < datetime.utcnow()):
        raise HTTPException(409, "This link has expired")
    if lead is None or lead.status == "dnc":
        raise HTTPException(409, "This link is no longer active")
    try:
        start = datetime.fromisoformat(str(slot)[:19])
    except ValueError:
        raise HTTPException(400, "Pick a time from the list")
    free = slots(db, token, start.date().isoformat())
    if start.strftime("%Y-%m-%dT%H:%M:00") not in free.get("slots", []):
        raise HTTPException(409, "That time was just taken. Please pick another.")
    minutes = int(link.appt_duration or C.DEFAULT_APPOINTMENT_MINUTES)
    from app.services import tenant_scheduling as TS
    f = C.jload(lead.custom_fields, {}) or {}
    company = f.get("company") or "Prospect"
    # Phone only on the client's calendar - the prospect's email stays with EvoSys.
    # A display-only stand-in for the calendar entry, never saved: a plain
    # object, not a Lead row, so it can never be added or counted as a lead.
    from types import SimpleNamespace
    lead_view = SimpleNamespace(first_name=company, last_name="", phone=lead.phone, email=None)
    from app.services.acquisition import acq_time as T
    tz = T.company_tz(_profile(db, org.id))
    sync = TS._push_to_calendar(db, booker, org, link, lead_view, link.appt_label or _label(org), start, minutes, tz,
                                "Booked from the acquisition pilot (%s)." % ("practice" if opp.is_practice else "real"))
    if not sync.get("ok"):
        db.rollback()
        raise HTTPException(502, "The calendar couldn't be updated, so nothing was booked. Please try again.")
    link.status = "booked"
    link.booked_time = T.to_engine(start, tz)          # stored in the shared engine's wall time
    link.review_request_sent_at = datetime.utcnow()   # no review-request text to a prospect business
    opp.booking_link_id = link.id
    E._move(db, opp, C.BOOKED, None, "Walkthrough booked for %s" % T.label(start, tz))
    db.commit()
    return {"status": "booked", "booked_time": start.strftime("%Y-%m-%dT%H:%M:00"), "timezone": tz, "minutes": minutes,
            "company": _client_name(db, org)}


def cancel_links(db: Session, org: Organization, opp: AcquisitionOpportunity, actor: User) -> int:
    """Withdraw every open walkthrough link for this prospect."""
    from app.services.acquisition import engine as E
    rows = (db.query(BookingLink).filter(BookingLink.lead_id == opp.lead_id, BookingLink.status == "pending")
            .all())
    for r in rows:
        r.status = "cancelled"
    if rows:
        E._event(db, org.id, opp.id, "booking_link_cancelled", actor.id, note="%d open link(s) withdrawn" % len(rows))
    db.commit()
    return len(rows)
