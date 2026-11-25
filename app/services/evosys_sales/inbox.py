"""EvoSys BookIt text inbox rules, shared by the Today screen, the AI auto-answer
and the person's Send button (owner, 2026-11-20).

- A reply is ANSWERED once anything real went back to that lead after it: a
  person's text, the AI's answer, or a booking confirmation. Appointment
  reminders don't count (they are not an answer).
- Every lead shows ONCE, in one of three groups:
    needs_you  - their latest text has no answer yet
    handled    - answered (shows what went back and who sent it)
    opted_out  - said STOP / is on Do Not Contact: shown, never actionable
- The same text is never sent to the same lead twice, and the AI never sends a
  second text right after another one.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models.evosys_sales_models import EvoSysOutreachMessage as M

REMINDER_PREFIXES = ("rd:", "r1:", "rm:")
DUPLICATE_DAYS = 14
AI_QUIET_MINUTES = 5           # no AI text if anything went to this lead this recently


def ct(d: datetime) -> str:
    """'Nov 20, 5:34 PM CT' from a naive UTC time (daylight saving handled)."""
    from zoneinfo import ZoneInfo
    from datetime import timezone
    x = d.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Chicago"))
    return x.strftime("%b %-d, %-I:%M %p") + " CT"


def is_reminder(m) -> bool:
    return bool(m.label) and m.label.startswith(REMINDER_PREFIXES)


def counts_as_answer(m) -> bool:
    """An outbound message that answers whatever they said before it."""
    return (m.direction == "out" and m.status in ("sent", "logged") and m.step_no is None
            and not is_reminder(m))


def _norm(text: str) -> str:
    t = re.sub(r"https?://\S+", "", (text or "").lower())
    return re.sub(r"[^a-z0-9áéíóúñü]+", " ", t).strip()


def duplicate_of(db: Session, opp_id: str, body: str, days: int = DUPLICATE_DAYS) -> Optional[M]:
    """The earlier text to this lead with the same words (links ignored), if any."""
    want = _norm(body)
    if not want:
        return None
    since = datetime.utcnow() - timedelta(days=days)
    for m in (db.query(M).filter(M.opportunity_id == opp_id, M.direction == "out", M.channel == "text",
                                 M.status == "sent", M.created_at >= since).all()):
        if _norm(m.body) == want:
            return m
    return None


def recent_out(db: Session, opp_id: str, minutes: int = AI_QUIET_MINUTES) -> Optional[M]:
    since = datetime.utcnow() - timedelta(minutes=minutes)
    rows = (db.query(M).filter(M.opportunity_id == opp_id, M.direction == "out", M.channel == "text",
                               M.status == "sent", M.created_at >= since)
            .order_by(M.created_at.desc()).all())
    return next((m for m in rows if counts_as_answer(m)), None)   # campaign texts and reminders don't count


def who_sent(m) -> str:
    if m.label == "ai_answer":
        return "ai"
    if m.label == "booking_confirmation":
        return "booking"
    if (m.body or "").strip() == "Answered outside the system":
        return "outside"
    return "person" if m.actor_user_id else "system"


def group(db: Session, opp_ids: Iterable[str], opted_out_ids: set, since: datetime) -> dict:
    """{'needs_you': [...], 'handled': [...], 'opted_out': [...]}; each item is
    (latest inbound message, last answer or None), newest first, one per lead."""
    ids = list(opp_ids)
    out = {"needs_you": [], "handled": [], "opted_out": []}
    if not ids:
        return out
    msgs = (db.query(M).filter(M.opportunity_id.in_(ids), M.created_at >= since - timedelta(days=30))
            .order_by(M.created_at).all())
    by_lead = {}
    for m in msgs:
        by_lead.setdefault(m.opportunity_id, []).append(m)
    for oid, ms in by_lead.items():
        ins = [m for m in ms if m.direction == "in"]
        if not ins or ins[-1].created_at < since:
            continue
        last_in = ins[-1]
        answers = [m for m in ms if counts_as_answer(m)]
        last_answer = answers[-1] if answers else None
        stopped = oid in opted_out_ids or any(m.label == "unsubscribe" for m in ins)
        if stopped:
            out["opted_out"].append((last_in, None))
        elif last_answer is not None and last_answer.created_at >= last_in.created_at:
            out["handled"].append((last_in, last_answer))
        else:
            out["needs_you"].append((last_in, last_answer))
    for k in out:
        out[k].sort(key=lambda p: p[0].created_at, reverse=True)
    return out
