"""CAMPAIGN PROGRESS (owner, Nov 7 2026) - where every family in one SCI
campaign stands, what went out, what is coming, and whether the senders are
keeping up. Read-only, counts only (no names, numbers, addresses or message
words), one tenant.

Every family is in exactly ONE roster group, decided in this order:
  excluded     - a source row held out of this campaign (e.g. already in the
                 original SCI campaign) and no approval
  data_review  - held for a location / data check, no approval
  not_started  - approved, journey never started (own-copy campaigns), or
                 approved and never contacted
  stopped      - STOP / DNC, replied, a person owns it, or an appointment
  held         - texts on an SMS Recovery hold
  completed    - texts done (or none) and emails done (or none possible)
  active       - everything else that is still moving
Attempts are never shown as deliveries: SMS "attempted" counts provider
accepts, "delivered" only carrier-confirmed deliveries.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import BookingLink, CadenceState, EmailMessage, Lead, Message
from app.models.program_models import (CampaignFamily, OutreachProgram, ProgramConversation,
                                       ProgramConversationTurn, ProgramEmailTouch, ProgramResponse,
                                       ProgramSourceRecord)

GROUPS = ("excluded", "data_review", "not_started", "active", "stopped", "completed", "held")
DAYS_BACK = 7
DAYS_AHEAD = 7
OVERDUE_GRACE = timedelta(minutes=15)
EMAIL_STOPS = ("failed", "unknown", "claimed")


def _local_day(dt: datetime) -> str:
    from zoneinfo import ZoneInfo
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/Chicago")).date().isoformat()


def _iso(dt) -> Optional[str]:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _chunks(ids: List[str], n: int = 500):
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def families(db: Session, prog: OutreachProgram) -> List[Dict]:
    rows = (db.query(CampaignFamily.key, CampaignFamily.name, CampaignFamily.is_active)
            .filter(CampaignFamily.organization_id == prog.organization_id).all())
    counts = dict(db.query(ProgramSourceRecord.campaign_family, func.count(ProgramSourceRecord.id))
                  .filter(ProgramSourceRecord.organization_id == prog.organization_id)
                  .group_by(ProgramSourceRecord.campaign_family).all())
    out = [{"key": k, "name": n, "active": bool(a), "records": counts.get(k, 0)} for k, n, a in rows]
    return sorted([f for f in out if f["records"]], key=lambda f: -f["records"])


def _email_plan(touches: Dict[int, ProgramEmailTouch], max_t: int) -> Dict:
    sent = [n for n, t in touches.items() if t.status == "sent"]
    last_n = max(sent) if sent else 0
    stopped = any(t.status in EMAIL_STOPS for t in touches.values())
    blocked = any(t.status == "blocked" for t in touches.values())
    last_at = touches[last_n].attempted_at if last_n else None
    return {"last_n": last_n, "last_at": last_at, "stopped": stopped, "blocked": blocked,
            "remaining": 0 if stopped else max(0, max_t - last_n)}


def report(db: Session, prog: OutreachProgram, family_key: str, now: Optional[datetime] = None) -> Dict:
    from app.services.programs import campaigns, daily_batch, email_touches, journey, send_window
    from app.services import cadence_service as cs
    now = now or datetime.utcnow()
    org = prog.organization_id
    fam = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == org,
                                          CampaignFamily.key == family_key).first())
    if fam is None:
        raise LookupError("campaign not found")
    own_copy = fam.key in campaigns.OWN_COPY_KEYS
    recs = (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org,
                                                 ProgramSourceRecord.campaign_family == fam.key).all())
    by_lead: Dict[str, list] = {}
    no_lead = {"excluded": 0, "data_review": 0, "other": 0}
    for r in recs:
        if r.lead_id is None:
            hr = (r.hold_reason or "").upper()
            no_lead["excluded" if hr.startswith("EXCLUDED") else "data_review" if r.on_hold else "other"] += 1
            continue
        by_lead.setdefault(r.lead_id, []).append(r)
    ids = list(by_lead)
    leads, states, touches, replied, booked, human, recovery = {}, {}, {}, set(), set(), set(), set()
    from app.models.sms_recovery_models import SmsRecoveryItem
    for chunk in _chunks(ids):
        for l in db.query(Lead).filter(Lead.organization_id == org, Lead.id.in_(chunk)).all():
            leads[l.id] = l
        for s in db.query(CadenceState).filter(CadenceState.lead_id.in_(chunk)).all():
            states[s.lead_id] = s
        for t in db.query(ProgramEmailTouch).filter(ProgramEmailTouch.organization_id == org,
                                                    ProgramEmailTouch.lead_id.in_(chunk)).all():
            touches.setdefault(t.lead_id, {})[t.touch_number] = t
        replied.update(x for (x,) in db.query(ProgramResponse.lead_id).filter(
            ProgramResponse.organization_id == org, ProgramResponse.lead_id.in_(chunk)).all())
        booked.update(x for (x,) in db.query(BookingLink.lead_id).filter(
            BookingLink.lead_id.in_(chunk), BookingLink.status.in_(("booked", "confirmed", "completed"))).all())
        human.update(x for (x,) in db.query(ProgramConversation.lead_id).filter(
            ProgramConversation.organization_id == org, ProgramConversation.lead_id.in_(chunk),
            ProgramConversation.state.in_(("human", "callback_requested"))).all())
        recovery.update(x for (x,) in db.query(SmsRecoveryItem.lead_id).filter(
            SmsRecoveryItem.organization_id == org, SmsRecoveryItem.lead_id.in_(chunk),
            SmsRecoveryItem.closed_at.is_(None)).all())
    contacted = set()
    for chunk in _chunks(ids):
        contacted.update(x for (x,) in db.query(Message.lead_id).filter(Message.lead_id.in_(chunk)).distinct())
        contacted.update(x for (x,) in db.query(EmailMessage.lead_id).filter(
            EmailMessage.lead_id.in_(chunk), EmailMessage.status == "sent").distinct())

    steps = campaigns.sms_steps(db, org)
    sms_total = len(steps)
    email_max = email_touches.max_touches()
    gap_days = email_touches.followup_days()
    groups = {g: 0 for g in GROUPS}
    stopped_why = {"stop_dnc": 0, "replied": 0, "person_has_it": 0, "appointment": 0}
    not_started_why: Dict[str, int] = {}
    sms_remaining = email_remaining = 0
    ahead_sms: Dict[str, int] = {}
    ahead_email: Dict[str, int] = {}
    last_planned: Optional[datetime] = None
    overdue_oldest: Optional[datetime] = None
    overdue_n = 0
    horizon = now + timedelta(days=DAYS_AHEAD)

    def _plan(day_counts, at):
        nonlocal last_planned
        at = send_window.next_open(at)
        last_planned = at if last_planned is None or at > last_planned else last_planned
        if at < horizon:
            d = _local_day(at)
            day_counts[d] = day_counts.get(d, 0) + 1
        return at

    for lid, rows in by_lead.items():
        lead = leads.get(lid)
        approved = any(r.approved_to_send_at for r in rows)
        if lead is None:
            continue
        if not approved:
            hr = " ".join((r.hold_reason or "") for r in rows).upper()
            groups["excluded" if "EXCLUDED" in hr else "data_review" if any(r.on_hold or r.needs_data_review
                                                                             for r in rows) else "not_started"] += 1
            if "EXCLUDED" not in hr and not any(r.on_hold or r.needs_data_review for r in rows):
                not_started_why["Not approved"] = not_started_why.get("Not approved", 0) + 1
            continue
        st = states.get(lid)
        started = any(r.journey_started_at for r in rows) if own_copy else (st is not None or lid in contacted)
        if not started:
            groups["not_started"] += 1
            a = journey._assess(db, prog, lead, rows, touches.get(lid, {}), lid in replied, lid in booked, st) \
                if own_copy else {"reason": "Approved, not contacted yet"}
            why = a.get("reason") or "Not started"
            not_started_why[why] = not_started_why.get(why, 0) + 1
            continue
        if (lead.status or "").lower() == "dnc" or (st is not None and (st.status or "").startswith("stopped_dnc")):
            groups["stopped"] += 1
            stopped_why["stop_dnc"] += 1
            continue
        if lid in booked:
            groups["stopped"] += 1
            stopped_why["appointment"] += 1
            continue
        if lid in human:
            groups["stopped"] += 1
            stopped_why["person_has_it"] += 1
            continue
        if lid in replied:
            groups["stopped"] += 1
            stopped_why["replied"] += 1
            continue
        em = _email_plan(touches.get(lid, {}), email_max)
        email_ok = journey._email_usable(lead) and not email_touches.provider_hold_reason(prog, lead.email) \
            and not em["blocked"]
        e_left = em["remaining"] if email_ok else 0
        s_active = st is not None and st.status == "active"
        if s_active and lid in recovery:
            groups["held"] += 1
            email_remaining += e_left
            continue
        s_left = (sms_total - (st.current_touch_number or 0)) if s_active else 0
        if s_left == 0 and e_left == 0:
            groups["completed"] += 1
            continue
        groups["active"] += 1
        sms_remaining += s_left
        email_remaining += e_left
        if s_active and st.next_touch_due_at is not None:
            if st.next_touch_due_at < now - OVERDUE_GRACE:
                overdue_n += 1
                overdue_oldest = st.next_touch_due_at if overdue_oldest is None or st.next_touch_due_at < overdue_oldest \
                    else overdue_oldest
            at = _plan(ahead_sms, max(st.next_touch_due_at, now))
            base = st.cadence_started_at or at
            for n in range((st.current_touch_number or 0) + 2, sms_total + 1):
                nd = next((s for s in steps if s[0] == n), None)
                if nd is None:
                    break
                gap = cs._touch_gap([{"touch_number": s[0], "day_offset": s[1]} for s in steps], n)
                at = _plan(ahead_sms, max(base + timedelta(days=nd[1]), at + gap))
        if e_left:
            at = (em["last_at"] + timedelta(days=gap_days)) if em["last_at"] else max(
                next((r.scheduled_start_at for r in rows if r.scheduled_start_at), None) or now, now)
            for i in range(e_left):
                at = _plan(ahead_email, max(at, now) if i == 0 else at + timedelta(days=gap_days))
    groups["excluded"] += no_lead["excluded"]
    groups["data_review"] += no_lead["data_review"]
    if no_lead["other"]:
        groups["not_started"] += no_lead["other"]
        not_started_why["No family record yet"] = not_started_why.get("No family record yet", 0) + no_lead["other"]

    # ── what went out, by Central day ─────────────────────────────────────────
    from zoneinfo import ZoneInfo
    local_today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/Chicago")).date()
    days = [(local_today - timedelta(days=i)).isoformat() for i in range(DAYS_BACK - 1, -1, -1)]
    since = datetime.combine(local_today - timedelta(days=DAYS_BACK - 1), datetime.min.time()) \
        .replace(tzinfo=ZoneInfo("America/Chicago")).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    daily = {d: {k: 0 for k in ("sms_attempted", "sms_delivered", "sms_failed", "email_sent", "email_bounced",
                                "email_blocked", "replies", "handoffs", "appointments")} for d in days}

    def _bump(key, dt):
        if dt is None:
            return
        d = _local_day(dt)
        if d in daily:
            daily[d][key] += 1
    for chunk in _chunks(ids):
        for sent_at, ds in db.query(Message.sent_at, Message.delivery_status).filter(
                Message.lead_id.in_(chunk), Message.send_source == "cadence", Message.sent_at >= since).all():
            _bump("sms_attempted", sent_at)
            s = (ds or "").lower()
            if s == "delivered":
                _bump("sms_delivered", sent_at)
            elif s in ("undelivered", "failed"):
                _bump("sms_failed", sent_at)
        for at, status in db.query(ProgramEmailTouch.attempted_at, ProgramEmailTouch.status).filter(
                ProgramEmailTouch.organization_id == org, ProgramEmailTouch.campaign_family == fam.key,
                ProgramEmailTouch.lead_id.in_(chunk), ProgramEmailTouch.attempted_at >= since).all():
            if status == "sent":
                _bump("email_sent", at)
            elif status == "blocked":
                _bump("email_blocked", at)
        for (bat,) in db.query(EmailMessage.bounced_at).filter(
                EmailMessage.lead_id.in_(chunk), EmailMessage.bounced_at >= since).all():
            _bump("email_bounced", bat)
        for (rat,) in db.query(ProgramResponse.received_at).filter(
                ProgramResponse.organization_id == org, ProgramResponse.lead_id.in_(chunk),
                ProgramResponse.received_at >= since).all():
            _bump("replies", rat)
        for (tat,) in db.query(ProgramConversationTurn.created_at).filter(
                ProgramConversationTurn.organization_id == org, ProgramConversationTurn.lead_id.in_(chunk),
                ProgramConversationTurn.action.in_(("handoff", "callback")),
                ProgramConversationTurn.created_at >= since).all():
            _bump("handoffs", tat)
        for (bat,) in db.query(func.coalesce(BookingLink.confirmed_at, BookingLink.created_at)).filter(
                BookingLink.lead_id.in_(chunk), BookingLink.status.in_(("booked", "confirmed", "completed")),
                func.coalesce(BookingLink.confirmed_at, BookingLink.created_at) >= since).all():
            _bump("appointments", bat)
    week = {k: sum(daily[d][k] for d in days) for k in daily[days[0]]}

    # ── the senders ───────────────────────────────────────────────────────────
    from app.models.job_models import JobRun
    sched = {}
    for name in ("cadence_loop", "program_sla_loop"):
        last = (db.query(JobRun).filter(JobRun.job_name == name).order_by(JobRun.started_at.desc()).first())
        bad = (db.query(func.count(JobRun.id)).filter(JobRun.job_name == name,
                                                    JobRun.started_at >= now - timedelta(hours=24),
                                                    JobRun.status == "error").scalar() or 0)
        age = (now - last.started_at).total_seconds() / 60 if last and last.started_at else None
        sched[name] = {"last_run": _iso(last.started_at) if last else None,
                       "last_status": getattr(last, "status", None) if last else None,
                       "minutes_ago": round(age, 1) if age is not None else None, "failed_24h": int(bad),
                       "healthy": bool(last) and age is not None and age < 20
                                  and getattr(last, "status", None) != "error"}
    ahead_days = [(local_today + timedelta(days=i)).isoformat() for i in range(DAYS_AHEAD)]
    return {
        "campaign": {"key": fam.key, "name": fam.name, "active": bool(fam.is_active), "own_journey": own_copy},
        "generated_at": _iso(now),
        "roster": {"source_records": len(recs), "families": len(by_lead) + sum(no_lead.values()),
                   "approved_records": sum(1 for r in recs if r.approved_to_send_at),
                   "approved_families": sum(1 for rows in by_lead.values() if any(r.approved_to_send_at for r in rows)),
                   **groups},
        "stopped_why": stopped_why,
        "not_started_why": not_started_why,
        "remaining": {"sms_touches": sms_remaining, "email_touches": email_remaining,
                      "expected_last_touch": _iso(last_planned)},
        "overdue": {"count": overdue_n, "oldest_due": _iso(overdue_oldest)},
        "daily": [{"day": d, **daily[d]} for d in days],
        "week": week,
        "next_7_days": [{"day": d, "sms": ahead_sms.get(d, 0), "email": ahead_email.get(d, 0)} for d in ahead_days],
        "schedule": {"sms_steps": [{"touch": s[0], "day": s[1]} for s in steps],
                     "email_touches": email_max, "email_every_days": gap_days},
        "window": send_window.describe(),
        "daily_batch": dict(daily_batch.status(prog), days_label=send_window.describe(daily_batch.days_of(prog))["days_label"]),
        "scheduler": sched,
    }
