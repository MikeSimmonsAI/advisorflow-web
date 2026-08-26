"""
Sales Workspace API — /sales/*

Checkpoint 1: My Day, My Pipeline, Opportunity Detail, against real data.

EVERY route here is guarded by `require_sales_member` or `require_sales_manager`
from app/services/sales_access.py, and every record read goes through
`assert_can_view_opportunity`. Route-level auth alone is not enough: a rep must
not be able to read another rep's deal, or another brand's pipeline, by
guessing an id.

WHAT THIS MODULE MUST NEVER DO
------------------------------
· Touch `leads`, `messages`, or any customer-tenant table. A salesperson is not
  a tenant user. The two domains meet at exactly one nullable column
  (Opportunity.customer_organization_id) and nowhere else.
· Read `current_user.organization_id`. It is NULL for these users by design.
· Invent an appointment. Scheduling arrives in Checkpoint 2; until then the
  appointment-shaped fields report honestly that there is no scheduling engine
  yet rather than returning a plausible empty list that reads as "no meetings".
"""
from datetime import datetime, timedelta, date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.models import User, Organization, Platform
from app.models.sales_models import (
    Membership, BrandSalesOrg, BrandPackage, Opportunity, DiscoveryRecord,
    OpportunityEvent,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    OPPORTUNITY_STAGES, ALL_STAGES, STAGE_LABELS, DEMO_STATUSES,
    STAGE_PROSPECT, STAGE_CONTACTED, STAGE_DISCOVERY, STAGE_DEMO_BUILD,
    STAGE_PROPOSAL, STAGE_CLOSING, STAGE_WON, STAGE_ONBOARDING, STAGE_LIVE,
    STAGE_LOST, DEMO_REQUESTED, DEMO_READY,
)
from app.models.scheduling_models import (
    SalesAppointment, AppointmentParticipant, MeetingType,
    APPT_CANCELLED, CONF_PENDING, CONF_SENT,
)
from app.services.sales_access import (
    require_sales_member, require_sales_manager,
    assert_can_view_opportunity, assert_can_edit_opportunity, assert_can_reassign,
    sales_org_ids, sales_memberships, is_sales_manager, is_god,
)
from app.services import availability as _av
from app.services import appointment_meetings as _apmeet
from app.services import proposal_workqueue as _pwq

router = APIRouter(prefix="/sales", tags=["sales"])

# Scheduling SHIPPED in Checkpoint 2. Everything that used to report
# {available:false} now returns real appointment data, and an empty result now
# genuinely means "no meetings", which is a different statement from the one
# this constant used to make. Kept only as the shape for capabilities that are
# still genuinely absent (calendar push — Checkpoint 3).
CALENDAR_SYNC_NOT_BUILT = {
    "available": False,
    "reason": "External calendar push (Microsoft 365, then Google, then .ics) "
              "lands in Checkpoint 3. The AdvisorFlow appointment is the source "
              "of truth and is not synced anywhere yet.",
}


# ── helpers ─────────────────────────────────────────────────────────────────

def _resolve_context(user: User, db: Session, brand_sales_org_id: Optional[str] = None):
    """Which brand sales org is this request operating in, and as what role?

    A user may hold several memberships (Mike holds god plus a manager seat).
    An explicit id wins; otherwise the single membership wins; otherwise the
    first, deterministically ordered so the answer never flickers.
    """
    allowed = sales_org_ids(user, db)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="No active brand sales membership.")
    if brand_sales_org_id:
        if brand_sales_org_id not in allowed:
            # 404 rather than 403 — do not confirm the org exists.
            raise HTTPException(status_code=404, detail="Brand sales org not found")
        target = brand_sales_org_id
    else:
        target = sorted(allowed)[0]
    org = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == target).first()
    if not org:
        raise HTTPException(status_code=404, detail="Brand sales org not found")
    return org


def _scoped_opportunities(user: User, db: Session, org: BrandSalesOrg):
    """Base query honouring the record-level rule, applied in SQL rather than
    filtered in Python after the fact."""
    q = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == org.id)
    if not is_sales_manager(user, db, org.id):
        # A rep sees their own book. Unowned opportunities are visible so a new
        # prospect is never orphaned into invisibility.
        q = q.filter(or_(Opportunity.owner_user_id == user.id,
                         Opportunity.owner_user_id.is_(None)))
    return q


def _days_in_stage(opp: Opportunity) -> Optional[int]:
    if not opp.stage_changed_at:
        return None
    return max(0, (datetime.utcnow() - opp.stage_changed_at).days)


def _money(v) -> Optional[float]:
    return float(v) if v is not None else None


def _user_name(db: Session, user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    u = db.query(User).filter(User.id == user_id).first()
    return u.full_name if u else None


def _attention(opp: Opportunity) -> Optional[str]:
    """Why this deal is shouting. One reason, most urgent first — a card with
    four warnings communicates nothing."""
    now = datetime.utcnow()
    if opp.status == "lost":
        return None
    if opp.next_action_due_at and opp.next_action_due_at < now:
        return "Next action overdue"
    if opp.stage == STAGE_DEMO_BUILD and opp.demo_due_at and opp.demo_due_at < now:
        return "Demo build past due"
    if opp.stage == STAGE_DEMO_BUILD and not opp.demo_requirements:
        return "Demo requested with no requirements captured"
    if opp.stage == STAGE_DISCOVERY and opp.discovery_completed_at is None:
        days = _days_in_stage(opp)
        if days is not None and days >= 7:
            return "Discovery not completed after %d days" % days
    if opp.next_action is None and opp.stage not in (STAGE_WON, STAGE_LIVE):
        return "No next action set"
    days = _days_in_stage(opp)
    if days is not None and days >= 21 and opp.stage not in (STAGE_WON, STAGE_LIVE, STAGE_ONBOARDING):
        return "Stalled %d days in %s" % (days, STAGE_LABELS.get(opp.stage, opp.stage))
    return None


def next_appt_map(db: Session, opps) -> dict:
    """opportunity_id -> its next upcoming appointment, in ONE query.

    Built per request and threaded into `_card` rather than looked up per card:
    a 40-deal board would otherwise fire 40 extra queries to draw one line of
    text on each tile.
    """
    ids = [o.id for o in opps]
    if not ids:
        return {}
    rows = (db.query(SalesAppointment)
            .filter(SalesAppointment.opportunity_id.in_(ids),
                    SalesAppointment.status != APPT_CANCELLED,
                    SalesAppointment.ends_at >= datetime.utcnow())
            .order_by(SalesAppointment.starts_at.asc()).all())
    out = {}
    for a in rows:
        out.setdefault(a.opportunity_id, a)   # first = soonest, given the sort
    return out


def _card(opp: Opportunity, db: Session, appts: Optional[dict] = None,
          names: Optional[dict] = None) -> dict:
    """The shape both My Pipeline and My Day render. One serializer so a card
    can never mean two different things on two screens.

    `names` is an optional user_id -> full_name map. Without it this fires one
    query per card to resolve the owner, which a rep's own board never noticed
    and a manager's brand-wide board would feel immediately.
    """
    nxt = (appts or {}).get(opp.id)
    return {
        "id": opp.id,
        "company_name": opp.company_name,
        "contact_name": opp.contact_name,
        "phone": opp.phone,
        "email": opp.email,
        "industry": opp.industry,
        "stage": opp.stage,
        "stage_label": STAGE_LABELS.get(opp.stage, opp.stage),
        "status": opp.status,
        "owner_user_id": opp.owner_user_id,
        "owner_name": (names.get(opp.owner_user_id) if names is not None
                       else _user_name(db, opp.owner_user_id)),
        "days_in_stage": _days_in_stage(opp),
        "next_action": opp.next_action,
        "next_action_due_at": opp.next_action_due_at,
        "package_interest_id": opp.package_interest_id,
        "selected_package_id": opp.selected_package_id,
        "deal_value": _money(opp.deal_value),
        "deal_value_override": bool(opp.deal_value_override),
        "demo_status": opp.demo_status,
        "demo_due_at": opp.demo_due_at,
        "attention": _attention(opp),
        "updated_at": opp.updated_at,
        # Real, as of Checkpoint 2. None here means "nothing booked", which is
        # now a fact rather than a placeholder.
        "next_appointment": {
            "id": nxt.id, "title": nxt.title, "starts_at": nxt.starts_at,
            "timezone": nxt.timezone,
            "starts_at_local": _av.utc_to_local(nxt.starts_at, nxt.timezone),
        } if nxt else None,
        "confirmation_status": nxt.confirmation_status if nxt else None,
    }


def _visible_sales_appointments(db: Session, user: User, org):
    """Appointment visibility, mirroring opportunity visibility exactly.

    A rep sees meetings they are ON, plus meetings attached to a deal they own
    (so an owner is never blind to a call a manager booked for their deal).
    A manager sees the whole brand.
    """
    q = db.query(SalesAppointment).filter(
        SalesAppointment.brand_sales_org_id == org.id)
    if is_sales_manager(user, db, org.id):
        return q
    own_appt_ids = [r[0] for r in db.query(AppointmentParticipant.appointment_id)
                    .filter(AppointmentParticipant.user_id == user.id).all()]
    own_opp_ids = [r[0] for r in db.query(Opportunity.id)
                   .filter(Opportunity.owner_user_id == user.id).all()]
    return q.filter(SalesAppointment.id.in_(own_appt_ids or [""])
                    | SalesAppointment.opportunity_id.in_(own_opp_ids or [""]))


def _appt_brief(db: Session, a: SalesAppointment) -> dict:
    """The compact appointment shape My Day and the opportunity record render.

    Deliberately lighter than the scheduling router's full serializer — a My Day
    list of twelve meetings should not fan out into dozens of participant
    queries for detail nobody reads on that screen.
    """
    parts = (db.query(AppointmentParticipant, User)
             .join(User, User.id == AppointmentParticipant.user_id)
             .filter(AppointmentParticipant.appointment_id == a.id).all())
    mt = (db.query(MeetingType).filter(MeetingType.id == a.meeting_type_id).first()
          if a.meeting_type_id else None)
    return {
        "id": a.id,
        "title": a.title,
        "starts_at": a.starts_at,
        "ends_at": a.ends_at,
        "timezone": a.timezone,
        "starts_at_local": _av.utc_to_local(a.starts_at, a.timezone),
        "duration_minutes": int((a.ends_at - a.starts_at).total_seconds() // 60),
        "status": a.status,
        "confirmation_status": a.confirmation_status,
        "meeting_type": mt.name if mt else None,
        "meeting_type_key": mt.key if mt else None,
        "opportunity_id": a.opportunity_id,
        "prospect_name": a.prospect_name,
        "prospect_company": a.prospect_company,
        "meeting_url": a.meeting_url,
        # Checkpoint 4 — what powers JOIN MEETING on My Day. Attendee link only:
        # `meeting_out` has no field for the host url, so this cannot leak one.
        "video": _apmeet.meeting_out(_apmeet.get_meeting_row(db, a.id)),
        "participants": [{"user_id": u.id, "full_name": u.full_name,
                          "is_required": bool(p.is_required)} for p, u in parts],
    }


def _event(db: Session, opp: Opportunity, actor: User, event_type: str,
           summary: str, detail: Optional[str] = None) -> OpportunityEvent:
    """Append to the timeline. Never updated, never deleted — corrections are
    new rows, which is what makes the timeline trustworthy."""
    ev = OpportunityEvent(opportunity_id=opp.id, event_type=event_type,
                          summary=summary, detail=detail, actor_user_id=actor.id)
    db.add(ev)
    return ev


def _package(db: Session, package_id: Optional[str], platform_id: str) -> Optional[BrandPackage]:
    if not package_id:
        return None
    pkg = db.query(BrandPackage).filter(BrandPackage.id == package_id).first()
    if not pkg or pkg.platform_id != platform_id:
        # A package from another brand must never attach to this deal.
        raise HTTPException(status_code=400,
                            detail="That package does not belong to this brand.")
    return pkg


# ── request models ──────────────────────────────────────────────────────────

class OpportunityCreate(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=250)
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    timezone: Optional[str] = None
    source: Optional[str] = None
    package_interest_id: Optional[str] = None
    next_action: Optional[str] = None
    next_action_due_at: Optional[datetime] = None
    brand_sales_org_id: Optional[str] = None
    owner_user_id: Optional[str] = None


class OpportunityPatch(BaseModel):
    company_name: Optional[str] = None
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    timezone: Optional[str] = None
    source: Optional[str] = None
    stage: Optional[str] = None
    next_action: Optional[str] = None
    next_action_due_at: Optional[datetime] = None
    package_interest_id: Optional[str] = None
    selected_package_id: Optional[str] = None
    deal_value: Optional[float] = None
    deal_value_override_reason: Optional[str] = None
    loss_reason: Optional[str] = None
    # Demo build
    demo_status: Optional[str] = None
    demo_owner_user_id: Optional[str] = None
    demo_due_at: Optional[datetime] = None
    demo_requirements: Optional[str] = None
    demo_url: Optional[str] = None
    demo_notes: Optional[str] = None


class DiscoveryPatch(BaseModel):
    business_description: Optional[str] = None
    business_goals: Optional[str] = None
    current_process: Optional[str] = None
    current_tools: Optional[str] = None
    bottlenecks: Optional[str] = None
    lead_sources: Optional[str] = None
    team_size: Optional[str] = None
    appointment_process: Optional[str] = None
    follow_up_process: Optional[str] = None
    required_integrations: Optional[str] = None
    automation_opportunities: Optional[str] = None
    desired_outcome: Optional[str] = None
    demo_requirements: Optional[str] = None
    opportunity_notes: Optional[str] = None
    mark_complete: bool = False


class NoteCreate(BaseModel):
    summary: str = Field(..., min_length=1, max_length=250)
    detail: Optional[str] = None
    event_type: str = "note"


class ReassignRequest(BaseModel):
    owner_user_id: str


# ── context ─────────────────────────────────────────────────────────────────

@router.get("/me")
def sales_me(brand_sales_org_id: Optional[str] = Query(None),
             user: User = Depends(require_sales_member),
             db: Session = Depends(get_db)):
    """Everything the workspace shell needs to render itself: who you are, which
    brand you are selling, what role you hold, and what you may do.

    The frontend uses this to decide navigation. It is NOT the authorization —
    every route re-checks server-side. Hiding a nav item is not access control.
    """
    org = _resolve_context(user, db, brand_sales_org_id)
    platform = db.query(Platform).filter(Platform.id == org.platform_id).first()
    manager = is_sales_manager(user, db, org.id)

    memberships = []
    for m in sales_memberships(user, db):
        bso = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == m.scope_id).first()
        if bso:
            memberships.append({"brand_sales_org_id": bso.id, "name": bso.name,
                                "slug": bso.slug, "role": m.role})

    if is_god(user) and not memberships:
        role_label = "Owner"
    elif manager:
        role_label = "Sales Manager"
    else:
        role_label = "Sales Representative"

    return {
        "user": {
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "role": user.role,
            # NULL for a brand-sales user, and that is the correct answer.
            "organization_id": user.organization_id,
        },
        "brand_sales_org": {"id": org.id, "name": org.name, "slug": org.slug,
                            "timezone": org.timezone},
        "platform": {"id": platform.id, "name": platform.name,
                     "slug": platform.slug} if platform else None,
        "role": ROLE_SALES_MANAGER if manager else ROLE_SALES_REP,
        "role_label": role_label,
        "is_god": is_god(user),
        "memberships": memberships,
        "permissions": {
            "view_own_pipeline": True,
            "create_opportunity": True,
            "edit_own_opportunity": True,
            "view_team_pipeline": manager,
            "reassign_opportunity": manager,
            "override_deal_value": manager,
        },
        "stages": [{"key": s, "label": STAGE_LABELS[s]} for s in OPPORTUNITY_STAGES],
        # Scheduling is LIVE as of Checkpoint 2 — see /sales/availability/* and
        # /sales/appointments. Calendar push to Outlook/Google is not.
        "scheduling": {"available": True},
        "calendar_sync": CALENDAR_SYNC_NOT_BUILT,
    }


@router.get("/packages")
def sales_packages(brand_sales_org_id: Optional[str] = Query(None),
                   user: User = Depends(require_sales_member),
                   db: Session = Depends(get_db)):
    """The brand's SALES catalog. Deliberately not the Stripe billing plans —
    billing_plan_key is returned so it is visible that the link is unset, and
    no code here infers one."""
    org = _resolve_context(user, db, brand_sales_org_id)
    rows = (db.query(BrandPackage)
            .filter(BrandPackage.platform_id == org.platform_id,
                    BrandPackage.is_active.is_(True))
            .order_by(BrandPackage.sort_order.asc()).all())
    return [{
        "id": p.id, "key": p.key, "name": p.name, "description": p.description,
        "price": _money(p.price), "currency": p.currency,
        "billing_period": p.billing_period, "is_custom": bool(p.is_custom),
        "billing_plan_key": p.billing_plan_key,
    } for p in rows]


# ── My Day ──────────────────────────────────────────────────────────────────

@router.get("/my-day")
def my_day(brand_sales_org_id: Optional[str] = Query(None),
           user: User = Depends(require_sales_member),
           db: Session = Depends(get_db)):
    """The salesperson's operating brief.

    Every number here is computed from real opportunity data. The
    appointment-shaped sections are NOT faked as empty — they carry the
    scheduling-unavailable marker so the UI states plainly that scheduling
    arrives in Checkpoint 2.
    """
    org = _resolve_context(user, db, brand_sales_org_id)
    base = _scoped_opportunities(user, db, org)
    now = datetime.utcnow()
    today_end = datetime.combine(now.date(), datetime.max.time())
    open_opps = base.filter(Opportunity.status == "open").all()

    follow_ups = [o for o in open_opps
                  if o.next_action_due_at and o.next_action_due_at <= today_end]
    follow_ups.sort(key=lambda o: o.next_action_due_at)

    needs_action = [o for o in open_opps if _attention(o)]
    needs_action.sort(key=lambda o: (o.next_action_due_at or datetime.max))

    demos_to_build = [o for o in open_opps
                      if o.stage == STAGE_DEMO_BUILD
                      or o.demo_status in (DEMO_REQUESTED, "in_progress")]
    demos_to_build.sort(key=lambda o: (o.demo_due_at or datetime.max))

    month_start = datetime(now.year, now.month, 1)
    won_this_month = (base.filter(Opportunity.status == "won",
                                  Opportunity.won_at >= month_start).all())
    won_value = sum(float(o.deal_value or 0) for o in won_this_month)

    recent = (db.query(OpportunityEvent)
              .join(Opportunity, Opportunity.id == OpportunityEvent.opportunity_id)
              .filter(Opportunity.id.in_([o.id for o in base.all()] or [""]))
              .order_by(OpportunityEvent.occurred_at.desc())
              .limit(15).all())

    stage_counts = {}
    for o in open_opps:
        stage_counts[o.stage] = stage_counts.get(o.stage, 0) + 1

    # ── real appointments (Checkpoint 2) ────────────────────────────────────
    # Scoped exactly like the pipeline: a rep sees meetings they are on or that
    # belong to a deal they own; a manager sees the brand.
    tz = org.timezone or "America/Chicago"
    today_local = _av.utc_to_local(now, tz).date()
    day_start = _av.local_to_utc(today_local, 0, tz)
    day_end = _av.local_to_utc(today_local + timedelta(days=1), 0, tz)

    appt_q = _visible_sales_appointments(db, user, org).filter(
        SalesAppointment.status != APPT_CANCELLED)
    todays = (appt_q.filter(SalesAppointment.starts_at >= day_start,
                            SalesAppointment.starts_at < day_end)
              .order_by(SalesAppointment.starts_at.asc()).all())
    upcoming = (appt_q.filter(SalesAppointment.ends_at >= now)
                .order_by(SalesAppointment.starts_at.asc()).limit(25).all())
    unconfirmed = [a for a in upcoming
                   if a.confirmation_status in (CONF_PENDING, CONF_SENT)]

    def kind(a):
        mt = (db.query(MeetingType).filter(MeetingType.id == a.meeting_type_id).first()
              if a.meeting_type_id else None)
        return (mt.key or "") if mt else ""

    discoveries_today = [a for a in todays if "discovery" in kind(a)]
    demos_today = [a for a in todays if "demo" in kind(a)]

    appt_map = next_appt_map(db, open_opps)

    return {
        "brand_sales_org": {"id": org.id, "name": org.name,
                            "timezone": org.timezone},
        "metrics": {
            "active_opportunities": len(open_opps),
            "follow_ups_due": len(follow_ups),
            "needs_action": len(needs_action),
            "demos_to_build": len(demos_to_build),
            "won_this_month": len(won_this_month),
            "won_value_this_month": won_value,
            "appointments_today": len(todays),
            "needs_confirmation": len(unconfirmed),
            "discoveries_today": len(discoveries_today),
            "demos_today": len(demos_today),
        },
        "follow_ups_due": [_card(o, db, appt_map) for o in follow_ups[:12]],
        "deals_needing_action": [_card(o, db, appt_map) for o in needs_action[:12]],
        "demos_to_build": [_card(o, db, appt_map) for o in demos_to_build[:12]],
        "stage_counts": stage_counts,
        "recent_activity": [{
            "id": e.id, "opportunity_id": e.opportunity_id,
            "event_type": e.event_type, "summary": e.summary,
            "detail": e.detail, "occurred_at": e.occurred_at,
            "actor_name": _user_name(db, e.actor_user_id),
        } for e in recent],
        # REAL appointment data. An empty list here now means "no meetings",
        # which it did not mean in Checkpoint 1.
        "todays_appointments": [_appt_brief(db, a) for a in todays],
        "next_appointment": _appt_brief(db, upcoming[0]) if upcoming else None,
        "needs_confirmation": [_appt_brief(db, a) for a in unconfirmed[:12]],
        "discoveries_today": [_appt_brief(db, a) for a in discoveries_today],
        "demos_today": [_appt_brief(db, a) for a in demos_today],
        "upcoming_appointments": [_appt_brief(db, a) for a in upcoming[:12]],
        # ── Proposal work (Checkpoint 4) ────────────────────────────────────
        # Six queues, each with an action and a reason. Not a proposal report:
        # a rep opening My Day is deciding what to touch next, and a number
        # they cannot act on competes with the ones they can.
        "proposals": _pwq.proposal_queues(db, open_opps, now=now),
        # A closing call today is the highest-stakes thing on the calendar, so
        # it is surfaced separately rather than buried in the day's list.
        "closing_today": [_appt_brief(db, a) for a in todays
                          if "closing" in kind(a)],
        # Still genuinely absent — see the constant.
        "calendar_sync": CALENDAR_SYNC_NOT_BUILT,
    }


@router.get("/opportunities/{opp_id}/closing")
def opportunity_closing(opp_id: str,
                        user: User = Depends(require_sales_member),
                        db: Session = Depends(get_db)):
    """The Closing workspace for one deal.

    Assembled entirely from data Checkpoint 4 already produces — no new tables
    and nothing inferred. The warnings are the point: a closing screen that
    only shows status tells a rep what they already knew.
    """
    opp = db.query(Opportunity).filter(Opportunity.id == opp_id).first()
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    assert_can_view_opportunity(user, opp, db)
    return _pwq.closing_view(db, opp)


# ── Pipeline ────────────────────────────────────────────────────────────────

@router.get("/opportunities")
def list_opportunities(brand_sales_org_id: Optional[str] = Query(None),
                       stage: Optional[str] = Query(None),
                       owner_user_id: Optional[str] = Query(None),
                       include_lost: bool = Query(False),
                       user: User = Depends(require_sales_member),
                       db: Session = Depends(get_db)):
    """The pipeline board. Grouped by stage so the client never has to know the
    stage order — it comes from the server, from one vocabulary."""
    org = _resolve_context(user, db, brand_sales_org_id)
    q = _scoped_opportunities(user, db, org)

    if stage:
        if stage not in ALL_STAGES:
            raise HTTPException(status_code=400, detail="Unknown stage '%s'" % stage)
        q = q.filter(Opportunity.stage == stage)
    if owner_user_id:
        # Filtering by another rep is a manager capability; a rep filtering to
        # themselves is harmless and useful.
        if owner_user_id != user.id and not is_sales_manager(user, db, org.id):
            raise HTTPException(status_code=403,
                                detail="Only a sales manager can filter by another representative.")
        q = q.filter(Opportunity.owner_user_id == owner_user_id)
    if not include_lost:
        q = q.filter(Opportunity.stage != STAGE_LOST)

    rows = q.order_by(Opportunity.stage_changed_at.desc().nullslast()).all()
    appts = next_appt_map(db, rows)
    cards = [_card(o, db, appts) for o in rows]

    by_stage = {s: [] for s in OPPORTUNITY_STAGES}
    lost = []
    for c in cards:
        if c["stage"] == STAGE_LOST:
            lost.append(c)
        elif c["stage"] in by_stage:
            by_stage[c["stage"]].append(c)
        else:
            # A stage value the vocabulary no longer knows. Surface it rather
            # than silently dropping the deal off the board.
            by_stage.setdefault(c["stage"], []).append(c)

    return {
        "brand_sales_org": {"id": org.id, "name": org.name},
        "is_manager": is_sales_manager(user, db, org.id),
        "stages": [{
            "key": s, "label": STAGE_LABELS.get(s, s),
            "count": len(by_stage.get(s, [])),
            "opportunities": by_stage.get(s, []),
        } for s in OPPORTUNITY_STAGES],
        "lost": lost,
        "total": len(cards),
    }


@router.post("/opportunities", status_code=201)
def create_opportunity(body: OpportunityCreate,
                       user: User = Depends(require_sales_member),
                       db: Session = Depends(get_db)):
    org = _resolve_context(user, db, body.brand_sales_org_id)

    owner_id = user.id
    if body.owner_user_id and body.owner_user_id != user.id:
        if not is_sales_manager(user, db, org.id):
            raise HTTPException(status_code=403,
                                detail="Only a sales manager can assign to another representative.")
        owner_id = body.owner_user_id

    pkg = _package(db, body.package_interest_id, org.platform_id)

    opp = Opportunity(
        brand_sales_org_id=org.id,
        owner_user_id=owner_id,
        company_name=body.company_name.strip(),
        contact_name=(body.contact_name or "").strip() or None,
        phone=(body.phone or "").strip() or None,
        email=(body.email or "").strip().lower() or None,
        website=(body.website or "").strip() or None,
        industry=(body.industry or "").strip() or None,
        # Captured, never assumed. Grok hardcoded a timezone and that was a real
        # defect; fall back to the team default only, and record it explicitly.
        timezone=body.timezone or org.timezone,
        source=(body.source or "").strip() or None,
        stage=STAGE_PROSPECT,
        status="open",
        package_interest_id=pkg.id if pkg else None,
        next_action=body.next_action or "First contact",
        next_action_due_at=body.next_action_due_at,
        stage_changed_at=datetime.utcnow(),
    )
    db.add(opp)
    db.flush()
    _event(db, opp, user, "created", "Prospect created",
           "%s%s" % (opp.company_name,
                     (" · " + opp.contact_name) if opp.contact_name else ""))
    db.commit()
    db.refresh(opp)
    return _card(opp, db, {})


# ── Opportunity detail ──────────────────────────────────────────────────────

def _load(opp_id: str, user: User, db: Session) -> Opportunity:
    opp = db.query(Opportunity).filter(Opportunity.id == opp_id).first()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    assert_can_view_opportunity(user, opp, db)
    return opp


@router.get("/opportunities/{opp_id}")
def get_opportunity(opp_id: str,
                    user: User = Depends(require_sales_member),
                    db: Session = Depends(get_db)):
    opp = _load(opp_id, user, db)
    org = db.query(BrandSalesOrg).filter(
        BrandSalesOrg.id == opp.brand_sales_org_id).first()

    disc = db.query(DiscoveryRecord).filter(
        DiscoveryRecord.opportunity_id == opp.id).first()
    discovery = {k: getattr(disc, k, None) for k, _ in DiscoveryRecord.FIELDS} if disc else {
        k: None for k, _ in DiscoveryRecord.FIELDS}
    discovery["completed_at"] = disc.completed_at if disc else None
    discovery["completed_by_name"] = _user_name(db, disc.completed_by) if disc else None

    events = (db.query(OpportunityEvent)
              .filter(OpportunityEvent.opportunity_id == opp.id)
              .order_by(OpportunityEvent.occurred_at.desc()).all())

    def pkg_out(pid):
        p = db.query(BrandPackage).filter(BrandPackage.id == pid).first() if pid else None
        return {"id": p.id, "name": p.name, "key": p.key,
                "price": _money(p.price), "is_custom": bool(p.is_custom)} if p else None

    upcoming_appts = (db.query(SalesAppointment)
                      .filter(SalesAppointment.opportunity_id == opp.id,
                              SalesAppointment.status != APPT_CANCELLED)
                      .order_by(SalesAppointment.starts_at.asc()).all())
    card = _card(opp, db, next_appt_map(db, [opp]))
    card.update({
        "brand_sales_org": {"id": org.id, "name": org.name} if org else None,
        "website": opp.website,
        "timezone": opp.timezone,
        "source": opp.source,
        "package_interest": pkg_out(opp.package_interest_id),
        "selected_package": pkg_out(opp.selected_package_id),
        "deal_value_override_reason": opp.deal_value_override_reason,
        "deal_value_override_by_name": _user_name(db, opp.deal_value_override_by),
        "deal_value_override_at": opp.deal_value_override_at,
        "demo": {
            "status": opp.demo_status,
            "owner_user_id": opp.demo_owner_user_id,
            "owner_name": _user_name(db, opp.demo_owner_user_id),
            "requested_at": opp.demo_requested_at,
            "due_at": opp.demo_due_at,
            "ready_at": opp.demo_ready_at,
            "requirements": opp.demo_requirements,
            "url": opp.demo_url,
            "notes": opp.demo_notes,
        },
        "lifecycle": {
            "created_at": opp.created_at,
            "contacted_at": opp.contacted_at,
            "discovery_completed_at": opp.discovery_completed_at,
            "demo_requested_at": opp.demo_requested_at,
            "demo_ready_at": opp.demo_ready_at,
            "proposal_sent_at": opp.proposal_sent_at,
            "won_at": opp.won_at,
            "lost_at": opp.lost_at,
            "stage_changed_at": opp.stage_changed_at,
        },
        "loss_reason": opp.loss_reason,
        # Decision #7 — the permanent link, NULL until Won provisions a customer.
        "customer_organization_id": opp.customer_organization_id,
        "discovery": discovery,
        "discovery_fields": [{"key": k, "label": lbl} for k, lbl in DiscoveryRecord.FIELDS],
        "timeline": [{
            "id": e.id, "event_type": e.event_type, "summary": e.summary,
            "detail": e.detail, "occurred_at": e.occurred_at,
            "actor_name": _user_name(db, e.actor_user_id),
        } for e in events],
        "can_reassign": is_sales_manager(user, db, opp.brand_sales_org_id),
        "can_override_value": is_sales_manager(user, db, opp.brand_sales_org_id),
        # Real meetings on this deal. Empty means none booked.
        "appointments": [_appt_brief(db, a) for a in upcoming_appts],
        "calendar_sync": CALENDAR_SYNC_NOT_BUILT,
    })
    return card


@router.patch("/opportunities/{opp_id}")
def patch_opportunity(opp_id: str, body: OpportunityPatch,
                      user: User = Depends(require_sales_member),
                      db: Session = Depends(get_db)):
    opp = _load(opp_id, user, db)
    assert_can_edit_opportunity(user, opp, db)
    org = db.query(BrandSalesOrg).filter(
        BrandSalesOrg.id == opp.brand_sales_org_id).first()
    now = datetime.utcnow()
    data = body.model_dump(exclude_unset=True)

    # ── stage ───────────────────────────────────────────────────────────────
    if "stage" in data and data["stage"] and data["stage"] != opp.stage:
        new_stage = data["stage"]
        if new_stage not in ALL_STAGES:
            raise HTTPException(status_code=400, detail="Unknown stage '%s'" % new_stage)
        old = opp.stage
        opp.stage = new_stage
        opp.stage_changed_at = now
        if new_stage == STAGE_CONTACTED and not opp.contacted_at:
            opp.contacted_at = now
        if new_stage == STAGE_DEMO_BUILD:
            if not opp.demo_requested_at:
                opp.demo_requested_at = now
            if not opp.demo_status:
                opp.demo_status = DEMO_REQUESTED
            # Carry discovery's demo requirements forward rather than making the
            # rep retype what they already captured in the room.
            if not opp.demo_requirements:
                d = db.query(DiscoveryRecord).filter(
                    DiscoveryRecord.opportunity_id == opp.id).first()
                if d and d.demo_requirements:
                    opp.demo_requirements = d.demo_requirements
        if new_stage == STAGE_WON:
            opp.status = "won"
            opp.won_at = opp.won_at or now
        elif new_stage == STAGE_LOST:
            opp.status = "lost"
            opp.lost_at = opp.lost_at or now
        elif opp.status in ("won", "lost") and new_stage in OPPORTUNITY_STAGES[:6]:
            # Reopened. Clear the terminal stamp so it cannot claim two endings.
            opp.status = "open"
            opp.won_at = None
            opp.lost_at = None
        _event(db, opp, user, "stage_changed",
               "Stage: %s → %s" % (STAGE_LABELS.get(old, old),
                                   STAGE_LABELS.get(new_stage, new_stage)))

    # ── packages and deal value ─────────────────────────────────────────────
    platform_id = org.platform_id if org else None
    if "package_interest_id" in data:
        pkg = _package(db, data["package_interest_id"], platform_id)
        opp.package_interest_id = pkg.id if pkg else None

    if "selected_package_id" in data:
        pkg = _package(db, data["selected_package_id"], platform_id)
        opp.selected_package_id = pkg.id if pkg else None
        # Decision #9 — value DERIVES from the package unless explicitly
        # overridden. Selecting a package never silently clobbers an override.
        if pkg and not opp.deal_value_override:
            opp.deal_value = pkg.price
        if pkg:
            _event(db, opp, user, "package_selected",
                   "Package selected: %s" % pkg.name,
                   ("$%s" % format(float(pkg.price), ",.2f")) if pkg.price is not None
                   else "custom pricing")

    if "deal_value" in data and data["deal_value"] is not None:
        derived = None
        if opp.selected_package_id:
            p = db.query(BrandPackage).filter(
                BrandPackage.id == opp.selected_package_id).first()
            derived = float(p.price) if (p and p.price is not None) else None
        new_val = float(data["deal_value"])
        if derived is None or abs(new_val - derived) > 0.005:
            # An override, not a re-derivation. Manager-only, and audited —
            # decision #9 says the override is recorded, never silent.
            if not is_sales_manager(user, db, opp.brand_sales_org_id):
                raise HTTPException(
                    status_code=403,
                    detail="Only a sales manager can override the derived deal value.")
            reason = (data.get("deal_value_override_reason") or "").strip()
            if not reason:
                raise HTTPException(
                    status_code=400,
                    detail="A reason is required to override the derived deal value.")
            opp.deal_value_override = True
            opp.deal_value_override_by = user.id
            opp.deal_value_override_at = now
            opp.deal_value_override_reason = reason
            _event(db, opp, user, "deal_value_override",
                   "Deal value overridden to $%s" % format(new_val, ",.2f"),
                   "Derived: %s. Reason: %s" % (
                       ("$%s" % format(derived, ",.2f")) if derived is not None else "none",
                       reason))
        opp.deal_value = Decimal(str(new_val))

    # ── demo build ──────────────────────────────────────────────────────────
    if "demo_status" in data and data["demo_status"]:
        if data["demo_status"] not in DEMO_STATUSES:
            raise HTTPException(status_code=400,
                                detail="Unknown demo status '%s'" % data["demo_status"])
        if data["demo_status"] != opp.demo_status:
            opp.demo_status = data["demo_status"]
            if opp.demo_status == DEMO_READY and not opp.demo_ready_at:
                opp.demo_ready_at = now
            _event(db, opp, user, "demo_status", "Demo status: %s" % opp.demo_status)
    for f in ("demo_owner_user_id", "demo_due_at", "demo_requirements",
              "demo_url", "demo_notes"):
        if f in data:
            setattr(opp, f, data[f])

    # ── plain fields ────────────────────────────────────────────────────────
    for f in ("company_name", "contact_name", "phone", "email", "website",
              "industry", "timezone", "source", "loss_reason"):
        if f in data:
            v = data[f]
            if isinstance(v, str):
                v = v.strip() or None
                if f == "email" and v:
                    v = v.lower()
            setattr(opp, f, v)

    if "next_action" in data or "next_action_due_at" in data:
        if "next_action" in data:
            opp.next_action = (data["next_action"] or "").strip() or None
        if "next_action_due_at" in data:
            opp.next_action_due_at = data["next_action_due_at"]
        _event(db, opp, user, "next_action",
               "Next action: %s" % (opp.next_action or "cleared"),
               opp.next_action_due_at.isoformat() if opp.next_action_due_at else None)

    db.commit()
    db.refresh(opp)
    return get_opportunity(opp.id, user, db)


@router.put("/opportunities/{opp_id}/discovery")
def upsert_discovery(opp_id: str, body: DiscoveryPatch,
                     user: User = Depends(require_sales_member),
                     db: Session = Depends(get_db)):
    """Discovery is structured, not one giant notes field — the answers feed the
    demo build, and a blob cannot be queried or handed to a builder."""
    opp = _load(opp_id, user, db)
    assert_can_edit_opportunity(user, opp, db)

    disc = db.query(DiscoveryRecord).filter(
        DiscoveryRecord.opportunity_id == opp.id).first()
    created = False
    if not disc:
        disc = DiscoveryRecord(opportunity_id=opp.id)
        db.add(disc)
        created = True

    data = body.model_dump(exclude_unset=True)
    for key, _label in DiscoveryRecord.FIELDS:
        if key in data:
            v = data[key]
            setattr(disc, key, (v.strip() or None) if isinstance(v, str) else v)

    if body.mark_complete and not disc.completed_at:
        disc.completed_at = datetime.utcnow()
        disc.completed_by = user.id
        opp.discovery_completed_at = disc.completed_at
        _event(db, opp, user, "discovery_completed", "Discovery completed")
    elif created:
        _event(db, opp, user, "discovery_started", "Discovery notes started")

    db.commit()
    return get_opportunity(opp.id, user, db)


@router.post("/opportunities/{opp_id}/notes", status_code=201)
def add_note(opp_id: str, body: NoteCreate,
             user: User = Depends(require_sales_member),
             db: Session = Depends(get_db)):
    opp = _load(opp_id, user, db)
    assert_can_edit_opportunity(user, opp, db)
    ev = _event(db, opp, user, body.event_type or "note",
                body.summary.strip(), (body.detail or "").strip() or None)
    db.commit()
    db.refresh(ev)
    return {"id": ev.id, "event_type": ev.event_type, "summary": ev.summary,
            "detail": ev.detail, "occurred_at": ev.occurred_at,
            "actor_name": user.full_name}


@router.post("/opportunities/{opp_id}/reassign")
def reassign(opp_id: str, body: ReassignRequest,
             user: User = Depends(require_sales_manager),
             db: Session = Depends(get_db)):
    """Manager capability (#5). Audited — an unaudited reassignment is how a
    rep's book quietly changes hands."""
    opp = db.query(Opportunity).filter(Opportunity.id == opp_id).first()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    assert_can_reassign(user, opp, db)

    new_owner = db.query(User).filter(User.id == body.owner_user_id).first()
    if not new_owner:
        raise HTTPException(status_code=404, detail="User not found")
    # The new owner must actually sell this brand. Assigning a deal to someone
    # with no membership would hide it from everyone including them.
    if opp.brand_sales_org_id not in sales_org_ids(new_owner, db):
        raise HTTPException(
            status_code=400,
            detail="%s has no active membership in this brand sales organization."
                   % new_owner.full_name)

    old_name = _user_name(db, opp.owner_user_id) or "unassigned"
    opp.owner_user_id = new_owner.id
    _event(db, opp, user, "reassigned",
           "Reassigned: %s → %s" % (old_name, new_owner.full_name))
    db.commit()
    return get_opportunity(opp.id, user, db)


@router.get("/team")
def sales_team(brand_sales_org_id: Optional[str] = Query(None),
               user: User = Depends(require_sales_member),
               db: Session = Depends(get_db)):
    """Who sells this brand. A rep needs this to know who to hand a deal to;
    the full manager surface arrives in Checkpoint 3."""
    org = _resolve_context(user, db, brand_sales_org_id)
    rows = (db.query(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .filter(Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                    Membership.scope_id == org.id,
                    Membership.is_active.is_(True),
                    User.is_active.is_(True))
            .order_by(User.full_name.asc()).all())
    return [{"id": u.id, "full_name": u.full_name, "email": u.email,
             "role": m.role,
             "role_label": "Sales Manager" if m.role == ROLE_SALES_MANAGER
                           else "Sales Representative"}
            for u, m in rows]
