"""SET UP THE SCI WORKSPACE ON ANY DEPLOYMENT (production included) and add
the owner's EvoSys login to it - god-only, dry run unless applied, idempotent.

The same steps that built SCI on staging (staging_harness_router.seed and
/toll-free), minus everything test-only: no test or simulation contacts, no
alias marked verified, nothing sent, every campaign OFF. Contacts arrive later
through the normal import.

  1. Organization "Service Corporation International" on the EvoSysPro platform
     (created if absent; refused if more than one exists). No Wholesale.
  2. Program: Kerry Allan, Head of Sales; the 39 locations from
     scripts/sci_location_aliases.csv plus the Location Review bucket; campaign
     families (all OFF); location email addresses; campus grouping.
  3. The toll-free line +1 844-917-2171 recorded as this workspace's number
     (texts + inbound voicemail, no outbound calls, voicemail-only). Left
     untouched - and reported - if another organization owns the row.
  4. A workspace membership for the login named by email, with the highest
     role that login already holds in its other workspaces (Atlantis,
     Wholesale...), else `manager`. The login must already exist.
"""
from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization, User

ORG_NAME = "Service Corporation International"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ALIAS_CSV = os.path.join(_ROOT, "scripts", "sci_location_aliases.csv")
FEATURES = ["leads", "reports", "users", "branding_settings", "audit_log", "imports", "email", "sms"]
ROLE_ORDER = ("super_admin", "org_admin", "manager", "advisor", "viewer")
MAIL_FOLDER = "Inbox/Customers Folder/SCI"


def location_names() -> List[str]:
    with open(ALIAS_CSV, encoding="utf-8") as fh:
        return [r["Location"] for r in csv.DictReader(fh) if (r.get("Location") or "").strip()]


def _orgs(db: Session) -> List[Organization]:
    return (db.query(Organization).filter(Organization.name == ORG_NAME,
                                          Organization.is_active.isnot(False)).all())


def _user(db: Session, email: str) -> Optional[User]:
    e = (email or "").strip().lower()
    return db.query(User).filter(User.email.ilike(e)).first() if e else None


def _workspaces_of(db: Session, user: User) -> List[Dict[str, Any]]:
    from app.services import workspace_access
    out = []
    for m in workspace_access.workspace_memberships(user, db):
        org = db.query(Organization).filter(Organization.id == m.scope_id).first()
        out.append({"organization_id": m.scope_id, "name": getattr(org, "name", None), "role": m.role})
    return out


def _role_for(workspaces: List[Dict[str, Any]]) -> str:
    held = {w["role"] for w in workspaces}
    return next((r for r in ROLE_ORDER if r in held and r != "viewer"), "manager")


def _toll_free_state(db: Session, org_id: Optional[str]) -> Dict[str, Any]:
    from app.models.telephony_models import PhoneNumber
    from app.services.programs import regional_pools as rp
    row = db.query(PhoneNumber).filter(PhoneNumber.e164 == rp.TOLL_FREE).first()
    if row is None:
        return {"number": rp.TOLL_FREE, "state": "absent", "action": "create"}
    if org_id and row.organization_id == org_id:
        return {"number": rp.TOLL_FREE, "state": "this workspace", "action": "update"}
    other = db.query(Organization).filter(Organization.id == row.organization_id).first()
    return {"number": rp.TOLL_FREE, "state": "another organization", "owner": getattr(other, "name", None),
            "action": "left unchanged"}


def plan(db: Session, email: str) -> Dict[str, Any]:
    """What `apply` would do. Writes nothing."""
    orgs = _orgs(db)
    org = orgs[0] if len(orgs) == 1 else None
    user = _user(db, email)
    ws = _workspaces_of(db, user) if user else []
    blockers = []
    if len(orgs) > 1:
        blockers.append("%d active organizations are named %s; resolve that first." % (len(orgs), ORG_NAME))
    if user is None:
        blockers.append("No login with the email %s." % (email or "(blank)"))
    in_sci = bool(org) and any(w["organization_id"] == org.id for w in ws)
    return {
        "organization": {"exists": org is not None, "id": getattr(org, "id", None), "name": ORG_NAME,
                         "action": "use existing" if org else ("refused" if len(orgs) > 1 else "create")},
        "locations": len(location_names()),
        "toll_free": _toll_free_state(db, getattr(org, "id", None)),
        "login": {"email": getattr(user, "email", None), "found": user is not None,
                  "current_workspaces": ws, "role_to_grant": _role_for(ws) if user else None,
                  "already_in_sci": in_sci},
        "blockers": blockers,
        "ready": not blockers,
    }


def apply(db: Session, actor: User, email: str) -> Dict[str, Any]:
    """Run the setup. Idempotent: a second run changes nothing it already did."""
    p = plan(db, email)
    if not p["ready"]:
        raise ValueError("; ".join(p["blockers"]))
    from app.models.models import Platform
    from app.models.telephony_models import PhoneNumber
    from app.services import customer_provisioning as cp, workspace_access
    from app.services.programs import aliases, campuses, regional_pools as rp, setup

    plat = db.query(Platform).filter(Platform.slug == "evosyspro").first()
    if plat is None:
        raise ValueError("Platform evosyspro is missing.")
    orgs = _orgs(db)
    created = False
    if orgs:
        org = orgs[0]
    else:
        org, _ = cp.create_customer(db, actor, name=ORG_NAME, platform_id=plat.id, industry="funeral",
                                    timezone="America/Chicago")
        org.enabled_features = json.dumps(FEATURES)
        org.brand_name = ORG_NAME
        created = True

    names = location_names()
    prog = setup.ensure_program(db, org, name=ORG_NAME, primary_contact_name="Kerry Allan",
                                primary_contact_title="Head of Sales", hero_title=ORG_NAME,
                                hero_subtitle="Family Service Lead & Communication Center")
    profiles = setup.ensure_locations(db, org, actor, names)
    families = setup.ensure_campaign_families(db, org)          # all OFF
    db.flush()
    assigned = aliases.assign(db, prog)
    campus_result = campuses.assign(db, org.id, campuses.load_grouping())
    prog.staff_sms_alerts_enabled = True
    if not prog.mailbox_folder_path:
        prog.mailbox_folder_path = MAIL_FOLDER

    tf = _toll_free_state(db, org.id)
    if tf["action"] in ("create", "update"):
        label = rp.POOL_LABEL_PREFIX + rp.TOLL_FREE_POOL["pool_id"]
        route = {"mode": "voicemail_only", "voicemail": True, "ring_user_ids": []}
        rec = db.query(PhoneNumber).filter(PhoneNumber.e164 == rp.TOLL_FREE).first()
        if rec is None:
            rec = PhoneNumber(e164=rp.TOLL_FREE, provider="twilio", organization_id=org.id, created_by_id=actor.id)
            db.add(rec)
        rec.workspace_id, rec.label, rec.is_active = None, label, True
        rec.cap_sms, rec.cap_voice_inbound, rec.cap_voicemail, rec.cap_voice_outbound = True, True, True, False
        rec.default_inbound_route = json.dumps(route)
    db.commit()

    user = _user(db, email)
    role = _role_for(_workspaces_of(db, user))
    workspace_access.grant_workspace_membership(db, user.id, org.id, role=role, granted_by=actor.id)
    workspace_access.invalidate_workspace_memberships(user)     # report what the login holds NOW

    return {
        "organization_id": org.id, "organization_created": created,
        "locations": sum(1 for x in profiles.values() if not x.is_review_bucket),
        "campaign_families": len(families), "campaigns_on": sum(1 for f in families if f.is_active),
        "location_emails_assigned": len(assigned), "campuses": campus_result,
        "toll_free": tf, "login": {"email": user.email, "role": role},
        "workspaces_now": _workspaces_of(db, user),
    }
