"""INSURANCE TEAM ACCESS — what a member may do, decided on the server (#523).

WHO IS AFFECTED
  * A person with an `agency_member_access` row in the ACTIVE workspace is an
    invited/configured member. Their access is exactly their effective access
    below, default deny, enforced by the fence (deps.get_current_user) on
    EVERY request and again by each retention route.
  * Everyone else keeps the behaviour they had before: owners/admins
    (org_admin, super_admin, god_admin), legacy managers and advisors with no
    row. A god_admin is never fenced.

EFFECTIVE ACCESS (precedence, highest first)
  1. Tenant: only the active workspace; nothing carries across organizations.
  2. Status: suspended/removed -> sign-in shell only (never a fallback to an
     ordinary advisor login).
  3. Permissions: profile permissions + personal grants - personal denials,
     each limited to actions the feature really has (CATALOG).
  4. Record visibility: personal override, else the profile's
     (assigned < team < locations < agency); always intersected with the
     member's own locations, and team visibility never reaches beyond them.
  5. Delegation: nobody can grant, edit or act on more than they hold now
     (permissions, visibility, locations); rechecked at invitation acceptance.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.models.models import User

ASSIGNED, TEAM, LOCATIONS, AGENCY = "assigned", "team", "locations", "agency"
SCOPES = (ASSIGNED, TEAM, LOCATIONS, AGENCY)
SCOPE_RANK = {ASSIGNED: 0, TEAM: 1, LOCATIONS: 2, AGENCY: 3}
SCOPE_LABELS = {ASSIGNED: "Assigned only", TEAM: "My team", LOCATIONS: "Permitted locations",
                AGENCY: "Entire agency"}
OWNER_ROLES = ("org_admin", "super_admin", "god_admin")
DASHBOARDS = ("admin", "manager", "producer", "retention", "scheduler", "analyst", "auto")

# Module -> (label, [(action, label, sensitive)]). Only actions a feature truly supports.
CATALOG: List[Tuple[str, str, List[Tuple[str, str, bool]]]] = [
    ("today", "Today / dashboard", [("view", "View", False)]),
    ("clients", "Clients & households", [("view", "View", False)]),
    ("policies", "Policies", [("view", "View", False)]),
    ("renewals", "Renewals", [("view", "View", False), ("edit", "Add notes & follow-ups", False),
                              ("claim", "Claim shared work", False), ("assign", "Assign / reassign", True),
                              ("outcome", "Record renewal outcome", True)]),
    ("conversations", "Conversations", [("view", "View", False), ("reply", "Reply / handle replies", False)]),
    ("appointments", "Appointments & calendar", [("view", "View", False), ("schedule", "Schedule", False),
                                                 ("edit", "Confirm / update", False)]),
    ("team", "Team & people", [("view", "View", False), ("manage", "Manage people & teams", True)]),
    ("locations", "Locations", [("view", "View", False), ("manage", "Manage locations", True)]),
    ("access", "Access profiles & invitations", [("view", "View", True), ("manage", "Invite & manage access", True)]),
    ("reports", "Reports", [("view", "View", False)]),
    ("imports", "Imports", [("import", "Import CSV", True)]),
    ("automation", "Automation", [("view", "View", False), ("manage", "Edit workflows", True),
                                  ("activate", "Start / pause workflows", True), ("run", "Run due steps", True)]),
    ("settings", "Agency settings", [("view", "View", False), ("manage", "Change settings", True)]),
]
MODULES = [m[0] for m in CATALOG]
ACTIONS = {m[0]: [a[0] for a in m[2]] for m in CATALOG}
MODULE_LABELS = {m[0]: m[1] for m in CATALOG}
ACTION_LABELS = {(m[0], a[0]): a[1] for m in CATALOG for a in m[2]}
FULL = {m: list(acts) for m, acts in ACTIONS.items()}
SENSITIVE = {(m[0], a[0]) for m in CATALOG for a in m[2] if a[2]}


def catalog_json() -> List[Dict[str, Any]]:
    return [{"key": k, "label": label, "actions": [{"key": a, "label": al, "sensitive": s} for a, al, s in acts]}
            for k, label, acts in CATALOG]


def _p(**mods) -> Dict[str, List[str]]:
    return {k: list(v) for k, v in mods.items()}


# Starter profiles. A profile is a reusable configuration, never an account
# type and never a statement that someone is licensed.
STARTERS: List[Dict[str, Any]] = [
    {"key": "agency_admin", "name": "Agency Administrator", "dashboard": "admin", "record_scope": AGENCY,
     "description": "Runs day-to-day operations across the agency: renewals, clients, appointments, teams, "
                    "locations and access. Does not include ownership, billing or platform settings.",
     "permissions": FULL},
    {"key": "location_manager", "name": "Location Manager", "dashboard": "manager", "record_scope": LOCATIONS,
     "description": "Oversees assigned locations: local priorities, the unassigned queue, workload, overdue "
                    "follow-ups and reassignment. Access administration only if separately delegated.",
     "permissions": _p(today=["view"], clients=["view"], policies=["view"],
                       renewals=["view", "edit", "claim", "assign", "outcome"], conversations=["view", "reply"],
                       appointments=["view", "schedule", "edit"], team=["view"], locations=["view"],
                       reports=["view"], automation=["view"])},
    {"key": "producer", "name": "Producer / Agent", "dashboard": "producer", "record_scope": ASSIGNED,
     "description": "Works an assigned book: renewals, client replies, follow-ups and review appointments. "
                    "May claim shared work at their locations.",
     "permissions": _p(today=["view"], clients=["view"], policies=["view"],
                       renewals=["view", "edit", "claim", "outcome"], conversations=["view", "reply"],
                       appointments=["view", "schedule", "edit"], team=["view"])},
    {"key": "retention", "name": "Retention / Customer Service Specialist", "dashboard": "retention",
     "record_scope": TEAM,
     "description": "Handles renewal questions, follow-ups and review coordination for their team, and escalates "
                    "to an authorized producer. No coverage changes or renewal outcomes by default.",
     "permissions": _p(today=["view"], clients=["view"], policies=["view"], renewals=["view", "edit", "claim"],
                       conversations=["view", "reply"], appointments=["view", "schedule", "edit"], team=["view"])},
    {"key": "scheduler", "name": "Appointment Setter / VA", "dashboard": "scheduler", "record_scope": LOCATIONS,
     "description": "Books and confirms review appointments for producers at their locations, with the minimum "
                    "client context needed. No policy "
                    "changes, outcomes, exports, automation or team administration.",
     "permissions": _p(today=["view"], clients=["view"], renewals=["view"], conversations=["view"],
                       appointments=["view", "schedule", "edit"])},
    {"key": "analyst", "name": "Read-only Analyst", "dashboard": "analyst", "record_scope": LOCATIONS,
     "aggregate_only": True,
     "description": "Views reports, cohorts and trends for permitted locations. No writes, sends, assignments or "
                    "invitations. Totals only - no customer identities.",
     "permissions": _p(reports=["view"])},
    {"key": "custom", "name": "Custom", "dashboard": "auto", "record_scope": ASSIGNED,
     "description": "Start from nothing and choose exactly what this person may do.",
     "permissions": _p(today=["view"])},
]


def clean(perms: Any, *, add_view: bool = True) -> Dict[str, List[str]]:
    """Only known modules and the actions that module really has. Any action
    implies the module's view (when it has one) unless add_view=False (denials)."""
    out: Dict[str, List[str]] = {}
    if not isinstance(perms, dict):
        return out
    for mod, acts in perms.items():
        if mod not in ACTIONS or not isinstance(acts, (list, tuple)):
            continue
        keep = [a for a in ACTIONS[mod] if a in acts]
        if keep and add_view and "view" in ACTIONS[mod] and "view" not in keep:
            keep.insert(0, "view")
        if keep:
            out[mod] = keep
    return out


def load(raw: Optional[str]) -> Dict[str, List[str]]:
    try:
        return clean(json.loads(raw or "{}"), add_view=False)
    except (TypeError, ValueError):
        return {}


def combine(profile: Dict[str, List[str]], grants: Dict[str, List[str]], denies: Dict[str, List[str]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for mod in MODULES:
        acts = set(profile.get(mod, [])) | set(grants.get(mod, []))
        acts -= set(denies.get(mod, []))
        if "view" in denies.get(mod, []):
            acts = set()          # without view, nothing else in the module makes sense
        if acts:
            out[mod] = [a for a in ACTIONS[mod] if a in acts]
    return out


def exceeds(a: Dict[str, List[str]], ceiling: Dict[str, List[str]]) -> List[str]:
    """Labels of everything in `a` that `ceiling` does not hold."""
    extra = []
    for mod, acts in a.items():
        for act in acts:
            if act not in ceiling.get(mod, []):
                extra.append("%s: %s" % (MODULE_LABELS.get(mod, mod), ACTION_LABELS.get((mod, act), act)))
    return extra


# ── who is a configured member, and their effective access ─────────────────

def is_owner_role(role: Optional[str]) -> bool:
    return role in OWNER_ROLES


def workspace_role(db: Session, user: User, org_id: str) -> Optional[str]:
    if getattr(user, "role", None) == "god_admin":
        return "god_admin"
    try:
        from app.services import workspace_access
        r = workspace_access.workspace_role(user, db, org_id)
        if r:
            return r
    except Exception:  # noqa: BLE001
        pass
    return getattr(user, "role", None) if user.organization_id == org_id else None


def member_row(db: Session, org_id: Optional[str], user_id: str):
    if not org_id:
        return None
    from app.models.agency_access_models import AgencyMemberAccess
    return (db.query(AgencyMemberAccess).filter(AgencyMemberAccess.organization_id == org_id,
                                                AgencyMemberAccess.user_id == user_id).first())


def user_location_ids(db: Session, org_id: str, user_id: str) -> List[str]:
    from app.models.location_models import UserLocation
    return [r[0] for r in db.query(UserLocation.location_id).filter(UserLocation.organization_id == org_id,
                                                                     UserLocation.user_id == user_id).all()]


def user_team_ids(db: Session, org_id: str, user_id: str) -> List[str]:
    from app.models.agency_access_models import AgencyTeam, AgencyTeamMember
    return [r[0] for r in db.query(AgencyTeamMember.team_id).join(AgencyTeam, AgencyTeam.id == AgencyTeamMember.team_id)
            .filter(AgencyTeamMember.organization_id == org_id, AgencyTeamMember.user_id == user_id,
                    AgencyTeam.is_archived.is_(False)).all()]


def team_mate_ids(db: Session, org_id: str, team_ids: Iterable[str]) -> Set[str]:
    from app.models.agency_access_models import AgencyTeamMember
    ids = list(team_ids)
    if not ids:
        return set()
    return {r[0] for r in db.query(AgencyTeamMember.user_id).filter(AgencyTeamMember.organization_id == org_id,
                                                                     AgencyTeamMember.team_id.in_(ids)).all()}


def effective(db: Session, user: User, org_id: str, row=None) -> Dict[str, Any]:
    """The configured member's effective access. Callers check `row` exists."""
    from app.models.agency_access_models import AgencyAccessProfile
    row = row if row is not None else member_row(db, org_id, user.id)
    prof = None
    if row is not None and row.profile_id:
        prof = db.query(AgencyAccessProfile).filter(AgencyAccessProfile.id == row.profile_id,
                                                    AgencyAccessProfile.organization_id == org_id).first()
    base = load(prof.permissions_json) if prof is not None and not prof.is_archived else {}
    perms = combine(base, load(row.grants_json) if row else {}, load(row.denies_json) if row else {})
    scope = (row.record_scope if row and row.record_scope in SCOPES else None) or \
            (prof.record_scope if prof is not None and prof.record_scope in SCOPES else ASSIGNED)
    teams = user_team_ids(db, org_id, user.id)
    return {"configured": True, "status": row.status if row else "removed", "permissions": perms,
            "record_scope": scope, "location_ids": user_location_ids(db, org_id, user.id), "team_ids": teams,
            "dashboard": (prof.dashboard if prof is not None else "auto"),
            "aggregate_only": bool(prof.aggregate_only) if prof is not None else False,
            "profile": ({"id": prof.id, "name": prof.name, "archived": bool(prof.is_archived)} if prof else None),
            "reports_to_user_id": row.reports_to_user_id if row else None}


# Legacy members (no access row): the behaviour they had before #523, written
# as permissions so every check reads the same way.
LEGACY_MANAGER = _p(today=["view"], clients=["view"], policies=["view"],
                    renewals=["view", "edit", "claim", "assign", "outcome"], conversations=["view", "reply"],
                    appointments=["view", "schedule", "edit"], team=["view", "manage"], locations=["view"],
                    reports=["view"], imports=["import"], automation=["view", "manage", "activate", "run"],
                    settings=["view"])
LEGACY_AGENT = _p(today=["view"], clients=["view"], policies=["view"], renewals=["view", "edit", "claim", "outcome"],
                  conversations=["view", "reply"], appointments=["view", "schedule", "edit"], team=["view"],
                  reports=["view"], automation=["view"], settings=["view"])


def legacy(role: Optional[str]) -> Dict[str, Any]:
    if is_owner_role(role):
        perms, scope, dash = FULL, AGENCY, "admin"
    elif role == "manager":
        perms, scope, dash = LEGACY_MANAGER, LOCATIONS, "manager"
    else:
        perms, scope, dash = LEGACY_AGENT, ASSIGNED, "producer"
    return {"configured": False, "status": "active", "permissions": perms, "record_scope": scope,
            "dashboard": dash, "aggregate_only": False, "profile": None, "team_ids": [], "location_ids": None}


def resolve_dashboard(eff: Dict[str, Any]) -> str:
    """The landing dashboard. 'auto' picks from what the person may actually do,
    so nobody lands on a page full of things they cannot use."""
    d = eff.get("dashboard") or "auto"
    p = eff.get("permissions") or {}
    if d != "auto":
        need = {"analyst": "reports", "scheduler": "appointments"}.get(d, "today")
        if need in p:
            return d
    if "today" not in p:
        return "analyst" if "reports" in p else "settings"
    if "assign" in p.get("renewals", []):
        return "manager" if eff.get("record_scope") != AGENCY else "admin"
    if "outcome" in p.get("renewals", []):
        return "producer"
    if "edit" in p.get("renewals", []) or "reply" in p.get("conversations", []):
        return "retention"
    if "schedule" in p.get("appointments", []):
        return "scheduler"
    return "producer"


# ── the route map for the retention API (default deny) ──────────────────────
# (method regex, path regex under /agency/retention, module, action). The
# first match wins; anything unmatched is refused for a configured member.
_R = r"[^/]+"
ROUTES: List[Tuple[str, str, Optional[str], Optional[str]]] = [
    ("GET", r"/workspace", None, None),
    ("GET", r"/me", None, None),
    ("PATCH", r"/me/prefs", None, None),
    ("GET", r"/dashboard", None, None),               # content is filtered by the dashboard itself
    ("GET", r"/today", "today", "view"),
    ("POST", r"/ask", "today", "view"),
    ("GET", r"/renewals", "renewals", "view"),
    ("POST", r"/renewals/bulk-assign", "renewals", "assign"),
    ("POST", r"/renewals/auto-route", "renewals", "assign"),
    ("GET", r"/renewals/%s" % _R, "renewals", "view"),
    ("GET", r"/renewals/%s/draft" % _R, "conversations", "reply"),
    ("GET", r"/renewals/%s/availability" % _R, "appointments", "schedule"),
    ("POST", r"/renewals/%s/assign" % _R, "renewals", "assign"),
    ("POST", r"/renewals/%s/claim" % _R, "renewals", "claim"),
    ("POST", r"/renewals/%s/book-review" % _R, "appointments", "schedule"),
    ("POST", r"/renewals/%s/outcome" % _R, "renewals", "outcome"),
    ("POST", r"/renewals/%s/note" % _R, "renewals", "edit"),
    ("POST", r"/renewals/%s/escalate" % _R, "renewals", "edit"),
    ("POST", r"/renewals/%s/simulate-reply" % _R, "conversations", "reply"),
    ("POST", r"/renewals/%s/run-next-step" % _R, "automation", "run"),
    ("GET", r"/workflows", "automation", "view"),
    ("POST", r"/workflows", "automation", "manage"),
    ("PATCH", r"/workflows/%s" % _R, "automation", "manage"),
    ("GET", r"/workflows/%s/preview" % _R, "automation", "view"),
    ("POST", r"/workflows/%s/(start|pause|resume)" % _R, "automation", "activate"),
    ("POST", r"/workflows/%s/run-due" % _R, "automation", "run"),
    ("GET", r"/clients", "clients", "view"),
    ("GET", r"/clients/%s" % _R, "clients", "view"),
    ("GET", r"/clients/%s/summary" % _R, "clients", "view"),
    ("GET", r"/conversations", "conversations", "view"),
    ("GET", r"/appointments", "appointments", "view"),
    ("POST", r"/appointments/%s/status" % _R, "appointments", "edit"),
    ("GET", r"/team", "team", "view"),
    ("PATCH", r"/team/%s" % _R, "team", "manage"),
    ("GET", r"/reports", "reports", "view"),
    ("POST", r"/import/(preview|commit)", "imports", "import"),
    ("PATCH", r"/settings", "settings", "manage"),
    ("POST", r"/demo/reset", "settings", "manage"),
    # Team & Access administration
    ("GET", r"/access/catalog", "access", "view"),
    ("GET", r"/access/(people|teams)", "team", "view"),
    ("GET", r"/access/locations", "locations", "view"),
    ("GET", r"/access/(profiles|invitations|history)", "access", "view"),
    ("GET", r"/access/people/%s" % _R, "team", "view"),
    ("POST", r"/access/preview", "access", "view"),
    ("POST", r"/access/(invitations|profiles)(/.*)?", "access", "manage"),
    ("PATCH", r"/access/(profiles|people)/%s" % _R, "access", "manage"),
    ("POST", r"/access/people/%s/(suspend|reactivate|remove|locations)" % _R, "access", "manage"),
    ("GET", r"/access/people/%s/owned-work" % _R, "access", "manage"),
    ("POST", r"/access/teams(/.*)?", "team", "manage"),
    ("PATCH", r"/access/teams/%s" % _R, "team", "manage"),
    ("POST", r"/access/locations(/.*)?", "locations", "manage"),
    ("PATCH", r"/access/locations/%s" % _R, "locations", "manage"),
]
_COMPILED = [(m, re.compile("^/agency/retention" + p + "$"), mod, act) for m, p, mod, act in ROUTES]


def route_permission(path: str, method: str):
    """(matched, module, action). Unmatched -> (False, None, None) = deny."""
    meth = (method or "GET").upper()
    if meth == "HEAD":
        meth = "GET"
    for m, rx, mod, act in _COMPILED:
        if m == meth and rx.match(path):
            return True, mod, act
    return False, None, None


_SHELL = ("/auth/", "/branding", "/notifications", "/me", "/health", "/push", "/work/identity",
          "/workspace-views", "/program/status", "/launch/me", "/agency-invites")
_SETTINGS_WRITABLE = ("/settings/profile", "/settings/profile-photo", "/settings/notifications")


def _under(path: str, prefixes) -> bool:
    for p in prefixes:
        b = p.rstrip("/")
        if path == b or path.startswith(b + "/"):
            return True
    return False


def allows(eff: Dict[str, Any], module: Optional[str], action: Optional[str]) -> bool:
    if module is None:
        return True
    return action in (eff.get("permissions") or {}).get(module, [])


def refusal(db: Session, user: User, org_id: str, path: str, method: str) -> Optional[str]:
    """The fence for a configured member. None = go ahead."""
    path = path or "/"
    row = member_row(db, org_id, user.id)
    if row is None:
        return None
    meth = (method or "GET").upper()
    if _under(path, ("/auth/", "/branding", "/health", "/agency-invites")):
        return None
    if row.status != "active":
        if meth in ("GET", "HEAD") and (_under(path, _SHELL) or path in ("/agency/retention/me",
                                                                       "/agency/retention/workspace")):
            return None
        return "Your access to this agency has been suspended or removed. Ask the agency owner."
    if _under(path, ("/settings",)):
        if _under(path, ("/settings/admin",)):
            return "This login is for the agency workspace only."
        return None if meth in ("GET", "HEAD") or _under(path, _SETTINGS_WRITABLE) else \
            "This login is for the agency workspace only."
    if _under(path, _SHELL):
        return None
    if not path.startswith("/agency/retention/"):
        return "This login is for the agency workspace only."
    matched, mod, act = route_permission(path, meth)
    if not matched:
        return "Your access doesn't include this."
    eff = effective(db, user, org_id, row)
    if not allows(eff, mod, act):
        return "Your access doesn't include %s: %s. Ask the agency owner." % (
            MODULE_LABELS.get(mod, mod), ACTION_LABELS.get((mod, act), act))
    return None


def fence_user(db: Session, user: User, path: str, method: str) -> Optional[str]:
    """Called from deps.get_current_user for advisor/viewer logins only."""
    if getattr(user, "role", None) not in ("advisor", "viewer"):
        return None
    try:
        from app.services.lead_scope import active_workspace_org_id
        org_id = active_workspace_org_id(user, db)
    except Exception:  # noqa: BLE001 - never widen
        org_id = getattr(user, "organization_id", None)
    if not org_id:
        return None
    return refusal(db, user, org_id, path, method)


# ── delegation ceiling ──────────────────────────────────────────────────────

def ceiling(db: Session, actor: User, org_id: str) -> Dict[str, Any]:
    """What `actor` holds here and may hand out. Owners hold everything."""
    role = workspace_role(db, actor, org_id)
    row = member_row(db, org_id, actor.id)
    if is_owner_role(role) and row is None:
        return {"owner": True, "permissions": FULL, "record_scope": AGENCY, "location_ids": None}
    if row is None:
        eff = legacy(role)
        return {"owner": False, "permissions": eff["permissions"], "record_scope": eff["record_scope"],
                "location_ids": user_location_ids(db, org_id, actor.id)}
    eff = effective(db, actor, org_id, row)
    if row.status != "active":
        return {"owner": False, "permissions": {}, "record_scope": ASSIGNED, "location_ids": []}
    return {"owner": False, "permissions": eff["permissions"], "record_scope": eff["record_scope"],
            "location_ids": eff["location_ids"]}


def check_delegation(db: Session, actor: User, org_id: str, perms: Dict[str, List[str]], record_scope: str,
                     location_ids: Optional[Iterable[str]] = None) -> None:
    """Raise PermissionError if the actor would hand out more than they hold."""
    c = ceiling(db, actor, org_id)
    extra = exceeds(perms, c["permissions"])
    if extra:
        raise PermissionError("You can't give access you don't have: %s." % ", ".join(extra[:4]))
    if SCOPE_RANK.get(record_scope, 0) > SCOPE_RANK.get(c["record_scope"], 0):
        raise PermissionError("You can't give wider record visibility than your own.")
    if c["location_ids"] is not None and location_ids:
        outside = set(location_ids) - set(c["location_ids"])
        if outside:
            raise PermissionError("You can only assign locations you work in.")
