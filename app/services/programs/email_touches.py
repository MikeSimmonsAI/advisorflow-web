"""Location outreach programs: automated campaign EMAIL touches.

The SMS cadence engine sends texts only; an email-only family was skipped.
This runner sends a program's campaign emails, location by location:

    touch 1  first-touch email  (family's first_touch_email_mode: none | hosted | attached)
    touch 2..5 follow-ups       (followup_email_mode), each FOLLOWUP_DAYS after the last,
                                every one a DIFFERENT strategy (programs/message_brain.py):
                                useful info -> common question -> easy appointment ->
                                permission-based close. PROGRAM_EMAIL_MAX_TOUCHES caps it.

Every touch's copy comes from the message brain (or, for touch 1, the
family's own edited copy) and must pass its QUALITY GATE; a failing message is
recorded as blocked "held by quality check" with its reasons and never sent.

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
import re
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


def pass_due(now: Optional[datetime] = None, *, key: str = "run",
             every: Optional[timedelta] = None) -> bool:
    """The SLA loop ticks every 2 minutes; campaign email needs no such pace."""
    now = now or datetime.utcnow()
    last = _last_pass.get(key)
    if last is not None and now - last < (every or PASS_INTERVAL):
        return False
    _last_pass[key] = now
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
# A TEMPORARY provider failure (rate limit, 5xx, connection refused before the
# request was sent) is retried on this backoff; anything else is permanent.
# A timeout is NOT temporary here: the provider may have accepted the email,
# so it is "unknown" and never re-sent automatically.
RETRY_BACKOFF = (timedelta(minutes=10), timedelta(minutes=45), timedelta(hours=3))
_TRANSIENT = ("429", "rate limit", "too many requests", "500", "502", "503", "504",
              "service unavailable", "bad gateway", "connection refused", "connecterror",
              "name or service not known", "temporarily")
_AMBIGUOUS = ("timed out", "timeout", "remotedisconnected", "connection reset", "connection aborted")


def classify_provider_error(text: Optional[str]) -> str:
    """'transient' | 'ambiguous' | 'permanent'."""
    t = (text or "").lower()
    if any(k in t for k in _AMBIGUOUS):
        return "ambiguous"
    if any(k in t for k in _TRANSIENT):
        return "transient"
    return "permanent"
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
    from app.models.models import BookingLink
    if db.query(BookingLink.id).filter(BookingLink.lead_id == lead.id,
                                       BookingLink.status.in_(("booked", "confirmed"))).first():
        return "an appointment is booked - the sequence stops"
    return identity.send_refusal(db, lead, "cadence")


DEFAULT_MAX_TOUCHES = 5


def max_touches() -> int:
    """Active email touches per contact (message_brain.TOUCH_STRATEGY 1..5)."""
    try:
        return max(1, min(5, int(os.environ.get("PROGRAM_EMAIL_MAX_TOUCHES") or DEFAULT_MAX_TOUCHES)))
    except ValueError:
        return DEFAULT_MAX_TOUCHES


def _due_touch(touches: Dict[int, ProgramEmailTouch], now: datetime) -> Optional[int]:
    """The touch due now (1..max_touches), or None.

    Touch n is due FOLLOWUP_DAYS after touch n-1 was SENT. A blocked touch is
    retried after BLOCKED_RETRY_AFTER, a temporary failure on its backoff;
    failed / unknown / claimed stop the sequence until a person looks."""
    for n in range(1, max_touches() + 1):
        t = touches.get(n)
        if t is None:
            if n == 1:
                return 1
            prev = touches.get(n - 1)
            return n if now - prev.attempted_at >= timedelta(days=followup_days()) else None
        if t.status == "blocked":
            return n if (t.attempted_at is None or now - t.attempted_at >= BLOCKED_RETRY_AFTER) else None
        if t.status == "retry":
            return n if (t.next_attempt_at is None or now >= t.next_attempt_at) else None
        if t.status != "sent" or not t.attempted_at:
            return None                          # failed / unknown / claimed: a person looks
    return None


def _hold(db: Session, prog: OutreachProgram, item: Dict, prof: LocationProfile, reason: str) -> None:
    """Record a touch the quality gate held. Nothing is sent; it is re-checked
    after BLOCKED_RETRY_AFTER (copy or data may have been fixed meanwhile)."""
    now = datetime.utcnow()
    row = item.get("existing")
    if row is None:
        row = ProgramEmailTouch(organization_id=prog.organization_id, lead_id=item["lead"].id,
                                source_record_id=item["record"].id, campaign_family=item["family"].key,
                                location_id=prof.location_id, touch_number=item["touch"])
        db.add(row)
    row.status, row.attempted_at, row.reason = "blocked", now, reason[:500]
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def _family_copy_is_default(fam: CampaignFamily) -> bool:
    from app.services.programs.setup import DEFAULT_FAMILIES
    d = next((f for f in DEFAULT_FAMILIES if f["key"] == fam.key), None)
    return d is None or ((fam.email_body_template or "") == d["body"]
                         and (fam.email_subject_template or "") == d["subject"])


def build_message(db: Session, prog: OutreachProgram, fam: CampaignFamily, prof: LocationProfile,
                  lead: Lead, touch: int, r: Dict) -> Dict:
    """The copy for this touch and its quality verdict.

    Touch 1 uses the family's own email copy when a person has edited it
    (approved creative wins); otherwise, and for every follow-up, the message
    brain writes the touch from the family's playbook. Either way the quality
    gate decides whether it may go."""
    from app.services.programs import message_brain as brain
    from app.models.models import EmailMessage
    packet = brain.context_packet(db, lead)
    if touch == 1 and not _family_copy_is_default(fam):
        msg = {"touch": 1, "channel": "email", "subject": r["subject"], "body": r["body"],
               "strategy": brain.TOUCH_STRATEGY[1], "family": fam.key, "source": "family copy"}
    else:
        flyer_line = r["fields"].get("flyer_link") or ""
        msg = dict(brain.compose(packet, touch, "email", flyer_line=flyer_line), source="playbook")
    prior = [re.sub(r"<[^>]+>", " ", m.body_html or "") for m in
             db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id,
                                           EmailMessage.status != "failed").limit(10).all()]
    others = [p.official_name for p in db.query(LocationProfile).filter(
        LocationProfile.organization_id == prog.organization_id,
        LocationProfile.is_review_bucket.is_(False)).all()]
    verdict = brain.quality(packet, msg, prior_bodies=prior, other_locations=others,
                            expected_display_name=packet.get("display_name"))
    msg["body_html"] = text_to_html(msg["body"], r.get("flyer_url") or "")
    return {"message": msg, "quality": verdict, "packet": packet}


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
                                ProgramEmailTouch.status.in_(("blocked", "retry")),
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
    report = {"enabled": sending_enabled(), "sent": 0, "failed": 0, "blocked": 0, "unknown": 0, "retry": 0,
              "skipped": 0, "held_no_flyer": 0, "held_quality": 0, "due": 0, "dry_run": dry_run, "would_send": []}
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
            built = build_message(db, prog, fam, prof, lead, touch, r)
            q = built["quality"]
            if dry_run:
                report["would_send"].append({
                    "lead_id": lead.id, "source_lead_id": item["record"].source_lead_id,
                    "location": prof.official_name, "family": fam.key, "touch": touch,
                    "strategy": built["message"].get("strategy"),
                    "email_mode": r["email_mode"], "subject": built["message"]["subject"],
                    "quality_ok": q["ok"], "quality_score": q["score"], "quality_failures": q["failures"],
                    "flyer": r["flyer"].title if r["flyer"] is not None else None})
                continue
            if not q["ok"]:
                # Low-confidence copy is HELD, never sent: recorded with every reason.
                _hold(db, prog, item, prof, "held by quality check: " + "; ".join(q["failures"]))
                report["held_quality"] = report.get("held_quality", 0) + 1
                report["skipped"] += 1
                continue
            r = dict(r, subject=built["message"]["subject"], body_html=built["message"]["body_html"])
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
                                         send_source=_ss.CADENCE, attachments=attachments,
                                         raise_on_provider_failure=True)
                status, reason, msg_id = "sent", None, msg.id
            except (ValueError, DemoBoundaryViolation) as exc:
                db.rollback()
                status, reason, msg_id = "blocked", str(exc)[:500], None
            except RuntimeError as exc:
                # The provider answered and refused (the failed EmailMessage row
                # is already written). Temporary -> retry on a backoff;
                # ambiguous -> unknown; anything else -> failed (a person looks).
                db.rollback()
                kind = classify_provider_error(str(exc))
                msg_id = None
                if kind == "transient":
                    status, reason = "retry", ("temporary provider failure: %s" % exc)[:500]
                elif kind == "ambiguous":
                    status, reason = "unknown", ("outcome unknown (%s) - check the provider log "
                                                 "before resending" % exc)[:500]
                else:
                    status, reason = "failed", ("provider refused: %s" % exc)[:500]
            except Exception as exc:                  # noqa: BLE001
                # The provider MAY have accepted it before this failed - so this
                # is "unknown", never "failed": a person checks before resending.
                db.rollback()
                status, reason, msg_id = "unknown", ("outcome unknown (%s) - check the provider log "
                                                     "before resending" % exc)[:500], None
                log.error("program email touch error for lead %s: %s", lead.id, exc, exc_info=True)
            row = db.get(ProgramEmailTouch, row_id)
            row.attempts = (row.attempts or 0) + 1
            if status == "retry":
                if row.attempts > len(RETRY_BACKOFF):
                    status, reason = "failed", "gave up after %d temporary failures: %s" % (row.attempts, reason)
                else:
                    row.next_attempt_at = datetime.utcnow() + RETRY_BACKOFF[row.attempts - 1]
            row.status, row.reason, row.email_message_id = status, reason, msg_id
            db.commit()
            report[status] = report.get(status, 0) + 1
    return report
