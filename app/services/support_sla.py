"""THE SLA CLOCK — measured in business minutes, because that is what we sold.

WHY NOT WALL-CLOCK HOURS
------------------------
"8 hours" measured on a wall clock means a ticket raised at 16:45 on Friday is
breached before anyone could have been at a desk, and a ticket raised at 09:00
on Monday gets its full window. Same promise, two entirely different products,
decided by when the customer happened to be annoyed. So the target is in
BUSINESS minutes and this module owns what a business minute is.

FOUR THINGS DECIDE THE CLOCK, AND ALL FOUR ARE CONFIGURATION
------------------------------------------------------------
    the brand's timezone            support_brand_settings.timezone
    which days are working days     .business_days
    the window inside a day         .business_start / .business_end
    the days nobody is here         .holidays_json

A brand with no row gets Monday–Friday 09:00–17:00 America/Chicago and NO
holidays. That default is deliberately CONSERVATIVE IN THE EXPENSIVE
DIRECTION: guessing 24/7 would make every target shorter than promised and
manufacture breaches; guessing a short week makes them longer, which costs us
and never lies to a customer.

THE EMERGENCY EXCEPTION
-----------------------
`emergency_is_24x7` makes the emergency queue run on wall-clock time. A
verified P1 outage does not wait for Monday, and encoding that as "critical has
a very small business-minute target" would still have made it wait for Monday.

WHAT PAUSING IS, AND WHAT IT IS NOT
------------------------------------
WAITING_ON_CUSTOMER pauses the first-response clock, because the next move is
theirs. It BANKS the minutes already spent and records the pause; it never
rewinds. Every pause and resume writes a ticket event, so "why did this take
nine days" has an answer that is not "somebody parked it".

Pausing a ticket that has ALREADY had its first response changes nothing —
the clock it would pause has already stopped. That is not a special case in
the callers; `pause()` simply has nothing to do.

NO RESOLUTION PROMISE. Every target here is FIRST RESPONSE. A resolution time
nobody has committed to must not appear on a customer's screen as though
somebody had.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.support_models import (
    Queue, Severity, SlaState, SupportBrandSettings, SupportTicket,
)
from app.services import support_entitlements

log = logging.getLogger(__name__)

# The documented fallback. Mirrors `scheduling_models.DEFAULT_TIMEZONE`, which
# is the team's own zone, so a brand that has not configured support hours
# behaves like the rest of the platform rather than like a different product.
DEFAULT_HOURS: Dict[str, Any] = {
    "timezone": "America/Chicago",
    "business_days": (0, 1, 2, 3, 4),
    "business_start_minute": 9 * 60,
    "business_end_minute": 17 * 60,
    "holidays": (),
    "emergency_is_24x7": True,
    "source": "default",
}

# When a ticket is close enough to its target that a human should look. 75% is
# a judgement, stated once here rather than in whichever screen renders a
# badge, so "at risk" means one thing across the platform.
AT_RISK_FRACTION = 0.75


def _parse_hhmm(value: Optional[str], fallback_minute: int) -> int:
    if not value:
        return fallback_minute
    try:
        hh, mm = str(value).strip().split(":")[:2]
        minute = int(hh) * 60 + int(mm)
    except (ValueError, TypeError):
        log.warning("support_sla: unreadable time %r; using the default", value)
        return fallback_minute
    return max(0, min(minute, 24 * 60))


def _parse_days(value: Optional[str]) -> tuple:
    if not value:
        return DEFAULT_HOURS["business_days"]
    days = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            day = int(part)
        except ValueError:
            continue
        if 0 <= day <= 6 and day not in days:
            days.append(day)
    return tuple(sorted(days)) or DEFAULT_HOURS["business_days"]


def _parse_holidays(raw: Optional[str]) -> tuple:
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("support_sla: unreadable holidays_json; treating as none")
        return ()
    out = []
    for item in parsed if isinstance(parsed, list) else []:
        try:
            out.append(date.fromisoformat(str(item)))
        except ValueError:
            continue
    return tuple(out)


def hours_for(db: Optional[Session], platform_id: Optional[str]) -> Dict[str, Any]:
    """This brand's working calendar. Never raises, never returns None."""
    cfg = dict(DEFAULT_HOURS)
    if db is None or not platform_id:
        return cfg
    try:
        row = (db.query(SupportBrandSettings)
               .filter(SupportBrandSettings.platform_id == platform_id)
               .first())
    except Exception:                                       # noqa: BLE001
        log.exception("support_sla: hours lookup failed for platform=%s", platform_id)
        return cfg
    if row is None:
        return cfg

    cfg["timezone"] = row.timezone or DEFAULT_HOURS["timezone"]
    cfg["business_days"] = _parse_days(row.business_days)
    cfg["business_start_minute"] = _parse_hhmm(row.business_start, 9 * 60)
    cfg["business_end_minute"] = _parse_hhmm(row.business_end, 17 * 60)
    cfg["holidays"] = _parse_holidays(row.holidays_json)
    cfg["emergency_is_24x7"] = bool(row.emergency_is_24x7)
    cfg["source"] = "config"

    # A window that ends before it starts is unusable and would silently make
    # every day zero minutes long — which reads as "we are never open" and
    # breaches every ticket at once. Refuse it and say so.
    if cfg["business_end_minute"] <= cfg["business_start_minute"]:
        log.warning("support_sla: platform=%s has business_end <= business_start; "
                    "falling back to the default window", platform_id)
        cfg["business_start_minute"] = DEFAULT_HOURS["business_start_minute"]
        cfg["business_end_minute"] = DEFAULT_HOURS["business_end_minute"]
    return cfg


def _is_business_day(day: date, cfg: Dict[str, Any]) -> bool:
    return day.weekday() in cfg["business_days"] and day not in cfg["holidays"]


def _local(dt_utc: datetime, cfg: Dict[str, Any]) -> datetime:
    from app.services.availability import utc_to_local
    return utc_to_local(dt_utc, cfg["timezone"])


def _to_utc(day: date, minute_of_day: int, cfg: Dict[str, Any]) -> datetime:
    from app.services.availability import local_to_utc
    return local_to_utc(day, minute_of_day, cfg["timezone"])


def business_minutes_between(start_utc: datetime, end_utc: datetime,
                             cfg: Dict[str, Any], *, continuous: bool = False) -> int:
    """How much of the support week separates two instants.

    `continuous=True` is the emergency queue: every minute counts, because
    somebody is on the hook for it.

    Walking local DATES rather than converting the window to UTC once is what
    makes this correct across a DST transition — 09:00 local stays 09:00 local
    and only its offset moves, exactly as `availability.py` documents.
    """
    if start_utc is None or end_utc is None or end_utc <= start_utc:
        return 0
    if continuous:
        return int((end_utc - start_utc).total_seconds() // 60)

    total = 0
    day = _local(start_utc, cfg).date()
    last = _local(end_utc, cfg).date()
    # A guard rather than a while-True: a corrupt timestamp pair must not spin
    # a request thread. Two years of business days is far past any real ticket.
    guard = 0
    while day <= last and guard < 800:
        guard += 1
        if _is_business_day(day, cfg):
            open_utc = _to_utc(day, cfg["business_start_minute"], cfg)
            close_utc = _to_utc(day, cfg["business_end_minute"], cfg)
            lo = max(open_utc, start_utc)
            hi = min(close_utc, end_utc)
            if hi > lo:
                total += int((hi - lo).total_seconds() // 60)
        day += timedelta(days=1)
    return total


def add_business_minutes(start_utc: datetime, minutes: int,
                         cfg: Dict[str, Any], *, continuous: bool = False) -> datetime:
    """The instant `minutes` of support time after `start_utc`.

    A ticket raised outside the window starts its clock at the next opening
    rather than accruing overnight — which is the whole reason a business-hours
    SLA exists.
    """
    if minutes is None:
        return start_utc
    minutes = max(int(minutes), 0)
    if continuous:
        return start_utc + timedelta(minutes=minutes)

    remaining = minutes
    day = _local(start_utc, cfg).date()
    cursor = start_utc
    guard = 0
    while guard < 800:
        guard += 1
        if _is_business_day(day, cfg):
            open_utc = _to_utc(day, cfg["business_start_minute"], cfg)
            close_utc = _to_utc(day, cfg["business_end_minute"], cfg)
            window_start = max(open_utc, cursor)
            if close_utc > window_start:
                available = int((close_utc - window_start).total_seconds() // 60)
                if remaining <= available:
                    return window_start + timedelta(minutes=remaining)
                remaining -= available
        day += timedelta(days=1)
        cursor = _to_utc(day, 0, cfg)
    # Ran out of guard. Returning the last cursor is wrong in a way somebody
    # would notice; refusing to answer is not an option on a create path. Log
    # loudly and give a wall-clock answer so the ticket still has a target.
    log.error("support_sla: add_business_minutes exhausted its day guard for "
              "minutes=%s tz=%s", minutes, cfg.get("timezone"))
    return start_utc + timedelta(minutes=minutes)


# ══════════════════════════════════════════════════════════════════════════
# TICKET-LEVEL
# ══════════════════════════════════════════════════════════════════════════

def is_continuous(queue: Optional[str], cfg: Dict[str, Any]) -> bool:
    """Does this ticket's clock run through the night?"""
    return bool(queue == Queue.EMERGENCY and cfg.get("emergency_is_24x7", True))


def target_for(entitlement: Dict[str, Any], severity: str) -> Optional[int]:
    return support_entitlements.first_response_minutes(entitlement, severity)


def compute_first_response_due(db: Optional[Session], *, platform_id: Optional[str],
                               entitlement: Dict[str, Any], severity: str,
                               queue: str, started_at: datetime) -> Optional[datetime]:
    """When we have promised to have said something. None means no promise.

    P4 has no first-response target by design (see
    `support_entitlements._shape`). Returning a due date anyway would invent a
    commitment, and the first thing anyone would do with it is measure us
    against it.
    """
    minutes = target_for(entitlement, severity)
    if minutes is None:
        return None
    cfg = hours_for(db, platform_id)
    return add_business_minutes(started_at, minutes, cfg,
                                continuous=is_continuous(queue, cfg))


def evaluate(db: Optional[Session], ticket: SupportTicket,
             now: Optional[datetime] = None) -> Dict[str, Any]:
    """The ticket's SLA state, computed rather than trusted.

    `ticket.sla_state` is a cached column for querying a large queue quickly;
    THIS is the answer. `refresh()` writes what this returns back onto the row,
    so the two can never disagree for longer than one evaluation.
    """
    now = now or datetime.utcnow()
    cfg = hours_for(db, getattr(ticket, "platform_id", None))
    continuous = is_continuous(ticket.queue, cfg)
    due = ticket.first_response_due_at

    if due is None:
        return {
            "state": SlaState.NOT_APPLICABLE,
            "due_at": None, "responded_at": ticket.first_response_at,
            "minutes_remaining": None, "minutes_over": None,
            "target_minutes": None, "continuous": continuous,
            "reason": "This request has no first-response commitment.",
        }

    if ticket.first_response_at is not None:
        met = ticket.first_response_at <= due
        return {
            "state": SlaState.MET if met else SlaState.BREACHED,
            "due_at": due, "responded_at": ticket.first_response_at,
            "minutes_remaining": None,
            "minutes_over": (
                None if met else business_minutes_between(
                    due, ticket.first_response_at, cfg, continuous=continuous)),
            "target_minutes": None, "continuous": continuous,
            "reason": "First response sent." if met
                      else "First response was later than the target.",
        }

    if ticket.sla_paused_at is not None:
        return {
            "state": SlaState.PAUSED,
            "due_at": due, "responded_at": None,
            "minutes_remaining": None, "minutes_over": None,
            "target_minutes": None, "continuous": continuous,
            "paused_at": ticket.sla_paused_at,
            "reason": "Waiting on the customer — the clock is stopped.",
        }

    if now >= due:
        return {
            "state": SlaState.BREACHED,
            "due_at": due, "responded_at": None,
            "minutes_remaining": 0,
            "minutes_over": business_minutes_between(due, now, cfg,
                                                     continuous=continuous),
            "target_minutes": None, "continuous": continuous,
            "reason": "The first-response target has passed.",
        }

    remaining = business_minutes_between(now, due, cfg, continuous=continuous)
    started = ticket.sla_clock_started_at or ticket.created_at or now
    total = business_minutes_between(started, due, cfg, continuous=continuous)
    total += int(ticket.sla_elapsed_minutes or 0)
    at_risk = total > 0 and (total - remaining) >= total * AT_RISK_FRACTION

    return {
        "state": SlaState.AT_RISK if at_risk else SlaState.WITHIN,
        "due_at": due, "responded_at": None,
        "minutes_remaining": remaining, "minutes_over": None,
        "target_minutes": total or None, "continuous": continuous,
        "reason": ("Approaching the first-response target." if at_risk
                   else "Within the first-response target."),
    }


def refresh(db: Session, ticket: SupportTicket,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Recompute and persist the cached state. Returns the full evaluation."""
    result = evaluate(db, ticket, now=now)
    ticket.sla_state = result["state"]
    return result


def pause(db: Session, ticket: SupportTicket, now: Optional[datetime] = None) -> bool:
    """Stop the first-response clock and BANK what has been spent.

    Returns True only if a running clock was actually stopped, so a caller can
    decide whether an event is worth writing. Idempotent: pausing a paused
    ticket, a ticket with no target, or one that has already been answered
    does nothing and says so.
    """
    now = now or datetime.utcnow()
    if ticket.first_response_at is not None:
        return False
    if ticket.first_response_due_at is None:
        return False
    if ticket.sla_paused_at is not None:
        return False

    cfg = hours_for(db, getattr(ticket, "platform_id", None))
    started = ticket.sla_clock_started_at or ticket.created_at or now
    spent = business_minutes_between(started, now, cfg,
                                     continuous=is_continuous(ticket.queue, cfg))
    ticket.sla_elapsed_minutes = int(ticket.sla_elapsed_minutes or 0) + spent
    ticket.sla_paused_at = now
    ticket.sla_state = SlaState.PAUSED
    return True


def resume(db: Session, ticket: SupportTicket, now: Optional[datetime] = None) -> bool:
    """Restart the clock, pushing the due date out by the time it was stopped.

    The DUE DATE MOVES; the elapsed total does not shrink. That is the honest
    arithmetic: the customer had the ball for three days, so our target is
    three support-days later, and the ticket's age still shows three days on
    it. Moving the target without recording the pause is how a queue becomes
    a fiction.
    """
    now = now or datetime.utcnow()
    if ticket.sla_paused_at is None:
        return False

    cfg = hours_for(db, getattr(ticket, "platform_id", None))
    continuous = is_continuous(ticket.queue, cfg)
    paused_for = business_minutes_between(ticket.sla_paused_at, now, cfg,
                                          continuous=continuous)
    if ticket.first_response_due_at is not None and paused_for:
        ticket.first_response_due_at = add_business_minutes(
            ticket.first_response_due_at, paused_for, cfg, continuous=continuous)

    ticket.sla_paused_at = None
    ticket.sla_clock_started_at = now
    refresh(db, ticket, now=now)
    return True


def describe(result: Dict[str, Any]) -> Dict[str, Any]:
    """The evaluation plus the words for it, so no screen keeps its own copy."""
    state = result.get("state", SlaState.NOT_APPLICABLE)
    labels = {
        SlaState.WITHIN: "Within target",
        SlaState.AT_RISK: "At risk",
        SlaState.BREACHED: "Past target",
        SlaState.PAUSED: "Paused — waiting on you",
        SlaState.MET: "Responded on time",
        SlaState.NOT_APPLICABLE: "No response target",
    }
    out = dict(result)
    out["label"] = labels.get(state, state)
    return out


def queue_targets(db: Optional[Session], entitlement: Dict[str, Any],
                  platform_id: Optional[str]) -> List[Dict[str, Any]]:
    """The customer-facing table on the Support Plan page.

    Rendered from the SAME entitlement the engine uses, so the page cannot
    advertise a target the clock does not honour.
    """
    cfg = hours_for(db, platform_id)
    rows = []
    for severity in Severity.ALL:
        minutes = target_for(entitlement, severity)
        rows.append({
            "severity": severity,
            "label": Severity.LABELS[severity],
            "meaning": Severity.MEANINGS[severity],
            "first_response_minutes": minutes,
            "first_response_text": _humanize(minutes, cfg),
        })
    return rows


def _humanize(minutes: Optional[int], cfg: Dict[str, Any]) -> str:
    """Business minutes as a person would say them.

    A support day is however long this brand's window is — dividing by 8 when
    the brand works a 6-hour day would quote a target it does not keep.
    """
    if minutes is None:
        return "No committed response target"
    day_minutes = max(cfg["business_end_minute"] - cfg["business_start_minute"], 60)
    if minutes <= 60:
        return "1 hour" if minutes == 60 else "%d minutes" % minutes
    if minutes < day_minutes:
        hours = minutes / 60.0
        return ("%d business hours" % hours) if hours.is_integer() else (
            "%.1f business hours" % hours)
    days = minutes / float(day_minutes)
    if days == 1:
        return "1 business day"
    return ("%d business days" % days) if float(days).is_integer() else (
        "%.1f business days" % days)


def hours_summary(db: Optional[Session], platform_id: Optional[str]) -> Dict[str, Any]:
    """Support hours, in the words a customer reads on their plan page."""
    cfg = hours_for(db, platform_id)
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]
    days = [names[d] for d in cfg["business_days"]]
    if len(days) > 1 and cfg["business_days"] == tuple(range(cfg["business_days"][0],
                                                            cfg["business_days"][-1] + 1)):
        day_text = "%s to %s" % (days[0], days[-1])
    else:
        day_text = ", ".join(days) if days else "No configured days"

    def _clock(minute: int) -> str:
        return "%02d:%02d" % (minute // 60, minute % 60)

    return {
        "timezone": cfg["timezone"],
        "days": day_text,
        "start": _clock(cfg["business_start_minute"]),
        "end": _clock(cfg["business_end_minute"]),
        "holiday_count": len(cfg["holidays"]),
        "emergency_is_24x7": cfg["emergency_is_24x7"],
        "source": cfg["source"],
        "text": "%s, %s–%s %s" % (day_text, _clock(cfg["business_start_minute"]),
                                  _clock(cfg["business_end_minute"]), cfg["timezone"]),
    }
