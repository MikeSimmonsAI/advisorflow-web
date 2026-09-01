import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
from sqlalchemy import func, distinct, or_
from pydantic import BaseModel, EmailStr, Field
import secrets
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

from app.deps import (
    get_db, require_admin, require_super_admin, require_god,
    get_platform_org_ids, load_org_in_scope, load_user_in_scope,
    platform_ids_in_scope, ELEVATED_ROLES,
)
from app.models.models import User, Lead, Message, Reply, LeadOutcome, ReplyClassification, CadenceState, ContactRegistry, Organization, TierDefinition
from app.services.auth_service import hash_password
from app.services import staff_activation as _activation
from app.models.staff_models import PURPOSE_SETUP as _PURPOSE_SETUP, PURPOSE_RESET as _PURPOSE_RESET
from app.services.dedup_service import normalize_phone, normalize_last_name
from app.routers.audit_log_router import log_action
# Refuses a write the owner has not given a destination for. See the
# create_user comment below.
from app.services.platform_owner import tenant_write_org_id as _tenant_write_org_id
from app.services import lead_scope

# ── Industry-specific tier presets ────────────────────────────────────────────
# Each entry: (tier_key, tier_label, track_key, track_label, ai_tone_context, sort_order)
INDUSTRY_TIERS = {
    "funeral": [
        ("pre_need", "Pre-Need", "pre_need_lock_price", "Pre-Need Lock Price",
         "Lead is planning ahead for end-of-life arrangements. Focus on price lock benefits and peace of mind.", 1),
        ("at_need", "At-Need", "at_need_support", "At-Need Support",
         "Lead has an immediate need due to a recent passing. Respond with urgency and compassion.", 2),
        ("aftercare", "Aftercare", "aftercare_follow_up", "Aftercare Follow-Up",
         "Lead is a past family we served. Reconnect with grief support resources and future planning.", 3),
        ("pre_need_general", "Pre-Need General", "pre_need_general", "Pre-Need General",
         "General pre-planning lead without specific urgency. Educate on the benefits of pre-arrangement.", 4),
    ],
    "fiber": [
        ("hot_mover", "Hot Mover", "high_urgency", "High Urgency",
         "Lead is moving soon and needs new internet service immediately. Emphasize fast installation and no contracts.", 1),
        ("interest_confirmed", "Interest Confirmed", "warm_follow_up", "Warm Follow-Up",
         "Lead expressed clear interest in switching providers. Ready to discuss speed, pricing, and promotions.", 2),
        ("comparison_shopping", "Comparison Shopping", "competitive_pitch", "Competitive Pitch",
         "Lead is comparing multiple providers. Focus on speed, reliability, and price differentiators vs the competition.", 3),
        ("needs_follow_up", "Needs Follow-Up", "gentle_follow_up", "Gentle Follow-Up",
         "Lead requested a callback or needs more time. Keep the door open without pressure.", 4),
    ],
    "roofing": [
        ("storm_damage", "Storm Damage", "urgent_inspection", "Urgent Inspection",
         "Lead has recent storm or hail damage. Urgent inspection needed before insurance claim deadlines.", 1),
        ("inspection_requested", "Inspection Requested", "inspection_scheduled", "Inspection Scheduled",
         "Lead agreed to a free roof inspection. Confirm the appointment and prepare the team.", 2),
        ("quote_ready", "Quote Ready", "conversion", "Conversion Ready",
         "Lead has had an inspection and is ready for a formal quote. Focus on value and financing options.", 3),
        ("long_term_plan", "Long-Term Plan", "nurture", "Long-Term Nurture",
         "Lead is planning a roof replacement in 6-12 months. Keep warm with occasional check-ins.", 4),
    ],
    "insurance": [
        ("hot_lead", "Hot Lead", "immediate_outreach", "Immediate Outreach",
         "Lead requested a quote or callback. Follow up immediately while interest is high.", 1),
        ("warm_prospect", "Warm Prospect", "soft_pitch", "Soft Pitch",
         "Lead is generally interested in coverage. Guide them through options without pressure.", 2),
        ("needs_review", "Needs Review", "needs_review", "Needs Review",
         "Lead data needs verification or tier assignment before outreach can begin.", 3),
        ("annual_review", "Annual Review", "annual_check_in", "Annual Check-In",
         "Existing client due for annual policy review. Reconnect and assess coverage adequacy.", 4),
    ],
    "health_insurance": [
        ("hot_lead", "Hot Lead", "immediate_outreach", "Immediate Outreach",
         "Lead requested a health insurance quote or has an urgent coverage need. Contact immediately.", 1),
        ("open_enrollment", "Open Enrollment", "enrollment_window", "Enrollment Window",
         "Lead is in or approaching open enrollment. Guide them through plan options and deadlines.", 2),
        ("life_event", "Life Event", "life_event_outreach", "Life Event Outreach",
         "Lead experienced a qualifying life event (job change, marriage, baby). Help them update coverage.", 3),
        ("annual_review", "Annual Review", "annual_check_in", "Annual Check-In",
         "Existing client due for annual plan review. Compare options and ensure adequate coverage.", 4),
    ],
    "medicare": [
        ("turning_65", "Turning 65", "birthday_outreach", "Birthday Outreach",
         "Lead is approaching Medicare eligibility. Reach out before the enrollment window opens.", 1),
        ("plan_comparison", "Plan Comparison", "plan_guidance", "Plan Guidance",
         "Lead is actively comparing Medicare Advantage and Supplement options. Provide clear guidance.", 2),
        ("enrollment_ready", "Enrollment Ready", "enrollment_support", "Enrollment Support",
         "Lead has decided to enroll. Assist with plan selection and paperwork.", 3),
        ("existing_member", "Existing Member", "member_check_in", "Member Check-In",
         "Existing Medicare client in the annual review or plan change period. Reconnect and assess needs.", 4),
    ],
    "real_estate": [
        ("active_buyer", "Active Buyer", "buyer_outreach", "Buyer Outreach",
         "Lead is actively searching for a property with a defined timeline and budget.", 1),
        ("seller_lead", "Seller Lead", "listing_pitch", "Listing Pitch",
         "Lead is considering listing their property. Focus on market analysis and home valuation.", 2),
        ("future_buyer", "Future Buyer", "nurture", "Long-Term Nurture",
         "Lead is interested in buying but timeline is 6+ months out. Keep warm with market updates.", 3),
        ("past_client", "Past Client", "referral_ask", "Referral Ask",
         "Previous client — check in for repeat business, referrals, or investment property opportunities.", 4),
    ],
    "auto_repair": [
        ("immediate_need", "Immediate Need", "urgent_service", "Urgent Service",
         "Lead has an active vehicle issue requiring immediate attention. Offer quick turnaround and transparent pricing.", 1),
        ("scheduled_service", "Scheduled Service", "appointment_reminder", "Appointment Reminder",
         "Lead has an upcoming service appointment. Confirm and set expectations for drop-off and timeline.", 2),
        ("maintenance_due", "Maintenance Due", "maintenance_outreach", "Maintenance Outreach",
         "Lead's vehicle is due for routine maintenance. Remind them of the service interval and offer a convenient time.", 3),
        ("past_customer", "Past Customer", "win_back", "Win-Back",
         "Lead was a previous customer. Re-engage with a service reminder, seasonal special, or loyalty offer.", 4),
    ],
    "solar": [
        ("hot_lead", "Hot Lead", "urgent_follow_up", "Urgent Follow-Up",
         "Lead expressed strong interest and has high utility bills. Contact immediately while motivation is high.", 1),
        ("site_visit_ready", "Site Visit Ready", "site_assessment", "Site Assessment",
         "Lead agreed to a free home solar assessment. Confirm the visit and prepare the site evaluation.", 2),
        ("proposal_sent", "Proposal Sent", "proposal_follow_up", "Proposal Follow-Up",
         "Lead received a solar proposal. Follow up on questions, financing options, and incentive deadlines.", 3),
        ("long_term_interest", "Long-Term Interest", "nurture", "Long-Term Nurture",
         "Lead is interested but waiting on better incentives or timing. Nurture with updates on savings and incentives.", 4),
    ],
}
GENERIC_TIERS = [
    ("hot_lead", "Hot Lead", "high_priority", "High Priority",
     "Lead has expressed strong interest. Follow up immediately.", 1),
    ("warm_lead", "Warm Lead", "standard_follow_up", "Standard Follow-Up",
     "Lead has shown interest but needs more nurturing before making a decision.", 2),
    ("cold_lead", "Cold Lead", "low_priority", "Low Priority",
     "Lead has minimal engagement. Keep in rotation with occasional touchpoints.", 3),
    ("needs_review", "Needs Review", "needs_review", "Needs Review",
     "Lead data needs verification or tier assignment before outreach begins.", 4),
]


def _seed_industry_tiers(db: Session, org: Organization):
    """Seed TierDefinition rows for an org based on their industry. Safe to call on existing orgs — only adds missing tiers."""
    industry = (org.industry or "general").lower()
    presets = INDUSTRY_TIERS.get(industry, GENERIC_TIERS)
    existing_keys = {t.tier_key for t in db.query(TierDefinition).filter(TierDefinition.organization_id == org.id).all()}
    for tier_key, tier_label, track_key, track_label, ai_tone_context, sort_order in presets:
        if tier_key not in existing_keys:
            db.add(TierDefinition(
                organization_id=org.id,
                tier_key=tier_key,
                tier_label=tier_label,
                track_key=track_key,
                track_label=track_label,
                ai_tone_context=ai_tone_context,
                sort_order=sort_order,
                is_active=True,
            ))
    db.flush()

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/dashboard")
def master_dashboard(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Master view - KPIs across every advisor. god_admin sees ALL orgs aggregated;
    org_admin/super_admin see their own org only.
    """
    org_ids = _get_org_ids(db, current_user)
    is_god = current_user.role == "god_admin"

    advisors = db.query(User).filter(User.organization_id.in_(org_ids), User.role == "advisor").all()

    # Three counts and an organisation lookup per advisor became three grouped
    # counts and one organisation lookup for the whole list. These counts key on
    # the advisor alone - unlike _advisor_metrics they carry no organisation
    # predicate - so the group key is the advisor alone too. An advisor with
    # nothing is absent and reads back 0, which is what COUNT(*) returned.
    _ids = sorted({a.id for a in advisors})

    def _counts(q):
        return {} if not _ids else {str(k): int(n) for k, n in q.all()}

    sent_by = _counts(db.query(Message.sender_id, func.count(Message.id))
                        .filter(Message.sender_id.in_(_ids))
                        .group_by(Message.sender_id))
    leads_by = _counts(db.query(Lead.assigned_to_id, func.count(Lead.id))
                         .filter(Lead.assigned_to_id.in_(_ids))
                         .group_by(Lead.assigned_to_id))
    hot_by = _counts(db.query(Lead.assigned_to_id, func.count(Reply.id))
                       .join(Lead, Reply.lead_id == Lead.id)
                       .filter(Lead.assigned_to_id.in_(_ids), Reply.is_hot == True)
                       .group_by(Lead.assigned_to_id))
    dash_org_names = {}
    if is_god:
        _want = sorted({a.organization_id for a in advisors if a.organization_id})
        if _want:
            dash_org_names = {o.id: o.name for o in
                              db.query(Organization).filter(Organization.id.in_(_want)).all()}

    per_advisor_stats = []
    for advisor in advisors:
        sent_count = sent_by.get(str(advisor.id), 0)
        lead_count = leads_by.get(str(advisor.id), 0)
        hot_count = hot_by.get(str(advisor.id), 0)
        org_name = dash_org_names.get(advisor.organization_id) if is_god else None
        per_advisor_stats.append({
            "advisor_id": advisor.id,
            "advisor_name": advisor.full_name,
            "organization_name": org_name,
            "leads_owned": lead_count,
            "messages_sent": sent_count,
            "hot_replies": hot_count,
        })

    total_leads = db.query(func.count(Lead.id)).filter(Lead.organization_id.in_(org_ids)).scalar()
    total_duplicates = (
        db.query(func.count(Lead.id))
        .filter(Lead.organization_id.in_(org_ids), Lead.is_duplicate == True)
        .scalar()
    )

    return {
        "organization_id": current_user.organization_id if not is_god else "all",
        "is_god_view": is_god,
        "total_leads": total_leads,
        "total_duplicates_prevented": total_duplicates,
        "advisors": per_advisor_stats,
    }


@router.get("/leads")
def all_org_leads(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Full lead list across all advisors in the org - master view. Joins in
    the assigned advisor's name rather than returning a bare
    assigned_to_id, since a raw foreign key UUID is meaningless on the
    admin dashboard - Mike needs to see WHO owns each lead at a glance.
    Returns a paginated envelope: {items, total, page, page_size}.
    """
    query = (
        db.query(Lead)
        .filter(Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db))
    )
    total = query.count()
    leads = (
        query.order_by(Lead.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    advisor_ids = {lead.assigned_to_id for lead in leads if lead.assigned_to_id}
    advisors_by_id = {
        u.id: u.full_name
        for u in db.query(User).filter(User.id.in_(advisor_ids)).all()
    } if advisor_ids else {}

    results = []
    for lead in leads:
        lead_dict = {
            "id": lead.id,
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "phone": lead.phone,
            "email": lead.email,
            "tier": lead.tier if lead.tier else None,
            "status": lead.status if lead.status else None,
            "assigned_to_id": lead.assigned_to_id,
            "assigned_to_name": advisors_by_id.get(lead.assigned_to_id, "Unassigned"),
            "created_at": lead.created_at,
        }
        results.append(lead_dict)
    return {"items": results, "total": total, "page": page, "page_size": page_size}




# ---------------------------------------------------------------------------
# Manager Command Dashboard - quality metrics, not just volume counts.
# These endpoints intentionally sit beside /admin/dashboard instead of
# replacing it, so the existing Master Dashboard contract stays stable.
# ---------------------------------------------------------------------------

HOT_REPLY_CLASSIFICATIONS = (ReplyClassification.INTERESTED, ReplyClassification.CALLBACK)


def _get_org_ids(db: Session, current_user: User) -> list:
    """Return org IDs to scope queries to.
    god_admin → all orgs; super_admin → platform-scoped orgs; everyone else → own org."""
    return get_platform_org_ids(current_user, db)


def _safe_rate(numerator: int, denominator: int) -> float:
    """Return a percentage rounded to 2 decimals, with 0 for empty denominators."""
    if not denominator:
        return 0
    return round((numerator / denominator) * 100, 2)


class AdvisorCounts:
    """Every count `_advisor_metrics` needs, for a WHOLE advisor cohort, in seven
    grouped queries instead of seven per advisor.

    WHY. `/admin/dashboard/metrics` called _advisor_metrics once per advisor, and
    each call ran seven COUNT(*) statements. At 75 advisors that is 525 counts,
    plus one Organization lookup per advisor on the god view - 604 statements to
    render one table. The counts themselves were always cheap; asking for them
    one advisor at a time was not.

    EQUIVALENCE. Each query below mirrors ONE of the original filters exactly,
    including the `is_duplicate == False` predicate (which excludes NULL in SQL,
    and still does here). Every group key is the PAIR (lead.organization_id,
    person) rather than the person alone, because the per-advisor version scoped
    every count to that advisor's OWN organization - grouping on the person alone
    would silently fold in another org's rows. A pair that never appears in the
    grouped result is absent from the dict and reads back as 0, which is what
    `.scalar() or 0` produced before.
    """

    __slots__ = ("leads_owned", "messages_sent", "replies", "hot_replies",
                 "booked", "dnc", "duplicates")

    def __init__(self, db: Session, advisors):
        ids = sorted({a.id for a in advisors})
        orgs = sorted({str(a.organization_id) for a in advisors
                       if a.organization_id is not None})
        empty = not ids or not orgs

        def grouped(q):
            if empty:
                return {}
            return {(str(org), str(who)): int(n) for org, who, n in q.all()}

        base_lead = lambda: (
            db.query(Lead.organization_id, Lead.assigned_to_id, func.count(Lead.id))
              .filter(Lead.organization_id.in_(orgs),
                      Lead.assigned_to_id.in_(ids))
              .group_by(Lead.organization_id, Lead.assigned_to_id))

        self.leads_owned = grouped(base_lead().filter(Lead.is_duplicate == False))
        self.booked = grouped(base_lead().filter(Lead.is_duplicate == False,
                                                 Lead.status == "booked"))
        self.dnc = grouped(base_lead().filter(Lead.is_duplicate == False,
                                              Lead.status == "dnc"))
        self.duplicates = grouped(base_lead().filter(Lead.is_duplicate == True))

        self.messages_sent = grouped(
            db.query(Lead.organization_id, Message.sender_id, func.count(Message.id))
              .join(Lead, Message.lead_id == Lead.id)
              .filter(Lead.organization_id.in_(orgs),
                      Message.sender_id.in_(ids),
                      Lead.is_duplicate == False)
              .group_by(Lead.organization_id, Message.sender_id))

        reply_q = lambda: (
            db.query(Lead.organization_id, Lead.assigned_to_id, func.count(Reply.id))
              .join(Lead, Reply.lead_id == Lead.id)
              .filter(Lead.organization_id.in_(orgs),
                      Lead.assigned_to_id.in_(ids),
                      Lead.is_duplicate == False)
              .group_by(Lead.organization_id, Lead.assigned_to_id))

        self.replies = grouped(reply_q())
        self.hot_replies = grouped(reply_q().filter(
            (Reply.classification.in_(HOT_REPLY_CLASSIFICATIONS)) | (Reply.is_hot == True)))


def _advisor_metrics(db: Session, organization_id: str, advisor: User,
                     pre: Optional[AdvisorCounts] = None) -> dict:
    """
    Build quality metrics for one advisor using only existing tables.

    Notes on definitions:
    - messages_sent: SMS Message rows sent by this advisor.
    - replies/hot_replies: Reply rows on leads currently owned by this advisor.
    - hot reply = AI/manual classification interested or callback OR legacy is_hot=True.
    - booking/dnc rates use total leads owned as denominator.
    - duplicate_leads_prevented follows the existing project convention:
      Lead.is_duplicate=True, set by the ContactRegistry/dedup flow.
    """
    if pre is not None:
        # Same numbers, already counted. The return shape below is deliberately
        # left as the single definition of this row, so the batched path cannot
        # drift away from the per-advisor one.
        k = (str(organization_id), str(advisor.id))
        leads_owned = pre.leads_owned.get(k, 0)
        messages_sent = pre.messages_sent.get(k, 0)
        replies = pre.replies.get(k, 0)
        hot_replies = pre.hot_replies.get(k, 0)
        booked_leads = pre.booked.get(k, 0)
        dnc_leads = pre.dnc.get(k, 0)
        duplicate_leads_prevented = pre.duplicates.get(k, 0)
        return _advisor_row(advisor, leads_owned, messages_sent, replies,
                            hot_replies, booked_leads, dnc_leads,
                            duplicate_leads_prevented)

    leads_owned = db.query(func.count(Lead.id)).filter(
        Lead.organization_id == organization_id,
        Lead.assigned_to_id == advisor.id,
        Lead.is_duplicate == False,
    ).scalar() or 0

    messages_sent = db.query(func.count(Message.id)).join(Lead, Message.lead_id == Lead.id).filter(
        Lead.organization_id == organization_id,
        Message.sender_id == advisor.id,
        Lead.is_duplicate == False,
    ).scalar() or 0

    replies = db.query(func.count(Reply.id)).join(Lead, Reply.lead_id == Lead.id).filter(
        Lead.organization_id == organization_id,
        Lead.assigned_to_id == advisor.id,
        Lead.is_duplicate == False,
    ).scalar() or 0

    hot_replies = db.query(func.count(Reply.id)).join(Lead, Reply.lead_id == Lead.id).filter(
        Lead.organization_id == organization_id,
        Lead.assigned_to_id == advisor.id,
        Lead.is_duplicate == False,
        ((Reply.classification.in_(HOT_REPLY_CLASSIFICATIONS)) | (Reply.is_hot == True)),
    ).scalar() or 0

    booked_leads = db.query(func.count(Lead.id)).filter(
        Lead.organization_id == organization_id,
        Lead.assigned_to_id == advisor.id,
        Lead.status == "booked",
        Lead.is_duplicate == False,
    ).scalar() or 0

    dnc_leads = db.query(func.count(Lead.id)).filter(
        Lead.organization_id == organization_id,
        Lead.assigned_to_id == advisor.id,
        Lead.status == "dnc",
        Lead.is_duplicate == False,
    ).scalar() or 0

    duplicate_leads_prevented = db.query(func.count(Lead.id)).filter(
        Lead.organization_id == organization_id,
        Lead.assigned_to_id == advisor.id,
        Lead.is_duplicate == True,
    ).scalar() or 0

    return _advisor_row(advisor, leads_owned, messages_sent, replies,
                        hot_replies, booked_leads, dnc_leads,
                        duplicate_leads_prevented)


def _advisor_row(advisor: User, leads_owned, messages_sent, replies, hot_replies,
                 booked_leads, dnc_leads, duplicate_leads_prevented) -> dict:
    """The one place an advisor metrics row is shaped. Both the per-advisor and
    the cohort path end here, so they cannot return different keys."""
    return {
        "advisor_id": advisor.id,
        "advisor_name": advisor.full_name,
        "leads_owned": leads_owned,
        "messages_sent": messages_sent,
        "replies": replies,
        "hot_replies": hot_replies,
        "booked_leads": booked_leads,
        "dnc_leads": dnc_leads,
        "duplicate_leads_prevented": duplicate_leads_prevented,
        "reply_rate": _safe_rate(replies, messages_sent),
        "hot_reply_rate": _safe_rate(hot_replies, messages_sent),
        "booking_rate": _safe_rate(booked_leads, leads_owned),
        "dnc_rate": _safe_rate(dnc_leads, leads_owned),
    }


@router.get("/dashboard/metrics")
def dashboard_quality_metrics(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Advisor quality metrics. god_admin sees all orgs aggregated; others see their own org."""
    org_ids = _get_org_ids(db, current_user)
    is_god = current_user.role == "god_admin"

    advisors = (
        db.query(User)
        .filter(User.organization_id.in_(org_ids), User.role == "advisor")
        .order_by(User.full_name.asc())
        .all()
    )

    counts = AdvisorCounts(db, advisors)
    # The god view printed an organisation name per advisor and fetched the
    # organisation again for every one of them, including the same org 3 times
    # over for a 3-advisor customer.
    org_names = {}
    if is_god:
        wanted = sorted({a.organization_id for a in advisors if a.organization_id})
        if wanted:
            org_names = {o.id: o.name for o in
                         db.query(Organization).filter(Organization.id.in_(wanted)).all()}

    advisor_rows = []
    for advisor in advisors:
        row = _advisor_metrics(db, str(advisor.organization_id), advisor, counts)
        if is_god:
            row["organization_name"] = org_names.get(advisor.organization_id)
        advisor_rows.append(row)

    totals = {
        "advisor_id": "org_total",
        "advisor_name": "All Organizations" if is_god else "Organization total",
        "leads_owned": sum(row["leads_owned"] for row in advisor_rows),
        "messages_sent": sum(row["messages_sent"] for row in advisor_rows),
        "replies": sum(row["replies"] for row in advisor_rows),
        "hot_replies": sum(row["hot_replies"] for row in advisor_rows),
        "booked_leads": sum(row["booked_leads"] for row in advisor_rows),
        "dnc_leads": sum(row["dnc_leads"] for row in advisor_rows),
        "duplicate_leads_prevented": db.query(func.count(Lead.id)).filter(
            Lead.organization_id.in_(org_ids),
            Lead.is_duplicate == True,
        ).scalar() or 0,
    }
    totals["reply_rate"] = _safe_rate(totals["replies"], totals["messages_sent"])
    totals["hot_reply_rate"] = _safe_rate(totals["hot_replies"], totals["messages_sent"])
    totals["booking_rate"] = _safe_rate(totals["booked_leads"], totals["leads_owned"])
    totals["dnc_rate"] = _safe_rate(totals["dnc_leads"], totals["leads_owned"])

    return {
        "organization_id": current_user.organization_id if not is_god else "all",
        "is_god_view": is_god,
        "totals": totals,
        "advisors": advisor_rows,
    }


@router.get("/dashboard/funnel")
def dashboard_funnel(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Lead funnel counts. god_admin sees all orgs aggregated; others see their own org."""
    org_ids = _get_org_ids(db, current_user)
    is_god = current_user.role == "god_admin"

    total_leads = db.query(func.count(Lead.id)).filter(Lead.organization_id.in_(org_ids)).scalar() or 0

    sent = db.query(func.count(distinct(Lead.id))).join(Message, Message.lead_id == Lead.id).filter(
        Lead.organization_id.in_(org_ids),
    ).scalar() or 0

    replied = db.query(func.count(distinct(Lead.id))).join(Reply, Reply.lead_id == Lead.id).filter(
        Lead.organization_id.in_(org_ids),
    ).scalar() or 0

    hot_interested = db.query(func.count(distinct(Lead.id))).join(Reply, Reply.lead_id == Lead.id).filter(
        Lead.organization_id.in_(org_ids),
        ((Reply.classification.in_(HOT_REPLY_CLASSIFICATIONS)) | (Reply.is_hot == True)),
    ).scalar() or 0

    booked = db.query(func.count(Lead.id)).filter(
        Lead.organization_id.in_(org_ids),
        Lead.status == "booked",
    ).scalar() or 0

    sold = db.query(func.count(distinct(Lead.id))).join(LeadOutcome, LeadOutcome.lead_id == Lead.id).filter(
        Lead.organization_id.in_(org_ids),
        LeadOutcome.resulted_in_sale == True,
    ).scalar() or 0

    stages = [
        {"key": "total_leads", "label": "Total leads", "count": total_leads},
        {"key": "sent", "label": "Sent", "count": sent},
        {"key": "replied", "label": "Replied", "count": replied},
        {"key": "hot_interested", "label": "Hot / interested", "count": hot_interested},
        {"key": "booked", "label": "Booked", "count": booked},
        {"key": "sold", "label": "Sold", "count": sold},
    ]

    return {
        "organization_id": current_user.organization_id if not is_god else "all",
        "is_god_view": is_god,
        "total_leads": total_leads,
        "sent": sent,
        "replied": replied,
        "hot_interested": hot_interested,
        "booked": booked,
        "sold": sold,
        "stages": stages,
    }


@router.get("/dashboard/revenue")
def dashboard_revenue(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Master Control Board / revenue analytics - step 6 of the original
    8-step build plan, never started until now.

    IMPORTANT DESIGN CONSTRAINT, matching LeadOutcome.sale_amount's own
    column comment: sale_amount is a free-text sales note field an
    advisor types in (e.g. "$3,200" or "approx 2800 plus marker"), NOT a
    structured currency column. This endpoint deliberately reports SALE
    COUNTS, not dollar totals - summing or parsing sale_amount strings as
    real currency would produce a number that looks precise and
    authoritative but isn't reliable financial data. Real revenue
    accounting belongs in Restland's actual accounting system, not here.
    What this CAN do reliably: how many sales, by whom, of what kind, and
    when - all of which come from structured boolean/date fields.
    """
    org_id = current_user.organization_id

    sale_outcomes = (
        db.query(LeadOutcome)
        .join(Lead, LeadOutcome.lead_id == Lead.id)
        .filter(Lead.organization_id == org_id, LeadOutcome.resulted_in_sale == True)
        .all()
    )

    total_sales = len(sale_outcomes)

    # Per-advisor sale counts - the actual gap _advisor_metrics didn't cover.
    sales_by_advisor: dict[str, int] = defaultdict(int)
    for outcome in sale_outcomes:
        sales_by_advisor[outcome.recorded_by_id] += 1

    advisor_ids = list(sales_by_advisor.keys())
    advisors_by_id = {}
    if advisor_ids:
        advisors_by_id = {
            a.id: a.full_name
            for a in db.query(User).filter(User.id.in_(advisor_ids)).all()
        }

    by_advisor = sorted(
        [
            {"advisor_id": advisor_id, "advisor_name": advisors_by_id.get(advisor_id, "Unknown"), "sale_count": count}
            for advisor_id, count in sales_by_advisor.items()
        ],
        key=lambda row: row["sale_count"],
        reverse=True,
    )

    # What's being sold - the structured checklist fields are reliable
    # counts; sale_items is free text and only shown as a recent-notes
    # list, never aggregated, since advisors don't write it in any
    # consistent structured format.
    product_mix = {
        "funeral_arrangement": sum(1 for o in sale_outcomes if o.has_funeral_arrangement),
        "cemetery_property": sum(1 for o in sale_outcomes if o.has_cemetery_property),
        "marker": sum(1 for o in sale_outcomes if o.has_marker),
        "memorial": sum(1 for o in sale_outcomes if o.has_memorial),
    }

    # Monthly trend, grouped in Python rather than via a DB-specific
    # date-trunc function, since this app runs on SQLite in tests/dev and
    # Postgres in production - a portable approach beats a function that
    # only works on one of the two.
    monthly_counts: dict[str, int] = defaultdict(int)
    for outcome in sale_outcomes:
        when = outcome.appointment_date or outcome.created_at
        if when:
            monthly_counts[when.strftime("%Y-%m")] += 1
    monthly_trend = [
        {"month": month, "sale_count": count}
        for month, count in sorted(monthly_counts.items())
    ]

    recent_sale_notes = [
        {
            "lead_id": outcome.lead_id,
            "advisor_name": advisors_by_id.get(outcome.recorded_by_id) or (
                db.query(User.full_name).filter(User.id == outcome.recorded_by_id).scalar()
            ),
            "sale_items": outcome.sale_items,
            "sale_amount": outcome.sale_amount,  # shown verbatim as the advisor's own note text, never parsed/summed
            "date": outcome.appointment_date or outcome.created_at,
        }
        for outcome in sorted(sale_outcomes, key=lambda o: o.created_at or datetime.min, reverse=True)[:20]
    ]

    return {
        "total_sales": total_sales,
        "by_advisor": by_advisor,
        "product_mix": product_mix,
        "monthly_trend": monthly_trend,
        "recent_sale_notes": recent_sale_notes,
    }


# ---------------------------------------------------------------------------
# User management - lets an org_admin/super_admin create and manage advisor
# accounts directly from the app, instead of running the seed.py script by
# hand. This was a real gap Mike specifically flagged: the only way to add
# an advisor was a one-time backend script, not a real in-app workflow.
# ---------------------------------------------------------------------------

class CreateUserRequest(BaseModel):
    email: EmailStr
    full_name: str
    role: str = "advisor"  # advisor, org_admin (super_admin is reserved, not creatable here)


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    must_change_password: bool
    # The one-time link, shown once. This REPLACED `temp_password`: a link
    # that expires and can only be redeemed once is a different kind of
    # secret from a live password sitting in a response body.
    setup_url: str | None = None


def _unknowable_password() -> str:
    """Generated, hashed by the caller, and discarded in the same breath.

    This REPLACED `_generate_temp_password`, which produced a short typeable
    string that was then returned to the caller - so a live credential travelled
    through an API response, a browser and whatever the admin pasted it into.

    An account still needs SOME hash so that no code path treats it as
    password-less. Nobody needs to know what it is, and now nobody can: the
    person is reached by a one-time link and chooses their own password. Same
    pattern as `customer_activation` and `sales_staff`.
    """
    return secrets.token_urlsafe(48)


class UserResponseWithOrg(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    must_change_password: bool
    setup_url: str | None = None
    organization_id: str | None = None
    organization_name: str | None = None
    profile_photo_url: str | None = None


@router.get("/users")
def list_users(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Lists users. God/super admin sees ALL users across every org; org_admin sees their own org only."""
    if current_user.role in ("super_admin", "god_admin"):
        scoped_org_ids = get_platform_org_ids(current_user, db)
        if current_user.role == "god_admin":
            users = db.query(User).order_by(User.organization_id.asc(), User.created_at.asc()).all()
        else:
            # super_admin: platform-scoped only
            users = (
                db.query(User)
                .filter(User.organization_id.in_(scoped_org_ids))
                .order_by(User.organization_id.asc(), User.created_at.asc())
                .all()
            )
        org_ids = {u.organization_id for u in users}
        orgs_by_id = {
            o.id: o.name
            for o in db.query(Organization).filter(Organization.id.in_(org_ids)).all()
        }
        return [
            {
                "id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role,
                "is_active": u.is_active, "must_change_password": u.must_change_password,
                "organization_id": u.organization_id,
                "organization_name": orgs_by_id.get(u.organization_id, "Unknown"),
                "profile_photo_url": getattr(u, "profile_photo_url", None),
            }
            for u in users
        ]
    users = (
        db.query(User)
        .filter(User.organization_id == current_user.organization_id)
        .order_by(User.created_at.asc())
        .all()
    )
    return [
        UserResponse(
            id=u.id, email=u.email, full_name=u.full_name, role=u.role,
            is_active=u.is_active, must_change_password=u.must_change_password,
            profile_photo_url=getattr(u, "profile_photo_url", None),
        )
        for u in users
    ]


@router.post("/users", response_model=UserResponse)
def create_user(
    req: CreateUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Creates a new advisor (or org_admin) account in the current admin's
    organization. Generates a temporary password, returned ONCE in this
    response only - never retrievable again afterward, same security
    pattern as how Twilio/OpenAI show API keys only at creation time.
    The new account is forced to change that password on first login.
    """
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="A user with this email already exists.")

    if req.role not in ("advisor", "org_admin"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Role must be 'advisor' or 'org_admin'.")

    # No plaintext password is created or returned. See _unknowable_password.
    secret = _unknowable_password()
    new_user = User(
        # A NEUTRAL OWNER MUST NOT MANUFACTURE AN IDENTITY.
        #
        # This read current_user.organization_id directly. For a
        # god_admin with no customer selected that is None, and an
        # org-NULL user is the system's POSITIVE ASSERTION that someone
        # is a brand-sales identity (deps.py). So creating a user from
        # God Mode with nothing selected produced a phantom seller:
        # refused by every tenant route, holding no membership, useless
        # in the sales workspace, and indistinguishable from a real one.
        # tenant_write_org_id returns a 409 naming what to select.
        organization_id=_tenant_write_org_id(current_user),
        email=req.email,
        password_hash=hash_password(secret),
        full_name=req.full_name,
        role=req.role,
        must_change_password=True,
    )
    db.add(new_user)
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="user.create", target_type="user", target_id=new_user.id,
        details={"email": new_user.email, "role": new_user.role, "created_by": current_user.full_name},
    )

    # Hand the operator a one-time link instead of a password. Issued after
    # flush so the user row exists to point at.
    _row, _raw = _activation.issue(db, new_user, current_user,
                                   purpose=_PURPOSE_SETUP)
    _setup_url = _activation.activation_url(getattr(req, 'base_url', None), _raw)
    db.commit()
    db.refresh(new_user)

    return UserResponse(
        id=new_user.id, email=new_user.email, full_name=new_user.full_name,
        role=new_user.role, is_active=new_user.is_active,
        must_change_password=new_user.must_change_password,
        setup_url=_setup_url,
    )


def _get_target_user_for_admin(user_id: str, current_user: User, db: Session) -> User:
    """
    Fetch the target user for admin actions with correct scope:
    - god_admin     → any user in any org (platform-wide)
    - super_admin   → users in their own org only
    - org_admin     → users in their own org only
    Raises 404 if not found within scope.

    Two refusals added Aug 26 2026, both about rows this filter could match
    that it was never meant to reach:

    - An elevated target. deactivate/force-logout each carried their own
      "cannot touch a super_admin or god_admin" check, but reactivate,
      clear-setup and the detail view did not, so the protection depended on
      which route you happened to call. It now lives in one place.
    - An actor with no organization_id. `organization_id == None` renders as
      IS NULL, so an org-less caller would have matched every brand-sales
      identity at once rather than nothing at all.
    """
    from fastapi import HTTPException
    if current_user.role == "god_admin":
        target = db.query(User).filter(User.id == user_id).first()
    elif getattr(current_user, "organization_id", None) is None:
        target = None
    else:
        target = db.query(User).filter(
            User.id == user_id, User.organization_id == current_user.organization_id
        ).first()
        if target and target.role in ELEVATED_ROLES and target.id != current_user.id:
            target = None
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    return target


@router.patch("/users/{user_id}/deactivate")
def deactivate_user(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Deactivates (not deletes) an advisor account — they can no longer log
    in, but their leads/messages/history stay intact for record-keeping.
    Also immediately kills any active session by clearing session_token,
    so outstanding JWTs are rejected on the next request.
    """
    from fastapi import HTTPException
    target = _get_target_user_for_admin(user_id, current_user, db)
    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
    if target.role in ("super_admin", "god_admin"):
        raise HTTPException(status_code=400, detail="Cannot deactivate a super_admin or god_admin account.")

    target.is_active = False
    target.session_token = None  # immediately invalidate any active JWT
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="user.deactivate", target_type="user", target_id=target.id,
        details={"email": target.email},
    )

    return {"success": True}


@router.patch("/users/{user_id}/reactivate")
def reactivate_user(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Re-enables a previously deactivated account."""
    target = _get_target_user_for_admin(user_id, current_user, db)

    target.is_active = True
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="user.reactivate", target_type="user", target_id=target.id,
        details={"email": target.email},
    )

    return {"success": True}


@router.post("/users/{user_id}/force-logout")
def force_logout_user(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Immediately invalidates the target user's active session by clearing
    their session_token. Their next API request (within milliseconds) will
    get a 401 and the frontend will redirect them to login. The account
    stays active — this is a kick, not a deactivation.

    Scope:
      - org_admin: own org only
      - super_admin / god_admin: any user in any org
    Cannot force-logout super_admin or god_admin accounts.
    """
    from fastapi import HTTPException
    target = _get_target_user_for_admin(user_id, current_user, db)
    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot force-logout your own account.")
    if target.role in ("super_admin", "god_admin"):
        raise HTTPException(status_code=400, detail="Cannot force-logout a super_admin or god_admin account.")

    target.session_token = None
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="user.force_logout", target_type="user", target_id=target.id,
        details={"email": target.email},
    )

    return {"success": True}


@router.patch("/users/{user_id}/clear-setup")
def clear_setup_flag(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Clears the must_change_password flag without touching the password.
    Used when an admin confirms a user has been set up manually (e.g.
    password was set at provisioning). Any org_admin can call this for
    users in their own org.
    """
    from fastapi import HTTPException
    target = db.query(User).filter(
        User.id == user_id, User.organization_id == current_user.organization_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    target.must_change_password = False
    db.commit()
    log_action(
        db, current_user.organization_id, current_user.id,
        action="user.clear_setup", target_type="user", target_id=target.id,
        details={"email": target.email},
    )
    return {"success": True}


# ---------------------------------------------------------------------------
# Password reset - SUPER ADMIN ONLY, by Mike's explicit instruction.
# Org admins can deactivate/reactivate accounts (above) but must NOT be
# able to reset passwords - that's a more sensitive action reserved for
# the super_admin role alone.
# ---------------------------------------------------------------------------

class ResetPasswordResponse(BaseModel):
    email: str
    # Null when the admin supplied an explicit password (it is not echoed back),
    # set when access was handed over as a one-time link instead.
    setup_url: str | None = None


# require_super_admin is imported from app.deps — platform-scoped, shared across routers


class ResetPasswordRequest(BaseModel):
    new_password: str | None = Field(default=None, min_length=8)  # if provided, must be 8+ chars; otherwise auto-generate


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
def reset_user_password(
    user_id: str,
    req: ResetPasswordRequest = ResetPasswordRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    Resets a user's password. Super admin only, cross-org WITHIN THE CALLER'S
    OWN PLATFORM.

    Until Aug 26 2026 this loaded the target with
    `db.query(User).filter(User.id == user_id)` and nothing else. A probe
    proved what that allowed: a super_admin on one platform reset the
    god_admin's password and then logged in as the owner. Full takeover, one
    POST, no error. load_user_in_scope is now the only way in - it refuses any
    god_admin target for a non-owner, refuses peer super_admins, and confines
    everyone below god to their own platform's organizations.

    TWO PATHS, NEITHER OF WHICH RETURNS A PASSWORD.

    If the admin supplies `new_password` it is honoured and NOT echoed back -
    they already know it, so returning it would only put a live credential in a
    response body for no gain.

    Otherwise the account is given a hash nobody can know and the person is
    reached by a one-time link, the same mechanism the brand-sales flow uses.
    That replaced generating a short password and handing it to the caller.
    """
    from fastapi import HTTPException
    target = load_user_in_scope(db, current_user, user_id)

    issued_url = None
    if req.new_password:
        if len(req.new_password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
        secret = req.new_password
        target.must_change_password = False
    else:
        secret = _unknowable_password()
        target.must_change_password = True
    target.password_hash = hash_password(secret)
    if not req.new_password:
        row, raw = _activation.issue(db, target, current_user,
                                    purpose=_PURPOSE_RESET)
        issued_url = _activation.activation_url(getattr(req, "base_url", None), raw)
    db.commit()

    # CRITICAL: never include temp_password in the audit details - the
    # audit log is the one thing that should outlive this response, and a
    # password (even temporary) has no business living in a log table.
    log_action(
        db, current_user.organization_id, current_user.id,
        action="user.reset_password", target_type="user", target_id=target.id,
        details={"email": target.email},
    )

    return ResetPasswordResponse(email=target.email, setup_url=issued_url)


# ---------------------------------------------------------------------------
# Edit user details - SUPER ADMIN ONLY, same reasoning as password reset
# above: a typo'd name or wrong email on an existing account is something
# the org owner needs to be able to fix directly, without it being
# delegated to every org_admin. Does NOT touch password or allow promoting
# to super_admin (role changes here are limited to advisor/org_admin, same
# restriction as create_user above).
# ---------------------------------------------------------------------------

class UpdateUserRequest(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None
    role: str | None = None  # 'advisor' or 'org_admin' only - see validation below


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    req: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    Edits an existing user's name, email, and/or role. Fields omitted from
    the request are left unchanged - this is a partial update, not a
    full replace, so the frontend doesn't have to resend everything.
    """
    from fastapi import HTTPException

    # Own-org scoping (this route was already correct on that point - it is the
    # pattern the other routes were missing). What it did NOT stop was a
    # super_admin editing a PEER super_admin in the same org: the role check
    # further down refused a role change but left email alone, and changing a
    # peer's email address then requesting a reset link is the same takeover
    # with one extra step. _get_target_user_for_admin now carries both rules.
    target = _get_target_user_for_admin(user_id, current_user, db)

    before = {"full_name": target.full_name, "email": target.email, "role": target.role}

    if req.email is not None and req.email != target.email:
        existing = db.query(User).filter(User.email == req.email, User.id != target.id).first()
        if existing:
            raise HTTPException(status_code=400, detail="A user with this email already exists.")
        target.email = req.email

    if req.full_name is not None:
        cleaned_name = req.full_name.strip()
        if not cleaned_name:
            raise HTTPException(status_code=400, detail="full_name cannot be blank.")
        target.full_name = cleaned_name

    if req.role is not None:
        if target.role in ("super_admin", "god_admin"):
            raise HTTPException(status_code=400, detail="Cannot change a super_admin or god_admin account's role.")
        if req.role not in ("advisor", "org_admin"):
            raise HTTPException(status_code=400, detail="Role must be 'advisor' or 'org_admin'.")
        target.role = req.role

    db.commit()
    db.refresh(target)

    after = {"full_name": target.full_name, "email": target.email, "role": target.role}
    changed = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
    if changed:
        log_action(
            db, current_user.organization_id, current_user.id,
            action="user.update", target_type="user", target_id=target.id,
            details=changed,
        )

    return UserResponse(
        id=target.id, email=target.email, full_name=target.full_name, role=target.role,
        is_active=target.is_active, must_change_password=target.must_change_password,
    )


# ---------------------------------------------------------------------------
# Per-user detail page - this didn't exist before. Clicking a name in User
# Management went nowhere; there was no way to see what a user had actually
# done (lead counts, performance, recent activity) without going through
# the org-wide Master Dashboard and manually finding their row. Reuses
# _advisor_metrics (already computes leads_owned/messages_sent/replies/
# booking_rate/etc. for the Master Dashboard) rather than recomputing those
# numbers a second way.
# ---------------------------------------------------------------------------

def _recent_activity_for_advisor(db: Session, organization_id: str, advisor_id: str, limit: int = 12) -> list[dict]:
    """
    Merges the advisor's most recent sent messages and the replies received
    on leads they own into one chronological feed. Two separate queries
    (not a SQL UNION) since Message and Reply have different columns and
    different join paths to Lead - simpler and clearer to merge in Python
    for a feed this small (limit defaults to 12) than to fight an ORM
    UNION across mismatched column sets.
    """
    messages = (
        db.query(Message, Lead.first_name, Lead.last_name, Lead.id)
        .join(Lead, Message.lead_id == Lead.id)
        .filter(Lead.organization_id == organization_id, Message.sender_id == advisor_id)
        .order_by(Message.sent_at.desc())
        .limit(limit)
        .all()
    )
    replies = (
        db.query(Reply, Lead.first_name, Lead.last_name, Lead.id)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(Lead.organization_id == organization_id, Lead.assigned_to_id == advisor_id)
        .order_by(Reply.received_at.desc())
        .limit(limit)
        .all()
    )

    feed = []
    for message, first_name, last_name, lead_id in messages:
        feed.append({
            "type": "sent",
            "timestamp": message.sent_at,
            "lead_id": lead_id,
            "lead_name": f"{first_name or ''} {last_name or ''}".strip() or "Unknown lead",
            "body": message.body,
        })
    for reply, first_name, last_name, lead_id in replies:
        feed.append({
            "type": "reply",
            "timestamp": reply.received_at,
            "lead_id": lead_id,
            "lead_name": f"{first_name or ''} {last_name or ''}".strip() or "Unknown lead",
            "body": reply.body,
            "classification": reply.classification.value if reply.classification else None,
        })

    feed.sort(key=lambda item: item["timestamp"] or datetime.min, reverse=True)
    return feed[:limit]


@router.get("/users/{user_id}/detail")
def get_user_detail(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Full detail view for one user: profile fields, performance metrics
    (same numbers as the Master Dashboard, scoped to just this person),
    and a recent activity feed. org_admin and super_admin can view any
    user in their org; this is read-only regardless of role - editing
    still goes through PATCH /users/{user_id} (super_admin only) above.
    """
    target = db.query(User).filter(
        User.id == user_id, User.organization_id == current_user.organization_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    metrics = _advisor_metrics(db, current_user.organization_id, target)
    activity = _recent_activity_for_advisor(db, current_user.organization_id, target.id)

    return {
        "id": target.id,
        "email": target.email,
        "full_name": target.full_name,
        "role": target.role,
        "is_active": target.is_active,
        "must_change_password": target.must_change_password,
        "last_login_at": target.last_login_at,
        "metrics": metrics,
        "recent_activity": activity,
    }


# ---------------------------------------------------------------------------
# Lead reassignment - the manual routing capability Mike specifically
# asked for: look at the full pool of leads and direct specific ones to
# specific advisors (e.g. "memorial-interested leads go to this person").
# ---------------------------------------------------------------------------

class ReassignLeadRequest(BaseModel):
    lead_ids: list[str] = Field(..., max_length=1000)
    new_assigned_to_id: str | None = None  # None = unassign, leave in the pool


class ReassignResultResponse(BaseModel):
    reassigned_count: int
    skipped_count: int
    skipped_ids: list[str]


@router.post("/leads/reassign", response_model=ReassignResultResponse)
def reassign_leads(
    req: ReassignLeadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Reassigns one or more leads to a different advisor (or unassigns them
    back to the pool if new_assigned_to_id is None). Both the leads and
    the target advisor must belong to the current admin's organization -
    enforced explicitly below rather than trusted from the request body.
    """
    from fastapi import HTTPException

    if req.new_assigned_to_id:
        target_advisor = db.query(User).filter(
            User.id == req.new_assigned_to_id,
            User.organization_id == current_user.organization_id,
            User.is_active == True,
        ).first()
        if not target_advisor:
            raise HTTPException(status_code=404, detail="Target advisor not found or inactive in this organization.")

    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids), Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db)
    ).all()
    found_ids = {l.id for l in leads}
    skipped_ids = [lid for lid in req.lead_ids if lid not in found_ids]

    for lead in leads:
        lead.assigned_to_id = req.new_assigned_to_id

    db.commit()

    if leads:
        log_action(
            db, current_user.organization_id, current_user.id,
            action="lead.reassign", target_type="lead_batch", target_id=",".join(found_ids) if len(found_ids) <= 20 else f"{len(found_ids)}_leads",
            details={
                "lead_ids": sorted(found_ids),
                "new_assigned_to_id": req.new_assigned_to_id,
                "count": len(leads),
            },
        )

    return ReassignResultResponse(
        reassigned_count=len(leads),
        skipped_count=len(skipped_ids),
        skipped_ids=skipped_ids,
    )


@router.get("/leads/unassigned")
def list_unassigned_leads(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Returns every lead in the org's pool that has no advisor assigned yet -
    the queue an admin works through when manually routing leads out to
    the team, rather than every lead defaulting to whoever happened to
    import it.
    """
    leads = (
        db.query(Lead)
        .filter(Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db), Lead.assigned_to_id.is_(None))
        .order_by(Lead.created_at.desc())
        .limit(500)
        .all()
    )
    return [
        {
            "id": l.id, "first_name": l.first_name, "last_name": l.last_name,
            "phone": l.phone, "email": l.email,
            "tier": l.tier if l.tier else None,
            "engagement_temperature": l.engagement_temperature.value if l.engagement_temperature else None,
            "created_at": l.created_at,
        }
        for l in leads
    ]


# ---------------------------------------------------------------------------
# Lead Cleanup Center - potential duplicate discovery, safe merge, contact fixes.
# ---------------------------------------------------------------------------

class LeadSummary(BaseModel):
    id: str
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str | None = None
    assigned_to_id: str | None = None
    is_duplicate: bool | None = None


class PotentialDuplicateGroup(BaseModel):
    match_type: str
    match_key: str
    leads: list[LeadSummary]


class MergeLeadsRequest(BaseModel):
    keep_lead_id: str
    merge_lead_ids: list[str] = Field(..., max_length=1000)


class MergeLeadsResponse(BaseModel):
    keep_lead_id: str
    merged_count: int
    moved_messages: int
    moved_replies: int
    moved_cadence_states: int
    moved_outcomes: int
    deleted_lead_ids: list[str]


class FixContactInfoRequest(BaseModel):
    phone: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    unflag_duplicate: bool | None = None  # set True to clear is_duplicate flag


def _lead_summary(lead: Lead) -> dict[str, Any]:
    return {
        "id": lead.id,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "phone": lead.phone,
        "email": lead.email,
        "status": lead.status if lead.status else None,
        "assigned_to_id": lead.assigned_to_id,
        "is_duplicate": bool(lead.is_duplicate),
    }


def _delete_merged_lead_records(db: Session, merge_leads: list[Lead]) -> None:
    """
    Small seam for transaction tests: route calls this after related rows are
    moved but before commit. If this raises, the caller rolls back everything.

    Uses a bulk delete instead of ORM db.delete(lead) so SQLAlchemy does not
    try to null out one-to-one relationship children such as CadenceState after
    their lead_id has already been reassigned to the kept lead.

    Must delete child records with non-cascading FKs first (booking_links,
    lead_outcomes, messages, replies) before deleting the lead rows.
    """
    from app.models.models import BookingLink, LeadOutcome, Message, Reply

    merge_ids = [lead.id for lead in merge_leads]
    if not merge_ids:
        return

    # Delete child records that reference lead_id without CASCADE
    db.query(BookingLink).filter(BookingLink.lead_id.in_(merge_ids)).delete(synchronize_session=False)
    db.query(LeadOutcome).filter(LeadOutcome.lead_id.in_(merge_ids)).delete(synchronize_session=False)
    db.query(Message).filter(Message.lead_id.in_(merge_ids)).delete(synchronize_session=False)
    db.query(Reply).filter(Reply.lead_id.in_(merge_ids)).delete(synchronize_session=False)

    # Clear self-referential duplicate_of_lead_id on any leads pointing to ones being deleted
    db.query(Lead).filter(Lead.duplicate_of_lead_id.in_(merge_ids)).update(
        {"duplicate_of_lead_id": None, "is_duplicate": False},
        synchronize_session=False
    )

    # Now safe to delete the lead rows
    db.query(Lead).filter(Lead.id.in_(merge_ids)).delete(synchronize_session=False)


def _apply_contact_registry_after_contact_fix(db: Session, lead: Lead) -> None:
    """
    Re-run the existing dedup normalization after a manual phone correction.

    If the corrected phone + normalized last name already belongs to another
    registry entry in the same org, mark this lead as duplicate of that original.
    Otherwise, update/create this lead's registry footprint.
    """
    normalized_phone = normalize_phone(lead.phone or "")
    normalized_last = normalize_last_name(lead.last_name or "")

    if not normalized_phone or not normalized_last:
        lead.is_duplicate = False
        lead.duplicate_of_lead_id = None
        return

    existing = (
        db.query(ContactRegistry)
        .filter(
            ContactRegistry.organization_id == lead.organization_id,
            ContactRegistry.normalized_phone == normalized_phone,
            ContactRegistry.normalized_last_name == normalized_last,
            ContactRegistry.first_seen_lead_id != lead.id,
        )
        .first()
    )
    if existing:
        lead.is_duplicate = True
        lead.duplicate_of_lead_id = existing.first_seen_lead_id
        return

    own_entry = (
        db.query(ContactRegistry)
        .filter(ContactRegistry.organization_id == lead.organization_id, ContactRegistry.first_seen_lead_id == lead.id)
        .first()
    )
    if own_entry:
        own_entry.normalized_phone = normalized_phone
        own_entry.normalized_last_name = normalized_last
        own_entry.owning_user_id = lead.assigned_to_id
    else:
        db.add(
            ContactRegistry(
                organization_id=lead.organization_id,
                normalized_phone=normalized_phone,
                normalized_last_name=normalized_last,
                first_seen_lead_id=lead.id,
                owning_user_id=lead.assigned_to_id,
            )
        )
    lead.is_duplicate = False
    lead.duplicate_of_lead_id = None


@router.get("/leads/potential-duplicates", response_model=list[PotentialDuplicateGroup])
def potential_duplicate_leads(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Find likely duplicate leads using two reliable signals:

    Tier 1 — PHONE match: same normalized phone number.
              Strongest signal. Same last name alone is NOT sufficient —
              common surnames like Acosta/Jones/Smith would create false
              positives. Phone is the primary dedup key.

    Tier 2 — EMAIL match: same non-empty email address (lowercased).
              Good signal; only shown for leads not already in a phone group.

    Name-only grouping (last name + year, etc.) is intentionally excluded
    — too noisy; same last name does NOT mean same person.

    Existing import-caught duplicates (Lead.is_duplicate=True) are excluded
    since they're already flagged.
    """
    leads = (
        db.query(Lead)
        .filter(
            Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
            (Lead.is_duplicate == False) | (Lead.is_duplicate.is_(None)),
        )
        .order_by(Lead.created_at.desc())
        .limit(5000)
        .all()
    )

    # Tier 1: group by normalized phone
    phone_groups: dict[str, list[Lead]] = defaultdict(list)
    # Tier 2: group by normalized email
    email_groups: dict[str, list[Lead]] = defaultdict(list)

    for lead in leads:
        phone_key = normalize_phone(lead.phone or lead.phone_raw or "")
        email_key = (lead.email or "").strip().lower()

        if phone_key:
            phone_groups[phone_key].append(lead)
        if email_key:
            email_groups[email_key].append(lead)

    results = []
    leads_in_phone_groups: set[str] = set()

    # Tier 1 — phone matches (highest confidence)
    for phone_key, group_leads in phone_groups.items():
        if len(group_leads) < 2:
            continue
        for l in group_leads:
            leads_in_phone_groups.add(l.id)
        results.append({
            "match_type": "phone",
            "match_key": phone_key,
            "leads": [_lead_summary(l) for l in group_leads],
        })

    # Tier 2 — email matches (only leads NOT already in a phone group)
    for email_key, group_leads in email_groups.items():
        new_leads = [l for l in group_leads if l.id not in leads_in_phone_groups]
        if len(new_leads) < 2:
            continue
        results.append({
            "match_type": "email",
            "match_key": email_key,
            "leads": [_lead_summary(l) for l in new_leads],
        })

    # Sort: phone first, then email
    results.sort(key=lambda r: (0 if r["match_type"] == "phone" else 1, r["match_key"]))

    return results


@router.get("/leads/flagged-duplicates")
def flagged_duplicate_leads(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Return all leads already flagged as is_duplicate=True for this org.
    These are leads caught at import time or when manually added.
    Shown in a separate section of Lead Cleanup so admins can review/delete them.
    """
    dupes = (
        db.query(Lead)
        .filter(
            Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
            Lead.is_duplicate == True,
        )
        .order_by(Lead.created_at.desc())
        .limit(1000)
        .all()
    )
    return [
        {
            "id": l.id,
            "first_name": l.first_name,
            "last_name": l.last_name,
            "phone": l.phone,
            "email": l.email,
            "tier": l.tier,
            "status": l.status,
            "source_file": l.source_file,
            "source_year": l.source_year,
            "import_list_name": l.import_list_name,
            "imported_by_name": getattr(l, "imported_by_name", None),
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in dupes
    ]


@router.post("/leads/merge", response_model=MergeLeadsResponse)
def merge_leads(
    req: MergeLeadsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Merge duplicate leads by moving history to the kept lead, then deleting the
    duplicate lead rows. All work is committed once at the end. Any error rolls
    the session back so no partial merge state is left behind.
    """
    if not req.merge_lead_ids:
        raise HTTPException(status_code=400, detail="At least one lead must be selected to merge.")
    if req.keep_lead_id in req.merge_lead_ids:
        raise HTTPException(status_code=400, detail="A lead cannot be merged into itself.")
    if len(set(req.merge_lead_ids)) != len(req.merge_lead_ids):
        raise HTTPException(status_code=400, detail="Duplicate merge lead ids are not allowed.")

    try:
        keep_lead = (
            db.query(Lead)
            .filter(Lead.id == req.keep_lead_id, Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db))
            .first()
        )
        if not keep_lead:
            raise HTTPException(status_code=404, detail="Lead to keep was not found in this organization.")

        merge_leads = (
            db.query(Lead)
            .filter(Lead.id.in_(req.merge_lead_ids), Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db))
            .all()
        )
        found_ids = {lead.id for lead in merge_leads}
        missing_ids = [lead_id for lead_id in req.merge_lead_ids if lead_id not in found_ids]
        if missing_ids:
            raise HTTPException(status_code=404, detail="One or more merge leads were not found in this organization.")

        merge_ids = [lead.id for lead in merge_leads]

        keep_has_cadence = db.query(CadenceState).filter(CadenceState.lead_id == keep_lead.id).first() is not None
        merge_cadence_states = db.query(CadenceState).filter(CadenceState.lead_id.in_(merge_ids)).all()
        if (keep_has_cadence and merge_cadence_states) or len(merge_cadence_states) > 1:
            raise HTTPException(
                status_code=409,
                detail="Cannot merge cadence history because CadenceState is one-to-one and multiple cadence records would point to the kept lead.",
            )

        moved_messages = db.query(Message).filter(Message.lead_id.in_(merge_ids)).update(
            {Message.lead_id: keep_lead.id}, synchronize_session=False
        )
        moved_replies = db.query(Reply).filter(Reply.lead_id.in_(merge_ids)).update(
            {Reply.lead_id: keep_lead.id}, synchronize_session=False
        )
        moved_outcomes = db.query(LeadOutcome).filter(LeadOutcome.lead_id.in_(merge_ids)).update(
            {LeadOutcome.lead_id: keep_lead.id}, synchronize_session=False
        )

        moved_cadence_states = 0
        for cadence_state in merge_cadence_states:
            cadence_state.lead_id = keep_lead.id
            moved_cadence_states += 1

        # Registry rows pointing at merged leads should follow the kept survivor.
        db.query(ContactRegistry).filter(ContactRegistry.first_seen_lead_id.in_(merge_ids)).update(
            {ContactRegistry.first_seen_lead_id: keep_lead.id}, synchronize_session=False
        )

        _delete_merged_lead_records(db, merge_leads)
        db.flush()
        db.commit()

        log_action(
            db, current_user.organization_id, current_user.id,
            action="lead.merge", target_type="lead", target_id=keep_lead.id,
            details={
                "kept_lead_id": keep_lead.id,
                "merged_lead_ids": merge_ids,
                "moved_messages": moved_messages,
                "moved_replies": moved_replies,
                "moved_outcomes": moved_outcomes,
                "moved_cadence_states": moved_cadence_states,
            },
        )

        return MergeLeadsResponse(
            keep_lead_id=keep_lead.id,
            merged_count=len(merge_leads),
            moved_messages=moved_messages,
            moved_replies=moved_replies,
            moved_cadence_states=moved_cadence_states,
            moved_outcomes=moved_outcomes,
            deleted_lead_ids=merge_ids,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Lead merge failed: %s", exc)
        raise HTTPException(status_code=500, detail="Lead merge failed and was rolled back. Contact support.") from exc


@router.patch("/leads/{lead_id}/fix-contact-info")
def fix_lead_contact_info(
    lead_id: str,
    req: FixContactInfoRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Correct a lead's phone/email/name while respecting org isolation and
    dedup normalization.

    Name fields were added alongside phone/email per Mike's explicit
    feedback that Lead Cleanup didn't actually let him "clean up anything"
    about a lead - a misspelled name matters here specifically, since
    duplicate-group matching (see /admin/leads/potential-duplicates) keys
    on normalized last_name. A typo'd last name could both cause a false
    duplicate match against an unrelated lead, AND prevent a real
    duplicate from being caught in the first place.
    """
    if req.phone is None and req.email is None and req.first_name is None and req.last_name is None and req.unflag_duplicate is None:
        raise HTTPException(status_code=400, detail="Provide at least one field to update.")

    lead = (
        db.query(Lead)
        .filter(Lead.id == lead_id, Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db))
        .first()
    )
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found in this organization.")

    before = {"first_name": lead.first_name, "last_name": lead.last_name, "phone": lead.phone, "email": lead.email}

    # Track whether the registry needs re-syncing - true if EITHER phone
    # or last_name changes, since the registry's match key is built from
    # both together. Re-syncing only on phone change (the original
    # behavior) left a stale registry entry any time just the name was
    # corrected.
    registry_needs_resync = False

    if req.phone is not None:
        normalized = normalize_phone(req.phone)
        if not normalized:
            raise HTTPException(status_code=400, detail="Phone could not be normalized.")
        lead.phone_raw = req.phone
        lead.phone = normalized
        registry_needs_resync = True

    if req.email is not None:
        lead.email = req.email.strip() or None

    if req.first_name is not None:
        cleaned_first = req.first_name.strip()
        lead.first_name = cleaned_first or None

    if req.last_name is not None:
        cleaned_last = req.last_name.strip()
        if not cleaned_last:
            raise HTTPException(status_code=400, detail="last_name cannot be blank.")
        lead.last_name = cleaned_last
        registry_needs_resync = True

    if req.unflag_duplicate:
        lead.is_duplicate = False
        lead.duplicate_of_lead_id = None
        if lead.status in ("dnc",):
            lead.status = "new"  # re-activate so it can enter the cadence

    if registry_needs_resync:
        _apply_contact_registry_after_contact_fix(db, lead)

    db.commit()
    db.refresh(lead)

    after = {"first_name": lead.first_name, "last_name": lead.last_name, "phone": lead.phone, "email": lead.email}
    changed = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
    if changed:
        log_action(
            db, current_user.organization_id, current_user.id,
            action="lead.fix_contact_info", target_type="lead", target_id=lead.id,
            details=changed,
        )

    return _lead_summary(lead)


# ---------------------------------------------------------------------------
# Provision Client — super_admin only
# Creates a new Organization + supervisor (org_admin) account in one shot.
# ---------------------------------------------------------------------------

class ProvisionClientRequest(BaseModel):
    org_name: str
    org_slug: str           # url-safe identifier e.g. "acme-funeral"
    industry: str = "funeral"
    plan: str = "trial"
    supervisor_full_name: str
    supervisor_email: EmailStr
    supervisor_password: str | None = None  # if omitted, a temp password is generated
    # Branding — optional at creation time
    brand_name: str | None = None
    brand_logo_url: str | None = None
    brand_color_primary: str | None = None
    brand_color_accent: str | None = None
    # Which platform the new customer belongs to. Honoured for god_admin only;
    # a super_admin always gets their own platform regardless of what is sent,
    # because provisioning onto someone else's platform is the same boundary
    # crossing as PATCH /orgs/{id}/platform by another route.
    platform_id: str | None = None


class ProvisionClientResponse(BaseModel):
    org_id: str
    org_name: str
    supervisor_id: str
    supervisor_email: str
    setup_url: str | None       # the one-time link, when we issued one
    message: str


@router.post("/provision-client", response_model=ProvisionClientResponse)
def provision_client(
    req: ProvisionClientRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    One-shot endpoint: creates the org and its first supervisor account.
    Restricted to super_admin (Mike) only.
    """
    # Slug uniqueness check
    existing = db.query(Organization).filter(Organization.slug == req.org_slug).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Slug '{req.org_slug}' is already taken.")

    # Email uniqueness check
    existing_user = db.query(User).filter(User.email == req.supervisor_email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail=f"Email '{req.supervisor_email}' is already registered.")

    # Which platform does this customer belong to?
    #
    # Before Aug 26 2026 the answer was "none" - every provisioned org was
    # created with platform_id NULL. get_platform_org_ids only ever returns
    # orgs WITH a platform, so a super_admin could provision a client and then
    # not see it in their own org list, while the record sat outside every
    # scoping decision in the system. Orphaned rows are not a boundary; they
    # are a boundary that has not been drawn yet.
    _resolved_platform_id = getattr(current_user, "platform_id", None)
    if current_user.role == "god_admin":
        _resolved_platform_id = req.platform_id  # the owner chooses, may be None
        if _resolved_platform_id:
            from app.models.models import Platform as _Plat
            if not db.query(_Plat).filter(_Plat.id == _resolved_platform_id).first():
                raise HTTPException(status_code=404, detail="Platform not found")

    # Create org
    new_org = Organization(
        name=req.org_name,
        slug=req.org_slug,
        industry=req.industry,
        plan=req.plan,
        is_active=True,
        platform_id=_resolved_platform_id,
        brand_name=req.brand_name,
        brand_logo_url=req.brand_logo_url,
        brand_color_primary=req.brand_color_primary,
        brand_color_accent=req.brand_color_accent,
    )
    db.add(new_org)
    db.flush()  # get new_org.id before creating user

    # Password — use provided or generate temp
    # No plaintext password leaves this route. An explicitly supplied one is
    # honoured and never echoed back; otherwise the account gets an unknowable
    # hash and the supervisor is reached by a one-time link.
    _issued_link = not req.supervisor_password
    raw_password = req.supervisor_password or _unknowable_password()

    new_supervisor = User(
        organization_id=new_org.id,
        email=req.supervisor_email,
        password_hash=hash_password(raw_password),
        full_name=req.supervisor_full_name,
        role="org_admin",
        is_active=True,
        # Only force reset if we auto-generated the password; if you set it, it's ready to go
        must_change_password=_issued_link,
    )
    db.add(new_supervisor)
    # Seed industry-appropriate tiers for this org
    _seed_industry_tiers(db, new_org)
    db.commit()
    db.refresh(new_org)
    db.refresh(new_supervisor)

    try:
        log_action(
            db, current_user.organization_id, current_user.id,
            action="provision_client",
            target_type="organization",
            target_id=new_org.id,
            details={
                "org_name": new_org.name,
                "org_slug": new_org.slug,
                "supervisor_email": new_supervisor.email,
                "created_by": current_user.full_name,
            },
        )
    except Exception:
        pass  # audit log failure should never block provisioning

    _provision_setup_url = None
    if _issued_link:
        _r, _raw = _activation.issue(db, new_supervisor, current_user,
                                     purpose=_PURPOSE_SETUP)
        _provision_setup_url = _activation.activation_url(
            getattr(req, 'base_url', None), _raw)
        db.commit()

    return ProvisionClientResponse(
        org_id=new_org.id,
        org_name=new_org.name,
        supervisor_id=new_supervisor.id,
        supervisor_email=new_supervisor.email,
        setup_url=_provision_setup_url,
        message=f"Client '{req.org_name}' provisioned successfully.",
    )


@router.post("/orgs/{org_id}/seed-industry-tiers")
def seed_industry_tiers_for_org(
    org_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    Seed (or back-fill) industry-appropriate TierDefinition rows for an existing org.
    Only adds tiers that don't already exist — never deletes or modifies existing tiers.
    """
    org = load_org_in_scope(db, current_user, org_id)
    _seed_industry_tiers(db, org)
    db.commit()
    industry = (org.industry or "general").lower()
    seeded = INDUSTRY_TIERS.get(industry, GENERIC_TIERS)
    return {"seeded_industry": industry, "tier_count": len(seeded), "org_id": org_id}


@router.get("/organizations")
def list_organizations(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """List orgs for the Provision Client page, scoped to the caller's platform.

    god_admin sees all; a super_admin sees only their own platform's orgs.
    Before Aug 25 2026 this returned EVERY org on EVERY platform.
    """
    allowed_ids = get_platform_org_ids(current_user, db)
    orgs = (
        db.query(Organization)
        .filter(Organization.id.in_(allowed_ids))
        .order_by(Organization.created_at.desc())
        .all()
    ) if allowed_ids else []
    return [
        {
            "id": o.id,
            "name": o.name,
            "slug": o.slug,
            "industry": o.industry,
            "plan": o.plan,
            "is_active": o.is_active,
            "brand_name": getattr(o, "brand_name", None),
            "brand_logo_url": getattr(o, "brand_logo_url", None),
            "brand_color_primary": getattr(o, "brand_color_primary", None),
            "brand_color_accent": getattr(o, "brand_color_accent", None),
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in orgs
    ]


class OrgUpdate(BaseModel):
    name: Optional[str] = None
    industry: Optional[str] = None
    plan: Optional[str] = None
    is_active: Optional[bool] = None
    brand_name: Optional[str] = None
    brand_logo_url: Optional[str] = None
    brand_color_primary: Optional[str] = None
    brand_color_accent: Optional[str] = None


@router.put("/organizations/{org_id}")
def update_organization(
    org_id: str,
    payload: OrgUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """Update an existing organization's details — super_admin, own platform only.

    A probe on Aug 26 2026 renamed a second platform's organization to
    "RENAMED BY SUPER A" through this route. It read the org id out of the URL
    and fetched it unconditionally, so every customer of every brand was
    editable by any platform operator who could guess an id.
    """
    org = load_org_in_scope(db, current_user, org_id)

    if payload.name is not None:
        org.name = payload.name.strip()
    if payload.industry is not None:
        org.industry = payload.industry
    if payload.plan is not None:
        org.plan = payload.plan
    if payload.is_active is not None:
        org.is_active = payload.is_active
    if payload.brand_name is not None:
        if hasattr(org, "brand_name"):
            org.brand_name = payload.brand_name.strip() or None
    if payload.brand_logo_url is not None:
        if hasattr(org, "brand_logo_url"):
            org.brand_logo_url = payload.brand_logo_url.strip() or None
    if payload.brand_color_primary is not None:
        if hasattr(org, "brand_color_primary"):
            org.brand_color_primary = payload.brand_color_primary
    if payload.brand_color_accent is not None:
        if hasattr(org, "brand_color_accent"):
            org.brand_color_accent = payload.brand_color_accent

    try:
        log_action(
            db, current_user.organization_id, current_user.id,
            action="org.update",
            target_type="organization",
            target_id=org_id,
            details={"updated_by": current_user.full_name, "org_name": org.name},
        )
    except Exception:
        pass

    db.commit()
    db.refresh(org)

    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "industry": org.industry,
        "plan": org.plan,
        "is_active": org.is_active,
        "brand_name": getattr(org, "brand_name", None),
        "brand_logo_url": getattr(org, "brand_logo_url", None),
        "brand_color_primary": getattr(org, "brand_color_primary", None),
        "brand_color_accent": getattr(org, "brand_color_accent", None),
    }




# ---------------------------------------------------------------------------
# Demo data seed — super_admin only. Seeds a sub-org with realistic leads,
# messages, and replies so charts/reports show meaningful data for demos.
# ---------------------------------------------------------------------------

class DemoSeedRequest(BaseModel):
    num_leads: int = 120
    days_span: int = 60

@router.post("/demo/seed/{org_id}")
def seed_demo_data(
    org_id: str,
    body: DemoSeedRequest = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """Seed a sub-account with realistic demo data. Super admin, own platform only."""
    import random, uuid as _uuid
    from datetime import datetime, timedelta
    from app.services.auth_service import hash_password
    from app.models.models import ReplyClassification

    target_org = load_org_in_scope(db, current_user, org_id)

    if body is None:
        body = DemoSeedRequest()
    num_leads = body.num_leads
    days_span = body.days_span
    now = datetime.utcnow()
    random.seed(42)

    # --- Name pools (realistic US consumer demographics) ---
    FIRST = [
        "James","Mary","Robert","Patricia","Michael","Jennifer","William","Linda",
        "David","Barbara","Richard","Elizabeth","Joseph","Susan","Thomas","Dorothy",
        "Charles","Helen","Christopher","Carol","Daniel","Ruth","Sharon","Matthew",
        "Anthony","Margaret","Mark","Betty","Donald","Sandra","Steven","Ashley",
        "Kenneth","Carolyn","Paul","Kimberly","George","Emily","Edward","Donna",
        "Brian","Michelle","Ronald","Deborah","Timothy","Stephanie","Gary","Lisa",
        "Larry","Rebecca","Frank","Virginia","Scott","Kathleen","Raymond","Amy",
        "Gregory","Angela","Benjamin","Shirley","Jerry","Emma","Samuel","Catherine",
    ]
    LAST = [
        "Smith","Johnson","Williams","Brown","Jones","Davis","Miller","Wilson",
        "Moore","Taylor","Anderson","Thomas","Jackson","White","Harris","Martin",
        "Thompson","Garcia","Martinez","Robinson","Clark","Rodriguez","Lewis","Lee",
        "Walker","Hall","Allen","Young","Hernandez","King","Wright","Lopez",
        "Hill","Scott","Green","Adams","Baker","Nelson","Carter","Mitchell",
        "Perez","Roberts","Turner","Phillips","Campbell","Parker","Evans","Edwards",
        "Collins","Stewart","Sanchez","Morris","Rogers","Reed","Cook","Morgan",
        "Bell","Murphy","Bailey","Rivera","Cooper","Richardson","Cox","Howard",
    ]

    # --- Cities with matching real area codes ---
    CITIES = [
        ("Atlanta","GA",["404","678","470"]),
        ("Houston","TX",["713","832","281"]),
        ("Phoenix","AZ",["602","480","623"]),
        ("Philadelphia","PA",["215","267"]),
        ("San Antonio","TX",["210","726"]),
        ("Dallas","TX",["214","972","469"]),
        ("Jacksonville","FL",["904"]),
        ("Columbus","OH",["614"]),
        ("Charlotte","NC",["704","980"]),
        ("Indianapolis","IN",["317"]),
        ("Memphis","TN",["901"]),
        ("Louisville","KY",["502"]),
        ("Baltimore","MD",["410","443"]),
        ("Milwaukee","WI",["414"]),
        ("Albuquerque","NM",["505"]),
        ("Tucson","AZ",["520"]),
        ("Fresno","CA",["559"]),
        ("Sacramento","CA",["916"]),
        ("Mesa","AZ",["480"]),
        ("Omaha","NE",["402"]),
        ("Cleveland","OH",["216"]),
        ("Raleigh","NC",["919"]),
        ("Virginia Beach","VA",["757"]),
        ("Colorado Springs","CO",["719"]),
        ("Aurora","CO",["303","720"]),
        ("Tampa","FL",["813"]),
        ("New Orleans","LA",["504"]),
        ("Wichita","KS",["316"]),
        ("Arlington","TX",["817"]),
        ("Bakersfield","CA",["661"]),
        ("Anaheim","CA",["714"]),
        ("Santa Ana","CA",["714"]),
        ("Corpus Christi","TX",["361"]),
        ("Riverside","CA",["951"]),
        ("St. Louis","MO",["314"]),
        ("Pittsburgh","PA",["412"]),
        ("Cincinnati","OH",["513"]),
        ("Greensboro","NC",["336"]),
        ("Anchorage","AK",["907"]),
        ("Plano","TX",["972","469"]),
    ]

    # --- Realistic email generation ---
    EMAIL_DOMAINS = [
        "gmail.com","gmail.com","gmail.com",
        "yahoo.com","yahoo.com",
        "hotmail.com","outlook.com",
        "aol.com","comcast.net","sbcglobal.net","icloud.com","bellsouth.net",
    ]

    def make_email(fn, ln, seed_i):
        domain = random.choice(EMAIL_DOMAINS)
        p = seed_i % 7
        fn_l, ln_l = fn.lower(), ln.lower()
        if p == 0: return f"{fn_l}.{ln_l}@{domain}"
        if p == 1: return f"{fn_l}{ln_l[:3]}@{domain}"
        if p == 2: return f"{fn_l[0]}{ln_l}@{domain}"
        if p == 3: return f"{ln_l}{fn_l[0]}@{domain}"
        if p == 4: return f"{fn_l}{random.randint(1945, 1985)}@{domain}"
        if p == 5: return f"{fn_l}_{ln_l}@{domain}"
        return f"{fn_l}.{ln_l}{random.randint(2, 99)}@{domain}"

    def make_phone(area_codes):
        area = random.choice(area_codes)
        exchange = random.randint(200, 989)
        subscriber = random.randint(1000, 9999)
        return f"({area}) {exchange}-{subscriber}"

    # Funeral industry tier distribution
    TIERS = (
        ["pre_need"]*55 + ["at_need"]*18 + ["imminent"]*10 +
        ["contract_sold"]*7 + ["email_only"]*10
    )
    # Realistic funnel shape
    STATUSES = (
        ["new"]*12 + ["sent"]*32 + ["replied"]*22 +
        ["hot"]*14 + ["booked"]*8 + ["dnc"]*7 + ["dead"]*5
    )
    ADVISOR_NAMES = [
        "Sarah Mitchell","James Crawford","Diana Reyes","Robert Okafor","Michelle Torres",
    ]

    # --- SMS templates (funeral/pre-need specific) ---
    MESSAGES = [
        "Hi {name}, I'm {adv} with EVOSYSPRO. Have you had a chance to think about pre-need planning for your family?",
        "Hello {name}, this is {adv} from EVOSYSPRO. Planning ahead protects your family from making difficult decisions under pressure — do you have 10 minutes this week?",
        "Hi {name}, {adv} here. We help families secure meaningful arrangements before the need arises. I'd love to share what's available in your area.",
        "Hello {name}, this is {adv} with EVOSYSPRO Pre-Planning. Many families are surprised how affordable it is to plan ahead — would you like a free overview?",
        "{name}, this is {adv}. Securing cemetery property today locks in current pricing for the future. Can I send you some information?",
        "Hi {name}, {adv} with EVOSYSPRO. I'm reaching out about final expense and pre-arrangement options available near you. Is now a good time?",
        "Hello {name}, I'm {adv}. We have a family counselor available this week for a no-pressure consultation. Would a brief call be helpful?",
        "{name}, this is {adv} from EVOSYSPRO. Our pre-need program lets you secure tomorrow's services at today's prices. Want to learn more?",
        "Hi {name}, {adv} here. Pre-planning is one of the greatest gifts you can give your family. Could we set aside 10 minutes this week?",
        "Hello {name}, I'm {adv} with EVOSYSPRO. We're offering complimentary pre-planning consultations this month — completely free, no obligation.",
    ]
    FOLLOWUPS = [
        "Hi {name}, just following up from last week. Did you have a chance to look over the information? Happy to answer any questions.",
        "{name}, {adv} again. I know things get busy — just wanted to make sure my message didn't get lost. Here whenever you're ready.",
        "Hi {name}, checking back in. A lot of families find that getting this handled brings real peace of mind. Is this a better time?",
        "{name}, this is {adv}. Our current pricing holds through end of month — wanted to make sure you had a chance to review before any changes.",
        "Hi {name}, one more quick follow-up from {adv}. No pressure at all — just want to make sure you have what you need to make a decision.",
    ]
    ADVISOR_HOT_REPLY = [
        "That's wonderful to hear, {name}! I'd love to set something up. Are mornings or afternoons generally better for you?",
        "So glad you reached out, {name}! I have availability Wednesday afternoon or Thursday morning — which works better?",
        "Perfect timing, {name}! Let me send over our pre-planning packet and we can schedule a call at your convenience.",
        "That's great news, {name}! I can give you a call tomorrow — does 2:00 PM work for you?",
        "Excellent, {name}! I'll get a time on the calendar. Would this week or next week be better?",
    ]
    LEAD_CONFIRM = [
        "Wednesday works great. See you then!",
        "Tomorrow at 2 is perfect. Thank you!",
        "Thursday morning works. 10am?",
        "Anytime this week. I'll be home most days.",
        "Next week is better. Please call Monday afternoon.",
        "That works for us. Should we bring anything?",
    ]
    HOT = [
        "Yes, I'm definitely interested. When can we talk?",
        "Please call me — I need to get this taken care of soon.",
        "I've been meaning to do this for a while. What are the next steps?",
        "My husband and I want to get this done this month. What do you need from us?",
        "We've been putting this off too long. I'm ready to move forward.",
        "Great timing — I was literally just talking to my kids about this. Can we set something up?",
        "How long does the process take? We want to get started right away.",
        "Yes please. My mother-in-law just passed and we want to make sure we're prepared.",
        "I've been thinking about this for a while. Can you call me?",
    ]
    NEUTRAL = [
        "Thank you, I'll think about it and get back to you.",
        "Not quite ready yet but please keep me in mind.",
        "Could you send some information? I'll review it when I have time.",
        "I'd like to learn more — can you email the details?",
        "We already have something in place but I'm happy to take a look at our options.",
        "My daughter usually handles these things for the family. I'll pass your information along.",
        "Maybe in the next few months. Thanks for reaching out.",
        "I appreciate it. Let me talk to my wife and I'll get back to you.",
    ]
    NEG = [
        "Not interested, please remove me from your list.",
        "We already have everything handled, thank you.",
        "Please do not contact me again.",
        "We have our arrangements made. Thank you.",
        "Please stop texting this number.",
    ]

    def rdate_span(span_days):
        days_ago = int(random.triangular(0, span_days, span_days * 0.10))
        return now - timedelta(days=days_ago)

    # --- Advisors: create up to 5 if fewer exist ---
    existing_advisors = db.query(User).filter(
        User.organization_id == org_id, User.role == "advisor"
    ).all()
    advisors = list(existing_advisors)
    created_advisor_count = max(0, 5 - len(existing_advisors))
    for i in range(created_advisor_count):
        name = ADVISOR_NAMES[i]
        fn, ln = name.split(" ", 1)
        adv = User(
            id=str(_uuid.uuid4()), organization_id=org_id,
            email=f"{fn.lower()}.{ln.lower()}@evosyspro.com",
            password_hash=hash_password("Demo1234!"),
            full_name=name, role="advisor", is_active=True, must_change_password=False,
        )
        db.add(adv)
        db.flush()
        advisors.append(adv)

    # --- Leads (batched commits every 500) ---
    leads = []
    msg_count = 0
    reply_count = 0
    outcome_count = 0
    BATCH = 500

    # Distribute leads evenly across advisors so charts look balanced
    advisor_cycle = [advisors[i % len(advisors)] for i in range(num_leads)]
    random.shuffle(advisor_cycle)

    for i in range(num_leads):
        adv = advisor_cycle[i]
        city, state, area_codes = random.choice(CITIES)
        fn = random.choice(FIRST)
        ln = random.choice(LAST)
        status = random.choice(STATUSES)
        tier = random.choice(TIERS)
        created_at = rdate_span(days_span)
        last_contact = (
            created_at + timedelta(days=random.randint(1, 14))
            if status not in ("new",) else None
        )
        lead = Lead(
            id=str(_uuid.uuid4()),
            organization_id=org_id,
            first_name=fn,
            last_name=ln,
            phone=make_phone(area_codes),
            email=make_email(fn, ln, i),
            tier=tier,
            status=status,
            source_year=random.randint(2018, 2025),
            assigned_to_id=adv.id,
            city=city,
            state=state,
            created_at=created_at,
            last_contact_date=last_contact,
        )
        db.add(lead)
        leads.append(lead)

        if (i + 1) % BATCH == 0:
            db.flush()
            db.commit()

    db.flush()
    db.commit()

    # --- Messages: outbound advisor touches ---
    msg_batch = []
    for lead in leads:
        if lead.status in ("new", "dnc", "dead"):
            continue
        adv = next((a for a in advisors if a.id == lead.assigned_to_id), advisors[0])
        adv_name = adv.full_name or "your advisor"
        if lead.status in ("hot", "booked"):
            n_msgs = random.choices([2, 3, 4], weights=[25, 50, 25])[0]
        elif lead.status == "replied":
            n_msgs = random.choices([1, 2, 3], weights=[40, 45, 15])[0]
        else:
            n_msgs = random.choices([1, 2, 3], weights=[60, 30, 10])[0]

        sent_at = lead.created_at + timedelta(hours=random.randint(1, 36))
        for touch in range(n_msgs):
            tmpl = random.choice(FOLLOWUPS if touch > 0 else MESSAGES)
            body_text = tmpl.format(name=lead.first_name, adv=adv_name)
            msg_batch.append(Message(
                id=str(_uuid.uuid4()),
                lead_id=lead.id,
                sender_id=adv.id,
                body=body_text,
                twilio_status="delivered",
                sent_at=sent_at,
            ))
            sent_at += timedelta(days=random.randint(3, 10))
            msg_count += 1

        if len(msg_batch) >= BATCH:
            db.bulk_save_objects(msg_batch)
            db.commit()
            msg_batch = []

    if msg_batch:
        db.bulk_save_objects(msg_batch)
        db.commit()
        msg_batch = []

    # --- Replies: lead responses ---
    reply_batch = []
    hot_leads_for_followup = []

    for lead in leads:
        if lead.status not in ("replied", "hot", "booked"):
            continue
        is_hot = lead.status in ("hot", "booked") or random.random() < 0.25
        is_neg = not is_hot and random.random() < 0.10
        reply_text = random.choice(HOT if is_hot else NEG if is_neg else NEUTRAL)
        clf = (
            ReplyClassification.INTERESTED if is_hot
            else ReplyClassification.NOT_INTERESTED if is_neg
            else ReplyClassification.NEUTRAL
        )
        received_at = lead.created_at + timedelta(days=random.randint(1, 14))
        reply_batch.append(Reply(
            id=str(_uuid.uuid4()),
            lead_id=lead.id,
            body=reply_text,
            source="sms",
            received_at=received_at,
            is_hot=is_hot,
            classification=clf,
        ))
        reply_count += 1

        if is_hot:
            hot_leads_for_followup.append((lead, received_at))

        if len(reply_batch) >= BATCH:
            db.bulk_save_objects(reply_batch)
            db.commit()
            reply_batch = []

    if reply_batch:
        db.bulk_save_objects(reply_batch)
        db.commit()
        reply_batch = []

    # --- Advisor follow-up messages after hot/booked replies (multi-turn threads) ---
    followup_msg_batch = []
    booked_second_reply_batch = []
    for lead, reply_received_at in hot_leads_for_followup:
        adv = next((a for a in advisors if a.id == lead.assigned_to_id), advisors[0])
        advisor_reply_text = random.choice(ADVISOR_HOT_REPLY).format(name=lead.first_name)
        advisor_reply_at = reply_received_at + timedelta(hours=random.randint(1, 6))
        followup_msg_batch.append(Message(
            id=str(_uuid.uuid4()),
            lead_id=lead.id,
            sender_id=adv.id,
            body=advisor_reply_text,
            twilio_status="delivered",
            sent_at=advisor_reply_at,
        ))
        msg_count += 1

        # Booked leads get a 2nd reply from the lead confirming the appointment
        if lead.status == "booked":
            confirm_text = random.choice(LEAD_CONFIRM)
            confirm_at = advisor_reply_at + timedelta(hours=random.randint(1, 12))
            booked_second_reply_batch.append(Reply(
                id=str(_uuid.uuid4()),
                lead_id=lead.id,
                body=confirm_text,
                source="sms",
                received_at=confirm_at,
                is_hot=True,
                classification=ReplyClassification.INTERESTED,
            ))
            reply_count += 1

        if len(followup_msg_batch) >= BATCH:
            db.bulk_save_objects(followup_msg_batch)
            db.commit()
            followup_msg_batch = []

    if followup_msg_batch:
        db.bulk_save_objects(followup_msg_batch)
        db.commit()
    if booked_second_reply_batch:
        db.bulk_save_objects(booked_second_reply_batch)
        db.commit()

    # --- Outcomes (booked leads) ---
    outcome_batch = []
    for lead in leads:
        if lead.status != "booked":
            continue
        adv = next((a for a in advisors if a.id == lead.assigned_to_id), advisors[0])
        resulted_in_sale = random.random() < 0.68
        has_arrangement = random.choices([True, False, None], weights=[60, 25, 15])[0]
        has_cemetery = random.choices([True, False, None], weights=[50, 35, 15])[0]
        appt_date = lead.created_at + timedelta(days=random.randint(5, 21))
        sale_notes = [
            "Family selected full pre-need package. Signed and funded.",
            "Couple purchased two spaces — cemetery property and arrangement package.",
            "Single pre-need arrangement finalized. Payment plan established.",
            "At-need family — services arranged within 48 hours of contact.",
            "Pre-need conversion. Family very engaged throughout the process.",
        ]
        hold_notes = [
            "Appointment held. Family reviewing package options — follow up in 2 weeks.",
            "Consultation complete. Client requested time to discuss with adult children.",
            "Good meeting. Price shopping — came in competitive. Decision pending.",
            "Family wants to include a sibling in the next call. Rescheduled.",
        ]
        outcome_batch.append(LeadOutcome(
            id=str(_uuid.uuid4()),
            lead_id=lead.id,
            recorded_by_id=adv.id,
            resulted_in_sale=resulted_in_sale,
            has_funeral_arrangement=has_arrangement,
            has_cemetery_property=has_cemetery,
            appointment_date=appt_date,
            notes=random.choice(sale_notes) if resulted_in_sale else random.choice(hold_notes),
        ))
        outcome_count += 1

    if outcome_batch:
        db.bulk_save_objects(outcome_batch)
        db.commit()

    return {
        "success": True,
        "org": target_org.name,
        "leads": num_leads,
        "messages": msg_count,
        "replies": reply_count,
        "outcomes": outcome_count,
        "advisors_created": created_advisor_count,
        "days_span": days_span,
    }


@router.delete("/demo/wipe/{org_id}")
def wipe_demo_data(
    org_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """Delete all seeded leads, messages, replies, outcomes, and demo advisors for an org.

    Super admin, own platform only. This is the most destructive route on the
    admin surface and it was the least guarded: the Aug 26 2026 probe called it
    against a second platform's organization and it returned 200 after deleting
    a lead and an advisor that did not belong to the caller's platform at all.
    """
    from sqlalchemy import delete as sql_delete
    from app.models.models import Message, Reply, LeadOutcome, CadenceState

    target_org = load_org_in_scope(db, current_user, org_id)

    # ONLY demo/sample rows. This route is called "demo wipe" and until
    # Aug 27 2026 it selected `Lead.organization_id == org_id` and nothing else
    # - every lead in the organization, real ones included - and then deleted
    # every user whose role was 'advisor'. The name promised a narrow operation
    # and the query performed a total one.
    #
    # The markers below are the ones that actually exist: the sample generator
    # stamps source_file, the demo runner prefixes ids, and an operator can flag
    # a row is_test. A lead matching none of them is a real lead and survives.
    from app.services.data_cleanup import SAMPLE_TAG, DEMO_PREFIX
    lead_ids = [
        r[0] for r in db.query(Lead.id).filter(
            Lead.organization_id == org_id,
            or_(Lead.source_file == SAMPLE_TAG,
                Lead.id.like(DEMO_PREFIX + "%"),
                Lead.is_test.is_(True)),
        ).all()
    ]

    if lead_ids:
        db.execute(sql_delete(LeadOutcome).where(LeadOutcome.lead_id.in_(lead_ids)))
        db.execute(sql_delete(Reply).where(Reply.lead_id.in_(lead_ids)))
        db.execute(sql_delete(Message).where(Message.lead_id.in_(lead_ids)))
        try:
            db.execute(sql_delete(CadenceState).where(CadenceState.lead_id.in_(lead_ids)))
        except Exception:
            pass
        # The selected ids, NOT every lead in the org. The line here used to be
        # `where(Lead.organization_id == org_id)`, which ignored the id list
        # that had just been carefully built.
        db.execute(sql_delete(Lead).where(Lead.id.in_(lead_ids)))

    # Demo advisors: ONLY ones the demo runner created, recognised by the same
    # 'demo-' id prefix it deletes by. This previously deleted every user in the
    # organization whose role was 'advisor' - which is most of a real funeral
    # home's staff, along with their logins.
    demo_advisors_deleted = (
        db.query(User)
        .filter(User.organization_id == org_id,
                User.role == "advisor",
                User.id.like(DEMO_PREFIX + "%"))
        .delete(synchronize_session=False)
    )

    db.commit()

    return {
        "success": True,
        "org": target_org.name,
        "leads_deleted": len(lead_ids),
        "demo_advisors_deleted": demo_advisors_deleted,
        "note": "Only sample/demo/test-flagged records are removed. Real leads "
                "and real staff accounts are not touched by this route.",
    }

# ---------------------------------------------------------------------------
# Org list — super admin only. Used by OrgManager.jsx to display all orgs
# with user counts. Org-admin-scoped data is handled by all other /admin/*
# endpoints via the standard require_admin filter on organization_id.
# ---------------------------------------------------------------------------

@router.get("/orgs")
def list_all_orgs(db: Session = Depends(get_db), current_user: User = Depends(require_super_admin)):
    """Returns organizations with user counts, scoped to the caller's platform.

    god_admin sees every org on every platform. A super_admin sees only the orgs
    on their own platform - a BookaBoost operator must never see EvoSys Pro orgs.
    Before Aug 25 2026 this returned EVERY org to any super_admin.
    """
    from sqlalchemy import func as sqlfunc
    allowed_ids = get_platform_org_ids(current_user, db)
    orgs = (
        db.query(Organization)
        .filter(Organization.id.in_(allowed_ids))
        .order_by(Organization.name.asc())
        .all()
    ) if allowed_ids else []
    org_ids = [o.id for o in orgs]

    user_counts = {}
    if org_ids:
        rows = (
            db.query(User.organization_id, sqlfunc.count(User.id).label("cnt"))
            .filter(User.organization_id.in_(org_ids))
            .group_by(User.organization_id)
            .all()
        )
        user_counts = {row.organization_id: row.cnt for row in rows}

    # Fetch platform names in one query for display in OrgManager
    from app.models.models import Platform
    platform_ids = [o.platform_id for o in orgs if getattr(o, "platform_id", None)]
    platforms = {}
    if platform_ids:
        for p in db.query(Platform).filter(Platform.id.in_(platform_ids)).all():
            platforms[p.id] = {"name": p.name, "slug": p.slug}

    return [
        {
            "id": o.id,
            "name": o.name,
            "slug": o.slug,
            "plan": o.plan if o.plan else None,
            "industry": o.industry if o.industry else None,
            "is_active": o.is_active,
            "brand_name": o.brand_name,
            "created_at": o.created_at,
            "user_count": user_counts.get(o.id, 0),
            "enabled_features": __import__("json").loads(o.enabled_features) if getattr(o, "enabled_features", None) else None,
            "platform_id":   getattr(o, "platform_id", None),
            "platform_name": platforms.get(o.platform_id, {}).get("name") if getattr(o, "platform_id", None) else None,
            "platform_slug": platforms.get(o.platform_id, {}).get("slug") if getattr(o, "platform_id", None) else None,
        }
        for o in orgs
    ]

# ---------------------------------------------------------------------------
# Platform list — god_admin only. Used by OrgManager to populate the
# platform assignment dropdown on each org card.
# ---------------------------------------------------------------------------

@router.get("/platforms")
def list_platforms(db: Session = Depends(get_db), current_user: User = Depends(require_super_admin)):
    """Platforms the caller may see: all of them for god_admin, their own for a
    super_admin.

    The docstring here used to say "God admin only" while the guard said
    require_super_admin and the query said "every active platform", so the
    comment was the only thing enforcing it. A BookaBoost operator could read
    the id and name of every other brand on the box.
    """
    from app.models.models import Platform
    q = db.query(Platform).filter(Platform.is_active == True)
    allowed = platform_ids_in_scope(current_user, db)
    if allowed is not None:
        if not allowed:
            return []
        q = q.filter(Platform.id.in_(allowed))
    platforms = q.order_by(Platform.name.asc()).all()
    return [{"id": p.id, "name": p.name, "slug": p.slug} for p in platforms]


# ---------------------------------------------------------------------------
# Assign an org to a platform — god_admin only.
# ---------------------------------------------------------------------------

@router.patch("/orgs/{org_id}/platform")
def set_org_platform(
    org_id: str,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_god),
):
    """Assign or unassign a platform for an org. Pass platform_id: null to unassign.

    god_admin only. The guard used to be require_super_admin while the comment
    above said god only, and this is the single most dangerous route of the set:
    it decides which platform an organization belongs to, which is the input to
    every other scoping decision in the system. A super_admin who could call it
    could move any org onto their own platform and then legitimately administer
    it - the boundary would have rewritten itself.
    """
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    platform_id = body.get("platform_id")  # None = unassign

    if platform_id is not None:
        from app.models.models import Platform
        platform = db.query(Platform).filter(Platform.id == platform_id).first()
        if not platform:
            raise HTTPException(status_code=404, detail="Platform not found")
        org.platform_id = platform_id
    else:
        org.platform_id = None

    db.commit()
    db.refresh(org)

    from app.models.models import Platform as PlatformModel
    p = db.query(PlatformModel).filter(PlatformModel.id == org.platform_id).first() if org.platform_id else None
    return {
        "id": org.id,
        "platform_id": org.platform_id,
        "platform_name": p.name if p else None,
        "platform_slug": p.slug if p else None,
    }
