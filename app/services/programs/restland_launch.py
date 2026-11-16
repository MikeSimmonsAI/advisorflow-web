"""RESTLAND FILE REVIEW LAUNCH (Issue #507, owner directive Nov 15 2026).

Run once from the predeploy step:

    WORKSPACE_RESET="<org id>:launch:<run key>"
    WORKSPACE_BOOKING_USER_ID=<Mike's Restland user>
    WORKSPACE_CALENDAR_USER_ID=<the login connected to info.evosyspro@gmail.com>
    WORKSPACE_LAUNCH_AT="2026-11-16T08:30"          (Central; the first slot)

What it does, recorded in one WorkspaceResetRun (mode "launch"), idempotent
per run key:
  1. Booking policy: the text slot schedule (8:30 AM, new family every 8 min,
     follow-up 2 min after), the shared Restland calendar account, and the
     approved appointment messages + survey switched on.
  2. The File Review campaign's copy (5 texts, 4 emails) replaced with the
     Nov 15 wording; the campaign is no longer a draft.
  3. The audience recomputed from production (workspace_reset.plan): every
     ready family is staged in the File Review round (the 25 already staged
     are kept, never duplicated), its program record put on the campaign and
     approved under the booking user's name.
  4. The campaign and the workspace texts switch on; the journey started at
     the first slot - each texting family in its own new-family slot, no daily
     cap. Nothing is sent here: the runners send at the scheduled times
     through every existing check.
`readback()` reports the switches, counts and the first queued times.
"""
import json
import logging
from collections import Counter
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models.models import CadenceState, Lead, Organization, User
from app.models.program_models import CampaignFamily, OutreachProgram, ProgramSourceRecord, WorkspaceSwitch
from app.models.reset_models import CampaignCycleMember, WorkspaceResetRun

log = logging.getLogger(__name__)
MODE = "launch"
TZ = "America/Chicago"
CYCLE_KEY = "ffr-batch-1"
CAMPAIGN_NAME = "Restland — Family File Review"
OUTREACH = {"slots": True, "open": "08:30", "close": "18:00", "new_every_minutes": 8,
            "followup_offset_minutes": 2}


class LaunchRefused(ValueError):
    pass


def _first_slot_utc(start_local: str) -> datetime:
    from zoneinfo import ZoneInfo
    local = datetime.strptime(start_local, "%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo(TZ))
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _ensure_tables(db: Session) -> None:
    from app.models.reset_models import OutreachTextSlot
    OutreachTextSlot.__table__.create(bind=db.get_bind(), checkfirst=True)


def launch(db: Session, org: Organization, run_key: str, *, booking_user_id: Optional[str],
           calendar_user_id: Optional[str], start_local: str, now: Optional[datetime] = None) -> Dict:
    from app.services import sms_programs
    from app.services.programs import campaigns as C
    from app.services.programs import journey, outreach_slots, rotation, send_approval
    from app.services.programs import restland_ffr as F
    from app.services.programs import workspace_reset as WR
    now = now or datetime.utcnow()
    if org is None:
        raise LaunchRefused("Organization not found.")
    if sms_programs.is_sci_org(db, org.id):
        raise LaunchRefused("Refused: this is the SCI organization.")
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org.id).first()
    if prog is None:
        raise LaunchRefused("This workspace has no program record.")
    done = db.query(WorkspaceResetRun).filter_by(organization_id=org.id, run_key=run_key, mode=MODE).first()
    if done is not None and done.status == "applied":
        return {"run_id": done.id, "status": done.status, "already": True,
                "summary": json.loads(done.summary_json or "{}")}
    user = db.query(User).filter(User.id == (booking_user_id or prog.booking_user_id),
                                 User.organization_id == org.id, User.is_active.is_(True)).first()
    if user is None:
        raise LaunchRefused("The booking user is not an active user of this workspace.")
    cal = db.query(User).filter(User.id == calendar_user_id, User.is_active.is_(True)).first() \
        if calendar_user_id else None
    if calendar_user_id and (cal is None or not cal.google_oauth_refresh_token_encrypted):
        raise LaunchRefused("The calendar account has no Google connection.")
    first = _first_slot_utc(start_local)
    _ensure_tables(db)
    run = done or WorkspaceResetRun(organization_id=org.id, run_key=run_key, mode=MODE)
    db.add(run)
    db.flush()
    summary: Dict = {"start_local": start_local}

    # 1. Policy: slots, shared calendar, appointment messages + survey on.
    pol = json.loads(prog.booking_policy_json or "{}")
    before = {k: pol.get(k) for k in ("outreach", "calendar_user_id", "notifications")}
    pol["outreach"] = dict(OUTREACH)
    if cal is not None:
        pol["calendar_user_id"] = cal.id
    notes = dict(pol.get("notifications") or {})
    notes.update({"enabled": True, "survey_enabled": True, "reminder_minutes": [1440, 120],
                  "survey_delay_hours": 24, "window": notes.get("window") or ["09:00", "18:00"]})
    notes.setdefault("active_since", now.replace(microsecond=0).isoformat() + "Z")
    pol["notifications"] = notes
    prog.booking_policy_json = json.dumps(pol)
    prog.booking_user_id = user.id
    outreach_slots.clear_cache()
    summary["policy_before"] = before
    db.flush()
    try:
        from app.services.programs.workspace_configure import calendar_probe
        summary["calendar"] = calendar_probe(user)         # read-only: summary, time zone
    except Exception as e:                                # noqa: BLE001
        summary["calendar"] = {"error": str(e)[:200]}

    # 2. Copy (the campaign's own words), no longer a draft.
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key=F.CAMPAIGN_KEY).first()
    if fam is None:
        raise LaunchRefused("The Family File Review campaign does not exist.")
    fam.name = CAMPAIGN_NAME
    fam.sms_template = F.SMS[1]
    fam.email_subject_template, fam.email_body_template = F.EMAIL[1]
    C.set_touch_copy(fam, sms={n: t for n, t in F.SMS.items() if n > 1},
                     email={n: {"subject": s, "body": b} for n, (s, b) in F.EMAIL.items() if n > 1},
                     editor="Restland launch (#507, Nov 15)")
    try:
        d = json.loads(fam.touch_copy_json or "{}")
        d["draft"] = False
        fam.touch_copy_json = json.dumps(d)
    except ValueError:
        pass
    db.flush()

    # 3. Audience from production, staged, records on the campaign, approved.
    p = WR.plan(db, org)
    segs = Counter(m["segment"] for m in p["members"].values())
    ready_ids = [lid for lid, m in p["members"].items() if m["segment"] == "ready"]
    summary["audience"] = {"source": "pre-need families in this workspace with a usable phone "
                                     "(workspace_reset.plan)", "considered": len(p["members"]),
                           "segments": dict(segs), "ready": len(ready_ids)}
    leads = {l.id: l for l in db.query(Lead).filter(Lead.organization_id == org.id,
                                                     Lead.id.in_(ready_ids or ["-"])).all()}
    staged = rotation.stage(db, org.id, F.CAMPAIGN_KEY, CYCLE_KEY, [leads[i] for i in ready_ids if i in leads],
                            run_id=run.id)
    staged_ids = {m.lead_id for m in staged}
    summary["staged_in_round"] = len(staged_ids)
    summary["not_staged_conflict"] = len(set(ready_ids) - staged_ids)
    fam.is_active = True
    db.flush()
    approved, skipped = 0, Counter()
    modes = Counter()
    # Records that were already on the campaign before this run keep it.
    keep_ids = {r.id for r in db.query(ProgramSourceRecord).filter(
        ProgramSourceRecord.organization_id == org.id, ProgramSourceRecord.campaign_family == F.CAMPAIGN_KEY).all()}
    for lid in ready_ids:
        lead = leads.get(lid)
        if lead is None or lid not in staged_ids:
            continue
        recs = (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org.id,
                                                     ProgramSourceRecord.lead_id == lid).all())
        if not recs:
            skipped["no program record"] += 1
            continue
        if any(r.campaign_family and r.campaign_family != F.CAMPAIGN_KEY for r in recs):
            skipped["record already in another campaign"] += 1
            continue
        # The record goes on the campaign FIRST, so the send checks below judge
        # it as a File Review family (an unassigned record reads "No campaign").
        for r in recs:
            r.campaign_family = F.CAMPAIGN_KEY
        db.flush()
        b = send_approval.blocker(db, lead)
        why = b or rotation.approval_refusal(db, org.id, lead, F.CAMPAIGN_KEY)
        mode = "text_email" if send_approval.textable(lead) else "email_only"
        if not why and mode == "email_only" and not (lead.email or "").strip():
            why = "no textable phone and no email"
        if why:
            skipped[why.split(":")[0][:60]] += 1
            for r in recs:
                if r.approved_to_send_at is None:
                    r.campaign_family = None if r.campaign_family == F.CAMPAIGN_KEY and r.id not in keep_ids \
                        else r.campaign_family
            continue
        for r in recs:
            r.campaign_family = F.CAMPAIGN_KEY
            if r.approved_to_send_at is None:
                r.approved_to_send_at, r.approved_to_send_by = now, user.id
            r.send_mode = mode
        if not lead.assigned_to_id:
            lead.assigned_to_id = user.id
        modes[mode] += 1
        approved += 1
    prog.require_send_approval = True
    db.flush()
    summary["approved"] = approved
    summary["approved_by_mode"] = dict(modes)
    summary["not_approved"] = dict(skipped)

    # 4. Texts on for this workspace; the journey started at the first slot.
    sw = db.query(WorkspaceSwitch).filter(WorkspaceSwitch.organization_id == org.id).first()
    if sw is None:
        sw = WorkspaceSwitch(organization_id=org.id)
        db.add(sw)
    sw.texts_on, sw.updated_by, sw.updated_at = True, user.id, now
    run.summary_json = json.dumps(summary, default=str)
    db.commit()
    res = journey.start(db, prog, fam, user, start_at=first, spacing_minutes=8, now=now)
    summary["journey"] = {k: res.get(k) for k in ("started", "texting", "email_only", "first_at", "last_at",
                                                  "by_day", "already_started_by_another_click")}
    summary["readback"] = readback(db, org)
    run.status, run.finished_at = "applied", datetime.utcnow()
    run.summary_json = json.dumps(summary, default=str)
    db.commit()
    return {"run_id": run.id, "status": run.status, "already": False, "summary": summary}


def readback(db: Session, org: Organization, now: Optional[datetime] = None) -> Dict:
    """What production really holds now: switches, counts, queued times."""
    from app.services.programs import outreach_slots
    from app.services.programs import restland_ffr as F
    now = now or datetime.utcnow()
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org.id).first()
    pol = json.loads(prog.booking_policy_json or "{}") if prog else {}
    fams = {f.key: bool(f.is_active) for f in db.query(CampaignFamily).filter_by(organization_id=org.id).all()}
    sw = db.query(WorkspaceSwitch).filter(WorkspaceSwitch.organization_id == org.id).first()
    recs = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org.id,
                                                ProgramSourceRecord.campaign_family == F.CAMPAIGN_KEY)
    approved = {r.lead_id for r in recs.filter(ProgramSourceRecord.approved_to_send_at.isnot(None)).all()}
    started = {r.lead_id: r for r in recs.filter(ProgramSourceRecord.journey_started_at.isnot(None)).all()}
    modes = Counter((r.send_mode or "text_email") for r in started.values())
    states = (db.query(CadenceState).join(Lead, Lead.id == CadenceState.lead_id)
              .filter(Lead.organization_id == org.id, CadenceState.status == "active").count())
    starts = sorted(r.scheduled_start_at for r in started.values() if r.scheduled_start_at)
    cyc = Counter(s for (s,) in db.query(CampaignCycleMember.status)
                  .filter(CampaignCycleMember.organization_id == org.id).all())
    from zoneinfo import ZoneInfo

    def loc(t):
        return t.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(TZ)).strftime("%a %b %-d %-I:%M %p") \
            if t else None
    return {
        "campaigns_on": fams, "texts_switch_on": bool(sw and sw.texts_on),
        "daily_batch_count": int(prog.daily_batch_count or 0) if prog else None,
        "outreach_slots": pol.get("outreach"), "calendar_user_set": bool(pol.get("calendar_user_id")),
        "notifications": pol.get("notifications"),
        "approved_families": len(approved), "journeys_started": len(started),
        "started_by_mode": dict(modes), "active_text_journeys": states,
        "round_members": dict(cyc),
        "first_start": loc(starts[0]) if starts else None, "last_start": loc(starts[-1]) if starts else None,
        "queue": outreach_slots.queue_view(db, org.id, now=now, limit=12),
    }


def pause(db: Session, org: Organization, run_key: str, *, now: Optional[datetime] = None) -> Dict:
    """STOP ALL RESTLAND OUTREACH (Nov 15, 8:48 PM - owner: "pause it right
    away" ahead of the workspace reset). Recorded as mode "pause"; safe to
    re-run. Every campaign off, the workspace texts switch off, every active
    text journey paused, every reserved text slot given back, pending email
    touches can no longer go (their campaign is off). Opt-out handling,
    inbound replies and appointment messages are untouched. Sends nothing."""
    from app.models.reset_models import OutreachTextSlot
    from app.services.programs import outreach_slots
    from app.services import sms_programs
    now = now or datetime.utcnow()
    if org is None:
        raise LaunchRefused("Organization not found.")
    if sms_programs.is_sci_org(db, org.id):
        raise LaunchRefused("Refused: this is the SCI organization.")
    run = db.query(WorkspaceResetRun).filter_by(organization_id=org.id, run_key=run_key, mode="pause").first() \
        or WorkspaceResetRun(organization_id=org.id, run_key=run_key, mode="pause")
    db.add(run)
    db.flush()
    out: Dict = {}
    fams = db.query(CampaignFamily).filter(CampaignFamily.organization_id == org.id).all()
    out["campaigns_turned_off"] = [f.key for f in fams if f.is_active]
    for f in fams:
        f.is_active = False
    sw = db.query(WorkspaceSwitch).filter(WorkspaceSwitch.organization_id == org.id).first()
    if sw is None:
        sw = WorkspaceSwitch(organization_id=org.id)
        db.add(sw)
    out["texts_switch_was_on"] = bool(sw.texts_on)
    sw.texts_on, sw.updated_at = False, now
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org.id).first()
    if prog is not None:
        out["daily_batch_was"] = int(prog.daily_batch_count or 0)
        prog.daily_batch_count = 0
    lead_ids = [i for (i,) in db.query(Lead.id).filter(Lead.organization_id == org.id).all()]
    paused = 0
    for i in range(0, len(lead_ids), 500):
        chunk = lead_ids[i:i + 500]
        paused += (db.query(CadenceState).filter(CadenceState.lead_id.in_(chunk), CadenceState.status == "active")
                   .update({"status": "paused"}, synchronize_session=False))
    out["text_journeys_paused"] = paused
    out["slots_released"] = (db.query(OutreachTextSlot).filter(OutreachTextSlot.organization_id == org.id,
                                                               OutreachTextSlot.status == "reserved")
                             .delete(synchronize_session=False))
    outreach_slots.clear_cache()
    run.status, run.finished_at = "applied", datetime.utcnow()
    run.summary_json = json.dumps(out, default=str)
    db.commit()
    out["readback"] = readback(db, org, now=now)
    return out


# ── NEED TYPE FROM THE FILE (Nov 16 launch of the Historical Family Review) ──
# The Historical Family Review file (batch RES-20261116-001) carries each
# family's need in its own "Need Type" column and the reviewer's order in
# "Review Priority"; the import kept both on the program record but did not
# set the family's tier, so the File Review audience (pre-need families only,
# workspace_reset.plan) found nobody. classify() copies the file's own answer:
#   "Pre Need" -> tier pre_need, "At Need" -> tier at_need (never texted by
#   File Review); a family the file marks "3 · Confirm location and fit" is
#   held for a person to confirm (its program record on hold, with the reason).
# Only families with no tier yet are touched; every change is recorded with
# its before value (WorkspaceResetItem). Restland only; sends nothing.
NEED_TIER = {"pre need": "pre_need", "at need": "at_need"}
CONFIRM_PRIORITY = "3"
CONFIRM_HOLD = "Confirm location and fit before outreach (file Review Priority 3)"


def classify(db: Session, org: Organization, run_key: str) -> Dict:
    from app.models.reset_models import WorkspaceResetItem
    from app.services import sms_programs
    if org is None:
        raise LaunchRefused("Organization not found.")
    if sms_programs.is_sci_org(db, org.id):
        raise LaunchRefused("Refused: this is the SCI organization.")
    run = db.query(WorkspaceResetRun).filter_by(organization_id=org.id, run_key=run_key, mode="classify").first()
    if run is not None and run.status == "applied":
        return {"run_id": run.id, "already": True, "summary": json.loads(run.summary_json or "{}")}
    run = run or WorkspaceResetRun(organization_id=org.id, run_key=run_key, mode="classify")
    db.add(run)
    db.flush()
    tiers, held, unknown = Counter(), 0, Counter()
    leads = {l.id: l for l in db.query(Lead).filter(Lead.organization_id == org.id).all()}
    recs = (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org.id,
                                                  ProgramSourceRecord.lead_id.isnot(None)).all())
    for r in recs:
        lead = leads.get(r.lead_id)
        if lead is None:
            continue
        try:
            raw = json.loads(r.raw_json or "{}")
        except ValueError:
            raw = {}
        need = str(raw.get("Need Type") or "").strip().lower()
        tier = NEED_TIER.get(need)
        if not lead.tier:
            if tier is None:
                unknown[need or "(blank)"] += 1
            else:
                db.add(WorkspaceResetItem(run_id=run.id, organization_id=org.id, entity="lead", entity_id=lead.id,
                                          action="set_tier", before_json=json.dumps({"tier": lead.tier}),
                                          after_json=json.dumps({"tier": tier}), reason="File Need Type: %s" % need))
                lead.tier = tier
                tiers[tier] += 1
        prio = str(raw.get("Review Priority") or "").strip()
        if (lead.tier == "pre_need" and prio.startswith(CONFIRM_PRIORITY) and not r.on_hold
                and r.approved_to_send_at is None):
            db.add(WorkspaceResetItem(run_id=run.id, organization_id=org.id, entity="program_source_record",
                                      entity_id=r.id, action="hold",
                                      before_json=json.dumps({"on_hold": r.on_hold, "hold_reason": r.hold_reason}),
                                      after_json=json.dumps({"on_hold": True, "hold_reason": CONFIRM_HOLD}),
                                      reason=CONFIRM_HOLD))
            r.on_hold, r.hold_reason = True, CONFIRM_HOLD
            held += 1
    summary = {"tier_set": dict(tiers), "held_confirm_location": held, "need_type_not_recognized": dict(unknown),
               "tiers_now": dict(Counter(l.tier or "(none)" for l in leads.values()))}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return {"run_id": run.id, "already": False, "summary": summary}
