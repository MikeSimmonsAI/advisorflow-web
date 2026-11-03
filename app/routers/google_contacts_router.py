"""
Google Contacts Router

Two endpoints:
1. POST /google-contacts/push/{lead_id} — push one lead to Google Contacts
2. POST /google-contacts/import — stage Google Contacts as a Universal Intake batch
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User, Lead
from app.services.import_permissions import require_import_stage
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)

router = APIRouter(prefix="/google-contacts", tags=["google-contacts"])


@router.post("/push/{lead_id}")
def push_lead_to_google(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Pushes one lead to the advisor's Google Contacts.
    Requires Google account to be connected with contacts scope.
    """
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    from app.services.google_contacts_service import push_lead_to_google_contacts
    try:
        result = push_lead_to_google_contacts(db, current_user, lead)
        return {"success": True, "resource_name": result.get("resourceName")}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/import")
def import_from_google_contacts(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_import_stage),
):
    """RETIRED (#470 Stage D). Kept so old clients get a clear answer instead of a 404."""
    raise HTTPException(status_code=410, detail='Import Google Contacts from Import Center (/imports/start, then Google Contacts). Nothing was imported.')
