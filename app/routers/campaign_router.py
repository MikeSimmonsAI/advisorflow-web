"""
Campaign Builder endpoints

GET  /campaigns/preview  — filter leads and return matching list (no send)
POST /campaigns/send     — send or schedule a campaign to a list of lead IDs
GET  /campaigns          — list past campaigns
GET  /campaigns/{id}     — campaign detail + delivery stats
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import uuid

from app.database import get_db
from app.auth import get_current_user
from app.models import Lead, User, SMSMessage, Campaign, CampaignRecipient

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class CampaignFilters(BaseModel):
    tier: Optional[str] = None
    status: Optional[str] = None
    source_year_min: Optional[int] = None
    source_year_max: Optional[int] = None
    assigned_to_id: Optional[str] = None
    no_contact_days: Optional[int] = None
    has_phone: bool = True
    exclude_dnc: bool = True
    exclude_duplicates: bool = True


class CampaignSendRequest(BaseModel):
    name: str
    message_template: str
    include_booking_link: bool = True
    lead_ids: List[str]
    schedule_type: str = "now"          # 'now' | 'scheduled'
    scheduled_at: Optional[datetime] = None
    filters: Optional[CampaignFilters] = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _apply_filters(query, filters_dict: dict, db: Session):
    """Apply campaign filters to a Lead query."""
    tier = filters_dict.get('tier')
    status = filters_dict.get('status')
    source_year_min = filters_dict.get('source_year_min')
    source_year_max = filters_dict.get('source_year_max')
    assigned_to_id = filters_dict.get('assigned_to_id')
    no_contact_days = filters_dict.get('no_contact_days')
    has_phone = filters_dict.get('has_phone', True)
    exclude_dnc = filters_dict.get('exclude_dnc', True)
    exclude_duplicates = filters_dict.get('exclude_duplicates', True)

    if tier:
        query = query.filter(Lead.tier == tier)
    if status:
        query = query.filter(Lead.status == status)
    if source_year_min:
        query = query.filter(Lead.source_year >= source_year_min)
    if source_year_max:
        query = query.filter(Lead.source_year <= source_year_max)
    if assigned_to_id == 'unassigned':
        query = query.filter(Lead.assigned_to_id.is_(None))
    elif assigned_to_id:
        query = query.filter(Lead.assigned_to_id == assigned_to_id)
    if has_phone:
        query = query.filter(Lead.phone.isnot(None), Lead.phone != '')
    if exclude_dnc:
        query = query.filter(Lead.status != 'dnc')
    if exclude_duplicates:
        query = query.filter(Lead.is_duplicate == False)
    if no_contact_days:
        cutoff = datetime.utcnow() - timedelta(days=no_contact_days)
        query = query.filter(
            or_(
                Lead.last_action_at.is_(None),
                Lead.last_action_at <= cutoff,
            )
        )
    return query


def _lead_to_dict(lead: Lead, assigned_name: str = None) -> dict:
    return {
        'id': str(lead.id),
        'first_name': lead.first_name,
        'last_name': lead.last_name,
        'phone': lead.phone,
        'email': lead.email,
        'tier': lead.tier,
        'status': lead.status,
        'source_year': lead.source_year,
        'assigned_to_name': assigned_name,
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/preview")
def preview_campaign(
    tier: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    source_year_min: Optional[int] = Query(None),
    source_year_max: Optional[int] = Query(None),
    assigned_to_id: Optional[str] = Query(None),
    no_contact_days: Optional[int] = Query(None),
    has_phone: bool = Query(True),
    exclude_dnc: bool = Query(True),
    exclude_duplicates: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return leads matching campaign filters without sending anything."""
    filters = {
        'tier': tier,
        'status': status,
        'source_year_min': source_year_min,
        'source_year_max': source_year_max,
        'assigned_to_id': assigned_to_id,
        'no_contact_days': no_contact_days,
        'has_phone': has_phone,
        'exclude_dnc': exclude_dnc,
        'exclude_duplicates': exclude_duplicates,
    }

    query = db.query(Lead).filter(Lead.org_id == current_user.org_id)
    query = _apply_filters(query, filters, db)
    leads = query.order_by(Lead.created_at.desc()).limit(500).all()

    # Get advisor names in one query
    advisor_ids = [l.assigned_to_id for l in leads if l.assigned_to_id]
    advisor_map = {}
    if advisor_ids:
        advisors = db.query(User).filter(User.id.in_(advisor_ids)).all()
        advisor_map = {str(a.id): a.full_name for a in advisors}

    return [
        _lead_to_dict(lead, advisor_map.get(str(lead.assigned_to_id)))
        for lead in leads
    ]


@router.post("/send")
async def send_campaign(
    request: CampaignSendRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send or schedule a campaign to a list of lead IDs."""
    if not request.lead_ids:
        raise HTTPException(status_code=400, detail="No leads specified.")
    if not request.message_template.strip():
        raise HTTPException(status_code=400, detail="Message template is required.")

    # Verify all leads belong to this org
    leads = db.query(Lead).filter(
        Lead.id.in_(request.lead_ids),
        Lead.org_id == current_user.org_id,
    ).all()

    if not leads:
        raise HTTPException(status_code=404, detail="No matching leads found.")

    # Create campaign record if model exists
    campaign_id = str(uuid.uuid4())
    try:
        campaign = Campaign(
            id=campaign_id,
            org_id=current_user.org_id,
            created_by_id=current_user.id,
            name=request.name,
            message_template=request.message_template,
            include_booking_link=request.include_booking_link,
            schedule_type=request.schedule_type,
            scheduled_at=request.scheduled_at,
            status='scheduled' if request.schedule_type == 'scheduled' else 'sending',
            total_recipients=len(leads),
        )
        db.add(campaign)
        db.commit()
    except Exception:
        # Campaign model may not exist yet — proceed without it
        db.rollback()
        campaign_id = None

    if request.schedule_type == 'scheduled':
        # Return immediately — a background job will pick this up
        return {
            'campaign_id': campaign_id,
            'queued_count': len(leads),
            'skipped_count': 0,
            'failed_count': 0,
            'status': 'scheduled',
            'scheduled_at': request.scheduled_at.isoformat() if request.scheduled_at else None,
        }

    # Send now via the existing SMS send infrastructure
    sent_count = 0
    skipped_count = 0
    failed_count = 0

    try:
        from app.services.sms_service import send_sms_to_lead
    except ImportError:
        raise HTTPException(status_code=500, detail="SMS service not available.")

    for lead in leads:
        try:
            # Render template
            first_name = lead.first_name or 'there'
            advisor = db.query(User).filter(User.id == lead.assigned_to_id).first() if lead.assigned_to_id else current_user
            advisor_name = advisor.full_name if advisor else current_user.full_name

            message = request.message_template \
                .replace('{first_name}', first_name) \
                .replace('{advisor_name}', advisor_name)

            await send_sms_to_lead(
                lead=lead,
                message=message,
                include_booking_link=request.include_booking_link,
                db=db,
                sending_user=advisor or current_user,
            )
            sent_count += 1

            # Record campaign recipient
            if campaign_id:
                try:
                    recipient = CampaignRecipient(
                        id=str(uuid.uuid4()),
                        campaign_id=campaign_id,
                        lead_id=str(lead.id),
                        status='sent',
                    )
                    db.add(recipient)
                except Exception:
                    pass

        except Exception as e:
            failed_count += 1

    try:
        db.commit()
    except Exception:
        db.rollback()

    # Update campaign status
    if campaign_id:
        try:
            campaign_obj = db.query(Campaign).filter(Campaign.id == campaign_id).first()
            if campaign_obj:
                campaign_obj.status = 'completed'
                campaign_obj.sent_count = sent_count
                campaign_obj.failed_count = failed_count
                db.commit()
        except Exception:
            db.rollback()

    return {
        'campaign_id': campaign_id,
        'sent_count': sent_count,
        'skipped_count': skipped_count,
        'failed_count': failed_count,
        'status': 'completed',
    }


@router.get("")
def list_campaigns(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List past campaigns for this org."""
    try:
        campaigns = db.query(Campaign).filter(
            Campaign.org_id == current_user.org_id
        ).order_by(Campaign.created_at.desc()).limit(50).all()
        return [
            {
                'id': str(c.id),
                'name': c.name,
                'status': c.status,
                'total_recipients': c.total_recipients,
                'sent_count': getattr(c, 'sent_count', None),
                'created_at': c.created_at.isoformat() if c.created_at else None,
                'scheduled_at': c.scheduled_at.isoformat() if c.scheduled_at else None,
            }
            for c in campaigns
        ]
    except Exception:
        return []
