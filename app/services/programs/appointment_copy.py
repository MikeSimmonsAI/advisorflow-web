"""SCI appointment-first outreach copy (#378).

THE MISSION IS TO SET THE APPOINTMENT. Kevin Quarles (Striffler-Hamby, Oct 23)
got a touch-1 text about his veteran benefits ("When's a good time to make sure
you receive all of your benefits?") and, five minutes later, a touch-1 email
asking "Would you like me to send it over?". Neither asked for an appointment.
Both came from the campaign family's own copy (program_campaign_families), and
the follow-up texts 2, 4 and 6 of the program cadence ("SCI 10-Day
Aggressive") were information-only too.

The information (veteran guide, planning guide, survey, seminar certificate)
starts the conversation; every message then asks for a short appointment and
names only the ways THIS location meets families ({appointment_options}:
at the location, at home when "home_visits" is on, by phone when
"offer_phone_alternative" is on). Texts and emails carry the family's own
pick-a-time link ({booking_line}) where the sender may carry links.

upgrade() replaces copy ONLY where it still holds the exact pre-#378 wording
below; anything a person has since edited is left alone and reported.
"""
import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

ASK_SMS = "I can meet you {appointment_options} - what day works for you?"
EMAIL_ASK = ("The easiest way is a short visit. I can meet with you {appointment_options}, go over it "
             "together and answer your questions, with no decisions needed.")
EMAIL_CLOSE = "{booking_line}Or just reply with a day that works and I'll set it up."


def _sms(opening: str, extra: str = "") -> str:
    return "Hi {first_name}, this is {primary_contact_name} with {location_name}. %s %s%s {call_line}\n{booking_line}" % (
        opening, ASK_SMS, (" " + extra) if extra else "")


def _email(opening: str) -> str:
    return ("Hi {first_name},\n\nIt's {primary_contact_name} at {location_name}. %s\n\n{flyer_link}\n\n%s\n\n%s"
            "\n\n{primary_contact_name}" % (opening, EMAIL_ASK, EMAIL_CLOSE))


_VET_SMS = _sms("A while back you requested a Veteran Benefits Guide from us. I'd like to go over it with you, "
                "along with the benefits that may apply to your family.", "Thank you for your service.")
_VET_EMAIL = _email("A while back you asked us about veteran planning information, and I want to make sure you "
                    "get it and know which benefits may apply to your family.")

# key -> (touch-1 text, touch-1 email body)
FAMILY_COPY: Dict[str, Dict[str, str]] = {
    "veteran_planning_guide": {"sms": _VET_SMS, "email": _VET_EMAIL},
    "veteran_official": {"sms": _VET_SMS, "email": _VET_EMAIL},
    "veteran_spanish": {
        "sms": _sms("You asked us about veteran planning information, and I'd like to go over it with you."),
        "email": _VET_EMAIL},
    "general_survey": {
        "sms": _sms("Thank you for returning our planning survey. I'd like to go over your answers and share a few "
                    "options most families don't know they have."),
        "email": _email("A while back you sent back one of our planning surveys, and I'd like to go over your "
                        "answers with you and share a few options most families don't know they have.")},
    "life_story": {
        "sms": _sms("A while back you asked us about a Life Story for your family. I'd be glad to show you how it "
                    "works and help you get started."),
        "email": _email("A while back you asked us about a Life Story for your family, and I'd be glad to show you "
                        "how it works and help you get started.")},
    "cemetery_x_sell": {
        "sms": _sms("I'd be glad to walk you through the cemetery planning options here."),
        "email": _email("I'd be glad to walk you through the cemetery planning options here - many families find "
                        "it much easier to decide once they've seen them.")},
    "cremation": {
        "sms": _sms("A while back you asked us about cremation information. I'd like to get it to you and walk "
                    "you through the options."),
        "email": _email("A while back you asked us about cremation information, and I'd like to get it to you and "
                        "walk you through the options.")},
    "seminar": {
        "sms": _sms("You signed up for one of our seminars, and I'm still holding your savings certificate and "
                    "personal planning guide. I'd like to go over them with you."),
        "email": _email("A while back you signed up for one of our planning seminars, and I'm still holding your "
                        "savings certificate and planning guide for you. I'd like to go over them with you.")},
    "web_lead": {
        "sms": _sms("You asked for information on our website, and I'd be glad to go over it with you."),
        "email": _email("A while back you asked for information on our website, and I'd be glad to go over it "
                        "with you.")},
    "re_engagement": {
        "sms": _sms("A while back you were in touch with us about planning, and I'd still be glad to help you "
                    "get started."),
        "email": _email("A while back you were in touch with us about planning, and I'd still be glad to sit down "
                        "with you and help you get started.")},
}

# Program cadence ("SCI 10-Day Aggressive") follow-up texts, by touch number.
PROGRAM_TOUCHES: Dict[int, str] = {
    2: ("Hi {first_name}, {primary_contact_name} with {location_name} again. I'd like to get your planning "
        "information to you and go over it together - it only takes a short visit. I can meet with you "
        "{appointment_options}. What day works for you?\n{booking_line}"),
    3: ("{first_name}, quick question from {location_name}: could we set up a short visit this week? I can "
        "meet with you {appointment_options}. {reply_instructions}\n{booking_line}"),
    4: ("Hi {first_name}, {primary_contact_name} here. Many families tell us they feel real relief once their "
        "wishes are written down. I'd be glad to help you get started in a short visit - I can meet with you "
        "{appointment_options}. What day works best?\n{booking_line}"),
    5: ("{first_name}, I have a few openings at {location_name} this week and I'd like to hold one for you. I "
        "can meet with you {appointment_options}, whichever is easier. {reply_instructions}\n{booking_line}"),
    6: ("Hi {first_name}, {primary_contact_name} at {location_name}. I'd still like to get a short visit on the "
        "calendar with you - I can meet with you {appointment_options}. Is morning or afternoon better for you?"
        "\n{booking_line}"),
}

# ── the exact pre-#378 wording in production (Oct 24) ───────────────────────
_OLD_VET_SMS = ("Hi {first_name}, this is {primary_contact_name} with {location_name}. A while back you requested a "
                "Veteran Benefits Guide from us, and our records show we never got connected. When's a good time to "
                "make sure you receive all of your benefits? Thank you for your service. I look forward to talking "
                "with you soon. {call_line}")


def _old_email(opening: str, ask: str) -> str:
    return ("Hi {first_name},\n\nIt's {primary_contact_name} at {location_name}. %s\n\n%s\n\n{primary_contact_name}"
            % (opening, ask))


_SEND_IT = "Would you like me to send it over? Just reply yes and I'll take care of it."
_ANYTHING = "Is there anything I can help you with? Just reply to this email and I'll get back to you myself."
OLD_FAMILY_COPY: Dict[str, Dict[str, str]] = {
    "veteran_planning_guide": {"sms": _OLD_VET_SMS, "email": _old_email(
        "A while back you asked us about veteran planning information, and I want to make sure it actually got to "
        "you.", _SEND_IT)},
    "veteran_official": {"sms": _OLD_VET_SMS, "email": _old_email(
        "A while back you asked us about veteran benefits information, and I want to make sure it actually got to "
        "you.", _SEND_IT)},
    "veteran_spanish": {
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name} about your veteran planning "
                "request. {call_line}"),
        "email": _old_email("A while back you asked us about veteran planning information, and I want to make sure "
                            "it actually got to you.", _SEND_IT)},
    "general_survey": {
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name}. Thank you for returning our "
                "planning survey. I'd like to go over your answers and share a few options most families don't "
                "know they have. Would a quick 10-minute call be better in the morning or the afternoon? "
                "{call_line}"),
        "email": _old_email("A while back you sent back one of our planning surveys, and I wanted to thank you "
                            "personally.", "Do you have any questions I can answer for you? Just reply to this email "
                                           "and I'll get back to you myself.")},
    "life_story": {
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name}, following up on your Life "
                "Story request. {call_line}"),
        "email": _old_email("A while back you asked us about a Life Story for your family, and I want to make sure "
                            "we followed up.", "Would you like me to send you the details? Just reply yes and I'll "
                                               "take care of it.")},
    "cemetery_x_sell": {
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name}. I'd be glad to share cemetery "
                "planning options with you. {call_line}"),
        "email": _old_email("I wanted to reach out personally in case you have any questions about planning here.",
                            _ANYTHING)},
    "cremation": {
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name}, following up on your "
                "cremation information request. {call_line}"),
        "email": _old_email("A while back you asked us about cremation information, and I want to make sure it "
                            "actually got to you.", _SEND_IT)},
    "seminar": {
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name}. I'm reaching out because you "
                "signed up for one of our seminars in the past. I'm still holding on to your savings certificate "
                "and the personal planning guide we ordered for you. What's the best time and way to get these to "
                "you? Thanks! {call_line}"),
        "email": _old_email("A while back you signed up for one of our planning seminars, and I'm still holding "
                            "your planning guide for you.", _SEND_IT)},
    "web_lead": {
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name}, following up on the "
                "information you requested on our website. When's a good time to connect? {call_line}"),
        "email": _old_email("A while back you asked for information on our website, and I want to make sure it "
                            "actually got to you.", _SEND_IT)},
    "re_engagement": {
        "sms": "Hi {first_name}, {primary_contact_name} with {location_name} checking in. {call_line}",
        "email": _old_email("A while back you were in touch with us about planning, and I wanted to make sure you "
                            "got everything you needed.", _ANYTHING)},
}

OLD_PROGRAM_TOUCHES: Dict[int, str] = {
    2: ("Hi {first_name}, {primary_contact_name} with {location_name} again. I can get your planning information "
        "over to you today - it only takes a minute. {reply_instructions}"),
    3: ("{first_name}, quick question from {location_name}: could you stop by for a short visit this week? "
        "{reply_instructions}\n{booking_line}"),
    4: ("Hi {first_name}, {primary_contact_name} here. Many families tell us they feel real relief once their "
        "wishes are written down. I'd be glad to help you get started. {reply_instructions}"),
    5: ("{first_name}, I have a few openings at {location_name} this week. Would you like me to hold one for you? "
        "{reply_instructions}\n{booking_line}"),
    6: ("Hi {first_name}, this is my last note for now from {location_name}. Whenever you're ready, I'm here to "
        "help. {reply_instructions}\n{booking_line}"),
}


def upgrade(db: Session, organization_id: str, *, dry_run: bool = True) -> Dict:
    """Replace pre-#378 copy with appointment-first copy for one program.
    Only exact pre-#378 text is replaced; edited copy is kept and listed.
    Returns {"changed": [...], "kept_edited": [...]} - nothing is written on a dry run."""
    from app.models.models import CadenceTemplate, CadenceTemplateTouch
    from app.models.program_models import CampaignFamily
    from app.services.programs.identity import PROGRAM_CADENCE_INDUSTRY
    changed: List[str] = []
    kept: List[str] = []
    for fam in db.query(CampaignFamily).filter(CampaignFamily.organization_id == organization_id).all():
        new, old = FAMILY_COPY.get(fam.key), OLD_FAMILY_COPY.get(fam.key)
        if not new or not old:
            continue
        for field, col in (("sms", "sms_template"), ("email", "email_body_template")):
            cur = getattr(fam, col) or ""
            if cur == new[field]:
                continue
            if cur == old[field]:
                changed.append("%s.%s" % (fam.key, field))
                if not dry_run:
                    setattr(fam, col, new[field])
            else:
                kept.append("%s.%s" % (fam.key, field))
    tpl = (db.query(CadenceTemplate)
           .filter(CadenceTemplate.organization_id == organization_id, CadenceTemplate.is_default.is_(True),
                   CadenceTemplate.industry == PROGRAM_CADENCE_INDUSTRY).first())
    if tpl is not None:
        for t in db.query(CadenceTemplateTouch).filter(CadenceTemplateTouch.template_id == tpl.id).all():
            new, old = PROGRAM_TOUCHES.get(t.touch_number), OLD_PROGRAM_TOUCHES.get(t.touch_number)
            if not new or (t.message_template or "") == new:
                continue
            if (t.message_template or "") == old:
                changed.append("cadence.touch%d" % t.touch_number)
                if not dry_run:
                    t.message_template = new
            else:
                kept.append("cadence.touch%d" % t.touch_number)
    if not dry_run and changed:
        db.commit()
        log.info("#378 appointment-first copy applied for org %s: %s", organization_id, changed)
    if kept:
        # Copy a person edited is never overwritten - but say so, so information-
        # only copy can't hide here unnoticed.
        log.warning("#378 copy NOT replaced (edited by a person) for org %s: %s", organization_id, kept)
    return {"changed": changed, "kept_edited": kept, "dry_run": dry_run}


def apply_for_sci(db: Session) -> Optional[Dict]:
    """The SCI program's upgrade (idempotent: a second run changes nothing)."""
    from app.services import sms_programs
    org_id = sms_programs.sci_org_id(db)
    if not org_id:
        return None
    return upgrade(db, org_id, dry_run=False)
