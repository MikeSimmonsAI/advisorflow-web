"""
WHO WAS CONTACTED, BY WHAT, AND WHAT HAPPENED TO IT.

SS3 and SS4 share one module because they are one question asked over two
windows: "today" and "this period, grouped".

WHY THIS IS NOT A FILTER OVER THE ACTIVITY FEED

The Activity screen answered "sent today" by fetching 300 rows from a 30-day
endpoint and filtering them in the browser with `new Date(x).toDateString()`.
An organization that sends more than 300 messages in a month therefore
undercounted today - silently, with a confident number on screen - and the
badge topped out at 300 whatever the truth was. A day boundary is a server
question: it depends on the organization's timezone, not the viewer's laptop.

WHAT MAKES THE ANSWER POSSIBLE NOW

Three things that did not exist before this workstream:

  send_source        which automation or human action produced the send, from
                     app/services/send_source.py.
  sent_by_user_id    the human who actually pressed the button, which is NOT
                     sender_id - that is the lead's assigned advisor, so the
                     email signs as the advisor the family deals with.
  cadence_touch_logs a row per cadence attempt including the ones that were
                     blocked, suppressed or failed and produced no message.

NULL IS REPORTED AS UNRECORDED, NEVER AS A GUESS. Rows written before
attribution existed have no source and no actor, and a report that quietly
labelled them "manual" would be inventing history. They are counted under
`unrecorded` so the shape of what is not yet known is visible rather than
hidden.
"""

from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.models import (
    CadenceTouchLog, EmailMessage, Lead, Message, Reply, User,
)

UNRECORDED = "unrecorded"


def _org_day_bounds(db: Session, organization_id: str,
                    on: Optional[datetime] = None,
                    tzname: Optional[str] = None):
    """Start and end of "today" for this organization, in UTC.

    A DAY BOUNDARY IS A SERVER QUESTION. The Activity screen computed it with
    `new Date(x).toDateString()` in the browser, so "today" was the viewer's
    day - which is the wrong day for an advisor working a funeral home two
    time zones away, and a different day again for anyone looking after 6pm
    Central from the east coast.

    THERE IS NO ORGANIZATION TIMEZONE COLUMN, and that is worth stating rather
    than papering over. The platform's convention for "when a business day
    starts" lives in scheduling_models.DEFAULT_TIMEZONE and on each advisor's
    `booking_timezone`. This uses the advisors' timezone when the organization
    agrees on one - which is the normal case for a single funeral home - and
    the platform default otherwise. A caller may override per request.

    Adding Organization.timezone is the real fix and is a separate, deliberate
    change; until then this resolves the same answer from the data that does
    exist rather than defaulting to UTC and being quietly wrong by six hours.
    """
    from app.models.scheduling_models import DEFAULT_TIMEZONE
    from app.models.models import User as _User

    if not tzname:
        zones = [z for (z,) in db.query(_User.booking_timezone)
                 .filter(_User.organization_id == organization_id,
                         _User.booking_timezone.isnot(None))
                 .distinct().all()]
        tzname = zones[0] if len(zones) == 1 else DEFAULT_TIMEZONE

    now = on or datetime.utcnow()
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tzname)
        local = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        start_local = datetime.combine(local.date(), time.min, tzinfo=tz)
        start = start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    except Exception:
        start = datetime.combine(now.date(), time.min)
        tzname = "UTC"
    return start, start + timedelta(days=1), tzname


def sent_today(db: Session, organization_id: str, *,
               user_ids: Optional[List[str]] = None,
               limit: int = 500, on: Optional[datetime] = None,
               tzname: Optional[str] = None) -> Dict[str, Any]:
    """SS3 — everything this organization sent today, answered by the server.

    `user_ids`, when given, narrows to sends attributable to those people -
    either because they are recorded as the actor OR because the lead is
    theirs. That is the same both-sides rule lead_scope.own_or_assigned_records_only
    applies, and for the same reason: the write stamps the lead's advisor while
    the person asking may be whoever pressed send.
    """
    start, end, tzname = _org_day_bounds(db, organization_id, on, tzname)
    limit = max(1, min(int(limit or 500), 2000))

    rows: List[Dict[str, Any]] = []

    sms = (db.query(Message, Lead)
           .join(Lead, Message.lead_id == Lead.id)
           .filter(Lead.organization_id == organization_id,
                   Message.sent_at >= start, Message.sent_at < end))
    emails = (db.query(EmailMessage, Lead)
              .join(Lead, EmailMessage.lead_id == Lead.id)
              .filter(Lead.organization_id == organization_id,
                      EmailMessage.sent_at >= start, EmailMessage.sent_at < end))

    if user_ids:
        from sqlalchemy import or_
        sms = sms.filter(or_(Message.sent_by_user_id.in_(user_ids),
                             Message.sender_id.in_(user_ids),
                             Lead.assigned_to_id.in_(user_ids)))
        emails = emails.filter(or_(EmailMessage.sent_by_user_id.in_(user_ids),
                                   EmailMessage.sender_id.in_(user_ids),
                                   Lead.assigned_to_id.in_(user_ids)))

    from app.services.message_state import describe as _describe
    for m, lead in sms.order_by(Message.sent_at.desc()).limit(limit).all():
        rows.append(_row("sms", m.id, lead, m.sent_at,
                         source=m.send_source, actor_id=m.sent_by_user_id,
                         advisor_id=m.sender_id,
                         delivery=_describe(m), body=m.body))
    for e, lead in emails.order_by(EmailMessage.sent_at.desc()).limit(limit).all():
        rows.append(_row("email", e.id, lead, e.sent_at,
                         source=e.send_source, actor_id=e.sent_by_user_id,
                         advisor_id=e.sender_id,
                         delivery={"state": e.status, "label": e.status},
                         subject=e.subject))

    rows.sort(key=lambda r: (r["sent_at"] is None, r["sent_at"] or datetime.min),
              reverse=True)
    rows = [r for r in rows if r["sent_at"] is not None] + \
           [r for r in rows if r["sent_at"] is None]
    rows = rows[:limit]

    _name_the_people(db, rows)

    # ── THE COUNTS ARE COUNTED, NOT INFERRED FROM THE PAGE ──────────────────
    #
    # THE DEFECT THIS FIXES. Every figure below used to be tallied from `rows`,
    # which is truncated to `limit` — so `total` was not "how many were sent
    # today", it was "how many of them fit on this page", and it silently
    # equalled the limit for any busy account. A caller asking for one row for
    # a summary tile read a total of 1. It is the same failure this module was
    # written to end, one layer further in: a day's count is a question for
    # the database, not for whatever slice a screen happened to request.
    #
    # Two grouped queries answer all of it — per-lead counts give the total,
    # the distinct-lead count, the repeat-touch count and each row's own
    # `touches_today`; two more give the source breakdown.
    per_lead: Dict[str, int] = {}
    by_channel: Dict[str, int] = {}
    for channel, model, ts in (("sms", Message, Message.sent_at),
                               ("email", EmailMessage, EmailMessage.sent_at)):
        grouped = (db.query(Lead.id, func.count(model.id))
                   .join(model, model.lead_id == Lead.id)
                   .filter(Lead.organization_id == organization_id,
                           ts >= start, ts < end))
        if user_ids:
            grouped = grouped.filter(or_(model.sent_by_user_id.in_(user_ids),
                                         model.sender_id.in_(user_ids),
                                         Lead.assigned_to_id.in_(user_ids)))
        for lead_id, n in grouped.group_by(Lead.id).all():
            per_lead[lead_id] = per_lead.get(lead_id, 0) + int(n)
            by_channel[channel] = by_channel.get(channel, 0) + int(n)

    by_source: Dict[str, int] = {}
    for model, ts in ((Message, Message.sent_at),
                      (EmailMessage, EmailMessage.sent_at)):
        grouped = (db.query(model.send_source, func.count(model.id))
                   .join(Lead, model.lead_id == Lead.id)
                   .filter(Lead.organization_id == organization_id,
                           ts >= start, ts < end))
        if user_ids:
            grouped = grouped.filter(or_(model.sent_by_user_id.in_(user_ids),
                                         model.sender_id.in_(user_ids),
                                         Lead.assigned_to_id.in_(user_ids)))
        for source, n in grouped.group_by(model.send_source).all():
            key = source or UNRECORDED
            by_source[key] = by_source.get(key, 0) + int(n)

    # REPEAT TOUCHES. "Did we contact this family twice today" was not
    # answerable at all before - there was no per-lead grouping anywhere.
    for r in rows:
        r["touches_today"] = per_lead.get(r["lead_id"], 1)

    return {
        "organization_id": organization_id,
        "timezone": tzname,
        "window": {"start": start, "end": end},
        # EVERY SEND TODAY, not every send on this page. `items` is still
        # capped at `limit`; `returned` says so, so a caller can tell a
        # truncated list from a complete one instead of guessing.
        "total": sum(per_lead.values()),
        "returned": len(rows),
        "leads_contacted": len(per_lead),
        "contacted_more_than_once": sum(1 for n in per_lead.values() if n > 1),
        "by_channel": by_channel,
        "by_source": by_source,
        "items": rows,
    }


def _row(channel, row_id, lead, sent_at, *, source, actor_id, advisor_id,
         delivery, body=None, subject=None):
    return {
        "id": row_id,
        "channel": channel,
        "lead_id": lead.id,
        "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip()
                     or lead.phone or lead.email or "—",
        "lead_phone": lead.phone,
        "lead_email": lead.email,
        "sent_at": sent_at,
        "send_source": source,
        # The two identities, kept apart on purpose.
        "sent_by_user_id": actor_id,
        "sent_by_name": None,
        "advisor_user_id": advisor_id,
        "advisor_name": None,
        "delivery": delivery,
        "preview": (body[:120] if body else None) or subject,
    }


def _name_the_people(db: Session, rows: List[Dict[str, Any]]) -> None:
    """One query for every person mentioned, rather than one per row."""
    ids = {r["sent_by_user_id"] for r in rows} | {r["advisor_user_id"] for r in rows}
    ids.discard(None)
    if not ids:
        return
    names = {u.id: (u.full_name or u.email)
             for u in db.query(User).filter(User.id.in_(ids)).all()}
    for r in rows:
        r["sent_by_name"] = names.get(r["sent_by_user_id"])
        r["advisor_name"] = names.get(r["advisor_user_id"])


# ── SS4 ─────────────────────────────────────────────────────────────────────

_EMAIL_STATES = ("queued", "sent", "delivered", "bounced", "failed")


def email_performance(db: Session, organization_id: str, *,
                      since: Optional[datetime] = None,
                      until: Optional[datetime] = None,
                      group_by: str = "source") -> Dict[str, Any]:
    """SS4 — email outcomes from authoritative delivery state.

    NOT INFERRED FROM UI EVENTS. Every number below is a count of rows in
    `email_messages`, whose status is written by the send path from the
    provider's answer. The UI's own success banner was, until recently,
    reporting 'Sent: 0' as a tick.

    WHAT IS HONESTLY NOT HERE YET, and why saying so matters more than a
    plausible zero:

      generated-but-not-sent   AI drafts are not persisted anywhere. There is
                               no table of drafts to count.
      replies per email        `replies` has no email_message_id, so an inbound
                               email can be tied to a lead but not to the
                               message it answers. Reply counts are per LEAD
                               and labelled as such.
      skipped / suppressed     recorded for SMS in cadence_touch_logs, and for
                               email only where the auto-send queue refused an
                               item. A gate refusal on the newly restored paths
                               raises and is logged but writes no row.

    Each of those is reported as a null with a reason rather than as 0.
    """
    until = until or datetime.utcnow()
    since = since or (until - timedelta(days=30))
    group_by = group_by if group_by in ("source", "user", "day", "status") else "source"

    base = (db.query(EmailMessage, Lead)
            .join(Lead, EmailMessage.lead_id == Lead.id)
            .filter(Lead.organization_id == organization_id,
                    EmailMessage.sent_at >= since,
                    EmailMessage.sent_at <= until))
    rows = base.all()

    totals = {state: 0 for state in _EMAIL_STATES}
    for e, _lead in rows:
        totals[e.status if e.status in totals else "queued"] += 1

    buckets: Dict[str, Dict[str, Any]] = {}
    for e, _lead in rows:
        if group_by == "source":
            key = e.send_source or UNRECORDED
        elif group_by == "user":
            key = e.sent_by_user_id or e.sender_id or UNRECORDED
        elif group_by == "day":
            key = e.sent_at.date().isoformat() if e.sent_at else UNRECORDED
        else:
            key = e.status or "queued"
        slot = buckets.setdefault(key, {"key": key, "total": 0,
                                        **{s: 0 for s in _EMAIL_STATES},
                                        "opened": 0, "clicked": 0})
        slot["total"] += 1
        slot[e.status if e.status in totals else "queued"] += 1
        if getattr(e, "opened_at", None):
            slot["opened"] += 1
        if (getattr(e, "click_count", 0) or 0) > 0:
            slot["clicked"] += 1

    if group_by == "user":
        ids = [k for k in buckets if k != UNRECORDED]
        names = {u.id: (u.full_name or u.email)
                 for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
        for key, slot in buckets.items():
            slot["label"] = names.get(key, "Unrecorded" if key == UNRECORDED else key)
    else:
        for key, slot in buckets.items():
            slot["label"] = key

    lead_ids = {lead.id for _e, lead in rows}
    replies = 0
    if lead_ids:
        replies = (db.query(func.count(Reply.id))
                   .filter(Reply.lead_id.in_(lead_ids),
                           Reply.received_at >= since,
                           Reply.received_at <= until).scalar()) or 0

    return {
        "organization_id": organization_id,
        "window": {"since": since, "until": until},
        "group_by": group_by,
        "totals": {
            **totals,
            "all": len(rows),
            "opened": sum(1 for e, _l in rows if getattr(e, "opened_at", None)),
            "clicked": sum(1 for e, _l in rows if (getattr(e, "click_count", 0) or 0) > 0),
        },
        "replies_from_contacted_leads": replies,
        "groups": sorted(buckets.values(), key=lambda b: -b["total"]),
        "not_available": {
            "generated_not_sent": "AI drafts are not persisted; there is no "
                                  "table of drafts to count.",
            "replies_per_email": "replies has no email_message_id, so a reply "
                                 "is tied to a lead and not to the message it "
                                 "answers. The count above is per lead.",
            "skipped_suppressed": "Recorded for SMS in cadence_touch_logs and "
                                  "for the auto-send queue; a gate refusal on "
                                  "the restored email paths raises and logs but "
                                  "writes no row.",
        },
    }


def cadence_outcomes(db: Session, organization_id: str, *,
                     since: Optional[datetime] = None,
                     until: Optional[datetime] = None) -> Dict[str, Any]:
    """The SMS half of the same question, which email cannot yet answer.

    cadence_touch_logs records blocked, suppressed, skipped and failed
    attempts, so this is the one place the platform can currently say how much
    outbound was REFUSED rather than merely how much succeeded.
    """
    until = until or datetime.utcnow()
    since = since or (until - timedelta(days=30))
    rows = (db.query(CadenceTouchLog.outcome, func.count(CadenceTouchLog.id))
            .filter(CadenceTouchLog.organization_id == organization_id,
                    CadenceTouchLog.created_at >= since,
                    CadenceTouchLog.created_at <= until)
            .group_by(CadenceTouchLog.outcome).all())
    return {
        "organization_id": organization_id,
        "window": {"since": since, "until": until},
        "by_outcome": {outcome: count for outcome, count in rows},
    }
