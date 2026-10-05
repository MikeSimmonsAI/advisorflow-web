"""Who a message is from: the program's person, at the family's own location.

    "Kerry Allan | Eastern Gate Memorial Gardens"

The person is constant; the location is resolved per lead, every time. A lead
in a program whose location cannot be resolved is NOT SENT TO - the send
refuses with LOCATION_REVIEW_REFUSAL and the record stays in Location Review.

Organizations without an OutreachProgram are untouched: every entry point
here returns None for them after one indexed lookup.
"""
import json
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models.models import Lead
from app.models.program_models import (
    LocationProfile, OutreachProgram, ProgramSourceRecord,
)

LOCATION_REVIEW_REFUSAL = (
    "LOCATION REVIEW: this contact has no resolved location, so nothing is sent "
    "under an unknown facility name. Assign the location first.")


def program_for_org(db: Session, organization_id: Optional[str]) -> Optional[OutreachProgram]:
    if not organization_id:
        return None
    return (db.query(OutreachProgram)
            .filter(OutreachProgram.organization_id == organization_id,
                    OutreachProgram.is_active.is_(True)).first())


def records_for_lead(db: Session, lead: Lead):
    """Every source row behind this lead (auto-linked rows share one lead)."""
    return (db.query(ProgramSourceRecord)
            .filter(ProgramSourceRecord.organization_id == lead.organization_id,
                    ProgramSourceRecord.lead_id == lead.id)
            .order_by(ProgramSourceRecord.row_number, ProgramSourceRecord.id).all())


def source_record_for_lead(db: Session, lead: Lead) -> Optional[ProgramSourceRecord]:
    """The contact master's own row when there is one, else the first row - stable."""
    recs = records_for_lead(db, lead)
    for r in recs:
        if r.contact_master_key and r.source_lead_id == r.contact_master_key:
            return r
    return recs[0] if recs else None


def location_profile_for_lead(db: Session, lead: Lead) -> Optional[LocationProfile]:
    """The ONE location every row behind this lead agrees on, or None (review)."""
    recs = records_for_lead(db, lead)
    if not recs or any(r.location_status != "mapped" or not r.location_id for r in recs):
        return None
    locs = {r.location_id for r in recs}
    if len(locs) != 1:
        return None
    prof = (db.query(LocationProfile)
            .filter(LocationProfile.organization_id == lead.organization_id,
                    LocationProfile.location_id == locs.pop()).first())
    if prof is None or prof.is_review_bucket:
        return None
    return prof


def display_name(prog: OutreachProgram, prof: LocationProfile) -> str:
    if prof.email_display_name:
        return prof.email_display_name
    person = (prog.primary_contact_name or "").strip()
    return "%s | %s" % (person, prof.official_name) if person else prof.official_name


def sms_signoff(prog: OutreachProgram, prof: LocationProfile) -> str:
    who = prof.sms_identity_name or display_name(prog, prof)
    return "- %s" % who.replace(" | ", ", ")


def context_for(db: Session, lead: Lead, channel: str = "sms",
                flyer_link: Optional[str] = None) -> Optional[Dict]:
    """Everything a message needs, or None if the lead is not in a program.

    {"ok": False, "reason": ...} when the program exists but the location does
    not resolve - the caller must not send.
    """
    prog = program_for_org(db, lead.organization_id)
    if prog is None:
        return None
    prof = location_profile_for_lead(db, lead)
    if prof is None:
        return {"ok": False, "reason": LOCATION_REVIEW_REFUSAL, "program": prog}
    reply = (prog.reply_instructions_email if channel == "email"
             else prog.reply_instructions_sms) or ""
    rec = source_record_for_lead(db, lead)
    return {
        "ok": True, "program": prog, "profile": prof,
        "display_name": display_name(prog, prof),
        "sms_signoff": sms_signoff(prog, prof),
        "fields": {
            "first_name": (lead.first_name or "").strip().title() or "there",
            "location_name": prof.official_name,
            "primary_contact_name": prog.primary_contact_name or "",
            "reply_instructions": reply,
            "flyer_link": flyer_link or "",
            "location_website": prof.website or "",
            "campaign": rec.campaign_family if rec else "",
        },
    }


def render(template: str, fields: Dict[str, str]) -> str:
    out = template or ""
    for k, v in fields.items():
        out = out.replace("{%s}" % k, v or "")
    import re
    out = "\n".join(line.rstrip() for line in out.splitlines()).strip()
    # An empty field (no flyer yet) must not leave a hole in the message.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return re.sub(r"[ \t]{2,}", " ", out)


DATA_REVIEW_REFUSAL = (
    "DATA REVIEW: the source row carries an operational note or was reported as "
    "the wrong person. Clear the review before anything is sent.")
DUPLICATE_REVIEW_REFUSAL = (
    "DUPLICATE REVIEW: this phone or email is shared with a different person in the "
    "source data. Confirm who it belongs to before anything is sent.")


CAMPAIGN_OFF_REFUSAL = (
    "CAMPAIGN OFF: this contact's campaign is not switched on, so no automated "
    "message goes out. A person can still reply by hand.")


def send_refusal(db: Session, lead: Lead, send_source: Optional[str] = None) -> Optional[str]:
    """The reason this lead must not be sent to, or None. Used by every send path.

    Automated sends (anything not stamped MANUAL) also require the contact's
    campaign family to be switched on - families are created OFF.
    """
    prog = program_for_org(db, lead.organization_id)
    if prog is None:
        return None
    recs = records_for_lead(db, lead)
    rec = source_record_for_lead(db, lead)
    if any(r.needs_data_review for r in recs):
        return DATA_REVIEW_REFUSAL
    if any(r.duplicate_review_reason for r in recs):
        return DUPLICATE_REVIEW_REFUSAL
    if prog.require_location_to_send and location_profile_for_lead(db, lead) is None:
        return LOCATION_REVIEW_REFUSAL
    from app.services import send_source as _ss
    if send_source != _ss.MANUAL and rec is not None:
        from app.models.program_models import CampaignFamily
        key = rec.campaign_family or "re_engagement"
        fam = (db.query(CampaignFamily)
               .filter(CampaignFamily.organization_id == lead.organization_id,
                       CampaignFamily.key == key).first())
        if fam is None or not fam.is_active:
            return CAMPAIGN_OFF_REFUSAL
    return None


def apply_email_identity(db: Session, lead: Lead, ident):
    """Set the From display name on a resolved SendingIdentity for a program lead."""
    ctx = context_for(db, lead, channel="email")
    if ctx and ctx.get("ok") and ident is not None:
        try:
            ident.from_name = ctx["display_name"]
        except AttributeError:
            pass
    return ident


def apply_sms_signoff(db: Session, lead: Lead, body: str) -> str:
    """Append "- Kerry Allan, <Location>" once, for a program lead."""
    ctx = context_for(db, lead, channel="sms")
    if not ctx or not ctx.get("ok"):
        return body
    signoff = ctx["sms_signoff"]
    text = (body or "").rstrip()
    if signoff in text:
        return body
    # The sign-off goes BEFORE the opt-out line, which stays last.
    from app.services.sms_content_policy import REQUIRED_OPT_OUT
    if text.endswith(REQUIRED_OPT_OUT):
        head = text[: -len(REQUIRED_OPT_OUT)].rstrip()
        return ("%s %s %s" % (head, signoff, REQUIRED_OPT_OUT)).strip()
    return ("%s %s" % (text, signoff)).strip()


def channels(prog: OutreachProgram):
    try:
        return json.loads(prog.customer_channels or "[]")
    except ValueError:
        return []


def cadence_text(db: Session, lead: Lead, touch_number: int) -> Optional[str]:
    """The program's own text for a cadence touch, rendered for the lead's location.

    Touch 1 uses the lead's campaign family; later touches use the program's
    re-engagement family. None (fall back to the platform template) when the
    lead is not in a program or no template applies. A lead whose location does
    not resolve gets None here and is refused by send_sms anyway.
    """
    from app.models.program_models import CampaignFamily
    ctx = context_for(db, lead, channel="sms")
    if not ctx or not ctx.get("ok"):
        return None
    rec = source_record_for_lead(db, lead)
    key = (rec.campaign_family if (rec and touch_number <= 1) else "re_engagement") or "re_engagement"
    fam = (db.query(CampaignFamily)
           .filter(CampaignFamily.organization_id == lead.organization_id,
                   CampaignFamily.key == key).first())
    if fam is None or not fam.sms_template:
        return None
    return render(fam.sms_template, ctx["fields"])
