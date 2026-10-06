"""/god/email/inbound-mailboxes - connect and watch the shared reply mailboxes.

Platform-owner only. Connecting is a Microsoft sign-in done BY THE PERSON in
their browser (we never see a password); everything else here is status plus
a "poll now" for a controlled test. The sign-in grants Mail.ReadWrite so a
processed reply can be filed into its location's Outlook folder.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
from app.models.models import Organization, User

router = APIRouter(prefix="/god/email", tags=["god-email"])


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _box_payload(db: Session, box: InboundMailbox, addresses=None, names=None) -> dict:
    from app.services.inbound_mailbox_service import candidate_org_ids
    ids = candidate_org_ids(db, box, addresses)
    if names is None:
        names = dict(db.query(Organization.id, Organization.name)
                     .filter(Organization.id.in_(ids or ["-"])).all())
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
    # Every mailbox routes against the same org address map; resolve it and
    # the org names once for the list, not once per mailbox.
    from app.services.inbound_mailbox_service import candidate_org_ids, org_sending_addresses
    addresses = org_sending_addresses(db) if boxes else {}
    routed = {i for b in boxes for i in candidate_org_ids(db, b, addresses)}
    names = dict(db.query(Organization.id, Organization.name)
                 .filter(Organization.id.in_(routed or {"-"})).all()) if routed else {}
    return {
        "mailboxes": [_box_payload(db, b, addresses, names) for b in boxes],
        "recent": [{"id": r.id, "mailbox_id": r.mailbox_id, "from": r.from_address, "subject": r.subject,
                    "received_at": _iso(r.received_at), "outcome": r.outcome, "detail": r.detail,
                    "lead_id": r.lead_id, "organization": org_names.get(r.organization_id),
                    "graph_message_id": r.graph_message_id, "logged_at": _iso(r.created_at)}
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


@router.get("/inbound-mailboxes/{box_id}/probe")
def probe_mailbox(box_id: str, folder: Optional[str] = Query(None, max_length=400),
                  god: User = Depends(require_god), db: Session = Depends(get_db)):
    """Changes nothing: granted access, folders, newest messages (subject/from/
    folder only) and, with ?folder=, whether that Outlook folder exists."""
    from app.services.inbound_mailbox_service import MailboxAuthError, probe
    box = _box_or_404(db, box_id)
    try:
        return probe(db, box, folder=folder)
    except MailboxAuthError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/inbound-mailboxes/{box_id}/active")
def set_active(box_id: str, active: bool = Query(...), god: User = Depends(require_god),
               db: Session = Depends(get_db)):
    box = _box_or_404(db, box_id)
    box.is_active = bool(active)
    db.commit()
    return _box_payload(db, box)
