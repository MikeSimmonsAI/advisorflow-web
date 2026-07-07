"""
Google Contacts Router

Two endpoints:
1. POST /google-contacts/push/{lead_id} — push one lead to Google Contacts
2. POST /google-contacts/import — pull all Google Contacts and import as leads
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User, Lead

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
    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
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
    current_user: User = Depends(get_current_user),
):
    """
    Pulls all contacts from the advisor's Google Contacts and imports
    them as leads. Uses the same dedup + tier-routing logic as the
    Excel import — duplicates are flagged, not doubled.
    """
    from app.services.google_contacts_service import pull_google_contacts
    from app.services.import_service import import_leads_from_rows

    try:
        rows = pull_google_contacts(current_user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not rows:
        return {"message": "No contacts found in Google Contacts.", "imported": 0}

    result = import_leads_from_rows(
        db,
        rows=rows,
        organization_id=current_user.organization_id,
        uploading_user_id=current_user.id,
        source_filename="Google Contacts",
    )

    return result
