"""Owner-only removal of synthetic VOLT test records - one run at a time.

WHY A LOCK
----------
In production (2026-11-05) the first cleanup took ~22 s. A second click started
while it was still running; it read the test reps before the first committed,
waited, and then reported counts for work the first request had already done.
The data ended up correct, but the answer was not.

Now every run takes a per-tenant transaction lock FIRST, before reading
anything. A run that cannot get the lock is refused with 409 ("already
running") instead of queueing behind the first, and a run that gets it reads
the state the previous run left behind - so a repeat after completion reports
zeros. The counts returned are counts of rows this run actually deleted.

PostgreSQL: pg_try_advisory_xact_lock, released automatically at commit or
rollback, so a crashed request can never leave the lock held. Other databases
(the SQLite test suite) use an in-process lock with the same contract.
"""
from __future__ import annotations

import threading
from datetime import datetime
import zlib
from typing import Dict

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.volt_models import (
    VoltAccess, VoltApplication, VoltAuditEvent, VoltDocument, VoltRep, VoltSession, VoltSessionAttendee,
    VoltTalentSignup,
)

_LOCAL_LOCKS: Dict[str, threading.Lock] = {}
_LOCAL_GUARD = threading.Lock()


def lock_key(org_id: str) -> int:
    # Stable signed 32-bit key per tenant.
    return zlib.crc32(("volt-test-cleanup:" + org_id).encode()) - (1 << 31)


def _try_lock(db: Session, org_id: str):
    """Returns a release callable, or None when another run holds the lock."""
    if db.get_bind().dialect.name == "postgresql":
        got = db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": lock_key(org_id)}).scalar()
        return (lambda: None) if got else None          # released at commit/rollback
    with _LOCAL_GUARD:
        lk = _LOCAL_LOCKS.setdefault(org_id, threading.Lock())
    if not lk.acquire(blocking=False):
        return None
    return lk.release


def purge(db: Session, org_id: str) -> Dict[str, int]:
    release = _try_lock(db, org_id)
    if release is None:
        raise HTTPException(status_code=409, detail={
            "message": "A test-record cleanup is already running. Wait for it to finish, then refresh.",
            "code": "cleanup_running"})
    try:
        return _purge_locked(db, org_id)
    finally:
        release()


def _purge_locked(db: Session, org_id: str) -> Dict[str, int]:
    from app.services import wholesale_files as storage
    removed = {"reps": 0, "applications": 0, "documents": 0, "sessions": 0, "logins": 0, "logins_deactivated": 0,
               "talent_signups": 0}
    reps = db.query(VoltRep).filter(VoltRep.organization_id == org_id, VoltRep.is_test.is_(True)).all()
    serials = [r.serial for r in reps]
    # Test Talent Pool signups (and any real signup's pointer to a test recruiter).
    removed["talent_signups"] = (db.query(VoltTalentSignup)
                                 .filter(VoltTalentSignup.organization_id == org_id, VoltTalentSignup.is_test.is_(True))
                                 .delete(synchronize_session=False))
    if serials:
        db.query(VoltTalentSignup).filter(VoltTalentSignup.referrer_serial.in_(serials)).update(
            {VoltTalentSignup.referrer_serial: None}, synchronize_session=False)
    if not serials:
        db.commit()
        return removed
    # Real reps never keep a pointer to a test rep.
    db.query(VoltRep).filter(VoltRep.original_recruiter_serial.in_(serials)).update(
        {VoltRep.original_recruiter_serial: None}, synchronize_session=False)
    db.query(VoltApplication).filter(VoltApplication.referrer_serial.in_(serials)).update(
        {VoltApplication.referrer_serial: None}, synchronize_session=False)
    ds = db.query(VoltDocument).filter(VoltDocument.rep_serial.in_(serials)).all()
    doc_ids = [d.id for d in ds]
    for d in ds:
        try:
            storage.remove(d.storage_backend, d.storage_key, db=db)
        except Exception:  # pragma: no cover - a missing blob is not a reason to keep the row
            pass
        db.delete(d)
    removed["documents"] = len(ds)
    apps = db.query(VoltApplication).filter(VoltApplication.rep_serial.in_(serials)).all()
    app_ids = [a.id for a in apps]
    removed["applications"] = len(apps)
    # Sessions whose ONLY attendees were test reps are test sessions - unless
    # the session is live staff availability (open for booking, upcoming,
    # not canceled): that stays, with its seats freed.
    touched = {sid for (sid,) in db.query(VoltSessionAttendee.session_id)
               .filter(VoltSessionAttendee.rep_serial.in_(serials)).distinct().all()}
    db.query(VoltSessionAttendee).filter(VoltSessionAttendee.rep_serial.in_(serials)).delete(synchronize_session=False)
    session_ids = []
    for sid in touched:
        if db.query(VoltSessionAttendee).filter(VoltSessionAttendee.session_id == sid).count() == 0:
            sess = db.get(VoltSession, sid)
            live = (sess is not None and sess.capacity is not None and sess.status == "scheduled"
                    and sess.starts_at > datetime.utcnow())
            if sess is not None and sess.organization_id == org_id and not live:
                session_ids.append(sid)
                db.delete(sess)
    removed["sessions"] = len(session_ids)
    q = db.query(VoltAuditEvent).filter(VoltAuditEvent.organization_id == org_id)
    user_ids_all = [r.user_id for r in reps]
    for etype, ids in (("rep", [str(s) for s in serials]), ("application", app_ids),
                       ("document", doc_ids), ("session", session_ids), ("access", user_ids_all)):
        if ids:
            q.filter(VoltAuditEvent.entity_type == etype, VoltAuditEvent.entity_id.in_(ids)).delete(
                synchronize_session=False)
    for a in apps:
        db.delete(a)
    user_ids = [r.user_id for r in reps]
    # VOLT access state of test logins (People & Access) goes with them.
    db.query(VoltAccess).filter(VoltAccess.organization_id == org_id, VoltAccess.user_id.in_(user_ids)).delete(
        synchronize_session=False)
    db.flush()
    for r in reps:
        db.delete(r)
    removed["reps"] = len(reps)
    db.flush()
    from app.models.session_models import UserSession
    for u in db.query(User).filter(User.id.in_(user_ids), User.role == "volt_rep").all():
        db.query(UserSession).filter(UserSession.user_id == u.id).delete(synchronize_session=False)
        try:
            with db.begin_nested():
                db.delete(u)
                db.flush()
            removed["logins"] += 1
        except Exception:
            # Some other table still points at this login: keep the row,
            # switch it off. It can no longer sign in to anything.
            u = db.get(User, u.id)
            if u is not None:
                u.is_active = False
                removed["logins_deactivated"] += 1
    db.commit()
    return removed
