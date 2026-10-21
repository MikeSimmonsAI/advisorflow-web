"""PROVISION A WHOLESALE WORKSPACE - a second (third, fourth...) tenant of the
shared Wholesale engine. God-only, dry run unless applied, idempotent.
LANE-E-BOOKABOOST (#272, steps P2 and P7).

What it does, and nothing else:
  1. The organization: an existing one named by id, or created on the named
     brand (platforms.slug) if no active org of that name exists there. Refused
     if more than one matches, if the org belongs to a different brand, or if it
     is EvoSys's own Wholesale org (that one is built in and never re-provisioned).
  2. Its feature list gains the Wholesale set plus `wholesale_workspace`, which
     is what makes it a Wholesale workspace (program_experience.is_wholesale_host).
     Keys it already has are kept. A legacy NULL ("everything") list is refused,
     not silently narrowed.
  3. EvoSense kill switches: created with every OUTBOUND and PAID switch paused
     (SMS, email, voice, AI replies, paid data, discovery). An existing row is
     reported and left exactly as it is.
  4. Wholesale settings row (the engine's defaults).
  5. Optional: workspace memberships for logins the owner NAMES by email. No one
     is added implicitly - not EvoSys operators, not brand staff. The Wholesale
     named-login seat is a separate, explicit God step (/god/access-setup/wholesale).

It never copies, moves or reads another tenant's Wholesale data, never turns on
a campaign, sender, hunt or paid provider, and never creates a god account.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization, Platform, User

CONFIRM = "SET UP WHOLESALE"
WORKSPACE_KEY = "wholesale_workspace"
# What a Wholesale workspace needs to run (org_blueprints' Wholesale blueprint
# plus the core it depends on). Nothing outbound is switched on by a feature key;
# sending stays behind the EvoSense pauses below and the platform send gates.
WHOLESALE_FEATURES = ["leads", "users", "reports", "imports", "compliance", "sms", "email", "ai_assist",
                      "calendar", "availability", "booking", "wholesale_real_estate", WORKSPACE_KEY]
PAUSED_ON_CREATE = ("paused_discovery", "paused_paid_data", "paused_sms", "paused_email",
                    "paused_voice", "paused_ai_replies")
WORKSPACE_ROLES = ("org_admin", "manager", "advisor", "viewer")


def _features(org: Optional[Organization]) -> Optional[List[str]]:
    """The org's stored allow-list; None for a legacy NULL list."""
    if org is None:
        return []
    raw = getattr(org, "enabled_features", None)
    if raw is None:
        return None
    try:
        keys = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return list(keys) if isinstance(keys, list) else []


def _platform(db: Session, brand_slug: str) -> Optional[Platform]:
    return db.query(Platform).filter(Platform.slug == (brand_slug or "").strip().lower()).first()


def _find(db: Session, *, org_id: Optional[str], name: str, platform: Optional[Platform]) -> List[Organization]:
    if org_id:
        o = db.query(Organization).filter(Organization.id == org_id).first()
        return [o] if o else []
    if platform is None or not (name or "").strip():
        return []
    return (db.query(Organization)
            .filter(Organization.platform_id == platform.id, Organization.name == name.strip(),
                    Organization.is_active.isnot(False)).all())


def _controls(db: Session, org_id: Optional[str]):
    if not org_id:
        return None
    from app.models.evosense_models import EvoSenseControl
    return db.query(EvoSenseControl).filter(EvoSenseControl.organization_id == org_id).first()


def _logins(db: Session, emails: List[str], org_id: Optional[str]) -> List[Dict[str, Any]]:
    from app.services import workspace_access
    out = []
    for e in [x.strip().lower() for x in (emails or []) if (x or "").strip()]:
        u = db.query(User).filter(User.email.ilike(e)).first()
        held = None
        if u is not None and org_id:
            held = next((m.role for m in workspace_access.workspace_memberships(u, db)
                         if m.scope_id == org_id), None)
        out.append({"email": e, "found": u is not None, "user_id": getattr(u, "id", None),
                    "is_god": getattr(u, "role", None) == "god_admin", "already_member_as": held})
    return out


def plan(db: Session, *, name: str = "", brand_slug: str = "", org_id: Optional[str] = None,
         emails: Optional[List[str]] = None, role: str = "org_admin") -> Dict[str, Any]:
    """What `apply` would do. Writes nothing."""
    from app.services.program_experience import WHOLESALE_HOST_SLUG
    platform = _platform(db, brand_slug) if brand_slug else None
    found = _find(db, org_id=org_id, name=name, platform=platform)
    org = found[0] if len(found) == 1 else None
    blockers: List[str] = []
    if org_id and org is None:
        blockers.append("No organization with id %s." % org_id)
    if not org_id:
        if not brand_slug:
            blockers.append("A brand is required (e.g. bookaboost).")
        elif platform is None:
            blockers.append("No brand with slug %s." % brand_slug)
        if not (name or "").strip():
            blockers.append("An organization name is required.")
        if len(found) > 1:
            blockers.append("%d active organizations on %s are named %s; name one by id." % (len(found), brand_slug, name))
    if org is not None:
        if (org.slug or "") == WHOLESALE_HOST_SLUG:
            blockers.append("That is EvoSys's own Wholesale workspace; it is built in and is never re-provisioned here.")
        if platform is not None and org.platform_id != platform.id:
            blockers.append("%s belongs to another brand (%s), not %s." % (org.name, org.platform_id, brand_slug))
        if org.is_active is False:
            blockers.append("%s is not active." % org.name)
    have = _features(org)
    if have is None:
        blockers.append("%s has a legacy open feature list (everything on). Set its features explicitly first." % org.name)
    if role not in WORKSPACE_ROLES:
        blockers.append("Role must be one of %s." % ", ".join(WORKSPACE_ROLES))
    logins = _logins(db, emails or [], getattr(org, "id", None))
    for x in logins:
        if not x["found"]:
            blockers.append("No login with the email %s." % x["email"])
    to_add = [k for k in WHOLESALE_FEATURES if k not in (have or [])]
    ctl = _controls(db, getattr(org, "id", None))
    return {
        "organization": {
            "exists": org is not None, "id": getattr(org, "id", None),
            "name": getattr(org, "name", None) or (name or "").strip() or None,
            "brand": getattr(org, "platform_id", None) or getattr(platform, "id", None),
            "action": "use existing" if org else ("refused" if blockers else "create"),
        },
        "features": {"current": have, "to_add": to_add, "already_a_wholesale_workspace": not to_add and org is not None},
        "evosense_controls": ({"exists": True, "action": "left unchanged",
                               "paused": {k: bool(getattr(ctl, k)) for k in PAUSED_ON_CREATE + ("paused_all",)}}
                              if ctl is not None else
                              {"exists": False, "action": "create with outbound and paid paused",
                               "paused": {k: True for k in PAUSED_ON_CREATE}}),
        "logins": [{**x, "action": ("unchanged" if x["already_member_as"] else "add as %s" % role)} for x in logins],
        "never": ["copy or read another tenant's data", "send anything", "turn on a provider, hunt or campaign",
                  "grant the Wholesale named-login seat", "create a god account"],
        "blockers": blockers,
        "ready": not blockers,
        "confirm_with": CONFIRM,
    }


def apply(db: Session, actor: User, *, name: str = "", brand_slug: str = "", org_id: Optional[str] = None,
          emails: Optional[List[str]] = None, role: str = "org_admin", industry: str = "generic") -> Dict[str, Any]:
    """Run it. Idempotent: a second run with the same input changes nothing."""
    p = plan(db, name=name, brand_slug=brand_slug, org_id=org_id, emails=emails, role=role)
    if not p["ready"]:
        raise ValueError("; ".join(p["blockers"]))
    from app.models.evosense_models import EvoSenseControl
    from app.models.models import AuditLogEntry
    from app.services import customer_provisioning as cp, workspace_access
    from app.services.wholesale_service import resolve_settings

    created = False
    if p["organization"]["exists"]:
        org = db.query(Organization).filter(Organization.id == p["organization"]["id"]).first()
    else:
        platform = _platform(db, brand_slug)
        org, _ = cp.create_customer(db, actor, name=name.strip(), platform_id=platform.id,
                                    industry=industry, timezone="America/Chicago")
        org.brand_name = name.strip()
        created = True
        db.flush()

    have = _features(org) or []
    added = [k for k in WHOLESALE_FEATURES if k not in have]
    if added:
        org.enabled_features = json.dumps(have + added)

    ctl = _controls(db, org.id)
    controls_created = False
    if ctl is None:
        ctl = EvoSenseControl(organization_id=org.id, updated_by_id=actor.id)
        for k in PAUSED_ON_CREATE:
            setattr(ctl, k, True)
        db.add(ctl)
        controls_created = True
    db.flush()
    resolve_settings(db, org.id, commit=False)

    if created or added or controls_created:
        db.add(AuditLogEntry(organization_id=org.id, actor_user_id=actor.id, action="wholesale_workspace_provisioned",
                             target_type="organization", target_id=org.id,
                             details=json.dumps({"organization_created": created, "features_added": added,
                                                 "evosense_controls_created": controls_created,
                                                 "source": "LANE-E-BOOKABOOST #272"})))
    db.commit()

    granted = []
    for x in p["logins"]:
        if x["already_member_as"]:
            granted.append({"email": x["email"], "action": "unchanged", "role": x["already_member_as"]})
            continue
        workspace_access.grant_workspace_membership(db, x["user_id"], org.id, role=role, granted_by=actor.id)
        u = db.query(User).filter(User.id == x["user_id"]).first()
        workspace_access.invalidate_workspace_memberships(u)
        granted.append({"email": x["email"], "action": "added", "role": role})
    db.commit()

    return {"organization_id": org.id, "organization_name": org.name, "brand": org.platform_id,
            "organization_created": created, "features_added": added,
            "evosense_controls_created": controls_created, "logins": granted,
            "is_wholesale_workspace": True}
