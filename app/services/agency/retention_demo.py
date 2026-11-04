"""VINCENT GREENE AGENCY — isolated, resettable retention DEMO (Issue #499).

WHAT IS REAL AND WHAT IS NOT
  * Verified public reference (supplied by Mike, cross-checked in public
    listings): Vincent Greene: Allstate Insurance, 18601 LBJ Fwy, Suite 420,
    Mesquite, TX 75150, (972) 288-2288, English and Spanish service.
    Only the Mesquite location carries an address and phone.
  * "Vincent Greene Agency" is a configurable display name for the proof of
    concept, not a statement about the agency's legal entity. Nothing here
    claims an Allstate affiliation or integration.
  * The other four locations, all 30 staff, every household, policy, renewal,
    message and appointment are SYNTHETIC. Staff use @example.com addresses and
    unusable random passwords (nobody can log in as them); clients use
    @example.com addresses and 555-01xx numbers reserved for fiction.
  * No login or outreach identity is created for any real person, including
    Vincent Greene or anyone on his staff page.

IT SENDS NOTHING. Every lead is is_test=True and every row is_demo=True; the
workflow runs in simulated delivery mode, which never reaches a provider.

IDEMPOTENT + RESETTABLE: `ensure_demo` creates the workspace once and seeds it
if it has no renewals; `reset_demo` deletes only this workspace's synthetic
retention data and seeds it again relative to today's date.
"""
import json
import random
import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.agency_models import AgencyAppointment, AgencyPolicy
from app.models.agency_retention_models import (AgencyClientProfile, AgencyRenewal, AgencyRenewalEnrollment,
                                                AgencyRenewalEvent, AgencyRenewalWorkflow,
                                                AgencyRetentionSettings, AgencyRetentionStaff)
from app.models.location_models import Location, UserLocation
from app.models.models import Lead, Organization, User
from app.services.agency import retention as R

SLUG = "vincent-greene-agency-demo"
ORG_NAME = "Vincent Greene Agency (DEMO)"
DISPLAY_NAME = "Vincent Greene Agency"
TZ = "America/Chicago"
FEATURES = ("leads", "insurance_agency", "booking", "calendar", "availability", "reports", "users",
            "master_dashboard")
REFERENCE = {
    "public_identity": "Vincent Greene: Allstate Insurance",
    "office": "18601 LBJ Fwy, Suite 420, Mesquite, TX 75150",
    "phone": "(972) 288-2288",
    "languages": ["English", "Spanish"],
    "source": "https://agents.allstate.com/vincent-greene-mesquite-tx.html (supplied by Mike; public listings agree)",
    "note": "Display name is configurable for the proof of concept. Other locations, staff counts and all "
            "customer data are illustrative.",
}

LOCATIONS = [  # slug, name, verified
    ("mesquite", "Mesquite", True),
    ("demo-2", "Demo location 2", False),
    ("demo-3", "Demo location 3", False),
    ("demo-4", "Demo location 4", False),
    ("demo-5", "Demo location 5", False),
]
# 30 synthetic staff: (key, full name, role, title, location slug, languages, capacity)
STAFF = [
    ("mbrooks", "Morgan Brooks", "manager", "manager", "mesquite", ["en"], 12),
    ("jlopez", "Jamie Lopez", "advisor", "producer", "mesquite", ["en", "es"], 14),
    ("asingh", "Avery Singh", "advisor", "producer", "mesquite", ["en"], 12),
    ("rortega", "Rosa Ortega", "advisor", "retention", "mesquite", ["en", "es"], 16),
    ("tnguyen", "Tess Nguyen", "advisor", "service", "mesquite", ["en"], 10),
    ("dwalker", "Dana Walker", "advisor", "service", "mesquite", ["en"], 10),
    ("cfields", "Casey Fields", "advisor", "producer", "mesquite", ["en"], 12),
    ("lmendez", "Luis Mendez", "advisor", "service", "mesquite", ["en", "es"], 10),
    ("phart", "Parker Hart", "advisor", "producer", "mesquite", ["en"], 8),
    ("svance", "Sam Vance", "advisor", "retention", "mesquite", ["en"], 14),
    ("kturner", "Kendall Turner", "manager", "manager", "demo-2", ["en"], 10),
    ("bcole", "Blake Cole", "advisor", "producer", "demo-2", ["en"], 12),
    ("mrios", "Marisol Rios", "advisor", "retention", "demo-2", ["en", "es"], 14),
    ("jpark", "Jordan Park", "advisor", "service", "demo-2", ["en"], 10),
    ("efoster", "Emery Foster", "advisor", "producer", "demo-2", ["en"], 10),
    ("hgray", "Harper Gray", "advisor", "service", "demo-2", ["en"], 8),
    ("creed", "Cameron Reed", "manager", "manager", "demo-3", ["en"], 10),
    ("vcastillo", "Valeria Castillo", "advisor", "retention", "demo-3", ["en", "es"], 12),
    ("rbennett", "Riley Bennett", "advisor", "producer", "demo-3", ["en"], 10),
    ("qmoss", "Quinn Moss", "advisor", "service", "demo-3", ["en"], 10),
    ("tellis", "Taylor Ellis", "advisor", "producer", "demo-3", ["en"], 8),
    ("aprice", "Alex Price", "manager", "manager", "demo-4", ["en"], 10),
    ("dflores", "Diego Flores", "advisor", "retention", "demo-4", ["en", "es"], 12),
    ("skim", "Sydney Kim", "advisor", "producer", "demo-4", ["en"], 10),
    ("nshaw", "Noel Shaw", "advisor", "service", "demo-4", ["en"], 8),
    ("ljames", "Logan James", "advisor", "service", "demo-4", ["en"], 8),
    ("rwhite", "Reese White", "manager", "manager", "demo-5", ["en"], 8),
    ("galvarez", "Gabriela Alvarez", "advisor", "retention", "demo-5", ["en", "es"], 12),
    ("mdean", "Micah Dean", "advisor", "producer", "demo-5", ["en"], 10),
    ("jwest", "Jesse West", "advisor", "service", "demo-5", ["en"], 8),
]
SURNAMES = ["Miller", "Robinson", "Chen", "Davis", "Patel", "Garcia", "Nguyen", "Hernandez", "Johnson", "Williams",
            "Brown", "Jones", "Martinez", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
            "Perez", "Thompson", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Walker", "Young", "Allen",
            "King", "Wright", "Scott", "Torres", "Hill", "Flores", "Green", "Adams", "Nelson", "Baker",
            "Hall", "Rivera", "Campbell", "Mitchell", "Carter", "Roberts", "Gomez", "Phillips", "Evans", "Turner",
            "Diaz", "Parker", "Cruz", "Edwards", "Collins", "Reyes", "Stewart", "Morris", "Morales", "Murphy"]
FIRST = ["Jordan", "Taylor", "Chris", "Pat", "Morgan", "Lee", "Kim", "Robin", "Drew", "Jamie", "Ana", "Luis",
         "Maria", "Jose", "Linh", "Raj", "Priya", "Grace", "Ben", "Olivia"]
SPANISH = {"Garcia", "Hernandez", "Martinez", "Perez", "Sanchez", "Ramirez", "Torres", "Flores", "Rivera",
           "Gomez", "Diaz", "Cruz", "Reyes", "Morales"}


class DemoError(Exception):
    pass


def find_org(db: Session) -> Optional[Organization]:
    return db.query(Organization).filter(Organization.slug == SLUG).first()


def is_vg_demo_org(org: Optional[Organization]) -> bool:
    return org is not None and bool(getattr(org, "is_demo", False)) and (org.slug or "") == SLUG


def _local(day: date, hh: int, mm: int = 0) -> datetime:
    tz = R.tzinfo_for(TZ)
    return datetime.combine(day, time(hh, mm), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def ensure_org(db: Session) -> Tuple[Organization, bool]:
    from app.services import entitlements as ent
    org = find_org(db)
    created = False
    if org is None:
        org = Organization(name=ORG_NAME, slug=SLUG, plan="enterprise", industry="insurance", is_active=True,
                           is_demo=True)
        db.add(org)
        db.flush()
        created = True
    elif not bool(org.is_demo):
        raise DemoError("Organization holding slug %s is not flagged is_demo; refusing to touch it." % SLUG)
    stored = ent.legacy_enabled_for(org)
    cur = set(stored) if stored is not None else set()
    if not cur >= set(FEATURES):
        org.enabled_features = json.dumps(ent.normalize_keys(sorted(cur | set(FEATURES))))
    s = R.get_settings(db, org.id)
    if s is None:
        s = AgencyRetentionSettings(organization_id=org.id)
        db.add(s)
    s.enabled, s.display_name, s.tagline, s.timezone = True, DISPLAY_NAME, "Keep every renewal moving.", TZ
    s.default_offsets, s.data_mode, s.is_demo = json.dumps(R.DEFAULT_OFFSETS), "demo", True
    s.reference = json.dumps(REFERENCE)
    db.flush()
    return org, created


def _ensure_locations(db: Session, org: Organization) -> Dict[str, Location]:
    out = {}
    for slug, name, verified in LOCATIONS:
        loc = db.query(Location).filter(Location.organization_id == org.id, Location.slug == slug).first()
        if loc is None:
            loc = Location(organization_id=org.id, slug=slug, name=name, is_primary=(slug == "mesquite"),
                           timezone=TZ, state="TX")
            db.add(loc)
        if verified:
            loc.address_line1, loc.city, loc.postal_code = "18601 LBJ Fwy, Suite 420", "Mesquite", "75150"
            loc.phone = "(972) 288-2288"
            loc.notes = "Verified office from the supplied public reference."
        else:
            loc.notes = "Illustrative demo location - not a real office. No address is shown on purpose."
        out[slug] = loc
    db.flush()
    return out


def _ensure_staff(db: Session, org: Organization, locs: Dict[str, Location]) -> Dict[str, User]:
    from app.services import workspace_access
    from app.services.auth_service import hash_password
    users = {}
    for key, name, role, title, loc_slug, langs, cap in STAFF:
        email = "demo.vg.%s@example.com" % key
        u = db.query(User).filter(User.email == email).first()
        if u is not None and u.organization_id != org.id:
            raise DemoError("Demo user %s belongs to another organization; refusing." % email)
        if u is None:
            u = User(organization_id=org.id, email=email, full_name=name, role=role, is_active=True,
                     password_hash=hash_password(secrets.token_urlsafe(24)), must_change_password=True)
            db.add(u)
            db.flush()
        workspace_access.grant_workspace_membership(db, u.id, org.id, role=role, commit=False, check_capacity=False)
        loc = locs[loc_slug]
        if not db.query(UserLocation).filter(UserLocation.user_id == u.id, UserLocation.location_id == loc.id).first():
            db.add(UserLocation(user_id=u.id, location_id=loc.id, organization_id=org.id, is_primary=True))
        st = db.query(AgencyRetentionStaff).filter(AgencyRetentionStaff.organization_id == org.id,
                                                   AgencyRetentionStaff.user_id == u.id).first()
        if st is None:
            db.add(AgencyRetentionStaff(organization_id=org.id, user_id=u.id, title=title, languages=json.dumps(langs),
                                        review_capacity=cap, available=True, is_demo=True))
        users[key] = u
    db.flush()
    return users


def grant_admins(db: Session, org: Organization, emails: List[str]) -> List[str]:
    """Give EXISTING platform users (Mike's own admin login) agency-admin access
    to the demo workspace. Never creates a user, never grants God access."""
    from app.services import workspace_access
    granted = []
    for e in [x.strip().lower() for x in emails if x and x.strip()]:
        u = db.query(User).filter(User.email == e).first()
        if u is None or u.role == "god_admin":
            continue
        workspace_access.grant_workspace_membership(db, u.id, org.id, role="org_admin", commit=False,
                                                    check_capacity=False)
        granted.append(e)
    return granted


# ─────────────────────────────── seed data ───────────────────────────────

def _lead(db, org, i, first, last, lang, loc, city="Mesquite") -> Lead:
    email = "demo.vg.client%02d@example.com" % i
    area = ("972", "214", "469")[i % 3]
    l = Lead(organization_id=org.id, first_name=first, last_name=last, email=email,
             phone="+1%s55501%02d" % (area, i % 100), state="TX", city=None, source="demo",
             source_detail="Synthetic retention demo", status="client", relationship_type="client", is_test=True)
    db.add(l)
    db.flush()
    db.add(AgencyClientProfile(organization_id=org.id, lead_id=l.id, household_name="%s Household" % last,
                               location_id=loc.id, preferred_language=lang,
                               preferred_channel="sms", source="demo", source_updated_at=R.utcnow(), is_demo=True))
    db.flush()
    return l


def _policy(db, org, lead, product, agent, rd: date, idx: int, status="in_force", annual=None):
    p = AgencyPolicy(organization_id=org.id, lead_id=lead.id, agent_user_id=agent.id if agent else None,
                     policy_number="DEMO-%s-%05d" % (product[:2].upper(), 10000 + idx), carrier=None,
                     product_category=product, status=status,
                     effective_date=rd - timedelta(days=365), annual_review_date=annual, is_demo=True)
    db.add(p)
    db.flush()
    prof = db.query(AgencyClientProfile).filter(AgencyClientProfile.lead_id == lead.id).first()
    if prof is not None and prof.servicing_user_id is None and agent is not None:
        prof.servicing_user_id = agent.id
    return p


def _renewal(db, org, lead, pol, loc, agent, rd, **kw) -> AgencyRenewal:
    now = R.utcnow()
    r = AgencyRenewal(organization_id=org.id, lead_id=lead.id, policy_id=pol.id, location_id=loc.id,
                      product=pol.product_category, renewal_date=rd, assigned_user_id=agent.id if agent else None,
                      assigned_at=now - timedelta(days=20) if agent else None,
                      assignment_reason="Current servicing agent" if agent else None, source="demo",
                      source_updated_at=now, is_demo=True, **kw)
    db.add(r)
    db.flush()
    return r


def seed(db: Session, org: Organization) -> Dict[str, Any]:
    if not is_vg_demo_org(org):
        raise DemoError("Refusing to seed: not the Vincent Greene demo organization.")
    rnd = random.Random(499)
    s = R.get_settings(db, org.id)
    tz = R.tzinfo_for(TZ)
    today = datetime.now(tz).date()
    now = R.utcnow()
    locs = _ensure_locations(db, org)
    staff = _ensure_staff(db, org, locs)
    by_loc: Dict[str, List[User]] = {}
    for key, _n, _r, title, loc_slug, _l, _c in STAFF:
        if title != "manager":
            by_loc.setdefault(loc_slug, []).append(staff[key])

    wf = AgencyRenewalWorkflow(organization_id=org.id, name="Renewal outreach (60/30/14/7)",
                               offsets=json.dumps(R.DEFAULT_OFFSETS), channel="sms", template=R.DEFAULT_TEMPLATE,
                               template_es=R.DEFAULT_TEMPLATE_ES, window_days=60, state="running",
                               delivery_mode="simulated", last_run_at=now - timedelta(minutes=40), is_demo=True)
    db.add(wf)
    db.flush()

    def enroll(r: AgencyRenewal, done: List[int], next_off: Optional[int], state="active"):
        en = AgencyRenewalEnrollment(organization_id=org.id, workflow_id=wf.id, renewal_id=r.id, state=state,
                                     steps_done=json.dumps(done), next_offset=next_off, is_demo=True,
                                     next_run_at=(R.next_allowed_send(max(R._step_time(r.renewal_date, next_off, tz), now),
                                                                      tz, s.send_window_start, s.send_window_end)
                                                  if next_off is not None and state == "active" else None))
        db.add(en)
        db.flush()
        R.add_event(db, org.id, r.lead_id, "enrolled", renewal_id=r.id, enrollment_id=en.id,
                    detail={"workflow": wf.name}, is_demo=True, at=now - timedelta(days=30))
        return en

    def sent(r: AgencyRenewal, en, lead: Lead, offset: int, at: datetime, status="simulated", lang="en"):
        body = R.render_template(wf, {"client_name": R.lead_name(lead), "product_label": R.PRODUCT_LABELS[r.product],
                                      "renewal_date": r.renewal_date.isoformat(), "preferred_language": lang,
                                      "assigned_name": R.user_names(db, [r.assigned_user_id]).get(r.assigned_user_id)},
                                 type("C", (), {"settings": s, "org": org})(), lang)
        R.add_event(db, org.id, r.lead_id, "outreach", renewal_id=r.id, enrollment_id=en.id if en else None,
                    direction="out", channel="sms", body=body, delivery_status=status,
                    key="step:%s:%s" % (en.id, offset) if en else None,
                    detail={"days_before_renewal": offset, "language": lang,
                            "failure": "Number on file is not a working mobile number" if status == "failed" else None},
                    is_demo=True, at=at)

    def reply(r, text, at):
        ev = R.record_reply(db, org.id, s, r, text, simulated=True)
        if ev is not None:
            ev.at = at
            r.next_action_at = at + timedelta(hours=4)

    idx = 0
    counts = {"households": 0, "policies": 0, "renewals": 0}

    # ── Scenario households (coherent versions of the mockup's sample rows) ──
    m = staff
    L = locs
    # Miller (Mesquite): two policies, two renewal dates; replied with a premium concern.
    miller = _lead(db, org, 1, "Jordan", "Miller", "en", L["mesquite"])
    p1 = _policy(db, org, miller, "home", m["jlopez"], today + timedelta(days=7), 1)
    p2 = _policy(db, org, miller, "auto", m["jlopez"], today + timedelta(days=40), 2)
    r1 = _renewal(db, org, miller, p1, L["mesquite"], m["jlopez"], today + timedelta(days=7))
    _renewal(db, org, miller, p2, L["mesquite"], m["jlopez"], today + timedelta(days=40), workflow_status="not_started")
    en = enroll(r1, [60, 30], 14)
    sent(r1, None, miller, 30, now - timedelta(days=23))
    sent(r1, en, miller, 14, now - timedelta(days=1, hours=2))
    r1.workflow_status = "in_outreach"
    reply(r1, "Our home premium went up again this year. Can someone explain why before it renews?",
          now - timedelta(hours=3))
    # Robinson (Demo location 2): renewal approaching, no review booked.
    rob = _lead(db, org, 2, "Taylor", "Robinson", "en", L["demo-2"])
    p = _policy(db, org, rob, "auto", m["bcole"], today + timedelta(days=9), 3)
    r = _renewal(db, org, rob, p, L["demo-2"], m["bcole"], today + timedelta(days=9), workflow_status="in_outreach")
    en = enroll(r, [60, 30, 14], 7)
    sent(r, en, rob, 14, now - timedelta(days=5))
    # Chen (Mesquite): asked for a review -> review booked with Morgan Brooks.
    chen = _lead(db, org, 3, "Linh", "Chen", "en", L["mesquite"])
    p = _policy(db, org, chen, "home", m["mbrooks"], today + timedelta(days=12), 4)
    r = _renewal(db, org, chen, p, L["mesquite"], m["mbrooks"], today + timedelta(days=12))
    en = enroll(r, [60, 30], 14)
    sent(r, en, chen, 14, now - timedelta(days=2))
    reply(r, "Yes, a review would be great. Thursday works.", now - timedelta(days=1, hours=5))
    appt = AgencyAppointment(organization_id=org.id, lead_id=chen.id, agent_user_id=m["mbrooks"].id, type="review",
                             medium="in_person", starts_at=_local(today + timedelta(days=2), 15, 0), status="booked",
                             is_demo=True)
    db.add(appt)
    db.flush()
    r.review_appointment_id, r.workflow_status = appt.id, "review_booked"
    R.resolve_attention(r)
    r.next_action, r.next_action_at = "Hold renewal review", appt.starts_at
    en.state, en.paused_reason = "paused", "review_booked"
    for ev in db.query(AgencyRenewalEvent).filter(AgencyRenewalEvent.renewal_id == r.id,
                                                  AgencyRenewalEvent.kind == "reply").all():
        ev.handled = True
    R.add_event(db, org.id, chen.id, "review_booked", renewal_id=r.id, is_demo=True, at=now - timedelta(hours=20),
                detail={"appointment_id": appt.id, "starts_at": R.iso(appt.starts_at), "agent": "Morgan Brooks"})
    # Davis (Demo location 3): reminder sent, follow-up now overdue.
    davis = _lead(db, org, 4, "Chris", "Davis", "en", L["demo-3"])
    p = _policy(db, org, davis, "auto", m["rbennett"], today + timedelta(days=15), 5)
    r = _renewal(db, org, davis, p, L["demo-3"], m["rbennett"], today + timedelta(days=15),
                 workflow_status="in_outreach", next_action="Call client - no reply to reminder",
                 next_action_at=now - timedelta(days=2))
    en = enroll(r, [60, 30], 14)
    sent(r, en, davis, 30, now - timedelta(days=16))
    # Patel (Mesquite): annual review booked for 1:00 PM today.
    patel = _lead(db, org, 5, "Priya", "Patel", "en", L["mesquite"])
    p = _policy(db, org, patel, "home", m["asingh"], today + timedelta(days=17), 6, annual=today)
    r = _renewal(db, org, patel, p, L["mesquite"], m["asingh"], today + timedelta(days=17))
    appt = AgencyAppointment(organization_id=org.id, lead_id=patel.id, agent_user_id=m["asingh"].id, type="review",
                             medium="in_person", starts_at=_local(today, 13, 0), status="booked", is_demo=True,
                             notes="Annual coverage review")
    db.add(appt)
    db.flush()
    r.review_appointment_id, r.workflow_status = appt.id, "review_booked"
    r.next_action, r.next_action_at = "Hold annual review", appt.starts_at
    # Hernandez (Mesquite, Spanish): renewal review 10:30 AM today with a bilingual agent.
    hern = _lead(db, org, 6, "Ana", "Hernandez", "es", L["mesquite"])
    pa = _policy(db, org, hern, "auto", m["rortega"], today + timedelta(days=11), 7)
    ph = _policy(db, org, hern, "home", m["rortega"], today + timedelta(days=11), 8)
    ra = _renewal(db, org, hern, pa, L["mesquite"], m["rortega"], today + timedelta(days=11))
    rh = _renewal(db, org, hern, ph, L["mesquite"], m["rortega"], today + timedelta(days=11))
    appt = AgencyAppointment(organization_id=org.id, lead_id=hern.id, agent_user_id=m["rortega"].id, type="review",
                             medium="in_person", starts_at=_local(today, 10, 30), status="booked", is_demo=True,
                             notes="Home & Auto review (Spanish)")
    db.add(appt)
    db.flush()
    for x in (ra, rh):
        x.workflow_status, x.next_action, x.next_action_at = "review_booked", "Hold renewal review", appt.starts_at
    ra.review_appointment_id = appt.id
    rh.review_appointment_id = appt.id
    # Garcia (Mesquite, Spanish): outreach failed - alternate contact needed.
    gar = _lead(db, org, 7, "Luis", "Garcia", "es", L["mesquite"])
    gar.phone = "+15550000107"
    p = _policy(db, org, gar, "renters", m["lmendez"], today + timedelta(days=10), 9)
    r = _renewal(db, org, gar, p, L["mesquite"], m["lmendez"], today + timedelta(days=10))
    en = enroll(r, [60, 30], 14)
    sent(r, en, gar, 14, now - timedelta(hours=26), status="failed", lang="es")
    en.state, en.paused_reason = "needs_human", "delivery_failed"
    r.attention_reason, r.workflow_status = "outreach_failed", "needs_attention"
    r.attention_detail = "The text to Luis Garcia could not be delivered. Try another contact method."
    r.next_action, r.next_action_at = "Find an alternate contact", now - timedelta(hours=26)
    # Nguyen (Demo location 4): unassigned service request in the shared queue.
    ng = _lead(db, org, 8, "Kim", "Nguyen", "en", L["demo-4"])
    p = _policy(db, org, ng, "condo", None, today + timedelta(days=20), 10)
    _renewal(db, org, ng, p, L["demo-4"], None, today + timedelta(days=20))
    # Morales (Demo location 5, Spanish): mentioned switching carriers.
    mo = _lead(db, org, 9, "Maria", "Morales", "es", L["demo-5"])
    p = _policy(db, org, mo, "auto", m["galvarez"], today + timedelta(days=13), 11)
    r = _renewal(db, org, mo, p, L["demo-5"], m["galvarez"], today + timedelta(days=13))
    en = enroll(r, [60, 30], 14)
    sent(r, en, mo, 14, now - timedelta(days=1), lang="es")
    reply(r, "Estoy pensando cambiar de compañía, el precio subió mucho.", now - timedelta(hours=6))
    idx = 11
    counts["households"] = 9

    # ── Background book: 51 more synthetic households across five locations ──
    loc_cycle = ["mesquite"] * 18 + ["demo-2"] * 10 + ["demo-3"] * 9 + ["demo-4"] * 8 + ["demo-5"] * 6
    used = {"Miller", "Robinson", "Chen", "Davis", "Patel", "Hernandez", "Garcia", "Nguyen", "Morales"}
    names = [n for n in SURNAMES if n not in used]
    for k in range(51):
        last = names[k % len(names)] + ("" if k < len(names) else "-%d" % k)
        loc_slug = loc_cycle[k]
        lang = "es" if last.split("-")[0] in SPANISH and rnd.random() < 0.6 else "en"
        lead = _lead(db, org, 10 + k, FIRST[k % len(FIRST)], last, lang, L[loc_slug])
        n_pol = 1 if rnd.random() < 0.55 else 2
        prods = rnd.sample(["auto", "home", "renters", "condo", "life", "business", "landlord"], n_pol)
        if n_pol == 2 and "auto" not in prods:
            prods[0] = "auto"
        pool = by_loc[loc_slug]
        es_pool = [u for u in pool if "es" in json.loads(db.query(AgencyRetentionStaff).filter(
            AgencyRetentionStaff.user_id == u.id).first().languages)]
        agent = rnd.choice(es_pool if lang == "es" and es_pool else pool)
        db.query(AgencyClientProfile).filter(AgencyClientProfile.lead_id == lead.id).first().servicing_user_id = agent.id
        for j, prod in enumerate(prods):
            idx += 1
            offset = rnd.choice([-20, -12, -6, -2, 3, 5, 8, 16, 19, 22, 25, 27, 29, 33, 41, 48, 55, 63, 72])
            rd = today + timedelta(days=offset)
            pol = _policy(db, org, lead, prod, agent, rd, idx)
            counts["policies"] += 1
            r = _renewal(db, org, lead, pol, L[loc_slug], agent, rd)
            if offset < 0 or (offset < 6 and rnd.random() < 0.5):
                # Past or imminent renewals with a recorded outcome (explicit, staff-entered).
                outcome = "confirmed" if rnd.random() < 0.86 else "lost"
                r.outcome, r.outcome_source, r.workflow_status = outcome, "staff", "closed"
                r.outcome_at = now - timedelta(days=rnd.randint(0, 25), hours=rnd.randint(1, 8))
                r.outcome_by = agent.id
                R.add_event(db, org.id, lead.id, "outcome", renewal_id=r.id, actor=agent.id, is_demo=True,
                            at=r.outcome_at, detail={"outcome": outcome, "source": "staff"})
            elif offset <= 30:
                en = enroll(r, [60] if offset <= 30 else [], 30 if offset > 14 else 14)
                sent(r, en, lead, 30 if offset <= 30 else 60, now - timedelta(days=max(1, 30 - offset)), lang=lang)
                r.workflow_status = "in_outreach"
                if rnd.random() < 0.25:
                    a = AgencyAppointment(organization_id=org.id, lead_id=lead.id, agent_user_id=agent.id,
                                          type="review", medium=rnd.choice(["in_person", "phone"]),
                                          starts_at=_local(today + timedelta(days=rnd.randint(1, 9)),
                                                           rnd.choice([9, 11, 14, 16]), rnd.choice([0, 30])),
                                          status="booked", is_demo=True)
                    db.add(a)
                    db.flush()
                    r.review_appointment_id, r.workflow_status = a.id, "review_booked"
                    en.state, en.paused_reason = "paused", "review_booked"
            if prod in ("home", "life") and rnd.random() < 0.2:
                pol.annual_review_date = today - timedelta(days=rnd.randint(5, 40))
        counts["households"] += 1
    for x in db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == org.id).all():
        x.last_activity_at = x.last_activity_at or now - timedelta(days=2)
    db.flush()
    counts["policies"] = db.query(AgencyPolicy).filter(AgencyPolicy.organization_id == org.id).count()
    counts["renewals"] = db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == org.id).count()
    counts["staff"] = len(STAFF)
    counts["locations"] = len(LOCATIONS)
    return counts


def wipe(db: Session, org: Organization) -> Dict[str, int]:
    """Delete this demo workspace's synthetic retention data. Locations and staff stay."""
    if not is_vg_demo_org(org):
        raise DemoError("Refusing to wipe: not the Vincent Greene demo organization.")
    out = {}
    lead_ids = [x[0] for x in db.query(AgencyClientProfile.lead_id).filter(
        AgencyClientProfile.organization_id == org.id, AgencyClientProfile.is_demo.is_(True)).all()]
    for model in (AgencyRenewalEvent, AgencyRenewalEnrollment, AgencyRenewal, AgencyRenewalWorkflow):
        out[model.__tablename__] = db.query(model).filter(model.organization_id == org.id,
                                                          model.is_demo.is_(True)).delete(synchronize_session=False)
    out["agency_appointments"] = db.query(AgencyAppointment).filter(
        AgencyAppointment.organization_id == org.id, AgencyAppointment.is_demo.is_(True)).delete(synchronize_session=False)
    out["agency_policies"] = db.query(AgencyPolicy).filter(
        AgencyPolicy.organization_id == org.id, AgencyPolicy.is_demo.is_(True)).delete(synchronize_session=False)
    out["agency_client_profiles"] = db.query(AgencyClientProfile).filter(
        AgencyClientProfile.organization_id == org.id, AgencyClientProfile.is_demo.is_(True)).delete(synchronize_session=False)
    out["leads"] = db.query(Lead).filter(Lead.organization_id == org.id, Lead.id.in_(lead_ids or ["-"]),
                                         Lead.is_test.is_(True)).delete(synchronize_session=False)
    db.flush()
    return out


def reset_demo(db: Session, org: Organization) -> Dict[str, Any]:
    removed = wipe(db, org)
    added = seed(db, org)
    return {"removed": removed, "seeded": added}


def ensure_demo(db: Session, admin_emails: Optional[List[str]] = None) -> Dict[str, Any]:
    org, created = ensure_org(db)
    seeded = None
    if db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == org.id).count() == 0:
        seeded = seed(db, org)
    else:
        _ensure_staff(db, org, _ensure_locations(db, org))
    granted = grant_admins(db, org, admin_emails or [])
    return {"organization_id": org.id, "created": created, "seeded": seeded, "granted": granted}
