"""Per-company launch readiness and the owner's campaign authorization.

Real outreach for a company needs BOTH: every launch check passing (YES),
and an explicit, unexpired owner authorization. Practice work (TEST
companies, practice prospects, owner test inboxes) never needs either.
Nothing here sends anything or turns anything on by itself."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.acquisition_models import (AcquisitionCampaignAuthorization, AcquisitionOpportunity,
                                           AcquisitionProfile, AcquisitionWorkforceClient)
from app.models.models import Organization, User
from app.services.acquisition import common as C

MAX_DAYS = 31
MAX_SENDS = 25


def active(db: Session, org_id: str, now: Optional[datetime] = None) -> Optional[AcquisitionCampaignAuthorization]:
    now = now or datetime.utcnow()
    rows = (db.query(AcquisitionCampaignAuthorization)
            .filter(AcquisitionCampaignAuthorization.organization_id == org_id,
                    AcquisitionCampaignAuthorization.status == "authorized")
            .order_by(AcquisitionCampaignAuthorization.authorized_at.desc()).all())
    for r in rows:
        if r.starts_at and r.starts_at > now:
            continue
        if r.ends_at and r.ends_at <= now:
            continue
        return r
    return None


def _eligible_count(db: Session, org_id: str) -> int:
    from app.services.acquisition import outreach as O
    from app.models.models import Lead
    n = 0
    opps = (db.query(AcquisitionOpportunity)
            .filter(AcquisitionOpportunity.organization_id == org_id,
                    AcquisitionOpportunity.is_practice.is_(False),
                    AcquisitionOpportunity.stage.in_(tuple(O.SENDABLE_REAL))).limit(500).all())
    for o in opps:
        lead = db.query(Lead).filter(Lead.id == o.lead_id).first()
        email = ((lead.email if lead else "") or "").strip().lower()
        if email and lead.status != "dnc" and O._verified_email(db, o, email):
            n += 1
    return n


def launch_readiness(db: Session, org_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """YES/NO for a REAL launch, with every reason. Read-only."""
    from app.services.acquisition import booking as B
    from app.services.acquisition import engine as E
    from app.services.acquisition import outreach as O
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(404, "Company not found")
    p = db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == org_id).first()
    is_test = E.is_test_company(db, org_id)
    checks: List[Dict[str, Any]] = []

    def add(key, label, ok, detail=""):
        checks.append({"key": key, "label": label, "ok": bool(ok), "detail": detail})

    add("real_company", "Real company (not a TEST company)", not is_test,
        "TEST companies are for practice only" if is_test else "")
    onb = E.readiness(org, p) if p else {"launch_ready": False, "missing": ["Acquisition profile"]}
    add("onboarding", "Onboarding facts complete", onb["launch_ready"],
        ("Missing: " + ", ".join(onb["missing"])) if onb["missing"] else "")
    add("not_paused", "Pilot not paused", p is not None and not p.paused, "Paused" if p is not None and p.paused else "")
    wc = db.query(AcquisitionWorkforceClient).filter(AcquisitionWorkforceClient.organization_id == org_id).first()
    add("workforce_on", "Harvey workforce switched on for this company", wc is not None and wc.enabled,
        "" if wc is not None and wc.enabled else "Switched off")
    cal_user = db.query(User).filter(User.id == p.booking_user_id).first() if p and p.booking_user_id else None
    cal_ok = cal_user is not None and B.calendar_connected(db, cal_user)
    add("booking_calendar", "Production booking calendar connected", cal_ok,
        "" if cal_ok else ("No booking calendar chosen" if cal_user is None else "Calendar chosen but not connected"))
    add("server_sending", "Server sending switch on", O.enabled(), "" if O.enabled() else "ACQUISITION_OUTREACH_ENABLED is off")
    box = None
    try:
        from app.services import inbound_mailbox_service as IMS
        box = IMS.sending_mailbox(db, O.SEND_FROM)
    except Exception:                                            # noqa: BLE001
        box = None
    add("mailbox", "Sending mailbox connected", box is not None, "" if box is not None else "%s not connected" % O.SEND_FROM)
    n = _eligible_count(db, org_id) if not is_test else 0
    add("eligible_prospects", "Verified prospects ready for first email", n > 0,
        "%d ready" % n if n else "None yet: needs prospects with a verified, sourced email at Contact verified")
    ready = all(c["ok"] for c in checks)
    auth = active(db, org_id, now)
    return {
        "organization_id": org_id, "company": org.name, "is_test": is_test,
        "ready_to_launch": "YES" if ready else "NO",
        "reasons": [c["label"] + (": " + c["detail"] if c["detail"] else "") for c in checks if not c["ok"]],
        "checks": checks,
        "authorized": auth is not None,
        "authorization": _row(auth) if auth else None,
        "live": bool(ready and auth is not None),
        "eligible_prospects": n,
    }


def _row(r: AcquisitionCampaignAuthorization) -> Dict[str, Any]:
    return {"id": r.id, "status": r.status, "max_sends_per_day": r.max_sends_per_day,
            "starts_at": C.iso(r.starts_at), "ends_at": C.iso(r.ends_at), "note": r.note,
            "authorized_by_id": r.authorized_by_id, "authorized_at": C.iso(r.authorized_at),
            "revoked_at": C.iso(r.revoked_at)}


def all_readiness(db: Session) -> List[Dict[str, Any]]:
    ids = [r[0] for r in db.query(AcquisitionProfile.organization_id).all()]
    out = [launch_readiness(db, i) for i in ids]
    return sorted(out, key=lambda r: (r["is_test"], r["company"] or ""))


def authorize(db: Session, actor: User, org_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Record the owner's go-ahead. Refused unless every launch check is YES."""
    if not data.get("confirm"):
        raise HTTPException(422, "Authorizing real outreach needs confirm: true")
    lr = launch_readiness(db, org_id)
    if lr["ready_to_launch"] != "YES":
        raise HTTPException(409, "Not ready to launch: " + "; ".join(lr["reasons"]))
    try:
        days = max(1, min(int(data.get("days") or 30), MAX_DAYS))
        per_day = max(1, min(int(data.get("max_sends_per_day") or 10), MAX_SENDS))
    except (TypeError, ValueError):
        raise HTTPException(422, "days and max_sends_per_day must be numbers")
    now = datetime.utcnow()
    row = AcquisitionCampaignAuthorization(
        organization_id=org_id, status="authorized", max_sends_per_day=per_day, starts_at=now,
        ends_at=now + timedelta(days=days), note=(data.get("note") or "")[:300] or None,
        readiness_snapshot=json.dumps(lr["checks"]), authorized_by_id=actor.id)
    db.add(row)
    db.commit()
    return _row(row)


def revoke(db: Session, actor: User, org_id: str) -> int:
    rows = (db.query(AcquisitionCampaignAuthorization)
            .filter(AcquisitionCampaignAuthorization.organization_id == org_id,
                    AcquisitionCampaignAuthorization.status == "authorized").all())
    for r in rows:
        r.status, r.revoked_by_id, r.revoked_at = "revoked", actor.id, datetime.utcnow()
    db.commit()
    return len(rows)
