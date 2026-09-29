"""/god/email/inbound-mailboxes - connect and watch the shared reply mailboxes.

Platform-owner only. Connecting is a Microsoft sign-in done BY THE PERSON in
their browser (we never see a password); everything else here is read-only
status plus a "poll now" for a controlled test.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
from app.models.models import Organization, User

router = APIRouter(prefix="/god/email", tags=["god-email"])


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _box_payload(db: Session, box: InboundMailbox) -> dict:
    from app.services.inbound_mailbox_service import candidate_org_ids
    ids = candidate_org_ids(db, box)
    names = dict(db.query(Organization.id, Organization.name).filter(Organization.id.in_(ids or ["-"])).all())
    return {
        "id": box.id, "address": box.address, "is_active": box.is_active,
        "organization_id": box.organization_id,
        "connected_at": _iso(box.connected_at), "last_polled_at": _iso(box.last_polled_at),
        "cursor_received_at": _iso(box.cursor_received_at),
        "last_status": box.last_status, "last_error": box.last_error,
        "last_checked": box.last_checked, "last_matched": box.last_matched,
        "routes_to": [{"id": i, "name": names.get(i)} for i in ids],
    }


@router.get("/inbound-mailboxes")
def list_mailboxes(god: User = Depends(require_god), db: Session = Depends(get_db),
                   limit: int = Query(50, ge=1, le=500)):
    boxes = db.query(InboundMailbox).order_by(InboundMailbox.connected_at.desc()).all()
    log = (db.query(InboundMailboxMessage).order_by(InboundMailboxMessage.created_at.desc())
           .limit(limit).all())
    org_names = dict(db.query(Organization.id, Organization.name)
                     .filter(Organization.id.in_({r.organization_id for r in log if r.organization_id} or {"-"}))
                     .all())
    return {
        "mailboxes": [_box_payload(db, b) for b in boxes],
        "recent": [{"id": r.id, "mailbox_id": r.mailbox_id, "from": r.from_address, "subject": r.subject,
                    "received_at": _iso(r.received_at), "outcome": r.outcome, "detail": r.detail,
                    "lead_id": r.lead_id, "organization": org_names.get(r.organization_id)}
                   for r in log],
    }


@router.post("/inbound-mailboxes/connect")
def connect_mailbox(request: Request, god: User = Depends(require_god), db: Session = Depends(get_db)):
    """Start the Microsoft sign-in for a shared mailbox. The person picks the
    MAILBOX account (e.g. support@evosyspro.live) on Microsoft's own page."""
    from app.models.oauth_models import FLOW_MAILBOX, PROVIDER_MICROSOFT
    from app.services import oauth_state_service
    from app.services.inbound_mailbox_service import authorization_url
    state = oauth_state_service.issue_state(
        db, provider=PROVIDER_MICROSOFT, subject=god, flow=FLOW_MAILBOX,
        client_ip=(request.client.host if request.client else None))
    try:
        return {"authorization_url": authorization_url(state)}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


def _box_or_404(db, box_id):
    box = db.query(InboundMailbox).filter(InboundMailbox.id == box_id).first()
    if box is None:
        raise HTTPException(status_code=404, detail="Mailbox not found")
    return box


@router.post("/inbound-mailboxes/{box_id}/poll-now")
def poll_now(box_id: str, god: User = Depends(require_god), db: Session = Depends(get_db)):
    from app.services.inbound_mailbox_service import poll_mailbox
    box = _box_or_404(db, box_id)
    result = poll_mailbox(db, box)
    return {"result": result, "mailbox": _box_payload(db, box)}


@router.post("/inbound-mailboxes/{box_id}/active")
def set_active(box_id: str, active: bool = Query(...), god: User = Depends(require_god),
               db: Session = Depends(get_db)):
    box = _box_or_404(db, box_id)
    box.is_active = bool(active)
    db.commit()
    return _box_payload(db, box)
