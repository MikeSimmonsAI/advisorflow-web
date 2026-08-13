"""
Cadence Template Router
Full CRUD for org cadence templates + pre-built defaults seeder.
"""

import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.deps import get_db, get_current_user, require_admin
from app.models.models import CadenceTemplate, CadenceTemplateTouch, User

router = APIRouter(prefix="/cadence-templates", tags=["cadence-templates"])

# ── Pre-built default templates ────────────────────────────────────────────────

DEFAULTS = {
    "funeral": {
        "name": "Funeral Home 9-Touch",
        "description": "Standard 9-touch re-engagement for cemetery and funeral home leads.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, this is {advisor_name} with {org_name}. I wanted to personally reach out and see how I can help you. {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, just following up. I'm here whenever you're ready to talk. {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 11, "channel": "email", "message_template": "Hi {first_name},\n\nI wanted to check in and see if you had any questions. I'd love to connect at your convenience.\n\n{booking_url}\n\n{advisor_name}", "subject_template": "Checking in, {first_name}"},
            {"touch_number": 4, "day_offset": 10, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, this is {advisor_name}. Still happy to help whenever you're ready. {booking_url}"},
            {"touch_number": 5, "day_offset": 14, "send_hour": 14, "channel": "sms",   "message_template": "Hi {first_name}, I have some availability this week if you'd like to connect. {booking_url}"},
            {"touch_number": 6, "day_offset": 21, "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\n\nI'm reaching out one more time. Many families find peace of mind in getting a plan in place. I'd be honored to help.\n\n{booking_url}\n\n{advisor_name}", "subject_template": "Still here for you, {first_name}"},
            {"touch_number": 7, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, {advisor_name} here. Just want to make sure you have everything you need. {booking_url}"},
            {"touch_number": 8, "day_offset": 45, "send_hour": 11, "channel": "both",  "message_template": "Hi {first_name}, I know life gets busy. I'm still here if you'd like to talk. {booking_url}"},
            {"touch_number": 9, "day_offset": 60, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, this will be my last reach out for a while. I'm always here if you need me. {booking_url}"},
        ]
    },
    "roofing": {
        "name": "Roofing 5-Touch",
        "description": "Fast 5-touch follow-up for roofing estimates and leads.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 9,  "channel": "sms",   "message_template": "Hi {first_name}, this is {advisor_name} from {org_name}. Thanks for your interest! Ready to schedule your free estimate? {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, following up on your roof estimate. Slots are filling up this week. {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\n\nI wanted to follow up on your roofing inquiry. We have special financing available this month. Let's get your estimate scheduled.\n\n{booking_url}\n\n{advisor_name}", "subject_template": "Your free estimate is waiting, {first_name}"},
            {"touch_number": 4, "day_offset": 14, "send_hour": 11, "channel": "sms",   "message_template": "Hi {first_name}, still interested in protecting your home? I can get you on the schedule quickly. {booking_url}"},
            {"touch_number": 5, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, last reach out for now. When you're ready for your estimate, I'm here. {booking_url}"},
        ]
    },
    "insurance": {
        "name": "Insurance 7-Touch",
        "description": "7-touch nurture sequence for insurance leads.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, this is {advisor_name} from {org_name}. I'd love to find you the right coverage. {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, just checking in. Even 10 minutes could save you significantly on coverage. {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 11, "channel": "email", "message_template": "Hi {first_name},\n\nI wanted to follow up on your insurance inquiry. I have several options that might be a great fit.\n\n{booking_url}\n\n{advisor_name}", "subject_template": "Your coverage options, {first_name}"},
            {"touch_number": 4, "day_offset": 10, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, rates can change. Let's lock in the best rate for you now. {booking_url}"},
            {"touch_number": 5, "day_offset": 14, "send_hour": 14, "channel": "both",  "message_template": "Hi {first_name}, I have a few options I think you'll like. Ready when you are. {booking_url}"},
            {"touch_number": 6, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, {advisor_name} here. Still here to help with your coverage needs. {booking_url}"},
            {"touch_number": 7, "day_offset": 60, "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\n\nThis is my final follow-up for now. I'm always here when you're ready to review your options.\n\n{advisor_name}", "subject_template": "Still here for you, {first_name}"},
        ]
    },
    "fiber": {
        "name": "Fiber/D2D 6-Touch",
        "description": "Fast 6-touch sequence for fiber internet and door-to-door sales leads.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}! This is {advisor_name} from {org_name}. We just expanded fiber service to your area — want to lock in availability? {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hey {first_name}, still got a few install slots open this week. Takes less than 2 hours. {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\n\nI wanted to follow up on your interest in fiber service. We have availability in your area and installation is quick.\n\n{booking_url}\n\n{advisor_name}", "subject_template": "Fiber service available in your area, {first_name}"},
            {"touch_number": 4, "day_offset": 14, "send_hour": 11, "channel": "sms",   "message_template": "Hi {first_name}, {advisor_name} here. Still interested in upgrading your internet? Happy to answer questions. {booking_url}"},
            {"touch_number": 5, "day_offset": 21, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, spots are going fast in your area. Let me know if you'd like to grab one. {booking_url}"},
            {"touch_number": 6, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, last reach out for now. I'm here whenever you're ready to get started. {booking_url}"},
        ]
    },
    "solar": {
        "name": "Solar 6-Touch",
        "description": "6-touch nurture for solar leads with energy savings focus.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}! This is {advisor_name} from {org_name}. Ready to see how much you could save with solar? {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, average homeowners in your area save $150+/month. Want a free savings estimate? {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 11, "channel": "email", "message_template": "Hi {first_name},\n\nI wanted to follow up on your solar interest. Our team can put together a custom savings estimate at no cost.\n\n{booking_url}\n\n{advisor_name}", "subject_template": "Your free solar savings estimate, {first_name}"},
            {"touch_number": 4, "day_offset": 14, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, incentive deadlines are approaching. Let me get you a number before rates change. {booking_url}"},
            {"touch_number": 5, "day_offset": 21, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, {advisor_name} here. Still here to answer any solar questions you have. {booking_url}"},
            {"touch_number": 6, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, this is my last follow-up. When you're ready to explore solar I'm here. {booking_url}"},
        ]
    },
    "real_estate": {
        "name": "Real Estate 7-Touch",
        "description": "7-touch sequence for buyer and seller real estate leads.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 9,  "channel": "sms",   "message_template": "Hi {first_name}! This is {advisor_name} with {org_name}. I'd love to help you find the right home. Ready to connect? {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, the market is moving fast. Let's set up a quick call so you don't miss out. {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\n\nI have some great listings that match what you're looking for. I'd love to walk you through them.\n\n{booking_url}\n\n{advisor_name}", "subject_template": "New listings for you, {first_name}"},
            {"touch_number": 4, "day_offset": 10, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, any questions about the buying process? Happy to walk you through it. {booking_url}"},
            {"touch_number": 5, "day_offset": 14, "send_hour": 14, "channel": "sms",   "message_template": "Hi {first_name}, I have a few properties I think you'd love. Worth 20 minutes? {booking_url}"},
            {"touch_number": 6, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, {advisor_name} here. Still searching for the right home. I'm here when you're ready. {booking_url}"},
            {"touch_number": 7, "day_offset": 60, "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\n\nThis is my last follow-up for now. Reach out any time — I'd love to help you find your next home.\n\n{advisor_name}", "subject_template": "Still here for you, {first_name}"},
        ]
    },
    "home_services": {
        "name": "Home Services 5-Touch",
        "description": "5-touch follow-up for home services leads.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 9,  "channel": "sms",   "message_template": "Hi {first_name}! This is {advisor_name} from {org_name}. Ready to schedule your free estimate? {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, following up on your service request. We have openings this week. {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\n\nI wanted to circle back on your service inquiry. We'd love to get you on the schedule.\n\n{booking_url}\n\n{advisor_name}", "subject_template": "Your estimate is waiting, {first_name}"},
            {"touch_number": 4, "day_offset": 14, "send_hour": 11, "channel": "sms",   "message_template": "Hi {first_name}, still interested? I can get you on the schedule quickly. {booking_url}"},
            {"touch_number": 5, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, last reach out for now. I'm here whenever you're ready. {booking_url}"},
        ]
    },
    "sales": {
        "name": "General Sales 5-Touch",
        "description": "General-purpose 5-touch sales outreach sequence.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}! This is {advisor_name} from {org_name}. I'd love to connect and see how I can help. {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, just following up. I'm here whenever you're ready. {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\n\nI wanted to check in. I'd love to find a time to connect and see how I can help.\n\n{booking_url}\n\n{advisor_name}", "subject_template": "Checking in, {first_name}"},
            {"touch_number": 4, "day_offset": 14, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, {advisor_name} here. Still happy to help whenever works for you. {booking_url}"},
            {"touch_number": 5, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, last reach out for now. I'm here when you're ready. {booking_url}"},
        ]
    },
}


# ── Pydantic models ────────────────────────────────────────────────────────────

class TouchInput(BaseModel):
    touch_number: int
    day_offset: int
    send_hour: int = 10
    channel: str = "sms"
    message_template: Optional[str] = None
    subject_template: Optional[str] = None
    is_active: bool = True


class TemplateCreate(BaseModel):
    name: str
    description: Optional[str] = None
    industry: str = "funeral"
    is_default: bool = False
    allow_advisor_override: bool = False
    touches: list[TouchInput]


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_default: Optional[bool] = None
    allow_advisor_override: Optional[bool] = None
    is_active: Optional[bool] = None
    touches: Optional[list[TouchInput]] = None


def _serialize_template(t: CadenceTemplate) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "industry": t.industry,
        "is_default": t.is_default,
        "allow_advisor_override": t.allow_advisor_override,
        "is_active": t.is_active,
        "created_at": t.created_at,
        "touch_count": len(t.touches),
        "touches": [
            {
                "id": touch.id,
                "touch_number": touch.touch_number,
                "day_offset": touch.day_offset,
                "send_hour": touch.send_hour,
                "channel": touch.channel,
                "message_template": touch.message_template,
                "subject_template": touch.subject_template,
                "is_active": touch.is_active,
            }
            for touch in sorted(t.touches, key=lambda x: x.touch_number)
        ]
    }



# ── Industry normalization ─────────────────────────────────────────────────────
# Maps org.industry values to cadence template industry keys.
# Fiber/D2D/telecom/solar all get their own sequences.
INDUSTRY_TO_CADENCE = {
    "fiber": "fiber",
    "fiber_internet": "fiber",
    "door_to_door": "fiber",
    "d2d": "fiber",
    "telecom": "fiber",
    "direct_sales": "fiber",
    "solar": "solar",
    "roofing": "roofing",
    "insurance": "insurance",
    "real_estate": "real_estate",
    "funeral": "funeral",
    "cemetery": "funeral",
    "home_services": "home_services",
}

def get_cadence_industry(org_industry: str) -> str:
    """Normalize org.industry → cadence template industry key."""
    if not org_industry:
        return "funeral"
    key = org_industry.lower().replace(" ", "_").replace("-", "_")
    return INDUSTRY_TO_CADENCE.get(key, "sales")


# ── Endpoints ─────────────────────────────────────────────────────────────────

def _seed_defaults_for_org(db: Session, organization_id: str, created_by_id: str, industry: str = "funeral") -> list:
    """Internal helper to seed default templates for an org. Safe to call multiple times."""
    seeded = []
    # Normalize: map org industry to cadence key
    effective_industry = get_cadence_industry(industry) if industry != "all" else "all"
    for key, data in DEFAULTS.items():
        if effective_industry != "all" and key != effective_industry:
            continue
        existing = db.query(CadenceTemplate).filter(
            CadenceTemplate.organization_id == organization_id,
            CadenceTemplate.name == data["name"],
        ).first()
        if existing:
            seeded.append({"name": data["name"], "status": "already_exists"})
            continue
        template = CadenceTemplate(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            name=data["name"],
            description=data["description"],
            industry=key,
            is_default=(key == industry),
            allow_advisor_override=False,
            created_by_id=created_by_id,
            created_at=datetime.utcnow(),
            is_active=True,
        )
        db.add(template)
        db.flush()
        for touch_data in data["touches"]:
            touch = CadenceTemplateTouch(
                id=str(uuid.uuid4()),
                template_id=template.id,
                touch_number=touch_data["touch_number"],
                day_offset=touch_data["day_offset"],
                send_hour=touch_data.get("send_hour", 10),
                channel=touch_data["channel"],
                message_template=touch_data.get("message_template"),
                subject_template=touch_data.get("subject_template"),
                is_active=True,
            )
            db.add(touch)
        seeded.append({"name": data["name"], "status": "created"})
    db.commit()
    return seeded

@router.get("/")
def list_templates(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from app.models.models import Organization as _Org
    from sqlalchemy import or_ as _or
    _org = db.query(_Org).filter(_Org.id == current_user.organization_id).first()
    _cadence_industry = get_cadence_industry((_org.industry if _org else None) or "funeral")

    templates = db.query(CadenceTemplate).filter(
        CadenceTemplate.organization_id == current_user.organization_id,
        CadenceTemplate.is_active == True,
        _or(
            CadenceTemplate.industry == _cadence_industry,
            CadenceTemplate.industry == "general",
            CadenceTemplate.industry == None,
        )
    ).order_by(CadenceTemplate.is_default.desc(), CadenceTemplate.created_at.asc()).all()

    # Auto-seed industry-appropriate defaults if this org has no matching templates
    if not templates:
        try:
            _seed_defaults_for_org(db, current_user.organization_id, current_user.id, industry=_cadence_industry)
            templates = db.query(CadenceTemplate).filter(
                CadenceTemplate.organization_id == current_user.organization_id,
                CadenceTemplate.is_active == True,
                _or(
                    CadenceTemplate.industry == _cadence_industry,
                    CadenceTemplate.industry == "general",
                    CadenceTemplate.industry == None,
                )
            ).order_by(CadenceTemplate.is_default.desc(), CadenceTemplate.created_at.asc()).all()
        except Exception:
            pass

    return [_serialize_template(t) for t in templates]


@router.get("/{template_id}")
def get_template(template_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(CadenceTemplate).filter(
        CadenceTemplate.id == template_id,
        CadenceTemplate.organization_id == current_user.organization_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return _serialize_template(t)


@router.post("/seed-defaults")
def seed_default_templates(
    industry: str = "funeral",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Seed the pre-built default templates for this org. Requires org_admin or super_admin."""
    seeded = _seed_defaults_for_org(db, current_user.organization_id, current_user.id, industry=industry)
    return {"seeded": seeded}


@router.post("/")
def create_template(
    req: TemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    template = CadenceTemplate(
        id=str(uuid.uuid4()),
        organization_id=current_user.organization_id,
        name=req.name,
        description=req.description,
        industry=req.industry,
        is_default=req.is_default,
        allow_advisor_override=req.allow_advisor_override,
        created_by_id=current_user.id,
        created_at=datetime.utcnow(),
        is_active=True,
    )
    db.add(template)
    db.flush()

    for t in req.touches:
        touch = CadenceTemplateTouch(
            id=str(uuid.uuid4()),
            template_id=template.id,
            touch_number=t.touch_number,
            day_offset=t.day_offset,
            send_hour=t.send_hour,
            channel=t.channel,
            message_template=t.message_template,
            subject_template=t.subject_template,
            is_active=t.is_active,
        )
        db.add(touch)

    db.commit()
    db.refresh(template)
    return _serialize_template(template)


@router.patch("/{template_id}")
def update_template(
    template_id: str,
    req: TemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    t = db.query(CadenceTemplate).filter(
        CadenceTemplate.id == template_id,
        CadenceTemplate.organization_id == current_user.organization_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    if req.name is not None: t.name = req.name
    if req.description is not None: t.description = req.description
    if req.is_default is not None: t.is_default = req.is_default
    if req.allow_advisor_override is not None: t.allow_advisor_override = req.allow_advisor_override
    if req.is_active is not None: t.is_active = req.is_active
    t.updated_at = datetime.utcnow()

    if req.touches is not None:
        db.query(CadenceTemplateTouch).filter(CadenceTemplateTouch.template_id == t.id).delete()
        for touch_data in req.touches:
            touch = CadenceTemplateTouch(
                id=str(uuid.uuid4()),
                template_id=t.id,
                touch_number=touch_data.touch_number,
                day_offset=touch_data.day_offset,
                send_hour=touch_data.send_hour,
                channel=touch_data.channel,
                message_template=touch_data.message_template,
                subject_template=touch_data.subject_template,
                is_active=touch_data.is_active,
            )
            db.add(touch)

    db.commit()
    db.refresh(t)
    return _serialize_template(t)


@router.delete("/{template_id}")
def delete_template(
    template_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    t = db.query(CadenceTemplate).filter(
        CadenceTemplate.id == template_id,
        CadenceTemplate.organization_id == current_user.organization_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    t.is_active = False
    db.commit()
    return {"deleted": True}
