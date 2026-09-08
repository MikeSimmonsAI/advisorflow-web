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
# THE ONE PLACE THE PORTFOLIO IS COMPUTED. Command Center totals, Portfolio
# Health rows and the organization drill-down all read from this, so a headline
# and the list behind it can never describe different sets.
from app.services import executive_portfolio as portfolio_service

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


# ── Customer health portfolio ──────────────────────────────────────────────────

@router.get("/customer-health")
def get_customer_health(
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """Portfolio-level health for every customer organization in this brand.

    Health classifications (based on last operational activity):
      healthy    — operational activity within 14 days
      watch      — activity slowing, 15–30 days
      at_risk    — no activity 31–60 days
      inactive   — no activity >60 days, or no activity ever (org >30 days old)
      onboarding — org provisioned within 30 days and not yet healthy

    Operational activity = outbound SMS, inbound reply, or booking.

    SECURITY:
    - require_brand_executive validates the grant before any query runs.
    - All org queries are filtered by platform_id — cross-brand access is
      structurally impossible; there is no org_id parameter to forge.
    - current_user.organization_id is never read or used.
    """
    user, mem, platform = executive
    platform_id = platform.id

    now = datetime.utcnow()

    # All customer organizations provisioned from this platform
    orgs = (
        db.query(Organization)
        .filter(Organization.platform_id == platform_id)
        .order_by(Organization.name)
        .all()
    )

    if not orgs:
        return {
            "platform_name": platform.name,
            "summary": {
                "total": 0, "healthy": 0, "watch": 0,
                "at_risk": 0, "inactive": 0, "onboarding": 0,
            },
            "organizations": [],
        }

    org_ids = [o.id for o in orgs]

    # ── Bulk: active user count per org ───────────────────────────────────────
    user_counts = dict(
        db.query(User.organization_id, func.count(User.id))
        .filter(
            User.organization_id.in_(org_ids),
            User.is_active.is_(True),
        )
        .group_by(User.organization_id)
        .all()
    )

    # ── Bulk: active lead count per org (same exclusion as Overview) ──────────
    total_lead_counts = dict(
        db.query(Lead.organization_id, func.count(Lead.id))
        .filter(
            Lead.organization_id.in_(org_ids),
            (Lead.manual_flag == None) | (Lead.manual_flag == "bad_email"),  # noqa: E711
        )
        .group_by(Lead.organization_id)
        .all()
    )

    # ── Bulk: hot lead count per org ──────────────────────────────────────────
    hot_lead_counts = dict(
        db.query(Lead.organization_id, func.count(Lead.id))
        .filter(
            Lead.organization_id.in_(org_ids),
            Lead.status == "hot",
        )
        .group_by(Lead.organization_id)
        .all()
    )

    # ── Bulk: booked lead count per org ───────────────────────────────────────
    booked_counts = dict(
        db.query(Lead.organization_id, func.count(Lead.id))
        .filter(
            Lead.organization_id.in_(org_ids),
            Lead.status == "booked",
        )
        .group_by(Lead.organization_id)
        .all()
    )

    # ── Bulk: last outbound SMS per org ───────────────────────────────────────
    last_sms = dict(
        db.query(Lead.organization_id, func.max(Message.sent_at))
        .join(Lead, Message.lead_id == Lead.id)
        .filter(Lead.organization_id.in_(org_ids))
        .group_by(Lead.organization_id)
        .all()
    )

    # ── Bulk: last inbound reply per org ──────────────────────────────────────
    last_reply = dict(
        db.query(Lead.organization_id, func.max(Reply.received_at))
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(Lead.organization_id.in_(org_ids))
        .group_by(Lead.organization_id)
        .all()
    )

    # ── Bulk: last confirmed booking per org ──────────────────────────────────
    last_booking = dict(
        db.query(Lead.organization_id, func.max(BookingLink.booked_time))
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(
            Lead.organization_id.in_(org_ids),
            BookingLink.booked_time.isnot(None),
        )
        .group_by(Lead.organization_id)
        .all()
    )

    def _last_op(org_id):
        """Latest timestamp across SMS sent, reply received, booking confirmed."""
        candidates = [
            last_sms.get(org_id),
            last_reply.get(org_id),
            last_booking.get(org_id),
        ]
        valid = [c for c in candidates if c is not None]
        return max(valid) if valid else None

    def _classify(org):
        """Deterministic health classification from real activity timestamps."""
        loa = _last_op(org.id)
        age_days = (now - org.created_at).days if org.created_at else 9999

        if loa is None:
            if age_days <= 30:
                return "onboarding", "New organization — no operational activity yet."
            return "inactive", "No outbound messages, replies, or bookings on record."

        days_ago = (now - loa).days

        if days_ago <= 14:
            return "healthy", f"Last activity {days_ago}d ago — cadence on track."
        if days_ago <= 30:
            if age_days <= 30:
                return "onboarding", f"New organization — last activity {days_ago}d ago."
            return "watch", f"Activity slowing — last op. activity {days_ago}d ago."
        if days_ago <= 60:
            return "at_risk", f"No activity for {days_ago} days — follow-up recommended."
        return "inactive", f"No operational activity for {days_ago} days."

    org_rows = []
    summary = {
        "total": 0, "healthy": 0, "watch": 0,
        "at_risk": 0, "inactive": 0, "onboarding": 0,
    }

    for org in orgs:
        health, reason = _classify(org)
        loa = _last_op(org.id)
        org_rows.append({
            "id":                       org.id,
            "name":                     org.name,
            "health":                   health,
            "reason":                   reason,
            "active_users":             user_counts.get(org.id, 0),
            "total_leads":              total_lead_counts.get(org.id, 0),
            "hot_leads":               hot_lead_counts.get(org.id, 0),
            "booked_count":             booked_counts.get(org.id, 0),
            "last_operational_activity": loa.isoformat() if loa else None,
            "provisioned_at":           org.created_at.isoformat() if org.created_at else None,
            "plan":                     org.plan,
        })
        summary["total"] += 1
        summary[health] += 1

    return {
        "platform_name":  platform.name,
        "summary":        summary,
        "organizations":  org_rows,
    }


# ═══════════════════════════════════════════════════════════════════════════
# THE EXECUTIVE COMMAND EXPERIENCE
# ═══════════════════════════════════════════════════════════════════════════
#
# WHY THESE EXIST BESIDE THE ENDPOINTS ABOVE
# ------------------------------------------
# `/command-center` and `/customer-health` shipped early and other things read
# them; they are left exactly as they are. What they could not answer is the
# question an executive actually opens the product with — *how is my business
# doing, and what needs me today* — because each computes its own slice with
# its own queries, so a total on one screen and a list on another could
# disagree the moment anybody checked.
#
# Everything below is served from `app/services/executive_portfolio.py`, where
# ONE function builds the per-organization rows and the totals are summed from
# those exact rows. A headline is therefore always openable, and the count
# always matches.
#
# SCOPE IS UNCHANGED AND UNCHANGEABLE. Every route here takes
# `require_brand_executive`, and the service cannot be called without a
# platform id. There is no organization parameter that widens a portfolio and
# no header that can substitute for the grant.

@router.get("/portfolio")
def get_portfolio(
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """The Executive Command Center — the whole authorized portfolio at once.

    Returns the totals AND the organizations that need attention, because an
    executive asking "how is my business doing" is really asking two questions
    and the second one is the actionable half.

    NOTHING IS FILLED IN. A rate with no denominator is null; revenue that
    cannot be priced is null and reported with its coverage, so "$4,491 across
    3 of 5 customers" is sayable and a bare "$4,491" that silently implies the
    other two earn nothing is not.
    """
    user, mem, platform = executive
    rows = portfolio_service.rows(db, platform.id)
    totals = portfolio_service.portfolio(db, platform.id)

    # WORST FIRST. An executive opens this to find trouble, so the exceptions
    # lead and the healthy majority sits behind Portfolio Health.
    ranked = sorted([r for r in rows if r["attention"]],
                    key=portfolio_service.sort_key)

    return {
        "platform_id": platform.id,
        "platform_name": platform.name,
        "summary": totals,
        # The attention list is capped for the page, and the page is told the
        # true number so it can say "showing 6 of 11" rather than implying six
        # is all there is.
        "attention": [
            {
                "id": r["id"], "name": r["name"],
                "health": r["health"], "health_label": r["health_label"],
                "reason": r["reason"], "items": r["attention"],
            }
            for r in ranked[:8]
        ],
        "attention_total": len(ranked),
        "health_labels": portfolio_service.HEALTH_LABELS,
    }


@router.get("/portfolio/health")
def get_portfolio_health(
    filter: str = "all",
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """Portfolio Health — every organization, with the reason for its state.

    EVERY FILTER IS A REAL PREDICATE over data already on the row. An empty
    result therefore means "none match" and never "we could not compute that",
    which is the difference between a filter and a dead control.
    """
    user, mem, platform = executive
    key = filter if filter in portfolio_service.FILTERS else "all"
    rows = portfolio_service.rows(db, platform.id)
    kept = [r for r in rows if portfolio_service.matches_filter(r, key)]
    kept.sort(key=portfolio_service.sort_key)

    # THE COUNT BESIDE EVERY FILTER, computed from the same rows the table is
    # built from. A tab reading "Billing (0)" is useful; a tab that turns out
    # to be empty only after you click it is not.
    counts = {f: sum(1 for r in rows if portfolio_service.matches_filter(r, f))
              for f in portfolio_service.FILTERS}

    return {
        "platform_id": platform.id,
        "platform_name": platform.name,
        "filter": key,
        "filters": [{"key": f, "count": counts[f]}
                    for f in portfolio_service.FILTERS],
        "total": len(rows),
        "shown": len(kept),
        "organizations": kept,
        "health_labels": portfolio_service.HEALTH_LABELS,
        "recent_window_days": portfolio_service.RECENT_DAYS,
    }


@router.get("/organizations/{org_id}/performance")
def get_org_performance(
    org_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    """ONE organization, composed for an executive rather than for its staff.

    THIS IS NOT THE CUSTOMER DASHBOARD WITH BUTTONS HIDDEN. The observation
    endpoint above returns the operational queues a customer's own people work
    from — hot replies to answer, leads to call. Useful, and not what a
    regional executive needs: they are not going to work the queue, they are
    deciding whether this organization is performing and what to do about it.

    So this returns outcomes, rates, exceptions and the commercial picture, and
    it deliberately returns NO per-lead action list.

    SECURITY, IDENTICAL TO EVERY OTHER ROUTE HERE:
      - require_brand_executive validates the grant; god is not whitelisted.
      - org.platform_id == platform.id, so a cross-brand id is a 404 and not a
        thinner page.
      - `org_id` comes from the PATH. current_user.organization_id is never
        read and never written — observing is not joining.
      - GET only. `read_only: True` is returned so the frontend never has to
        infer it.
    """
    user, mem, platform = executive
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org or org.platform_id != platform.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Organization not found.")

    # BUILT FROM THE SAME ROWS AS THE PORTFOLIO. If this page and Portfolio
    # Health could compute a customer's health differently, an executive would
    # have two answers about the same business and no way to choose.
    rows = portfolio_service.rows(db, platform.id)
    row = next((r for r in rows if r["id"] == org_id), None)
    if row is None:                                      # pragma: no cover
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Organization not found.")

    # The peer comparison an executive actually wants: not a league table, just
    # where this one sits against the rest of the portfolio on the two figures
    # that matter. Reported only when there is something to compare against.
    peers = [r for r in rows if r["id"] != org_id]

    def _median(values):
        vals = sorted(v for v in values if v is not None)
        if not vals:
            return None
        mid = len(vals) // 2
        return vals[mid] if len(vals) % 2 else round(
            (vals[mid - 1] + vals[mid]) / 2, 1)

    comparison = None
    if peers:
        comparison = {
            "peer_count": len(peers),
            "median_response_rate": _median([p["response_rate"] for p in peers]),
            "median_conversion_rate": _median([p["conversion_rate"] for p in peers]),
            "median_appointments": _median(
                [float(p["appointments_total"]) for p in peers]),
        }

    return {
        "read_only": True,
        "platform_id": platform.id,
        "platform_name": platform.name,
        "organization": row,
        "comparison": comparison,
        # THE SWITCHER'S DATA, served with the page so moving between
        # organizations never requires a round trip through the owner shell.
        # Durable ids only — nothing here resolves an organization by name.
        "portfolio": [{"id": r["id"], "name": r["name"],
                       "health": r["health"], "health_label": r["health_label"]}
                      for r in sorted(rows, key=lambda r: (r["name"] or "").lower())],
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
