"""Consumer Leads launch safeguards (#619): who collects the information, the
privacy notice, the two-part consent, licensed-agent routing, and the
readiness check that public intake must pass before it can be turned on.

Nothing here invents facts. The collecting company, its address and privacy
contact, and every agent's license come from the owner through the audited
settings screen; until they exist the privacy notice says so, the consent names
no company, and intake cannot be switched on.
"""
import hashlib
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.volt_leads_models import VoltLeadGrant, VoltLeadSettings

CONSENT_VERSION = "2026-11-v3"
PRIVACY_VERSION = "2026-11-v2"
PRIVACY_UPDATED = "November 15, 2026"
PRIVACY_PATH = "/volt/check-coverage/privacy"
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
_NPN = re.compile(r"^\d{4,10}$")
NOT_SET = "[collecting company not set]"


def _bad(field: str, msg: str):
    raise HTTPException(status_code=422, detail={"field": field, "message": msg})


# ── the collecting company ──────────────────────────────────────────────────

def collector(row: Optional[VoltLeadSettings]) -> Optional[Dict[str, str]]:
    """The legal company that collects the information, or None until the
    owner has entered its name, mailing address and privacy contact email."""
    if row is None or not (row.collector_name and row.collector_address and row.privacy_email):
        return None
    return {"name": row.collector_name, "address": row.collector_address, "email": row.privacy_email,
            "phone": row.privacy_phone or None}


def set_collector(db: Session, row: VoltLeadSettings, actor: User, body: Dict[str, Any], audit) -> Dict[str, Any]:
    """Owner only (checked by the caller). Changing who collects the data
    turns intake OFF: the consent people agreed to named the old company."""
    name = re.sub(r"\s+", " ", str(body.get("collector_name") or "")).strip()[:160]
    addr = re.sub(r"[ \t]+", " ", str(body.get("collector_address") or "")).strip()[:300]
    email = str(body.get("privacy_email") or "").strip().lower()[:200]
    phone = re.sub(r"[^\d+()\-. ]", "", str(body.get("privacy_phone") or "")).strip()[:30]
    if len(name) < 3:
        _bad("collector_name", "Enter the company's full legal name.")
    if len(addr) < 10:
        _bad("collector_address", "Enter the company's mailing address.")
    if not _EMAIL.match(email):
        _bad("privacy_email", "Enter an email address people can use for privacy requests.")
    old = (row.collector_name, row.collector_address, row.privacy_email, row.privacy_phone)
    new = (name, addr, email, phone or None)
    if old != new:
        if row.intake_enabled and row.collector_name and row.collector_name != name:
            row.intake_enabled = False
            audit("intake_enabled", True, False, "turned off: collecting company changed")
        row.collector_name, row.collector_address, row.privacy_email, row.privacy_phone = new
        row.collector_updated_by, row.collector_updated_at = actor.id, datetime.utcnow()
        audit("collector", old[0], name, "privacy contact %s" % email)
    return collector(row)


# ── consent ─────────────────────────────────────────────────────────────────

def consent_texts(c: Optional[Dict[str, str]]) -> Tuple[str, str]:
    """(required request consent, OPTIONAL automated-contact consent).

    The required box covers only the person's own request: a licensed agent
    contacting them about it. Automated calls and texts are a separate, unticked
    box that is never a condition of getting help."""
    who = c["name"] if c else NOT_SET
    contact = (
        "I am asking %s to pass my request for help with health coverage options to a licensed insurance agent "
        "or agency it works with. %s and that licensed agent or agency may contact me about this request by phone, "
        "text message or email, using the details I entered. %s does not sell insurance. This is not a requirement "
        "to buy anything. I can stop contact at any time by telling the caller, replying STOP to a text, or using "
        "the privacy contact in the privacy notice." % (who, who, who))
    automated = (
        "Optional: I also agree that %s and the licensed insurance agents or agencies it works with may call or text me at the phone "
        "number I entered about health coverage using automated technology, including autodialed or prerecorded "
        "calls and automated text messages. This consent is not a condition of any purchase or of getting help. "
        "Message frequency varies. Message and data rates may apply. Reply STOP to opt out." % who)
    return contact, automated


def consent_hash(contact: str, automated: str, automated_given: bool) -> str:
    """sha256 of exactly what the person agreed to: the request consent, plus
    the automated-contact wording only if they ticked it."""
    return hashlib.sha256((contact + ("\n" + automated if automated_given else "")).encode()).hexdigest()


# ── privacy notice ──────────────────────────────────────────────────────────

def privacy_notice(c: Optional[Dict[str, str]], brand: str) -> Dict[str, Any]:
    if c is None:
        return {"available": False, "version": PRIVACY_VERSION, "title": "Privacy notice",
                "message": "This notice is being finished. Online requests stay closed until it is published."}
    who = c["name"]
    contact = "Email %s%s, or write to %s at %s." % (
        c["email"], (" or call %s" % c["phone"]) if c.get("phone") else "", who, c["address"])
    s = [
        ["Who we are", [
            "%s is a working name used by %s (\"we\", \"us\"). We collect requests from people looking for help "
            "with health coverage and pass them to licensed insurance agents or agencies we work with. We do not sell "
            "insurance and we do not give insurance advice. %s collects the information on this website and is "
            "responsible for it." % (brand, who, who),
            "This is not a government website and we are not affiliated with HealthCare.gov or any government agency."]],
        ["What we collect", [
            "What you type into the form: first and last name, phone number, email address, ZIP code and state.",
            "Your answers to the questions: age range, household size, household income range, current coverage, "
            "how and when you prefer to be contacted.",
            "Your consent: the exact wording you agreed to, its version, the date and time, and whether you also agreed "
            "to automated calls and texts.",
            "How you reached us: the advertising campaign and source in the web address (for example utm_source), "
            "the website that sent you (its domain only) and the page you arrived on.",
            "We do not ask for your Social Security number, medical history or health conditions on this website."]],
        ["Why we collect it", [
            "To answer your request: to understand what kind of coverage help you are looking for and pass your request "
            "to a licensed insurance agent or agency who can contact you about it.",
            "To keep records required for insurance and telemarketing rules, including proof of your consent and any "
            "request to stop contact.",
            "To measure which of our campaigns bring people to this website."]],
        ["Who may contact you", [
            "Our staff, to confirm your request, and the licensed insurance agents or agencies we pass it to, using the "
            "contact method you chose. Automated calls or automated text messages are used only if you ticked that "
            "separate, optional box.",
            "Submitting the form does not enroll you in any plan."]],
        ["Who we share it with", [
            "Licensed insurance agents or agencies we work with, so they can contact you about your request. We may be "
            "paid by them for passing on requests.",
            "Service providers that run our systems for us under contract, such as website and database hosting, "
            "email, and our internal record-keeping spreadsheets. They may use the information only to provide those "
            "services to us.",
            "If you choose to apply for a plan, the information needed for that application goes to the insurance "
            "company or marketplace you choose, with your permission.",
            "When the law requires it, for example in response to a valid legal request.",
            "We do not share your information with anyone else for their own marketing."]],
        ["How long we keep it", [
            "We keep your request while we are helping you and for as long as insurance and telemarketing record-keeping "
            "rules require. After that we delete it or remove what identifies you.",
            "A request to stop contact is kept so that we keep honoring it."]],
        ["Your choices and rights", [
            "You can stop contact at any time: tell the caller, reply STOP to a text, or use the contact details below.",
            "You can ask what information we hold about you, ask us to correct it, or ask us to delete it. Some state laws "
            "give you additional rights; we honor requests as those laws require. We will not treat you differently for "
            "making a request.",
            "We may need to confirm your identity before acting on a request."]],
        ["Security", ["Access to your information is limited to the people who need it to help you, and every access "
                      "to lead records is logged."]],
        ["Children", ["This website is for adults. We do not knowingly collect information from anyone under 18."]],
        ["Changes to this notice", ["If we change this notice we will update the date below. The version you agreed to "
                                    "is kept with your request."]],
        ["Contact us", [contact]],
    ]
    return {"available": True, "version": PRIVACY_VERSION, "updated": PRIVACY_UPDATED,
            "title": "Privacy notice", "collector": who, "sections": s}


# ── licensed agents ─────────────────────────────────────────────────────────

def states_of(g: Optional[VoltLeadGrant]) -> List[str]:
    if g is None or not g.license_verified_at or not g.licensed_states:
        return []
    return [s for s in g.licensed_states.split(",") if s]


def license_dict(g: Optional[VoltLeadGrant]) -> Dict[str, Any]:
    return {"npn": getattr(g, "license_npn", None), "states": states_of(g),
            "verified": bool(g is not None and g.license_verified_at),
            "verified_at": g.license_verified_at.isoformat() + "Z" if g is not None and g.license_verified_at else None}


def set_license(db: Session, org_id: str, actor: User, uid: str, body: Dict[str, Any], valid_states, audit) -> Dict[str, Any]:
    """Owner only (checked by the caller): records an agent's National Producer
    Number and the states where the owner has verified a health insurance
    license. Clearing it removes the agent from assignment of real leads."""
    g = db.get(VoltLeadGrant, (org_id, uid))
    if g is None or not g.active:
        _bad("user_id", "Add this person to the Consumer Leads team first.")
    if body.get("clear") is True:
        old = g.licensed_states
        g.license_npn = g.licensed_states = g.license_verified_by = None
        g.license_verified_at = None
        audit(uid, "license_cleared", old, None)
        return license_dict(g)
    npn = re.sub(r"\D", "", str(body.get("npn") or ""))
    states = sorted({str(s).upper() for s in (body.get("states") or [])})
    if not _NPN.match(npn):
        _bad("npn", "Enter the agent's National Producer Number (digits only).")
    bad = [s for s in states if s not in valid_states]
    if not states or bad:
        _bad("states", "Choose the states where the license is verified.")
    if body.get("verified") is not True:
        _bad("verified", "Confirm that you checked this license yourself (for example on NIPR or the state's lookup).")
    old = g.licensed_states
    g.license_npn, g.licensed_states = npn, ",".join(states)
    g.license_verified_by, g.license_verified_at = actor.id, datetime.utcnow()
    audit(uid, "license_verified", old, g.licensed_states)
    return license_dict(g)


def licensed_agents(db: Session, org_id: str) -> List[VoltLeadGrant]:
    return [g for g in db.query(VoltLeadGrant).filter(VoltLeadGrant.organization_id == org_id,
                                                      VoltLeadGrant.active.is_(True)).all() if states_of(g)]


def check_assignable(db: Session, org_id: str, target: str, state: Optional[str]) -> None:
    """A real lead may only go to someone whose license the owner verified for
    the lead's state. Otherwise it stays unassigned, awaiting a licensed agent."""
    g = db.get(VoltLeadGrant, (org_id, target))
    st = states_of(g if g is not None and g.active else None)
    if not st:
        _bad("user_id", "This person has no verified insurance license on file, so real leads can't be assigned to them.")
    if not state or state not in st:
        _bad("user_id", "This person's verified license does not cover %s." % (state or "this lead's state"))


# ── readiness ───────────────────────────────────────────────────────────────

def readiness(db: Session, row: VoltLeadSettings) -> List[Dict[str, Any]]:
    """Each launch safeguard, met or not, with what is missing. Public intake
    can be turned on only when every item is met."""
    c = collector(row)
    return [
        {"key": "collector", "ok": c is not None, "label": "Collecting company named (legal name, mailing address, privacy contact)",
         "detail": c["name"] if c else "Not entered yet."},
        {"key": "privacy", "ok": c is not None, "label": "Privacy notice published at %s" % PRIVACY_PATH,
         "detail": "Version %s" % PRIVACY_VERSION if c else "Needs the collecting company."},
        {"key": "consent", "ok": c is not None, "label": "Consent names the company; automated calls/texts are a separate optional box",
         "detail": "Version %s" % CONSENT_VERSION if c else "Needs the collecting company."},
    ]
