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
        "name": user.full_name or "",
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
                func.coalesce(func.sum(Opportunity.deal_value), 0).label("pipeline_value"),
                func.coalesce(
                    func.sum(sa_case((Opportunity.stage == "won", Opportunity.deal_value), else_=0)),
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
                "name": u.full_name or u.email,
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

# ── Customer Health ────────────────────────────────────────────────────────────

def _classify_health(
    age_days: int,
    active_users: int,
    total_leads: int,
    days_since_op,          # int | None
):
    """
    Classification priority order:
      HEALTHY     → active_users >= 1, total_leads > 0, days_since_op <= 14
      ONBOARDING  → org age <= 30 days AND not HEALTHY
      INACTIVE    → no operational activity ever, or days_since_op > 60
      AT_RISK     → days_since_op 31–60
      WATCH       → everything else (15–30 days since op, zero users, zero leads)
    """
    has_users  = active_users >= 1
    has_leads  = total_leads  > 0
    has_recent = days_since_op is not None and days_since_op <= 14

    if has_users and has_leads and has_recent:
        reason = (
            f"Active: {active_users} user{'s' if active_users != 1 else ''}, "
            f"{total_leads} lead{'s' if total_leads != 1 else ''}, "
            f"operational activity {days_since_op} day{'s' if days_since_op != 1 else ''} ago."
        )
        return "healthy", reason

    if age_days <= 30:
        parts = []
        if not has_users:
            parts.append("no active users yet")
        if not has_leads:
            parts.append("no leads imported yet")
        if not has_recent:
            parts.append("no operational activity yet" if days_since_op is None
                         else f"last operational activity {days_since_op} days ago")
        reason = "New organization (under 30 days old). " + ("; ".join(parts) if parts else "Getting started.")
        return "onboarding", reason

    if days_since_op is None or days_since_op > 60:
        parts = []
        if days_since_op is None:
            parts.append("no outbound messages, inbound replies, or bookings on record")
        else:
            parts.append(f"last operational activity {days_since_op} days ago")
        if not has_users:
            parts.append("no active users")
        if not has_leads:
            parts.append("no leads imported")
        reason = "Inactive. " + "; ".join(parts) + "."
        return "inactive", reason

    if days_since_op > 30:
        reason = (
            f"At risk: last operational activity {days_since_op} days ago "
            f"(outbound message, inbound reply, or booking). "
            f"{active_users} active user{'s' if active_users != 1 else ''}, "
            f"{total_leads} lead{'s' if total_leads != 1 else ''}."
        )
        return "at_risk", reason

    # 15–30 days
    parts = []
    if days_since_op is not None:
        parts.append(f"last operational activity {days_since_op} days ago")
    if not has_users:
        parts.append("no active users")
    if not has_leads:
        parts.append("no leads imported")
    if not parts:
        parts.append("activity slowing")
    reason = "Watch: " + "; ".join(parts) + "."
    return "watch", reason


@router.get("/customer-health")
def get_customer_health(
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """
    Portfolio-level health for every customer organization under this brand.

    NO N+1: six aggregate queries regardless of org count.
    Security: platform_id filter on every query; no god bypass.
    NULL stays NULL — login excluded from operational signal.
    """
    from datetime import datetime, timedelta
    from app.models.models import Lead, Message, Reply, BookingLink

    user, mem, platform = executive
    platform_id = platform.id
    now = datetime.utcnow()

    # ── 1. All orgs for this platform ─────────────────────────────────────────
    orgs = (
        db.query(Organization)
        .filter(Organization.platform_id == platform_id)
        .order_by(Organization.name)
        .all()
    )
    if not orgs:
        return {
            "platform_id": platform_id,
            "platform_name": platform.name,
            "summary": {"total": 0, "healthy": 0, "watch": 0,
                        "at_risk": 0, "inactive": 0, "onboarding": 0},
            "organizations": [],
        }

    org_ids = [o.id for o in orgs]

    # ── 2. Active-user count per org ──────────────────────────────────────────
    user_rows = (
        db.query(
            User.organization_id,
            func.count(User.id).label("active_users"),
            func.max(User.last_login_at).label("last_login"),
        )
        .filter(
            User.organization_id.in_(org_ids),
            User.is_active.is_(True),
        )
        .group_by(User.organization_id)
        .all()
    )
    user_map = {r.organization_id: r for r in user_rows}

    # ── 3. Lead stats per org (exclude test leads) ────────────────────────────
    thirty_days_ago = now - timedelta(days=30)
    lead_rows = (
        db.query(
            Lead.organization_id,
            func.count(Lead.id).label("total_leads"),
            func.sum(
                sa_case((Lead.created_at >= thirty_days_ago, 1), else_=0)
            ).label("leads_last_30d"),
            func.sum(
                sa_case((Lead.status == "hot", 1), else_=0)
            ).label("hot_leads"),
            func.max(Lead.created_at).label("last_lead_import"),
        )
        .filter(
            Lead.organization_id.in_(org_ids),
            Lead.is_test.is_(False),
        )
        .group_by(Lead.organization_id)
        .all()
    )
    lead_map = {r.organization_id: r for r in lead_rows}

    # ── 4. Last outbound message per org ──────────────────────────────────────
    msg_rows = (
        db.query(
            Lead.organization_id,
            func.max(Message.sent_at).label("last_outbound_message"),
        )
        .join(Message, Message.lead_id == Lead.id)
        .filter(Lead.organization_id.in_(org_ids))
        .group_by(Lead.organization_id)
        .all()
    )
    msg_map = {r.organization_id: r.last_outbound_message for r in msg_rows}

    # ── 5. Last inbound reply per org ─────────────────────────────────────────
    reply_rows = (
        db.query(
            Lead.organization_id,
            func.max(Reply.received_at).label("last_inbound_reply"),
        )
        .join(Reply, Reply.lead_id == Lead.id)
        .filter(Lead.organization_id.in_(org_ids))
        .group_by(Lead.organization_id)
        .all()
    )
    reply_map = {r.organization_id: r.last_inbound_reply for r in reply_rows}

    # ── 6. Last booking per org ───────────────────────────────────────────────
    booking_rows = (
        db.query(
            Lead.organization_id,
            func.count(BookingLink.id).label("booked_count"),
            func.max(BookingLink.booked_time).label("last_booking"),
        )
        .join(BookingLink, BookingLink.lead_id == Lead.id)
        .filter(
            Lead.organization_id.in_(org_ids),
            BookingLink.status == "booked",
        )
        .group_by(Lead.organization_id)
        .all()
    )
    booking_map = {r.organization_id: r for r in booking_rows}

    # ── Build per-org records ─────────────────────────────────────────────────
    summary = {"total": len(orgs), "healthy": 0, "watch": 0,
               "at_risk": 0, "inactive": 0, "onboarding": 0}
    result_orgs = []

    for org in orgs:
        oid = org.id
        u   = user_map.get(oid)
        l   = lead_map.get(oid)
        b   = booking_map.get(oid)

        active_users   = u.active_users if u else 0
        last_login     = u.last_login if u else None
        total_leads    = int(l.total_leads or 0) if l else 0
        leads_30d      = int(l.leads_last_30d or 0) if l else 0
        hot_leads      = int(l.hot_leads or 0) if l else 0
        last_import    = l.last_lead_import if l else None
        booked_count   = int(b.booked_count or 0) if b else 0
        last_booking   = b.last_booking if b else None
        last_outbound  = msg_map.get(oid)
        last_reply     = reply_map.get(oid)

        candidates = [t for t in (last_outbound, last_reply, last_booking) if t is not None]
        last_op = max(candidates) if candidates else None

        all_activity = [t for t in (last_login, last_op) if t is not None]
        last_activity = max(all_activity) if all_activity else None

        age_days      = (now - org.created_at).days if org.created_at else 0
        days_since_op = (now - last_op).days if last_op else None

        health, reason = _classify_health(age_days, active_users, total_leads, days_since_op)
        summary[health] += 1

        def _iso(dt):
            return dt.isoformat() if dt else None

        result_orgs.append({
            "id":                        oid,
            "name":                      org.name,
            "health":                    health,
            "reason":                    reason,
            "plan":                      getattr(org, "plan", None),
            "provisioned_at":            _iso(org.created_at),
            "organization_age_days":     age_days,
            "active_users":              active_users,
            "total_leads":               total_leads,
            "leads_last_30d":            leads_30d,
            "hot_leads":                 hot_leads,
            "booked_count":              booked_count,
            "last_login":                _iso(last_login),
            "last_lead_import":          _iso(last_import),
            "last_outbound_message":     _iso(last_outbound),
            "last_inbound_reply":        _iso(last_reply),
            "last_booking":              _iso(last_booking),
            "last_operational_activity": _iso(last_op),
            "last_activity":             _iso(last_activity),
        })

    return {
        "platform_id":   platform_id,
        "platform_name": platform.name,
        "summary":       summary,
        "organizations": result_orgs,
    }


# ── Organization Observation (read-only executive view) ───────────────────────

def _get_org_or_403(db, platform_id, org_id):
    """Fetch org, assert it belongs to this executive's platform. 404/403 otherwise."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")
    if org.platform_id != platform_id:
        raise HTTPException(
            status_code=403,
            detail="Organization is not in your brand's portfolio.",
        )
    return org


@router.get("/organizations/{org_id}")
def get_org_observation_detail(
    org_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """Single-org overview for the Executive Observation Console.
    Platform-isolation enforced: returns 403 if org does not belong to this executive's brand.
    No customer credentials, no config, no PII beyond org name.
    """
    from datetime import datetime
    from app.models.models import Lead, Message, Reply, BookingLink

    user, mem, platform = executive
    org = _get_org_or_403(db, platform.id, org_id)

    now = datetime.utcnow()
    age_days = (now - org.created_at).days if org.created_at else 0

    active_users = (
        db.query(func.count(User.id))
        .filter(User.organization_id == org_id, User.is_active.is_(True))
        .scalar() or 0
    )
    total_leads = (
        db.query(func.count(Lead.id))
        .filter(Lead.organization_id == org_id, Lead.is_test.is_(False))
        .scalar() or 0
    )

    last_outbound = (
        db.query(func.max(Message.sent_at))
        .join(Lead, Lead.id == Message.lead_id)
        .filter(Lead.organization_id == org_id)
        .scalar()
    )
    last_reply = (
        db.query(func.max(Reply.received_at))
        .join(Lead, Lead.id == Reply.lead_id)
        .filter(Lead.organization_id == org_id)
        .scalar()
    )
    last_booking = (
        db.query(func.max(BookingLink.booked_time))
        .join(Lead, Lead.id == BookingLink.lead_id)
        .filter(Lead.organization_id == org_id, BookingLink.status == "booked")
        .scalar()
    )

    candidates = [t for t in (last_outbound, last_reply, last_booking) if t is not None]
    last_op = max(candidates) if candidates else None
    days_since_op = (now - last_op).days if last_op else None
    health, reason = _classify_health(age_days, active_users, total_leads, days_since_op)

    def _iso(dt):
        return dt.isoformat() if dt else None

    return {
        "id":                        org.id,
        "name":                      org.name,
        "platform_id":               platform.id,
        "platform_name":             platform.name,
        "is_active":                 org.is_active,
        "provisioned_at":            _iso(org.created_at),
        "organization_age_days":     age_days,
        "health":                    health,
        "health_reason":             reason,
        "active_users":              active_users,
        "total_leads":               total_leads,
        "last_outbound_message":     _iso(last_outbound),
        "last_inbound_reply":        _iso(last_reply),
        "last_booking":              _iso(last_booking),
        "last_operational_activity": _iso(last_op),
    }


@router.get("/organizations/{org_id}/leads/summary")
def get_org_leads_summary(
    org_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """Lead volume and status breakdown. No lead PII, no message content."""
    from datetime import datetime, timedelta
    from app.models.models import Lead

    user, mem, platform = executive
    org = _get_org_or_403(db, platform.id, org_id)

    now = datetime.utcnow()
    thirty_days_ago = now - timedelta(days=30)

    rows = (
        db.query(Lead.status, func.count(Lead.id).label("count"))
        .filter(Lead.organization_id == org_id, Lead.is_test.is_(False))
        .group_by(Lead.status)
        .all()
    )
    by_status = {r.status: r.count for r in rows}
    total = sum(by_status.values())

    leads_30d = (
        db.query(func.count(Lead.id))
        .filter(
            Lead.organization_id == org_id,
            Lead.is_test.is_(False),
            Lead.created_at >= thirty_days_ago,
        )
        .scalar() or 0
    )

    return {
        "org_id":              org.id,
        "org_name":            org.name,
        "total_leads":         total,
        "leads_last_30_days":  leads_30d,
        "by_status":           by_status,
    }


@router.get("/organizations/{org_id}/team")
def get_org_team(
    org_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """Users in this customer org. Email exposed for executive visibility; no credentials."""
    user, mem, platform = executive
    org = _get_org_or_403(db, platform.id, org_id)

    users = (
        db.query(User)
        .filter(User.organization_id == org_id)
        .order_by(User.role, User.email)
        .all()
    )

    active_count  = sum(1 for u in users if u.is_active)
    advisor_count = sum(1 for u in users if getattr(u, "role", None) == "advisor")

    return {
        "org_id":        org.id,
        "org_name":      org.name,
        "total_users":   len(users),
        "active_users":  active_count,
        "advisor_count": advisor_count,
        "members": [
            {
                "name":      u.full_name or u.email.split("@")[0],
                "email":     u.email,
                "role":      u.role,
                "is_active": u.is_active,
            }
            for u in users
        ],
    }


@router.get("/organizations/{org_id}/activity")
def get_org_activity(
    org_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """Last 15 operational events (aggregate). No message content, no lead PII."""
    from app.models.models import Lead, Message, Reply, BookingLink

    user, mem, platform = executive
    org = _get_org_or_403(db, platform.id, org_id)

    events = []

    lead_rows = (
        db.query(Lead.created_at)
        .filter(Lead.organization_id == org_id, Lead.is_test.is_(False))
        .order_by(Lead.created_at.desc())
        .limit(15).all()
    )
    for r in lead_rows:
        if r.created_at:
            events.append({"ts": r.created_at, "type": "lead_imported", "description": "Lead imported"})

    msg_rows = (
        db.query(Message.sent_at)
        .join(Lead, Lead.id == Message.lead_id)
        .filter(Lead.organization_id == org_id)
        .order_by(Message.sent_at.desc())
        .limit(15).all()
    )
    for r in msg_rows:
        if r.sent_at:
            events.append({"ts": r.sent_at, "type": "message_sent", "description": "Outbound message sent"})

    reply_rows = (
        db.query(Reply.received_at)
        .join(Lead, Lead.id == Reply.lead_id)
        .filter(Lead.organization_id == org_id)
        .order_by(Reply.received_at.desc())
        .limit(15).all()
    )
    for r in reply_rows:
        if r.received_at:
            events.append({"ts": r.received_at, "type": "reply_received", "description": "Inbound reply received"})

    booking_rows = (
        db.query(BookingLink.booked_time)
        .join(Lead, Lead.id == BookingLink.lead_id)
        .filter(Lead.organization_id == org_id, BookingLink.status == "booked")
        .order_by(BookingLink.booked_time.desc())
        .limit(15).all()
    )
    for r in booking_rows:
        if r.booked_time:
            events.append({"ts": r.booked_time, "type": "appointment_booked", "description": "Appointment booked"})

    events.sort(key=lambda e: e["ts"], reverse=True)
    events = events[:15]

    return {
        "org_id":   org.id,
        "org_name": org.name,
        "events": [
            {
                "timestamp":   e["ts"].isoformat(),
                "type":        e["type"],
                "description": e["description"],
            }
            for e in events
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
