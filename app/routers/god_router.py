"""
AdvisorFlow Command Center — god_admin only.

All routes here are gated by require_god (deps.py). Returns 403 with no
information to anyone whose role != 'god_admin', including super_admins.
The existence of this router and the AdvisorFlow layer is invisible to
every role below god_admin.

Endpoints:
  GET   /god/stats                       — top-level KPIs across all platforms
  GET   /god/platforms                   — all platforms with org/lead counts
  POST  /god/platforms                   — create a new platform
  GET   /god/orgs                        — all orgs across all platforms
  POST  /god/orgs                        — create a new org
  GET   /god/leads                       — all leads across all platforms
  GET   /god/users                       — all super_admins + god_admins
  POST  /god/users                       — create a new admin user
  PATCH /god/users/{user_id}/role        — promote/demote a user's role
  POST  /god/users/{user_id}/deactivate  — deactivate any account
  POST  /god/users/{user_id}/activate    — reactivate any account
  POST  /god/orgs/{org_id}/impersonate   — return a short-lived org context token
"""

import logging
import re
import secrets
import string
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import User, Organization, Lead, Platform
from app.services.auth_service import hash_password

log = logging.getLogger(__name__)

router = APIRouter(prefix="/god", tags=["AdvisorFlow Command Center"])


# ── Schemas ────────────────────────────────────────────────────────────────

class RolePatch(BaseModel):
    role: str  # "god_admin" | "super_admin" | "org_admin" | "advisor" | "viewer"


class PlatformCreate(BaseModel):
    name: str
    slug: str
    domain: Optional[str] = None
    support_email: Optional[str] = None


class OrgCreate(BaseModel):
    name: str
    platform_slug: str
    plan: Optional[str] = "trial"


class UserCreate(BaseModel):
    email: str
    full_name: str
    role: str = "super_admin"
    platform_slug: Optional[str] = None   # used to find an org to attach the user to
    org_id: Optional[str] = None          # explicit org; takes precedence over platform_slug


# ── Helpers ────────────────────────────────────────────────────────────────

ALLOWED_ROLES = {"god_admin", "super_admin", "org_admin", "advisor", "viewer"}


def _slugify(text: str) -> str:
    """Turn a display name into a URL-safe slug."""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _temp_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _safe_count(db: Session, model, filters=None):
    q = db.query(func.count(model.id))
    if filters:
        q = q.filter(*filters)
    return q.scalar() or 0


# ── Routes ─────────────────────────────────────────────────────────────────

@router.post("/platforms", status_code=201)
def god_create_platform(
    body: PlatformCreate,
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """Create a new platform (brand)."""
    slug = body.slug.strip().lower() or _slugify(body.name)
    existing = db.query(Platform).filter(Platform.slug == slug).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Platform slug '{slug}' already exists.")

    platform = Platform(
        name=body.name.strip(),
        slug=slug,
        domain=body.domain,
        support_email=body.support_email,
        is_active=True,
    )
    db.add(platform)
    db.commit()
    db.refresh(platform)
    log.info("AUDIT: god_admin %s created platform %s (%s)", god.email, platform.name, platform.id)
    return {
        "id":            platform.id,
        "name":          platform.name,
        "slug":          platform.slug,
        "domain":        platform.domain,
        "support_email": platform.support_email,
        "is_active":     platform.is_active,
        "org_count":     0,
        "lead_count":    0,
    }


@router.post("/orgs", status_code=201)
def god_create_org(
    body: OrgCreate,
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """Create a new organization under a platform."""
    platform = db.query(Platform).filter(Platform.slug == body.platform_slug).first()
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform '{body.platform_slug}' not found.")

    slug = _slugify(body.name)
    # Ensure slug uniqueness
    base_slug, suffix = slug, 1
    while db.query(Organization).filter(Organization.slug == slug).first():
        slug = f"{base_slug}-{suffix}"
        suffix += 1

    org = Organization(
        name=body.name.strip(),
        slug=slug,
        plan=body.plan or "trial",
        platform_id=platform.id,
        is_active=True,
    )
    db.add(org)
    db.commit()
    db.refresh(org)
    log.info("AUDIT: god_admin %s created org %s (%s) on %s", god.email, org.name, org.id, body.platform_slug)
    return {
        "id":          org.id,
        "name":        org.name,
        "slug":        org.slug,
        "plan":        org.plan,
        "platform_id": org.platform_id,
        "lead_count":  0,
        "user_count":  0,
        "created_at":  org.created_at.isoformat() if org.created_at else None,
    }


@router.post("/users", status_code=201)
def god_create_user(
    body: UserCreate,
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """
    Create a new admin/super_admin user.
    A temporary password is generated and returned once — the user must change it on first login.
    """
    if body.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Allowed: {sorted(ALLOWED_ROLES)}")

    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"User with email '{body.email}' already exists.")

    # Resolve org_id
    org_id = body.org_id
    if not org_id and body.platform_slug:
        # Find the first org on that platform as a home org for this admin
        row = db.execute(
            text("SELECT o.id FROM organizations o JOIN platforms p ON o.platform_id=p.id WHERE p.slug=:slug LIMIT 1"),
            {"slug": body.platform_slug}
        ).fetchone()
        if row:
            org_id = row[0]

    if not org_id:
        # Fall back to the first org in the system
        first_org = db.query(Organization).order_by(Organization.created_at).first()
        if not first_org:
            raise HTTPException(status_code=400, detail="No organizations exist yet. Create an org first.")
        org_id = first_org.id

    temp_pw = _temp_password()
    user = User(
        organization_id=org_id,
        email=body.email.strip().lower(),
        full_name=body.full_name.strip(),
        password_hash=hash_password(temp_pw),
        role=body.role,
        must_change_password=True,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log.info("AUDIT: god_admin %s created user %s (%s) role=%s", god.email, user.email, user.id, user.role)
    return {
        "id":                  user.id,
        "email":               user.email,
        "name":                user.full_name,
        "role":                user.role,
        "is_active":           user.is_active,
        "must_change_password": True,
        "temp_password":       temp_pw,   # shown once in the UI — not stored
        "organization_id":     user.organization_id,
    }


@router.get("/stats")
def god_stats(
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """
    Top-level KPIs for the AdvisorFlow Command Center dashboard.
    Returns totals across every platform, org, and lead in the system.
    """
    total_leads   = _safe_count(db, Lead)
    total_orgs    = _safe_count(db, Organization)
    total_users   = _safe_count(db, User)
    total_admins  = _safe_count(db, User, [User.role.in_(["org_admin", "super_admin", "god_admin"])])

    # Leads created in last 30 days
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=30)
    new_leads_30d = _safe_count(db, Lead, [Lead.created_at >= cutoff])

    # Platform breakdown via raw SQL (platforms table may not have a SQLAlchemy model yet)
    try:
        platform_rows = db.execute(text("""
            SELECT p.name, p.slug,
                   COUNT(DISTINCT o.id) AS org_count
            FROM platforms p
            LEFT JOIN organizations o ON o.platform_id = p.id
            GROUP BY p.id, p.name, p.slug
            ORDER BY p.name
        """)).fetchall()
        platforms = [
            {"name": r[0], "slug": r[1], "org_count": r[2]}
            for r in platform_rows
        ]
    except Exception:
        platforms = []

    # Active orgs (orgs that have at least 1 lead)
    try:
        active_org_count = db.execute(text(
            "SELECT COUNT(DISTINCT organization_id) FROM leads"
        )).scalar() or 0
    except Exception:
        active_org_count = 0

    return {
        "total_platforms":    len(platforms) or 3,
        "total_orgs":         total_orgs,
        "active_orgs":        active_org_count,
        "total_leads":        total_leads,
        "new_leads_30d":      new_leads_30d,
        "total_users":        total_users,
        "total_admins":       total_admins,
        "platforms":          platforms,
        "as_of":              datetime.utcnow().isoformat(),
    }


@router.get("/platforms")
def god_platforms(
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """All platforms with org and lead counts."""
    try:
        rows = db.execute(text("""
            SELECT
                p.id,
                p.name,
                p.slug,
                p.domain,
                p.support_email,
                p.is_active,
                COUNT(DISTINCT o.id)    AS org_count,
                COUNT(DISTINCT l.id)    AS lead_count,
                COUNT(DISTINCT u.id)    AS user_count
            FROM platforms p
            LEFT JOIN organizations o ON o.platform_id = p.id
            LEFT JOIN leads l         ON l.organization_id = o.id
            LEFT JOIN users u         ON u.organization_id = o.id
            GROUP BY p.id, p.name, p.slug, p.domain, p.support_email, p.is_active
            ORDER BY p.name
        """)).fetchall()
        return [
            {
                "id":            r[0],
                "name":          r[1],
                "slug":          r[2],
                "domain":        r[3],
                "support_email": r[4],
                "is_active":     r[5],
                "org_count":     r[6],
                "lead_count":    r[7],
                "user_count":    r[8],
            }
            for r in rows
        ]
    except Exception as e:
        log.warning("god_platforms query failed: %s", e)
        return []


@router.get("/orgs")
def god_orgs(
    platform_slug: Optional[str] = Query(None, description="Filter by platform slug"),
    search:        Optional[str] = Query(None, description="Search by org name"),
    skip:          int = Query(0, ge=0),
    limit:         int = Query(50, ge=1, le=200),
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """
    All organizations across all platforms.
    Filterable by platform and searchable by name.
    """
    q = db.query(Organization)

    if platform_slug:
        # Join through platforms table
        q = q.filter(
            Organization.platform_id.in_(
                db.execute(
                    text("SELECT id FROM platforms WHERE slug = :slug"),
                    {"slug": platform_slug}
                ).scalars().all()
            )
        )

    if search:
        q = q.filter(Organization.name.ilike(f"%{search}%"))

    total = q.count()
    orgs  = q.order_by(Organization.name).offset(skip).limit(limit).all()

    result = []
    for org in orgs:
        lead_count = _safe_count(db, Lead, [Lead.organization_id == org.id])
        user_count = _safe_count(db, User, [User.organization_id == org.id])
        result.append({
            "id":          org.id,
            "name":        org.name,
            "platform_id": getattr(org, "platform_id", None),
            "lead_count":  lead_count,
            "user_count":  user_count,
            "created_at":  org.created_at.isoformat() if getattr(org, "created_at", None) else None,
        })

    return {"total": total, "orgs": result}


@router.get("/leads")
def god_leads(
    platform_slug:   Optional[str] = Query(None),
    org_id:          Optional[str] = Query(None),
    search:          Optional[str] = Query(None, description="Search by name, email, or phone"),
    status:          Optional[str] = Query(None),
    skip:            int = Query(0, ge=0),
    limit:           int = Query(50, ge=1, le=200),
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """
    All leads across the entire system. Filterable by platform, org, status, search.
    god_admin sees everything — zero org scoping applied.
    """
    q = db.query(Lead)

    if org_id:
        q = q.filter(Lead.organization_id == org_id)
    elif platform_slug:
        try:
            org_ids = db.execute(
                text("""
                    SELECT o.id FROM organizations o
                    JOIN platforms p ON o.platform_id = p.id
                    WHERE p.slug = :slug
                """),
                {"slug": platform_slug}
            ).scalars().all()
            q = q.filter(Lead.organization_id.in_(org_ids))
        except Exception:
            pass

    if search:
        q = q.filter(
            Lead.first_name.ilike(f"%{search}%") |
            Lead.last_name.ilike(f"%{search}%") |
            Lead.email.ilike(f"%{search}%") |
            Lead.phone.ilike(f"%{search}%")
        )

    if status:
        q = q.filter(Lead.status == status)

    total = q.count()
    leads = q.order_by(Lead.created_at.desc()).offset(skip).limit(limit).all()

    def _lead_dict(l):
        return {
            "id":              l.id,
            "name":            f"{l.first_name or ''} {l.last_name or ''}".strip() or None,
            "email":           getattr(l, "email", None),
            "phone":           getattr(l, "phone", None),
            "status":          getattr(l, "status", None),
            "tier":            getattr(l, "tier", None),
            "source":          getattr(l, "source_file", None),
            "organization_id": l.organization_id,
            "created_at":      l.created_at.isoformat() if getattr(l, "created_at", None) else None,
        }

    return {"total": total, "leads": [_lead_dict(l) for l in leads]}


@router.get("/users")
def god_users(
    role:   Optional[str] = Query(None, description="Filter by role"),
    search: Optional[str] = Query(None, description="Search by name or email"),
    skip:   int = Query(0, ge=0),
    limit:  int = Query(50, ge=1, le=200),
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """
    All users across the system (defaults to admins only).
    god_admin can see and manage every account.
    """
    q = db.query(User)

    if role:
        q = q.filter(User.role == role)
    else:
        # Default: only show privileged accounts — not every advisor
        q = q.filter(User.role.in_(["god_admin", "super_admin", "org_admin"]))

    if search:
        q = q.filter(
            User.email.ilike(f"%{search}%") |
            User.full_name.ilike(f"%{search}%")
        )

    total = q.count()
    users = q.order_by(User.email).offset(skip).limit(limit).all()

    def _user_dict(u):
        return {
            "id":              u.id,
            "email":           u.email,
            "name":            getattr(u, "full_name", None),
            "role":            u.role,
            "is_active":       getattr(u, "is_active", True),
            "organization_id": getattr(u, "organization_id", None),
            "platform_id":     getattr(u, "platform_id", None),
            "created_at":      u.created_at.isoformat() if getattr(u, "created_at", None) else None,
        }

    return {"total": total, "users": [_user_dict(u) for u in users]}


@router.patch("/users/{user_id}/role")
def god_set_role(
    user_id: str,
    body:    RolePatch,
    god:     User = Depends(require_god),
    db:      Session = Depends(get_db),
):
    """
    Promote or demote any user's role.
    god_admin cannot demote themselves (safety guard).
    """
    if body.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Allowed: {sorted(ALLOWED_ROLES)}")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent god from accidentally locking themselves out
    if target.id == god.id and body.role != "god_admin":
        raise HTTPException(
            status_code=400,
            detail="Cannot demote your own god_admin account. Use another god_admin account."
        )

    old_role  = target.role
    target.role = body.role
    db.commit()

    log.info(
        "AUDIT: god_admin %s changed user %s (%s) role: %s → %s",
        god.email, target.email, user_id, old_role, body.role
    )

    return {"user_id": user_id, "email": target.email, "old_role": old_role, "new_role": body.role}


@router.post("/users/{user_id}/deactivate")
def god_deactivate_user(
    user_id: str,
    god:     User = Depends(require_god),
    db:      Session = Depends(get_db),
):
    """Deactivate any account (blocks login). Irreversible without /activate."""
    if user_id == god.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account.")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    target.is_active = False
    db.commit()
    log.info("AUDIT: god_admin %s deactivated user %s (%s)", god.email, target.email, user_id)
    return {"user_id": user_id, "email": target.email, "is_active": False}


@router.post("/users/{user_id}/activate")
def god_activate_user(
    user_id: str,
    god:     User = Depends(require_god),
    db:      Session = Depends(get_db),
):
    """Re-activate a previously deactivated account."""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    target.is_active = True
    db.commit()
    log.info("AUDIT: god_admin %s activated user %s (%s)", god.email, target.email, user_id)
    return {"user_id": user_id, "email": target.email, "is_active": True}


@router.post("/orgs/{org_id}/impersonate")
def god_impersonate_org(
    org_id: str,
    god:    User = Depends(require_god),
    db:     Session = Depends(get_db),
):
    """
    Returns the org_id to use as X-Org-Override header value.
    The god_admin can pass this header on any request to view data
    exactly as that org's super_admin would see it.
    """
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    log.info(
        "AUDIT: god_admin %s initiated impersonation of org %s (%s)",
        god.email, org.name, org_id
    )

    return {
        "org_id":       org_id,
        "org_name":     org.name,
        "header_name":  "X-Org-Override",
        "header_value": org_id,
        "instruction":  "Pass X-Org-Override: <org_id> on subsequent requests to view as that org.",
    }


# ── Role Permission Config ─────────────────────────────────────────────────────
# God admin can view and override what each role is allowed to do.
# Stored in the system_config table as a JSON blob under key "role_permissions".
# The backend enforces the most critical overrides (cross-org access is handled
# directly in deps.py). This API exposes the full matrix for the Command Center UI.

import json as _json
from sqlalchemy import text as _text

_DEFAULT_ROLE_PERMISSIONS = {
    "advisor": {
        "own_leads":            True,
        "import_leads":         True,
        "own_settings":         True,
        "change_own_password":  True,
    },
    "org_admin": {
        "own_leads":            True,
        "import_leads":         True,
        "own_settings":         True,
        "change_own_password":  True,
        "view_all_org_leads":   True,
        "create_users":         True,
        "deactivate_users":     True,
        "force_logout_users":   True,
        "view_audit_log":       True,
        "edit_org_settings":    True,
        "assign_twilio":        True,
        "change_user_roles":    True,
        "cross_org_access":     False,
    },
    "super_admin": {
        "own_leads":            True,
        "import_leads":         True,
        "own_settings":         True,
        "change_own_password":  True,
        "view_all_org_leads":   True,
        "create_users":         True,
        "deactivate_users":     True,
        "force_logout_users":   True,
        "view_audit_log":       True,
        "edit_org_settings":    True,
        "assign_twilio":        True,
        "change_user_roles":    True,
        "cross_org_access":     False,  # super_admin is org-scoped — only god_admin sees all
    },
    "god_admin": {
        "own_leads":            True,
        "import_leads":         True,
        "own_settings":         True,
        "change_own_password":  True,
        "view_all_org_leads":   True,
        "create_users":         True,
        "deactivate_users":     True,
        "force_logout_users":   True,
        "view_audit_log":       True,
        "edit_org_settings":    True,
        "assign_twilio":        True,
        "change_user_roles":    True,
        "cross_org_access":     True,
        "manage_platforms":     True,
        "create_orgs":          True,
        "promote_to_god":       True,
        "command_center":       True,
    },
}

_CONFIG_KEY = "role_permissions"


def _load_role_permissions(db: Session) -> dict:
    row = db.execute(_text("SELECT value FROM system_config WHERE key = :k"), {"k": _CONFIG_KEY}).fetchone()
    if row:
        try:
            stored = _json.loads(row[0])
            # Merge stored overrides on top of defaults so new permissions
            # added in code are always present even without a re-save.
            merged = _json.loads(_json.dumps(_DEFAULT_ROLE_PERMISSIONS))
            for role, perms in stored.items():
                if role in merged:
                    merged[role].update(perms)
            return merged
        except Exception:
            pass
    return _json.loads(_json.dumps(_DEFAULT_ROLE_PERMISSIONS))


def _save_role_permissions(db: Session, config: dict) -> None:
    db.execute(_text("""
        INSERT INTO system_config (key, value)
        VALUES (:k, :v)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
    """), {"k": _CONFIG_KEY, "v": _json.dumps(config)})
    db.commit()


class RolePermissionPatch(BaseModel):
    role: str
    permission: str
    enabled: bool


@router.get("/role-config")
def get_role_config(
    god: User = Depends(require_god),
    db:  Session = Depends(get_db),
):
    """Return the full role permission matrix."""
    return _load_role_permissions(db)


@router.patch("/role-config")
def patch_role_config(
    body: RolePermissionPatch,
    god:  User = Depends(require_god),
    db:   Session = Depends(get_db),
):
    """Toggle a single permission for a role. god_admin-only."""
    PROTECTED = {"god_admin"}
    LOCKED_GOD_PERMS = {"command_center", "cross_org_access", "manage_platforms",
                        "create_orgs", "promote_to_god"}

    if body.role == "god_admin":
        raise HTTPException(status_code=400, detail="god_admin permissions cannot be modified.")
    if body.role not in _DEFAULT_ROLE_PERMISSIONS:
        raise HTTPException(status_code=400, detail=f"Unknown role: {body.role}")
    if body.permission not in _DEFAULT_ROLE_PERMISSIONS.get(body.role, {}):
        raise HTTPException(status_code=400, detail=f"Unknown permission: {body.permission}")

    config = _load_role_permissions(db)
    config[body.role][body.permission] = body.enabled
    _save_role_permissions(db, config)

    log.info(
        "AUDIT: god_admin %s set %s.%s = %s",
        god.email, body.role, body.permission, body.enabled
    )
    return {"role": body.role, "permission": body.permission, "enabled": body.enabled}
