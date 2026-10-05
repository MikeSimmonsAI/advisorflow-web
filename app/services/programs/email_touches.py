"""Location outreach programs: automated campaign EMAIL touches.

The SMS cadence engine sends texts only; an email-only family was skipped.
This runner sends a program's campaign emails, location by location:

    touch 1  first-touch email  (family's first_touch_email_mode: none | hosted | attached)
    touch 2  follow-up email    (followup_email_mode), FOLLOWUP_DAYS after touch 1

WHO GETS A TOUCH - every one of these must hold, every pass:
  * PROGRAM_EMAIL_TOUCHES_SENDING is on for the deployment (default OFF), and
  * the contact's campaign family is switched ON (default OFF), and
  * the contact is a live lead promoted from the program's source records, and
  * it has an email address and no email opt-out (allow_email is not False), and
  * nobody at that contact has replied on any channel (a reply ends the
    sequence - a person takes it from there), and
  * no Location / Data / Duplicate Review is open (identity.send_refusal), and
  * it is inside sending hours (9:00-18:00 in the organization's timezone),
  * the program is under its daily cap (PROGRAM_EMAIL_TOUCH_DAILY_CAP,
    default 50 per local day; at most PROGRAM_EMAIL_TOUCH_BATCH per pass), and
  * if the campaign's email mode carries a flyer (hosted / attached), an
    approved flyer is active for that location - otherwise it is held.
Then send_email_to_lead runs the same compliance gate every email passes
(DNC, opt-out, bad address, demo boundary, AI-paused holds).

NEVER TWICE. A touch is claimed by inserting its ProgramEmailTouch row
(unique on lead + touch) and committing BEFORE the provider is called.
A provider rejection is recorded as failed, an error after the provider
call as UNKNOWN (it may have gone out) - neither is retried automatically; a
person looks. A refusal (DNC, opt-out, bad address, AI paused, plan hold) is
recorded as blocked with the reason and re-tried after 24 hours, so a
temporary hold never uses up the family's touch. A staff-set lead status
(booked, hot, not interested, ...) holds the sequence.

dry_run=True returns the plan and touches nothing - the screen's "what would
go out" view.
"""
import base64
import html
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import Lead, Organization, User
from app.models.program_models import (
    CampaignFamily, LocationProfile, OutreachProgram, ProgramAsset, ProgramEmailTouch,
    ProgramResponse, ProgramSourceRecord,
)
from app.services.programs import identity

log = logging.getLogger(__name__)

SENDING_ENV = "PROGRAM_EMAIL_TOUCHES_SENDING"
DEFAULT_FOLLOWUP_DAYS = 4
DEFAULT_BATCH = 25
SEND_HOURS = (9, 18)          # local, [start, end)


PASS_INTERVAL = timedelta(minutes=10)
_last_pass: Dict[str, datetime] = {}


def pass_due(now: Optional[datetime] = None) -> bool:
    """The SLA loop ticks every 2 minutes; campaign email needs no such pace."""
    now = now or datetime.utcnow()
    last = _last_pass.get("run")
    if last is not None and now - last < PASS_INTERVAL:
        return False
    _last_pass["run"] = now
    return True


def sending_enabled() -> bool:
    raw = os.environ.get(SENDING_ENV)
    return bool(raw) and raw.strip().lower() in ("1", "true", "yes", "on")


def followup_days() -> int:
    try:
        return max(1, int(os.environ.get("PROGRAM_EMAIL_FOLLOWUP_DAYS") or DEFAULT_FOLLOWUP_DAYS))
    except ValueError:
        return DEFAULT_FOLLOWUP_DAYS


def batch_limit() -> int:
    try:
        return max(1, min(500, int(os.environ.get("PROGRAM_EMAIL_TOUCH_BATCH") or DEFAULT_BATCH)))
    except ValueError:
        return DEFAULT_BATCH


DEFAULT_DAILY_CAP = 50


def daily_cap() -> int:
    """Most automated campaign emails one program may send per local day.
    Switching a campaign on then TRICKLES - it never empties a list in one
    afternoon, and a problem shows up after 50 families, not 500."""
    try:
        return max(0, int(os.environ.get("PROGRAM_EMAIL_TOUCH_DAILY_CAP") or DEFAULT_DAILY_CAP))
    except ValueError:
        return DEFAULT_DAILY_CAP


def _org_tz(db: Session, org_id: str):
    from zoneinfo import ZoneInfo
    org = db.query(Organization).filter(Organization.id == org_id).first()
    try:
        return ZoneInfo((getattr(org, "timezone", None) if org else None) or "America/Chicago")
    except Exception:                                    # noqa: BLE001
        return ZoneInfo("America/Chicago")


def used_today(db: Session, org_id: str, now: Optional[datetime] = None) -> int:
    """Touches attempted since local midnight (blocked ones never reached a provider)."""
    from zoneinfo import ZoneInfo
    tz = _org_tz(db, org_id)
    local = (now or datetime.utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return (db.query(ProgramEmailTouch.id)
            .filter(ProgramEmailTouch.organization_id == org_id,
                    ProgramEmailTouch.attempted_at >= midnight,
                    ProgramEmailTouch.status.in_(("claimed", "sent", "failed", "unknown"))).count())


def in_sending_hours(db: Session, org_id: str, now: Optional[datetime] = None) -> bool:
    from zoneinfo import ZoneInfo
    org = db.query(Organization).filter(Organization.id == org_id).first()
    tzname = (getattr(org, "timezone", None) or "America/Chicago") if org else "America/Chicago"
    try:
        tz = ZoneInfo(tzname)
    except Exception:                                    # noqa: BLE001
        tz = ZoneInfo("America/Chicago")
    utc = (now or datetime.utcnow()).replace(tzinfo=ZoneInfo("UTC"))
    hour = utc.astimezone(tz).hour
    return SEND_HOURS[0] <= hour < SEND_HOURS[1]


# ── rendering (shared with the campaign preview) ────────────────────────────

def public_asset_url(token: str) -> str:
    """Absolute hosted link for a family. PROGRAM_ASSET_BASE_URL (e.g. the
    customer-facing app domain) wins; otherwise the backend's own public origin."""
    from app.services.twilio_callbacks import public_api_base
    base = (os.environ.get("PROGRAM_ASSET_BASE_URL") or "").strip().rstrip("/") or public_api_base()
    return "%s/program-assets/%s" % (base, token)


def flyer_for(db: Session, org_id: str, fam: CampaignFamily, location_id: str) -> Optional[ProgramAsset]:
    """The active flyer for this family/category, location-specific first.
    The file bytes are NOT loaded here (deferred) - only when attaching."""
    from sqlalchemy.orm import defer
    q = db.query(ProgramAsset).options(defer(ProgramAsset.data)).filter(ProgramAsset.organization_id == org_id,
                                      ProgramAsset.kind == "flyer", ProgramAsset.is_active.is_(True))
    cands = q.filter((ProgramAsset.campaign_family == fam.key) |
                     (ProgramAsset.category == fam.asset_category)).all()
    cands.sort(key=lambda a: (a.location_id != location_id, a.location_id is not None and a.location_id != location_id,
                              a.campaign_family != fam.key, -a.version))
    for a in cands:
        if a.location_id in (None, location_id):
            return a
    return None


def render_touch(db: Session, prog: OutreachProgram, fam: CampaignFamily, prof: LocationProfile,
                 first_name: Optional[str], touch: str = "first", flyer_cache: Optional[Dict] = None) -> Dict:
    """Subject, plain body, HTML body and attachment for one family/location/touch."""
    mode = fam.first_touch_email_mode if touch == "first" else fam.followup_email_mode
    key = (fam.key, prof.location_id)
    if flyer_cache is not None and key in flyer_cache:
        found = flyer_cache[key]
    else:
        found = flyer_for(db, prog.organization_id, fam, prof.location_id)
        if flyer_cache is not None:
            flyer_cache[key] = found
    flyer = found if mode in ("hosted", "attached") else None
    flyer_url = public_asset_url(flyer.public_token) if (flyer is not None and mode == "hosted") else ""
    flyer_link = "View your %s: %s" % (flyer.title, flyer_url) if flyer_url else ""
    attachment = None
    if flyer is not None and mode == "attached":
        attachment = {"filename": flyer.filename or "%s.pdf" % flyer.title, "asset_id": flyer.id}
    name = (first_name or "").strip().title() or "there"
    fields = {
        "first_name": name, "location_name": prof.official_name,
        "primary_contact_name": prog.primary_contact_name or "",
        "location_website": prof.website or "", "flyer_link": flyer_link,
    }
    subject = identity.render(fam.email_subject_template, fields)
    body = identity.render(fam.email_body_template,
                           dict(fields, reply_instructions=prog.reply_instructions_email or ""))
    return {"email_mode": mode, "flyer": flyer, "flyer_available": found is not None, "flyer_url": flyer_url, "attachment": attachment,
            "subject": subject, "body": body, "body_html": text_to_html(body, flyer_url),
            "fields": fields}


def text_to_html(text: str, link_url: str = "") -> str:
    """Plain template text -> safe HTML paragraphs; the flyer URL becomes a link."""
    out = []
    for para in (text or "").split("\n\n"):
        p = html.escape(para.strip()).replace("\n", "<br>")
        if not p:
            continue
        if link_url:
            esc = html.escape(link_url)
            p = p.replace(esc, '<a href="%s">%s</a>' % (esc, esc))
        out.append("<p>%s</p>" % p)
    return "\n".join(out)


# ── eligibility ─────────────────────────────────────────────────────────────

# Lead statuses at which an automated campaign email may still go out. Anything
# else (booked, hot, not_interested, dead, closed, ...) means a PERSON has set
# where this family stands, and an automated email on top of that is wrong.
OUTREACH_STATUSES = {"new", "sent", "contacted", "no_response", ""}
BLOCKED_RETRY_AFTER = timedelta(hours=24)   # a refused touch is re-tried, not burned
STALE_CLAIM_AFTER = timedelta(hours=1)      # a claim never resolved -> outcome unknown


def _sender(db: Session, prog: OutreachProgram, lead: Lead) -> Optional[User]:
    for uid in (prog.primary_contact_user_id, lead.assigned_to_id):
        if uid:
            u = db.query(User).filter(User.id == uid, User.is_active.is_(True),
                                      User.organization_id == prog.organization_id).first()
            if u is not None:
                return u
    from app.services.programs.responses import _org_admin_users
    admins = [u for u in _org_admin_users(db, prog.organization_id)
              if u.organization_id == prog.organization_id]
    return admins[0] if admins else None


def _skip_reason(db: Session, lead: Lead, replied: bool) -> Optional[str]:
    if not (lead.email or "").strip():
        return "no email address"
    if lead.allow_email is False:
        return "email opt-out of record"
    status = (lead.status or "").strip().lower()
    if status == "dnc":
        return "do not contact"
    if status not in OUTREACH_STATUSES:
        return "lead status is '%s' - a person has it" % lead.status
    if replied:
        return "replied - a person has it"
    return identity.send_refusal(db, lead, "cadence")


def _due_touch(touches: Dict[int, ProgramEmailTouch], now: datetime) -> Optional[int]:
    """1, 2, or None. A blocked touch is retried after BLOCKED_RETRY_AFTER."""
    t1, t2 = touches.get(1), touches.get(2)
    if t1 is None:
        return 1
    if t1.status == "blocked":
        return 1 if (t1.attempted_at is None or now - t1.attempted_at >= BLOCKED_RETRY_AFTER) else None
    if t1.status != "sent" or not t1.attempted_at:
        return None                              # failed / unknown / claimed: a person looks
    if t2 is None:
        return 2 if now - t1.attempted_at >= timedelta(days=followup_days()) else None
    if t2.status == "blocked":
        return 2 if (t2.attempted_at is None or now - t2.attempted_at >= BLOCKED_RETRY_AFTER) else None
    return None


def plan(db: Session, prog: OutreachProgram, now: Optional[datetime] = None,
         with_skip: bool = True, mark_stale: bool = False) -> List[Dict]:
    """Every contact due a touch right now, with the touch and why/why not.

    Bulk-loaded: one query each for records, leads, touches and replies; the
    per-contact review check runs only for contacts actually due a touch."""
    now = now or datetime.utcnow()
    org_id = prog.organization_id
    fams = {f.key: f for f in db.query(CampaignFamily).filter(
        CampaignFamily.organization_id == org_id, CampaignFamily.is_active.is_(True)).all()}
    if not fams:
        return []
    recs = (db.query(ProgramSourceRecord)
            .filter(ProgramSourceRecord.organization_id == org_id,
                    ProgramSourceRecord.lead_id.isnot(None))
            .order_by(ProgramSourceRecord.row_number, ProgramSourceRecord.id).all())
    by_lead: Dict[str, List[ProgramSourceRecord]] = {}
    for r in recs:
        by_lead.setdefault(r.lead_id, []).append(r)
    if not by_lead:
        return []
    ids = list(by_lead)
    leads, touches, replied = {}, {}, set()
    for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
        for l in db.query(Lead).filter(Lead.organization_id == org_id, Lead.id.in_(chunk)).all():
            leads[l.id] = l
        for t in db.query(ProgramEmailTouch).filter(ProgramEmailTouch.organization_id == org_id,
                                                    ProgramEmailTouch.lead_id.in_(chunk)).all():
            touches.setdefault(t.lead_id, {})[t.touch_number] = t
        replied.update(x for (x,) in db.query(ProgramResponse.lead_id).filter(
            ProgramResponse.organization_id == org_id, ProgramResponse.lead_id.in_(chunk)).all())
    out = []
    for lead_id, rows in by_lead.items():
        lead = leads.get(lead_id)
        if lead is None:
            continue
        master = next((r for r in rows if r.contact_master_key and r.source_lead_id == r.contact_master_key),
                      rows[0])
        fam = fams.get(master.campaign_family)
        if fam is None:
            continue
        mine = touches.get(lead_id, {})
        for t in (mine.values() if mark_stale else ()):   # surface claims that never resolved
            if t.status == "claimed" and t.attempted_at and now - t.attempted_at >= STALE_CLAIM_AFTER:
                t.status = "unknown"
                t.reason = "the runner stopped mid-send; check the provider log before resending"
        touch = _due_touch(mine, now)
        if touch is None:
            continue
        out.append({"lead": lead, "record": master, "family": fam, "touch": touch,
                    "existing": mine.get(touch), "replied": lead_id in replied,
                    "skip": _skip_reason(db, lead, lead_id in replied) if with_skip else None})
    return out


# ── the runner ──────────────────────────────────────────────────────────────

def _claim(db: Session, prog: OutreachProgram, item: Dict, prof: LocationProfile, r: Dict):
    """Insert (or re-arm a blocked) touch row and COMMIT before any provider call.
    Returns the row, or None if another pass got there first."""
    from sqlalchemy import update
    now = datetime.utcnow()
    existing = item.get("existing")
    if existing is not None:
        res = db.execute(update(ProgramEmailTouch)
                         .where(ProgramEmailTouch.id == existing.id,
                                ProgramEmailTouch.status == "blocked",
                                ProgramEmailTouch.attempted_at == existing.attempted_at)
                         .values(status="claimed", attempted_at=now, reason=None,
                                 email_mode=r["email_mode"],
                                 flyer_asset_id=r["flyer"].id if r["flyer"] is not None else None))
        db.commit()
        if res.rowcount != 1:
            return None
        db.refresh(existing)
        return existing
    row = ProgramEmailTouch(organization_id=prog.organization_id, lead_id=item["lead"].id,
                            source_record_id=item["record"].id, campaign_family=item["family"].key,
                            location_id=prof.location_id, touch_number=item["touch"],
                            email_mode=r["email_mode"],
                            flyer_asset_id=r["flyer"].id if r["flyer"] is not None else None,
                            status="claimed", attempted_at=now)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return row


def run(db: Session, organization_id: Optional[str] = None, *, dry_run: bool = False,
        now: Optional[datetime] = None, force_hours: bool = False) -> Dict:
    """One pass. Returns counts plus (dry_run) the would-send list."""
    report = {"enabled": sending_enabled(), "sent": 0, "failed": 0, "blocked": 0, "unknown": 0,
              "skipped": 0, "held_no_flyer": 0, "due": 0, "dry_run": dry_run, "would_send": []}
    if not dry_run and not report["enabled"]:
        report["reason"] = "%s is off" % SENDING_ENV
        return report
    from app.services import outbound_brake
    if not dry_run and outbound_brake.engaged():
        # Platform-wide stop: claim nothing, so no touch is burned.
        report["reason"] = "outbound emergency stop is engaged"
        return report
    q = db.query(OutreachProgram).filter(OutreachProgram.is_active.is_(True))
    if organization_id:
        q = q.filter(OutreachProgram.organization_id == organization_id)
    from app.services.email_service import send_email_to_lead
    from app.services import send_source as _ss
    from app.services.demo_guard import DemoBoundaryViolation
    for prog in q.all():
        if not dry_run and not force_hours and not in_sending_hours(db, prog.organization_id, now):
            continue
        budget = batch_limit()
        if not dry_run:
            budget = min(budget, daily_cap() - used_today(db, prog.organization_id, now))
            if budget <= 0:
                report["reason"] = "daily cap of %d reached" % daily_cap()
                continue
        flyer_cache: Dict = {}
        # A dry run reads only - it neither writes nor commits nor rolls back.
        items = plan(db, prog, now, with_skip=dry_run, mark_stale=not dry_run)
        if not dry_run:
            db.commit()                               # persist any stale-claim -> unknown
        for item in items:
            report["due"] += 1
            lead, fam, touch = item["lead"], item["family"], item["touch"]
            if not dry_run and budget <= 0:
                break                                 # live pass: stop work at the cap
            # Cheapest hold first: a flyer campaign with no approved flyer for
            # this location (one lookup per family+location, cached).
            mode = fam.first_touch_email_mode if touch == 1 else fam.followup_email_mode
            loc = item["record"].location_id
            if mode in ("hosted", "attached") and loc:
                key = (fam.key, loc)
                if key not in flyer_cache:
                    flyer_cache[key] = flyer_for(db, prog.organization_id, fam, loc)
                if flyer_cache[key] is None:
                    report["skipped"] += 1
                    report["held_no_flyer"] += 1
                    continue
            if item["skip"] is None and not dry_run:
                item["skip"] = _skip_reason(db, lead, item["replied"])
            if item["skip"]:
                report["skipped"] += 1
                continue
            prof = identity.location_profile_for_lead(db, lead)
            if prof is None:
                report["skipped"] += 1
                continue
            r = render_touch(db, prog, fam, prof, lead.first_name, "first" if touch == 1 else "followup",
                             flyer_cache=flyer_cache)
            if r["email_mode"] in ("hosted", "attached") and r["flyer"] is None:
                # The campaign is set to carry a flyer and no approved flyer is
                # active for this location: an email promising a guide with no
                # guide is worse than no email. Held until one is uploaded.
                report["skipped"] += 1
                report["held_no_flyer"] += 1
                continue
            if dry_run:
                report["would_send"].append({
                    "lead_id": lead.id, "source_lead_id": item["record"].source_lead_id,
                    "location": prof.official_name, "family": fam.key, "touch": touch,
                    "email_mode": r["email_mode"], "subject": r["subject"],
                    "flyer": r["flyer"].title if r["flyer"] is not None else None})
                continue
            if budget <= 0:
                break
            # Re-checked at the moment of sending: hours (a long pass), and a
            # reply that arrived while this pass was running.
            if not force_hours and not in_sending_hours(db, prog.organization_id):
                break
            if db.query(ProgramResponse.id).filter(ProgramResponse.lead_id == lead.id).first() is not None:
                report["skipped"] += 1
                continue
            advisor = _sender(db, prog, lead)
            if advisor is None:
                report["skipped"] += 1
                continue
            row = _claim(db, prog, item, prof, r)
            if row is None:
                continue                              # another pass has it
            budget -= 1
            attachments = None
            if r["attachment"] is not None and r["flyer"] is not None:
                data = db.query(ProgramAsset.data).filter(ProgramAsset.id == r["flyer"].id).scalar()
                attachments = [{"filename": r["attachment"]["filename"],
                                "content": base64.b64encode(data or b"").decode(),
                                "content_type": r["flyer"].content_type or "application/pdf"}]
            row_id = row.id
            try:
                msg = send_email_to_lead(db, advisor, lead, subject=r["subject"], body_html=r["body_html"],
                                         send_source=_ss.CADENCE, attachments=attachments)
                status, reason, msg_id = ("sent", None, msg.id) if msg.status == "sent" else \
                    ("failed", "the email provider did not accept the message", msg.id)
            except (ValueError, DemoBoundaryViolation) as exc:
                db.rollback()
                status, reason, msg_id = "blocked", str(exc)[:500], None
            except Exception as exc:                  # noqa: BLE001
                # The provider MAY have accepted it before this failed - so this
                # is "unknown", never "failed": a person checks before resending.
                db.rollback()
                status, reason, msg_id = "unknown", ("outcome unknown (%s) - check the provider log "
                                                     "before resending" % exc)[:500], None
                log.error("program email touch error for lead %s: %s", lead.id, exc, exc_info=True)
            row = db.get(ProgramEmailTouch, row_id)
            row.status, row.reason, row.email_message_id = status, reason, msg_id
            db.commit()
            report[status] += 1
    return report
