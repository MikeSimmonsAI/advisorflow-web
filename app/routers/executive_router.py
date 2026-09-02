"""
Executive Suite API — brand-scoped, read-only executive visibility.

WHO CAN USE THIS
----------------
Users who hold Membership(scope_type="platform", scope_id=<platform_id>,
role="brand_executive", is_active=True).

WHAT THEY CAN SEE
-----------------
- KPI summary for their brand (won customers, active orgs, pipeline value)
- Internal brand team (sales managers + reps across their BrandSalesOrgs)
- Portfolio of customer organizations provisioned from their brand

WHAT THEY CANNOT DO
-------------------
- Enter any customer workspace (require_tenant_user blocks them)
- Access any god/owner control plane (require_god blocks them)
- See any other brand's data (platform_id filter enforced on every query)
- Grant themselves or anyone else elevated access (only god_admin may grant)

PUBLIC-FACING COPY RULE
-----------------------
This router and all pages it powers are for internal business use.
No response body or frontend label may contain "god", "god_admin",
"God Mode", "God Admin", "God Operations", or similar internal
platform-owner terminology.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, case as sa_case

from app.deps import get_db, get_current_user, require_god, require_brand_executive
from app.models.models import User, Organization, Platform
from app.models.sales_models import (
    Membership, BrandSalesOrg, Opportunity,
    SCOPE_PLATFORM, SCOPE_BRAND_SALES_ORG,
    ROLE_BRAND_EXECUTIVE, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    BRAND_SALES_ROLES,
)

router = APIRouter(prefix="/executive", tags=["executive"])


# ── Context ────────────────────────────────────────────────────────────────────

@router.get("/context")
def get_executive_context(
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """Return the caller's brand context. 401/403 if they hold no executive grant."""
    user, mem, platform = executive
    return {
        "user_id": user.id,
        "email": user.email,
        "name": (user.first_name or "") + " " + (user.last_name or ""),
        "platform_id": platform.id,
        "platform_name": platform.name,
        "platform_slug": platform.slug,
        "role": mem.role,
        "granted_since": mem.created_at.isoformat() if mem.created_at else None,
    }

# ── Command Center ─────────────────────────────────────────────────────────────

@router.get("/command-center")
def get_command_center(
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """Brand-scoped KPIs for the Executive Command Center."""
    user, mem, platform = executive
    platform_id = platform.id

    brand_org_ids = [
        row.id for row in
        db.query(BrandSalesOrg.id)
        .filter(BrandSalesOrg.platform_id == platform_id)
        .all()
    ]

    opp_stats = {"total": 0, "won": 0, "pipeline_value": 0.0, "won_value": 0.0}
    if brand_org_ids:
        rows = (
            db.query(
                func.count(Opportunity.id).label("total"),
                func.sum(sa_case((Opportunity.stage == "won", 1), else_=0)).label("won"),
                func.coalesce(func.sum(Opportunity.value), 0).label("pipeline_value"),
                func.coalesce(
                    func.sum(sa_case((Opportunity.stage == "won", Opportunity.value), else_=0)),
                    0
                ).label("won_value"),
            )
            .filter(Opportunity.brand_sales_org_id.in_(brand_org_ids))
            .first()
        )
        if rows:
            opp_stats = {
                "total": rows.total or 0,
                "won": rows.won or 0,
                "pipeline_value": float(rows.pipeline_value or 0),
                "won_value": float(rows.won_value or 0),
            }

    active_orgs = (
        db.query(func.count(Organization.id))
        .filter(Organization.platform_id == platform_id)
        .scalar() or 0
    )

    team_count = (
        db.query(func.count(Membership.id))
        .filter(
            Membership.scope_type == SCOPE_BRAND_SALES_ORG,
            Membership.scope_id.in_(brand_org_ids),
            Membership.role.in_(BRAND_SALES_ROLES),
            Membership.is_active.is_(True),
        )
        .scalar() or 0
    ) if brand_org_ids else 0

    return {
        "platform_id": platform_id,
        "platform_name": platform.name,
        "opportunities": opp_stats,
        "active_customer_orgs": active_orgs,
        "team_headcount": team_count,
        "brand_sales_org_count": len(brand_org_ids),
    }

# ── Team ───────────────────────────────────────────────────────────────────────

@router.get("/team")
def get_executive_team(
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """Internal brand team: sales managers and reps across all BrandSalesOrgs."""
    user, mem, platform = executive
    platform_id = platform.id

    brand_org_ids = [
        row.id for row in
        db.query(BrandSalesOrg.id)
        .filter(BrandSalesOrg.platform_id == platform_id)
        .all()
    ]

    members = []
    if brand_org_ids:
        rows = (
            db.query(Membership, User, BrandSalesOrg)
            .join(User, User.id == Membership.user_id)
            .join(BrandSalesOrg, BrandSalesOrg.id == Membership.scope_id)
            .filter(
                Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                Membership.scope_id.in_(brand_org_ids),
                Membership.role.in_(BRAND_SALES_ROLES),
                Membership.is_active.is_(True),
            )
            .order_by(BrandSalesOrg.name, Membership.role, User.email)
            .all()
        )
        for m, u, bso in rows:
            members.append({
                "user_id": u.id,
                "email": u.email,
                "name": ((u.first_name or "") + " " + (u.last_name or "")).strip() or u.email,
                "role": m.role,
                "brand_sales_org_id": bso.id,
                "brand_sales_org_name": bso.name,
                "joined": m.created_at.isoformat() if m.created_at else None,
            })

    return {"platform_id": platform_id, "team": members}


# ── Organizations portfolio ────────────────────────────────────────────────────

@router.get("/organizations")
def get_executive_organizations(
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """Customer organizations provisioned from this executive's brand platform."""
    user, mem, platform = executive
    platform_id = platform.id

    orgs = (
        db.query(Organization)
        .filter(Organization.platform_id == platform_id)
        .order_by(Organization.name)
        .all()
    )

    return {
        "platform_id": platform_id,
        "platform_name": platform.name,
        "total": len(orgs),
        "organizations": [
            {
                "id": org.id,
                "name": org.name,
                "created_at": org.created_at.isoformat()
                    if hasattr(org, "created_at") and org.created_at else None,
            }
            for org in orgs
        ],
    }

# ── Admin: grant executive membership (god_admin only) ────────────────────────

@router.post("/admin/grant", status_code=201)
def grant_executive_membership(
    payload: dict,
    user: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """Create a brand_executive membership. Callable by god_admin only.

    Body: { "user_id": str, "platform_id": str }
    Idempotent: calling again while an active grant exists returns 200 with existing record.
    """
    target_user_id = payload.get("user_id", "").strip()
    platform_id = payload.get("platform_id", "").strip()

    if not target_user_id or not platform_id:
        raise HTTPException(status_code=400, detail="user_id and platform_id are required.")

    target_user = db.query(User).filter(User.id == target_user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found.")

    platform = db.query(Platform).filter(Platform.id == platform_id).first()
    if not platform:
        raise HTTPException(status_code=404, detail="Platform not found.")

    existing = (
        db.query(Membership)
        .filter(
            Membership.user_id == target_user_id,
            Membership.scope_type == SCOPE_PLATFORM,
            Membership.scope_id == platform_id,
            Membership.role == ROLE_BRAND_EXECUTIVE,
        )
        .first()
    )
    if existing:
        if existing.is_active:
            return {"status": "already_active", "membership_id": existing.id}
        existing.is_active = True
        existing.granted_by = user.id
        db.commit()
        db.refresh(existing)
        return {"status": "reactivated", "membership_id": existing.id}

    mem = Membership(
        user_id=target_user_id,
        scope_type=SCOPE_PLATFORM,
        scope_id=platform_id,
        role=ROLE_BRAND_EXECUTIVE,
        is_active=True,
        granted_by=user.id,
    )
    db.add(mem)
    db.commit()
    db.refresh(mem)
    return {
        "status": "granted",
        "membership_id": mem.id,
        "platform_id": platform_id,
        "platform_name": platform.name,
    }

# ── Admin: revoke executive membership (god_admin only) ────────────────────────

@router.post("/admin/revoke")
def revoke_executive_membership(
    payload: dict,
    user: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    """Deactivate a brand_executive membership. Callable by god_admin only.

    Body: { "user_id": str, "platform_id": str }
    """
    target_user_id = payload.get("user_id", "").strip()
    platform_id = payload.get("platform_id", "").strip()

    if not target_user_id or not platform_id:
        raise HTTPException(status_code=400, detail="user_id and platform_id are required.")

    mem = (
        db.query(Membership)
        .filter(
            Membership.user_id == target_user_id,
            Membership.scope_type == SCOPE_PLATFORM,
            Membership.scope_id == platform_id,
            Membership.role == ROLE_BRAND_EXECUTIVE,
            Membership.is_active.is_(True),
        )
        .first()
    )
    if not mem:
        raise HTTPException(status_code=404, detail="Active executive membership not found.")

    mem.is_active = False
    db.commit()
    return {"status": "revoked", "membership_id": mem.id}
