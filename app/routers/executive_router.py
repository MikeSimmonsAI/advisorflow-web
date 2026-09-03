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

from datetime import datetime, timedelta, time as dt_time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, case as sa_case, distinct as sa_distinct

from app.deps import get_db, get_current_user, require_god, require_brand_executive
from app.models.models import (
    User, Organization, Platform,
    Lead, Reply, ReplyClassification, Message, EmailMessage,
    CadenceState, BookingLink,
)
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

# ── Per-organization detail and observation (read-only) ───────────────────────

@router.get("/organizations/{org_id}")
def get_executive_org_detail(
    org_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """Return org identity for the executive observation banner.

    SECURITY:
    - require_brand_executive validates the grant (never modifies user)
    - org.platform_id == platform.id enforced (cross-brand = 404)
    """
    user, mem, platform = executive
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org or org.platform_id != platform.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Organization not found.")
    return {
        "id": org.id,
        "name": org.name,
        "platform_id": org.platform_id,
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }


@router.get("/organizations/{org_id}/observe/overview")
def get_org_observation_overview(
    org_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """Aggregate read-only dashboard data for a customer organization.

    SECURITY INVARIANTS (never break these):
    - require_brand_executive validates brand_executive membership.
      It is never whitelisted for god_admin here — observation is an executive tool.
    - org.platform_id == platform.id: cross-brand access returns 404.
    - ALL queries use org_id from the path parameter, NEVER current_user.organization_id.
    - current_user.organization_id is never read, never mutated. Stays NULL for Michael.
    - Mutation is never performed. This endpoint is GET-only, read-only throughout.
    - read_only: True is always returned to inform the frontend.
    """
    user, mem, platform = executive
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org or org.platform_id != platform.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Organization not found.")

    now = datetime.utcnow()
    start_24h = now - timedelta(hours=24)
    start_7d = now - timedelta(days=7)
    end_of_today = datetime.combine(now.date(), dt_time.max)

    # Active lead filter — same exclusion as normal Overview (Gate E).
    # Excludes manual_flag='remove_all'; allows None and 'bad_email'.
    def _active(q):
        return q.filter(
            Lead.organization_id == org_id,
            (Lead.manual_flag == None) | (Lead.manual_flag == "bad_email"),
        )

    # ── Lead counts ──────────────────────────────────────────────────────────
    total_leads = _active(db.query(func.count(Lead.id))).scalar() or 0

    dnc_count = db.query(func.count(Lead.id)).filter(
        Lead.organization_id == org_id,
        Lead.status == "dnc",
    ).scalar() or 0

    # ── Status funnel ─────────────────────────────────────────────────────────
    stages = ["new", "sent", "replied", "hot", "booked"]
    funnel_rows = (
        _active(db.query(Lead.status, func.count(Lead.id)))
        .filter(Lead.status.in_(stages))
        .group_by(Lead.status)
        .all()
    )
    stage_counts = {s: 0 for s in stages}
    for s, c in funnel_rows:
        if s in stage_counts:
            stage_counts[s] = int(c or 0)
    funnel = [
        {"status": s, "label": s.replace("_", " ").title(), "count": stage_counts[s]}
        for s in stages
    ]
    new_leads = stage_counts["new"]
    sent_leads = stage_counts["sent"]
    booked_leads = stage_counts["booked"]
    hot_reply_funnel = stage_counts["hot"]

    # ── Hot replies (needs_attention: INTERESTED + CALLBACK) ─────────────────
    reply_rows = (
        db.query(Reply, Lead.first_name, Lead.last_name)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(
            Lead.organization_id == org_id,
            Reply.classification.in_([
                ReplyClassification.INTERESTED,
                ReplyClassification.CALLBACK,
            ]),
        )
        .order_by(Reply.received_at.desc())
        .limit(20)
        .all()
    )
    hot_replies = [
        {
            "id": r.Reply.id,
            "lead_id": r.Reply.lead_id,
            "lead_name": (
                f"{r.first_name or ''} {r.last_name or ''}".strip() or "Unknown"
            ),
            "body": r.Reply.body,
            "classification": (
                r.Reply.classification.value if r.Reply.classification else None
            ),
            "is_hot": r.Reply.is_hot,
            "source": r.Reply.source,
            "reviewed_at": (
                r.Reply.reviewed_at.isoformat() if r.Reply.reviewed_at else None
            ),
            "received_at": (
                r.Reply.received_at.isoformat() if r.Reply.received_at else None
            ),
        }
        for r in reply_rows
    ]
    hot_reply_count = len(hot_replies)

    # ── Briefing metrics ──────────────────────────────────────────────────────
    cadence_touches = (
        db.query(func.count(CadenceState.id))
        .join(Lead, CadenceState.lead_id == Lead.id)
        .filter(
            Lead.organization_id == org_id,
            CadenceState.status == "active",
            CadenceState.next_touch_due_at.isnot(None),
            CadenceState.next_touch_due_at <= end_of_today,
        )
        .scalar() or 0
    )

    leads_last_24h = (
        db.query(func.count(Lead.id))
        .filter(Lead.organization_id == org_id, Lead.created_at >= start_24h)
        .scalar() or 0
    )

    bookings_7d = (
        db.query(func.count(sa_distinct(BookingLink.lead_id)))
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(
            Lead.organization_id == org_id,
            BookingLink.status == "booked",
            BookingLink.booked_time.isnot(None),
            BookingLink.booked_time >= start_7d,
        )
        .scalar() or 0
    )

    appts_waiting = (
        db.query(func.count(sa_distinct(BookingLink.lead_id)))
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(
            Lead.organization_id == org_id,
            BookingLink.status.in_(["booked", "confirmed"]),
        )
        .scalar() or 0
    )

    # ── Leads needing action (new, replied, hot — not yet booked or dnc) ──────
    action_leads_raw = (
        _active(db.query(Lead))
        .filter(Lead.status.in_(["new", "replied", "hot"]))
        .order_by(Lead.last_messaged_at.asc())
        .limit(8)
        .all()
    )
    leads_needing_action = [
        {
            "id": l.id,
            "first_name": l.first_name,
            "last_name": l.last_name,
            "phone": l.phone,
            "email": l.email,
            "status": l.status,
            "source_file": l.source_file,
            "import_list_name": l.import_list_name,
            "assigned_to_id": l.assigned_to_id,
            "last_messaged_at": (
                l.last_messaged_at.isoformat() if l.last_messaged_at else None
            ),
        }
        for l in action_leads_raw
    ]

    # ── Recent activity (SMS + email, last 7 days) ────────────────────────────
    cutoff = start_7d
    sms_rows = (
        db.query(Message, Lead)
        .join(Lead, Message.lead_id == Lead.id)
        .filter(Lead.organization_id == org_id, Message.sent_at >= cutoff)
        .order_by(Message.sent_at.desc())
        .limit(8)
        .all()
    )
    sms_items = [
        {
            "id": msg.id, "channel": "sms",
            "lead_id": lead.id,
            "lead_name": (
                f"{lead.first_name or ''} {lead.last_name or ''}".strip()
                or lead.phone or "—"
            ),
            "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
            "delivery_status": (
                msg.delivery_status or msg.twilio_status or "pending"
            ),
        }
        for msg, lead in sms_rows
    ]
    email_rows = (
        db.query(EmailMessage, Lead)
        .join(Lead, EmailMessage.lead_id == Lead.id)
        .filter(Lead.organization_id == org_id, EmailMessage.sent_at >= cutoff)
        .order_by(EmailMessage.sent_at.desc())
        .limit(8)
        .all()
    )
    email_items = [
        {
            "id": msg.id, "channel": "email",
            "lead_id": lead.id,
            "lead_name": (
                f"{lead.first_name or ''} {lead.last_name or ''}".strip()
                or lead.email or "—"
            ),
            "subject": msg.subject,
            "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
            "delivery_status": msg.status or "sent",
        }
        for msg, lead in email_rows
    ]
    recent_activity = sorted(
        sms_items + email_items,
        key=lambda x: x["sent_at"] or "",
        reverse=True,
    )[:8]

    # ── Rate calculations ─────────────────────────────────────────────────────
    reply_rate = (
        round((hot_reply_count / sent_leads) * 100) if sent_leads > 0 else None
    )
    booking_rate = (
        round((booked_leads / sent_leads) * 100) if sent_leads > 0 else None
    )

    return {
        "read_only": True,
        "org": {
            "id": org.id,
            "name": org.name,
            "platform_id": org.platform_id,
        },
        "lead_summary": {
            "total": total_leads,
            "new_unworked": new_leads,
            "hot_replies": hot_reply_count,
            "arrangements": booked_leads,
            "dnc_opted_out": dnc_count,
            "sent": sent_leads,
            "reply_rate": reply_rate,
            "arrangement_rate": booking_rate,
            "cadence_touches_due_today": cadence_touches,
            "leads_imported_last_24h": leads_last_24h,
            "bookings_last_7_days": bookings_7d,
            "certified_appointments_waiting": appts_waiting,
        },
        "funnel": funnel,
        "hot_replies": hot_replies,
        "leads_needing_action": leads_needing_action,
        "recent_activity": recent_activity,
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
