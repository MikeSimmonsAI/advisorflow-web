"""Engagement rate for a location outreach program: of the people we have
reached, how many did something back.

A COHORT, NOT A RATIO OF EVENTS. The question is "of the people we contacted,
what share engaged?", so both sides count PEOPLE (distinct leads), and the
denominator is everyone texted or emailed in the window. A person counts as
engaged once, however many things they did.

    engaged    replied (anything but an opt-out), opened their booking page,
               or booked - the positive signals
    responded  engaged, plus the people who opted out - every human answer

Opt-outs are reported on their own and are NOT in the engagement rate: a STOP
proves the message reached a person, but it is not the system working, and a
number shown to a client should not be flattered by it.

Test records never count, on either side.
"""
from datetime import datetime
from typing import Dict, Optional, Set

from sqlalchemy.orm import Session

BOOKED_STATUSES = ("booked", "confirmed", "completed")


def _pct(n: int, d: int) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


def summary(db: Session, org_id: str, since: Optional[datetime] = None,
            until: Optional[datetime] = None) -> Dict:
    """People contacted in [since, until) and what share of them engaged
    (at any time since they were contacted)."""
    from app.models.models import BookingLink, EmailMessage, Lead, Message
    from app.models.program_models import BookingPageEvent, ProgramResponse

    real = db.query(Lead.id).filter(Lead.organization_id == org_id, Lead.is_test.isnot(True))

    def window(q, col):
        if since is not None:
            q = q.filter(col >= since)
        if until is not None:
            q = q.filter(col < until)
        return q

    texted = window(db.query(Message.lead_id).filter(Message.lead_id.in_(real)), Message.sent_at)
    emailed = window(db.query(EmailMessage.lead_id).filter(EmailMessage.lead_id.in_(real),
                                                           EmailMessage.status != "failed"),
                     EmailMessage.sent_at)
    contacted: Set[str] = {i for (i,) in texted.distinct().all()} | {i for (i,) in emailed.distinct().all()}
    # Message totals for the same window (every message, not people).
    tstat = [((d or t or "sent") or "sent").lower() for d, t in window(
        db.query(Message.delivery_status, Message.twilio_status).filter(Message.lead_id.in_(real)),
        Message.sent_at).all()]
    erows = window(db.query(EmailMessage.status, EmailMessage.opened_at).filter(EmailMessage.lead_id.in_(real)),
                   EmailMessage.sent_at).all()
    estat = [(st or "sent").lower() for st, _o in erows]
    emails_opened = sum(1 for _s, o in erows if o is not None)
    bad = ("failed", "undelivered", "bounced")
    out = {"contacted": len(contacted), "engaged": 0, "engaged_pct": None, "responded": 0,
           "responded_pct": None, "replied": 0, "opened_booking": 0, "booked": 0, "opted_out": 0,
           "texts_sent": len(tstat), "texts_delivered": sum(1 for x in tstat if x == "delivered"),
           "emails_sent": sum(1 for x in estat if x not in bad), "emails_opened": emails_opened,
           "failed": sum(1 for x in tstat + estat if x in bad)}
    if not contacted:
        return out
    ids = list(contacted)

    replied, opted = set(), set()
    for lead_id, cls in (db.query(ProgramResponse.lead_id, ProgramResponse.response_class)
                         .filter(ProgramResponse.organization_id == org_id,
                                 ProgramResponse.lead_id.in_(ids)).all()):
        (opted if cls == "opt_out" else replied).add(lead_id)
    opened = {i for (i,) in db.query(BookingPageEvent.lead_id).filter(
        BookingPageEvent.organization_id == org_id, BookingPageEvent.lead_id.in_(ids),
        BookingPageEvent.event == "opened").distinct().all()}
    booked = {i for (i,) in db.query(BookingLink.lead_id).filter(
        BookingLink.lead_id.in_(ids), BookingLink.status.in_(BOOKED_STATUSES)).distinct().all()}
    booked |= {i for (i,) in db.query(BookingPageEvent.lead_id).filter(
        BookingPageEvent.organization_id == org_id, BookingPageEvent.lead_id.in_(ids),
        BookingPageEvent.event == "booked").distinct().all()}

    engaged = replied | opened | booked
    responded = engaged | opted
    n = len(contacted)
    out.update(engaged=len(engaged), engaged_pct=_pct(len(engaged), n),
               responded=len(responded), responded_pct=_pct(len(responded), n),
               replied=len(replied), opened_booking=len(opened), booked=len(booked),
               opted_out=len(opted - engaged))
    return out
