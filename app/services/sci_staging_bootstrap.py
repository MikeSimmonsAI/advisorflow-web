"""SCI STAGING ONLY: give one named person a manager seat in the SCI workspace.

Runs from the migrate/startup path (`python -m app.migrate`). INERT unless
`SCI_STAGING_MANAGER_BOOTSTRAP_ENABLED=true` and every required non-secret env
var is present. It FAILS CLOSED - touches nothing and says why - when:

  * the process is not `APP_ENV=staging` (production, demo, absent, a typo);
  * DATABASE_URL looks like a production database, or does not look like the
    SCI staging database (name or user containing `sci_staging`/`sci-staging`);
  * the target organization id does not exist, is inactive, or is not named
    exactly "Service Corporation International";
  * the configured email is blank or malformed.

WHAT IT WRITES, EXHAUSTIVELY
  * users: ONE row for the configured email, only if absent. role `advisor`,
    organization_id NULL, active. The password is random, hashed and discarded
    in the same expression; nobody can know it. An existing row is reused and
    its role / organization / password are left alone.
  * memberships: exactly one customer_org membership in the SCI org with role
    `manager`, through `workspace_access.grant_workspace_membership` (idempotent
    on user+org). Nothing else is granted or revoked.
  * staff_activations + audit_log: ONE one-time setup link via
    `staff_activation.issue` (hash/prefix persist, never the token), emailed to
    the configured address, and an audit row `sci_staging_activation_sent`
    carrying only non-secret evidence.

NO-RESPAM RULE. A link is issued only when the person has no accepted
activation AND no `sci_staging_activation_sent` marker. A pending link whose
raw token is unrecoverable and which has no marker is revoked by `issue` and
replaced by exactly one new link. If the send fails the new link is revoked
(nobody holds it) and no marker is written, so the next start retries once.

The raw token and URL are local variables passed to the mailer and nothing
else: never logged, returned, persisted or put in the summary dict.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

SCI_ORG_NAME = "Service Corporation International"
MANAGER_ROLE = "manager"
SENT_ACTION = "sci_staging_activation_sent"
FRONTEND_BASE = "https://sci-staging-frontend.onrender.com"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ENV_ENABLED = "SCI_STAGING_MANAGER_BOOTSTRAP_ENABLED"
ENV_EMAIL = "SCI_STAGING_MANAGER_EMAIL"
ENV_NAME = "SCI_STAGING_MANAGER_FULL_NAME"
ENV_ORG = "SCI_STAGING_MANAGER_ORG_ID"
ENV_SEND = "SCI_STAGING_MANAGER_SEND_ACTIVATION"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _truthy(name: str) -> bool:
    return _env(name).lower() in ("1", "true", "yes", "on")


def database_is_sci_staging(url: Optional[str] = None) -> bool:
    """True only for a database that is positively the SCI staging one."""
    from app.services import environment
    raw = url if url is not None else _env("DATABASE_URL")
    if not raw or environment.looks_like_production_db(raw):
        return False
    try:
        u = make_url(raw)
    except Exception:                                   # noqa: BLE001
        return False
    ident = ("%s %s" % (u.database or "", u.username or "")).lower()
    return "sci_staging" in ident or "sci-staging" in ident


def preflight(db: Session) -> Dict[str, Any]:
    """Every fail-closed check. Returns {"ok": bool, "reason": str, ...}."""
    from app.models.models import Organization
    from app.services import environment

    if environment.current() != environment.ENV_STAGING:
        return {"ok": False, "reason": "not_staging_environment"}
    if not database_is_sci_staging():
        return {"ok": False, "reason": "database_not_sci_staging"}
    email = _env(ENV_EMAIL).lower()
    if not email or not EMAIL_RE.match(email):
        return {"ok": False, "reason": "email_blank_or_invalid"}
    if not _env(ENV_NAME):
        return {"ok": False, "reason": "full_name_blank"}
    org_id = _env(ENV_ORG)
    if not org_id:
        return {"ok": False, "reason": "org_id_blank"}
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        return {"ok": False, "reason": "org_not_found"}
    if (org.name or "").strip() != SCI_ORG_NAME:
        return {"ok": False, "reason": "org_name_mismatch"}
    if org.is_active is False:
        return {"ok": False, "reason": "org_inactive"}
    return {"ok": True, "reason": "ok", "email": email, "org_id": org.id}


def _ensure_user(db: Session, email: str):
    from app.models.models import User
    from app.services.auth_service import hash_password
    user = db.query(User).filter(func.lower(User.email) == email).first()
    if user is not None:
        return user, "reused"
    user = User(
        email=email, full_name=_env(ENV_NAME),
        # Hashed and discarded in one expression: no variable ever holds it.
        password_hash=hash_password(secrets.token_urlsafe(48)),
        role="advisor", organization_id=None,
        must_change_password=True, is_active=True)
    db.add(user)
    db.flush()
    return user, "created"


def _ensure_membership(db: Session, user, org_id: str) -> Dict[str, Any]:
    from app.models.sales_models import Membership
    from app.services.workspace_access import (
        SCOPE_CUSTOMER_ORG, grant_workspace_membership)
    # check_capacity=False: this is platform provisioning of a named seat, not
    # a customer buying one - the same declared bypass god provisioning uses.
    grant_workspace_membership(db, user.id, org_id, MANAGER_ROLE,
                               commit=False, check_capacity=False)
    db.flush()
    rows = (db.query(Membership)
              .filter(Membership.user_id == user.id,
                      Membership.scope_type == SCOPE_CUSTOMER_ORG,
                      Membership.scope_id == org_id).all())
    return {"sci_rows": len(rows),
            "active_manager": sum(1 for m in rows
                                  if m.is_active and m.role == MANAGER_ROLE)}


def _activation_state(db: Session, user) -> Dict[str, Any]:
    from app.models.models import AuditLogEntry
    from app.models.staff_models import (
        StaffActivation, STAFF_INVITE_ACCEPTED, STAFF_INVITE_PENDING)
    accepted = (db.query(StaffActivation)
                  .filter(StaffActivation.user_id == user.id,
                          StaffActivation.status == STAFF_INVITE_ACCEPTED)
                  .count())
    pending = (db.query(StaffActivation)
                 .filter(StaffActivation.user_id == user.id,
                         StaffActivation.status == STAFF_INVITE_PENDING)
                 .count())
    sent = (db.query(AuditLogEntry)
              .filter(AuditLogEntry.action == SENT_ACTION,
                      AuditLogEntry.target_id == user.id).count())
    return {"accepted": accepted, "pending": pending, "sent_markers": sent}


def _issue_actor(db: Session, user):
    from app.models.models import User
    god = (db.query(User).filter(User.role == "god_admin",
                                 User.is_active.is_(True))
             .order_by(User.created_at.asc()).first())
    return god or user


def _build_message(full_name: str, url: str):
    subject = "Set up your SCI staging access"
    body = (
        "Hello %s,\n\n"
        "Your SCI staging access is ready. Use the one-time link below to "
        "choose your own password. It works once and expires in 72 hours.\n\n"
        "%s\n\n"
        "This is a test environment. If you were not expecting this, ignore "
        "this message." % (full_name, url))
    return subject, body


def _send_activation(db: Session, user) -> Dict[str, Any]:
    from app.models.staff_models import PURPOSE_SETUP, STAFF_INVITE_REVOKED
    from app.routers.audit_log_router import log_action
    from app.services import email_service, staff_activation
    from datetime import datetime

    actor = _issue_actor(db, user)
    row, raw = staff_activation.issue(db, user, actor, purpose=PURPOSE_SETUP)
    activation_id, prefix = row.id, row.token_prefix
    try:
        url = staff_activation.activation_url(FRONTEND_BASE, raw)
        subject, body = _build_message(user.full_name, url)
        result = email_service.send_email_via_provider(
            to_email=user.email, subject=subject,
            body_html=email_service.plain_text_to_html(body),
            message_type="staff_invitation", sensitivity="sensitive")
    except Exception as e:                              # noqa: BLE001
        result = {"success": False, "error": type(e).__name__}
    finally:
        raw = None
        url = None
        body = None

    if not result.get("success"):
        # Nobody holds this link; leave no live, unsent credential behind.
        row.status = STAFF_INVITE_REVOKED
        row.revoked_at = datetime.utcnow()
        db.commit()
        return {"sent": False, "activation_id": activation_id,
                "error": str(result.get("error") or "send_failed")[:80]}

    log_action(
        db, None, actor.id, action=SENT_ACTION,
        target_type="user", target_id=user.id,
        after={"activation_id": activation_id, "token_prefix": prefix,
               "provider_message_id": result.get("provider_message_id"),
               "recipient": user.email},
        note="Staging activation link emailed. The token is not recorded.",
        commit=False)
    db.commit()
    return {"sent": True, "activation_id": activation_id,
            "token_prefix": prefix}


def run(db: Session) -> Dict[str, Any]:
    """Idempotent. Returns a NON-SECRET summary; never raises on gate failure."""
    if not _truthy(ENV_ENABLED):
        return {"status": "inert"}
    pf = preflight(db)
    if not pf["ok"]:
        log.warning("SCI staging bootstrap refused: %s", pf["reason"])
        return {"status": "refused", "reason": pf["reason"]}

    user, user_result = _ensure_user(db, pf["email"])
    membership = _ensure_membership(db, user, pf["org_id"])
    db.commit()

    state = _activation_state(db, user)
    activation: Dict[str, Any]
    if not _truthy(ENV_SEND):
        activation = {"action": "send_disabled", **state}
    elif state["accepted"]:
        activation = {"action": "skipped_accepted", **state}
    elif state["sent_markers"]:
        activation = {"action": "skipped_already_sent", **state}
    elif not user.is_active:
        activation = {"action": "skipped_user_inactive", **state}
    else:
        activation = {"action": "issued_and_sent", **state,
                      **_send_activation(db, user)}

    summary = {"status": "ok", "user": user_result, "user_id": user.id,
               "membership": membership, "activation": activation}
    log.info("SCI staging bootstrap: %s", summary)
    return summary
