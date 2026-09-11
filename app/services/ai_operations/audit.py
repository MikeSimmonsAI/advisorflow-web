"""THE OPERATIONAL AUDIT — and the redaction that makes it safe to keep.

EVERY CONSEQUENTIAL ACTION ANSWERS TWELVE QUESTIONS: who acted, which AI
employee, for which tenant, about which contact, under which objective, which
policy allowed it, which registered tool executed, which provider handled it,
what happened, what state changed, what it cost where measurable, whether a
human was involved, and what is next. A row that cannot answer them is not an
audit row, it is a log line.

REFUSALS ARE AUDITED WITH THE SAME WEIGHT AS SUCCESSES. "The employee tried to
text a lead in another tenant and was refused" is the most valuable line this
table can hold, and an audit that recorded only what happened would never show
it.

WHAT NEVER REACHES THIS TABLE. Message bodies, email addresses, phone numbers,
provider credentials, model output. `digest()` reduces content to a length and
a SHA-256 prefix; `redact()` strips anything that looks like a secret or a
contact detail out of a detail blob. The audit has to be readable by somebody
investigating an incident who has no business reading a family's words.
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.ai_operations_models import AIOpsAuditEntry
from app.services.ai_operations import constants as C
from app.services.ai_operations import contracts

_log = logging.getLogger(__name__)

# Keys whose values never survive redaction, whatever they contain.
_SECRET_KEYS = frozenset({
    "body", "message", "text", "subject", "content", "html", "body_html",
    "transcript", "prompt", "completion", "password", "token", "secret",
    "api_key", "apikey", "auth_token", "authorization", "credential",
    "account_sid", "phone", "to", "from", "to_address", "from_address",
    "email", "to_email", "from_email", "address",
})

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d\-\s().]{7,}\d")
_PREVIEW_LIMIT = 120


def digest(value: Optional[str]) -> Optional[str]:
    """A stable fingerprint of content, with none of the content in it.

    Used for idempotency keys and for the audit's `arguments_digest`. Twelve
    hex characters is enough to tell two messages apart and far too few to
    reverse — and reversing is not the threat anyway; accidentally storing the
    text is.
    """
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]


def preview(value: Optional[str], limit: int = _PREVIEW_LIMIT) -> Optional[str]:
    """A bounded, contact-detail-free excerpt for the operations console.

    An operator triaging a stuck conversation needs to see roughly what was
    said; they do not need the family's phone number rendered a second time in
    a table that is not the CRM.
    """
    if not value:
        return None
    cleaned = _EMAIL_RE.sub("[email]", str(value))
    cleaned = _PHONE_RE.sub("[phone]", cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def redact(detail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Strip secrets and contact details out of a detail blob, recursively."""
    if not detail:
        return {}
    out: Dict[str, Any] = {}
    for key, value in detail.items():
        lower = str(key).strip().lower()
        if lower in _SECRET_KEYS:
            if isinstance(value, str):
                out[key + "_digest"] = digest(value)
                out[key + "_length"] = len(value)
            else:
                out[key] = "[redacted]"
            continue
        if isinstance(value, dict):
            out[key] = redact(value)
        elif isinstance(value, (list, tuple)):
            out[key] = [redact(v) if isinstance(v, dict)
                        else (preview(v) if isinstance(v, str) else v)
                        for v in value][:50]
        elif isinstance(value, str):
            out[key] = preview(value, 200)
        else:
            out[key] = value
    return out


def record(db: Session, *, event_code: str,
           ctx: Optional[contracts.EmployeeContext] = None,
           organization_id: Optional[str] = None,
           severity: str = "info",
           actor_kind: str = C.ACTOR_AI_EMPLOYEE,
           actor_id: Optional[str] = None,
           actor_user_id: Optional[str] = None,
           thread_id: Optional[str] = None,
           action_id: Optional[str] = None,
           communication_id: Optional[str] = None,
           work_item_id: Optional[str] = None,
           subject_type: Optional[str] = None,
           subject_id: Optional[str] = None,
           operation: Optional[str] = None,
           tool_key: Optional[str] = None,
           channel: Optional[str] = None,
           provider: Optional[str] = None,
           decision: Optional[str] = None,
           denial_code: Optional[str] = None,
           authority: Optional[str] = None,
           activation_state: Optional[str] = None,
           eligibility_result: Optional[str] = None,
           state_from: Optional[str] = None,
           state_to: Optional[str] = None,
           outcome: Optional[str] = None,
           human_involved: bool = False,
           simulated: bool = True,
           estimated_cost_usd: Optional[float] = None,
           next_action: Optional[str] = None,
           message: Optional[str] = None,
           detail: Optional[Dict[str, Any]] = None) -> Optional[AIOpsAuditEntry]:
    """Write one audit row. Flushes, never commits.

    NEVER COMMITS, DELIBERATELY. The audit row belongs to the same transaction
    as the thing it describes: an action that is rolled back must not leave an
    audit row claiming it happened, and an action that commits must not be
    able to commit without its audit row. The caller owns the transaction.

    NEVER RAISES. An audit failure must not turn a successful, already-sent
    message into an exception the caller reads as "it did not send" — that
    reading is how a duplicate gets sent. The failure is logged at ERROR,
    because an audit that is quietly not being written is a serious condition
    even though it is not this request's problem.
    """
    try:
        row = AIOpsAuditEntry(
            organization_id=(organization_id
                             or (ctx.organization_id if ctx else None)),
            platform_id=(ctx.platform_id if ctx else None),
            event_code=event_code, severity=severity,
            actor_kind=actor_kind,
            actor_id=(actor_id or (ctx.employee_id if ctx else None)),
            actor_user_id=actor_user_id,
            employee_id=(ctx.employee_id if ctx else None),
            thread_id=thread_id, action_id=action_id,
            communication_id=communication_id, work_item_id=work_item_id,
            subject_type=subject_type, subject_id=subject_id,
            operation=operation, tool_key=tool_key, channel=channel,
            provider=provider, decision=decision, denial_code=denial_code,
            authority=authority,
            activation_state=(activation_state
                              or (ctx.activation_state if ctx else None)),
            eligibility_result=eligibility_result,
            state_from=state_from, state_to=state_to, outcome=outcome,
            human_involved=bool(human_involved), simulated=bool(simulated),
            estimated_cost_usd=estimated_cost_usd, next_action=next_action,
            message=(message or "")[:480] or None,
            detail=json.dumps(redact(detail)) if detail else None,
            created_at=datetime.utcnow())
        db.add(row)
        db.flush()
        return row
    except Exception as exc:                                 # noqa: BLE001
        _log.error("ai_operations: AUDIT WRITE FAILED for %s (%s)",
                   event_code, exc)
        return None


def human_action(db: Session, *, organization_id: str, actor_user_id: str,
                 action: str, target_type: str, target_id: str,
                 details: Optional[Dict] = None) -> None:
    """Mirror a PERSON's operations-console action into the platform audit.

    A human pausing an employee or taking over a conversation is a human
    action, and it belongs where humans' actions are looked for —
    `audit_log_entries`, through the helper 57 other call sites already use.
    The operations audit records it too; this makes it visible to an admin who
    has never heard of the AI workforce.
    """
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, organization_id=organization_id,
                   actor_user_id=actor_user_id, action=action,
                   target_type=target_type, target_id=target_id,
                   details=redact(details or {}), commit=False)
    except Exception as exc:                                 # noqa: BLE001
        _log.warning("ai_operations: platform audit mirror failed (%s)", exc)
