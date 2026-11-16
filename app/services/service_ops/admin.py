"""EvoSys-side management of Service Operations for ONE service company:
the four switches, their dependencies, readiness, history, and a safe
turn-off. Used by the owner and authorized Acquisition program admins only.

The switches are OPERATIONAL access controls (service_ops_settings flags,
the same flags the API already enforces). They never touch prices,
packages, commissions, Stripe or subscriptions.

Turning a switch OFF deletes nothing: customers, locations, jobs, schedules,
invoices, team members and history all stay. While Service Operations is off
the company's staff, workers and buyers can't reach it, and no new recurring
visits are created (the loop only serves switched-on companies). Visits
already on the calendar are neither cancelled nor started - they wait,
untouched, until the switch comes back on.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import AuditLogEntry, Organization, User
from app.models.service_ops_models import (ServiceBuyerAccess, ServiceCustomer, ServiceInvoice, ServiceJob,
                                           ServiceLocation, ServiceOpsSettings, ServiceRecurringSchedule,
                                           ServiceRequest, ServiceTeamMember)
from app.services.service_ops import core as K

AUDIT_ACTION = "service_ops_switch"
SWITCHES = [
    ("ops_enabled", "Service Operations", None,
     ["Customers & locations", "Jobs and won-contract conversion", "Recurring service", "Team & assignments",
      "Phone worker view", "Checklists, photos and job history", "Estimates and invoices (records)",
      "Payment status (reported vs confirmed)", "Operations report and suggestions"]),
    ("advanced_enabled", "Advanced Operations", "ops_enabled",
     ["Invoice PDFs (view and download)", "PDFs in the buyer portal"]),
    ("buyer_portal_enabled", "Buyer Portal", "ops_enabled",
     ["Portal logins for the company's own customers", "Their locations, visits, history, invoices",
      "Service requests and estimate approval"]),
    ("ai_assist_enabled", "AI Operations Assistant", "ops_enabled",
     ["AI summary of a finished visit (on request, labelled a suggestion)"]),
]
LABEL = {k: l for k, l, _d, _f in SWITCHES}


def _counts(db: Session, org_id: str, now: datetime) -> Dict[str, int]:
    def n(model, *crit):
        return db.query(model).filter(model.organization_id == org_id, *crit).count()
    return {
        "customers": n(ServiceCustomer), "locations": n(ServiceLocation),
        "jobs": n(ServiceJob),
        "upcoming_jobs": n(ServiceJob, ServiceJob.status.in_(("scheduled", "assigned", "confirmed")),
                           ServiceJob.scheduled_start >= now),
        "in_progress_jobs": n(ServiceJob, ServiceJob.status == "in_progress"),
        "active_schedules": n(ServiceRecurringSchedule, ServiceRecurringSchedule.status == "active"),
        "invoices": n(ServiceInvoice),
        "team_members": n(ServiceTeamMember, ServiceTeamMember.active.is_(True)),
        "buyer_logins": n(ServiceBuyerAccess, ServiceBuyerAccess.status != "revoked"),
        "open_requests": n(ServiceRequest, ServiceRequest.status == "open"),
    }


def _company_admins(db: Session, org: Organization) -> int:
    """People who can run the company's portal: workspace managers (home
    users or active customer-org memberships)."""
    from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG
    from app.services.lead_scope import MANAGER_ROLES
    ids = {u.id for u in db.query(User).filter(User.organization_id == org.id, User.is_active.is_(True),
                                                User.role.in_(MANAGER_ROLES)).all()}
    ids |= {m.user_id for m in db.query(Membership).filter(Membership.scope_type == SCOPE_CUSTOMER_ORG,
                                                           Membership.scope_id == org.id,
                                                           Membership.role.in_(MANAGER_ROLES),
                                                           Membership.is_active.is_(True)).all()}
    return len(ids)


def _ai_state(db: Session) -> Dict[str, Any]:
    try:
        from app.services.acquisition import ai_worker as AI
        r = AI.readiness(db)
        return {"state": r.get("state"), "reason": r.get("reason")}
    except Exception as exc:                                     # noqa: BLE001
        return {"state": "unavailable", "reason": "AI gateway status unavailable (%s)" % str(exc)[:120]}


def _last_changes(db: Session, org_id: str) -> Dict[str, Dict[str, Any]]:
    rows = (db.query(AuditLogEntry).filter(AuditLogEntry.organization_id == org_id,
                                           AuditLogEntry.action == AUDIT_ACTION)
            .order_by(AuditLogEntry.created_at.desc()).limit(200).all())
    users = {u.id: u for u in db.query(User).filter(User.id.in_({r.actor_user_id for r in rows} or {"-"})).all()}
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        try:
            before, after = json.loads(r.before_state or "{}"), json.loads(r.after_state or "{}")
        except ValueError:
            continue
        for k in after:
            if k in out or before.get(k) == after.get(k):
                continue
            u = users.get(r.actor_user_id)
            out[k] = {"by": (u.full_name or u.email) if u else None, "at": K.iso(r.created_at),
                      "to": bool(after[k])}
    return out


def status(db: Session, org_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(404, "Company not found")
    s = K.settings(db, org_id)
    flags = {k: bool(getattr(s, k)) if s else False for k, *_ in SWITCHES}
    counts = _counts(db, org_id, now)
    admins = _company_admins(db, org)
    ai = _ai_state(db)
    changes = _last_changes(db, org_id)
    invoice_missing = [lbl for f, lbl in (("business_name", "Business name on invoices"),
                                          ("billing_address", "Billing address on invoices"),
                                          ("payment_instructions", "Payment instructions"))
                       if not (s and getattr(s, f))]
    caps = []
    for key, label, dep, funcs in SWITCHES:
        missing: List[str] = []
        blocked = None
        if dep and not flags[dep]:
            blocked = "Turn on %s first" % LABEL[dep]
        if key == "ops_enabled":
            if admins == 0:
                missing.append("No company admin login yet - invite the client first (Service Companies → "
                               "Invite client) so someone can run it")
        if key == "advanced_enabled":
            missing += invoice_missing
        if key == "buyer_portal_enabled" and counts["customers"] == 0:
            missing.append("The company has no customers in Service Operations yet")
        if key == "ai_assist_enabled":
            if ai["state"] in ("disabled", "unavailable"):
                blocked = blocked or ("AI gateway not usable: %s" % ai["reason"])
            elif ai["state"] == "blocked":
                missing.append(ai["reason"])
        ready = not blocked and not missing
        caps.append({"key": key, "label": label, "enabled": flags[key], "depends_on": dep,
                     "ready": ready, "missing": missing, "blocked_reason": blocked if not flags[key] else None,
                     "functions": funcs, "last_change": changes.get(key)})
    return {"organization_id": org_id, "company": org.name, "is_test": _is_test(db, org_id),
            "timezone": K.company_tz(db, org_id), "capabilities": caps, "counts": counts,
            "company_admins": admins, "ai_gateway": ai,
            "while_off": "Records are kept. Staff, workers and buyers can't open Service Operations, and no new "
                         "recurring visits are created. Visits already scheduled are not cancelled or started."}


def _is_test(db: Session, org_id: str) -> bool:
    try:
        from app.services.acquisition import engine as E
        return bool(E.is_test_company(db, org_id))
    except Exception:                                            # noqa: BLE001
        return False


def apply(db: Session, actor: User, org_id: str, data: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Change switches. Validates dependencies; turning the foundation off
    turns its dependents off too (never the other way round)."""
    from app.services.acquisition import common as AC
    now = now or datetime.utcnow()
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None or not AC.acquisition_identity(org):
        raise HTTPException(404, "Company not found")
    wanted = {k: bool(data[k]) for k, *_ in SWITCHES if k in data}
    if not wanted:
        raise HTTPException(422, "Nothing to change")
    s = K.settings(db, org_id)
    before = {k: bool(getattr(s, k)) if s else False for k, *_ in SWITCHES}
    after = dict(before, **wanted)
    for k, label, dep, _f in SWITCHES:            # asked for ON without its foundation: refuse, say why
        if wanted.get(k) and dep and not after[dep]:
            raise HTTPException(409, "%s needs %s on" % (label, LABEL[dep]))
    if not after["ops_enabled"]:
        for k, _l, dep, _f in SWITCHES:
            if dep == "ops_enabled":
                after[k] = False
    for k, label, dep, _f in SWITCHES:
        if after[k] and dep and not after[dep]:
            raise HTTPException(409, "%s needs %s on" % (label, LABEL[dep]))
    st = status(db, org_id, now)
    for cap in st["capabilities"]:
        k = cap["key"]
        if after[k] and not before[k] and k == "ai_assist_enabled" and st["ai_gateway"]["state"] in ("disabled", "unavailable"):
            raise HTTPException(409, "AI Operations Assistant can't be turned on: %s" % st["ai_gateway"]["reason"])
    c = st["counts"]
    if before["ops_enabled"] and not after["ops_enabled"] and (c["upcoming_jobs"] or c["in_progress_jobs"]) \
            and not data.get("confirm_upcoming"):
        raise HTTPException(409, "This company has %d upcoming and %d in-progress job(s). Turning Service "
                                 "Operations off keeps them but nobody can work them until it's back on. "
                                 "Confirm to continue." % (c["upcoming_jobs"], c["in_progress_jobs"]))
    if before == after:
        return status(db, org_id, now)
    K.save_settings(db, org_id, actor, after, entitlements=True)
    from app.routers.audit_log_router import log_action
    log_action(db, org_id, actor.id, AUDIT_ACTION, "organization", org_id, before=before, after=after,
               details={"company": org.name, "upcoming_jobs": c["upcoming_jobs"]},
               note="Service Operations switches changed by EvoSys. No records were deleted.")
    db.commit()
    return status(db, org_id, now)


def directory_states(db: Session) -> Dict[str, Dict[str, bool]]:
    return {s.organization_id: {"ops": s.ops_enabled, "advanced": s.advanced_enabled,
                                "buyer": s.buyer_portal_enabled, "ai": s.ai_assist_enabled}
            for s in db.query(ServiceOpsSettings).all()}
