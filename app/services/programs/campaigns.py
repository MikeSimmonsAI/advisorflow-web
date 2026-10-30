"""SCI campaigns that are not one of the original ten families (#385).

CENTRAL ALABAMA 2025–2026 MS - its own campaign, never routed to Veteran
Planning Guide, Veteran Official, General Survey, Re-engagement or any other
family because nothing else matched.

What the source data establishes (all 473 rows, Oct 25):
  * Campaign Channel = "Direct Mail" on every row;
  * the source designation "Central Alabama 2025–2026 MS" ("MS" is NOT
    expanded - nobody has said what it stands for);
  * 16 Central Alabama locations.
THE OWNER'S CAMPAIGN BRIEF (Mike, #441, Oct 29) - the working source of truth:
  * these families completed one of our surveys requesting a PRE-PLANNING GUIDE
    and a SAVINGS CERTIFICATE;
  * Kerry Allan reaches out with the family's own location;
  * the goal is a short appointment to get those to them and go over everything;
  * friendly, personal, conversational, straightforward;
  * SCI's existing cadence timing, but every touch has its own wording.
  Owner's line: "Name, this is Kerry with xyz Cemetery. Reason I'm reaching out
  is you filled out one of our surveys requesting a pre planning guide and a
  savings certificate. When would be a good time to get those out to you."
Never invent offer terms: no certificate value, expiration, guarantee or
benefit. "MS" is an internal identifier and never appears in a message.

EVERY TOUCH HAS ITS OWN COPY (touch_copy_json): text 1..N follows the SCI
program cadence's touches, email 1..M follows the campaign email runner. A
blank step never falls back to another campaign's words, and the campaign
cannot be switched on while any step is blank (activation_blocker).
"""
import json
from typing import Dict, Optional

from sqlalchemy.orm import Session

CENTRAL_ALABAMA_KEY = "central_alabama_ms"
CENTRAL_ALABAMA_NAME = "Central Alabama 2025–2026 MS"
CENTRAL_ALABAMA = {
    "key": CENTRAL_ALABAMA_KEY, "name": CENTRAL_ALABAMA_NAME,
    # Matches the file's own campaign text in either dash style - and nothing
    # any other family uses.
    "patterns": ["Central Alabama 2025-2026 MS", "Central Alabama 2025–2026 MS", "Central Alabama 2025 2026 MS"],
    "asset_category": "central_alabama_ms",
}

# What the AI may and may not say about WHY we contacted a family, by campaign.
# A campaign without an entry gets nothing extra (its outreach copy speaks for it).
AI_NOTES: Dict[str, Dict[str, str]] = {
    CENTRAL_ALABAMA_KEY: {
        "campaign": CENTRAL_ALABAMA_NAME,
        "what_we_know": ("This family filled out one of our surveys requesting a pre-planning guide and a "
                         "savings certificate (owner's campaign brief)."),
        "rules": ("Kerry Allan with the family's own location is getting them the pre-planning guide and savings "
                  "certificate they asked for and would like a short appointment to go over everything. Do NOT state "
                  "a certificate value, amount, expiration date, guarantee or any benefit - none is on file; if asked, "
                  "say Kerry will go over the details at the visit. Do not mention veteran benefits, seminars, Life "
                  "Story or cremation offers. Never say or explain 'MS'. Be friendly, personal and brief, and invite a "
                  "short appointment the ways this location offers."),
    },
}

# Campaigns that speak ONLY with their own approved copy: never a playbook,
# never the re-engagement follow-up line, never the program's generic touches.
OWN_COPY_KEYS = {CENTRAL_ALABAMA_KEY}

# ── per-touch copy ───────────────────────────────────────────────────────────
#
# Text 1 / email 1 live in the family's own columns (sms_template,
# email_subject_template, email_body_template) so every existing reader keeps
# working; touches 2+ live in CampaignFamily.touch_copy_json:
#   {"sms": {"2": "..."}, "email": {"2": {"subject": "...", "body": "..."}}, "seeded": true}
# Placeholders: {first_name} {location_name} {primary_contact_name}
# {appointment_options} {location_phone} {booking_line} (email)

DEFAULT_SMS_STEPS = [(1, 0, 10), (2, 1, 11), (3, 3, 14), (4, 5, 11), (5, 7, 15), (6, 9, 11)]

# The owner's own wording is touch 1; the rest follow the same brief, each a
# different angle, every one asking for a short visit. No value, expiry or
# guarantee is ever stated.
CA_SMS = {
    1: ("Hi {first_name}, this is {primary_contact_name} with {location_name}. I'm reaching out because you "
        "filled out one of our surveys requesting a pre-planning guide and savings certificate. I'd like to get "
        "those to you and briefly go over everything together. What day works best for you?"),
    2: ("Hi {first_name}, {primary_contact_name} again. I'd still like to get your pre-planning guide and "
        "savings certificate to you. Would a short visit later this week work for you?"),
    3: ("{first_name}, would mornings or afternoons be easier for you? I can meet with you {appointment_options} "
        "to go over your pre-planning guide and savings certificate. It only takes a few minutes."),
    4: ("Hi {first_name}, it's {primary_contact_name}. Many families tell me the guide makes planning ahead "
        "feel a lot simpler. I'd be glad to walk you through it. Is there a day this week or next that suits you?"),
    5: ("{first_name}, your pre-planning guide and savings certificate are still waiting for you at "
        "{location_name}. Text me back a day and time and I'll set it up around your schedule."),
    6: ("Hi {first_name}, {primary_contact_name} with {location_name} one more time. Whenever you're ready, "
        "just reply with a good day and I'll get your pre-planning guide and savings certificate to you."),
}
_SIG = "{primary_contact_name}\n{location_name}\n{location_phone}"
CA_EMAIL = {
    1: ("Your Pre-Planning Guide and Savings Certificate",
        "Hi {first_name},\n\nThis is {primary_contact_name} with {location_name}.\n\n"
        "I'm following up regarding the survey you completed requesting a pre-planning guide and savings "
        "certificate.\n\nI'd like to get those materials to you and take a few minutes to go over everything "
        "together.\n\nWhat day would be convenient for you to meet? Just reply to this email and let me know."
        "\n\n{booking_line}I look forward to hearing from you.\n\n" + _SIG),
    2: ("Following up on your planning guide",
        "Hi {first_name},\n\nI wanted to follow up on the pre-planning guide and savings certificate you asked "
        "for on our survey. Rather than simply mailing them, I like to sit down for a few minutes so you know "
        "what's in them and can ask anything that comes to mind.\n\nI can meet with you {appointment_options}. "
        "If you reply with a day that suits you, I'll take care of the rest.\n\n{booking_line}" + _SIG),
    3: ("A quick question about your visit",
        "Hi {first_name},\n\nA question I hear a lot is whether a planning visit means making decisions on the "
        "spot. It doesn't. We simply go over your guide and savings certificate together, and you take "
        "everything home to think about.\n\nWould a morning or an afternoon be better for you? Reply here and "
        "I'll find a time.\n\n{booking_line}" + _SIG),
    4: ("Saving a few minutes for you",
        "Hi {first_name},\n\nI'm putting together my schedule at {location_name} and would be glad to save a "
        "few minutes for you. That's all it takes to get your pre-planning guide and savings certificate into "
        "your hands and answer your questions.\n\nReply with a day that works and I'll hold it for you."
        "\n\n{booking_line}" + _SIG),
    5: ("Whenever you're ready, {first_name}",
        "Hi {first_name},\n\nI don't want your pre-planning guide and savings certificate to sit here "
        "unclaimed. Whenever the timing feels right, reply to this email with a day, and I'll meet with you "
        "{appointment_options}.\n\nThank you for filling out our survey. I look forward to meeting you."
        "\n\n{booking_line}" + _SIG),
}


def _copy_json(fam) -> Dict:
    try:
        d = json.loads(getattr(fam, "touch_copy_json", None) or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def sms_steps(db: Optional[Session], organization_id: Optional[str]):
    """[(touch, day_offset, send_hour)] - the program cadence's own SMS touches."""
    if db is not None and organization_id:
        from app.models.models import CadenceTemplate, CadenceTemplateTouch
        from app.services.programs.identity import PROGRAM_CADENCE_INDUSTRY
        tpl = (db.query(CadenceTemplate)
               .filter(CadenceTemplate.organization_id == organization_id,
                       CadenceTemplate.is_default.is_(True), CadenceTemplate.is_active.is_(True),
                       CadenceTemplate.industry == PROGRAM_CADENCE_INDUSTRY).first())
        if tpl is not None:
            rows = (db.query(CadenceTemplateTouch)
                    .filter(CadenceTemplateTouch.template_id == tpl.id,
                            CadenceTemplateTouch.is_active.isnot(False))
                    .order_by(CadenceTemplateTouch.touch_number).all())
            steps = [(t.touch_number, t.day_offset, t.send_hour) for t in rows if (t.channel or "sms") in ("sms", "both")]
            if steps:
                return steps
    return list(DEFAULT_SMS_STEPS)


def email_steps():
    """[(touch, day_offset)] - the campaign email runner's touches."""
    from app.services.programs import email_touches as et
    return [(n, (n - 1) * et.followup_days()) for n in range(1, et.max_touches() + 1)]


def sms_for(fam, n: int) -> Optional[str]:
    """This campaign's own text for touch n, or None (never another campaign's)."""
    if n == 1:
        t = fam.sms_template
    else:
        t = (_copy_json(fam).get("sms") or {}).get(str(n))
    return t if (t or "").strip() else None


def email_for(fam, n: int) -> Optional[Dict[str, str]]:
    if n == 1:
        subj, body = fam.email_subject_template, fam.email_body_template
    else:
        e = (_copy_json(fam).get("email") or {}).get(str(n)) or {}
        subj, body = e.get("subject"), e.get("body")
    if not (subj or "").strip() or not (body or "").strip():
        return None
    return {"subject": subj, "body": body}


def touch_copy(db: Optional[Session], fam) -> Dict:
    """Every step of this campaign with its schedule and copy (blank = "")."""
    sms = [{"touch": n, "day_offset": d, "send_hour": h, "body": sms_for(fam, n) or ""}
           for n, d, h in sms_steps(db, fam.organization_id)]
    email = []
    for n, d in email_steps():
        e = email_for(fam, n) or {}
        email.append({"touch": n, "day_offset": d, "subject": e.get("subject") or "", "body": e.get("body") or ""})
    return {"sms": sms, "email": email}


def set_touch_copy(fam, sms: Optional[Dict] = None, email: Optional[Dict] = None) -> None:
    """Save steps. Never activates anything. Keys are touch numbers."""
    d = _copy_json(fam)
    d.setdefault("sms", {})
    d.setdefault("email", {})
    for k, v in (sms or {}).items():
        n = int(k)
        if n < 1 or n > 12:
            raise ValueError("text step %s is outside the cadence" % k)
        v = (v or "").strip()
        if n == 1:
            fam.sms_template = v or None
        else:
            d["sms"][str(n)] = v
    for k, v in (email or {}).items():
        n = int(k)
        if n < 1 or n > 12:
            raise ValueError("email step %s is outside the cadence" % k)
        subj, body = ((v or {}).get("subject") or "").strip(), ((v or {}).get("body") or "").strip()
        if "call us" in body.lower():
            raise ValueError("Families are asked to reply, not to call.")
        if n == 1:
            fam.email_subject_template, fam.email_body_template = subj or None, body or None
        else:
            d["email"][str(n)] = {"subject": subj, "body": body}
    fam.touch_copy_json = json.dumps(d)


def copy_problems(fam) -> list:
    """What the delivery quality gate would hold, checked when copy is SAVED, so
    the editor never stores an email that previews fine and is then held."""
    import re
    from app.services.programs import message_brain as brain
    out = []
    pb = brain.playbook(fam.key)
    prohibited = [p for p, _ in brain._COMMON_PROHIBITED] + list(pb.get("prohibited") or [])
    known = {"first_name", "location_name", "primary_contact_name", "appointment_options",
             "location_phone", "booking_line", "reply_instructions", "location_website", "call_line"}
    def check(label, text, email):
        for name in re.findall(r"\{(\w+)\}", text or ""):
            if name not in known:
                out.append("%s uses {%s}, which isn't available" % (label, name))
        for pat in prohibited:
            if re.search(pat, text or "", re.I):
                out.append("%s states something not on file (%s)" % (label, re.search(pat, text, re.I).group(0)))
                break
        if email and text:
            if not re.search(brain.CTA_REPLY, text.lower()):
                out.append("%s needs to ask the family to reply" % label)
            if len(re.findall(r"[A-Za-z']+", text)) > 170:
                out.append("%s is over 170 words" % label)
            if "{primary_contact_name}" not in text or "{location_name}" not in text:
                out.append("%s must name {primary_contact_name} and {location_name}" % label)
    for n in range(1, 13):
        t = sms_for(fam, n)
        if t:
            check("Text %d" % n, t, False)
        e = email_for(fam, n)
        if e:
            check("Email %d" % n, e["subject"] + "\n" + e["body"], True)
    return out


def missing_steps(db: Optional[Session], fam) -> list:
    out = ["Text %d" % n for n, _d, _h in sms_steps(db, fam.organization_id) if sms_for(fam, n) is None]
    out += ["Email %d" % n for n, _d in email_steps() if email_for(fam, n) is None]
    return out


def seed_central_alabama_copy(fam) -> bool:
    """Write the owner's campaign sequence ONCE into blank steps (as a draft:
    the campaign stays OFF). Edits are never overwritten, and a step a person
    later clears is not refilled."""
    d = _copy_json(fam)
    if d.get("seeded"):
        return False
    sms = {n: t for n, t in CA_SMS.items() if sms_for(fam, n) is None}
    email = {n: {"subject": su, "body": bo} for n, (su, bo) in CA_EMAIL.items() if email_for(fam, n) is None}
    set_touch_copy(fam, sms=sms, email=email)
    d = _copy_json(fam)
    d["seeded"] = True
    fam.touch_copy_json = json.dumps(d)
    return True


def ensure_central_alabama(db: Session, organization_id: str):
    """Create the campaign OFF, once, with the owner's message sequence as its
    draft copy. Never switches it on and never overwrites copy a person edited."""
    from app.models.program_models import CampaignFamily
    fam = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == organization_id,
                                           CampaignFamily.key == CENTRAL_ALABAMA_KEY).first())
    if fam is not None:
        # #441: the owner's sequence goes into blank steps once (still OFF).
        if seed_central_alabama_copy(fam):
            db.commit()
        return fam
    fam = CampaignFamily(
        organization_id=organization_id, key=CENTRAL_ALABAMA_KEY, name=CENTRAL_ALABAMA_NAME,
        source_campaign_patterns=json.dumps(CENTRAL_ALABAMA["patterns"]),
        asset_category=CENTRAL_ALABAMA["asset_category"], language="en",
        first_touch_email_mode="none", followup_email_mode="none",
        sms_template=None, email_subject_template=None, email_body_template=None,
        is_active=False)
    seed_central_alabama_copy(fam)
    db.add(fam)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - another request created it first
        db.rollback()
        fam = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == organization_id,
                                               CampaignFamily.key == CENTRAL_ALABAMA_KEY).first())
    return fam


def ensure_kilgroe_for_sci(db: Session, organization_id: str):
    """Owner-selected brand-only Kilgroe routing for the Central Alabama POC.

    The source says only 'Kilgroe Funeral Home', not Pell City vs Leeds. Use
    the actual published Pell City telephone, but deliberately do NOT assert
    that all families selected the Pell City physical branch or invent an
    address, hours or an advisor. This is an idempotent program location.
    """
    from app.models.location_models import Location
    from app.models.program_models import LocationProfile
    profile = (db.query(LocationProfile)
               .filter(LocationProfile.organization_id == organization_id,
                       LocationProfile.official_name == "Kilgroe Funeral Home").first())
    if profile is not None:
        return profile
    loc = (db.query(Location)
           .filter(Location.organization_id == organization_id,
                   Location.slug == "kilgroe-central-alabama").first())
    if loc is None:
        loc = Location(organization_id=organization_id, name="Kilgroe Funeral Home",
                       slug="kilgroe-central-alabama", phone="+12053383341",
                       state="AL", notes="Central Alabama POC: brand-only routing; physical branch unverified")
        db.add(loc)
        db.flush()
    profile = LocationProfile(
        organization_id=organization_id, location_id=loc.id,
        official_name="Kilgroe Funeral Home",
        source_names=json.dumps(["Kilgroe Funeral Home"]),
        facility_phone="+12053383341", is_review_bucket=False)
    db.add(profile)
    db.commit()
    return profile


def ensure_for_sci(db: Session) -> Optional[str]:
    from app.services import sms_programs
    org_id = sms_programs.sci_org_id(db)
    if org_id:
        ensure_central_alabama(db, org_id)
        ensure_kilgroe_for_sci(db, org_id)
    return org_id


def ai_notes_for(campaign_family: Optional[str]) -> Optional[Dict[str, str]]:
    return AI_NOTES.get(campaign_family or "")


def activation_blocker(fam, db: Optional[Session] = None) -> Optional[str]:
    """Why this campaign may not be switched on yet, or None."""
    if fam.key in OWN_COPY_KEYS:
        missing = missing_steps(db, fam)
        if missing:
            return ("Every step of this campaign needs its own wording before it can be switched on. "
                    "Blank: %s." % ", ".join(missing))
        return None
    if not (fam.sms_template or "").strip() or not (fam.email_body_template or "").strip() \
            or not (fam.email_subject_template or "").strip():
        return "This campaign has no approved text and email copy yet, so it can't be switched on."
    return None
