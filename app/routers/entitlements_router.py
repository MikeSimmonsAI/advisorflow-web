"""GOD-ONLY FEATURE ENTITLEMENTS CONTROL PLANE — /god/entitlements.

Every answer here is produced by app/services/entitlement_resolver.evaluate(),
the same function `require_feature` enforces through, so the screen cannot
disagree with the server. Every write goes to three places: the override row,
the append-only feature_override_events history, and the platform audit log.

Counts are computed from the evaluation, never stored or estimated.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.entitlement_models import (
    PLATFORM_SCOPE_ID, SCOPES, FeatureOverride, FeatureOverrideEvent,
)
from app.models.location_models import Location
from app.models.models import Organization, Platform, User
from app.routers.audit_log_router import log_action
from app.services import entitlement_resolver as er
from app.services import entitlements as ent

router = APIRouter(prefix="/god/entitlements", tags=["god-entitlements"])

_SCOPE_ALIASES = {"organization": "org", "organisation": "org", "location": "workspace"}


def _norm_scope(scope: Optional[str]) -> str:
    s = (scope or "org").strip().lower()
    s = _SCOPE_ALIASES.get(s, s)
    if s not in SCOPES:
        raise HTTPException(400, "scope must be one of %s" % ", ".join(SCOPES))
    return s


def _allowed_roles() -> List[str]:
    from app.services.workspace_access import WORKSPACE_ROLES
    return sorted(set(WORKSPACE_ROLES))


class Target:
    """A validated scope selection. 404 on anything that does not exist or does
    not belong to the selected organization."""

    def __init__(self, db: Session, scope: str, platform_id: Optional[str] = None,
                 org_id: Optional[str] = None, workspace_id: Optional[str] = None,
                 role: Optional[str] = None, user_id: Optional[str] = None):
        self.scope = _norm_scope(scope)
        self.org: Optional[Organization] = None
        self.platform: Optional[Platform] = None
        self.workspace: Optional[Location] = None
        self.role = None
        self.user: Optional[User] = None
        self.platform_id = platform_id or None
        if self.scope in ("org", "workspace", "role", "user"):
            if not org_id:
                raise HTTPException(400, "org_id is required for scope %s" % self.scope)
            self.org = db.query(Organization).filter(Organization.id == org_id).first()
            if self.org is None:
                raise HTTPException(404, "Organization not found")
            self.platform_id = self.org.platform_id
        if self.scope == "brand":
            if not self.platform_id:
                raise HTTPException(400, "platform_id is required for scope brand")
        if self.platform_id:
            self.platform = db.query(Platform).filter(Platform.id == self.platform_id).first()
            if self.platform is None and self.scope == "brand":
                raise HTTPException(404, "Brand not found")
        if self.scope == "workspace":
            if not workspace_id:
                raise HTTPException(400, "workspace_id is required for scope workspace")
            self.workspace = db.query(Location).filter(
                Location.id == workspace_id, Location.organization_id == self.org.id).first()
            if self.workspace is None:
                raise HTTPException(404, "Workspace not found")
        if self.scope in ("role", "user") or (role and self.org is not None):
            if role:
                if role not in _allowed_roles():
                    raise HTTPException(400, "role must be one of %s" % ", ".join(_allowed_roles()))
                self.role = role
            elif self.scope == "role":
                raise HTTPException(400, "role is required for scope role")
        if self.scope == "user":
            if not user_id:
                raise HTTPException(400, "user_id is required for scope user")
            u = db.query(User).filter(User.id == user_id).first()
            if u is None or not _user_in_org(db, u, self.org.id):
                raise HTTPException(404, "User not found")
            self.user = u
            if not self.role:
                self.role = _role_of(db, u, self.org.id)

    @property
    def scope_id(self) -> Optional[str]:
        return {"platform": PLATFORM_SCOPE_ID, "brand": self.platform_id,
                "org": self.org.id if self.org else None,
                "workspace": self.workspace.id if self.workspace else None,
                "role": self.role, "user": self.user.id if self.user else None}[self.scope]

    @property
    def row_org_id(self) -> str:
        return self.org.id if (self.org and self.scope in ("workspace", "role", "user")) else ""

    def evaluate(self, db: Session, key: str, overrides, cache, legacy) -> Dict[str, Any]:
        return er.evaluate(
            db, key, org=self.org, platform_id=self.platform_id, level=self.scope,
            workspace_id=self.workspace.id if self.workspace else None,
            role=self.role, user_id=self.user.id if self.user else None,
            overrides=overrides, legacy_allowed=legacy, _cache=cache)

    def describe(self) -> Dict[str, Any]:
        return {
            "scope": self.scope, "scope_id": self.scope_id,
            "platform": ({"id": self.platform.id, "name": self.platform.name,
                          "slug": self.platform.slug} if self.platform else None),
            "organization": ({"id": self.org.id, "name": self.org.name, "plan": self.org.plan,
                              "allow_list_mode": ("all" if ent.legacy_enabled_for(self.org) is None
                                                  else "allow_list")}
                             if self.org else None),
            "workspace": ({"id": self.workspace.id, "name": getattr(self.workspace, "name", None)}
                          if self.workspace else None),
            "role": self.role,
            "user": ({"id": self.user.id, "name": self.user.full_name, "email": self.user.email}
                     if self.user else None),
        }


def _user_in_org(db: Session, user: User, org_id: str) -> bool:
    if user.organization_id == org_id:
        return True
    try:
        from app.services.workspace_access import has_workspace
        return bool(has_workspace(user, db, org_id))
    except Exception:  # noqa: BLE001
        return False


def _role_of(db: Session, user: User, org_id: str) -> Optional[str]:
    try:
        from app.services.workspace_access import workspace_role
        r = workspace_role(user, db, org_id)
    except Exception:  # noqa: BLE001
        r = None
    return r or user.role


def _actor_names(db: Session, ids) -> Dict[str, str]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {u.id: (u.full_name or u.email) for u in
            db.query(User).filter(User.id.in_(ids)).all()}


def _load(db: Session, t: Target):
    ov = er.load_overrides(db, t.org, t.platform_id)
    legacy = ent.legacy_enabled_for(t.org) if t.org is not None else None
    return ov, legacy


def _layer_state(res: Dict[str, Any], layer: str) -> Optional[Dict[str, Any]]:
    for l in res["layers"]:
        if l["layer"] == layer:
            return {"state": l["state"], "result": l["result"],
                    "override": l.get("override")}
    return None


def _row(db: Session, t: Target, key: str, ov, cache, legacy, names) -> Dict[str, Any]:
    res = t.evaluate(db, key, ov, cache, legacy)
    here = ov.get(t.scope, t.scope_id, key, t.row_org_id)
    # Last change among every row in this feature's chain.
    chain = [l.get("override") for l in res["layers"] if l.get("override")]
    last = max(chain, key=lambda o: o.get("updated_at") or "") if chain else None
    required_by = sorted(k for k, reqs in ent.REQUIRES.items() if key in reqs)
    return {
        "key": key, "label": ent.FEATURES[key], "category": er.category_of(key),
        "effective_state": res["state"], "enabled": res["enabled"],
        "inherited_from": res["inherited_from"],
        "override": ({"state": here.state, "reason": here.reason,
                      "updated_at": here.updated_at.isoformat() if here.updated_at else None,
                      "actor": names.get(here.actor_user_id)} if here else None),
        "is_inherited": here is None,
        "layers": {name: _layer_state(res, name) for name in SCOPES},
        "dependencies": res["dependencies"],
        "dependencies_met": res["dependencies_met"],
        "unmet_dependencies": [d["key"] for d in res["dependencies"] if not d["met"]],
        "required_by": required_by,
        "setup": res["setup"],
        "last_updated": ({"at": last.get("updated_at"),
                          "by": names.get(last.get("actor_user_id")),
                          "reason": last.get("reason")} if last else None),
    }


# ── Catalog / scopes ────────────────────────────────────────────────────────

@router.get("/catalog")
def catalog(db: Session = Depends(get_db), user: User = Depends(require_god)):
    feats = []
    counts: Dict[str, int] = {}
    for k in ent.ALL_FEATURE_KEYS:
        cat = er.category_of(k)
        counts[cat] = counts.get(cat, 0) + 1
        feats.append({"key": k, "label": ent.FEATURES[k], "category": cat,
                      "requires": list(ent.REQUIRES.get(k, ())),
                      "required_by": sorted(x for x, r in ent.REQUIRES.items() if k in r),
                      "setup_signal": er.SETUP_PROBES.get(k)})
    return {
        "features": feats,
        "categories": [{"name": c, "count": n} for c, n in sorted(counts.items())],
        "scopes": list(SCOPES),
        "states": ["enabled", "disabled", "inherited", "override",
                   "requires_setup", "blocked_by_dependency"],
        "plan_presets": ent.PLAN_FEATURES,
        "precedence": ("platform -> brand -> organization: the most specific explicit "
                       "override wins; with no organization override the organization "
                       "also needs its plan/allow-list. Workspace, role and user overrides "
                       "can only narrow. A prerequisite disabled by an explicit override "
                       "blocks its dependents."),
    }


@router.get("/scopes")
def scopes(org_id: Optional[str] = None, platform_id: Optional[str] = None,
           q: Optional[str] = None, limit: int = Query(50, ge=1, le=200),
           db: Session = Depends(get_db), user: User = Depends(require_god)):
    brands = [{"id": p.id, "name": p.name, "slug": p.slug}
              for p in db.query(Platform).order_by(Platform.name).all()]
    oq = db.query(Organization)
    if platform_id:
        oq = oq.filter(Organization.platform_id == platform_id)
    if q:
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        oq = oq.filter(Organization.name.ilike("%" + term + "%", escape="\\"))
    orgs = [{"id": o.id, "name": o.name, "platform_id": o.platform_id, "plan": o.plan}
            for o in oq.order_by(Organization.name).limit(limit).all()]
    workspaces, users = [], []
    if org_id:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if org is None:
            raise HTTPException(404, "Organization not found")
        workspaces = [{"id": l.id, "name": l.name} for l in
                      db.query(Location).filter(Location.organization_id == org_id)
                      .order_by(Location.name).all()]
        users = [{"id": u.id, "name": u.full_name or u.email, "role": u.role} for u in
                 db.query(User).filter(User.organization_id == org_id)
                 .order_by(User.full_name).limit(200).all()]
    return {"brands": brands, "organizations": orgs, "workspaces": workspaces,
            "roles": _allowed_roles(), "users": users}


# ── Matrix ──────────────────────────────────────────────────────────────────

@router.get("/matrix")
def matrix(scope: str = "org", platform_id: Optional[str] = None,
           org_id: Optional[str] = None, workspace_id: Optional[str] = None,
           role: Optional[str] = None, user_id: Optional[str] = None,
           db: Session = Depends(get_db), user: User = Depends(require_god)):
    t = Target(db, scope, platform_id, org_id, workspace_id, role, user_id)
    ov, legacy = _load(db, t)
    names = _actor_names(db, [r.actor_user_id for r in ov.rows])
    cache: Dict[str, Any] = {}
    rows = [_row(db, t, k, ov, cache, legacy, names) for k in ent.ALL_FEATURE_KEYS]
    kpis = {
        "total": len(rows),
        "enabled": sum(1 for r in rows if r["enabled"]),
        "disabled": sum(1 for r in rows if r["effective_state"] == "disabled"),
        "inherited": sum(1 for r in rows if r["is_inherited"]),
        "overrides": sum(1 for r in rows if not r["is_inherited"]),
        "requires_setup": sum(1 for r in rows if r["effective_state"] == "requires_setup"),
        "blocked": sum(1 for r in rows if r["effective_state"] == "blocked_by_dependency"),
    }
    return {"target": t.describe(), "kpis": kpis, "features": rows,
            "enforced_at_request_time": t.scope in ("platform", "brand", "org", "role", "user"),
            "note": (None if t.scope != "workspace" else
                     "Workspace (location) overrides are stored and shown here, but requests "
                     "carry no location context, so require_feature does not yet apply them.")}


# ── Writes ──────────────────────────────────────────────────────────────────

class OverrideIn(BaseModel):
    scope: str
    feature_key: str
    state: Optional[str] = None                 # enabled | disabled (set only)
    platform_id: Optional[str] = None
    org_id: Optional[str] = None
    workspace_id: Optional[str] = None
    role: Optional[str] = None
    user_id: Optional[str] = None
    reason: Optional[str] = Field(None, max_length=1000)


class BulkIn(BaseModel):
    action: str                                  # set | reset
    scope: str
    feature_keys: List[str]
    state: Optional[str] = None
    platform_id: Optional[str] = None
    org_id: Optional[str] = None
    workspace_id: Optional[str] = None
    role: Optional[str] = None
    user_id: Optional[str] = None
    reason: Optional[str] = Field(None, max_length=1000)


def _audit(db: Session, t: Target, actor: User, action: str, key: str,
           before: Optional[str], after: Optional[str], reason: Optional[str]) -> None:
    log_action(
        db, t.org.id if t.org else None, actor.id,
        action=action, target_type="feature_entitlement",
        target_id="%s:%s:%s" % (t.scope, t.scope_id, key),
        platform_id=t.platform_id,
        details={"scope": t.scope, "scope_id": t.scope_id, "feature_key": key},
        before={"state": before or "inherited"}, after={"state": after or "inherited"},
        note=reason, commit=False)


def _set(db: Session, t: Target, actor: User, key: str, state: str,
         reason: Optional[str]) -> None:
    if key not in ent.FEATURES:
        raise HTTPException(400, "Unknown feature key: %s" % key)
    if state not in ("enabled", "disabled"):
        raise HTTPException(400, "state must be 'enabled' or 'disabled'")
    before = ov_state(db, t, key)
    er.set_override(db, scope=t.scope, scope_id=t.scope_id, feature_key=key, state=state,
                    actor_user_id=actor.id, reason=reason, org_id=t.row_org_id or None)
    _audit(db, t, actor, "entitlement.override_set", key, before, state, reason)


def _reset(db: Session, t: Target, actor: User, key: str, reason: Optional[str]) -> bool:
    if key not in ent.FEATURES:
        raise HTTPException(400, "Unknown feature key: %s" % key)
    prev = er.reset_override(db, scope=t.scope, scope_id=t.scope_id, feature_key=key,
                             actor_user_id=actor.id, reason=reason,
                             org_id=t.row_org_id or None)
    if prev is None:
        return False
    _audit(db, t, actor, "entitlement.override_reset", key, prev, None, reason)
    return True


def ov_state(db: Session, t: Target, key: str) -> Optional[str]:
    r = db.query(FeatureOverride).filter(
        FeatureOverride.scope == t.scope, FeatureOverride.scope_id == t.scope_id,
        FeatureOverride.org_id == t.row_org_id, FeatureOverride.feature_key == key).first()
    return r.state if r else None


def _feature_view(db: Session, t: Target, key: str) -> Dict[str, Any]:
    ov, legacy = _load(db, t)
    names = _actor_names(db, [r.actor_user_id for r in ov.rows])
    return _row(db, t, key, ov, {}, legacy, names)


@router.post("/overrides")
def set_override(body: OverrideIn, db: Session = Depends(get_db),
                 user: User = Depends(require_god)):
    t = Target(db, body.scope, body.platform_id, body.org_id, body.workspace_id,
               body.role, body.user_id)
    _set(db, t, user, body.feature_key, (body.state or "").lower(), body.reason)
    db.commit()
    return {"ok": True, "feature": _feature_view(db, t, body.feature_key)}


@router.post("/overrides/reset")
def reset_override_post(body: OverrideIn, db: Session = Depends(get_db),
                        user: User = Depends(require_god)):
    t = Target(db, body.scope, body.platform_id, body.org_id, body.workspace_id,
               body.role, body.user_id)
    changed = _reset(db, t, user, body.feature_key, body.reason)
    db.commit()
    return {"ok": True, "reset": changed, "feature": _feature_view(db, t, body.feature_key)}


@router.delete("/overrides")
def reset_override_delete(scope: str, feature_key: str, platform_id: Optional[str] = None,
                          org_id: Optional[str] = None, workspace_id: Optional[str] = None,
                          role: Optional[str] = None, user_id: Optional[str] = None,
                          reason: Optional[str] = None,
                          db: Session = Depends(get_db), user: User = Depends(require_god)):
    t = Target(db, scope, platform_id, org_id, workspace_id, role, user_id)
    changed = _reset(db, t, user, feature_key, reason)
    db.commit()
    return {"ok": True, "reset": changed, "feature": _feature_view(db, t, feature_key)}


@router.post("/bulk")
def bulk(body: BulkIn, db: Session = Depends(get_db), user: User = Depends(require_god)):
    t = Target(db, body.scope, body.platform_id, body.org_id, body.workspace_id,
               body.role, body.user_id)
    action = (body.action or "").lower()
    if action not in ("set", "reset"):
        raise HTTPException(400, "action must be 'set' or 'reset'")
    keys = []
    for k in body.feature_keys:
        k = (k or "").strip().lower()
        if k not in ent.FEATURES:
            raise HTTPException(400, "Unknown feature key: %s" % k)
        if k not in keys:
            keys.append(k)
    if not keys:
        raise HTTPException(400, "feature_keys is empty")
    changed = 0
    for k in keys:
        if action == "set":
            _set(db, t, user, k, (body.state or "").lower(), body.reason)
            changed += 1
        elif _reset(db, t, user, k, body.reason):
            changed += 1
    db.commit()
    return {"ok": True, "action": action, "changed": changed, "feature_keys": keys}


# ── Diagnostics ─────────────────────────────────────────────────────────────

@router.get("/explain")
def explain(feature_key: str, scope: str = "org", platform_id: Optional[str] = None,
            org_id: Optional[str] = None, workspace_id: Optional[str] = None,
            role: Optional[str] = None, user_id: Optional[str] = None,
            db: Session = Depends(get_db), user: User = Depends(require_god)):
    if feature_key not in ent.FEATURES:
        raise HTTPException(404, "Unknown feature key")
    t = Target(db, scope, platform_id, org_id, workspace_id, role, user_id)
    res = er.explain(db, feature_key, org=t.org, platform_id=t.platform_id, level=t.scope,
                     workspace_id=t.workspace.id if t.workspace else None,
                     role=t.role, user_id=t.user.id if t.user else None)
    names = _actor_names(db, [(l.get("override") or {}).get("actor_user_id")
                              for l in res["steps"]])
    for l in res["steps"]:
        o = l.get("override")
        if o:
            o["actor"] = names.get(o.get("actor_user_id"))
    # Would the server actually let a customer in? The same call require_feature makes.
    if t.org is not None and t.scope == "org":
        res["server_enforcement"] = {"org_has_feature": ent.org_has_feature(t.org, feature_key)}
    res["target"] = t.describe()
    return res


@router.get("/history")
def history(feature_key: Optional[str] = None, scope: Optional[str] = None,
            platform_id: Optional[str] = None, org_id: Optional[str] = None,
            limit: int = Query(100, ge=1, le=500),
            db: Session = Depends(get_db), user: User = Depends(require_god)):
    """Append-only override history. With org_id: every event that can affect
    that organization (platform, its brand, the org, and rows scoped inside it)."""
    q = db.query(FeatureOverrideEvent)
    if feature_key:
        q = q.filter(FeatureOverrideEvent.feature_key == feature_key)
    if org_id:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if org is None:
            raise HTTPException(404, "Organization not found")
        clauses = [FeatureOverrideEvent.scope == "platform",
                   (FeatureOverrideEvent.scope == "org") & (FeatureOverrideEvent.scope_id == org.id),
                   FeatureOverrideEvent.org_id == org.id]
        if org.platform_id:
            clauses.append((FeatureOverrideEvent.scope == "brand")
                           & (FeatureOverrideEvent.scope_id == org.platform_id))
        q = q.filter(or_(*clauses))
    elif platform_id:
        q = q.filter(or_(FeatureOverrideEvent.scope == "platform",
                         (FeatureOverrideEvent.scope == "brand")
                         & (FeatureOverrideEvent.scope_id == platform_id)))
    if scope:
        q = q.filter(FeatureOverrideEvent.scope == _norm_scope(scope))
    rows = q.order_by(FeatureOverrideEvent.at.desc(), FeatureOverrideEvent.id.desc()).limit(limit).all()
    names = _actor_names(db, [r.actor_user_id for r in rows])
    return {"events": [{
        "id": r.id, "scope": r.scope, "scope_id": r.scope_id, "org_id": r.org_id or None,
        "feature_key": r.feature_key, "action": r.action,
        "previous_state": r.previous_state or "inherited",
        "new_state": r.new_state or "inherited", "reason": r.reason,
        "actor_user_id": r.actor_user_id, "actor": names.get(r.actor_user_id),
        "at": r.at.isoformat() if r.at else None} for r in rows]}
