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
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

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
    STAGE_LOST, DEMO_REQUESTED, DEMO_READY, DEMO_IN_PROGRESS, DEMO_DELIVERED,
    DEMO_NOT_REQUESTED, demo_is_outstanding,
)
from app.models.scheduling_models import (
    SalesAppointment, AppointmentParticipant, MeetingType,
    APPT_CANCELLED, CONF_PENDING, CONF_SENT,
)
from app.models.meeting_models import AppointmentMeeting
from app.services.sales_access import (
    require_sales_member, require_sales_manager,
    assert_can_view_opportunity, assert_can_edit_opportunity, assert_can_reassign,
    sales_org_ids, sales_memberships, is_sales_manager, is_god,
)
from app.services import availability as _av
from app.services import appointment_meetings as _apmeet
from app.services import proposal_workqueue as _pwq
from app.services import package_pricing as _pp
# The single answer to "may this person quote this price?", and the queue a
# below-floor negotiation is routed into. Imported here rather than reimplemented
# so the PATCH endpoint and the approval endpoint judge the same deal the same way.
from app.services import pricing_authority as _authority
from app.services import pricing_approvals as _pricing_approvals
from app.services import compensation as _comp
from app.services import pipeline_projection as _projection
# Seller discovery as structured answers. A PRESENTATION adapter over the
# DiscoveryRecord columns that already exist — not a second discovery engine,
# and not a per-question column. See app/services/discovery_schema.py.
from app.services import discovery_schema as _discovery

router = APIRouter(prefix="/sales", tags=["sales"])


def _may_see_compensation(user, db, brand_sales_org_id) -> bool:
    """WHO MAY READ WHAT THE COMPANY PAYS ITS SALESPEOPLE.

    A sales manager qualifies by role - they already run the team's numbers.
    Everybody else needs the `sales_comp_view` capability explicitly granted,
    which is what lets a finance or ops person be shown compensation without
    also being made a manager of a sales team.

    An ordinary rep therefore sees NOTHING here by default. That is deliberate:
    the payload carries the override layers above them, and "what does my
    manager earn on my deal" is not a question the pipeline screen should answer
    by accident.

    WHY THE `sales_comp_view` CAPABILITY IS NOT CONSULTED HERE YET, AND WHY
    THAT IS NOT AN OVERSIGHT.

    `UserCapabilityGrant` is scoped by (user, customer ORGANIZATION). Every
    brand-sales identity has `organization_id = NULL` by positive assertion, so
    `grants_for()` returns [] for exactly the people this gate is about, and a
    capability check here would be a branch that can never be true. Shipping a
    permission that silently never fires is worse than not having it: it reads
    as configurable and is not.

    So the capability is REGISTERED (see capabilities.py) and the gate is
    role-based until the grant table can express a brand-sales scope. Widening
    it is one change in one function, and the registry entry is what makes that
    change obvious rather than archaeological.
    """
    if is_god(user):
        return True
    return bool(is_sales_manager(user, db, brand_sales_org_id))

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

def _no_workspace_error(user: User, db: Session) -> HTTPException:
    """Why is there no sales workspace to open? TWO DIFFERENT ANSWERS.

    THE OWNER IS NEVER REFUSED FOR WANT OF A MEMBERSHIP. `sales_org_ids` does
    not read memberships at all in its god branch - it returns the brand sales
    orgs belonging to the SELECTED brand, or every one of them when the owner is
    neutral. So an empty list for god never means "you have no membership"; it
    means the brand you selected has no sales team record yet.

    Reporting that as "No active brand sales membership." sent the owner looking
    for a permission problem that does not exist, and the obvious "fix" - grant
    god a membership - would have been a fake row papering over a provisioning
    gap. Two genuinely different conditions had one message, so this splits them:

        god, brand selected   -> that BRAND has no sales team yet   (409)
        god, neutral          -> NO brand has a sales team yet      (409)
        anyone else           -> no active membership               (403, unchanged)

    409 rather than 403 because nothing is being refused: the workspace is not
    forbidden, it does not exist yet, and the fix is to create it rather than to
    ask for access.
    """
    if not is_god(user):
        # UNCHANGED, deliberately and byte-for-byte. Normal enforcement is not
        # what is being fixed here, and a membership is still required.
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                             detail="No active brand sales membership.")

    from app.services import platform_owner as _po
    brand_id = _po.selected_brand_id(user)
    if brand_id:
        plat = db.query(Platform).filter(Platform.id == brand_id).first()
        name = plat.name if plat else "This brand"
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="%s has no sales team yet, so there is no sales workspace to "
                   "open. Create one from Workspaces." % name)
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="No brand has a sales team yet. Create one from Workspaces.")


def _resolve_context(user: User, db: Session, brand_sales_org_id: Optional[str] = None):
    """Which brand sales org is this request operating in, and as what role?

    A user may hold several memberships (Mike holds god plus a manager seat).
    An explicit id wins; otherwise the single membership wins; otherwise the
    first, deterministically ordered so the answer never flickers.

    For god the answer comes from the SELECTED BRAND, never from a membership -
    see `sales_access.sales_org_ids` and `_no_workspace_error` above.
    """
    allowed = sales_org_ids(user, db)
    if not allowed:
        raise _no_workspace_error(user, db)
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


def _name_map(db: Session, user_ids) -> dict:
    """user_id -> full_name for a whole list, in one query.

    The per-id helper above is correct for a single lookup and ruinous for a
    list: a fifteen-row activity feed resolved fifteen actors one at a time.
    Returns the same values that helper would, so a caller can swap to it
    without changing a single byte of its response.
    """
    ids = sorted({i for i in user_ids if i})
    if not ids:
        return {}
    return {u.id: u.full_name
            for u in db.query(User).filter(User.id.in_(ids)).all()}


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
        # `deal_value` above still means exactly what it always meant. These
        # are the commercial numbers the new pricing produces, named separately
        # so no historical figure changes meaning.
        "billing_option": opp.billing_option,
        "billing_option_label": (_pp.option_label(opp.billing_option,
                                                  opp.contract_term_months)
                                 if opp.billing_option else None),
        "contract_term_months": opp.contract_term_months,
        "implementation_fee": _money(opp.implementation_fee),
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


class ApptPrefetch:
    """Everything `_appt_brief` needs for a SET of appointments, in 3 queries.

    WHY THIS EXISTS. My Day renders seven overlapping lists — today's, the next
    one, the unconfirmed, discoveries, demos, upcoming, closing — off one
    appointment set. `_appt_brief` costs three queries per call, so the same
    meeting paid for its participants, its meeting type and its video row up to
    four times, and `kind()` re-read the meeting type three more times on top.

    This changes NOTHING about what gets returned. It changes how many times the
    database is asked for rows the request already has. The brief for a given
    appointment is also memoised, so a meeting that appears in four lists is
    built once and the four lists share it.
    """

    __slots__ = ("participants", "types", "meetings", "_briefs")

    def __init__(self, db: Session, appts):
        self.participants = {}
        self.types = {}
        self.meetings = {}
        self._briefs = {}
        # NO ORDER BY on the participant read below, deliberately. The
        # per-appointment query this replaces has none either, so adding one
        # here - even a sensible one - reorders every participants list in the
        # response. That is a behaviour change, and this refactor is not the
        # place to make one. (Participant order being unspecified at all is a
        # real latent issue in both versions; it predates this change and is
        # noted in the report rather than smuggled in with it.)
        ids = sorted({a.id for a in appts if a is not None})
        type_ids = sorted({a.meeting_type_id for a in appts
                           if a is not None and a.meeting_type_id})
        if ids:
            for p, u in (db.query(AppointmentParticipant, User)
                         .join(User, User.id == AppointmentParticipant.user_id)
                         .filter(AppointmentParticipant.appointment_id.in_(ids))
                         .all()):
                self.participants.setdefault(p.appointment_id, []).append((p, u))
            for m in (db.query(AppointmentMeeting)
                      .filter(AppointmentMeeting.appointment_id.in_(ids)).all()):
                # One row per appointment (unique constraint), so first wins is
                # not a choice being made here — there is only ever one.
                self.meetings.setdefault(m.appointment_id, m)
        if type_ids:
            for mt in (db.query(MeetingType)
                       .filter(MeetingType.id.in_(type_ids)).all()):
                self.types[mt.id] = mt

    def meeting_type(self, a: SalesAppointment):
        return self.types.get(a.meeting_type_id) if a.meeting_type_id else None

    def kind(self, a: SalesAppointment) -> str:
        """The meeting type KEY, or "" — exactly what my_day's local kind() did,
        without a query per appointment per list."""
        mt = self.meeting_type(a)
        return (mt.key or "") if mt else ""


def _appt_brief(db: Session, a: SalesAppointment,
                pre: Optional["ApptPrefetch"] = None) -> dict:
    """The compact appointment shape My Day and the opportunity record render.

    Deliberately lighter than the scheduling router's full serializer — a My Day
    list of twelve meetings should not fan out into dozens of participant
    queries for detail nobody reads on that screen.

    `pre` is an optional ApptPrefetch covering this appointment. With it the
    body below runs on rows already in memory and issues no queries at all;
    without it the original three-query path is untouched, which is what keeps
    every other caller working unchanged.
    """
    if pre is not None and a.id in pre._briefs:
        return pre._briefs[a.id]
    if pre is not None:
        parts = pre.participants.get(a.id, [])
        mt = pre.meeting_type(a)
        video_row = pre.meetings.get(a.id)
    else:
        parts = (db.query(AppointmentParticipant, User)
                 .join(User, User.id == AppointmentParticipant.user_id)
                 .filter(AppointmentParticipant.appointment_id == a.id).all())
        mt = (db.query(MeetingType).filter(MeetingType.id == a.meeting_type_id).first()
              if a.meeting_type_id else None)
        video_row = _apmeet.get_meeting_row(db, a.id)
    out = {
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
        "video": _apmeet.meeting_out(video_row),
        "participants": [{"user_id": u.id, "full_name": u.full_name,
                          "is_required": bool(p.is_required)} for p, u in parts],
    }
    if pre is not None:
        pre._briefs[a.id] = out
    return out


def _event(db: Session, opp: Opportunity, actor: User, event_type: str,
           summary: str, detail: Optional[str] = None) -> OpportunityEvent:
    """Append to the timeline. Never updated, never deleted — corrections are
    new rows, which is what makes the timeline trustworthy."""
    ev = OpportunityEvent(opportunity_id=opp.id, event_type=event_type,
                          summary=summary, detail=detail, actor_user_id=actor.id)
    db.add(ev)
    return ev


def _opportunity_billing(db: Session, opp) -> dict:
    """What this deal has actually been sold on, and what the alternative is.

    Returns the SELECTED option's terms plus every option still available, so a
    screen can render the choice and a contract can state the commitment without
    either of them recomputing prices and disagreeing.
    """
    pkg = (db.query(BrandPackage).filter(BrandPackage.id == opp.selected_package_id).first()
           if opp.selected_package_id else None)
    if pkg is None:
        return {"selected": None, "options": [], "package_id": None,
                "implementation_fee": None}
    # A rate agreed on THIS deal, if there is one. It outranks the catalogue,
    # which is the entire point of a custom package.
    custom = _pp.custom_rate(opp)
    option = _pp.normalize_option(opp.billing_option, pkg, custom)
    # The stored term wins over the catalogue's current one: what the customer
    # agreed to cannot be rewritten by a later catalogue edit.
    selected = _pp.quote(pkg, option, opp,
                         term_months=opp.contract_term_months or None,
                         custom=custom)
    return {
        "package_id": pkg.id,
        "package_name": pkg.name,
        "selected": selected,
        "options": _pp.options_for(pkg, opp, custom),
        "custom_rate": {
            "unit_price": float(custom["unit_price"]),
            "unit_label": custom["unit_label"],
            "min_units": custom["min_units"],
            "term_months": custom["term_months"],
            "monthly_rate": float(custom["monthly_rate"]),
            "basis": _pp.custom_basis(custom),
        } if custom else None,
        # Named at the top level too, because it is the number most often read
        # off this block on its own.
        "implementation_fee": selected["implementation_fee"],
        "implementation_fee_is_one_time": True,
        "implementation_fee_source": selected["implementation_fee_source"],
    }


def _custom_signature(c):
    """What makes two custom rates the same agreement.

    Compared rather than the raw columns, so re-sending an unchanged value does
    not write a timeline row saying the price changed when it did not.
    """
    if not c:
        return None
    return (c["unit_price"], c["unit_label"], c["min_units"], c["term_months"])


def _custom_note(after, before) -> str:
    """The timeline detail for a custom-rate change — what it is now, and what
    it was, because a price change nobody can reconstruct is not an audit."""
    def one(c):
        if not c:
            return "none"
        bits = [_pp.custom_basis(c) or "%s/month" % _pp._plain_money(c["monthly_rate"])]
        if _pp.custom_basis(c):
            bits.append("= %s/month" % _pp._plain_money(c["monthly_rate"]))
        bits.append("%d-month agreement" % c["term_months"] if c["term_months"]
                    else "no term commitment")
        return " · ".join(bits)
    return "Now: %s. Was: %s." % (one(after), one(before))


def _price_note(q: dict) -> str:
    """The one-line money summary that goes on the timeline.

    States the rate, the term and the separate setup fee. A note that said only
    "$500/month" would read identically for a discount and for a 13-month
    commitment, which is the confusion this whole change exists to remove.
    """
    bits = []
    if q.get("implementation_fee") is not None:
        bits.append("implementation $%s one-time"
                    % format(q["implementation_fee"], ",.2f"))
    if q.get("monthly_rate") is None:
        bits.append("no recurring rate configured")
        return " · ".join(bits) or "custom pricing"
    bits.append("$%s/month" % format(q["monthly_rate"], ",.2f"))
    if q.get("term_months"):
        bits.append("%d-month agreement, all %d payments required"
                    % (q["term_months"], q["term_months"]))
        if q.get("savings_per_month"):
            bits.append("saves $%s/month vs month-to-month"
                        % format(q["savings_per_month"], ",.2f"))
        if q.get("total_contract_value") is not None:
            bits.append("total contract value $%s"
                        % format(q["total_contract_value"], ",.2f"))
    else:
        bits.append("month-to-month")
    return " · ".join(bits)


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
    billing_option: Optional[str] = None       # month_to_month | term_agreement
    # Per-deal one-time implementation charge. Send null to fall back to the
    # package's; omit to leave whatever is there alone.
    implementation_fee: Optional[float] = None
    # Per-deal RECURRING rate — manager-only, audited. Send custom_unit_price
    # null to clear the whole agreement; omit any field to leave it alone.
    custom_unit_price: Optional[float] = None
    custom_unit_label: Optional[str] = None
    custom_min_units: Optional[int] = None
    custom_term_months: Optional[int] = None
    # Why the price was agreed (deal desk only, never customer-facing) and what
    # the customer may be told about it. Two fields because they are two
    # different documents — see the Opportunity model.
    pricing_notes_internal: Optional[str] = None
    pricing_description_customer: Optional[str] = None
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
    # THE STRUCTURED ANSWERS. `{field_key: {options, other, note, parts}}`.
    # Sent INSTEAD of the long-form string for any field the seller answered
    # with the structured controls; the server renders it into that field's
    # existing Text column, so a client may still send either shape and the
    # two never disagree about what the column holds.
    structured: Optional[dict] = None
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
    # `price` is kept and still means what it always did - the MONTH-TO-MONTH
    # rate - so no existing consumer of this endpoint silently starts reading a
    # contracted rate. `month_to_month_price` names it explicitly for anything
    # written from here on, and `pricing.options` carries both choices.
    return [dict({
        "id": p.id, "key": p.key, "name": p.name, "description": p.description,
        "price": _money(p.price), "currency": p.currency,
        "billing_period": p.billing_period, "is_custom": bool(p.is_custom),
        "billing_plan_key": p.billing_plan_key,
        "pricing": _pp.package_pricing(p),
    }) for p in rows]


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

    # THE shared rule, not a copy of it — see `demo_is_outstanding`. This line
    # and the manager rollup's used to be two hand-written predicates and they
    # disagreed on screen.
    demos_to_build = [o for o in open_opps if demo_is_outstanding(o)]
    demos_to_build.sort(key=lambda o: (o.demo_due_at or datetime.max))

    month_start = datetime(now.year, now.month, 1)
    won_this_month = (base.filter(Opportunity.status == "won",
                                  Opportunity.won_at >= month_start).all())
    won_value = sum(float(o.deal_value or 0) for o in won_this_month)

    # `base` was already executed above for the open deals. Re-running it here
    # loaded every scoped Opportunity a SECOND time, in full, to read one column
    # off each. Same rows, same filter, id column only.
    scoped_ids = [r[0] for r in base.with_entities(Opportunity.id).all()]
    recent = (db.query(OpportunityEvent)
              .join(Opportunity, Opportunity.id == OpportunityEvent.opportunity_id)
              .filter(Opportunity.id.in_(scoped_ids or [""]))
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

    # EVERY appointment this response can mention, resolved once. todays and
    # upcoming overlap heavily and feed seven output lists between them; before
    # this, each list re-read the same participants, meeting types and video
    # rows from scratch.
    pre = ApptPrefetch(db, list(todays) + list(upcoming))

    discoveries_today = [a for a in todays if "discovery" in pre.kind(a)]
    demos_today = [a for a in todays if "demo" in pre.kind(a)]
    closing_today = [a for a in todays if "closing" in pre.kind(a)]

    appt_map = next_appt_map(db, open_opps)
    # `_card` already accepts a names map precisely so a board does not fire one
    # query per tile to print an owner. My Day was not passing one, so up to 36
    # cards resolved their owner individually.
    names = _name_map(db, [o.owner_user_id for o in open_opps])
    actor_names = _name_map(db, [e.actor_user_id for e in recent])

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
        "follow_ups_due": [_card(o, db, appt_map, names) for o in follow_ups[:12]],
        "deals_needing_action": [_card(o, db, appt_map, names) for o in needs_action[:12]],
        "demos_to_build": [_card(o, db, appt_map, names) for o in demos_to_build[:12]],
        "stage_counts": stage_counts,
        "recent_activity": [{
            "id": e.id, "opportunity_id": e.opportunity_id,
            "event_type": e.event_type, "summary": e.summary,
            "detail": e.detail, "occurred_at": e.occurred_at,
            "actor_name": actor_names.get(e.actor_user_id),
        } for e in recent],
        # REAL appointment data. An empty list here now means "no meetings",
        # which it did not mean in Checkpoint 1.
        "todays_appointments": [_appt_brief(db, a, pre) for a in todays],
        "next_appointment": _appt_brief(db, upcoming[0], pre) if upcoming else None,
        "needs_confirmation": [_appt_brief(db, a, pre) for a in unconfirmed[:12]],
        "discoveries_today": [_appt_brief(db, a, pre) for a in discoveries_today],
        "demos_today": [_appt_brief(db, a, pre) for a in demos_today],
        "upcoming_appointments": [_appt_brief(db, a, pre) for a in upcoming[:12]],
        # ── Proposal work (Checkpoint 4) ────────────────────────────────────
        # Six queues, each with an action and a reason. Not a proposal report:
        # a rep opening My Day is deciding what to touch next, and a number
        # they cannot act on competes with the ones they can.
        "proposals": _pwq.proposal_queues(db, open_opps, now=now),
        # A closing call today is the highest-stakes thing on the calendar, so
        # it is surfaced separately rather than buried in the day's list.
        "closing_today": [_appt_brief(db, a, pre) for a in closing_today],
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
    # The board resolved an owner name per tile. A manager's brand-wide board is
    # a hundred tiles, and `_card` already takes a names map for exactly this.
    card_names = _name_map(db, [o.owner_user_id for o in rows])
    cards = [_card(o, db, appts, card_names) for o in rows]

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
        # Who is asking. My Pipeline needs it to show a MANAGER only their own
        # deals without a second round trip, and the stored login profile in the
        # browser carries no user id. Not sensitive - it is the caller's own id,
        # which they already hold in their token.
        "viewer_user_id": user.id,
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
    # The structured side of the same answers, plus whatever long-form text was
    # captured before structured discovery existed. Both are sent every time:
    # the panel draws the controls from `structured` and shows `legacy` under a
    # collapsed "Previous / detailed notes", so an old deal loses nothing.
    _disc_state = _discovery.load(getattr(disc, "structured_json", None) if disc else None)
    discovery["structured"] = _disc_state["fields"]
    discovery["legacy"] = _disc_state["legacy"]
    discovery["progress"] = _discovery.progress(
        _disc_state["fields"], {k: discovery.get(k) for k, _ in DiscoveryRecord.FIELDS})

    events = (db.query(OpportunityEvent)
              .filter(OpportunityEvent.opportunity_id == opp.id)
              .order_by(OpportunityEvent.occurred_at.desc()).all())

    def pkg_out(pid):
        p = db.query(BrandPackage).filter(BrandPackage.id == pid).first() if pid else None
        # `price` remains the MONTH-TO-MONTH rate. `pricing` carries both rates
        # plus the selectable options, so the screen never has to infer either.
        return {"id": p.id, "name": p.name, "key": p.key,
                "price": _money(p.price), "is_custom": bool(p.is_custom),
                "pricing": _pp.package_pricing(p)} if p else None

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
        # THE AGREEMENT TERMS THIS DEAL IS ON, stated rather than implied.
        # Everything downstream - proposal, contract, checkout - reads this
        # rather than re-deriving it and risking a different answer.
        "billing": _opportunity_billing(db, opp),
        # ── Pricing authority, so the screen knows what it may offer ────────
        # Sent as a resolved verdict rather than as raw ceilings the browser
        # would have to apply itself. A UI that computes its own floor is a
        # second implementation of the rule, and the two drift.
        "pricing_authority": _authority.ceilings_for(
            db, opp.brand_sales_org_id,
            _authority.actor_role(user, db, opp.brand_sales_org_id)),
        "pricing_notes_internal": opp.pricing_notes_internal,
        "pricing_description_customer": opp.pricing_description_customer,
        "pending_pricing_approval": (
            lambda r: {"id": r.id, "requested_at": r.requested_at,
                       "reason": r.reason,
                       "requested_by_name": _user_name(db, r.requested_by)}
            if r else None)(_pricing_approvals.open_request_for_opportunity(db, opp.id)),

        # ── Projected compensation on THIS deal ─────────────────────────────
        # Gated: a rep sees nothing here unless they hold sales_comp_view, and
        # what the company pays the layers above them is not theirs to read.
        # None means "not permitted to see", which the screen renders as absent
        # rather than as zero.
        "compensation": (_comp.compute_public(db, opp)
                         if _may_see_compensation(user, db, opp.brand_sales_org_id)
                         else None),

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
        # The controls the seller gets, served rather than hardcoded in the
        # browser — the same rule that makes `/sales/me` send the stage list.
        "discovery_schema": _discovery.schema_payload(),
        # ── WHO MAY DRIVE THE DEMO BUILD ────────────────────────────────────
        # A salesperson sees the demo's OUTCOME: status, who is building it,
        # when it is due, the link. The operational controls behind it — the
        # requirements work order, the internal build notes, reassigning the
        # builder, declaring it ready — belong to whoever is actually building
        # it and to the manager who answers for it.
        #
        # The screen hides those for everybody else, and this is the server's
        # own answer rather than the browser's guess. PATCH is still gated by
        # `assert_can_edit_opportunity` regardless of what this says, so a
        # hidden control is a courtesy, not the access control.
        "can_manage_demo": bool(
            is_sales_manager(user, db, opp.brand_sales_org_id)
            or (opp.demo_owner_user_id and opp.demo_owner_user_id == user.id)),
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

    # ── the per-deal custom rate ────────────────────────────────────────────
    # Applied BEFORE the package block below, because the billing option and
    # the term are resolved against it: a custom agreement carries its own term,
    # and resolving the option first would refuse the very commitment being set.
    #
    # WAS MANAGER-ONLY. IT IS NOW FLOOR-BOUNDED, WHICH IS A DIFFERENT RULE
    # RATHER THAN A LOOSER ONE.
    #
    # A rep may negotiate inside the discount ceiling their brand's pricing
    # policy grants them; past it the deal is not refused and is not silently
    # written either - it is captured as a request a manager decides on. The
    # old rule sent the rep to Slack and lost the negotiated figure; this keeps
    # the figure and routes the question.
    #
    # With no policy configured `pricing_authority` falls back to "a rep
    # discounts nothing", so on a brand nobody has configured this behaves
    # exactly as the manager-only rule did.
    _CUSTOM = ("custom_unit_price", "custom_unit_label",
               "custom_min_units", "custom_term_months")
    _PRICING_TEXT = ("pricing_notes_internal", "pricing_description_customer")
    if any(f in data for f in _CUSTOM):
        before = _pp.custom_rate(opp)

        # What the caller is proposing, resolved the same way the engine would
        # resolve it once written - so the judgement is made against the deal
        # that would exist, not against the fields in isolation.
        _unit = data.get("custom_unit_price", opp.custom_unit_price)
        _units = data.get("custom_min_units", opp.custom_min_units) or 1
        _proposed_monthly = None
        if _unit is not None:
            try:
                _proposed_monthly = Decimal(str(_unit)) * Decimal(int(_units))
            except (InvalidOperation, ValueError, TypeError):
                raise HTTPException(status_code=400,
                                    detail="Custom unit price is not a number.")
        _proposed_term = data.get("custom_term_months", opp.custom_term_months)
        _pkg_for_floor = (db.query(BrandPackage)
                          .filter(BrandPackage.id == opp.selected_package_id).first()
                          if opp.selected_package_id else None)
        _option = data.get("billing_option", opp.billing_option)

        verdict = _authority.evaluate(
            db, user, _pkg_for_floor,
            brand_sales_org_id=opp.brand_sales_org_id,
            billing_option=_option,
            proposed_monthly=_proposed_monthly,
            proposed_term_months=_proposed_term)

        if verdict["outcome"] == _authority.REFUSED:
            # A policy configured as a HARD floor. No request is created,
            # because there is nothing to ask: this brand has said the floor is
            # the floor. 403 rather than 409 — the difference matters to the
            # screen, which offers "send for approval" on one and not the other.
            raise HTTPException(
                status_code=403,
                detail={"error": "pricing_below_floor",
                        "message": verdict["summary"],
                        "breaches": verdict["breaches"],
                        "ceilings": verdict["ceilings"]})

        if verdict["outcome"] == _authority.NEEDS_APPROVAL:
            # NOTHING IS WRITTEN TO THE DEAL. The proposed economics live on the
            # request until a manager decides, so the pipeline, the proposal and
            # the compensation projection all keep describing the deal as it
            # actually stands rather than as somebody hopes it will.
            req = _pricing_approvals.request_custom_deal(
                db, opp, user,
                proposed_unit_price=_unit,
                proposed_unit_label=data.get("custom_unit_label",
                                             opp.custom_unit_label),
                proposed_min_units=_units,
                proposed_term_months=_proposed_term,
                proposed_billing_option=_option,
                proposed_implementation_fee=data.get("implementation_fee",
                                                     opp.implementation_fee),
                reason=(data.get("pricing_notes_internal") or "").strip()
                       or "Negotiated pricing below the approved floor.",
                verdict=verdict)
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "pricing_approval_required",
                    "message": verdict["summary"],
                    "request_id": req["id"],
                    "breaches": verdict["breaches"],
                    "ceilings": verdict["ceilings"],
                })

        for f in _CUSTOM:
            if f in data:
                v = data[f]
                if isinstance(v, str):
                    v = v.strip() or None
                setattr(opp, f, v)
        after = _pp.custom_rate(opp)
        if _custom_signature(before) != _custom_signature(after):
            _event(db, opp, user, "custom_rate_set",
                   "Custom rate set" if after else "Custom rate cleared",
                   _custom_note(after, before))

    # The two pricing narratives. `pricing_notes_internal` is deal-desk only and
    # follows `demo_notes`: it has no code path into a proposal, an email or the
    # portal. `pricing_description_customer` is the sentence that MAY be shown.
    for f in _PRICING_TEXT:
        if f in data:
            v = data[f]
            setattr(opp, f, (v.strip() or None) if isinstance(v, str) else v)

    # ── packages and deal value ─────────────────────────────────────────────
    platform_id = org.platform_id if org else None
    if "package_interest_id" in data:
        pkg = _package(db, data["package_interest_id"], platform_id)
        opp.package_interest_id = pkg.id if pkg else None

    # THE BILLING OPTION IS PART OF THE PRICE, so it is resolved before the
    # value is derived. A package selected without one lands on month-to-month:
    # that is the package's normal rate, and defaulting to the contracted rate
    # would put a customer on a 13-month commitment nobody chose.
    selecting_package = "selected_package_id" in data
    if selecting_package or "billing_option" in data:
        pkg = (_package(db, data["selected_package_id"], platform_id)
               if selecting_package
               else (db.query(BrandPackage)
                       .filter(BrandPackage.id == opp.selected_package_id).first()
                     if opp.selected_package_id else None))
        if selecting_package:
            opp.selected_package_id = pkg.id if pkg else None

        requested = data.get("billing_option",
                             opp.billing_option or _pp.DEFAULT_BILLING_OPTION)
        # The deal's own rate, which supplies a term the catalogue does not have.
        _custom = _pp.custom_rate(opp)
        # Refuse rather than guess: asking for a term agreement on a package
        # that has no contracted rate is answered, not silently downgraded.
        if (requested == _pp.BILLING_TERM_AGREEMENT and pkg is not None
                and not _pp.has_term_option(pkg, _custom)):
            raise HTTPException(
                status_code=400,
                detail="%s has no term-agreement rate. Set a custom rate with a "
                       "term on this deal, or use month-to-month." % pkg.name)
        option = _pp.normalize_option(requested, pkg, _custom)

        prev_option = opp.billing_option
        opp.billing_option = option if pkg is not None else None
        # A custom agreement's term is the one that was negotiated; only fall
        # back to the catalogue's when the deal has not stated its own.
        opp.contract_term_months = (
            (_custom["term_months"] if _custom and _custom.get("term_months")
             else _pp.term_months_for(pkg))
            if pkg is not None and option == _pp.BILLING_TERM_AGREEMENT
            else None)

        # Decision #9 — value DERIVES from the package unless explicitly
        # overridden. Selecting a package never silently clobbers an override.
        #
        # DELIBERATELY UNCHANGED BY THE BILLING OPTION. `deal_value` has always
        # meant "the package's `price`", every historical figure and every
        # report was built on that, and re-pointing it at a monthly rate would
        # silently restate the whole pipeline. The option-aware commercial
        # numbers - MRR, recurring contract value, total contract value - are
        # exposed beside it instead, where they can be read without rewriting
        # what the old field meant.
        if pkg and not opp.deal_value_override:
            opp.deal_value = pkg.price

        if pkg and selecting_package:
            q = _pp.quote(pkg, option, opp, custom=_custom)
            _event(db, opp, user, "package_selected",
                   "Package selected: %s (%s)" % (pkg.name, q["billing_option_label"]),
                   _price_note(q))
        elif pkg and option != prev_option:
            q = _pp.quote(pkg, option, opp, custom=_custom)
            _event(db, opp, user, "billing_option_changed",
                   "Billing option: %s" % q["billing_option_label"],
                   _price_note(q))

    # A per-deal setup figure, quoted for this customer and nobody else. Kept
    # off the catalogue on purpose: editing the package would move the number
    # for every deal that has ever referenced it.
    if "implementation_fee" in data:
        fee = data["implementation_fee"]
        if fee is not None and float(fee) < 0:
            raise HTTPException(status_code=400,
                                detail="The implementation fee cannot be negative.")
        before_fee = opp.implementation_fee
        opp.implementation_fee = Decimal(str(fee)) if fee is not None else None
        if before_fee != opp.implementation_fee:
            _event(db, opp, user, "implementation_fee_set",
                   ("Implementation fee: $%s one-time" % format(float(fee), ",.2f"))
                   if fee is not None
                   else "Implementation fee reset to the package's",
                   "Quoted for this deal. The package catalogue is unchanged.")

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
    # The identity of the deal — who this is and how to reach them. Corrected
    # from the RECORD card, and recorded on the timeline like every other write
    # on this page: a phone number that changed, or a company name that was
    # typed wrong at intake, is a fact about the deal and belongs in its history.
    _IDENTITY = ("company_name", "contact_name", "phone", "email",
                 "website", "industry", "timezone")
    _IDENTITY_LABELS = {
        "company_name": "Company", "contact_name": "Contact",
        "phone": "Phone", "email": "Email", "website": "Website",
        "industry": "Industry", "timezone": "Timezone",
    }
    identity_changes = []
    for f in _IDENTITY + ("source", "loss_reason"):
        if f in data:
            v = data[f]
            if isinstance(v, str):
                v = v.strip() or None
                if f == "email" and v:
                    v = v.lower()
            before = getattr(opp, f)
            setattr(opp, f, v)
            if f in _IDENTITY and before != v:
                identity_changes.append((f, before, v))

    if identity_changes:
        _event(db, opp, user, "record_updated",
               "Record updated: %s" % ", ".join(
                   _IDENTITY_LABELS[f] for f, _b, _a in identity_changes),
               " · ".join("%s: %s → %s" % (_IDENTITY_LABELS[f],
                                           b or "(blank)", a or "(blank)")
                          for f, b, a in identity_changes))

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

    # ── the structured answers ──────────────────────────────────────────────
    # Sanitised against the schema (a browser does not get to invent option
    # values), merged into the side-car, and RENDERED into the same Text
    # columns the long-form form always wrote. Nothing downstream — the demo
    # requirements carry-forward, provisioning's discovery summary, proposals —
    # learns that anything changed, because from their side nothing did.
    #
    # Whatever prose was in a column before its first structured answer is
    # snapshotted into `legacy` by `apply`, so a rep's earlier long-form note
    # is preserved rather than replaced.
    if "structured" in data and data["structured"] is not None:
        incoming = _discovery.sanitize(data["structured"])
        if incoming:
            state = _discovery.load(getattr(disc, "structured_json", None))
            current_text = {k: getattr(disc, k, None)
                            for k, _ in DiscoveryRecord.FIELDS}
            state = _discovery.apply(state, incoming, current_text)
            disc.structured_json = _discovery.dump(state)
            for key in incoming:
                # Only fields `apply` actually accepted — an empty answer to an
                # untouched question is ignored there, and must not null the
                # column here either.
                if key not in state["fields"]:
                    continue
                rendered = _discovery.render(key, state["fields"][key])
                setattr(disc, key, rendered or None)

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


# ══ DEMOS TO BUILD — the work queue, not the count ══════════════════════════
#
# WHAT WAS WRONG, AND IT WAS NOT A UI BUG.
#
# "Demos to build — 3" and "Mike Simmons — 2 to build" on /sales/proposals are
# assembled from `manager_workspace`'s per-rep rollup, which carries ONE
# INTEGER per rep. There was no endpoint anywhere that returned the individual
# demo jobs for a brand, so the queue could not have listed them however the
# screen was written. Discovery → Request Demo → count goes up → dead end.
#
# So this is the missing read: the same opportunities `my-day` already counts,
# returned as JOBS. It introduces no new model, no new status vocabulary and no
# second notion of what a demo is — every field below is a column that already
# existed on Opportunity, and the membership rule is `_scoped_opportunities`,
# unchanged, so a rep sees their book and a manager sees the brand.
#
# NO NEW PRIORITY CONCEPT. The ordering is the one the platform already uses
# for this list in `my-day` — target date, soonest first — and `_attention`
# supplies the one reason a job is shouting, exactly as it does on every card.

# The membership rule lives in the model as `demo_is_outstanding`, so the
# queue, My Day and the manager rollup all ask the same question. There is
# deliberately no local copy of it here.

_DEMO_STATUS_LABELS = {
    DEMO_NOT_REQUESTED: "Not requested",
    DEMO_REQUESTED: "Requested",
    DEMO_IN_PROGRESS: "In progress",
    DEMO_READY: "Ready",
    DEMO_DELIVERED: "Delivered",
}


def _demo_job(opp: Opportunity, names: dict, disc_progress: dict,
              now: datetime) -> dict:
    """One demo build, as the person who has to build it needs to see it."""
    status = opp.demo_status or (DEMO_REQUESTED if opp.stage == STAGE_DEMO_BUILD
                                 else DEMO_NOT_REQUESTED)
    prog = disc_progress.get(opp.id) or {"answered": 0, "required": 0,
                                         "complete": False}
    return {
        "opportunity_id": opp.id,
        "company_name": opp.company_name,
        "contact_name": opp.contact_name,
        "email": opp.email,
        "phone": opp.phone,
        "industry": opp.industry,
        "stage": opp.stage,
        "stage_label": STAGE_LABELS.get(opp.stage, opp.stage),

        # The seller who owns the relationship, and the builder who owns the
        # work. Two different people on purpose — see `assign_owner` in
        # implementation_service for the same distinction after the sale.
        "sales_owner_user_id": opp.owner_user_id,
        "sales_owner_name": names.get(opp.owner_user_id),
        "builder_user_id": opp.demo_owner_user_id,
        "builder_name": names.get(opp.demo_owner_user_id),

        "demo_status": status,
        "demo_status_label": _DEMO_STATUS_LABELS.get(status, status),
        "requested_at": opp.demo_requested_at,
        "due_at": opp.demo_due_at,
        "ready_at": opp.demo_ready_at,
        "overdue": bool(opp.demo_due_at and opp.demo_due_at < now
                        and status not in (DEMO_READY, DEMO_DELIVERED)),
        "demo_url": opp.demo_url,

        # THE HANDOFF, ANSWERED HERE SO THE QUEUE CAN SAY IT.
        # A job whose discovery is incomplete is a job that will bounce back,
        # and the builder should see that before opening it rather than after.
        "requirements_captured": bool((opp.demo_requirements or "").strip()),
        "discovery_answered": prog.get("answered", 0),
        "discovery_required": prog.get("required", 0),
        "discovery_complete": bool(prog.get("complete")),
        "discovery_completed_at": opp.discovery_completed_at,

        "attention": _attention(opp),
        "last_activity_at": opp.stage_changed_at or opp.updated_at or opp.created_at,
    }


@router.get("/demo-queue")
def demo_queue(brand_sales_org_id: Optional[str] = Query(None),
               builder: Optional[str] = Query(
                   None, description="a user id, 'me', or 'unassigned'"),
               include_done: bool = Query(False),
               user: User = Depends(require_sales_member),
               db: Session = Depends(get_db)):
    """Every demo waiting to be built, as individual openable jobs.

    `include_done` adds the ones already Ready or Delivered, so somebody can
    find the demo they published last week without leaving the queue. They are
    NOT counted in `summary.total`, which stays the number of outstanding
    builds — the figure the screen has always shown.
    """
    org = _resolve_context(user, db, brand_sales_org_id)
    now = datetime.utcnow()

    base = _scoped_opportunities(user, db, org)
    # Open deals only. A demo on a lost deal is not work; a demo on a won deal
    # already did its job.
    opps = base.filter(Opportunity.status == "open").all()

    outstanding = [o for o in opps if demo_is_outstanding(o)]
    outstanding_ids = {o.id for o in outstanding}
    done = [o for o in opps
            if o.id not in outstanding_ids
            and o.demo_status in (DEMO_READY, DEMO_DELIVERED)]

    # Ordering: the platform's existing one for this list. Soonest target
    # first, then oldest request — a job with no date sorts last rather than
    # first, because "no target" is not "due now".
    outstanding.sort(key=lambda o: (o.demo_due_at or datetime.max,
                                    o.demo_requested_at or datetime.max))
    done.sort(key=lambda o: (o.demo_ready_at or datetime.min), reverse=True)

    shown = outstanding + (done if include_done else [])

    if builder == "unassigned":
        shown = [o for o in shown if not o.demo_owner_user_id]
    elif builder == "me":
        shown = [o for o in shown if o.demo_owner_user_id == user.id]
    elif builder:
        shown = [o for o in shown if o.demo_owner_user_id == builder]

    # Names and discovery progress for the whole page, in two queries rather
    # than two per row.
    names = _name_map(db, [o.owner_user_id for o in shown]
                      + [o.demo_owner_user_id for o in shown])
    disc_progress = {}
    ids = [o.id for o in shown]
    if ids:
        for d in (db.query(DiscoveryRecord)
                  .filter(DiscoveryRecord.opportunity_id.in_(ids)).all()):
            state = _discovery.load(getattr(d, "structured_json", None))
            legacy = {k: getattr(d, k, None) for k, _ in DiscoveryRecord.FIELDS}
            disc_progress[d.opportunity_id] = _discovery.progress(
                state["fields"], legacy)

    # WHO IS CARRYING WHAT. The same per-builder split /sales/proposals shows
    # as "<name> — N to build", derived from these rows rather than from a
    # second aggregate that could disagree with them.
    by_builder = {}
    for o in outstanding:
        key = o.demo_owner_user_id or ""
        by_builder.setdefault(key, 0)
        by_builder[key] += 1
    builder_names = _name_map(db, [k for k in by_builder if k])

    return {
        "brand_sales_org": {"id": org.id, "name": org.name},
        "summary": {
            "total": len(outstanding),
            "unassigned": sum(1 for o in outstanding if not o.demo_owner_user_id),
            "overdue": sum(1 for o in outstanding
                           if o.demo_due_at and o.demo_due_at < now),
            "no_target": sum(1 for o in outstanding if not o.demo_due_at),
            "mine": sum(1 for o in outstanding
                        if o.demo_owner_user_id == user.id),
            "by_builder": sorted(
                [{"user_id": k or None,
                  "name": builder_names.get(k) if k else "Unassigned",
                  "count": v} for k, v in by_builder.items()],
                key=lambda r: (-r["count"], r["name"] or "")),
        },
        "jobs": [_demo_job(o, names, disc_progress, now) for o in shown],
        "can_manage": is_sales_manager(user, db, org.id),
    }


@router.get("/opportunities/{opp_id}/demo-build")
def demo_build_workspace(opp_id: str,
                         user: User = Depends(require_sales_member),
                         db: Session = Depends(get_db)):
    """One demo build, with discovery already turned into a brief.

    ===================================================================
    THE BUILDER DOES NOT RE-ASK WHAT THE SELLER ALREADY ASKED
    ===================================================================

    Lead sources, current outreach, tools, follow-up process, pain, goals,
    appointment handling, automation requirements, integrations and the demo
    requirements are all captured in discovery, on the call, by the person who
    was in the room. Asking for them again on a build form is asking the
    company to collect the same facts twice and then reconcile two answers.

    So `handoff` below is discovery RENDERED — `discovery_schema.render()`,
    which already exists and is already how a structured answer becomes a
    sentence. It runs on the SERVER for the same reason the intake schema does:
    the vocabulary must not be reimplemented in a browser where it can drift
    from the one the seller answered against.

    Legacy long-form text (deals captured before structured discovery) is
    included under the same labels, so an old deal hands off as completely as
    a new one rather than appearing blank.

    This endpoint adds no field to any model and no second notion of what a
    demo is. It is a READ, assembled from `Opportunity.demo_*` and
    `DiscoveryRecord`, both of which already existed.
    """
    opp = _load(opp_id, user, db)
    org = db.query(BrandSalesOrg).filter(
        BrandSalesOrg.id == opp.brand_sales_org_id).first()

    disc = db.query(DiscoveryRecord).filter(
        DiscoveryRecord.opportunity_id == opp.id).first()
    state = _discovery.load(getattr(disc, "structured_json", None) if disc else None)
    legacy_text = {k: (getattr(disc, k, None) if disc else None)
                   for k, _ in DiscoveryRecord.FIELDS}
    field_labels = dict(DiscoveryRecord.FIELDS)

    handoff: List[Dict[str, Any]] = []
    seen = set()
    for spec in _discovery.schema_payload():
        key = spec["key"]
        seen.add(key)
        rendered = _discovery.render(key, (state["fields"] or {}).get(key))
        text = (legacy_text.get(key) or "").strip() if legacy_text.get(key) else ""
        # Both, when both genuinely differ: the structured answer is the
        # checklist and the long-form note is what the seller actually heard.
        # Dropping either one loses something the builder needs.
        #
        # NOT BOTH WHEN THEY ARE THE SAME ANSWER. The discovery save path
        # writes the rendered text into the legacy column as well, so joining
        # the two unconditionally printed every answer on this screen twice —
        # which reads as a rendering fault and buries a long brief in its own
        # duplicate. Verified against a live deal: nine questions, eighteen
        # paragraphs.
        parts = []
        if rendered:
            parts.append(rendered)
        if text and text not in (rendered or ""):
            parts.append(text)
        value = "\n".join(parts) or None
        handoff.append({
            "key": key,
            "label": spec.get("label") or field_labels.get(key, key),
            "group": spec.get("group"),
            "required": bool(spec.get("required")),
            "value": value,
        })
    # Anything discovery stored that the current schema no longer asks about.
    # Kept rather than hidden — a question retired last month is still the best
    # information this deal has.
    for key, label in DiscoveryRecord.FIELDS:
        if key in seen:
            continue
        text = (legacy_text.get(key) or "").strip() if legacy_text.get(key) else ""
        if text:
            handoff.append({"key": key, "label": label, "group": "Earlier notes",
                            "required": False, "value": text})

    progress = _discovery.progress(state["fields"], legacy_text)

    team = []
    if org is not None:
        rows = (db.query(User, Membership)
                .join(Membership, Membership.user_id == User.id)
                .filter(Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                        Membership.scope_id == org.id,
                        Membership.is_active.is_(True),
                        User.is_active.is_(True))
                .order_by(User.full_name.asc()).all())
        team = [{"id": u.id, "full_name": u.full_name, "role": m.role}
                for u, m in rows]

    status = opp.demo_status or (DEMO_REQUESTED if opp.stage == STAGE_DEMO_BUILD
                                 else DEMO_NOT_REQUESTED)
    return {
        "opportunity": {
            "id": opp.id,
            "company_name": opp.company_name,
            "contact_name": opp.contact_name,
            "email": opp.email,
            "phone": opp.phone,
            "website": opp.website,
            "industry": opp.industry,
            "timezone": opp.timezone,
            "stage": opp.stage,
            "stage_label": STAGE_LABELS.get(opp.stage, opp.stage),
            "status": opp.status,
            "owner_user_id": opp.owner_user_id,
            "owner_name": _user_name(db, opp.owner_user_id),
            "brand_sales_org_id": opp.brand_sales_org_id,
            "brand_name": org.name if org else None,
        },
        "demo": {
            "status": status,
            "status_label": _DEMO_STATUS_LABELS.get(status, status),
            "owner_user_id": opp.demo_owner_user_id,
            "owner_name": _user_name(db, opp.demo_owner_user_id),
            "requested_at": opp.demo_requested_at,
            "due_at": opp.demo_due_at,
            "ready_at": opp.demo_ready_at,
            "requirements": opp.demo_requirements,
            "url": opp.demo_url,
            "notes": opp.demo_notes,
            "overdue": bool(opp.demo_due_at
                            and opp.demo_due_at < datetime.utcnow()
                            and status not in (DEMO_READY, DEMO_DELIVERED)),
        },
        "discovery": {
            "completed_at": disc.completed_at if disc else None,
            "completed_by_name": _user_name(db, disc.completed_by) if disc else None,
            "progress": progress,
            "handoff": handoff,
        },
        # The same server-side answer the deal screen uses, so a control is
        # hidden in exactly the same circumstances on both. PATCH remains
        # gated by `assert_can_edit_opportunity` either way.
        "can_manage_demo": bool(
            is_sales_manager(user, db, opp.brand_sales_org_id)
            or (opp.demo_owner_user_id and opp.demo_owner_user_id == user.id)),
        "can_reassign": is_sales_manager(user, db, opp.brand_sales_org_id),
        "team": team,
        "statuses": [{"value": s, "label": _DEMO_STATUS_LABELS.get(s, s)}
                     for s in DEMO_STATUSES],
    }


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


# ══ post-Won visibility (Checkpoint 6 §15 / §16) ════════════════════════════
#
# The salesperson does not vanish at Won. They get a PROJECTION of the
# implementation - status, owner, target date, blocked yes/no, percent - and
# nothing else. What they explicitly do not get: the customer's leads, the
# customer's users, the customer's communications, the internal implementation
# notes, or the blocker's text. `implementation_service.sales_projection` is the
# whitelist, assembled server-side, and these routes never return the ORM object.

@router.get("/implementations")
def my_implementations(user: User = Depends(require_sales_member),
                       db: Session = Depends(get_db)):
    """Post-Won status for every deal this user is entitled to see.

    A rep sees the deals they sold. A manager sees every implementation in their
    own brand sales orgs - and only theirs, so a manager of one brand probing
    another brand's ids gets nothing back rather than a permission message that
    confirms the id exists.
    """
    from app.models.implementation_models import Implementation
    from app.services.implementation_service import (
        sales_projection, SalesProjPrefetch)
    from app.services.sales_access import sales_org_ids, is_sales_manager, is_god

    q = db.query(Implementation)
    manager_orgs = []
    if not is_god(user):
        allowed = sales_org_ids(user, db)
        if not allowed:
            return {"implementations": [], "is_manager": False, "total": 0,
                    "brand_sales_org": None}
        manager_orgs = [o for o in allowed if is_sales_manager(user, db, o)]
        if manager_orgs:
            q = q.filter(or_(Implementation.brand_sales_org_id.in_(manager_orgs),
                             Implementation.sold_by_user_id == user.id))
        else:
            q = q.filter(Implementation.sold_by_user_id == user.id)
    rows = q.order_by(Implementation.created_at.desc()).limit(500).all()

    # Wrapped rather than returned bare so the screen can tell a manager's team
    # view from a rep's own without a second call to /sales/me. The scoping above
    # is unchanged - this only reports which of its two branches ran.
    org = _resolve_context(user, db, None) if sales_org_ids(user, db) else None
    _impl_pre = SalesProjPrefetch(db, rows)
    return {
        "implementations": [sales_projection(db, i, _impl_pre) for i in rows],
        "is_manager": bool(manager_orgs) or is_god(user),
        "total": len(rows),
        "brand_sales_org": {"id": org.id, "name": org.name} if org else None,
    }


@router.get("/opportunities/{opportunity_id}/implementation")
def opportunity_implementation(opportunity_id: str,
                               user: User = Depends(require_sales_member),
                               db: Session = Depends(get_db)):
    """Where the customer got to, for one deal.

    Authorised through the SAME `assert_can_view_opportunity` that guards every
    other view of this record, so post-Won visibility can never be wider than
    pre-Won visibility was.
    """
    from app.models.implementation_models import Implementation
    from app.services.implementation_service import sales_projection

    opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    assert_can_view_opportunity(user, opp, db)

    impl = (db.query(Implementation)
              .filter(Implementation.opportunity_id == opp.id).first())
    if impl is None:
        return {"provisioned": False,
                "is_won": opp.status == "won",
                "message": ("Won — awaiting provisioning." if opp.status == "won"
                            else "Not won yet.")}
    return {"provisioned": True, **sales_projection(db, impl)}
