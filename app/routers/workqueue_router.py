"""
Advisor daily work queue.

One advisor-facing endpoint that consolidates the work already implied by
existing Lead, Reply, CadenceState, and LeadOutcome data. No new database fields
are introduced here.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import (
    CadenceState,
    CadenceStatus,
    Lead,
    LeadOutcome,
    LeadStatus,
    Reply,
    ReplyClassification,
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


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


@router.get("/today")
def get_todays_work(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the four buckets an advisor should work right now.

    Scoping rule: every query filters by BOTH current_user.organization_id and
    current_user.id through Lead.assigned_to_id. An advisor never sees work from
    another org or another advisor.
    """
    now = datetime.utcnow()

    base_lead_filters = (
        Lead.organization_id == current_user.organization_id,
        Lead.assigned_to_id == current_user.id,
    )

    needs_text_leads = (
        db.query(Lead)
        .filter(*base_lead_filters, Lead.status == LeadStatus.NEW)
        .order_by(Lead.created_at.asc(), Lead.id.asc())
        .limit(100)
        .all()
    )

    needs_reply_rows = (
        db.query(Reply, Lead)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(
            *base_lead_filters,
            Reply.classification.in_([ReplyClassification.INTERESTED, ReplyClassification.CALLBACK]),
            Reply.reviewed_at.is_(None),
        )
        .order_by(Reply.received_at.desc(), Reply.id.desc())
        .limit(100)
        .all()
    )

    cadence_due_rows = (
        db.query(CadenceState, Lead)
        .join(Lead, CadenceState.lead_id == Lead.id)
        .filter(
            *base_lead_filters,
            CadenceState.status == CadenceStatus.ACTIVE,
            CadenceState.next_touch_due_at.isnot(None),
            CadenceState.next_touch_due_at <= now,
        )
        .order_by(CadenceState.next_touch_due_at.asc(), CadenceState.id.asc())
        .limit(100)
        .all()
    )

    outcomes_needed_leads = (
        db.query(Lead)
        .outerjoin(LeadOutcome, LeadOutcome.lead_id == Lead.id)
        .filter(*base_lead_filters, Lead.status == LeadStatus.BOOKED)
        .group_by(Lead.id)
        .having(func.count(LeadOutcome.id) == 0)
        .order_by(Lead.updated_at.asc(), Lead.id.asc())
        .limit(100)
        .all()
    )

    return {
        "needs_text": [
            {
                **_lead_base(lead),
                "status": _enum_value(lead.status),
                "tier": _enum_value(lead.tier),
                "context": "New lead assigned to you and not yet contacted.",
                "created_at": lead.created_at,
            }
            for lead in needs_text_leads
        ],
        "needs_reply": [
            {
                **_lead_base(lead),
                "reply_id": reply.id,
                "classification": _enum_value(reply.classification),
                "body": reply.body,
                "context": f"{_enum_value(reply.classification) or 'reply'} reply needs review.",
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
                "status": _enum_value(lead.status),
                "context": "Booked lead has no recorded outcome yet.",
                "updated_at": lead.updated_at,
            }
            for lead in outcomes_needed_leads
        ],
    }
