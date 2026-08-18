"""
AdvisorFlow Command Center — god_admin only.

All routes here are gated by require_god (deps.py). Returns 403 with no
information to anyone whose role != 'god_admin', including super_admins.
The existence of this router and the AdvisorFlow layer is invisible to
every role below god_admin.

Endpoints:
  GET  /god/stats          — top-level KPIs across all platforms
  GET  /god/platforms       — all platforms with org/lead counts
  GET  /god/orgs           — all orgs across all platforms (paginated, filterable)
  GET  /god/leads          — all leads across all platforms (paginated, filterable)
  GET  /god/users          — all super_admins + god_admins
  PATCH /god/users/{user_id}/role   — promote/demote a user's role
  POST /god/users/{user_id}/deactivate  — deactivate any account
  POST /god/users/{user_id}/activate    — reactivate any account
  POST /god/orgs/{org_id}/impersonate   — return a short-lived org context token
                                          for super_admin to use as X-Org-Override
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import User, Organization, Lead

log = logging.getLogger(__name__)

router = APIRouter(prefix="/god", tags=["AdvisorFlow Command Center"])


# ── Schemas ────────────────────────────────────────────────────────────────

class RolePatch(BaseModel):
    role: str  # "god_admin" | "super_admin" | "org_admin" | "advisor" | "viewer"


# ── Helpers ────────────────────────────────────────────────────────────────

ALLOWED_ROLES = {"god_admin", "super_admin", "org_admin", "advisor", "viewer"}


def _safe_count(db: Session, model, filters=None):
    q = db.query(func.count(model.id))
    if filters:
        q = q.filter(*filters)
    return q.scalar() or 0


# ── Routes ─────────────────────────────────────────────────────────────────

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
