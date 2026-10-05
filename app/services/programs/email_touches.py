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
  * it is inside sending hours (9:00-18:00 in the organization's timezone), and
  * if the campaign's email mode carries a flyer (hosted / attached), an
    approved flyer is active for that location - otherwise it is held.
Then send_email_to_lead runs the same compliance gate every email passes
(DNC, opt-out, bad address, demo boundary, AI-paused holds).

NEVER TWICE. A touch is claimed by inserting its ProgramEmailTouch row
(unique on lead + touch) and committing BEFORE the provider is called.
A provider failure is recorded as failed and not retried automatically - a
person looks at it. A refusal is recorded as blocked with the reason.

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
    """The active flyer for this family/category, location-specific first."""
    q = db.query(ProgramAsset).filter(ProgramAsset.organization_id == org_id,
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
                 first_name: Optional[str], touch: str = "first") -> Dict:
    """Subject, plain body, HTML body and attachment for one family/location/touch."""
    mode = fam.first_touch_email_mode if touch == "first" else fam.followup_email_mode
    found = flyer_for(db, prog.organization_id, fam, prof.location_id)
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

def _sender(db: Session, prog: OutreachProgram, lead: Lead) -> Optional[User]:
    for uid in (prog.primary_contact_user_id, lead.assigned_to_id):
        if uid:
            u = db.query(User).filter(User.id == uid, User.is_active.is_(True)).first()
            if u is not None:
                return u
    from app.services.programs.responses import _org_admin_users
    admins = _org_admin_users(db, prog.organization_id)
    return admins[0] if admins else None


def _skip_reason(db: Session, prog: OutreachProgram, lead: Lead) -> Optional[str]:
    if not (lead.email or "").strip():
        return "no email address"
    if lead.allow_email is False:
        return "email opt-out of record"
    if (lead.status or "").upper() == "DNC":
        return "do not contact"
    if db.query(ProgramResponse.id).filter(ProgramResponse.lead_id == lead.id).first() is not None:
        return "replied - a person has it"
    refusal = identity.send_refusal(db, lead, "cadence")
    if refusal:
        return refusal
    return None


def plan(db: Session, prog: OutreachProgram, now: Optional[datetime] = None) -> List[Dict]:
    """Every contact due a touch right now, with the touch and why/why not."""
    now = now or datetime.utcnow()
    fams = {f.key: f for f in db.query(CampaignFamily).filter(
        CampaignFamily.organization_id == prog.organization_id, CampaignFamily.is_active.is_(True)).all()}
    if not fams:
        return []
    recs = (db.query(ProgramSourceRecord)
            .filter(ProgramSourceRecord.organization_id == prog.organization_id,
                    ProgramSourceRecord.lead_id.isnot(None),
                    ProgramSourceRecord.campaign_family.in_(list(fams)))
            .order_by(ProgramSourceRecord.row_number).all())
    seen, out = set(), []
    wait = timedelta(days=followup_days())
    for rec in recs:
        if rec.lead_id in seen:
            continue
        seen.add(rec.lead_id)
        lead = db.query(Lead).filter(Lead.id == rec.lead_id,
                                     Lead.organization_id == prog.organization_id).first()
        if lead is None:
            continue
        master = identity.source_record_for_lead(db, lead) or rec
        fam = fams.get(master.campaign_family)
        if fam is None:
            continue
        touches = {t.touch_number: t for t in db.query(ProgramEmailTouch)
                   .filter(ProgramEmailTouch.lead_id == lead.id).all()}
        if 1 not in touches:
            touch = 1
        elif 2 not in touches and touches[1].status == "sent" and touches[1].attempted_at \
                and now - touches[1].attempted_at >= wait:
            touch = 2
        else:
            continue
        out.append({"lead": lead, "record": master, "family": fam, "touch": touch,
                    "skip": _skip_reason(db, prog, lead)})
    return out


# ── the runner ──────────────────────────────────────────────────────────────

def run(db: Session, organization_id: Optional[str] = None, *, dry_run: bool = False,
        now: Optional[datetime] = None, force_hours: bool = False) -> Dict:
    """One pass. Returns counts plus (dry_run) the would-send list."""
    report = {"enabled": sending_enabled(), "sent": 0, "failed": 0, "blocked": 0,
              "skipped": 0, "due": 0, "dry_run": dry_run, "would_send": []}
    if not dry_run and not report["enabled"]:
        report["reason"] = "%s is off" % SENDING_ENV
        return report
    from app.services import outbound_brake
    if not dry_run and outbound_brake.engaged():
        # Platform-wide stop: claim nothing, so no touch is burned as "failed".
        report["reason"] = "outbound emergency stop is engaged"
        return report
    q = db.query(OutreachProgram).filter(OutreachProgram.is_active.is_(True))
    if organization_id:
        q = q.filter(OutreachProgram.organization_id == organization_id)
    from app.services.email_service import send_email_to_lead
    from app.services import send_source as _ss
    for prog in q.all():
        if not dry_run and not force_hours and not in_sending_hours(db, prog.organization_id, now):
            continue
        budget = batch_limit()
        for item in plan(db, prog, now):
            report["due"] += 1
            lead, fam, touch = item["lead"], item["family"], item["touch"]
            if item["skip"]:
                report["skipped"] += 1
                continue
            prof = identity.location_profile_for_lead(db, lead)
            if prof is None:
                report["skipped"] += 1
                continue
            r = render_touch(db, prog, fam, prof, lead.first_name, "first" if touch == 1 else "followup")
            if r["email_mode"] in ("hosted", "attached") and r["flyer"] is None:
                # The campaign is set to carry a flyer and no approved flyer is
                # active for this location: an email promising a guide with no
                # guide is worse than no email. Held until one is uploaded.
                report["skipped"] += 1
                report["held_no_flyer"] = report.get("held_no_flyer", 0) + 1
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
            advisor = _sender(db, prog, lead)
            if advisor is None:
                report["skipped"] += 1
                continue
            row = ProgramEmailTouch(organization_id=prog.organization_id, lead_id=lead.id,
                                    source_record_id=item["record"].id, campaign_family=fam.key,
                                    location_id=prof.location_id, touch_number=touch,
                                    email_mode=r["email_mode"],
                                    flyer_asset_id=r["flyer"].id if r["flyer"] is not None else None,
                                    status="claimed", attempted_at=datetime.utcnow())
            db.add(row)
            try:
                db.commit()                      # the claim - before any provider call
            except IntegrityError:
                db.rollback()
                continue                         # another pass has it
            budget -= 1
            attachments = None
            if r["attachment"] is not None and r["flyer"] is not None:
                attachments = [{"filename": r["attachment"]["filename"],
                                "content": base64.b64encode(r["flyer"].data).decode(),
                                "content_type": r["flyer"].content_type or "application/pdf"}]
            try:
                msg = send_email_to_lead(db, advisor, lead, subject=r["subject"], body_html=r["body_html"],
                                         send_source=_ss.CADENCE, attachments=attachments)
                row.email_message_id = msg.id
                row.status = "sent" if msg.status == "sent" else "failed"
                if row.status == "failed":
                    row.reason = "the email provider did not accept the message"
            except ValueError as exc:
                db.rollback()
                row = db.merge(row)
                row.status, row.reason = "blocked", str(exc)[:500]
            except Exception as exc:                     # noqa: BLE001
                db.rollback()
                row = db.merge(row)
                row.status, row.reason = "failed", ("error: %s" % exc)[:500]
                log.error("program email touch failed for lead %s: %s", lead.id, exc, exc_info=True)
            db.commit()
            report[row.status if row.status in ("sent", "failed", "blocked") else "failed"] += 1
    return report
