"""Durable, tenant-scoped Google Sheets sync for the two VOLT businesses.

Flow:  record changes -> (same DB transaction) outbox row upserted ->
       commit -> background worker (main._volt_sheets_loop, every ~15 s while
       there is work) -> claim rows -> reload records from PostgreSQL ->
       Google Sheets insert-or-update by id -> finish rows -> status recorded.

Guarantees:
* Capture: an after_flush listener queues EVERY change to a real lead,
  application (once submitted) or applicant - every code path, present and
  future - in the same transaction, with an atomic insert-or-update so two
  concurrent changes can never make a submission fail.
* Nothing is lost: the outbox row is committed with the change. Google being
  down only delays the mirror.
* No Google call in a request. The worker holds NO row lock while it talks to
  Google (claim and finish are short transactions).
* No duplicates: one outbox row per record; a record is claimed by one worker
  at a time; one worker at a time (lease + database advisory lock); the Sheet
  row is found by the record's id. A change during a write bumps `version`
  and the row stays pending, so the newest data is written next pass.
* Only the approved organization (VOLT_SHEETS_ORG_ID) is ever exported, only
  real records (no test leads/applicants, no drafts), only fixed columns.
* Off unless VOLT_SHEETS_SYNC_ENABLED=on.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import event, func, text
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.volt_leads_models import VoltLead, VoltLeadSheetOutbox, VoltSheetSyncState
from app.models.volt_models import VoltApplication, VoltProject, VoltRep
from app.services.volt import sheets_export as X

log = logging.getLogger(__name__)
BUSINESSES = ("recruiting", "consumer")
MAX_ATTEMPTS = 12
CLAIM_TTL = timedelta(minutes=10)
ALERT_AFTER = timedelta(minutes=30)
ALERT_EVERY = timedelta(hours=6)
LOCK_KEY = 571_620      # pg advisory lock: one Sheets worker at a time
CONFIG_PROBLEMS = ("not_configured", "credential", "permission", "not_found", "sheet_layout")


def approved_org() -> str:
    return (os.getenv("VOLT_SHEETS_ORG_ID") or "").strip()


def enabled() -> bool:
    return (os.getenv("VOLT_SHEETS_SYNC_ENABLED") or "off").strip().lower() == "on"


# ── capture ─────────────────────────────────────────────────────────────────

def _upsert_stmt(conn, org_id: str, business: str, record_id: str):
    now = datetime.utcnow()
    table = VoltLeadSheetOutbox.__table__
    values = dict(id=str(uuid.uuid4()), organization_id=org_id, business=business, record_id=str(record_id),
                  status="pending", version=1, attempts=0, created_at=now, updated_at=now)
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    ins = insert(table).values(**values)
    return ins.on_conflict_do_update(
        index_elements=["organization_id", "business", "record_id"],
        set_={"version": table.c.version + 1,
              # a row a worker is writing right now stays claimed; the worker
              # sees the higher version when it finishes and leaves it pending
              "status": text("CASE WHEN volt_lead_sheet_outbox.status = 'processing' "
                             "THEN 'processing' ELSE 'pending' END"),
              "attempts": 0, "next_retry_at": None, "last_error": None, "updated_at": now})


def enqueue(db: Session, org_id: str, business: str, record_id: str) -> None:
    """Queue one record (idempotent). Use inside the record's transaction."""
    if business not in BUSINESSES:
        raise ValueError("Invalid VOLT business")
    db.connection().execute(_upsert_stmt(db.connection(), org_id, business, record_id))


def _capture(session: Session, flush_context) -> None:
    org = approved_org()
    if not org:
        return
    wanted = set()
    with session.no_autoflush:
        for obj in list(session.new) + list(session.dirty):
            if isinstance(obj, VoltLead):
                if obj.organization_id == org and not obj.is_test and obj.id \
                        and (obj in session.new or session.is_modified(obj, include_collections=False)):
                    wanted.add(("consumer", obj.id))
            elif isinstance(obj, VoltApplication):
                if obj.organization_id == org and obj.status != "draft" and obj.id \
                        and (obj in session.new or session.is_modified(obj, include_collections=False)):
                    wanted.add(("recruiting", obj.id))
            elif isinstance(obj, VoltRep):
                if obj.organization_id == org and not obj.is_test and obj in session.dirty \
                        and session.is_modified(obj, include_collections=False):
                    for (aid,) in session.query(VoltApplication.id).filter(
                            VoltApplication.organization_id == org, VoltApplication.rep_serial == obj.serial,
                            VoltApplication.status != "draft").all():
                        wanted.add(("recruiting", aid))
    if not wanted:
        return
    conn = session.connection()
    for business, rid in sorted(wanted):
        conn.execute(_upsert_stmt(conn, org, business, rid))


_REGISTERED = {"done": False}


def register_listeners() -> None:
    """Attach the capture hook once per process (called from app.main)."""
    if _REGISTERED["done"]:
        return
    event.listen(Session, "after_flush", _capture)
    _REGISTERED["done"] = True


# ── rows ────────────────────────────────────────────────────────────────────

def _row(db: Session, item: VoltLeadSheetOutbox) -> Optional[List[Any]]:
    """The record as a Sheet row, or None when it must not be exported."""
    if item.business == "consumer":
        from app.services.volt import leads as L
        lead = db.query(VoltLead).filter(VoltLead.organization_id == item.organization_id,
                                         VoltLead.id == item.record_id).first()
        if lead is None or lead.is_test:
            return None
        assigned = db.get(User, lead.assigned_user_id) if lead.assigned_user_id else None
        source = L.CHANNEL_LABELS.get(lead.channel or "", lead.channel or lead.source or "")
        if lead.source_id:
            from app.models.volt_leads_models import VoltLeadSource
            src = db.get(VoltLeadSource, lead.source_id)
            if src is not None:
                source = "%s · %s" % (source, src.name) if source else src.name
        return X.consumer_row(lead, (assigned.full_name or assigned.email) if assigned else "",
                              L.STATUS_LABELS.get(lead.status, lead.status), source)
    if item.business == "recruiting":
        from app.services.volt import common as C
        app = db.query(VoltApplication).filter(VoltApplication.organization_id == item.organization_id,
                                               VoltApplication.id == item.record_id).first()
        if app is None or app.status == "draft":
            return None
        rep = db.query(VoltRep).filter(VoltRep.organization_id == item.organization_id,
                                       VoltRep.serial == app.rep_serial).first()
        if rep is None or rep.is_test:
            return None
        project = db.query(VoltProject).filter(VoltProject.organization_id == item.organization_id,
                                               VoltProject.id == app.project_id).first()
        user = db.get(User, rep.user_id)
        return X.recruiting_row(app, rep, project.name if project else "", user.email if user else "",
                                C.STATUS_LABELS.get(app.status, app.status), C.rep_code(rep.serial) or "")
    raise ValueError("Invalid business in sync queue")


# ── state ───────────────────────────────────────────────────────────────────

def state(db: Session, org_id: str, business: str) -> VoltSheetSyncState:
    st = db.get(VoltSheetSyncState, (org_id, business))
    if st is None:
        st = VoltSheetSyncState(organization_id=org_id, business=business, paused=False, consecutive_failures=0,
                                updated_at=datetime.utcnow())
        db.add(st)
        db.flush()
    return st


def _lock(db: Session) -> bool:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return True
    return bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar())


def _unlock(db: Session) -> None:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        try:
            db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        except Exception:                                      # noqa: BLE001
            pass


def has_work(db: Session) -> bool:
    org = approved_org()
    if not (enabled() and org):
        return False
    now = datetime.utcnow()
    return db.query(VoltLeadSheetOutbox.id).filter(
        VoltLeadSheetOutbox.organization_id == org,
        ((VoltLeadSheetOutbox.status == "pending") |
         ((VoltLeadSheetOutbox.status == "failed") & (VoltLeadSheetOutbox.next_retry_at <= now)
          & (VoltLeadSheetOutbox.attempts < MAX_ATTEMPTS)) |
         ((VoltLeadSheetOutbox.status == "processing") & (VoltLeadSheetOutbox.claimed_at < now - CLAIM_TTL)))
    ).first() is not None


# ── worker ──────────────────────────────────────────────────────────────────

def _claim(db: Session, org: str, limit: int) -> List[VoltLeadSheetOutbox]:
    now = datetime.utcnow()
    paused = {s.business for s in db.query(VoltSheetSyncState).filter(
        VoltSheetSyncState.organization_id == org, VoltSheetSyncState.paused.is_(True)).all()}
    q = (db.query(VoltLeadSheetOutbox)
         .filter(VoltLeadSheetOutbox.organization_id == org,
                 ((VoltLeadSheetOutbox.status == "pending") |
                  ((VoltLeadSheetOutbox.status == "failed")
                   & ((VoltLeadSheetOutbox.next_retry_at.is_(None)) | (VoltLeadSheetOutbox.next_retry_at <= now))
                   & (VoltLeadSheetOutbox.attempts < MAX_ATTEMPTS)) |
                  ((VoltLeadSheetOutbox.status == "processing") & (VoltLeadSheetOutbox.claimed_at < now - CLAIM_TTL))))
         .order_by(VoltLeadSheetOutbox.updated_at, VoltLeadSheetOutbox.id))
    if paused:
        q = q.filter(~VoltLeadSheetOutbox.business.in_(paused))
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    rows = q.limit(max(1, min(limit, 200))).all()
    token = str(uuid.uuid4())
    for r in rows:
        r.status, r.claim_token, r.claimed_version, r.claimed_at = "processing", token, r.version, now
    db.commit()
    return rows


def _finish(db: Session, row_id: str, token: str, ok: bool, category: Optional[str] = None,
            skipped: bool = False) -> None:
    r = db.get(VoltLeadSheetOutbox, row_id)
    if r is None or r.claim_token != token:
        return                                                # a newer claim owns it now
    now = datetime.utcnow()
    changed_since_claim = (r.version or 0) != (r.claimed_version or 0)
    if ok:
        r.status = "pending" if changed_since_claim else ("skipped" if skipped else "synced")
        r.attempts, r.last_error, r.next_retry_at = 0, None, None
        r.synced_at = now if not skipped else r.synced_at
    else:
        r.last_error = category
        if category in CONFIG_PROBLEMS:
            # Not this record's fault: keep it, try again every 15 minutes until
            # the setup is fixed, without using up its retry budget.
            r.status, r.next_retry_at = "failed", now + timedelta(minutes=15)
        elif category == "data":
            r.attempts = (r.attempts or 0) + 1
            r.status, r.next_retry_at = "failed", None          # needs a person (manual retry)
        else:
            r.attempts = (r.attempts or 0) + 1
            r.status = "failed"
            r.next_retry_at = now + timedelta(seconds=min(3600, 15 * 2 ** min(r.attempts, 8)))
    r.claim_token, r.claimed_at, r.updated_at = None, None, now


def sync_pass(db: Session, *, limit: int = 100, client_factory=None) -> Dict[str, Any]:
    """One pass. Owns its own short transactions; holds no lock during Google calls."""
    org = approved_org()
    if not enabled():
        return {"disabled": True, "reason": "sync_off"}
    if not org:
        return {"disabled": True, "reason": "tenant_not_configured"}
    if not _lock(db):
        return {"disabled": False, "skipped": "another worker is running"}
    out: Dict[str, Any] = {"disabled": False, "synced": 0, "failed": 0, "skipped": 0}
    try:
        items = _claim(db, org, limit)
        groups: Dict[str, List[VoltLeadSheetOutbox]] = {}
        for it in items:
            groups.setdefault(it.business, []).append(it)
        for business, rows in groups.items():
            token = rows[0].claim_token
            st = state(db, org, business)
            st.last_attempt_at = datetime.utcnow()
            if not X.configured(business):
                for r in rows:
                    _finish(db, r.id, token, False, "not_configured")
                _record_failure(st, "not_configured")
                db.commit()
                out["failed"] += len(rows)
                continue
            built, ready = [], []
            for r in rows:
                try:
                    row = _row(db, r)
                except Exception:                              # noqa: BLE001
                    _finish(db, r.id, token, False, "data")
                    out["failed"] += 1
                    continue
                if row is None:
                    _finish(db, r.id, token, True, skipped=True)
                    out["skipped"] += 1
                else:
                    built.append(row)
                    ready.append(r)
            db.commit()                                        # nothing held while Google runs
            if not ready:
                continue
            try:
                client = (client_factory or X.api)()
                X.upsert(business, built, client)
            except Exception as exc:                           # noqa: BLE001
                cat = X.categorize(exc)
                log.warning("VOLT Sheets %s write failed: %s (%d rows)", business, cat, len(ready))
                st = state(db, org, business)
                for r in ready:
                    _finish(db, r.id, token, False, cat)
                _record_failure(st, cat)
                out["failed"] += len(ready)
                db.commit()
                continue
            st = state(db, org, business)
            for r in ready:
                _finish(db, r.id, token, True)
            st.last_success_at, st.consecutive_failures = datetime.utcnow(), 0
            st.last_error_category = None
            st.verified_at, st.verified_ok, st.verify_detail = datetime.utcnow(), True, None
            out["synced"] += len(ready)
            db.commit()
        alert_if_stuck(db, org)
        db.commit()
    finally:
        _unlock(db)
    return out


def _record_failure(st: VoltSheetSyncState, category: str) -> None:
    now = datetime.utcnow()
    st.last_error_category, st.last_error_at = category, now
    st.consecutive_failures = (st.consecutive_failures or 0) + 1
    if category in ("credential", "permission", "not_found", "sheet_layout", "not_configured"):
        st.verified_at, st.verified_ok, st.verify_detail = now, False, category
    st.updated_at = now


# ── reconciliation, retry, pause, verify ────────────────────────────────────

def reconcile(db: Session, org_id: str, business: str, *, full: bool = False, days: int = 2) -> int:
    """Queue real records whose mirror may be behind. Incremental (default):
    records changed in the last `days` days that were never synced or changed
    after their last sync. full=True queues every real record (a deliberate,
    audited owner action - the first backfill)."""
    if business not in BUSINESSES:
        raise ValueError("Invalid business")
    since = datetime.utcnow() - timedelta(days=days)
    synced = {r.record_id: r.synced_at for r in db.query(VoltLeadSheetOutbox).filter(
        VoltLeadSheetOutbox.organization_id == org_id, VoltLeadSheetOutbox.business == business).all()}
    ids: List[str] = []
    if business == "consumer":
        q = db.query(VoltLead.id, VoltLead.updated_at).filter(VoltLead.organization_id == org_id,
                                                               VoltLead.is_test.is_(False))
        if not full:
            q = q.filter(VoltLead.updated_at >= since)
        rows = q.all()
    else:
        q = (db.query(VoltApplication.id, VoltApplication.updated_at)
             .join(VoltRep, VoltRep.serial == VoltApplication.rep_serial)
             .filter(VoltApplication.organization_id == org_id, VoltApplication.status != "draft",
                     VoltRep.is_test.is_(False)))
        if not full:
            q = q.filter(VoltApplication.updated_at >= since)
        rows = q.all()
    for rid, upd in rows:
        last = synced.get(rid)
        if full or last is None or (upd is not None and upd > last):
            ids.append(rid)
    for rid in ids:
        enqueue(db, org_id, business, rid)
    st = state(db, org_id, business)
    st.last_reconcile_at, st.last_reconcile_count, st.updated_at = datetime.utcnow(), len(ids), datetime.utcnow()
    db.flush()
    return len(ids)


def retry_failed(db: Session, org_id: str, business: str) -> int:
    rows = db.query(VoltLeadSheetOutbox).filter(VoltLeadSheetOutbox.organization_id == org_id,
                                                VoltLeadSheetOutbox.business == business,
                                                VoltLeadSheetOutbox.status == "failed").all()
    for r in rows:
        r.status, r.attempts, r.next_retry_at, r.updated_at = "pending", 0, None, datetime.utcnow()
    db.flush()
    return len(rows)


def set_paused(db: Session, org_id: str, business: str, paused: bool) -> None:
    st = state(db, org_id, business)
    st.paused, st.updated_at = bool(paused), datetime.utcnow()
    db.flush()


def verify_now(db: Session, org_id: str, business: str, client_factory=None) -> Dict[str, Any]:
    st = state(db, org_id, business)
    now = datetime.utcnow()
    try:
        X.verify(business, (client_factory or X.api)())
        st.verified_at, st.verified_ok, st.verify_detail = now, True, None
    except Exception as exc:                                   # noqa: BLE001
        cat = X.categorize(exc)
        st.verified_at, st.verified_ok, st.verify_detail = now, False, cat
    st.updated_at = now
    db.flush()
    return {"ok": bool(st.verified_ok), "category": st.verify_detail,
            "text": X.CATEGORY_TEXT.get(st.verify_detail or "", None)}


# ── status and alerts ───────────────────────────────────────────────────────

def _utc(dt):
    from app.services.volt.leads import _utc as u
    return u(dt)


def status(db: Session, org_id: str) -> Dict[str, Any]:
    allowed = approved_org() == org_id
    out = {"enabled": enabled(), "organization_approved": allowed,
           "service_account": X.service_account_email() if allowed else None, "businesses": []}
    for b in BUSINESSES:
        st = db.get(VoltSheetSyncState, (org_id, b))
        counts = dict(db.query(VoltLeadSheetOutbox.status, func.count(VoltLeadSheetOutbox.id)).filter(
            VoltLeadSheetOutbox.organization_id == org_id, VoltLeadSheetOutbox.business == b)
            .group_by(VoltLeadSheetOutbox.status).all())
        oldest = db.query(func.min(VoltLeadSheetOutbox.updated_at)).filter(
            VoltLeadSheetOutbox.organization_id == org_id, VoltLeadSheetOutbox.business == b,
            VoltLeadSheetOutbox.status.in_(("pending", "failed", "processing"))).scalar()
        cat = st.last_error_category if st else None
        configured = allowed and X.configured(b)
        if not allowed or not X.spreadsheet_id(b):
            health = "not_configured"
        elif not X.configured(b):
            health = "needs_credential"
        elif not enabled():
            health = "off"
        elif st and st.paused:
            health = "paused"
        elif st and st.verified_ok is False:
            health = "broken"
        elif counts.get("failed"):
            health = "retrying"
        elif st and st.last_success_at:
            health = "ok"
        else:
            health = "waiting"
        out["businesses"].append({
            "business": b, "label": X.BUSINESS_LABEL[b], "health": health, "configured": configured,
            "spreadsheet_url": X.sheet_url(b) if allowed else None, "tab": X.TABS[b],
            "paused": bool(st.paused) if st else False,
            "pending": int(counts.get("pending", 0)) + int(counts.get("processing", 0)),
            "failed": int(counts.get("failed", 0)), "synced": int(counts.get("synced", 0)),
            "oldest_waiting_at": _utc(oldest),
            "last_success_at": _utc(st.last_success_at) if st else None,
            "last_attempt_at": _utc(st.last_attempt_at) if st else None,
            "last_error_category": cat, "last_error_text": X.CATEGORY_TEXT.get(cat or "") if cat else None,
            "last_error_at": _utc(st.last_error_at) if st else None,
            "verified_at": _utc(st.verified_at) if st else None,
            "verified_ok": st.verified_ok if st else None,
            "last_reconcile_at": _utc(st.last_reconcile_at) if st else None,
            "last_reconcile_count": st.last_reconcile_count if st else None,
        })
    return out


def alert_if_stuck(db: Session, org_id: str) -> List[str]:
    """Email the VOLT operators (VOLT_DEV_NOTIFY) when a business has had
    records waiting or failing for 30+ minutes; at most once every 6 hours per
    business. No personal data in the email."""
    sent = []
    now = datetime.utcnow()
    for b in BUSINESSES:
        st = db.get(VoltSheetSyncState, (org_id, b))
        if st is None or st.paused:
            continue
        oldest = db.query(func.min(VoltLeadSheetOutbox.updated_at)).filter(
            VoltLeadSheetOutbox.organization_id == org_id, VoltLeadSheetOutbox.business == b,
            VoltLeadSheetOutbox.status.in_(("failed", "pending", "processing"))).scalar()
        stuck = oldest is not None and oldest < now - ALERT_AFTER and (st.consecutive_failures or 0) > 0
        if not stuck or (st.alerted_at and st.alerted_at > now - ALERT_EVERY):
            continue
        try:
            from app.services.volt import common as C
            from app.services.volt import devrelay as R
            from app.services.volt import mail
            prog = C.program_for_org(db, org_id)
            cat = st.last_error_category or "unknown"
            html = ("<p>The %s Google Sheet has not been updating for over 30 minutes.</p>"
                    "<p><b>Problem:</b> %s</p><p>Records are safe in EvoSys and will sync once this is fixed. "
                    "Open Settings → Google Sheets in the back office to see the details and retry.</p>"
                    % (mail._e(X.BUSINESS_LABEL[b]), mail._e(X.CATEGORY_TEXT.get(cat, cat))))
            window = now.strftime("%Y%m%d") + str(now.hour // 6)
            for to in R.notify_list():
                mail.queue(db, prog, kind="sheets_alert", key="sheets:%s:%s:%s:%s" % (org_id, b, window, to),
                           to=to, subject="Action needed: %s Google Sheet is not updating" % X.BUSINESS_LABEL[b],
                           html=html)
            st.alerted_at = now
            sent.append(b)
        except Exception:                                      # noqa: BLE001
            log.warning("VOLT Sheets alert could not be queued for %s", b, exc_info=True)
    return sent


def require_operator(db: Session, user: User, org_id: str) -> None:
    """Only the owner and VOLT's named operators see or control the mirror."""
    from app.services.volt import leads as L
    if not L.is_admin(db, user, org_id):
        raise HTTPException(status_code=403, detail="Google Sheets settings are for the owner and VOLT's named operators.")
