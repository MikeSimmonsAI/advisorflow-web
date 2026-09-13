"""What an unauthenticated website submission BECOMES once its destination is
known.

`public_intake.py` answers WHERE a public submission lands. This answers WHAT
lands there. The two were never separated, so every public arrival path grew
its own copy of "find the person, or make one": the demo-request handler
matched an unnormalized phone, the SMS opt-in handler matched a normalized one,
and neither could see the other's record. The same human filling in both forms
became two rows in the same workspace, and the consent typed into one of them
did not attach to the person named in the other.

WHAT THIS OWNS, once and for every public form:

  * FIND OR CREATE, on one matching rule. Phone compared after normalizing -
    "(214) 555-0100" and "+1 214 555 0100" are one person - then email
    compared case-insensitively. A second submission updates the record it
    found; it never creates a second one.
  * PROVENANCE. `source` says which website, `source_detail` says which form.
    Both are written on every arrival, including repeat ones, because the
    latest thing a prospect did is the useful fact.
  * CONSENT AS EVIDENCE, NOT AS A NOTE. An opt-in writes the columns a carrier
    dispute is actually argued from: the flag, the timestamp, the IP, the
    user agent, and THE EXACT WORDING the person was shown. The opt-in route
    used to write all of that into free text, where nothing can query it.
  * NOTHING ELSE. No cadence is started, no message is queued, no AI is woken.

WHAT IT DELIBERATELY DOES NOT DO.

CONSENT IS NOT SALES INTENT. Somebody who ticks "yes, text me appointment
reminders" has not asked to be sold to, and a platform that reads those as the
same thing will put them in a sales cadence on the strength of a compliance
checkbox. So an SMS opt-in never sets a sales tier, never sets
`message_track`, and never raises an existing lead's status. A demo request
does carry sales intent and is recorded as such.

A CONSENT IS NEVER WITHDRAWN BY A LATER FORM. Filling in the waitlist after
opting in does not clear the opt-in: consent already given is left exactly as
it was recorded. Only an explicit opt-out revokes it, and that is not this
module's job.
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import Lead
from app.services.dedup_service import normalize_phone

log = logging.getLogger(__name__)


# ── THE THREE PUBLIC FORMS ───────────────────────────────────────────────────
# A kind is not free text. It decides whether the arrival carries sales intent,
# what `source_detail` is written, and how the note reads. Adding a fourth form
# means adding it here, which is the point: the decision is in one table rather
# than re-argued at each call site.

KIND_DEMO = "demo_request"
KIND_SMS_OPTIN = "sms_optin"
KIND_WAITLIST = "waitlist"

SOURCE_DETAIL = {
    KIND_DEMO: "Request Demo",
    KIND_SMS_OPTIN: "SMS Opt-In",
    KIND_WAITLIST: "Waitlist",
}

# Which arrivals are somebody asking to be sold to. An opt-in and a waitlist
# signup are not, and the difference is enforced here rather than remembered.
SALES_INTENT = {KIND_DEMO: True, KIND_SMS_OPTIN: False, KIND_WAITLIST: False}

NOTE_HEADING = {
    KIND_DEMO: "Demo Request",
    KIND_SMS_OPTIN: "SMS Opt-In",
    KIND_WAITLIST: "Waitlist Signup",
}

# Everything arriving through a public web form shares this classification, so
# the audience builders can separate web arrivals from purchased lists and
# database cohorts without parsing `source`.
SOURCE_CATEGORY = "web_form"


def website_source(platform) -> str:
    """"EvoSys Pro Website" - the BRAND's own name, read at request time.

    Not a constant, and not the organization's name. The brand is what the
    person visiting the site believes they contacted, and a second brand
    posting into the same platform must not be recorded as the first one.
    """
    name = (getattr(platform, "name", None) or "").strip()
    return ("%s Website" % name) if name else "Website"


@dataclass
class Consent:
    """What the person was shown and whether they agreed to it.

    `text` is the wording verbatim. A paraphrase is worthless here: in a
    carrier or TCPA dispute the exact sentence is the evidence, and a
    reconstruction of it is not.
    """
    given: bool = False
    text: Optional[str] = None
    version: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    page_url: Optional[str] = None
    at: Optional[datetime] = None


@dataclass
class Submission:
    """One website form submission, already validated by its caller."""
    kind: str
    first_name: str = ""
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None
    message: Optional[str] = None
    page_url: Optional[str] = None
    referrer: Optional[str] = None
    # utm_source / utm_medium / utm_campaign / utm_term / utm_content, when the
    # site collected them. Absent is normal and is not an error.
    utm: dict = field(default_factory=dict)
    # Anything else the form asked that has no column of its own: locations,
    # lead volume, current system, package of interest. Kept verbatim in the
    # note and in custom_fields rather than invented into schema.
    extra: dict = field(default_factory=dict)
    consent: Optional[Consent] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    submitted_at: Optional[datetime] = None


def split_name(full: Optional[str]) -> tuple[str, Optional[str]]:
    """"Dana Whitfield" -> ("Dana", "Whitfield"). One word stays a first name.

    Marketing forms ask for a full name in one box; the lead record has two.
    Somebody has to split it, and doing it here means all three forms split it
    the same way.
    """
    parts = (full or "").strip().split()
    if not parts:
        return "", None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _clean(value: Optional[str], limit: int = 2000) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def find_existing(db: Session, org_id: str, *, phone_norm: str,
                  phone_raw: Optional[str], email: Optional[str]):
    """The person this submission is about, if this workspace already knows them.

    Phone first, because a phone number identifies one handset and an email
    address is more often shared or mistyped. Both the normalized and the raw
    form are compared: rows written before normalization existed still carry
    whatever the form sent, and missing them would create the duplicate this
    function exists to prevent.
    """
    if phone_norm:
        candidates = [phone_norm]
        if phone_raw and phone_raw != phone_norm:
            candidates.append(phone_raw)
        hit = (db.query(Lead)
               .filter(Lead.organization_id == org_id,
                       Lead.phone.in_(candidates))
               .first())
        if hit is not None:
            return hit
    if email:
        hit = (db.query(Lead)
               .filter(Lead.organization_id == org_id,
                       Lead.email.isnot(None))
               .all())
        lowered = email.lower()
        for lead in hit:
            if (lead.email or "").strip().lower() == lowered:
                return lead
    return None


def _note_block(sub: Submission, stamp: datetime) -> str:
    heading = NOTE_HEADING.get(sub.kind, "Website Submission")
    lines = ["[%s %s UTC]" % (heading, stamp.strftime("%Y-%m-%d %H:%M"))]
    if sub.company:
        lines.append("Company: %s" % sub.company)
    if sub.industry:
        lines.append("Industry: %s" % sub.industry)
    for key in sorted(sub.extra):
        value = _clean(sub.extra.get(key), 400)
        if value:
            lines.append("%s: %s" % (key.replace("_", " ").title(), value))
    if sub.message:
        lines.append("Message: %s" % sub.message)
    if sub.page_url:
        lines.append("Page: %s" % sub.page_url)
    if sub.referrer:
        lines.append("Referrer: %s" % sub.referrer)
    for key in sorted(sub.utm):
        value = _clean(sub.utm.get(key), 200)
        if value:
            lines.append("%s: %s" % (key, value))
    consent = sub.consent
    if consent and consent.given:
        lines.append("SMS consent: YES (version %s)"
                     % (consent.version or "unversioned"))
    elif consent is not None and sub.kind == KIND_DEMO:
        lines.append("SMS consent: no")
    return "\n".join(lines)


def _merge_custom_fields(lead: Lead, sub: Submission) -> None:
    """Keep the form's own answers queryable without inventing columns for them.

    `custom_fields` is the column the model already designates for exactly
    this. Existing keys are preserved and only the ones this submission
    actually carried are written, so a later waitlist signup cannot blank out
    what a demo request said.
    """
    payload = {}
    raw = getattr(lead, "custom_fields", None)
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                payload = loaded
        except (ValueError, TypeError):
            payload = {}
    for key, value in list(sub.extra.items()) + list(sub.utm.items()):
        cleaned = _clean(value, 400)
        if cleaned:
            payload[key] = cleaned
    if sub.page_url:
        payload["page_url"] = _clean(sub.page_url, 400)
    if sub.referrer:
        payload["referrer"] = _clean(sub.referrer, 400)
    if payload:
        lead.custom_fields = json.dumps(payload)[:8000]


def _apply_consent(lead: Lead, sub: Submission, stamp: datetime) -> None:
    """Write consent where it can be queried, and never un-write it.

    An arrival that carries no consent block, or carries one the person did
    not tick, leaves every consent column exactly as it was. Someone who opted
    in last month and fills in a demo form today without ticking the optional
    box has not withdrawn anything, and a form is not a revocation channel.
    """
    consent = sub.consent
    if consent is None or not consent.given:
        return
    lead.sms_consent = True
    lead.sms_consent_timestamp = consent.at or stamp
    lead.sms_consent_ip = _clean(consent.ip or sub.ip, 64)
    lead.sms_consent_text = _clean(consent.text, 4000)
    # The source records WHERE the consent was taken, at a granularity a
    # compliance reviewer can act on: which form, on which page, and under
    # which version of the wording.
    bits = [SOURCE_DETAIL.get(sub.kind, sub.kind)]
    if consent.page_url or sub.page_url:
        bits.append(consent.page_url or sub.page_url)
    if consent.version:
        bits.append("v%s" % consent.version)
    lead.sms_consent_source = _clean(" · ".join(b for b in bits if b), 400)
    user_agent = _clean(consent.user_agent or sub.user_agent, 400)
    if user_agent:
        # The user agent has no column of its own and inventing one for a
        # single evidence field would be worse than keeping it beside the rest
        # of the form's answers, where it is still queryable as JSON.
        _stash(lead, "sms_consent_user_agent", user_agent)


def _stash(lead: Lead, key: str, value: str) -> None:
    payload = {}
    raw = getattr(lead, "custom_fields", None)
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                payload = loaded
        except (ValueError, TypeError):
            payload = {}
    payload[key] = value
    lead.custom_fields = json.dumps(payload)[:8000]


def capture(db: Session, *, platform, org, sub: Submission,
            commit: bool = True) -> dict:
    """Record one public submission against one organization.

    Returns {"action": "created"|"updated", "lead_id": ...}. Raises nothing of
    its own: the destination was already resolved and verified by the caller,
    and a submission that got this far is one the caller validated.
    """
    stamp = sub.submitted_at or datetime.utcnow()
    phone_raw = _clean(sub.phone, 40)
    phone_norm = normalize_phone(phone_raw or "")
    email = _clean(sub.email, 180)
    first = _clean(sub.first_name, 120) or ""
    last = _clean(sub.last_name, 120)
    source = website_source(platform)
    detail = SOURCE_DETAIL.get(sub.kind, sub.kind)

    lead = find_existing(db, org.id, phone_norm=phone_norm,
                         phone_raw=phone_raw, email=email)
    created = lead is None

    if created:
        lead = Lead(
            id=str(_uuid.uuid4()),
            organization_id=org.id,
            first_name=first,
            last_name=last,
            email=email,
            phone=phone_norm or phone_raw,
            phone_raw=phone_raw,
            status="new",
            created_at=stamp,
        )
        # An SMS opt-in is a consent event, so it arrives on the channel it
        # consented to. The other two forms leave the model default alone.
        if sub.kind == KIND_SMS_OPTIN:
            lead.contact_channel = "sms"
    else:
        # NEVER OVERWRITE WITH LESS. A repeat submission that left the name
        # blank must not erase the name the first one gave.
        if first and not (lead.first_name or "").strip():
            lead.first_name = first
        if last and not (lead.last_name or "").strip():
            lead.last_name = last
        if email and not (lead.email or "").strip():
            lead.email = email
        if phone_norm and not (lead.phone or "").strip():
            lead.phone = phone_norm
            lead.phone_raw = phone_raw

    # PROVENANCE, on every arrival including repeats. The most recent thing a
    # prospect did is what a seller opening the record needs to see.
    lead.source = source
    lead.source_detail = detail
    lead.source_category = SOURCE_CATEGORY
    if not (lead.source_file or "").strip():
        # Kept for the callers and dashboards that still read it; it is the
        # legacy field and it is never allowed to hold a filename it isn't.
        lead.source_file = detail.lower().replace(" ", "_").replace("-", "_")

    # SALES INTENT, only where there is some. `tier` and `message_track` steer
    # sales workflow, so a consent event must not set them - and must not raise
    # a lead a seller has already advanced back to "new".
    if SALES_INTENT.get(sub.kind):
        if not (lead.tier or "").strip():
            lead.tier = "web_lead"
        if created:
            lead.message_track = "new_inquiry_intro"

    note = _note_block(sub, stamp)
    lead.notes = ((lead.notes or "") + ("\n" if lead.notes else "") + note).strip()
    lead.updated_at = stamp

    _merge_custom_fields(lead, sub)
    _apply_consent(lead, sub, stamp)

    if created:
        # PLAN CAPACITY - HELD, NEVER DROPPED. A real person filled in a form;
        # a billing ceiling is a reason to hold the record for release, never a
        # reason to throw the person away.
        from app.services import lead_capacity
        lead_capacity.hold_if_over_capacity(db, lead, org)
        db.add(lead)

    if commit:
        db.commit()
        db.refresh(lead)

    # NOTHING IS SENT FROM HERE. No cadence is started, no SMS or email is
    # queued, no AI conversation is opened. Outreach begins when a person
    # decides it does.
    return {"action": "created" if created else "updated", "lead_id": lead.id}
