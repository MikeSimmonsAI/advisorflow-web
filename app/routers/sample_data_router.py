"""
Sample Data router - lets Mike populate his live org with realistic
demo leads so every screen (Overview, Leads, Cadence, Replies,
Compliance) has something real to look at instead of all-zero empty
states, and clear it all out again to start clean before real data
goes in.

Restricted to super_admin only (same tier as password reset and lead
reassignment) since bulk-generating or bulk-deleting data are both
genuinely high-stakes actions - an org_admin should not be able to
wipe an organization's leads by accident or by misuse.

IMPORTANT SAFETY DESIGN: every sample lead gets a literal tag
(`source_file = "SAMPLE_DATA"`) so the clear-all endpoint can
surgically delete ONLY sample-tagged records, never touching real
imported leads even if real data exists alongside sample data in the
same organization. Sample leads also use a recognizable fake phone
number prefix (555) which real US phone numbers never use, so even a
person looking at raw data can immediately tell what's fake.
"""

import random
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.models import (
    User, Lead, LeadTier, LeadStatus, MessageTrack, EngagementTemperature,
    Reply, ReplyClassification, CadenceState, CadenceStatus, Message,
)
from app.routers.admin_router import require_super_admin
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/sample-data", tags=["sample-data"])

SAMPLE_TAG = "SAMPLE_DATA"  # the literal marker used to find/delete sample records safely

FIRST_NAMES = ["James", "Mary", "Robert", "Patricia", "John", "Linda", "Michael", "Barbara",
               "William", "Elizabeth", "David", "Susan", "Richard", "Jessica", "Joseph", "Sarah",
               "Charles", "Karen", "Thomas", "Nancy"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
              "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas"]

SAMPLE_REPLY_BODIES = {
    ReplyClassification.INTERESTED: [
        "Yes, I'd like to set up a time to come in.",
        "Sounds good, when can we meet?",
        "I'm interested, please call me to schedule.",
    ],
    ReplyClassification.CALLBACK: [
        "Can you call me tomorrow morning instead?",
        "I'm driving right now, call me later today.",
        "Please give me a call when you get a chance.",
    ],
    ReplyClassification.NEUTRAL: [
        "What time does your office close today?",
        "Who is this exactly?",
        "I need to think about it.",
    ],
    ReplyClassification.DNC: [
        "Please stop texting me.",
        "Remove me from this list, not interested.",
    ],
    ReplyClassification.NOT_INTERESTED: [
        "No thanks, we already have something in place.",
        "Not interested at this time.",
    ],
    ReplyClassification.WRONG_NUMBER: [
        "Wrong number, you have the wrong person.",
        "I don't know what this is about.",
    ],
    ReplyClassification.QUESTION: [
        "What's the price difference between the two options?",
        "Is this something my whole family needs to be there for?",
    ],
}


def _random_phone(seed: int) -> str:
    """Generates a fake but realistic-looking phone number using the 555 prefix, which is never assigned to real US numbers."""
    return f"1469555{seed:04d}"


@router.post("/generate")
def generate_sample_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    Generates a realistic mix of leads across every tier/status/
    engagement-temperature combination, plus replies and active cadence
    states for some of them, so every screen has real-looking data to
    display. All organization-scoped to current_user's org only.
    """
    now = datetime.now(timezone.utc)
    created_leads = []

    scenarios = [
        # (tier, status, message_track, engagement_temp, has_reply, reply_classification, in_cadence)
        (LeadTier.PRE_NEED, LeadStatus.NEW, MessageTrack.PRE_NEED_LOCK_PRICE, EngagementTemperature.UNKNOWN, False, None, False),
        (LeadTier.PRE_NEED, LeadStatus.SENT, MessageTrack.PRE_NEED_LOCK_PRICE, EngagementTemperature.WARM, False, None, True),
        (LeadTier.PRE_NEED, LeadStatus.HOT, MessageTrack.PRE_NEED_LOCK_PRICE, EngagementTemperature.HOT, True, ReplyClassification.INTERESTED, False),
        (LeadTier.AT_NEED, LeadStatus.REPLIED, MessageTrack.AT_NEED_SUPPORT, EngagementTemperature.WARM, True, ReplyClassification.CALLBACK, False),
        (LeadTier.IMMINENT, LeadStatus.HOT, MessageTrack.IMMINENT_SUPPORT, EngagementTemperature.HOT, True, ReplyClassification.INTERESTED, False),
        (LeadTier.CONTRACT_SOLD, LeadStatus.SENT, MessageTrack.UPSELL_EXISTING_CUSTOMER, EngagementTemperature.WARM, False, None, True),
        (LeadTier.CONTRACT_SOLD, LeadStatus.BOOKED, MessageTrack.UPSELL_EXISTING_CUSTOMER, EngagementTemperature.HOT, True, ReplyClassification.INTERESTED, False),
        (LeadTier.PARTIAL, LeadStatus.NEEDS_TIER_REVIEW, None, EngagementTemperature.UNKNOWN, False, None, False),
        (LeadTier.PRE_NEED, LeadStatus.REPLIED, MessageTrack.PRE_NEED_LOCK_PRICE, EngagementTemperature.WARM, True, ReplyClassification.NEUTRAL, False),
        (LeadTier.PRE_NEED, LeadStatus.DNC, MessageTrack.PRE_NEED_LOCK_PRICE, EngagementTemperature.COLD, True, ReplyClassification.DNC, False),
        (LeadTier.AT_NEED, LeadStatus.SENT, MessageTrack.AT_NEED_SUPPORT, EngagementTemperature.COLD, False, None, False),
        (LeadTier.EMAIL_ONLY, LeadStatus.NEW, MessageTrack.EMAIL_ONLY_NURTURE, EngagementTemperature.UNKNOWN, False, None, False),
    ]

    for i, (tier, status, track, temp, has_reply, reply_class, in_cadence) in enumerate(scenarios):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        is_email_only = tier == LeadTier.EMAIL_ONLY

        lead = Lead(
            organization_id=current_user.organization_id,
            assigned_to_id=current_user.id,
            first_name=first,
            last_name=last,
            phone=None if is_email_only else _random_phone(i),
            email=f"{first.lower()}.{last.lower()}@example.com" if is_email_only or random.random() > 0.4 else None,
            tier=tier.value if hasattr(tier, 'value') else tier,
            engagement_temperature=temp,
            message_track=track,
            contact_channel="email_only" if is_email_only else "sms",
            status=status.value if hasattr(status, 'value') else status,
            source_year=2024,
            source_file=SAMPLE_TAG,
            last_action_raw="Called: Scheduled Appt." if (status.value if hasattr(status, 'value') else status) == "booked" else "Called: LM/No Answer",
            last_contact_date=now - timedelta(days=random.randint(1, 14)),
        )
        db.add(lead)
        db.commit()
        created_leads.append(lead)

        if has_reply and lead.phone:
            reply_body = random.choice(SAMPLE_REPLY_BODIES[reply_class])
            reply = Reply(
                lead_id=lead.id,
                body=reply_body,
                is_hot=(reply_class == ReplyClassification.INTERESTED),
                classification=reply_class,
                classification_confidence="high",
                classification_reasoning="Sample data",
                received_at=now - timedelta(hours=random.randint(1, 48)),
            )
            db.add(reply)

        if not is_email_only:
            message = Message(
                lead_id=lead.id,
                sender_id=current_user.id,
                body=f"Hi {first}, this is {current_user.full_name} with Restland. Following up on your file review.",
                twilio_status="delivered",
                sent_at=now - timedelta(days=1),
            )
            db.add(message)

        if in_cadence:
            cadence = CadenceState(
                lead_id=lead.id,
                status="active",
                current_touch_number=random.randint(1, 4),
                cadence_started_at=now - timedelta(days=random.randint(1, 10)),
                next_touch_due_at=now + timedelta(days=random.randint(1, 5)),
            )
            db.add(cadence)

        db.commit()

    return {
        "created_count": len(created_leads),
        "message": f"Created {len(created_leads)} sample leads with replies and cadence state across every tier/status combination.",
    }


@router.delete("/clear")
def clear_sample_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    Deletes ONLY sample-tagged leads (source_file == SAMPLE_DATA) and
    their associated replies/messages/cadence state, scoped to the
    current admin's organization. Real imported leads are never
    touched, even if they exist alongside sample data - the literal
    source_file tag is the only thing this query matches on.
    """
    sample_leads = (
        db.query(Lead)
        .filter(Lead.organization_id == current_user.organization_id, Lead.source_file == SAMPLE_TAG)
        .all()
    )
    sample_lead_ids = [l.id for l in sample_leads]

    if not sample_lead_ids:
        return {"deleted_leads": 0, "message": "No sample data found to clear."}

    db.query(Reply).filter(Reply.lead_id.in_(sample_lead_ids)).delete(synchronize_session=False)
    db.query(Message).filter(Message.lead_id.in_(sample_lead_ids)).delete(synchronize_session=False)
    db.query(CadenceState).filter(CadenceState.lead_id.in_(sample_lead_ids)).delete(synchronize_session=False)
    db.query(Lead).filter(Lead.id.in_(sample_lead_ids)).delete(synchronize_session=False)
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="sample_data.clear", target_type="organization", target_id=current_user.organization_id,
        details={"deleted_leads": len(sample_lead_ids)},
    )

    return {"deleted_leads": len(sample_lead_ids), "message": f"Cleared {len(sample_lead_ids)} sample leads and their associated data."}
