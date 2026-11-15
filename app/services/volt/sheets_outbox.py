"""Durable, tenant-scoped Sheets projection queue for VOLT Marketing.

The database is authoritative. Export never runs during user submissions and
must NOT roll back intake when Google is unavailable. No PII in queue rows.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.volt_leads_models import VoltLead, VoltLeadSheetOutbox
from app.models.volt_models import VoltApplication, VoltProject, VoltRep
from app.services.volt import sheets_export as sheets

MAX_ATTEMPTS = 12


def enqueue(db: Session, org_id: str, business: str, record_id: str) -> bool:
    """Call in the SAME database transaction as a new or changed real record."""
    if business not in ("consumer", "recruiting"):
        raise ValueError("Invalid VOLT business")
    q = db.query(VoltLeadSheetOutbox).filter(
        VoltLeadSheetOutbox.organization_id == org_id,
        VoltLeadSheetOutbox.business == business,
        VoltLeadSheetOutbox.record_id == str(record_id))
    item = q.first()
    if item is None:
        try:
            with db.begin_nested():
                db.add(VoltLeadSheetOutbox(
                    organization_id=org_id, business=business, record_id=str(record_id),
                    status="pending", attempts=0, updated_at=datetime.utcnow()))
                db.flush()
            return True
        except IntegrityError:  # concurrent event was queued first
            item = q.first()
    if item is not None:
        item.status = "pending"
        item.attempts = 0
        item.last_error = None
        item.next_retry_at = None
        item.updated_at = datetime.utcnow()
    return False


def _row(db: Session, item: VoltLeadSheetOutbox):
    if item.business == "consumer":
        lead = db.query(VoltLead).filter(
            VoltLead.organization_id == item.organization_id,
            VoltLead.id == item.record_id,
            VoltLead.is_test.is_(False)).first()
        if lead is None:
            return None
        assigned = db.get(User, lead.assigned_user_id) if lead.assigned_user_id else None
        return sheets.consumer_row(lead, assigned.full_name or assigned.email if assigned else "")
    if item.business == "recruiting":
        app = db.query(VoltApplication).filter(
            VoltApplication.organization_id == item.organization_id,
            VoltApplication.id == item.record_id).first()
        if app is None or app.status == "draft":
            return None
        rep = db.query(VoltRep).filter(
            VoltRep.organization_id == item.organization_id,
            VoltRep.serial == app.rep_serial,
            VoltRep.is_test.is_(False)).first()
        if rep is None:
            return None
        project = db.query(VoltProject).filter(
            VoltProject.organization_id == item.organization_id,
            VoltProject.id == app.project_id).first()
        user = db.get(User, rep.user_id)
        return sheets.recruiting_row(
            app, rep, project.name if project else "",
            user.email if user else "")
    raise ValueError("Invalid business in sync queue")


def sync_pass(db: Session, *, limit: int = 25) -> dict:
    """One scheduled pass. Caller owns transaction and a distributed worker lock.

    Export is opt-in, fails closed, and must run ONLY on a designated scheduler
    with a distributed lock. A stale worker can be replayed safely by record ID.
    """
    if os.getenv("VOLT_SHEETS_SYNC_ENABLED", "off").lower() != "on":
        return {"disabled": True, "synced": 0}
    # A spreadsheet is an external PII destination. Never mix organizations:
    # only the explicitly approved tenant may be exported to these two IDs.
    approved_org_id = os.getenv("VOLT_SHEETS_ORG_ID", "").strip()
    if not approved_org_id:
        return {"disabled": True, "reason": "tenant_not_configured", "synced": 0}
    now = datetime.utcnow()
    items = (db.query(VoltLeadSheetOutbox)
             .filter(VoltLeadSheetOutbox.organization_id == approved_org_id,
                     VoltLeadSheetOutbox.status.in_(("pending", "failed")),
                     (VoltLeadSheetOutbox.next_retry_at.is_(None) |
                      (VoltLeadSheetOutbox.next_retry_at <= now)),
                     VoltLeadSheetOutbox.attempts < MAX_ATTEMPTS)
             .order_by(VoltLeadSheetOutbox.updated_at, VoltLeadSheetOutbox.id)
             .with_for_update(skip_locked=True)
             .limit(min(max(limit, 1), 100)).all())
    out = {"disabled": False, "synced": 0, "failed": 0, "unconfigured": 0}
    for item in items:
        if not sheets.configured(item.business):
            out["unconfigured"] += 1
            continue
        try:
            row = _row(db, item)
            if row is None:  # deleted or synthetic: never export it
                item.status = "synced"
            else:
                sheets.sync_row(item.business, row)
                item.status = "synced"
                out["synced"] += 1
            item.synced_at = datetime.utcnow()
            item.next_retry_at = None
            item.last_error = None
            item.attempts = 0
        except Exception as exc:  # noqa: BLE001 - external provider may be unavailable
            item.status = "failed"
            item.attempts += 1
            # Never record the exception message; Google errors may contain PII.
            item.last_error = type(exc).__name__[:100]
            delay = min(3600, 20 * 2 ** min(item.attempts, 8))
            item.next_retry_at = datetime.utcnow() + timedelta(seconds=delay)
            out["failed"] += 1
        item.updated_at = datetime.utcnow()
    db.flush()
    return out
