"""The Max Life DEMO workspace seed — one importable, idempotent function.

Called by:
  * scripts/seed_maxlife_demo.py      (local / staging, with a DB URL)
  * POST /god/demo/maxlife             (production, by the platform owner, through the API)

WHAT IT CREATES, AND WHAT IT NEVER DOES
  * Every agency_* row is is_demo=True; every lead is is_test=True with an
    example.com address and a 555-01xx phone number; every person is named
    "DEMO ..." and uses an example.com address. No real person, no consent:
    sms_consent is left at its default (False) on every lead.
  * It SENDS NOTHING. No invitation, no welcome email, no SMS, no provider call.
    Demo agent users are created with an unusable random password and
    must_change_password=True (unless the caller passes a presenter password), so
    nobody can log in as them until someone deliberately resets one. The platform
    owner views the workspace through the ordinary God-mode "enter customer" path.
  * It REFUSES an organization that is not flagged is_demo, and refuses to adopt a
    demo user email that already belongs to a different organization.

IDEMPOTENT: keyed on stable demo emails / names and "does this org already have
one" checks. A second run adds nothing and never undoes an assignment a presenter
made while demonstrating. Max Life specifics are DATA in the tables below.
"""
import json
import secrets
from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.agency_models import (AgencyAgentProfile, AgencyApplication, AgencyApplicationEvent,
                                      AgencyAppointment, AgencyPolicy, AgencyProspectProfile,
                                      AgencyRecruit, AgencyRecruitMilestone, AgencyTask)
from app.models.models import Lead, Organization, Reply, User
from app.services.agency.common import get_config, jdump, now as _now

SLUG = "max-life-demo-agency"
NAME = "Max Life Demo Agency (DEMO)"
INDUSTRY = "insurance"
# The modules the agency screens use. Deliberately NOT sms / email / voice /
# campaigns / cadences: a demo workspace has nothing to send with.
DEMO_FEATURES = ("leads", "insurance_agency", "booking", "calendar", "availability", "reports",
                 "users", "master_dashboard")

AGENTS = [  # email, name, role, jurisdictions, specializations, available, max_active, avg_resp(DEMO)
    ("demo.owner@example.com", "DEMO Owner Morgan Hale", "org_admin", ["TX"], [], True, 20, None),
    ("demo.maya@example.com", "DEMO Maya Thompson", "advisor", ["TX", "OK"],
     ["family_protection", "living_benefits"], True, 25, 14.0),
    ("demo.andre@example.com", "DEMO Andre Wallace", "advisor", ["TX", "LA"],
     ["retirement", "business_owner"], True, 20, None),
    ("demo.priya@example.com", "DEMO Priya Nair", "advisor", ["TX"], ["family_protection", "final_expense"],
     False, 15, None),
]
PROSPECTS = [  # first, last, state, needs, intent, household, minutes_ago, reply
    ("Jordan", "Reyes", "TX", ["family_protection"], "high", {"spouse": True, "children": 2}, 240,
     "I already have life insurance through work. Why would I need anything else?"),
    ("Alicia", "Brooks", "TX", ["retirement"], "medium", {"spouse": False}, 1440, None),
    ("Marcus", "Lee", "OK", ["family_protection", "living_benefits"], "high", {"children": 1}, 30, None),
    ("Dana", "Whitfield", "TX", ["business_owner"], "low", None, 4000, "What would this cost for a small business?"),
    ("Sam", "Ortiz", "CA", ["final_expense"], None, None, 600, None),
]
UNASSIGNED = ("Jordan", "Marcus", "Sam")   # left open so distribution can be demonstrated


class DemoSeedError(Exception):
    """The seed refused (wrong kind of organization, email collision...)."""


def is_maxlife_demo_org(org: Optional[Organization]) -> bool:
    """True only for an is_demo organization in this seed's slug family."""
    if org is None or not bool(getattr(org, "is_demo", False)):
        return False
    slug = org.slug or ""
    return slug == SLUG or slug.startswith(SLUG + "-")


def find_demo_org(db: Session) -> Optional[Organization]:
    return db.query(Organization).filter(Organization.slug == SLUG).first()


def ensure_demo_org(db: Session, *, actor: Optional[User] = None,
                    platform_id: Optional[str] = None) -> Tuple[Organization, bool]:
    """Find-or-create the demo organization by its stable slug. Returns (org, created).

    Refuses if the slug is held by an organization that is NOT flagged is_demo -
    the seed never converts a real organization into a demo one.
    """
    org = find_demo_org(db)
    created = False
    if org is None:
        org = Organization(name=NAME, slug=SLUG, plan="enterprise", industry=INDUSTRY,
                           is_active=True, is_demo=True, platform_id=platform_id)
        db.add(org)
        db.flush()
        created = True
    elif not bool(org.is_demo):
        raise DemoSeedError("Organization '%s' holds the demo slug but is not flagged is_demo; "
                            "refusing to touch it." % org.name)
    enable_demo_features(db, org, actor)
    return org, created


def enable_demo_features(db: Session, org: Organization, actor: Optional[User]) -> list:
    """Add DEMO_FEATURES to the org's stored allow-list via the existing mechanism."""
    from app.services import entitlements as ent
    stored = ent.legacy_enabled_for(org)
    current = set(stored) if stored is not None else set()
    wanted = sorted(current | set(DEMO_FEATURES))
    if stored is not None and set(stored) >= set(DEMO_FEATURES):
        return list(stored)
    if actor is not None:
        return ent.set_features(db, org, actor, wanted)   # audited (customer.features_set)
    org.enabled_features = json.dumps(ent.normalize_keys(wanted))
    db.flush()
    return wanted


def counts(db: Session, org_id: str) -> Dict[str, int]:
    def c(model, *flt):
        return db.query(model).filter(model.organization_id == org_id, *flt).count()
    return {
        "agents": c(AgencyAgentProfile),
        "users": c(User),
        "prospects": c(Lead),
        "prospect_profiles": c(AgencyProspectProfile),
        "applications": c(AgencyApplication),
        "policies": c(AgencyPolicy),
        "appointments": c(AgencyAppointment),
        "tasks": c(AgencyTask),
        "recruits": c(AgencyRecruit),
        "unassigned_prospects": c(Lead, Lead.assigned_to_id.is_(None)),
    }


def seed_maxlife_demo(db: Session, *, organization: Optional[Organization] = None,
                      created_by: Optional[User] = None, password: Optional[str] = None,
                      commit: bool = True) -> Dict[str, Any]:
    """Seed (or top up) the Max Life demo workspace. Idempotent. Sends nothing.

    organization: an is_demo org in the demo slug family; None -> find/create by slug.
    created_by:   the acting user (audit actor for the feature change); optional.
    password:     a presenter password for the demo users; None -> unusable random
                  password + must_change_password=True.
    """
    from app.services import workspace_access
    from app.services.auth_service import hash_password

    if organization is None:
        org, org_created = ensure_demo_org(db, actor=created_by)
    else:
        if not is_maxlife_demo_org(organization):
            raise DemoSeedError("Refusing to seed demo data into '%s': it is not a Max Life demo "
                                "organization (must be is_demo and created by the demo seed)."
                                % organization.name)
        org, org_created = organization, False
        enable_demo_features(db, org, created_by)

    before = counts(db, org.id)
    now = _now()

    users = {}
    for email, name, role, juris, specs, avail, cap, avg in AGENTS:
        u = db.query(User).filter(User.email == email).first()
        if u is not None and u.organization_id != org.id:
            raise DemoSeedError("Demo user %s already belongs to another organization; refusing "
                                "to grant it access to this one." % email)
        if u is None:
            u = User(organization_id=org.id, email=email, full_name=name, role=role, is_active=True,
                     password_hash=hash_password(password or secrets.token_urlsafe(24)),
                     # Random password -> nobody can log in until it is reset. A chosen
                     # presenter password is usable as-is.
                     must_change_password=not password)
            db.add(u)
            db.flush()
        users[email] = u
        # /workspace/{id} resolves access through memberships, not users.organization_id.
        workspace_access.grant_workspace_membership(db, u.id, org.id, role=role, commit=False,
                                                    check_capacity=False)
        if not db.query(AgencyAgentProfile).filter(AgencyAgentProfile.user_id == u.id,
                                                   AgencyAgentProfile.organization_id == org.id).first():
            db.add(AgencyAgentProfile(organization_id=org.id, user_id=u.id, jurisdictions=jdump(juris),
                                      specializations=jdump(specs), available=avail, max_active=cap,
                                      avg_response_minutes=avg, is_demo=True))
    owner = users["demo.owner@example.com"]
    cfg = get_config(db, org.id)
    if cfg.escalation_user_id is None:
        cfg.escalation_user_id = owner.id
    cfg.is_demo = True

    leads = {}
    new_leads = set()
    for i, (first, last, st, needs, intent, hh, ago, msg) in enumerate(PROSPECTS):
        email = "demo.%s.%s@example.com" % (first.lower(), last.lower())
        l = db.query(Lead).filter(Lead.organization_id == org.id, Lead.email == email).first()
        if l is None:
            l = Lead(organization_id=org.id, first_name=first, last_name=last, email=email,
                     phone="+1214555%04d" % (100 + i), state=st, source="website", source_detail="DEMO intake",
                     status="new", is_test=True, created_at=now - timedelta(minutes=ago))
            db.add(l)
            db.flush()
            new_leads.add(first)
            db.add(AgencyProspectProfile(organization_id=org.id, lead_id=l.id, need_categories=jdump(needs),
                                         intent_level=intent, household=jdump(hh), page="/find-your-path",
                                         is_demo=True))
            if msg:   # an INBOUND reply on record - nothing is sent
                db.add(Reply(lead_id=l.id, body=msg, source="sms"))
        leads[first] = l
    db.flush()
    maya = users["demo.maya@example.com"]
    andre = users["demo.andre@example.com"]
    alicia = leads["Alicia"]
    if alicia.assigned_to_id is None and "Alicia" in new_leads:
        alicia.assigned_to_id = andre.id

    if not db.query(AgencyApplication).filter(AgencyApplication.organization_id == org.id).first():
        l = leads["Dana"]
        l.assigned_to_id = andre.id
        stalled = AgencyApplication(organization_id=org.id, lead_id=l.id, agent_user_id=andre.id,
                                    owner_user_id=andre.id, carrier="DEMO Carrier (entered)",
                                    product_category="term_life", status="awaiting_client",
                                    status_changed_at=now - timedelta(days=12),
                                    submitted_at=now - timedelta(days=20), is_demo=True,
                                    created_at=now - timedelta(days=25))
        db.add(stalled)
        db.flush()
        for frm, to, d in ((None, "draft", 25), ("draft", "prepared", 22), ("prepared", "submitted", 20),
                           ("submitted", "awaiting_client", 12)):
            db.add(AgencyApplicationEvent(organization_id=org.id, application_id=stalled.id, from_status=frm,
                                          to_status=to, at=now - timedelta(days=d), by_user_id=andre.id,
                                          note="DEMO"))
        db.add(AgencyPolicy(organization_id=org.id, lead_id=alicia.id, agent_user_id=andre.id,
                            policy_number="DEMO-POL-0001", carrier="DEMO Carrier (entered)",
                            product_category="retirement_annuity", status="in_force",
                            effective_date=date.today() - timedelta(days=350),
                            annual_review_date=date.today() + timedelta(days=15), is_demo=True))
        db.add(AgencyAppointment(organization_id=org.id, lead_id=leads["Jordan"].id, agent_user_id=maya.id,
                                 type="discovery", medium="video", starts_at=now + timedelta(days=1, hours=2),
                                 status="pending", notes="DEMO", is_demo=True))
        db.add(AgencyTask(organization_id=org.id, kind="follow_up", title="DEMO: call back about cost question",
                          lead_id=l.id, assigned_user_id=andre.id, due_at=now - timedelta(days=1),
                          is_demo=True))
    if not db.query(AgencyRecruit).filter(AgencyRecruit.organization_id == org.id).first():
        for name, stage, juris in (("DEMO Chris Patel", "licensing", "TX"), ("DEMO Lena Ford", "training", "TX"),
                                   ("DEMO Omar Diaz", "candidate", "OK")):
            r = AgencyRecruit(organization_id=org.id, name=name, stage=stage, jurisdiction=juris,
                              stage_changed_at=now - timedelta(days=5), recruiter_user_id=owner.id,
                              exam_status="scheduled (as entered)" if stage == "licensing" else None,
                              is_demo=True, created_at=now - timedelta(days=30))
            db.add(r)
            db.flush()
            if stage == "licensing":
                db.add(AgencyRecruitMilestone(organization_id=org.id, recruit_id=r.id,
                                              label="Pre-licensing course", status="done",
                                              completed_at=now - timedelta(days=3)))
                db.add(AgencyRecruitMilestone(organization_id=org.id, recruit_id=r.id, label="State exam",
                                              status="pending", due=date.today() - timedelta(days=1)))
    db.flush()
    after = counts(db, org.id)
    if commit:
        db.commit()
    unassigned = [n for n in UNASSIGNED if leads[n].assigned_to_id is None]
    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "organization_created": org_created,
        "is_demo": bool(org.is_demo),
        "counts": after,
        "added": {k: after[k] - before.get(k, 0) for k in after if k != "unassigned_prospects"},
        "unassigned": unassigned,
        "presenter_password_set": bool(password),
        "sent": {"sms": 0, "email": 0, "invitations": 0},
    }
