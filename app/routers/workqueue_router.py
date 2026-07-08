"""
Advisor daily work queue.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import (
    CadenceState,
    Lead,
    LeadOutcome,
    Reply,
    User,
)

router = APIRouter(prefix="/workqueue", tags=["workqueue"])


def _lead_name(lead: Lead) -> str:
    name = f"{lead.first_name or ''} {lead.last_name or ''}".strip()
    return name or "Unnamed lead"


def _lead_base(lead: Lead) -> dict[str, Any]:
    return {
        "lead_id": lead.id,
        "name": _lead_name(lead),
        "phone": lead.phone,
    }


def _val(value):
    """Return the raw string value whether it's an enum or plain string."""
    return value.value if hasattr(value, "value") else value


@router.get("/today")
def get_todays_work(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = datetime.utcnow()
    org_id = current_user.organization_id
    user_id = current_user.id

    # New leads not yet contacted
    needs_text_leads = (
        db.query(Lead)
        .filter(
            Lead.organization_id == org_id,
            Lead.assigned_to_id == user_id,
            Lead.status == "new",
        )
        .order_by(Lead.created_at.asc(), Lead.id.asc())
        .limit(100)
        .all()
    )

    # Hot/callback replies not yet reviewed
    needs_reply_rows = (
        db.query(Reply, Lead)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(
            Lead.organization_id == org_id,
            Lead.assigned_to_id == user_id,
            Reply.classification.in_(["interested", "callback"]),
            Reply.reviewed_at.is_(None),
        )
        .order_by(Reply.received_at.desc(), Reply.id.desc())
        .limit(100)
        .all()
    )

    # Cadence touches due now
    cadence_due_rows = (
        db.query(CadenceState, Lead)
        .join(Lead, CadenceState.lead_id == Lead.id)
        .filter(
            Lead.organization_id == org_id,
            Lead.assigned_to_id == user_id,
            CadenceState.status == "active",
            CadenceState.next_touch_due_at.isnot(None),
            CadenceState.next_touch_due_at <= now,
        )
        .order_by(CadenceState.next_touch_due_at.asc(), CadenceState.id.asc())
        .limit(100)
        .all()
    )

    # Booked leads with no outcome recorded — use subquery instead of group_by
    leads_with_outcomes = (
        db.query(LeadOutcome.lead_id)
        .filter(LeadOutcome.lead_id.isnot(None))
        .distinct()
        .subquery()
    )

    outcomes_needed_leads = (
        db.query(Lead)
        .filter(
            Lead.organization_id == org_id,
            Lead.assigned_to_id == user_id,
            Lead.status == "booked",
            ~Lead.id.in_(
                db.query(LeadOutcome.lead_id)
                .filter(LeadOutcome.lead_id.isnot(None))
                .scalar_subquery()
            ),
        )
        .order_by(Lead.updated_at.asc(), Lead.id.asc())
        .limit(100)
        .all()
    )

    return {
        "needs_text": [
            {
                **_lead_base(lead),
                "status": _val(lead.status),
                "tier": _val(lead.tier),
                "context": "New lead assigned to you and not yet contacted.",
                "created_at": lead.created_at,
            }
            for lead in needs_text_leads
        ],
        "needs_reply": [
            {
                **_lead_base(lead),
                "reply_id": reply.id,
                "classification": _val(reply.classification),
                "body": reply.body,
                "context": f"{_val(reply.classification) or 'reply'} reply needs review.",
                "received_at": reply.received_at,
            }
            for reply, lead in needs_reply_rows
        ],
        "cadence_due": [
            {
                **_lead_base(lead),
                "cadence_state_id": state.id,
                "current_touch_number": state.current_touch_number,
                "next_touch_due_at": state.next_touch_due_at,
                "context": f"Touch {state.current_touch_number + 1} is due now.",
            }
            for state, lead in cadence_due_rows
        ],
        "outcomes_needed": [
            {
                **_lead_base(lead),
                "status": _val(lead.status),
                "context": "Booked lead has no recorded outcome yet.",
                "updated_at": lead.updated_at,
            }
            for lead in outcomes_needed_leads
        ],
    }
