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


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/")
def list_templates(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    templates = db.query(CadenceTemplate).filter(
        CadenceTemplate.organization_id == current_user.organization_id,
        CadenceTemplate.is_active == True,
    ).order_by(CadenceTemplate.is_default.desc(), CadenceTemplate.created_at.asc()).all()
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
    """Seed the pre-built default templates for this org."""
    seeded = []
    for key, data in DEFAULTS.items():
        if industry != "all" and key != industry:
            continue
        existing = db.query(CadenceTemplate).filter(
            CadenceTemplate.organization_id == current_user.organization_id,
            CadenceTemplate.name == data["name"],
        ).first()
        if existing:
            seeded.append({"name": data["name"], "status": "already_exists"})
            continue

        template = CadenceTemplate(
            id=str(uuid.uuid4()),
            organization_id=current_user.organization_id,
            name=data["name"],
            description=data["description"],
            industry=key,
            is_default=(key == industry),
            allow_advisor_override=False,
            created_by_id=current_user.id,
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
