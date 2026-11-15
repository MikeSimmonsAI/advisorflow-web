"""VOLT consumer leads (DEV-4): intake, assignment, status history, reports.

Authorization lives here, not in the router. Every read, write, search and
report goes through `scope()`, which is the ONLY place a lead query is built:

    owner (god_admin)                       all leads of the tenant
    org staff WITH an active owner grant    all leads of the tenant
    org staff WITHOUT a grant               only leads assigned to them
    viewers, ACA applicant logins, others   nothing (403)

Another tenant's leads are never reachable: every query filters on the
organization id from the path, which the router's guard already validated.

Personal data: lead events, audit rows, log lines and public responses never
contain names, phone numbers, emails or ZIP codes.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.volt_leads_models import VoltLead, VoltLeadEvent, VoltLeadGrant, VoltLeadSettings
from app.services.volt import common as C

# ── content that needs compliance review before real traffic ────────────────
CONSENT_VERSION = "2026-11-draft-1"
CONSENT_TEXT = (
    "I agree that a licensed agent working with VOLT may contact me by phone, text message or email "
    "about health coverage options, including with an automated dialer. Consent is not required to "
    "get coverage or help. Message and data rates may apply. I can withdraw consent at any time."
)
ELIGIBILITY_CAVEAT = (
    "Some people qualify for plans with a $0 monthly premium after financial assistance. That does not "
    "mean all healthcare is free, and not everyone qualifies. Eligibility depends on location, household "
    "income, household size and other coverage. This is not a government website."
)
# Shown on the staff screen: everything the owner must clear before turning intake on.
REVIEW_CHECKLIST = [
    "Privacy notice and consent wording (CONSENT_TEXT, CONSENT_VERSION) approved by compliance",
    "$0-premium / savings eligibility claims approved by compliance",
    "Agent routing and the licensed-agent list confirmed",
    "TCPA / state telemarketing rules checked for the call and text consent",
]

# Consumer page copy (also needs the same compliance review as the consent).
PAGE = {
    # V4 design (docs/volt/design/consumer_leads_v4_design_demo.html, owner-approved direction 2026-11-14).
    # "CoveragePath" is a TEMPORARY working name, not a registered brand: change it here only.
    "brand": "CoveragePath",
    "brand_note": "CoveragePath is a working name for an independent health coverage help service.",
    "eyebrow": "Coverage help, made simple",
    "headline": "Health coverage that fits your life and budget.",
    "headline_accent": "fits your life",
    "subhead": "You may qualify for health insurance with a $0 monthly premium. See what options might be available in your area.",
    "cta": "Check My Options",
    "trust": ["Free to check", "No obligation", "No account required", "Help from licensed agents"],
    "highlights": [
        ["Plans worth exploring", "Understand what may be available where you live"],
        ["Possible monthly savings", "Financial help may lower your premium"],
        ["Guidance from a person", "A licensed agent can explain your options"],
    ],
    "benefits_title": "Coverage for the moments that matter.",
    "benefits_intro": "Health plans can include benefits that help you take care of yourself and your family. What is covered, and what you pay, depends on the plan.",
    "benefits": [
        ["Potential premium savings", "Financial help can lower what you pay each month. For some people it brings the premium to $0, but not for everyone."],
        ["Doctor visits", "Plans can help with check-ups and visits when you are sick. What you pay depends on the plan and its network."],
        ["Prescription coverage", "Plans include coverage for medicines. Your cost depends on the plan and the medicine."],
        ["Preventive care", "Many check-ups, screenings and vaccines are covered at no cost when you use the plan's network."],
        ["Emergency benefits", "Plans cover emergency care. Deductibles, copays or coinsurance may apply."],
        ["Human guidance", "A licensed agent can explain options and answer questions, at no cost to you."],
    ],
    "steps_title": "Three steps. That's it.",
    "steps_intro": "Clear and simple from the first question to the last.",
    "steps": [
        ["Tell us a little about yourself", "Start with your ZIP code and answer a few basic questions."],
        ["Explore possible options", "Find out whether savings or coverage options may be available for your situation."],
        ["Get help from a person", "Ask a licensed agent to reach out and explain your options."],
    ],
    "faq_title": "Answers without the jargon.",
    "faq": [
        ["Does it cost anything to check?", "No. Checking your options is free, and there is no obligation to enroll. "
         "Plan premiums and out-of-pocket costs depend on the coverage you choose."],
        ["Is health insurance really $0 a month?", "Some people qualify for plans with a $0 monthly premium after financial "
         "assistance. Eligibility is not guaranteed, and deductibles, copays and other costs may still apply."],
        ["Do I have to sign up right now?", "No. These questions do not enroll you in anything. They help a licensed agent "
         "understand what may be available to you."],
        ["What if I'm 65 or older?", "Medicare may be the right fit, and it works differently from these plans. "
         "A licensed agent can explain your options. This website does not decide eligibility."],
        ["Is this a government website?", "No. This is an independent health coverage help service. It is not a "
         "government website and is not affiliated with HealthCare.gov or any government agency."],
    ],
    "final_title": "See what options might fit your budget.",
    "final_text": "No account, no pressure, no obligation.",
    "disclaimers": [
        "Not everyone qualifies. Eligibility depends on where you live, your household income and size, and other coverage you have.",
        "A $0 monthly premium is not guaranteed.",
        "A $0 monthly premium does not mean all care is free. Deductibles, copays and coinsurance may still apply.",
        "This is not a government website and is not affiliated with HealthCare.gov or any government agency.",
    ],
    "medicare_note": ("If you are 65 or older, Medicare may be a better fit, and it works differently from these plans. "
                      "A licensed agent can explain your options. You can still continue."),
}

STATES = ("AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
          "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR",
          "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY")
INCOME_RANGES = ("under_20k", "20k_35k", "35k_50k", "50k_75k", "75k_100k", "over_100k", "prefer_not")
CONTACT_PREFS = ("call", "text", "email")
BEST_TIMES = ("morning", "afternoon", "evening", "any")
CHANNELS = ("meta", "google", "organic", "direct", "referral", "other")
CHANNEL_LABELS = {"meta": "Meta / Facebook", "google": "Google", "organic": "Organic search", "direct": "Direct",
                  "referral": "Referral", "other": "Other"}
# Events an agent can record on a lead (beyond status/assignment/notes).
CONTACT_EVENTS = {"call_attempted": "Call attempted", "contact_completed": "Contact completed",
                  "appointment_set": "Appointment set", "appointment_changed": "Appointment changed",
                  "enrollment_recorded": "Enrollment recorded", "follow_up_scheduled": "Follow-up scheduled"}
GRANT_ROLES = ("manager", "agent")
HOME_SLUG = "volt-marketing"   # the VOLT program that owns the Consumer Leads business

AGE_RANGES = ("18-25", "26-44", "45-64", "65+")
COVERAGE = ("none", "have_compare", "not_sure")
SOURCES = ("facebook", "google", "direct", "other")
STATUSES = ("new", "contacted", "qualified", "appointment_set", "enrolled", "not_interested")
STATUS_LABELS = {"new": "New", "contacted": "Contacted", "qualified": "Qualified",
                 "appointment_set": "Appointment Set", "enrolled": "Enrolled", "not_interested": "Not Interested"}
MAX_PAGE = 200
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
_CAMPAIGN = re.compile(r"[^A-Za-z0-9._\- ]")


# ── settings ────────────────────────────────────────────────────────────────

def settings_row(db: Session, org_id: str) -> VoltLeadSettings:
    row = db.get(VoltLeadSettings, org_id)
    if row is None:
        row = VoltLeadSettings(organization_id=org_id, intake_enabled=False, updated_at=datetime.utcnow())
        db.add(row)
        db.flush()
    return row


def public_config(db: Session, org_id: str) -> Dict[str, Any]:
    row = db.get(VoltLeadSettings, org_id)
    return {"enabled": bool(row and row.intake_enabled), "consent_version": CONSENT_VERSION,
            "consent_text": CONSENT_TEXT, "eligibility_caveat": ELIGIBILITY_CAVEAT, "page": PAGE,
            "states": list(STATES)}


def _utc(dt) -> Optional[str]:
    """Stored times are UTC (naive columns, written with utcnow). Always send
    them with the offset so a browser never reads them as local time."""
    if dt is None:
        return None
    from datetime import timezone
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def channel_of(source: str, medium: str, referrer_host: str) -> str:
    src, med, ref = (source or "").lower(), (medium or "").lower(), (referrer_host or "").lower()
    if src in ("facebook", "fb", "meta", "instagram", "ig") or "facebook." in ref or "instagram." in ref:
        return "meta"
    if src in ("google", "adwords", "gads") or med in ("cpc", "ppc", "paid_search") and "google" in (src + ref):
        return "google"
    if src in ("referral", "ref") or med == "referral":
        return "referral"
    if ref and any(e in ref for e in ("google.", "bing.", "duckduckgo.", "yahoo.")):
        return "organic"
    if src in ("organic", "seo") or med == "organic":
        return "organic"
    if not src and not ref:
        return "direct"
    if ref and not src:
        return "referral"
    return "other"


def set_intake(db: Session, org_id: str, actor: User, body: Dict[str, Any]) -> Dict[str, Any]:
    """Owner only (checked by the caller). Turning intake ON needs a review note."""
    row = settings_row(db, org_id)
    enable = bool(body.get("intake_enabled"))
    note = str(body.get("review_note") or "").strip()[:1000]
    if enable and (body.get("reviewed") is not True or len(note) < 10):
        raise HTTPException(status_code=422, detail="Confirm the compliance review and record who approved it before turning intake on.")
    old = row.intake_enabled
    row.intake_enabled = enable
    if enable:
        row.review_note, row.reviewed_by, row.reviewed_at = note, actor.id, datetime.utcnow()
    row.updated_at = datetime.utcnow()
    C.audit(db, org_id, actor, "lead_settings", org_id, "intake_enabled", "intake_enabled", old, enable, note or None)
    return settings_dict(row)


def settings_dict(row: VoltLeadSettings) -> Dict[str, Any]:
    return {"intake_enabled": bool(row.intake_enabled), "review_note": row.review_note,
            "reviewed_at": _utc(row.reviewed_at),
            "consent_version": CONSENT_VERSION, "checklist": REVIEW_CHECKLIST}


# ── intake ──────────────────────────────────────────────────────────────────

def _bad(field: str, msg: str):
    raise HTTPException(status_code=422, detail={"field": field, "message": msg})


def _clean_campaign(v) -> Optional[str]:
    s = _CAMPAIGN.sub("", str(v or "")).strip()[:80]
    return s or None


def _norm_phone(v) -> Optional[str]:
    d = re.sub(r"\D", "", str(v or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 and d[0] in "23456789" else None


def validate(body: Dict[str, Any], strict: bool = True) -> Dict[str, Any]:
    """strict=True for the website (every question answered, consent wording
    version checked). strict=False for external sources (ad forms, CSV): the
    questionnaire answers are optional and consent is checked by the caller."""
    first = str(body.get("first_name") or "").strip()
    if not 1 <= len(first) <= 60:
        _bad("first_name", "Enter your first name.")
    last = str(body.get("last_name") or "").strip()[:60] or None
    zip_code = str(body.get("zip") or "").strip()
    if not re.fullmatch(r"\d{5}", zip_code):
        _bad("zip", "Enter a valid 5-digit ZIP code.")
    phone = _norm_phone(body.get("phone")) if body.get("phone") else None
    if body.get("phone") and phone is None:
        _bad("phone", "Enter a valid 10-digit phone number.")
    email = str(body.get("email") or "").strip().lower() or None
    if email and not _EMAIL.match(email):
        _bad("email", "Enter a valid email address.")
    if not phone and not email:
        _bad("phone", "Enter a phone number or an email address so an agent can reach you.")
    age = body.get("age_range") or (None if not strict else body.get("age_range"))
    if not (age is None and not strict) and age not in AGE_RANGES:
        _bad("age_range", "Choose an age range.")
    cov = body.get("coverage_status") or (None if not strict else body.get("coverage_status"))
    if not (cov is None and not strict) and cov not in COVERAGE:
        _bad("coverage_status", "Choose the answer that best describes you.")
    hh = body.get("household_size")
    if hh in (None, ""):
        hh = None
    else:
        try:
            hh = int(hh)
        except (TypeError, ValueError):
            _bad("household_size", "Household size must be a number.")
        if not 1 <= hh <= 20:
            _bad("household_size", "Household size must be between 1 and 20.")
    state = str(body.get("state") or "").strip().upper() or None
    if state is not None and state not in STATES:
        _bad("state", "Choose your state.")
    income = body.get("income_range") or None
    if income is not None and income not in INCOME_RANGES:
        _bad("income_range", "Choose an income range.")
    pref = body.get("contact_preference") or None
    if pref is not None and pref not in CONTACT_PREFS:
        _bad("contact_preference", "Choose how you'd like to be contacted.")
    if pref in ("call", "text") and not phone:
        _bad("phone", "Enter a phone number so an agent can call or text you.")
    if pref == "email" and not email:
        _bad("email", "Enter an email address so an agent can email you.")
    best = body.get("best_time") or None
    if best is not None and best not in BEST_TIMES:
        _bad("best_time", "Choose a time of day.")
    if body.get("consent") is not True:
        _bad("consent", "Please agree to be contacted so an agent can help you.")
    if strict and body.get("consent_version") != CONSENT_VERSION:
        _bad("consent_version", "The consent wording changed. Reload the page and review it again.")
    def utm(k, n=80):
        return _clean_campaign(body.get(k))[:n] if _clean_campaign(body.get(k)) else None
    utm_source, utm_medium = utm("utm_source") or utm("source"), utm("utm_medium")
    ref = re.sub(r"[^a-z0-9.\-]", "", str(body.get("referrer_host") or "").lower())[:120] or None
    landing = re.sub(r"[^A-Za-z0-9/_\-.]", "", str(body.get("landing_path") or ""))[:120] or None
    chan = channel_of(utm_source or "", utm_medium or "", ref or "")
    legacy = {"meta": "facebook", "google": "google", "direct": "direct"}.get(chan, "other")
    return {"first_name": first, "last_name": last, "zip_code": zip_code, "phone": phone, "email": email,
            "age_range": age, "coverage_status": cov, "household_size": hh,
            "help_wanted": body.get("help_wanted") is not False,
            "source": legacy, "campaign": utm("utm_campaign") or _clean_campaign(body.get("campaign")),
            "state": state, "income_range": income, "contact_preference": pref, "best_time": best,
            "channel": chan, "utm_source": utm_source, "utm_medium": utm_medium, "utm_campaign": utm("utm_campaign"),
            "utm_content": utm("utm_content"), "utm_term": utm("utm_term"), "referrer_host": ref, "landing_path": landing}


def _event(db, lead, actor, action, old=None, new=None, note=None):
    db.add(VoltLeadEvent(lead_id=lead.id, organization_id=lead.organization_id,
                         actor_user_id=getattr(actor, "id", None), action=action,
                         old_value=None if old is None else str(old), new_value=None if new is None else str(new),
                         note=note))


def _dedup_key(org_id: str, ident: str, test: bool) -> str:
    return hashlib.sha256(("%s%s|%s" % ("test|" if test else "", org_id, ident)).encode()).hexdigest()


def create_or_merge(db: Session, org_id: str, v: Dict[str, Any], *, test: bool, actor=None,
                    consent_version: str = CONSENT_VERSION, consent_text: str = CONSENT_TEXT,
                    consent_note: Optional[str] = None, origin: Optional[str] = None,
                    source_id: Optional[str] = None):
    """Every way a lead arrives ends here. Returns (lead, created). A repeat
    from the same phone/email merges into the existing lead (one more
    submission, one more history line) - never a second row. Test and real
    rows are deduplicated separately and never merge into each other."""
    ident = v["phone"] or v["email"]
    key = _dedup_key(org_id, ident, test)
    q = lambda: db.query(VoltLead).filter(VoltLead.organization_id == org_id, VoltLead.dedup_key == key).first()  # noqa: E731
    existing = q()
    if existing is None:
        now = datetime.utcnow()
        lead = VoltLead(organization_id=org_id, dedup_key=key, consent_version=consent_version,
                        consent_text_hash=hashlib.sha256(consent_text.encode()).hexdigest(), consent_at=now,
                        status="new", created_at=now, updated_at=now, is_test=bool(test), source_id=source_id, **v)
        try:
            with db.begin_nested():
                db.add(lead)
                db.flush()
        except IntegrityError:           # a concurrent retry won the race
            existing = q()
        else:
            _event(db, lead, actor, "created", new=v["channel"], note=origin)
            _event(db, lead, actor, "consent_recorded", new=consent_version,
                   note=consent_note or "consent text sha256 %s" % lead.consent_text_hash[:16])
            if not test:
                from app.services.volt import sheets_outbox
                sheets_outbox.enqueue(db, org_id, "consumer", lead.id)
            return lead, True
    existing.submissions = (existing.submissions or 1) + 1
    existing.updated_at = datetime.utcnow()
    _event(db, existing, actor, "repeat_submission", note=origin)
    if not test:
        from app.services.volt import sheets_outbox
        sheets_outbox.enqueue(db, org_id, "consumer", existing.id)
    return existing, False


def submit(db: Session, org_id: str, body: Dict[str, Any]) -> None:
    """Public intake. Same silent outcome for new and repeat submissions: the
    caller learns nothing about whether a person is already on file."""
    cfg = db.get(VoltLeadSettings, org_id)
    if cfg is None or not cfg.intake_enabled:
        raise HTTPException(status_code=503, detail="This form is not accepting requests right now.")
    create_or_merge(db, org_id, validate(body), test=False)


def preview_submit(db: Session, user: User, org_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Signed-in preview of the customer website. Works whether public intake
    is on or off, and writes ONLY to the test partition (is_test=True): it never
    shows in the real inbox, reports or card, and nothing is sent to anyone."""
    if access(db, user, org_id) != "all":
        raise HTTPException(status_code=403, detail="Previewing the customer website needs Consumer Leads manager access.")
    v = validate(body)
    lead, created = create_or_merge(db, org_id, v, test=True, actor=user,
                                    origin="Test submission from the website preview by %s" % (user.full_name or user.email))
    C.audit(db, org_id, user, "lead_preview", lead.id, "test_lead_submitted")
    db.flush()
    return {"ok": True, "test": True, "lead_id": lead.id, "created": created}


def clear_test(db: Session, user: User, org_id: str) -> Dict[str, Any]:
    """Removes test-partition leads only. Real leads cannot be touched here."""
    if access(db, user, org_id) != "all":
        raise HTTPException(status_code=403, detail="Clearing test data needs Consumer Leads manager access.")
    ids = [r[0] for r in db.query(VoltLead.id).filter(VoltLead.organization_id == org_id, VoltLead.is_test.is_(True)).all()]
    if ids:
        db.query(VoltLeadEvent).filter(VoltLeadEvent.lead_id.in_(ids)).delete(synchronize_session=False)
        db.query(VoltLead).filter(VoltLead.id.in_(ids), VoltLead.is_test.is_(True)).delete(synchronize_session=False)
    C.audit(db, org_id, user, "lead_preview", org_id, "test_leads_cleared", None, len(ids))
    db.flush()
    return {"removed": len(ids)}


# ── authorization ───────────────────────────────────────────────────────────

def _grant(db: Session, org_id: str, user: User) -> Optional[VoltLeadGrant]:
    g = db.get(VoltLeadGrant, (org_id, user.id))
    return g if g is not None and g.active else None


def has_grant(db: Session, org_id: str, user: User) -> bool:
    return _grant(db, org_id, user) is not None


def _member(db: Session, user: User, org_id: str) -> bool:
    """Belongs to the VOLT organization (any workspace role) and is not
    suspended or removed in VOLT People & Access."""
    from app.services import workspace_access
    from app.services.volt import access as _access
    if _access.blocked(db, org_id, user):
        return False
    return workspace_access.workspace_role(user, db, org_id) is not None


def is_admin(db: Session, user: User, org_id: str) -> bool:
    """Runs the Consumer Leads business: the owner, or one of VOLT's named
    operators (VOLT_DEV_PRINCIPALS AND an active VOLT admin, re-checked on
    every call - D'Angelo today)."""
    if C.staff_access(db, user, org_id) == "owner":
        return True
    from app.services.volt import devrelay
    return devrelay.is_principal(db, user, org_id)


def access(db: Session, user: User, org_id: str) -> str:
    """'all' | 'assigned'. Raises 403 for everyone else.

    Consumer Leads is its own business inside VOLT Marketing: recruiting roles
    give NO access here. Access comes only from
      * the owner and VOLT's named operators -> every lead;
      * a Consumer Leads grant (role manager -> every lead, role agent ->
        leads assigned to them), held by an active member of the VOLT org."""
    if user is None or not getattr(user, "is_active", False):
        raise HTTPException(status_code=403, detail="Consumer Leads is for authorized staff.")
    if is_admin(db, user, org_id):
        return "all"
    g = _grant(db, org_id, user)
    if g is not None and _member(db, user, org_id):
        return "all" if (g.role or "manager") == "manager" else "assigned"
    raise HTTPException(status_code=403, detail="Consumer Leads is for authorized staff.")


def try_access(db: Session, user: User, org_id: str) -> Optional[str]:
    try:
        return access(db, user, org_id)
    except HTTPException:
        return None


def is_test_param(params) -> bool:
    return str((params or {}).get("test") or "").lower() in ("1", "true", "yes")


def scope(db: Session, user: User, org_id: str, test: bool = False):
    mode = access(db, user, org_id)
    q = db.query(VoltLead).filter(VoltLead.organization_id == org_id, VoltLead.is_test.is_(bool(test)))
    if mode != "all":
        q = q.filter(VoltLead.assigned_user_id == user.id)
    return q, mode


def _lead(db, user, org_id, lead_id, test: bool = False) -> VoltLead:
    q, _ = scope(db, user, org_id, test)
    lead = q.filter(VoltLead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found.")       # same for "exists but not yours"
    return lead


def require_owner(db: Session, user: User, org_id: str) -> None:
    if C.staff_access(db, user, org_id) != "owner":
        raise HTTPException(status_code=403, detail="Only the VOLT owner can do this.")


def require_admin(db: Session, user: User, org_id: str) -> None:
    if not is_admin(db, user, org_id):
        raise HTTPException(status_code=403, detail="Only the owner or a Consumer Leads operator can do this.")


# ── dashboard ───────────────────────────────────────────────────────────────

def _names(db, ids) -> Dict[str, str]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(ids)).all()}


def lead_dict(lead: VoltLead, names: Dict[str, str], detail: bool = False) -> Dict[str, Any]:
    out = {"id": lead.id, "name": ("%s %s" % (lead.first_name, lead.last_name or "")).strip(), "phone": lead.phone,
           "zip": lead.zip_code, "source": lead.source, "status": lead.status,
           "status_label": STATUS_LABELS.get(lead.status, lead.status), "assigned_user_id": lead.assigned_user_id,
           "assigned_to": names.get(lead.assigned_user_id) if lead.assigned_user_id else None,
           "created_at": _utc(lead.created_at), "updated_at": _utc(lead.updated_at),
           "channel": lead.channel or {"facebook": "meta"}.get(lead.source, lead.source),
           "state": lead.state, "appointment_at": _utc(lead.appointment_at),
           "next_follow_up_at": _utc(lead.next_follow_up_at), "test": bool(lead.is_test),
           "campaign": lead.utm_campaign or lead.campaign}
    if detail:
        out.update(email=lead.email, age_range=lead.age_range, coverage_status=lead.coverage_status,
                   household_size=lead.household_size, help_wanted=lead.help_wanted, campaign=lead.campaign,
                   consent_version=lead.consent_version, consent_at=_utc(lead.consent_at),
                   submissions=lead.submissions, income_range=lead.income_range,
                   contact_preference=lead.contact_preference, best_time=lead.best_time,
                   utm={k: getattr(lead, k) for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content",
                                                      "utm_term", "referrer_host", "landing_path")},
                   enrolled_at=_utc(lead.enrolled_at), last_contact_at=_utc(lead.last_contact_at))
    return out


def listing(db: Session, user: User, org_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    test = is_test_param(params)
    q, mode = scope(db, user, org_id, test)
    status, source = params.get("status") or "", params.get("source") or ""
    if status:
        q = q.filter(VoltLead.status == status)
    if source:
        q = q.filter(VoltLead.source == source)
    channel = params.get("channel") or ""
    if channel:
        q = q.filter(VoltLead.channel == channel)
    camp = _clean_campaign(params.get("campaign"))
    if camp:
        q = q.filter(or_(VoltLead.utm_campaign == camp, VoltLead.campaign == camp))
    if params.get("unassigned") in ("1", "true"):
        q = q.filter(VoltLead.assigned_user_id.is_(None))
    term = str(params.get("q") or "").strip()[:60]
    if term:
        like = "%" + term.lower().replace("%", "").replace("_", "") + "%"
        digits = re.sub(r"\D", "", term)
        conds = [VoltLead.first_name.ilike(like), VoltLead.last_name.ilike(like), VoltLead.zip_code.like(like),
                 VoltLead.email.ilike(like)]
        if digits:
            conds.append(VoltLead.phone.like("%" + digits + "%"))
        q = q.filter(or_(*conds))
    try:
        limit = max(1, min(int(params.get("limit") or 100), MAX_PAGE))
    except (TypeError, ValueError):
        limit = 100
    rows = q.order_by(VoltLead.created_at.desc()).limit(limit).all()
    names = _names(db, [r.assigned_user_id for r in rows])
    return {"access": mode, "test": test, "leads": [lead_dict(r, names) for r in rows]}


def detail(db: Session, user: User, org_id: str, lead_id: str, test: bool = False) -> Dict[str, Any]:
    lead = _lead(db, user, org_id, lead_id, test)
    events = db.query(VoltLeadEvent).filter(VoltLeadEvent.lead_id == lead.id).order_by(VoltLeadEvent.created_at).all()
    ids = [lead.assigned_user_id] + [e.actor_user_id for e in events]
    ids += [v for e in events if e.action == "assigned" for v in (e.old_value, e.new_value)]
    names = _names(db, ids)

    def show(e, v):
        if v is None:
            return None
        if e.action == "assigned":
            return names.get(v, "someone")
        if e.action == "status":
            return STATUS_LABELS.get(v, v)
        return v
    out = lead_dict(lead, names, detail=True)
    out["history"] = [{"id": e.id, "action": e.action,
                       "label": CONTACT_EVENTS.get(e.action) or e.action.replace("_", " ").capitalize(),
                       "old": show(e, e.old_value), "new": show(e, e.new_value), "note": e.note,
                       "by": names.get(e.actor_user_id) if e.actor_user_id else "Visitor",
                       "at": _utc(e.created_at)} for e in events]
    return out


def _when(body, key, label):
    from datetime import timezone
    try:
        when = datetime.fromisoformat(str(body.get(key) or "").replace("Z", "+00:00"))
    except ValueError:
        _bad(key, "Give the %s date and time." % label)
    return when.astimezone(timezone.utc).replace(tzinfo=None) if when.tzinfo else when


def update(db: Session, user: User, org_id: str, lead_id: str, body: Dict[str, Any], test: bool = False) -> Dict[str, Any]:
    lead = _lead(db, user, org_id, lead_id, test)
    status = body.get("status")
    if status is not None:
        if status not in STATUSES:
            _bad("status", "Unknown status.")
        if status != lead.status:
            _event(db, lead, user, "status", lead.status, status)
            lead.status = status
    note = str(body.get("note") or "").strip()[:1000]
    ev = body.get("event")
    now = datetime.utcnow()
    if ev is not None:
        if ev not in CONTACT_EVENTS:
            _bad("event", "Unknown activity.")
        if ev == "follow_up_scheduled":
            when = _when(body, "follow_up_at", "follow-up")
            old_fu = lead.next_follow_up_at
            lead.next_follow_up_at = when
            _event(db, lead, user, ev, _utc(old_fu), _utc(when), note or None)
        elif ev in ("appointment_set", "appointment_changed"):
            when = _when(body, "appointment_at", "appointment")
            old_appt = lead.appointment_at
            ev = "appointment_changed" if old_appt else "appointment_set"
            lead.appointment_at = when
            _event(db, lead, user, ev, _utc(old_appt), _utc(when), note or None)
            if lead.status in ("new", "contacted", "qualified"):
                _event(db, lead, user, "status", lead.status, "appointment_set")
                lead.status = "appointment_set"
        elif ev == "enrollment_recorded":
            lead.enrolled_at = now
            _event(db, lead, user, ev, None, "enrolled", note or None)
            if lead.status != "enrolled":
                _event(db, lead, user, "status", lead.status, "enrolled")
                lead.status = "enrolled"
        else:
            lead.last_contact_at = now
            _event(db, lead, user, ev, None, None, note or None)
            if ev == "contact_completed" and lead.status == "new":
                _event(db, lead, user, "status", "new", "contacted")
                lead.status = "contacted"
        note = ""
    if note:
        _event(db, lead, user, "note", note=note)
    lead.updated_at = now
    if not test:
        from app.services.volt import sheets_outbox
        sheets_outbox.enqueue(db, org_id, "consumer", lead.id)
    db.flush()
    return detail(db, user, org_id, lead.id, test)


def _principal_ids(db: Session, org_id: str) -> List[str]:
    from app.services.volt import devrelay
    out = []
    for email in devrelay.named_principals():
        u = db.query(User).filter(func.lower(User.email) == email).first()
        if u is not None and devrelay.is_principal(db, u, org_id):
            out.append(u.id)
    return out


def assignable(db: Session, org_id: str) -> List[Dict[str, Any]]:
    """Who can hold leads: active Consumer Leads managers and agents, and
    VOLT's named operators. Recruiting roles alone do not qualify."""
    rows = db.query(VoltLeadGrant).filter(VoltLeadGrant.organization_id == org_id, VoltLeadGrant.active.is_(True)).all()
    ids = [r.user_id for r in rows] + _principal_ids(db, org_id)
    users = db.query(User).filter(User.id.in_(ids), User.is_active.is_(True)).all() if ids else []
    out = []
    for u in users:
        if u.id in {r.user_id for r in rows} and not _member(db, u, org_id):
            continue
        out.append({"id": u.id, "name": u.full_name or u.email})
    return sorted(out, key=lambda x: x["name"].lower())


def candidates(db: Session, org_id: str) -> List[Dict[str, Any]]:
    """People who may be ADDED to the Consumer Leads team: active members of
    the VOLT organization (not recruiting applicants/reps)."""
    from app.models.sales_models import Membership
    ids = {m.user_id for m in db.query(Membership).filter(
        Membership.scope_type == "customer_org", Membership.scope_id == org_id, Membership.is_active.is_(True)).all()}
    ids |= {u.id for u in db.query(User.id).filter(User.organization_id == org_id).all()}
    users = db.query(User).filter(User.id.in_(ids), User.is_active.is_(True)).all() if ids else []
    return sorted([{"id": u.id, "name": u.full_name or u.email} for u in users
                   if u.role not in ("volt_rep", "god_admin") and _member(db, u, org_id)], key=lambda x: x["name"].lower())


def assign(db: Session, user: User, org_id: str, lead_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if access(db, user, org_id) != "all":
        raise HTTPException(status_code=403, detail="Assigning leads needs Consumer Leads manager access.")
    test = is_test_param(body)
    lead = _lead(db, user, org_id, lead_id, test)
    target = body.get("user_id") or None
    if target is not None and target not in {a["id"] for a in assignable(db, org_id)}:
        _bad("user_id", "That person cannot be assigned leads.")
    if target != lead.assigned_user_id:
        _event(db, lead, user, "assigned", lead.assigned_user_id, target)
        lead.assigned_user_id = target
        lead.updated_at = datetime.utcnow()
    if not test:
        from app.services.volt import sheets_outbox
        sheets_outbox.enqueue(db, org_id, "consumer", lead.id)
    db.flush()
    return detail(db, user, org_id, lead.id, test)


def report(db: Session, user: User, org_id: str, test: bool = False) -> Dict[str, Any]:
    """Counts from the (scoped) lead table only - real rows, or the test
    partition when test=True; never both. Cost figures appear only when real
    spend has been recorded - none is today, so none are shown."""
    q, mode = scope(db, user, org_id, test)
    rows = q.with_entities(VoltLead.status, VoltLead.channel, VoltLead.source, VoltLead.assigned_user_id,
                           VoltLead.appointment_at, VoltLead.updated_at, VoltLead.utm_campaign,
                           VoltLead.campaign).all()
    by_status = {s: 0 for s in STATUSES}
    by_channel: Dict[str, Dict[str, int]] = {}
    agents: Dict[str, Dict[str, int]] = {}
    appts = 0
    last = None
    by_campaign: Dict[str, Dict[str, int]] = {}
    for st, ch, src, who, appt, upd, ucamp, camp in rows:
        c = by_campaign.setdefault(ucamp or camp or "(none)", {"leads": 0, "appointments": 0, "enrolled": 0})
        c["leads"] += 1
        c["appointments"] += 1 if (appt is not None or st in ("appointment_set", "enrolled")) else 0
        c["enrolled"] += 1 if st == "enrolled" else 0
        by_status[st] = by_status.get(st, 0) + 1
        ch = ch or {"facebook": "meta"}.get(src, src or "other")
        b = by_channel.setdefault(ch, {"leads": 0, "qualified": 0, "appointments": 0, "enrolled": 0})
        b["leads"] += 1
        b["qualified"] += 1 if st in ("qualified", "appointment_set", "enrolled") else 0
        b["appointments"] += 1 if (appt is not None or st in ("appointment_set", "enrolled")) else 0
        b["enrolled"] += 1 if st == "enrolled" else 0
        appts += 1 if (appt is not None or st in ("appointment_set", "enrolled")) else 0
        if who:
            a = agents.setdefault(who, {"assigned": 0, "enrolled": 0})
            a["assigned"] += 1
            a["enrolled"] += 1 if st == "enrolled" else 0
        last = upd if last is None or (upd and upd > last) else last
    names = _names(db, list(agents))
    return {"access": mode, "test": test, "total": len(rows), "by_status": by_status, "appointments": appts,
            "by_campaign": [dict(campaign=k, **v) for k, v in sorted(by_campaign.items())],
            "enrollments": by_status.get("enrolled", 0), "last_activity_at": _utc(last),
            "by_channel": [dict(channel=k, label=CHANNEL_LABELS.get(k, k), **v) for k, v in sorted(by_channel.items())],
            "by_source": {k: {"leads": v["leads"], "enrolled": v["enrolled"]} for k, v in by_channel.items()},
            "agents": [{"name": names.get(k, "Agent"), "assigned": v["assigned"], "enrolled": v["enrolled"],
                        "conversion_pct": round(100.0 * v["enrolled"] / v["assigned"], 1) if v["assigned"] else None}
                       for k, v in agents.items()],
            "cost": None, "cost_note": "No ad spend has been recorded, so cost per lead and per enrollment are not shown."}


def summary(db: Session, user: User, org_id: str) -> Dict[str, Any]:
    """The campaign card: counts and last activity only - never lead details,
    never test rows."""
    r = report(db, user, org_id)
    cfg = db.get(VoltLeadSettings, org_id)
    from app.services.volt import devrelay
    return {"title": "Consumer Leads", "subtitle": "Health Insurance Lead Acquisition & Enrollment",
            "parent": "VOLT Marketing", "access": r["access"], "total": r["total"],
            "new": r["by_status"].get("new", 0), "contacted": r["by_status"].get("contacted", 0),
            "qualified": r["by_status"].get("qualified", 0), "appointments": r["appointments"],
            "enrollments": r["enrollments"], "last_activity_at": r["last_activity_at"],
            "intake_enabled": bool(cfg and cfg.intake_enabled),
            "can_settings": is_admin(db, user, org_id),
            "can_intake": C.staff_access(db, user, org_id) == "owner",
            "can_preview": r["access"] == "all",
            "can_develop": devrelay.is_principal(db, user, org_id) or C.staff_access(db, user, org_id) == "owner",
            "path": "/consumer-leads/%s" % org_id}


# ── owner grants ────────────────────────────────────────────────────────────

def grants(db: Session, org_id: str) -> List[Dict[str, Any]]:
    rows = db.query(VoltLeadGrant).filter(VoltLeadGrant.organization_id == org_id, VoltLeadGrant.active.is_(True)).all()
    names = _names(db, [r.user_id for r in rows])
    return [{"user_id": r.user_id, "name": names.get(r.user_id), "role": r.role or "manager"} for r in rows]


def set_grant(db: Session, org_id: str, actor: User, body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Add, change or remove a Consumer Leads team member. The owner or a named
    operator may add agents; only the owner may make someone a manager (who
    sees every lead)."""
    require_admin(db, actor, org_id)
    uid = str(body.get("user_id") or "")
    active = body.get("active") is not False
    role = str(body.get("role") or "agent")
    g = db.get(VoltLeadGrant, (org_id, uid))
    if active:
        if role not in GRANT_ROLES:
            _bad("role", "Choose manager or agent.")
        if role == "manager" and C.staff_access(db, actor, org_id) != "owner":
            raise HTTPException(status_code=403, detail="Only the owner can make someone a Consumer Leads manager.")
        if uid not in {c["id"] for c in candidates(db, org_id)}:
            _bad("user_id", "That person is not an active member of the VOLT organization.")
    elif g is None:
        return grants(db, org_id)
    elif (g.role or "manager") == "manager" and C.staff_access(db, actor, org_id) != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can remove a Consumer Leads manager.")
    old = None if g is None or not g.active else (g.role or "manager")
    if g is None:
        db.add(VoltLeadGrant(organization_id=org_id, user_id=uid, granted_by=actor.id, active=True, role=role))
    else:
        g.active, g.granted_by = active, actor.id
        if active:
            g.role = role
    C.audit(db, org_id, actor, "lead_grant", uid, "granted" if active else "revoked", old, role if active else None)
    db.flush()
    return grants(db, org_id)


# ── responses & follow-up ───────────────────────────────────────────────────

def followup(db: Session, user: User, org_id: str, test: bool = False) -> Dict[str, Any]:
    """Response-time monitoring and the follow-up queue. Read-only: nothing
    here contacts a customer."""
    q, mode = scope(db, user, org_id, test)
    now = datetime.utcnow()
    leads = q.all()
    ids = [l.id for l in leads]
    first: Dict[str, datetime] = {}
    if ids:
        for lid, at in (db.query(VoltLeadEvent.lead_id, VoltLeadEvent.created_at)
                        .filter(VoltLeadEvent.lead_id.in_(ids),
                                VoltLeadEvent.action.in_(("call_attempted", "contact_completed")))
                        .order_by(VoltLeadEvent.created_at).all()):
            first.setdefault(lid, at)
    names = _names(db, [l.assigned_user_id for l in leads])
    waiting = sorted([l for l in leads if l.id not in first and l.status == "new"], key=lambda l: l.created_at)
    mins = sorted((first[l.id] - l.created_at).total_seconds() / 60.0 for l in leads if l.id in first)
    due = sorted([l for l in leads if l.next_follow_up_at and l.next_follow_up_at <= now + timedelta(days=1)
                  and l.status not in ("enrolled", "not_interested")], key=lambda l: l.next_follow_up_at)
    appts = sorted([l for l in leads if l.appointment_at and l.appointment_at >= now - timedelta(hours=2)],
                   key=lambda l: l.appointment_at)

    def row(l):
        d = lead_dict(l, names)
        d["waiting_minutes"] = int((now - l.created_at).total_seconds() // 60)
        d["overdue"] = bool(l.next_follow_up_at and l.next_follow_up_at < now)
        return d
    return {"access": mode, "test": test, "now": _utc(now),
            "waiting_first_contact": [row(l) for l in waiting[:100]],
            "follow_ups_due": [row(l) for l in due[:100]],
            "upcoming_appointments": [row(l) for l in appts[:100]],
            "response": {"contacted": len(mins), "median_minutes": round(mins[len(mins) // 2], 1) if mins else None,
                         "within_15_min": sum(1 for m in mins if m <= 15), "never_contacted": len(waiting)},
            "channels": [
                {"key": "staff_new_lead", "label": "New-lead alert to managers", "state": "prepared",
                 "note": "Built into the plan; off until you choose who gets it."},
                {"key": "staff_assignment", "label": "Assignment alert to the agent", "state": "prepared",
                 "note": "Off. Agents see new assignments in their inbox today."},
                {"key": "escalation", "label": "Escalation when no one responds in time", "state": "prepared",
                 "note": "Off. The waiting list above shows how long each new lead has waited."},
                {"key": "customer_text", "label": "Text messages to customers", "state": "not_enabled",
                 "note": "Off. Outreach rules, consent wording and calling/texting compliance are not final."},
                {"key": "customer_email", "label": "Email follow-up to customers", "state": "not_enabled",
                 "note": "Off. Not configured or authorized."},
                {"key": "calling", "label": "Calling integration", "state": "not_enabled",
                 "note": "Off. No dialer is connected."},
            ]}


# ── Program Access card ─────────────────────────────────────────────────────

def contexts_for(db: Session, user: User) -> List[Dict[str, Any]]:
    """One Program Access card per VOLT tenant whose Consumer Leads business
    this login may enter - decided by access() above, nothing else."""
    if user is None or getattr(user, "role", None) == "volt_rep":
        return []
    from app.models.volt_models import VoltProgram
    out = []
    # Consumer Leads is VOLT Marketing's subsidiary: the program whose public
    # customer website is /volt/check-coverage (frontend voltApi SLUG).
    for p in db.query(VoltProgram).filter(VoltProgram.slug == HOME_SLUG).all():
        mode = try_access(db, user, p.organization_id)
        if not mode:
            continue
        org = db.get(Organization, p.organization_id)
        if org is None or org.is_active is False:
            continue
        out.append({"type": "consumer_leads", "organization_id": p.organization_id,
                    "organization_name": "Consumer Leads", "parent_name": org.name, "access": mode,
                    "path": "/consumer-leads/%s" % p.organization_id})
    return out
