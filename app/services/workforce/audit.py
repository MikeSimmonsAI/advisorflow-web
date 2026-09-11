"""THE ACTOR MODEL, REDACTION, AND THE AUDIT TRAIL.

WHO ACTED, WHEN THE ACTOR IS NOT A PERSON.

`audit_log_entries.actor_user_id` is a NOT NULL foreign key to `users`. That is
correct for the platform's audit trail and it is not a mistake to work around:
an audit entry naming a user id that cannot be looked up is worse than no
entry. An AI employee is NOT a user — see the header of
app/models/workforce_models.py for why modelling one as a `users` row would
hand it an authentication surface.

So attribution is split, deliberately, across three records:

  ai_tool_executions   THE AI-NATIVE TRAIL. Every attempt, allowed or refused,
                       with the employee, the work item, the run, the tool, the
                       redacted arguments, the decision and the reason. This is
                       the authoritative record of what an AI employee did and
                       it never depends on a human being findable.

  ai_work_item_events  every state change, with actor_kind ("ai_employee",
                       "human", "system", "ai_supervisor") and actor_id.

  audit_log_entries    THE PLATFORM TRAIL, written for consequential mutations
                       so that an AI action appears in the SAME audit log an
                       operator already reads. Attributed to the human who
                       created the employee — which is true and checkable: that
                       person authorized this employee to act. The employee id,
                       the run and the work item go in `details`, so the entry
                       reads "AI employee <name> (created by <person>) did X".

If no creating human can be resolved, the platform entry is SKIPPED rather than
forged. The AI-native trail still has it, and a warning is logged. Inventing a
user id to satisfy a foreign key is how an audit log becomes fiction.

REDACTION. Nothing that reaches `ai_tool_executions.arguments` may contain a
credential, and message bodies are stored as a length and a digest rather than
as text. Section 42: do not log secrets, and do not dump conversation content
into general logs. A body is reconstructible from the platform's own
`messages` / `email_messages` tables, which is where message content belongs.
"""

import hashlib
import json
import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.workforce_models import AIEmployee
from app.services.workforce import constants as C

_log = logging.getLogger(__name__)

# Argument names whose VALUES are message content. Replaced with a length and a
# digest so a duplicate can still be detected and a body can still be compared,
# without the text living in a second place.
CONTENT_KEYS = frozenset({"body", "message", "text", "subject", "note",
                          "summary", "transcript", "rationale"})

# Argument names that must never appear at all, at any length. None of the
# registered tools take one; the filter exists so that a tool added later
# cannot leak one by accident.
SECRET_KEYS = frozenset({"password", "token", "api_key", "apikey", "secret",
                         "auth_token", "authorization", "credential",
                         "access_token", "refresh_token", "signature",
                         "private_key", "client_secret"})

MAX_VALUE_CHARS = 300


def digest(value: Any) -> str:
    """A short, stable fingerprint. Used for idempotency and for redaction."""
    try:
        raw = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = str(value)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]


def redact(args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Make an argument dict safe to store and safe to log."""
    if not isinstance(args, dict):
        return {}
    out: Dict[str, Any] = {}
    for key, value in args.items():
        k = str(key)
        lowered = k.lower()
        if lowered in SECRET_KEYS or any(s in lowered for s in ("password",
                                                                "secret",
                                                                "token")):
            out[k] = "[redacted]"
            continue
        if lowered in CONTENT_KEYS and isinstance(value, str):
            out[k] = {"_content": True, "chars": len(value),
                      "digest": digest(value)}
            continue
        if isinstance(value, str):
            out[k] = value[:MAX_VALUE_CHARS]
        elif isinstance(value, (int, float, bool)) or value is None:
            out[k] = value
        elif isinstance(value, (list, tuple)):
            out[k] = [redact(v) if isinstance(v, dict) else
                      (v[:MAX_VALUE_CHARS] if isinstance(v, str) else v)
                      for v in list(value)[:25]]
        elif isinstance(value, dict):
            out[k] = redact(value)
        else:
            out[k] = str(value)[:MAX_VALUE_CHARS]
    return out


def actor_descriptor(employee: AIEmployee) -> Dict[str, Any]:
    """The "who" block that goes on every AI-attributed record."""
    return {
        "actor_kind": C.ACTOR_AI_EMPLOYEE,
        "ai_employee_id": employee.id,
        "ai_employee_name": employee.name,
        "job_role": employee.job_role,
        "organization_id": employee.organization_id,
        "platform_id": employee.platform_id,
        "authorized_by_user_id": employee.created_by,
    }


def write_platform_audit(db: Session, employee: AIEmployee, *, action: str,
                         target_type: str, target_id: str,
                         details: Optional[Dict] = None,
                         before: Optional[Dict] = None,
                         after: Optional[Dict] = None,
                         run_id: Optional[str] = None,
                         work_item_id: Optional[str] = None,
                         note: Optional[str] = None) -> bool:
    """Mirror an AI action into the platform audit log. Best effort.

    Returns False when there is no human to attribute it to, or when the write
    fails. NEVER raises: an audit failure must not become a refusal to act on a
    decision that has already been authorized, and the AI-native trail has the
    record regardless.
    """
    actor = getattr(employee, "created_by", None)
    if not actor:
        _log.warning(
            "workforce audit: AI employee %s has no creating user; platform "
            "audit entry skipped for %s on %s/%s (the ai_tool_executions "
            "record still holds it)", employee.id, action, target_type,
            target_id)
        return False
    payload = dict(details or {})
    payload.update(actor_descriptor(employee))
    if run_id:
        payload["run_id"] = run_id
    if work_item_id:
        payload["work_item_id"] = work_item_id
    try:
        from app.routers.audit_log_router import log_action
        log_action(
            db, employee.organization_id, actor, action=action,
            target_type=target_type, target_id=str(target_id),
            details=payload, platform_id=employee.platform_id,
            before=before, after=after,
            note=note or ("Performed by AI employee %s" % employee.name),
            commit=False)
        return True
    except Exception:                                        # noqa: BLE001
        _log.exception("workforce audit: platform audit write failed for %s",
                       action)
        return False


def safe_log(level: int, message: str, *args) -> None:
    """Structured operational logging that never carries message content.

    Every call site in this package passes ids, codes and counts. This wrapper
    exists so that is a rule with one place to check rather than a habit.
    """
    _log.log(level, message, *args)
