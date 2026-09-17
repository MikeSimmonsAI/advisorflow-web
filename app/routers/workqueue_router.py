"""
Advisor daily work queue.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_tenant_or_observer
from app.models.models import (
    CadenceState,
    Lead,
    LeadOutcome,
    Reply,
    User,
)
from app.services import operational_queues
from app.services import reply_classification_service as _rcs

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
            Lead.is_duplicate == False,
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
            Lead.is_duplicate == False,
            # "hot" and "callback_request" were in this list and are not
            # ReplyClassification values - nothing is ever stored as either, so
            # they matched nothing and this was always the other two. Stated
            # once now, beside the vocabulary.
            *_rcs.attention_filters(),
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
            Lead.is_duplicate == False,
            CadenceState.status == "active",
            CadenceState.next_touch_due_at.isnot(None),
            CadenceState.next_touch_due_at <= now,
        )
        .order_by(CadenceState.next_touch_due_at.asc(), CadenceState.id.asc())
        .limit(100)
        .all()
    )

    # Booked leads with no outcome recorded
    outcomes_needed_leads = (
        db.query(Lead)
        .filter(
            Lead.organization_id == org_id,
            Lead.assigned_to_id == user_id,
            Lead.status == "booked",
            Lead.is_duplicate == False,
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


# ── SS8: THE FOUR QUEUES, DERIVED ───────────────────────────────────────────
#
# THEY LIVE HERE ON PURPOSE.
#
# `/workqueue/today` above IS the follow-up queue - it was built as one, and
# it is the only one of the six queue-ish screens in the product that derives
# its list rather than reading a materialized table. Hanging the other three
# off a brand-new router would have made a seventh queue surface, which is the
# exact shape of the problem SS8 exists to undo.
#
# `/workqueue/today` is UNCHANGED and still serves its page. The endpoints
# below are the wider, correctly-scoped answer; nothing has to move at once.

@router.get("/queues")
def queue_summary(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_or_observer),
):
    """Counts for all four queues - the tabs on a queue shell."""
    return operational_queues.summary(db, current_user, request=request)


@router.get("/queues/provenance")
def queue_provenance(current_user: User = Depends(require_tenant_or_observer)):
    """WHERE EACH QUEUE'S ANSWER COMES FROM.

    Not documentation for its own sake. The claim SS8 rests on is that these
    queues need no tables of their own, and a rep or an admin asking "why is
    this lead in my queue" deserves an answer better than "the system decided".
    Every source named here is asserted by a test to be one the code actually
    reads.
    """
    return operational_queues.provenance()


@router.get("/queues/{name}")
def queue(
    name: str,
    request: Request,
    limit: int = Query(default=operational_queues.DEFAULT_LIMIT, ge=1,
                       le=operational_queues.MAX_LIMIT),
    include_excluded: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_or_observer),
):
    """One derived queue: sms | email | voice | follow_up.

    include_excluded reports the leads that did NOT make the queue together
    with the reasons qualification gave, rather than silently omitting them.
    A rep who cannot see why a lead is missing concludes the queue is broken.
    """
    kwargs = {"request": request, "limit": limit}
    if name in (operational_queues.SMS, operational_queues.EMAIL,
                operational_queues.VOICE):
        kwargs["include_excluded"] = include_excluded
    try:
        return operational_queues.get(name, db, current_user, **kwargs)
    except ValueError as exc:
        # A typo'd queue name returning an empty list would read as "nothing
        # to do today", which is the most expensive wrong answer this endpoint
        # could give.
        raise HTTPException(status_code=400, detail=str(exc))
