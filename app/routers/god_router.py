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
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import User, Organization, Lead, Platform, Message
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


class OrgStatusUpdate(BaseModel):
    reason: Optional[str] = None


class UserCreate(BaseModel):
    email: str
    full_name: str
    role: str = "super_admin"
    platform_slug: Optional[str] = None
    org_id: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────────────

ALLOWED_ROLES = {"god_admin", "super_admin", "org_admin", "advisor", "viewer"}


def _slugify(text: str) -> str:
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


def _compute_health_score(
    is_active: bool,
    lead_count: int,
    advisor_count: int,
    messages_30d: int,
    days_since_activity: Optional[float],
) -> int:
    """80-100 = Healthy, 60-79 = Attention, <60 = Critical"""
    if not is_active:
        return 0
    score = 100
    if lead_count == 0:
        score -= 30
    elif lead_count < 5:
        score -= 10
    if advisor_count == 0:
        score -= 20
    if messages_30d == 0:
        score -= 25
    elif messages_30d < 5:
        score -= 10
    if days_since_activity is None:
        score -= 20
    elif days_since_activity > 60:
        score -= 20
    elif days_since_activity > 30:
        score -= 10
    return max(0, min(100, score))


def _enrich_org(db: Session, org: Organization) -> dict:
    """Build the full God Mode intelligence record for one org."""
    cutoff_30 = datetime.utcnow() - timedelta(days=30)
    lead_count    = _safe_count(db, Lead, [Lead.organization_id == org.id])
    advisor_count = _safe_count(db, User, [User.organization_id == org.id, User.role == "advisor"])
    user_count    = _safe_count(db, User, [User.organization_id == org.id])
    try:
        messages_30d = db.execute(text("""
            SELECT COUNT(m.id) FROM messages m
            JOIN leads l ON m.lead_id = l.id
            WHERE l.organization_id = :org_id AND m.sent_at >= :cutoff
        """), {"org_id": org.id, "cutoff": cutoff_30}).scalar() or 0
    except Exception:
        messages_30d = 0
    try:
        last_msg_at = db.execute(text("""
            SELECT MAX(m.sent_at) FROM messages m
            JOIN leads l ON m.lead_id = l.id
            WHERE l.organization_id = :org_id
        """), {"org_id": org.id}).scalar()
    except Exception:
        last_msg_at = None
    try:
        last_login_at = db.execute(text("""
            SELECT MAX(last_login_at) FROM users WHERE organization_id = :org_id
        """), {"org_id": org.id}).scalar()
    except Exception:
        last_login_at = None
    candidates = [t for t in [last_msg_at, last_login_at] if t is not None]
    last_activity = max(candidates) if candidates else None
    days_since = None
    if last_activity:
        la = last_activity.replace(tzinfo=None) if hasattr(last_activity, 'replace') and last_activity.tzinfo else last_activity
        days_since = (datetime.utcnow() - la).total_seconds() / 86400
    health_score = _compute_health_score(
        is_active=org.is_active, lead_count=lead_count, advisor_count=advisor_count,
        messages_30d=int(messages_30d), days_since_activity=days_since,
    )
    return {
        "id": org.id, "name": org.name, "slug": getattr(org, "slug", None),
        "plan": getattr(org, "plan", "trial"), "is_active": org.is_active,
        "status": "active" if org.is_active else "dormant",
        "platform_id": getattr(org, "platform_id", None),
        "lead_count": lead_count, "user_count": user_count, "advisor_count": advisor_count,
        "messages_30d": int(messages_30d),
        "last_activity": last_activity.isoformat() if last_activity else None,
        "health_score": health_score,
        "created_at": org.created_at.isoformat() if getattr(org, "created_at", None) else None,
        "brand_name": getattr(org, "brand_name", None),
        "org_phone": getattr(org, "org_phone", None),
        "org_address": getattr(org, "org_address", None),
        "industry": getattr(org, "industry", None),
    }


# ── Routes ─────────────────────────────────────────────────────────────────

@router.post("/platforms", status_code=201)
def god_create_platform(body: PlatformCreate, god: User = Depends(require_god), db: Session = Depends(get_db)):
    slug = body.slug.strip().lower() or _slugify(body.name)
    if db.query(Platform).filter(Platform.slug == slug).first():
        raise HTTPException(status_code=409, detail=f"Platform slug '{slug}' already exists.")
    platform = Platform(name=body.name.strip(), slug=slug, domain=body.domain,
                        support_email=body.support_email, is_active=True)
    db.add(platform); db.commit(); db.refresh(platform)
    log.info("AUDIT: god_admin %s created platform %s (%s)", god.email, platform.name, platform.id)
    return {"id": platform.id, "name": platform.name, "slug": platform.slug,
            "domain": platform.domain, "support_email": platform.support_email,
            "is_active": platform.is_active, "org_count": 0, "lead_count": 0}


@router.post("/orgs", status_code=201)
def god_create_org(body: OrgCreate, god: User = Depends(require_god), db: Session = Depends(get_db)):
    platform = db.query(Platform).filter(Platform.slug == body.platform_slug).first()
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform '{body.platform_slug}' not found.")
    slug = _slugify(body.name)
    base_slug, suffix = slug, 1
    while db.query(Organization).filter(Organization.slug == slug).first():
        slug = f"{base_slug}-{suffix}"; suffix += 1
    org = Organization(name=body.name.strip(), slug=slug, plan=body.plan or "trial",
                       platform_id=platform.id, is_active=True)
    db.add(org); db.commit(); db.refresh(org)
    log.info("AUDIT: god_admin %s created org %s (%s) on %s", god.email, org.name, org.id, body.platform_slug)
    return {"id": org.id, "name": org.name, "slug": org.slug, "plan": org.plan,
            "platform_id": org.platform_id, "lead_count": 0, "user_count": 0,
            "created_at": org.created_at.isoformat() if org.created_at else None}


@router.post("/users", status_code=201)
def god_create_user(body: UserCreate, god: User = Depends(require_god), db: Session = Depends(get_db)):
    if body.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Allowed: {sorted(ALLOWED_ROLES)}")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail=f"User '{body.email}' already exists.")
    org_id = body.org_id
    if not org_id and body.platform_slug:
        row = db.execute(text("SELECT o.id FROM organizations o JOIN platforms p ON o.platform_id=p.id WHERE p.slug=:slug LIMIT 1"),
                         {"slug": body.platform_slug}).fetchone()
        if row: org_id = row[0]
    if not org_id:
        first_org = db.query(Organization).order_by(Organization.created_at).first()
        if not first_org:
            raise HTTPException(status_code=400, detail="No organizations exist yet.")
        org_id = first_org.id
    temp_pw = _temp_password()
    user = User(organization_id=org_id, email=body.email.strip().lower(),
                full_name=body.full_name.strip(), password_hash=hash_password(temp_pw),
                role=body.role, must_change_password=True, is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    log.info("AUDIT: god_admin %s created user %s (%s) role=%s", god.email, user.email, user.id, user.role)
    return {"id": user.id, "email": user.email, "name": user.full_name, "role": user.role,
            "is_active": user.is_active, "must_change_password": True,
            "temp_password": temp_pw, "organization_id": user.organization_id}


@router.get("/stats")
def god_stats(god: User = Depends(require_god), db: Session = Depends(get_db)):
    total_leads  = _safe_count(db, Lead)
    total_orgs   = _safe_count(db, Organization)
    total_users  = _safe_count(db, User)
    total_admins = _safe_count(db, User, [User.role.in_(["org_admin","super_admin","god_admin"])])
    cutoff = datetime.utcnow() - timedelta(days=30)
    new_leads_30d = _safe_count(db, Lead, [Lead.created_at >= cutoff])
    try:
        platform_rows = db.execute(text("""
            SELECT p.name, p.slug, COUNT(DISTINCT o.id) AS org_count
            FROM platforms p LEFT JOIN organizations o ON o.platform_id = p.id
            GROUP BY p.id, p.name, p.slug ORDER BY p.name
        """)).fetchall()
        platforms = [{"name": r[0], "slug": r[1], "org_count": r[2]} for r in platform_rows]
    except Exception:
        platforms = []
    try:
        active_org_count = db.execute(text("SELECT COUNT(DISTINCT organization_id) FROM leads")).scalar() or 0
    except Exception:
        active_org_count = 0
    return {"total_platforms": len(platforms) or 3, "total_orgs": total_orgs,
            "active_orgs": active_org_count, "total_leads": total_leads,
            "new_leads_30d": new_leads_30d, "total_users": total_users,
            "total_admins": total_admins, "platforms": platforms,
            "as_of": datetime.utcnow().isoformat()}


@router.get("/platforms")
def god_platforms(god: User = Depends(require_god), db: Session = Depends(get_db)):
    try:
        rows = db.execute(text("""
            SELECT p.id, p.name, p.slug, p.domain, p.support_email, p.is_active,
                   COUNT(DISTINCT o.id) AS org_count, COUNT(DISTINCT l.id) AS lead_count,
                   COUNT(DISTINCT u.id) AS user_count
            FROM platforms p
            LEFT JOIN organizations o ON o.platform_id = p.id
            LEFT JOIN leads l ON l.organization_id = o.id
            LEFT JOIN users u ON u.organization_id = o.id
            GROUP BY p.id, p.name, p.slug, p.domain, p.support_email, p.is_active ORDER BY p.name
        """)).fetchall()
        return [{"id": r[0], "name": r[1], "slug": r[2], "domain": r[3],
                 "support_email": r[4], "is_active": r[5],
                 "org_count": r[6], "lead_count": r[7], "user_count": r[8]} for r in rows]
    except Exception as e:
        log.warning("god_platforms query failed: %s", e)
        return []


@router.get("/orgs")
def god_orgs(
    platform_slug: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    health: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    q = db.query(Organization)
    if platform_slug:
        q = q.filter(Organization.platform_id.in_(
            db.execute(text("SELECT id FROM platforms WHERE slug = :slug"), {"slug": platform_slug}).scalars().all()
        ))
    if search:
        q = q.filter(
            Organization.name.ilike(f"%{search}%") |
            Organization.slug.ilike(f"%{search}%") |
            Organization.id.ilike(f"%{search}%")
        )
    if status == "active":
        q = q.filter(Organization.is_active == True)
    elif status == "dormant":
        q = q.filter(Organization.is_active == False)
    total = q.count()
    orgs = q.order_by(Organization.name).offset(skip).limit(limit).all()
    result = [_enrich_org(db, org) for org in orgs]
    if health:
        def _band(s): return "healthy" if s >= 80 else "attention" if s >= 60 else "critical"
        result = [r for r in result if _band(r["health_score"]) == health.lower()]
    return {"total": total, "orgs": result}


@router.get("/orgs/{org_id}/detail")
def god_org_detail(org_id: str, god: User = Depends(require_god), db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    enriched = _enrich_org(db, org)
    advisors = db.query(User).filter(
        User.organization_id == org_id,
        User.role.in_(["advisor","org_admin","super_admin"]),
        User.is_active == True,
    ).order_by(User.full_name).limit(10).all()
    enriched["advisors"] = [{"id": u.id, "full_name": u.full_name, "email": u.email, "role": u.role} for u in advisors]
    try:
        recent = db.execute(text("""
            SELECT DATE(m.sent_at) as day, COUNT(*) as cnt
            FROM messages m JOIN leads l ON m.lead_id = l.id
            WHERE l.organization_id = :org_id AND m.sent_at >= NOW() - INTERVAL '7 days'
            GROUP BY day ORDER BY day
        """), {"org_id": org_id}).fetchall()
        enriched["msg_trend_7d"] = [{"date": str(r[0]), "count": r[1]} for r in recent]
    except Exception:
        enriched["msg_trend_7d"] = []
    if org.platform_id:
        try:
            plat = db.execute(text("SELECT name, slug FROM platforms WHERE id = :pid"), {"pid": org.platform_id}).fetchone()
            enriched["platform_name"] = plat[0] if plat else None
            enriched["platform_slug"] = plat[1] if plat else None
        except Exception:
            enriched["platform_name"] = None; enriched["platform_slug"] = None
    return enriched


@router.post("/orgs/{org_id}/suspend")
def god_suspend_org(org_id: str, body: OrgStatusUpdate, god: User = Depends(require_god), db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org: raise HTTPException(status_code=404, detail="Organization not found")
    org.is_active = False; db.commit()
    log.info("AUDIT: god_admin %s SUSPENDED org %s (%s) reason=%s", god.email, org.name, org_id, body.reason or "none")
    return {"org_id": org_id, "name": org.name, "status": "dormant", "is_active": False}


@router.post("/orgs/{org_id}/reactivate")
def god_reactivate_org(org_id: str, body: OrgStatusUpdate, god: User = Depends(require_god), db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org: raise HTTPException(status_code=404, detail="Organization not found")
    org.is_active = True; db.commit()
    log.info("AUDIT: god_admin %s REACTIVATED org %s (%s) reason=%s", god.email, org.name, org_id, body.reason or "none")
    return {"org_id": org_id, "name": org.name, "status": "active", "is_active": True}


@router.get("/leads")
def god_leads(
    platform_slug: Optional[str] = Query(None), org_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None), status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
    god: User = Depends(require_god), db: Session = Depends(get_db),
):
    q = db.query(Lead)
    if org_id:
        q = q.filter(Lead.organization_id == org_id)
    elif platform_slug:
        try:
            org_ids = db.execute(text("""
                SELECT o.id FROM organizations o JOIN platforms p ON o.platform_id = p.id WHERE p.slug = :slug
            """), {"slug": platform_slug}).scalars().all()
            q = q.filter(Lead.organization_id.in_(org_ids))
        except Exception: pass
    if search:
        q = q.filter(Lead.first_name.ilike(f"%{search}%") | Lead.last_name.ilike(f"%{search}%") |
                     Lead.email.ilike(f"%{search}%") | Lead.phone.ilike(f"%{search}%"))
    if status: q = q.filter(Lead.status == status)
    total = q.count()
    leads = q.order_by(Lead.created_at.desc()).offset(skip).limit(limit).all()
    def _ld(l):
        return {"id": l.id, "name": f"{l.first_name or ''} {l.last_name or ''}".strip() or None,
                "email": getattr(l,"email",None), "phone": getattr(l,"phone",None),
                "status": getattr(l,"status",None), "tier": getattr(l,"tier",None),
                "source": getattr(l,"source_file",None), "organization_id": l.organization_id,
                "created_at": l.created_at.isoformat() if getattr(l,"created_at",None) else None}
    return {"total": total, "leads": [_ld(l) for l in leads]}


@router.get("/users")
def god_users(
    role: Optional[str] = Query(None), search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
    god: User = Depends(require_god), db: Session = Depends(get_db),
):
    q = db.query(User)
    if role: q = q.filter(User.role == role)
    else: q = q.filter(User.role.in_(["god_admin","super_admin","org_admin"]))
    if search: q = q.filter(User.email.ilike(f"%{search}%") | User.full_name.ilike(f"%{search}%"))
    total = q.count()
    users = q.order_by(User.email).offset(skip).limit(limit).all()
    def _ud(u):
        return {"id": u.id, "email": u.email, "name": getattr(u,"full_name",None),
                "role": u.role, "is_active": getattr(u,"is_active",True),
                "organization_id": getattr(u,"organization_id",None),
                "created_at": u.created_at.isoformat() if getattr(u,"created_at",None) else None}
    return {"total": total, "users": [_ud(u) for u in users]}


@router.patch("/users/{user_id}/role")
def god_set_role(user_id: str, body: RolePatch, god: User = Depends(require_god), db: Session = Depends(get_db)):
    if body.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Allowed: {sorted(ALLOWED_ROLES)}")
    target = db.query(User).filter(User.id == user_id).first()
    if not target: raise HTTPException(status_code=404, detail="User not found")
    if target.id == god.id and body.role != "god_admin":
        raise HTTPException(status_code=400, detail="Cannot demote your own god_admin account.")
    old_role = target.role; target.role = body.role; db.commit()
    log.info("AUDIT: god_admin %s changed user %s (%s) role: %s → %s", god.email, target.email, user_id, old_role, body.role)
    return {"user_id": user_id, "email": target.email, "old_role": old_role, "new_role": body.role}


@router.post("/users/{user_id}/deactivate")
def god_deactivate_user(user_id: str, god: User = Depends(require_god), db: Session = Depends(get_db)):
    if user_id == god.id: raise HTTPException(status_code=400, detail="Cannot deactivate your own account.")
    target = db.query(User).filter(User.id == user_id).first()
    if not target: raise HTTPException(status_code=404, detail="User not found")
    target.is_active = False; db.commit()
    log.info("AUDIT: god_admin %s deactivated user %s (%s)", god.email, target.email, user_id)
    return {"user_id": user_id, "email": target.email, "is_active": False}


@router.post("/users/{user_id}/activate")
def god_activate_user(user_id: str, god: User = Depends(require_god), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target: raise HTTPException(status_code=404, detail="User not found")
    target.is_active = True; db.commit()
    log.info("AUDIT: god_admin %s activated user %s (%s)", god.email, target.email, user_id)
    return {"user_id": user_id, "email": target.email, "is_active": True}


@router.post("/orgs/{org_id}/impersonate")
def god_impersonate_org(org_id: str, god: User = Depends(require_god), db: Session = Depends(get_db)):
    """ENTER ORGANIZATION — establishes a god-mode tenant session with full audit trail."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org: raise HTTPException(status_code=404, detail="Organization not found")
    session_id = secrets.token_urlsafe(16)
    entered_at = datetime.utcnow().isoformat()
    log.info("AUDIT: GOD_ENTER_ORG | admin=%s | org_id=%s | org_name=%s | session=%s | entered_at=%s",
             god.email, org_id, org.name, session_id, entered_at)
    return {"org_id": org_id, "org_name": org.name, "org_slug": getattr(org,"slug",None),
            "org_plan": getattr(org,"plan","trial"), "is_active": org.is_active,
            "session_id": session_id, "entered_at": entered_at, "god_email": god.email,
            "header_name": "X-Org-Override", "header_value": org_id}


@router.post("/orgs/{org_id}/exit-session")
def god_exit_org_session(org_id: str, god: User = Depends(require_god), db: Session = Depends(get_db)):
    """Record exit from a God Mode tenant session."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    org_name = org.name if org else org_id
    log.info("AUDIT: GOD_EXIT_ORG | admin=%s | org_id=%s | org_name=%s | exited_at=%s",
             god.email, org_id, org_name, datetime.utcnow().isoformat())
    return {"status": "exited", "org_id": org_id}
