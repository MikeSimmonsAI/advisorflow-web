"""ONE PERSON, EVERY CONTEXT THEY HOLD, AND HOW TO CORRECT IT.

═══════════════════════════════════════════════════════════════════════════
THE FAILURE THIS EXISTS TO MAKE IMPOSSIBLE
═══════════════════════════════════════════════════════════════════════════

Christina was provisioned into the wrong brand: EvoSys Pro, as an org admin,
when she should hold BookaBoost executive and sales-manager authority. Before
this module the only way to correct that was to delete her and create a second
Christina — which throws away her login, her activity, her attribution on
everything she has touched, and the audit trail that says any of it happened.

    A PERSON IS ONE IDENTITY. Their access is memberships, roles,
    capabilities and entitlements. Being put in the wrong place is a
    correction, not a reincarnation.

So a "move" here is: create the correct membership, verify it, then deactivate
the incorrect one. The `users` row is never touched by it and the user id never
changes, which is what keeps every foreign key, every audit entry and every
"assigned to" pointing at the same human afterwards.

═══════════════════════════════════════════════════════════════════════════
THIS IS NOT A SECOND AUTHORITY SYSTEM
═══════════════════════════════════════════════════════════════════════════

Every write below goes through the mechanism that already owns that kind of
authority:

    customer workspace access   → workspace_access.grant/revoke_workspace_membership
    brand sales / executive     → Membership rows, the vocabulary in sales_models
    executive portfolio         → executive_authority.assign / unassign
    infrastructure capability   → capabilities.set_user_grants
    demo entitlement            → capabilities.set_platform_grants
    training                    → training_service.assign / revoke

Nothing here invents a role, a scope or a permission. What it adds is a single
place where a person's whole footprint can be READ, a plan can be PREVIEWED,
and the minimum set of changes can be APPLIED as one audited operation.

═══════════════════════════════════════════════════════════════════════════
GOD IS THE ONLY ROOT AUTHORITY, AND THIS SURFACE DOES NOT LOOSEN THAT
═══════════════════════════════════════════════════════════════════════════

`god_admin` cannot be granted here at all — not as a platform role, not as a
template, not as a capability. Nothing in this module can produce a second
platform owner, and nothing here lets any role grant itself anything. The route
is god-only; this is the belt to that braces, because a provisioning surface
that could mint its own authority is the one bug in this area that cannot be
recovered from.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.models import Organization, Platform, User, UserCapabilityGrant
from app.models.sales_models import (BRAND_SALES_ROLES, ROLE_BRAND_EXECUTIVE,
                                     ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, SCOPE_CUSTOMER_ORG,
                                     SCOPE_PLATFORM, BrandSalesOrg, Membership)
from app.services import capabilities, executive_authority, training_service
from app.services import training_catalog
from app.services import workspace_access

log = logging.getLogger(__name__)

# The platform roles this surface may set. `god_admin` is ABSENT and its
# absence is the point: root authority is not something a provisioning screen
# hands out. Changing somebody into a platform owner remains a deliberate,
# separate act.
ASSIGNABLE_PLATFORM_ROLES = ("super_admin", "org_admin", "advisor", "viewer")

# Roles inside a customer workspace, imported rather than restated so the two
# lists cannot drift.
WORKSPACE_ROLES = workspace_access.WORKSPACE_ROLES

SCOPE_LABELS = {
    SCOPE_PLATFORM: "Brand",
    SCOPE_BRAND_SALES_ORG: "Sales organization",
    SCOPE_CUSTOMER_ORG: "Customer workspace",
}

ROLE_LABELS = {
    ROLE_BRAND_EXECUTIVE: "Executive",
    ROLE_SALES_MANAGER: "Sales Manager",
    ROLE_SALES_REP: "Salesperson",
    "org_admin": "Workspace Admin",
    "advisor": "Workspace User",
    "viewer": "Workspace Viewer",
    "super_admin": "Platform Operator",
    "god_admin": "Platform Owner",
}


def _label_role(role: str) -> str:
    return ROLE_LABELS.get(role, (role or "").replace("_", " ").title())


# ─────────────────────────────────────────────────────────────────────────────
# THE DIRECTORY — names, so nobody has to read a UUID
# ─────────────────────────────────────────────────────────────────────────────

def directory(db: Session) -> Dict[str, Any]:
    """Everything the pickers need, named. Ids are carried but never displayed
    alone: an operator choosing where to put somebody is choosing a place, and
    a place has a name."""
    platforms = db.query(Platform).order_by(Platform.name).all()
    brands = db.query(BrandSalesOrg).order_by(BrandSalesOrg.name).all()
    orgs = db.query(Organization).order_by(Organization.name).all()
    plat_names = {p.id: p.name for p in platforms}
    return {
        "platforms": [{"id": p.id, "name": p.name, "slug": p.slug}
                      for p in platforms],
        "sales_organizations": [
            {"id": b.id, "name": b.name, "platform_id": b.platform_id,
             "platform_name": plat_names.get(b.platform_id),
             "is_active": bool(b.is_active),
             "is_demo": bool(getattr(b, "is_demo", False))}
            for b in brands],
        "workspaces": [
            {"id": o.id, "name": o.name, "slug": o.slug,
             "platform_id": o.platform_id,
             "platform_name": plat_names.get(o.platform_id),
             "is_active": bool(o.is_active),
             "is_demo": bool(getattr(o, "is_demo", False))}
            for o in orgs],
        "workspace_roles": [{"key": r, "label": _label_role(r)}
                            for r in WORKSPACE_ROLES],
        "sales_roles": [{"key": r, "label": _label_role(r)}
                        for r in BRAND_SALES_ROLES],
        "platform_roles": [{"key": r, "label": _label_role(r)}
                           for r in ASSIGNABLE_PLATFORM_ROLES],
        "training_paths": training_catalog.catalogue(),
        "templates": templates(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# THE FOOTPRINT — a person, in words a business owner can read
# ─────────────────────────────────────────────────────────────────────────────

def footprint(db: Session, target: User) -> Dict[str, Any]:
    """Everything this person holds, everywhere, grouped by what it means.

    HUMAN-READABLE FIRST. Every entry carries the id it resolves to, because a
    screen needs something to post back — but every entry also carries the NAME
    and a sentence saying what the access means, because the operator reading
    it is deciding about a person, not debugging a join.
    """
    platforms = {p.id: p for p in db.query(Platform).all()}
    brands = {b.id: b for b in db.query(BrandSalesOrg).all()}
    orgs = {o.id: o for o in db.query(Organization).all()}

    rows = (db.query(Membership)
            .filter(Membership.user_id == target.id).all())

    brand_contexts: List[Dict[str, Any]] = []
    back_office: List[Dict[str, Any]] = []
    workspaces: List[Dict[str, Any]] = []
    executive_assignments: List[Dict[str, Any]] = []

    for m in rows:
        base = {
            "membership_id": m.id,
            "scope_type": m.scope_type,
            "scope_type_label": SCOPE_LABELS.get(m.scope_type, m.scope_type),
            "scope_id": m.scope_id,
            "role": m.role,
            "role_label": _label_role(m.role),
            "is_active": bool(m.is_active),
            # A REVOKED ROW IS SHOWN, LABELLED. "There is no membership" and
            # "there was one and somebody switched it off" are different
            # diagnoses with different fixes, and a list of active rows alone
            # cannot tell them apart.
            "state": "active" if m.is_active else "revoked",
            "granted_at": m.created_at.isoformat() if m.created_at else None,
        }
        if m.scope_type == SCOPE_PLATFORM:
            plat = platforms.get(m.scope_id)
            brand_contexts.append({
                **base, "name": plat.name if plat else None,
                "resolves": plat is not None,
                "means": ("Sees the %s Executive Suite — revenue, performance "
                          "and the customers they are assigned."
                          % (plat.name if plat else "brand"))
                if m.role == ROLE_BRAND_EXECUTIVE else
                ("Holds %s in the %s brand context."
                 % (_label_role(m.role), plat.name if plat else "brand")),
            })
        elif m.scope_type == SCOPE_BRAND_SALES_ORG:
            bso = brands.get(m.scope_id)
            plat = platforms.get(bso.platform_id) if bso else None
            back_office.append({
                **base, "name": bso.name if bso else None,
                "platform_name": plat.name if plat else None,
                "resolves": bso is not None,
                "means": ("Runs the %s sales team — sees the whole team's "
                          "pipeline and approves pricing."
                          % (bso.name if bso else "brand"))
                if m.role == ROLE_SALES_MANAGER else
                ("Sells for %s — owns their own deals."
                 % (bso.name if bso else "the brand")),
            })
        elif m.scope_type == SCOPE_CUSTOMER_ORG:
            org = orgs.get(m.scope_id)
            plat = platforms.get(org.platform_id) if org and org.platform_id else None
            entry = {
                **base, "name": org.name if org else None,
                "platform_name": plat.name if plat else None,
                "resolves": org is not None,
                "organization_is_active": (None if org is None
                                           else bool(org.is_active)),
                "is_demo": bool(getattr(org, "is_demo", False)) if org else False,
            }
            if m.role == ROLE_BRAND_EXECUTIVE:
                # An org-scoped executive row is a PORTFOLIO ASSIGNMENT, not a
                # workspace membership, and showing it beside "can enter this
                # workspace" would be a lie — the executive cannot.
                entry["means"] = ("Appears in this executive's portfolio. Does "
                                  "NOT grant entry to the workspace.")
                executive_assignments.append(entry)
            else:
                entry["means"] = ("Can enter the %s workspace as %s."
                                  % (org.name if org else "workspace",
                                     _label_role(m.role)))
                workspaces.append(entry)

    # ── capability grants, by scope ──
    grants = (db.query(UserCapabilityGrant)
              .filter(UserCapabilityGrant.user_id == target.id).all())
    capability_rows = []
    demo_brands: Dict[str, Dict[str, Any]] = {}
    for g in grants:
        cap = capabilities.CAPABILITIES.get(g.capability)
        where = None
        if g.scope_type == SCOPE_PLATFORM:
            where = platforms.get(g.scope_id)
        elif g.scope_type == SCOPE_BRAND_SALES_ORG:
            where = brands.get(g.scope_id)
        else:
            where = orgs.get(g.organization_id or g.scope_id)
        capability_rows.append({
            "capability": g.capability,
            "label": cap.label if cap else g.capability,
            "registered": cap is not None,
            "scope_type": g.scope_type,
            "scope_type_label": SCOPE_LABELS.get(g.scope_type, g.scope_type),
            "scope_id": g.scope_id or g.organization_id,
            "scope_name": getattr(where, "name", None),
            "is_active": bool(g.is_active),
            "state": "active" if g.is_active else "revoked",
        })
        if (g.scope_type == SCOPE_PLATFORM and g.is_active
                and g.capability in capabilities.PLATFORM_SCOPED_CAPABILITIES):
            plat = platforms.get(g.scope_id)
            entry = demo_brands.setdefault(g.scope_id, {
                "platform_id": g.scope_id,
                "platform_name": plat.name if plat else None,
                "may_present": False, "may_admin": False})
            if g.capability == "demo_suite":
                entry["may_present"] = True
            if g.capability == "demo_admin":
                entry["may_admin"] = True

    is_god = getattr(target, "role", None) == "god_admin"
    home_org = (orgs.get(target.organization_id)
                if target.organization_id else None)

    return {
        "identity": {
            "user_id": target.id,
            "full_name": target.full_name,
            "email": target.email,
            "is_active": bool(target.is_active),
            "platform_role": target.role,
            "platform_role_label": _label_role(target.role),
            "is_platform_owner": is_god,
            "must_change_password": bool(
                getattr(target, "must_change_password", False)),
            "last_login_at": (target.last_login_at.isoformat()
                              if getattr(target, "last_login_at", None)
                              else None),
            "created_at": (target.created_at.isoformat()
                           if getattr(target, "created_at", None) else None),
            "home_organization_id": target.organization_id,
            "home_organization_name": home_org.name if home_org else None,
            # NULL organization_id is this architecture's positive assertion
            # that somebody belongs to the control plane and to no tenant.
            "is_internal": target.organization_id is None,
        },
        "brand_contexts": brand_contexts,
        "back_office": back_office,
        "workspaces": workspaces,
        "executive_assignments": executive_assignments,
        "capabilities": capability_rows,
        "demo": {
            "brands": sorted(demo_brands.values(),
                             key=lambda d: d["platform_name"] or ""),
            "is_platform_owner": is_god,
            "note": ("The platform owner can present every brand without a "
                     "grant." if is_god else
                     "Demo access is granted per brand and carries no other "
                     "authority."),
        },
        "training": training_service.for_user(db, target),
        "summary": _summary(target, brand_contexts, back_office, workspaces,
                            executive_assignments, demo_brands),
    }


def _summary(target: User, brand_contexts, back_office, workspaces,
             executive_assignments, demo_brands) -> List[str]:
    """The footprint in sentences. What an operator reads before they act."""
    out: List[str] = []
    if getattr(target, "role", None) == "god_admin":
        out.append("Platform owner. Holds root authority everywhere and is not "
                   "granted access per brand or per customer.")
    if not bool(target.is_active):
        out.append("This account is deactivated and cannot sign in.")
    active = lambda rows: [r for r in rows if r["is_active"]]
    for r in active(brand_contexts):
        out.append("%s in %s." % (r["role_label"], r["name"] or "an unknown brand"))
    for r in active(back_office):
        out.append("%s in %s." % (r["role_label"], r["name"] or "an unknown sales organization"))
    n_ws = len(active(workspaces))
    if n_ws:
        out.append("Can enter %d customer workspace%s: %s."
                   % (n_ws, "" if n_ws == 1 else "s",
                      ", ".join("%s (%s)" % (r["name"], r["role_label"])
                                for r in active(workspaces))))
    n_ex = len(active(executive_assignments))
    if n_ex:
        out.append("Executive portfolio covers %d customer%s."
                   % (n_ex, "" if n_ex == 1 else "s"))
    if demo_brands:
        out.append("Demo Suite access for %s."
                   % ", ".join(sorted(d["platform_name"] or "a brand"
                                      for d in demo_brands.values())))
    if not out:
        out.append("This person holds no access of any kind yet.")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# TEMPLATES — the shapes people are actually hired into
#
# EVERY TEMPLATE MAPS ONTO EXISTING AUTHORITY. None of them creates a role, and
# none of them is stored: a template is a way of typing less, not a new thing
# to keep in step with the roles it expands to.
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATES: List[Dict[str, Any]] = [
    {"key": "executive", "name": "Executive",
     "scope": SCOPE_PLATFORM, "role": ROLE_BRAND_EXECUTIVE,
     "picker": "platform",
     "what": "Sees the brand's Executive Suite — revenue, performance and the "
             "customers they are explicitly assigned.",
     "not": "Does not grant entry to any customer workspace, and grants no "
            "platform administration."},
    {"key": "sales_manager", "name": "Sales Manager",
     "scope": SCOPE_BRAND_SALES_ORG, "role": ROLE_SALES_MANAGER,
     "picker": "sales_organization",
     "what": "Runs a brand's sales team — the whole team's pipeline, "
             "approvals and team activity.",
     "not": "Does not see another brand, and does not see customer workspace "
            "data."},
    {"key": "salesperson", "name": "Salesperson",
     "scope": SCOPE_BRAND_SALES_ORG, "role": ROLE_SALES_REP,
     "picker": "sales_organization",
     "what": "Sells for a brand — owns their own deals, discovery and "
             "proposals.",
     "not": "Does not see the rest of the team's pipeline."},
    {"key": "workspace_admin", "name": "Workspace Admin",
     "scope": SCOPE_CUSTOMER_ORG, "role": "org_admin",
     "picker": "workspace",
     "what": "Administers one customer's workspace — its users, settings and "
             "configuration.",
     "not": "Holds no infrastructure capability unless that customer is "
            "permitted to self-manage it AND this person is granted it."},
    {"key": "workspace_user", "name": "Workspace User",
     "scope": SCOPE_CUSTOMER_ORG, "role": "advisor",
     "picker": "workspace",
     "what": "Works inside one customer's workspace — their own leads, "
             "conversations and calendar.",
     "not": "Does not see other advisors' books."},
    {"key": "workspace_viewer", "name": "Workspace Viewer",
     "scope": SCOPE_CUSTOMER_ORG, "role": "viewer",
     "picker": "workspace",
     "what": "Read-only access to one customer's workspace.",
     "not": "Cannot send, edit or configure anything."},
    {"key": "demo_presenter", "name": "Demo Presenter",
     "scope": SCOPE_PLATFORM, "role": None, "capability": "demo_suite",
     "picker": "platform",
     "what": "Can run a product demonstration for one brand against seeded "
             "fictional data.",
     "not": "Carries no customer access, no platform administration, and no "
            "access to another brand's demonstration."},
    {"key": "demo_admin", "name": "Demo Environment Administrator",
     "scope": SCOPE_PLATFORM, "role": None, "capability": "demo_admin",
     "picker": "platform",
     "what": "Can rebuild and re-seed a brand's demonstration environment.",
     "not": "Is not the same as being able to present it, and grants nothing "
            "outside the demonstration environment."},
]


def templates() -> List[Dict[str, Any]]:
    return [dict(t) for t in TEMPLATES]


def _template(key: str) -> Dict[str, Any]:
    for t in TEMPLATES:
        if t["key"] == key:
            return t
    raise HTTPException(status_code=400,
                        detail="Unknown access template %r." % key)


# ═══════════════════════════════════════════════════════════════════════════
# THE CHANGE PLAN — preview, confirm, execute, audit
#
# A consequential access change has to be UNDERSTANDABLE BEFORE IT HAPPENS.
# `preview` and `apply` therefore share one normalisation step and one
# expansion step, so what the operator confirmed is provably what runs: a
# preview computed by different code than the execution is a preview that will
# eventually describe something else.
# ═══════════════════════════════════════════════════════════════════════════

OPS = (
    "apply_template",
    "add_membership", "change_role", "remove_membership", "move_membership",
    "grant_demo", "revoke_demo",
    "assign_training", "revoke_training",
    "set_platform_role", "set_home_organization", "set_active",
)


class Change:
    """One resolved change, with the sentence that describes it.

    `kind` drives how a preview groups it — adding, removing, changing — and
    the sentence is what the operator actually reads. Both are produced here,
    once, so the confirmation screen and the audit entry say the same thing.
    """

    __slots__ = ("kind", "op", "sentence", "detail", "warning")

    def __init__(self, kind: str, op: str, sentence: str,
                 detail: Optional[Dict[str, Any]] = None,
                 warning: Optional[str] = None):
        self.kind = kind
        self.op = op
        self.sentence = sentence
        self.detail = detail or {}
        self.warning = warning

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "op": self.op, "sentence": self.sentence,
                "detail": self.detail, "warning": self.warning}


def _name_of(db: Session, scope_type: str, scope_id: str) -> Optional[str]:
    if scope_type == SCOPE_PLATFORM:
        row = db.query(Platform).filter(Platform.id == scope_id).first()
    elif scope_type == SCOPE_BRAND_SALES_ORG:
        row = db.query(BrandSalesOrg).filter(
            BrandSalesOrg.id == scope_id).first()
    elif scope_type == SCOPE_CUSTOMER_ORG:
        row = db.query(Organization).filter(Organization.id == scope_id).first()
    else:
        return None
    return getattr(row, "name", None)


def _require_scope(db: Session, scope_type: str, scope_id: str) -> str:
    """The scope exists, and is of the type claimed. 400 with the reason.

    Membership.scope_id is deliberately not a foreign key — it points at
    different tables depending on scope_type — so nothing in the database will
    catch a pasted id from the wrong table. This is where that is caught.
    """
    if scope_type not in SCOPE_LABELS:
        raise HTTPException(
            status_code=400,
            detail="Unknown scope type %r. Valid: %s."
                   % (scope_type, ", ".join(sorted(SCOPE_LABELS))))
    if not scope_id:
        raise HTTPException(status_code=400,
                            detail="No %s was named."
                                   % SCOPE_LABELS[scope_type].lower())
    name = _name_of(db, scope_type, scope_id)
    if name is None:
        raise HTTPException(
            status_code=400,
            detail="No %s with that id. A membership pointing at something "
                   "that does not exist grants nothing and cannot be removed "
                   "from a screen, so it is refused here."
                   % SCOPE_LABELS[scope_type].lower())
    return name


def _valid_role(scope_type: str, role: str) -> str:
    role = (role or "").strip()
    if scope_type == SCOPE_CUSTOMER_ORG:
        allowed = tuple(WORKSPACE_ROLES) + (ROLE_BRAND_EXECUTIVE,)
    elif scope_type == SCOPE_BRAND_SALES_ORG:
        allowed = BRAND_SALES_ROLES
    else:
        allowed = (ROLE_BRAND_EXECUTIVE,)
    if role not in allowed:
        raise HTTPException(
            status_code=400,
            detail="%s is not a role that means anything in a %s. Valid: %s."
                   % (role or "(none)", SCOPE_LABELS[scope_type].lower(),
                      ", ".join(allowed)))
    if role == "god_admin":                       # pragma: no cover - defensive
        raise HTTPException(status_code=400,
                            detail="Root authority is not granted here.")
    return role


def expand(db: Session, operations: List[Dict[str, Any]]
           ) -> List[Dict[str, Any]]:
    """Turn templates into the primitive operations they stand for.

    Done ONCE, before both preview and apply, so a template can never mean one
    thing on the confirmation screen and another when it runs.
    """
    out: List[Dict[str, Any]] = []
    for raw in operations or []:
        op = (raw.get("op") or "").strip()
        if op not in OPS:
            raise HTTPException(
                status_code=400,
                detail="Unknown operation %r. Valid: %s."
                       % (op, ", ".join(OPS)))
        if op != "apply_template":
            out.append(dict(raw))
            continue
        tpl = _template((raw.get("template") or "").strip())
        scope_id = raw.get("scope_id")
        if tpl.get("capability"):
            out.append({"op": "grant_demo", "platform_id": scope_id,
                        "admin": tpl["capability"] == "demo_admin",
                        "_from_template": tpl["key"]})
        else:
            out.append({"op": "add_membership", "scope_type": tpl["scope"],
                        "scope_id": scope_id, "role": tpl["role"],
                        "_from_template": tpl["key"]})
    return out


def _active_membership(db: Session, target: User, scope_type: str,
                       scope_id: str, role: Optional[str] = None
                       ) -> Optional[Membership]:
    q = (db.query(Membership)
         .filter(Membership.user_id == target.id,
                 Membership.scope_type == scope_type,
                 Membership.scope_id == scope_id,
                 Membership.is_active.is_(True)))
    if role:
        q = q.filter(Membership.role == role)
    return q.first()


# ─────────────────────────────────────────────────────────────────────────────
# PREVIEW
# ─────────────────────────────────────────────────────────────────────────────

def preview(db: Session, target: User,
            operations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What would change, what would be removed, and what would NOT change.

    THE UNCHANGED LIST IS NOT DECORATION. The fear an operator has when
    correcting somebody's placement is that they are about to lose the person's
    history, their login or the access they were right to have. Saying so
    explicitly, every time, is what makes the confirm button pressable.
    """
    ops = expand(db, operations)
    before = footprint(db, target)
    changes: List[Change] = []
    warnings: List[str] = []

    for raw in ops:
        op = raw["op"]

        if op in ("add_membership", "change_role"):
            st, sid = raw.get("scope_type"), raw.get("scope_id")
            name = _require_scope(db, st, sid)
            role = _valid_role(st, raw.get("role"))
            current = _active_membership(db, target, st, sid)
            if current is None:
                changes.append(Change(
                    "adding", op,
                    "Add %s in %s (%s)." % (_label_role(role), name,
                                            SCOPE_LABELS[st].lower()),
                    {"scope_type": st, "scope_id": sid, "name": name,
                     "role": role}))
            elif current.role == role:
                changes.append(Change(
                    "unchanged", op,
                    "Already %s in %s — nothing to do."
                    % (_label_role(role), name),
                    {"scope_type": st, "scope_id": sid, "name": name,
                     "role": role}))
            else:
                changes.append(Change(
                    "changing", op,
                    "Change %s from %s to %s."
                    % (name, _label_role(current.role), _label_role(role)),
                    {"scope_type": st, "scope_id": sid, "name": name,
                     "from_role": current.role, "role": role}))

        elif op == "remove_membership":
            st, sid = raw.get("scope_type"), raw.get("scope_id")
            name = _require_scope(db, st, sid)
            current = _active_membership(db, target, st, sid, raw.get("role"))
            if current is None:
                changes.append(Change(
                    "unchanged", op,
                    "No active access in %s to remove." % name,
                    {"scope_type": st, "scope_id": sid, "name": name}))
            else:
                changes.append(Change(
                    "removing", op,
                    "Remove %s in %s." % (_label_role(current.role), name),
                    {"scope_type": st, "scope_id": sid, "name": name,
                     "role": current.role}))

        elif op == "move_membership":
            src = raw.get("from") or {}
            dst = raw.get("to") or {}
            src_name = _require_scope(db, src.get("scope_type"),
                                      src.get("scope_id"))
            dst_name = _require_scope(db, dst.get("scope_type"),
                                      dst.get("scope_id"))
            role = _valid_role(dst.get("scope_type"), dst.get("role"))
            current = _active_membership(db, target, src["scope_type"],
                                         src["scope_id"])
            changes.append(Change(
                "adding", op,
                "Add %s in %s (%s)." % (_label_role(role), dst_name,
                                        SCOPE_LABELS[dst["scope_type"]].lower()),
                {"scope_type": dst["scope_type"], "scope_id": dst["scope_id"],
                 "name": dst_name, "role": role}))
            if current is None:
                changes.append(Change(
                    "unchanged", op,
                    "No active access in %s to remove." % src_name,
                    {"name": src_name}))
            else:
                changes.append(Change(
                    "removing", op,
                    "Remove %s in %s — after the new access is verified."
                    % (_label_role(current.role), src_name),
                    {"scope_type": src["scope_type"],
                     "scope_id": src["scope_id"], "name": src_name,
                     "role": current.role}))

        elif op == "grant_demo":
            pid = raw.get("platform_id")
            name = _require_scope(db, SCOPE_PLATFORM, pid)
            admin = bool(raw.get("admin"))
            held = capabilities.platform_grants_for(db, target.id, pid)
            wanted = ["demo_suite"] + (["demo_admin"] if admin else [])
            new = [k for k in wanted if k not in held]
            if not new:
                changes.append(Change(
                    "unchanged", op,
                    "Already has Demo Suite access for %s." % name,
                    {"platform_id": pid, "name": name}))
            else:
                changes.append(Change(
                    "adding", op,
                    "Grant %s for %s."
                    % (" and ".join(capabilities.CAPABILITIES[k].label.split(
                        " — ")[0] for k in new), name),
                    {"platform_id": pid, "name": name, "capabilities": wanted}))

        elif op == "revoke_demo":
            pid = raw.get("platform_id")
            name = _require_scope(db, SCOPE_PLATFORM, pid)
            held = capabilities.platform_grants_for(db, target.id, pid)
            if not held:
                changes.append(Change("unchanged", op,
                                      "No Demo Suite access for %s to remove."
                                      % name, {"platform_id": pid}))
            else:
                changes.append(Change(
                    "removing", op,
                    "Remove Demo Suite access for %s." % name,
                    {"platform_id": pid, "name": name, "capabilities": held}))

        elif op == "assign_training":
            path = training_catalog.path_or_404(raw.get("path_key"))
            existing = [t for t in training_service.for_user(db, target)
                        if t["path_key"] == path["key"] and t["is_active"]]
            if existing:
                changes.append(Change("unchanged", op,
                                      "Already assigned: %s." % path["name"],
                                      {"path_key": path["key"]}))
            else:
                changes.append(Change("adding", op,
                                      "Assign training: %s." % path["name"],
                                      {"path_key": path["key"],
                                       "name": path["name"]}))
                if path["requires_demo"]:
                    warnings.append(
                        "%s practises inside the Demo Suite. Without Demo "
                        "Suite access for a brand, its practice steps cannot "
                        "be completed." % path["name"])

        elif op == "revoke_training":
            path = training_catalog.path_or_404(raw.get("path_key"))
            changes.append(Change("removing", op,
                                  "Un-assign training: %s. Any completion "
                                  "already recorded is kept." % path["name"],
                                  {"path_key": path["key"]}))

        elif op == "set_platform_role":
            role = (raw.get("role") or "").strip()
            if role not in ASSIGNABLE_PLATFORM_ROLES:
                raise HTTPException(
                    status_code=400,
                    detail="%s is not a platform role this screen may set. "
                           "Valid: %s. Root authority is never granted here."
                           % (role or "(none)",
                              ", ".join(ASSIGNABLE_PLATFORM_ROLES)))
            if target.role == role:
                changes.append(Change("unchanged", op,
                                      "Platform role is already %s."
                                      % _label_role(role), {"role": role}))
            else:
                changes.append(Change(
                    "changing", op,
                    "Change platform role from %s to %s."
                    % (_label_role(target.role), _label_role(role)),
                    {"from_role": target.role, "role": role}))
                if target.role == "god_admin":
                    raise HTTPException(
                        status_code=400,
                        detail="This account is the platform owner. Removing "
                               "root authority is not done from a provisioning "
                               "screen.")

        elif op == "set_home_organization":
            org_id = raw.get("organization_id")
            if org_id:
                name = _require_scope(db, SCOPE_CUSTOMER_ORG, org_id)
                if target.organization_id == org_id:
                    changes.append(Change("unchanged", op,
                                          "Home organization is already %s."
                                          % name, {"organization_id": org_id}))
                else:
                    changes.append(Change(
                        "changing", op,
                        "Set home organization to %s." % name,
                        {"organization_id": org_id, "name": name}))
                    if _active_membership(db, target, SCOPE_CUSTOMER_ORG,
                                          org_id) is None:
                        warnings.append(
                            "Setting %s as the home organization without a "
                            "workspace membership there leaves the legacy "
                            "column and the membership disagreeing. Add the "
                            "workspace membership in the same change."
                            % name)
            else:
                if target.organization_id is None:
                    changes.append(Change(
                        "unchanged", op,
                        "Already a control-plane identity with no home "
                        "organization.", {}))
                else:
                    old = _name_of(db, SCOPE_CUSTOMER_ORG,
                                   target.organization_id)
                    changes.append(Change(
                        "changing", op,
                        "Clear the home organization (currently %s). The "
                        "account becomes a control-plane identity; workspace "
                        "access then comes from memberships alone."
                        % (old or "unknown"), {"organization_id": None}))

        elif op == "set_active":
            want = bool(raw.get("is_active"))
            if bool(target.is_active) == want:
                changes.append(Change("unchanged", op,
                                      "Account is already %s."
                                      % ("active" if want else "deactivated"),
                                      {"is_active": want}))
            else:
                changes.append(Change(
                    "changing", op,
                    "%s this account." % ("Reactivate" if want
                                          else "Deactivate"),
                    {"is_active": want}))

    adding = [c.to_dict() for c in changes if c.kind == "adding"]
    removing = [c.to_dict() for c in changes if c.kind == "removing"]
    changing = [c.to_dict() for c in changes if c.kind == "changing"]
    unchanged = [c.to_dict() for c in changes if c.kind == "unchanged"]
    for c in changes:
        if c.warning:
            warnings.append(c.warning)

    touched = {(d["detail"].get("scope_type"), d["detail"].get("scope_id"))
               for d in adding + removing + changing
               if d["detail"].get("scope_id")}
    untouched_workspaces = [w["name"] for w in before["workspaces"]
                            if w["is_active"]
                            and (SCOPE_CUSTOMER_ORG, w["scope_id"]) not in touched]

    return {
        "current": before,
        "adding": adding,
        "removing": removing,
        "changing": changing,
        "unchanged": unchanged,
        "warnings": sorted(set(warnings)),
        # SAID EXPLICITLY, EVERY TIME. This is the half of the preview that
        # makes the operator willing to press the button.
        "preserved": [
            "Login identity — the same account, the same email, the same "
            "password.",
            "Historical activity and attribution on everything this person "
            "has touched.",
            "Audit history, including the record of this change.",
            "Ownership of existing records and any reference to this user.",
        ] + (["Customer workspace access not named above: %s."
              % ", ".join(untouched_workspaces)]
             if untouched_workspaces else []),
        "requires_confirmation": bool(adding or removing or changing),
    }


# ─────────────────────────────────────────────────────────────────────────────
# APPLY
# ─────────────────────────────────────────────────────────────────────────────

def _grant_membership(db: Session, target: User, scope_type: str,
                      scope_id: str, role: str, actor: User) -> str:
    """Create or reactivate ONE membership, through whichever module owns it.

    Nothing here writes a `Membership` for a customer workspace by hand:
    `workspace_access` owns that shape, enforces the workspace role vocabulary
    and applies the plan's seat limit, and a second implementation would
    eventually disagree with it about all three.
    """
    if scope_type == SCOPE_CUSTOMER_ORG and role == ROLE_BRAND_EXECUTIVE:
        # An org-scoped executive row is a PORTFOLIO ASSIGNMENT, not workspace
        # access — `executive_authority` owns it and says so at length.
        executive_authority.assign(db, executive_user_id=target.id,
                                   organization_id=scope_id,
                                   granted_by_user_id=actor.id, commit=False)
        # Same reason as the flush below: the reactivate branch of `assign`
        # returns before flushing, and the verification that follows queries.
        db.flush()
        return "executive_assignment"
    if scope_type == SCOPE_CUSTOMER_ORG:
        workspace_access.grant_workspace_membership(
            db, target.id, scope_id, role=role, granted_by=actor.id,
            commit=False)
        workspace_access.invalidate_workspace_memberships(target)
        # FLUSHED HERE, AND THIS LINE IS LOAD-BEARING.
        #
        # `grant_workspace_membership` flushes on the branch that INSERTS a new
        # membership and returns early — without flushing — on the branch that
        # REACTIVATES an existing one. The session is created with
        # autoflush=False, so on the reactivate branch the change was still
        # pending in memory when `_verify_membership` issued its query, the
        # query saw the old row, verification failed, and the whole plan rolled
        # back with "the replacement access could not be verified".
        #
        # Which is to say: correcting somebody who had NEVER held access in
        # that workspace worked, and correcting somebody who had — the entire
        # point of the screen — did not. Caught by clicking the button, not by
        # the tests, which is why there is now a test for it below.
        db.flush()
        return "workspace_membership"

    # Brand-sales and platform scopes. Idempotent on (user, scope) WITHOUT the
    # role, for the same reason `grant_workspace_membership` is: the table's
    # unique constraint includes `role`, so inserting blind would leave one
    # person holding two live memberships in one scope the moment their role
    # changed, and every reader would answer with whichever came back first.
    existing = (db.query(Membership)
                .filter(Membership.user_id == target.id,
                        Membership.scope_type == scope_type,
                        Membership.scope_id == scope_id)
                .order_by(Membership.created_at.asc())
                .first())
    if existing is not None:
        existing.is_active = True
        existing.role = role
        if not existing.granted_by:
            existing.granted_by = actor.id
    else:
        db.add(Membership(user_id=target.id, scope_type=scope_type,
                          scope_id=scope_id, role=role, is_active=True,
                          granted_by=actor.id))
    db.flush()
    return "membership"


def _revoke_membership(db: Session, target: User, scope_type: str,
                       scope_id: str, role: Optional[str] = None) -> int:
    if scope_type == SCOPE_CUSTOMER_ORG and role == ROLE_BRAND_EXECUTIVE:
        executive_authority.unassign(db, executive_user_id=target.id,
                                     organization_id=scope_id, commit=False)
        return 1
    if scope_type == SCOPE_CUSTOMER_ORG and role is None:
        # Remove workspace access, and the executive assignment separately if
        # one exists — they are two different rows meaning two different
        # things, and "remove their access to this customer" means both.
        n = workspace_access.revoke_workspace_membership(
            db, target.id, scope_id, commit=False)
        workspace_access.invalidate_workspace_memberships(target)
        return n
    q = (db.query(Membership)
         .filter(Membership.user_id == target.id,
                 Membership.scope_type == scope_type,
                 Membership.scope_id == scope_id,
                 Membership.is_active.is_(True)))
    if role:
        q = q.filter(Membership.role == role)
    rows = q.all()
    for m in rows:
        # DEACTIVATED, NEVER DELETED. Who could enter this workspace in June is
        # exactly the question an incident asks afterwards.
        m.is_active = False
    db.flush()
    return len(rows)


def _verify_membership(db: Session, target: User, scope_type: str,
                       scope_id: str, role: str) -> None:
    """The new access is really there, before the old one is taken away.

    THIS IS THE 'MOVE' GUARANTEE. Create, verify, then remove — in that order,
    inside one transaction. If the verification fails, the whole plan is rolled
    back and the person still holds the access they had this morning, which is
    the only acceptable failure mode for a correction.
    """
    if scope_type == SCOPE_CUSTOMER_ORG and role == ROLE_BRAND_EXECUTIVE:
        ok = scope_id in executive_authority.assigned_org_ids(db, target.id)
    else:
        ok = _active_membership(db, target, scope_type, scope_id,
                                role) is not None
    if not ok:
        raise HTTPException(
            status_code=500,
            detail="The replacement access could not be verified, so nothing "
                   "was removed. The person still holds everything they held "
                   "before this change.")


def apply(db: Session, target: User, operations: List[Dict[str, Any]],
          actor: User) -> Dict[str, Any]:
    """Execute the minimum required changes, as one transaction, and audit it.

    ORDER IS FIXED AND IT MATTERS:

        1. additions and role changes      the new access exists first
        2. verification                    it is provably there
        3. removals                        only now is anything taken away
        4. entitlements and training       neither can strand anybody
        5. one audit entry, before → after

    Additions before removals is what makes a correction safe. Doing it the
    other way round means a failure halfway leaves the person with neither the
    old access nor the new one, mid-week, with no record of what they used to
    have.
    """
    from app.routers.audit_log_router import log_action

    if getattr(target, "role", None) == "god_admin":
        # The owner's authority is not administered from a provisioning screen.
        # Not because god cannot be trusted with it, but because a screen that
        # can edit root authority is a screen whose own bugs are unbounded.
        raise HTTPException(
            status_code=400,
            detail="This account is the platform owner. Root authority is not "
                   "administered from Manage Access.")

    ops = expand(db, operations)
    if not ops:
        raise HTTPException(status_code=400, detail="No changes were requested.")

    before = footprint(db, target)
    applied: List[str] = []

    adds: List[Tuple[str, str, str]] = []
    removes: List[Tuple[str, str, Optional[str]]] = []
    tail: List[Dict[str, Any]] = []

    for raw in ops:
        op = raw["op"]
        if op in ("add_membership", "change_role"):
            st, sid = raw.get("scope_type"), raw.get("scope_id")
            _require_scope(db, st, sid)
            adds.append((st, sid, _valid_role(st, raw.get("role"))))
        elif op == "remove_membership":
            st, sid = raw.get("scope_type"), raw.get("scope_id")
            _require_scope(db, st, sid)
            removes.append((st, sid, raw.get("role")))
        elif op == "move_membership":
            src, dst = raw.get("from") or {}, raw.get("to") or {}
            _require_scope(db, src.get("scope_type"), src.get("scope_id"))
            _require_scope(db, dst.get("scope_type"), dst.get("scope_id"))
            adds.append((dst["scope_type"], dst["scope_id"],
                         _valid_role(dst["scope_type"], dst.get("role"))))
            removes.append((src["scope_type"], src["scope_id"],
                            src.get("role")))
        else:
            tail.append(raw)

    try:
        # 1. ADDITIONS
        for st, sid, role in adds:
            _grant_membership(db, target, st, sid, role, actor)
            applied.append("added %s in %s" % (_label_role(role),
                                               _name_of(db, st, sid)))
        # 2. VERIFICATION
        for st, sid, role in adds:
            _verify_membership(db, target, st, sid, role)
        # 3. REMOVALS
        for st, sid, role in removes:
            n = _revoke_membership(db, target, st, sid, role)
            if n:
                applied.append("removed access in %s" % _name_of(db, st, sid))
        # 4. THE REST
        for raw in tail:
            applied.extend(_apply_tail(db, target, raw, actor))

        after = footprint(db, target)
        log_action(
            db, None, actor.id,
            action="access.manage", target_type="user", target_id=target.id,
            before=_digest(before), after=_digest(after),
            note="; ".join(applied) or "no effective change",
            details={"operations": ops},
            commit=False)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:                                # pragma: no cover
        db.rollback()
        log.exception("access change failed for user %s", target.id)
        raise HTTPException(
            status_code=500,
            detail="The access change could not be applied and nothing was "
                   "changed: %s" % str(e)[:300])

    log.info("AUDIT: access.manage target=%s by=%s: %s",
             target.email, actor.email, "; ".join(applied))
    return {"applied": applied, "footprint": footprint(db, target)}


def _apply_tail(db: Session, target: User, raw: Dict[str, Any],
                actor: User) -> List[str]:
    op = raw["op"]

    if op == "grant_demo":
        pid = raw.get("platform_id")
        name = _require_scope(db, SCOPE_PLATFORM, pid)
        held = set(capabilities.platform_grants_for(db, target.id, pid))
        held.add("demo_suite")
        if raw.get("admin"):
            held.add("demo_admin")
        capabilities.set_platform_grants(db, target, pid, name, actor,
                                         sorted(held), commit=False)
        return ["granted Demo Suite access for %s" % name]

    if op == "revoke_demo":
        pid = raw.get("platform_id")
        name = _require_scope(db, SCOPE_PLATFORM, pid)
        capabilities.set_platform_grants(db, target, pid, name, actor, [],
                                         commit=False)
        return ["removed Demo Suite access for %s" % name]

    if op == "assign_training":
        due = raw.get("due_at")
        due_at = None
        if due:
            try:
                due_at = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
                due_at = due_at.replace(tzinfo=None)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="due_at must be an ISO-8601 date or timestamp.")
        path = training_service.assign(
            db, user=target, path_key=raw.get("path_key"), actor=actor,
            due_at=due_at, note=raw.get("note"), commit=False)
        return ["assigned training: %s" % path.path_key]

    if op == "revoke_training":
        training_service.revoke(db, user=target, path_key=raw.get("path_key"),
                                actor=actor, commit=False)
        return ["un-assigned training: %s" % raw.get("path_key")]

    if op == "set_platform_role":
        role = (raw.get("role") or "").strip()
        if role not in ASSIGNABLE_PLATFORM_ROLES:
            raise HTTPException(
                status_code=400,
                detail="%s is not a platform role this screen may set. Root "
                       "authority is never granted here." % role)
        old = target.role
        target.role = role
        db.flush()
        return ["platform role %s → %s" % (old, role)]

    if op == "set_home_organization":
        org_id = raw.get("organization_id") or None
        if org_id:
            _require_scope(db, SCOPE_CUSTOMER_ORG, org_id)
        old = target.organization_id
        target.organization_id = org_id
        db.flush()
        return ["home organization %s → %s"
                % (_name_of(db, SCOPE_CUSTOMER_ORG, old) if old else "none",
                   _name_of(db, SCOPE_CUSTOMER_ORG, org_id) if org_id
                   else "none (control plane)")]

    if op == "set_active":
        want = bool(raw.get("is_active"))
        if target.id == getattr(actor, "id", None) and not want:
            raise HTTPException(
                status_code=400,
                detail="You cannot deactivate your own account.")
        target.is_active = want
        db.flush()
        return ["account %s" % ("reactivated" if want else "deactivated")]

    raise HTTPException(status_code=400,
                        detail="Unhandled operation %r." % op)      # defensive


def _digest(fp: Dict[str, Any]) -> Dict[str, Any]:
    """The audit's before/after. Access, never secrets.

    No tokens, no password hashes, no credentials — only which contexts the
    person held and what they were called. An audit entry that carried a secret
    would make the audit log the most sensitive table in the database, which is
    the opposite of what it is for.
    """
    act = lambda rows: ["%s: %s" % (r.get("name") or r.get("scope_id"),
                                    r.get("role_label"))
                        for r in rows if r.get("is_active")]
    return {
        "platform_role": fp["identity"]["platform_role"],
        "is_active": fp["identity"]["is_active"],
        "home_organization": fp["identity"]["home_organization_name"],
        "brand_contexts": act(fp["brand_contexts"]),
        "back_office": act(fp["back_office"]),
        "workspaces": act(fp["workspaces"]),
        "executive_assignments": act(fp["executive_assignments"]),
        "demo_brands": [d["platform_name"] for d in fp["demo"]["brands"]],
        "training": ["%s: %s" % (t["name"], t["status"])
                     for t in fp["training"] if t["is_active"]],
    }
