"""THE POST-VISIT SURVEY (Issue #507, Restland).

Four questions, nothing about the family's file:
  1. Overall experience, 1-5
  2. Was the review helpful? yes / somewhat / no
  3. Comments (optional)
  4. Would you like someone to follow up? (optional) - and how: phone / text / email

The link carries only an unguessable token (no ids, no phone, no email, no
appointment details). One answer per appointment; a second submit is refused
politely. Expired links say so. A follow-up request becomes an open task on
the existing task list, assigned to the person who held the appointment, and
an in-app note for the team - no staff text/email is sent (alerts stay off).
Nothing is sent to the family because of an answer, and there is no review
gating or public review request.

Why not the older /survey module: it routes 4-5 star answers to Google and
social review links, which this program must not do, and asks different
questions. It stays unchanged for the programs that use it.
"""
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.reset_models import AppointmentSurvey

HELPFUL = ("yes", "somewhat", "no")
METHODS = ("phone", "text", "email")


class SurveyError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _get(db: Session, token: str) -> AppointmentSurvey:
    if not token or len(token) < 20 or len(token) > 64:
        raise SurveyError("not_found", "This survey link isn't valid.")
    sv = db.query(AppointmentSurvey).filter(AppointmentSurvey.token == token).first()
    if sv is None:
        raise SurveyError("not_found", "This survey link isn't valid.")
    return sv


def context(db: Session, token: str, now: Optional[datetime] = None) -> Dict:
    """What the public page needs - first name and who they met, nothing more."""
    from app.models.models import BookingLink, Lead, User
    now = now or datetime.utcnow()
    sv = _get(db, token)
    lead = db.query(Lead).filter(Lead.id == sv.lead_id, Lead.organization_id == sv.organization_id).first()
    b = db.query(BookingLink).filter(BookingLink.id == sv.booking_id).first()
    adv = db.query(User).filter(User.id == b.user_id).first() if b else None
    state = "submitted" if sv.submitted_at else ("expired" if sv.expires_at < now else "open")
    return {"state": state, "first_name": ((lead.first_name or "").strip().title() if lead else "") or None,
            "advisor": (adv.full_name if adv else None) or "Mike Simmons",
            "business": "Restland Cemetery and Funeral Home",
            "questions": {"helpful": list(HELPFUL), "methods": list(METHODS)}}


def submit(db: Session, token: str, body: Dict, now: Optional[datetime] = None) -> Dict:
    from app.models.models import BookingLink, Lead
    now = now or datetime.utcnow()
    sv = _get(db, token)
    if sv.submitted_at is not None:
        raise SurveyError("already", "Thank you - we already have your answers.")
    if sv.expires_at < now:
        raise SurveyError("expired", "This survey has closed. If you'd like to share anything, just reply to our message.")
    try:
        rating = int(body.get("rating"))
    except (TypeError, ValueError):
        raise SurveyError("invalid", "Please choose a rating from 1 to 5.")
    if not 1 <= rating <= 5:
        raise SurveyError("invalid", "Please choose a rating from 1 to 5.")
    helpful = (body.get("helpful") or "").strip().lower()
    if helpful not in HELPFUL:
        raise SurveyError("invalid", "Please tell us whether the review was helpful.")
    comments = (body.get("comments") or "").strip()[:2000] or None
    follow = bool(body.get("follow_up"))
    method = (body.get("follow_up_method") or "").strip().lower() or None
    if follow and method not in METHODS:
        raise SurveyError("invalid", "Please choose how you'd like us to follow up.")
    if not follow:
        method = None
    # Exactly one answer, even with two submits racing.
    res = db.execute(update(AppointmentSurvey)
                     .where(AppointmentSurvey.id == sv.id, AppointmentSurvey.submitted_at.is_(None))
                     .values(rating=rating, helpful=helpful, comments=comments, follow_up=follow,
                             follow_up_method=method, submitted_at=now)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        raise SurveyError("already", "Thank you - we already have your answers.")
    db.commit()
    db.refresh(sv)
    task_id = None
    if follow:
        task_id = _follow_up_task(db, sv, method)
    return {"ok": True, "follow_up_task": bool(task_id)}


def _follow_up_task(db: Session, sv: AppointmentSurvey, method: str) -> Optional[str]:
    from app.models.models import BookingLink, Lead
    from app.models.work_models import LeadTask
    from app.models.program_models import ProgramAlert
    b = db.query(BookingLink).filter(BookingLink.id == sv.booking_id).first()
    lead = db.query(Lead).filter(Lead.id == sv.lead_id).first()
    name = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() if lead else "A family"
    t = LeadTask(organization_id=sv.organization_id, lead_id=sv.lead_id,
                 title="Follow up after the Family File Review (%s)" % {"phone": "call", "text": "text",
                                                                       "email": "email"}[method],
                 details="%s asked for a follow-up by %s in the visit survey." % (name, method),
                 status="open", assigned_to_id=getattr(b, "user_id", None), source="visit_survey")
    db.add(t)
    db.flush()
    sv.task_id = t.id
    # In-app only: the team sees it on the alert list; no staff text/email
    # goes while staff alerts are off.
    db.add(ProgramAlert(organization_id=sv.organization_id, kind="task", audience="team", channel="in_app",
                        delivered=True, reason="visit_survey_follow_up",
                        message="%s asked for a follow-up (%s) after their visit." % (name, method)))
    db.commit()
    return t.id


def staff_view(sv: Optional[AppointmentSurvey]) -> Optional[Dict]:
    if sv is None:
        return None
    iso = lambda d: d.isoformat() + "Z" if d else None   # noqa: E731
    return {"submitted_at": iso(sv.submitted_at), "expires_at": iso(sv.expires_at), "rating": sv.rating,
            "helpful": sv.helpful, "comments": sv.comments, "follow_up": sv.follow_up,
            "follow_up_method": sv.follow_up_method, "task_id": sv.task_id}
