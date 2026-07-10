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
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.deps import get_db, get_current_user, require_admin
from app.models.models import Campaign, Lead, Message, Reply, User
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

# ── Campaign purpose types ────────────────────────────────────────────────────

CAMPAIGN_PURPOSES = [
    {"value": "memorial_sales", "label": "Memorial Sales", "desc": "Target leads who may be interested in pre-arranged memorial services"},
    {"value": "pre_need_outreach", "label": "Pre-Need Outreach", "desc": "First contact with pre-need leads who haven't been reached yet"},
    {"value": "at_need_followup", "label": "At-Need Follow-up", "desc": "Follow up with at-need families after initial contact"},
    {"value": "re_engagement", "label": "Re-engagement", "desc": "Win back leads who went cold — no reply in 30+ days"},
    {"value": "upsell_existing", "label": "Upsell Existing", "desc": "Reach existing customers with upgrades or additional services"},
    {"value": "appointment_reminder", "label": "Appointment Reminder", "desc": "Remind booked leads of upcoming appointments"},
    {"value": "custom", "label": "Custom Campaign", "desc": "Define your own targeting and message"},
]

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

    # Exclude DNC always
    query = query.filter(Lead.status != "dnc")

    return query


# ── AI message generation ─────────────────────────────────────────────────────

def _generate_campaign_message(purpose: str, tone: str, org_name: str, advisor_name: str, lead_type: str = None, ai_direction: str = None) -> str:
    """Generate an AI opening message for this campaign."""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    purpose_label = next((p["label"] for p in CAMPAIGN_PURPOSES if p["value"] == purpose), purpose)
    tone_map = {
        "cold": "soft and low-pressure, just an introduction",
        "warm": "friendly and inviting, suggest a conversation",
        "hot": "direct and confident, clear call to action",
        "urgent": "brief and urgent, time-sensitive",
    }
    tone_desc = tone_map.get(tone, "warm and professional")

    lead_type_line = f"\nLead type: {lead_type}" if lead_type else ""
    direction_line = f"\nSpecific direction: {ai_direction}" if ai_direction else ""

    prompt = f"""Write a short SMS outreach message for a campaign.

Business: {org_name}
Advisor: {advisor_name}
Campaign type: {purpose_label}
Tone: {tone_desc}{lead_type_line}{direction_line}

Rules:
- Under 320 characters
- Sound human, not like a template
- Use {{first_name}} as a placeholder for the lead's first name
- End with {{booking_url}} if asking for an appointment
- No hashtags, no all-caps, no emojis unless natural
- Respond with ONLY the message text, nothing else

Write the message:"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=120,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return f"Hi {{first_name}}, this is {advisor_name} with {org_name}. I'd love to connect and see how we can help. {{booking_url}}"


# ── Pydantic models ───────────────────────────────────────────────────────────

class CampaignFilterCriteria(BaseModel):
    tier: Optional[str] = None
    status: Optional[str] = None
    source_year: Optional[int] = None
    source_year_min: Optional[int] = None   # year range start
    source_year_max: Optional[int] = None   # year range end
    source_file: Optional[str] = None
    channel: Optional[str] = None
    advisor_id: Optional[str] = None
    contact_history: Optional[str] = None  # never_contacted | contacted_no_reply | replied_not_booked
    message_track: Optional[str] = None    # file_check | code_lead | new_inquiry | referral | web_lead
    engagement_temperature: Optional[str] = None  # hot | warm | cold | unknown
    lead_type: Optional[str] = None        # alias for message_track - used in UI
    contractor_type: Optional[str] = None  # for non-funeral orgs: roofing, insurance, etc.


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


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/purposes")
def get_purposes():
    return CAMPAIGN_PURPOSES


@router.post("/generate-message")
def generate_message(
    req: GenerateMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI generates an opening campaign message based on purpose and tone."""
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    org_name = org.name if org else "our organization"
    advisor_name = current_user.full_name or "your advisor"

    message = _generate_campaign_message(req.purpose, req.tone, org_name, advisor_name, lead_type=req.lead_type, ai_direction=req.ai_direction)
    return {"message": message, "purpose": req.purpose, "tone": req.tone}


@router.post("/preview")
def preview_campaign_leads(
    req: CampaignBuildPreview,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
    log_action(db, current_user, action="campaign.create", target_type="campaign", target_id=campaign.id)
    return {"id": campaign.id, "name": campaign.name}


@router.get("")
def list_campaigns(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaigns = (
        db.query(Campaign)
        .filter(Campaign.organization_id == current_user.organization_id)
        .order_by(Campaign.created_at.desc())
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
        try:
            if lead.contact_channel == "email_only" or not lead.phone:
                skipped += 1
                continue
            # Personalize message
            name = lead.first_name or "there"
            personalized = req.message.replace("{first_name}", name)
            send_sms(db=db, lead=lead, advisor=current_user, template=personalized, include_booking_link=True)
            if req.start_cadence:
                start_cadence(db, lead)
            sent += 1
        except Exception:
            errors += 1

    log_action(db, current_user, action="campaign.send", target_type="campaign", target_id=campaign_id)
    return {"sent": sent, "skipped": skipped, "errors": errors, "total": len(leads)}


# ── Campaign Builder endpoints (used by CampaignBuilder.jsx) ─────────────────
# The builder UI calls /campaigns/builder/preview and /campaigns/builder/send.
# These are the entry points that actually power the Campaign Builder wizard.

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


class BuilderSendRequest(BaseModel):
    name: str
    message_template: str
    include_booking_link: bool = True
    lead_ids: list[str]
    filters: Optional[dict] = None
    ai_direction: Optional[str] = None
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Preview leads matching the Campaign Builder filters. Returns full lead list."""
    criteria = {}
    if tier: criteria["tier"] = tier
    if status: criteria["status"] = status
    if source_year_min: criteria["source_year_min"] = source_year_min
    if source_year_max: criteria["source_year_max"] = source_year_max
    if assigned_to_id: criteria["advisor_id"] = assigned_to_id
    if lead_type: criteria["lead_type"] = lead_type
    if engagement_temperature: criteria["engagement_temperature"] = engagement_temperature
    if contact_history: criteria["contact_history"] = contact_history

    query = _apply_filters(db.query(Lead), current_user.organization_id, criteria)

    if has_phone:
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

    leads = query.order_by(Lead.last_name.asc(), Lead.first_name.asc()).all()

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
    current_user: User = Depends(get_current_user),
):
    """
    Execute Campaign Builder send — any advisor can run this.
    Creates a campaign record, then sends to all lead_ids provided.
    """
    from app.services.sms_service import send_sms

    # Create campaign record for history
    campaign = Campaign(
        id=str(uuid.uuid4()),
        organization_id=current_user.organization_id,
        name=req.name,
        created_by_id=current_user.id,
        filter_criteria=json.dumps(req.filters or {}),
        created_at=datetime.utcnow(),
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    # Fetch the leads
    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids),
        Lead.organization_id == current_user.organization_id,
        Lead.is_duplicate == False,
        Lead.status != "dnc",
    ).all()

    sent = 0
    skipped = 0
    errors = 0
    error_details = []

    for lead in leads:
        try:
            if not lead.phone and lead.contact_channel != "email_only":
                skipped += 1
                continue
            if lead.contact_channel == "email_only":
                skipped += 1
                continue

            # Personalize
            name = lead.first_name or "there"
            personalized = (req.message_template
                .replace("{first_name}", name)
                .replace("{advisor_name}", current_user.full_name or "")
                .replace("{booking_url}", ""))

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

    log_action(db, current_user, action="campaign.builder_send", target_type="campaign", target_id=campaign.id)

    return {
        "campaign_id": campaign.id,
        "sent": sent,
        "skipped": skipped,
        "errors": errors,
        "total": len(leads),
        "error_details": error_details[:5],  # first 5 errors for debugging
    }
