"""
Campaign Router — Full rebuild.

Supports rich lead filtering, AI message generation, cadence template
assignment, and auto-reply mode.

NOTE: Lead.tier, Lead.status, Lead.contact_channel are plain VARCHAR columns.
Never use LeadTier(x) or LeadStatus(x) enum constructors in queries here.
Compare with plain strings only.
"""

import json
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin, require_tenant_user
from app.models.models import Campaign, Lead, Message, Reply, User
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

# ── Campaign purpose types — by industry ─────────────────────────────────────

CAMPAIGN_PURPOSES_BY_INDUSTRY = {
    "funeral": [
        {"value": "pre_need_outreach", "label": "Pre-Need Outreach", "desc": "First contact with pre-need leads who haven't been reached yet"},
        {"value": "at_need_followup", "label": "At-Need Follow-up", "desc": "Follow up with at-need families after initial contact"},
        {"value": "memorial_sales", "label": "Memorial Sales", "desc": "Target leads interested in pre-arranged memorial services"},
        {"value": "re_engagement", "label": "Re-engagement", "desc": "Win back leads who went cold — no reply in 30+ days"},
        {"value": "appointment_reminder", "label": "Appointment Reminder", "desc": "Remind booked leads of upcoming appointments"},
        {"value": "custom", "label": "Custom Campaign", "desc": "Define your own targeting and message"},
    ],
    "roofing": [
        {"value": "storm_damage", "label": "Storm Damage Outreach", "desc": "Reach out to leads in storm-affected areas"},
        {"value": "estimate_followup", "label": "Estimate Follow-up", "desc": "Follow up on quotes that haven't converted yet"},
        {"value": "referral_outreach", "label": "Referral Campaign", "desc": "Ask satisfied customers for referrals"},
        {"value": "re_engagement", "label": "Re-engagement", "desc": "Win back leads who went cold — no reply in 30+ days"},
        {"value": "appointment_reminder", "label": "Appointment Reminder", "desc": "Remind leads of scheduled inspections or estimates"},
        {"value": "custom", "label": "Custom Campaign", "desc": "Define your own targeting and message"},
    ],
    "insurance": [
        {"value": "policy_review", "label": "Policy Review", "desc": "Invite existing clients to review and update their coverage"},
        {"value": "new_client_outreach", "label": "New Client Outreach", "desc": "First contact with fresh prospects"},
        {"value": "referral_outreach", "label": "Referral Campaign", "desc": "Ask satisfied clients for referrals"},
        {"value": "re_engagement", "label": "Re-engagement", "desc": "Win back leads who went cold — no reply in 30+ days"},
        {"value": "appointment_reminder", "label": "Appointment Reminder", "desc": "Remind leads of upcoming consultations"},
        {"value": "custom", "label": "Custom Campaign", "desc": "Define your own targeting and message"},
    ],
    "real_estate": [
        {"value": "buyer_outreach", "label": "Buyer Outreach", "desc": "Reach leads looking to purchase a home"},
        {"value": "seller_outreach", "label": "Seller Outreach", "desc": "Target homeowners who may want to list"},
        {"value": "listing_followup", "label": "Listing Follow-up", "desc": "Follow up on leads who viewed a listing"},
        {"value": "re_engagement", "label": "Re-engagement", "desc": "Win back leads who went cold — no reply in 30+ days"},
        {"value": "appointment_reminder", "label": "Showing Reminder", "desc": "Remind leads of upcoming showings"},
        {"value": "custom", "label": "Custom Campaign", "desc": "Define your own targeting and message"},
    ],
    "dental": [
        {"value": "new_patient", "label": "New Patient Outreach", "desc": "Welcome new patient leads and invite them in"},
        {"value": "recall", "label": "Recall Campaign", "desc": "Bring back patients overdue for a cleaning or checkup"},
        {"value": "treatment_followup", "label": "Treatment Follow-up", "desc": "Follow up on patients with pending treatment plans"},
        {"value": "re_engagement", "label": "Re-engagement", "desc": "Win back patients who haven't visited in 12+ months"},
        {"value": "appointment_reminder", "label": "Appointment Reminder", "desc": "Remind patients of upcoming appointments"},
        {"value": "custom", "label": "Custom Campaign", "desc": "Define your own targeting and message"},
    ],
    "home_services": [
        {"value": "seasonal_outreach", "label": "Seasonal Outreach", "desc": "Promote seasonal services to your lead list"},
        {"value": "estimate_followup", "label": "Estimate Follow-up", "desc": "Follow up on quotes that haven't converted"},
        {"value": "referral_outreach", "label": "Referral Campaign", "desc": "Ask satisfied customers for referrals"},
        {"value": "re_engagement", "label": "Re-engagement", "desc": "Win back leads who went cold — no reply in 30+ days"},
        {"value": "appointment_reminder", "label": "Appointment Reminder", "desc": "Remind leads of upcoming service visits"},
        {"value": "custom", "label": "Custom Campaign", "desc": "Define your own targeting and message"},
    ],
    "custom": [
        {"value": "general_outreach", "label": "General Outreach", "desc": "Reach out to leads with a custom message"},
        {"value": "re_engagement", "label": "Re-engagement", "desc": "Win back leads who went cold — no reply in 30+ days"},
        {"value": "appointment_reminder", "label": "Appointment Reminder", "desc": "Remind leads of upcoming appointments"},
        {"value": "custom", "label": "Custom Campaign", "desc": "Define your own targeting and message"},
    ],
}

CAMPAIGN_PURPOSES = CAMPAIGN_PURPOSES_BY_INDUSTRY["custom"]


# ── Rich filter helper ────────────────────────────────────────────────────────

def _apply_filters(query, organization_id: str, criteria: dict):
    """Apply all filter criteria to a Lead query. All comparisons use plain strings."""
    query = query.filter(Lead.organization_id == organization_id, Lead.is_duplicate == False)

    if criteria.get("tier"):
        query = query.filter(Lead.tier == criteria["tier"])

    if criteria.get("status"):
        query = query.filter(Lead.status == criteria["status"])

    if criteria.get("source_year"):
        query = query.filter(Lead.source_year == int(criteria["source_year"]))

    if criteria.get("source_year_min"):
        query = query.filter(Lead.source_year >= int(criteria["source_year_min"]))

    if criteria.get("source_year_max"):
        query = query.filter(Lead.source_year <= int(criteria["source_year_max"]))

    if criteria.get("message_track") or criteria.get("lead_type"):
        track = criteria.get("message_track") or criteria.get("lead_type")
        query = query.filter(Lead.message_track == track)

    if criteria.get("engagement_temperature"):
        query = query.filter(Lead.engagement_temperature == criteria["engagement_temperature"])

    if criteria.get("source_file"):
        query = query.filter(Lead.source_file.ilike(f"%{criteria['source_file']}%"))

    if criteria.get("import_list_name"):
        query = query.filter(Lead.import_list_name == criteria["import_list_name"])

    if criteria.get("relationship_type"):
        query = query.filter(Lead.relationship_type == criteria["relationship_type"])

    if criteria.get("channel"):
        query = query.filter(Lead.contact_channel == criteria["channel"])

    if criteria.get("advisor_id"):
        query = query.filter(Lead.assigned_to_id == criteria["advisor_id"])

    # Contact history filters
    contact_history = criteria.get("contact_history")
    if contact_history == "never_contacted":
        contacted_ids = query.session.query(Message.lead_id).distinct()
        query = query.filter(~Lead.id.in_(contacted_ids))
    elif contact_history == "contacted_no_reply":
        has_message_ids = query.session.query(Message.lead_id).distinct()
        has_reply_ids = query.session.query(Reply.lead_id).distinct()
        query = query.filter(Lead.id.in_(has_message_ids), ~Lead.id.in_(has_reply_ids))
    elif contact_history == "replied_not_booked":
        has_reply_ids = query.session.query(Reply.lead_id).distinct()
        query = query.filter(Lead.id.in_(has_reply_ids), Lead.status != "booked")

    # Always exclude DNC
    query = query.filter(Lead.status != "dnc")

    # Always exclude manually flagged leads from campaign targeting
    # bad_email leads are excluded from email campaigns but allowed for SMS
    channel = criteria.get("channel", "sms")
    if channel == "email":
        # Exclude both bad_email and remove_all flagged leads from email campaigns
        query = query.filter(Lead.manual_flag == None)
    else:
        # For SMS/auto, only exclude remove_all flagged leads
        query = query.filter(
            (Lead.manual_flag == None) | (Lead.manual_flag == "bad_email")
        )

    return query


def _compliance_check(lead: Lead, channel: str = "sms") -> tuple[bool, str]:
    """
    Inline compliance gate — returns (ok, reason).
    A lead that fails this must never be sent to, no exceptions.
    channel: "sms" | "email" | "auto"
    """
    if lead.status == "dnc":
        return False, "DNC"
    if lead.is_duplicate:
        return False, "duplicate"
    # Manual flag gate — remove_all blocks all channels; bad_email blocks email only
    if getattr(lead, "manual_flag", None) == "remove_all":
        return False, "manually flagged — removed from all outreach"
    if getattr(lead, "manual_flag", None) == "bad_email" and channel in ("email", "auto"):
        return False, "manually flagged — bad email"
    # Channel-specific checks
    if channel in ("sms", "auto"):
        if lead.contact_channel != "email_only" and (not lead.phone or lead.phone.strip() == ""):
            return False, "no phone"
    if channel == "sms" and lead.contact_channel == "email_only":
        return False, "email_only lead — use email channel"
    if channel == "email" and not lead.email:
        return False, "no email"
    return True, ""


# ── AI message generation ─────────────────────────────────────────────────────

_OFFER_HOOK_LABELS = {
    "lunch_and_learn": "Lunch & Learn event (invite them — no pressure, just educational)",
    "free_tour": "Free funeral home tour (low-commitment, educational visit)",
    "free_space": "Complimentary cemetery space consultation",
    "family_service_consult": "Free Family Service consultation",
}

_RELATIONSHIP_TONE_GUIDANCE = {
    "cold_lead": (
        "This is a cold lead with no prior relationship. Open very softly — no pressure, "
        "no assumptions. Introduce yourself briefly and make the ask feel like a gentle offer, "
        "not a pitch. If there is an offer hook, present it as a low-key invitation."
    ),
    "warm_lead": (
        "This is a warm lead who has shown some prior interest. Be friendly and direct. "
        "You can reference their interest or the topic without being pushy."
    ),
    "re_engagement": (
        "This lead has gone quiet. Acknowledge the gap lightly and offer a fresh, simple "
        "reason to reconnect. Keep it easy and no-pressure."
    ),
    "previous_prospect": (
        "This lead was a prospect before but didn't convert. Re-open the conversation "
        "naturally — don't reference failure, just offer a new reason to reconnect."
    ),
    "past_customer": (
        "This is a past customer. You can be warmer and more personal. Reference the prior "
        "relationship naturally and invite them back or check in on how things are going."
    ),
    "existing_customer": (
        "This is an active existing customer. The tone should be personal and appreciative. "
        "This might be a check-in, an upgrade offer, or a referral ask — keep it warm."
    ),
}


def _generate_campaign_message(
    purpose: str, tone: str, org_name: str, advisor_name: str,
    lead_type: str = None, ai_direction: str = None, industry: str = "funeral",
    offer_hook: str = None, relationship_type: str = None,
) -> str:
    """Generate an AI opening message for this campaign."""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    all_purposes = CAMPAIGN_PURPOSES_BY_INDUSTRY.get(industry, CAMPAIGN_PURPOSES_BY_INDUSTRY["custom"])
    purpose_label = next((p["label"] for p in all_purposes if p["value"] == purpose), purpose)

    tone_map = {
        "cold": "soft and low-pressure, this is likely a first introduction",
        "warm": "friendly and inviting, suggest a conversation",
        "hot": "direct and confident, clear call to action",
        "urgent": "brief and time-sensitive, make it clear this needs a response soon",
    }
    tone_desc = tone_map.get(tone, "warm and professional")

    lead_type_line = f"\nLead type/track: {lead_type}" if lead_type else ""
    direction_line = f"\nSpecific direction from advisor: {ai_direction}" if ai_direction else ""

    # Offer hook line
    offer_desc = _OFFER_HOOK_LABELS.get(offer_hook) if offer_hook else None
    if offer_desc:
        offer_line = f"\nOffer hook (weave in naturally, low-pressure): {offer_desc}"
    elif offer_hook and offer_hook not in ("", "none"):
        offer_line = f"\nOffer hook: {offer_hook}"
    else:
        offer_line = ""

    # Relationship-type tone calibration (Task 115)
    rel_guidance = _RELATIONSHIP_TONE_GUIDANCE.get(relationship_type, "") if relationship_type else ""
    rel_line = f"\nRelationship context: {rel_guidance}" if rel_guidance else ""

    prompt = f"""Write a short SMS outreach message for a campaign.

Business: {org_name}
Advisor: {advisor_name}
Campaign type: {purpose_label}
Tone: {tone_desc}{lead_type_line}{direction_line}{offer_line}{rel_line}

Rules:
- Under 320 characters total
- Sound human, not like a mass template
- Use {{first_name}} as the lead's first name placeholder
- If it makes sense for this campaign type, end with {{booking_url}}
- No hashtags, no all-caps, no emojis unless completely natural
- If an offer hook is provided, weave it in as a soft option — don't make it the entire message
- Respond with ONLY the message text, nothing else

Write the message:"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.65,
            max_tokens=130,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return f"Hi {{first_name}}, this is {advisor_name} with {org_name}. I'd love to connect and see how I can help. {{booking_url}}"


# ── Pydantic models ───────────────────────────────────────────────────────────

class CampaignFilterCriteria(BaseModel):
    tier: Optional[str] = None
    status: Optional[str] = None
    source_year: Optional[int] = None
    source_year_min: Optional[int] = None
    source_year_max: Optional[int] = None
    source_file: Optional[str] = None
    channel: Optional[str] = None
    advisor_id: Optional[str] = None
    contact_history: Optional[str] = None
    message_track: Optional[str] = None
    engagement_temperature: Optional[str] = None
    lead_type: Optional[str] = None
    contractor_type: Optional[str] = None


class CampaignCreate(BaseModel):
    name: str
    purpose: str = "custom"
    filter_criteria: CampaignFilterCriteria = CampaignFilterCriteria()
    message_track: Optional[str] = None
    cadence_template_id: Optional[str] = None
    tone: str = "warm"
    auto_reply: bool = False


class CampaignBuildPreview(BaseModel):
    filter_criteria: CampaignFilterCriteria
    purpose: str = "custom"
    tone: str = "warm"


class CampaignSend(BaseModel):
    campaign_id: str
    message: str
    start_cadence: bool = False
    cadence_template_id: Optional[str] = None
    auto_reply: bool = False


class GenerateMessageRequest(BaseModel):
    purpose: str
    tone: str = "warm"
    lead_type: Optional[str] = None
    ai_direction: Optional[str] = None
    offer_hook: Optional[str] = None


# ── Endpoints — specific before wildcard ──────────────────────────────────────

@router.get("/purposes")
def get_purposes(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    industry = (org.industry if org else None) or "funeral"
    return CAMPAIGN_PURPOSES_BY_INDUSTRY.get(industry, CAMPAIGN_PURPOSES_BY_INDUSTRY["custom"])


@router.post("/generate-message")
def generate_message(
    req: GenerateMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """AI generates an opening campaign message based on purpose and tone."""
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    org_name = org.name if org else "our organization"
    industry = (org.industry if org else None) or "funeral"
    advisor_name = current_user.full_name or "your advisor"

    message = _generate_campaign_message(
        req.purpose, req.tone, org_name, advisor_name,
        lead_type=req.lead_type, ai_direction=req.ai_direction, industry=industry,
        offer_hook=req.offer_hook,
    )
    return {"message": message, "purpose": req.purpose, "tone": req.tone}


@router.post("/preview")
def preview_campaign_leads(
    req: CampaignBuildPreview,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Preview which leads match the filter criteria."""
    criteria = req.filter_criteria.dict(exclude_none=True)
    query = _apply_filters(db.query(Lead), current_user.organization_id, criteria)
    total = query.count()
    sample = query.limit(10).all()

    return {
        "total_matched": total,
        "criteria": criteria,
        "sample": [
            {
                "id": l.id,
                "name": f"{l.first_name or ''} {l.last_name or ''}".strip(),
                "phone": l.phone,
                "tier": l.tier,
                "status": l.status,
                "source_file": l.source_file,
                "source_year": l.source_year,
            }
            for l in sample
        ],
    }


@router.get("/history")
def get_campaign_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Return past campaigns with stats for the Campaign Builder history tab."""
    campaigns = (
        db.query(Campaign)
        .filter(Campaign.organization_id == current_user.organization_id)
        .order_by(Campaign.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": c.id,
            "name": c.name,
            "purpose": getattr(c, "purpose", None),
            "tone": getattr(c, "tone", None),
            "status": getattr(c, "status", "draft"),
            "sent_count": getattr(c, "sent_count", 0) or 0,
            "skipped_count": getattr(c, "skipped_count", 0) or 0,
            "error_count": getattr(c, "error_count", 0) or 0,
            "sent_at": getattr(c, "sent_at", None),
            "created_at": c.created_at,
            "filter_criteria": json.loads(c.filter_criteria) if c.filter_criteria else {},
        }
        for c in campaigns
    ]


@router.post("")
def create_campaign(
    payload: CampaignCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    campaign = Campaign(
        id=str(uuid.uuid4()),
        organization_id=current_user.organization_id,
        name=payload.name,
        created_by_id=current_user.id,
        filter_criteria=json.dumps(payload.filter_criteria.dict(exclude_none=True)),
        message_track=payload.message_track,
        created_at=datetime.utcnow(),
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    log_action(db, current_user.organization_id, current_user.id, action="campaign.create", target_type="campaign", target_id=campaign.id)
    return {"id": campaign.id, "name": campaign.name}


@router.get("")
def list_campaigns(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    campaigns = (
        db.query(Campaign)
        .filter(Campaign.organization_id == current_user.organization_id)
        .order_by(Campaign.created_at.desc())
        .limit(500)
        .all()
    )
    return [
        {
            "id": c.id,
            "name": c.name,
            "created_at": c.created_at,
            "message_track": c.message_track,
            "filter_criteria": json.loads(c.filter_criteria) if c.filter_criteria else {},
        }
        for c in campaigns
    ]


@router.post("/{campaign_id}/send")
def send_campaign(
    campaign_id: str,
    req: CampaignSend,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Execute the campaign — send messages to all matched leads."""
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.organization_id == current_user.organization_id,
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    criteria = json.loads(campaign.filter_criteria) if campaign.filter_criteria else {}
    query = _apply_filters(db.query(Lead), current_user.organization_id, criteria)
    leads = query.all()

    from app.services.sms_service import send_sms
    from app.services.cadence_service import start_cadence

    sent = 0
    skipped = 0
    errors = 0

    for lead in leads:
        ok, reason = _compliance_check(lead)
        if not ok:
            skipped += 1
            continue
        try:
            name = lead.first_name or "there"
            personalized = req.message.replace("{first_name}", name)
            send_sms(db=db, lead=lead, advisor=current_user, template=personalized, include_booking_link=True)
            if req.start_cadence:
                start_cadence(db, lead)
            sent += 1
        except Exception:
            errors += 1

    log_action(db, current_user.organization_id, current_user.id, action="campaign.send", target_type="campaign", target_id=campaign_id)
    return {"sent": sent, "skipped": skipped, "errors": errors, "total": len(leads)}


# ── Campaign Builder endpoints ────────────────────────────────────────────────

class BuilderPreviewRequest(BaseModel):
    tier: Optional[str] = None
    status: Optional[str] = None
    source_year_min: Optional[int] = None
    source_year_max: Optional[int] = None
    assigned_to_id: Optional[str] = None
    no_contact_days: Optional[int] = None
    has_phone: bool = True
    exclude_dnc: bool = True
    exclude_duplicates: bool = True
    lead_type: Optional[str] = None
    engagement_temperature: Optional[str] = None
    contact_history: Optional[str] = None
    import_list_name: Optional[str] = None
    relationship_type: Optional[str] = None
    channel: Optional[str] = None  # "sms" | "email" | "auto"


class BuilderSendRequest(BaseModel):
    name: str
    purpose: Optional[str] = "custom"
    tone: Optional[str] = "warm"
    message_template: str
    include_booking_link: bool = True
    lead_ids: list[str]
    filters: Optional[dict] = None
    ai_direction: Optional[str] = None
    offer_hook: Optional[str] = None   # e.g. "lunch_and_learn", "free_tour", etc.
    channel: Optional[str] = "sms"    # "sms" | "email" | "auto"
    schedule_type: str = "now"
    scheduled_at: Optional[str] = None


@router.get("/builder/preview")
def builder_preview(
    tier: Optional[str] = None,
    status: Optional[str] = None,
    source_year_min: Optional[int] = None,
    source_year_max: Optional[int] = None,
    assigned_to_id: Optional[str] = None,
    no_contact_days: Optional[int] = None,
    has_phone: bool = True,
    exclude_dnc: bool = True,
    exclude_duplicates: bool = True,
    lead_type: Optional[str] = None,
    engagement_temperature: Optional[str] = None,
    contact_history: Optional[str] = None,
    import_list_name: Optional[str] = None,
    relationship_type: Optional[str] = None,
    channel: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Preview leads matching the Campaign Builder filters. Returns full lead list. Open to all advisors."""
    is_manager = current_user.role in ("org_admin", "super_admin", "god_admin")
    criteria = {}
    if tier: criteria["tier"] = tier
    if status: criteria["status"] = status
    if source_year_min: criteria["source_year_min"] = source_year_min
    if source_year_max: criteria["source_year_max"] = source_year_max
    if assigned_to_id: criteria["advisor_id"] = assigned_to_id
    if lead_type: criteria["lead_type"] = lead_type
    if engagement_temperature: criteria["engagement_temperature"] = engagement_temperature
    if contact_history: criteria["contact_history"] = contact_history
    if import_list_name: criteria["import_list_name"] = import_list_name
    if relationship_type: criteria["relationship_type"] = relationship_type
    if channel and channel != "auto": criteria["channel"] = "email_only" if channel == "email" else channel

    query = _apply_filters(db.query(Lead), current_user.organization_id, criteria)

    # Advisors can only preview their own leads; managers see all
    if not is_manager:
        query = query.filter(Lead.assigned_to_id == current_user.id)

    if has_phone and channel not in ("email", "email_only"):
        query = query.filter(Lead.phone.isnot(None), Lead.phone != "")
    if exclude_dnc:
        query = query.filter(Lead.status != "dnc")
    if exclude_duplicates:
        query = query.filter(Lead.is_duplicate == False)
    if no_contact_days:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=no_contact_days)
        contacted_recent = db.query(Message.lead_id).filter(Message.sent_at >= cutoff).distinct()
        query = query.filter(~Lead.id.in_(contacted_recent))

    leads = query.order_by(Lead.last_name.asc(), Lead.first_name.asc()).limit(5000).all()

    return [
        {
            "id": l.id,
            "first_name": l.first_name,
            "last_name": l.last_name,
            "phone": l.phone,
            "email": l.email,
            "tier": l.tier,
            "status": l.status,
            "source_year": l.source_year,
            "source_file": l.source_file,
            "message_track": l.message_track,
            "assigned_to_name": l.assigned_to.full_name if l.assigned_to else None,
        }
        for l in leads
    ]


@router.post("/builder/send")
def builder_send(
    req: BuilderSendRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Execute Campaign Builder send — open to all advisors.
    Creates a campaign record, sends to all lead_ids provided,
    and writes the results back to the campaign row for history.
    Supports channel: "sms" (default), "email", or "auto" (routes by lead's contact_channel).
    """
    from app.services.sms_service import send_sms

    now = datetime.utcnow()

    # Create campaign record for history
    campaign = Campaign(
        id=str(uuid.uuid4()),
        organization_id=current_user.organization_id,
        name=req.name,
        created_by_id=current_user.id,
        filter_criteria=json.dumps(req.filters or {}),
        created_at=now,
    )
    # Set extra stats columns if they exist (added by auto_migrate)
    for attr, val in [
        ("purpose", req.purpose or "custom"),
        ("tone", req.tone or "warm"),
        ("ai_direction", req.ai_direction),
        ("status", "sending"),
        ("sent_at", now),
    ]:
        if hasattr(campaign, attr):
            setattr(campaign, attr, val)

    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    # Fetch the leads
    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids),
        Lead.organization_id == current_user.organization_id,
    ).all()

    sent = 0
    skipped = 0
    errors = 0
    error_details = []

    req_channel = req.channel or "sms"

    for lead in leads:
        # Determine effective channel for this lead
        if req_channel == "auto":
            effective_channel = "email" if lead.contact_channel == "email_only" else "sms"
        else:
            effective_channel = req_channel

        ok, reason = _compliance_check(lead, channel=effective_channel)
        if not ok:
            skipped += 1
            continue
        try:
            name = lead.first_name or "there"
            personalized = (req.message_template
                .replace("{first_name}", name)
                .replace("{advisor_name}", current_user.full_name or "")
                .replace("{booking_url}", ""))

            if effective_channel == "email":
                # Send via email — use the same provider logic as email router
                subject = f"Following up, {lead.first_name or 'there'}"
                body_html = personalized.replace("\n", "<br>")
                if current_user.microsoft_365_connected:
                    from app.services.microsoft_email_service import send_email_via_microsoft_graph
                    result = send_email_via_microsoft_graph(current_user, lead.email, subject, body_html)
                else:
                    from app.services.email_service import send_email_via_provider
                    result = send_email_via_provider(lead.email, subject, body_html)
                if not result.get("success"):
                    raise Exception(result.get("error", "Email send failed"))
                from app.models.models import EmailMessage
                from datetime import datetime as _dt
                db.add(EmailMessage(
                    lead_id=lead.id,
                    sender_id=current_user.id,
                    subject=subject,
                    body_html=body_html,
                    status="sent",
                    provider_message_id=result.get("provider_message_id"),
                    sent_at=_dt.utcnow(),
                ))
                lead.status = "sent"
                db.flush()
            else:
                send_sms(
                    db=db,
                    lead=lead,
                    advisor=current_user,
                    template=personalized,
                    include_booking_link=req.include_booking_link,
                )
            sent += 1
        except Exception as e:
            errors += 1
            error_details.append({"lead_id": lead.id, "error": str(e)})

    # Write results back to campaign row
    for attr, val in [
        ("sent_count", sent),
        ("skipped_count", skipped),
        ("error_count", errors),
        ("status", "sent"),
    ]:
        if hasattr(campaign, attr):
            setattr(campaign, attr, val)
    db.commit()

    log_action(db, current_user.organization_id, current_user.id, action="campaign.builder_send", target_type="campaign", target_id=campaign.id)

    return {
        "campaign_id": campaign.id,
        "sent": sent,
        "skipped": skipped,
        "errors": errors,
        "total": len(leads),
        "error_details": error_details[:5],
    }
