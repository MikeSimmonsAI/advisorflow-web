"""
Advisor-facing system health status.

Read-only endpoint that reports whether the logged-in advisor has the core
integrations configured. This intentionally does not try to repair or reconnect
anything; it only reflects the current User record and known scheduler state.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import User

router = APIRouter(prefix="/health", tags=["health"])


class AdvisorHealthStatus(BaseModel):
    twilio_connected: bool
    google_calendar_connected: bool
    microsoft_365_connected: bool
    last_cadence_run: Optional[datetime] = None


def _get_last_cadence_run(db: Session, user: User) -> Optional[datetime]:
    """
    Return the last cadence-job timestamp if the project has explicit run tracking.

    Current AdvisorFlow only tracks per-lead CadenceState timestamps
    (next_touch_due_at, last_touch_sent_at, completed_at). It does not have a
    dedicated job-run ledger/table, and Task 4 explicitly says not to invent one.
    Therefore this returns None until a future scheduler-run table is added.
    """
    return None


@router.get("/advisor-status", response_model=AdvisorHealthStatus)
def advisor_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdvisorHealthStatus:
    return AdvisorHealthStatus(
        twilio_connected=bool(current_user.twilio_account_sid),
        google_calendar_connected=bool(current_user.google_calendar_connected),
        microsoft_365_connected=bool(current_user.microsoft_365_connected),
        last_cadence_run=_get_last_cadence_run(db, current_user),
    )
