"""MANAGEMENT ACTIONS - every one of them somebody else's to perform.

THE RULE, AND THE REASON IT IS STRUCTURAL RATHER THAN WRITTEN DOWN.

T9 is not permission to bypass T6, T7 or T8. A management screen that could
pause an employee by writing `paused_at`, or resume one by setting an
activation stage, would be a second control plane - the customer's own screens
would disagree with it, the owning system's checks would not have run, and the
deployment event log would have a hole where the change was.

So every action here is a DELEGATION, and the delegation is enforced three
ways:

    The action must be in `MANAGEMENT_ACTIONS`. An unknown name is refused,
    not handled generically.

    The action must have an entry in `ACTION_AUTHORITY` naming the module that
    performs it. That name is written onto every `ai_management_actions` row,
    so "T9 delegates" is checkable from the data rather than from this
    docstring.

    The performing module runs its OWN checks. `lifecycle.pause` refuses a
    retired deployment; `handoff.accept` refuses another tenant's handoff;
    `stop.take_over` writes the ownership T7's orchestrator reads on every
    operation. T9 passes the actor through and does not pre-authorise anything
    on their behalf.

WHAT CANNOT BE ASKED FOR HERE. Granting a tool, changing authority, inventing
consent, enabling a channel, enabling voice, switching on live sending,
altering billing, changing commercial entitlement, bypassing readiness,
touching God authority. None of these has a constant in the vocabulary, so
none of them can be named - and `perform` refuses anything it cannot name.

REFUSALS ARE RECORDED. An action the owning system refused is a fact a manager
needs ("I pressed pause and nothing happened"), with that system's own refusal
code kept verbatim rather than translated into something friendlier.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.ai_deployment_models import AIEmployeeDeployment
from app.models.ai_operations_models import AIConversationThread
from app.models.workforce_intelligence_models import AIManagementAction
from app.models.workforce_models import AIHandoff
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence.scope import Scope, ScopeRefused

_log = logging.getLogger(__name__)
_audit = logging.getLogger("security.authz")


class ActionRefused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _record(db: Session, *, scope: Scope, action: str, organization_id: str,
            outcome: str, user, target_kind=None, target_id=None,
            employee_id=None, deployment_id=None, platform_id=None,
            refusal_code=None, reason=None, detail=None,
            origin_kind=None, origin_id=None) -> AIManagementAction:
    row = AIManagementAction(
        organization_id=organization_id, platform_id=platform_id,
        employee_id=employee_id, deployment_id=deployment_id,
        action=action, target_kind=target_kind, target_id=target_id,
        reason=(reason or "")[:255] or None,
        authority_path=C.ACTION_AUTHORITY.get(action),
        outcome=outcome, refusal_code=refusal_code,
        detail=(json.dumps(detail, default=str)[:4000] if detail else None),
        origin_kind=origin_kind, origin_id=origin_id,
        requested_by=getattr(user, "id", None),
        requested_at=datetime.utcnow())
    db.add(row)
    db.flush()
    return row


def perform(db: Session, scope: Scope, *, action: str, user,
            target_id: Optional[str] = None,
            reason: Optional[str] = None,
            payload: Optional[Dict[str, Any]] = None,
            origin_kind: Optional[str] = None,
            origin_id: Optional[str] = None) -> Dict[str, Any]:
    """Ask the owning system to do something, and record what it answered.

    READ-ONLY OBSERVATION IS REFUSED HERE AS WELL AS AT THE ROUTE. The route's
    `require_not_observation` is the security boundary; this second check
    exists because `perform` is also reachable from a background pass and from
    tests, and a guard that only lives on an HTTP decorator is a guard with
    one entrance.
    """
    payload = payload or {}
    if action not in C.MANAGEMENT_ACTIONS:
        raise ActionRefused(C.R_UNKNOWN_ACTION,
                            "That is not an action this surface performs.")
    if scope.read_only and action in C.ACTIONS_TOUCHING_OTHER_SYSTEMS:
        raise ActionRefused(C.R_OBSERVATION_MODE,
                            "This view is read-only.")
    handler = _HANDLERS.get(action)
    if handler is None:
        raise ActionRefused(C.R_NOT_PERMITTED_BY_T9,
                            "AdvisorFlow does not perform that from here.")
    try:
        return handler(db, scope, user=user, target_id=target_id,
                       reason=reason, payload=payload,
                       origin_kind=origin_kind, origin_id=origin_id)
    except ScopeRefused as exc:
        _audit.warning("t9 action refused by scope actor=%s action=%s "
                       "target=%s", getattr(user, "id", None), action,
                       target_id)
        raise ActionRefused(exc.code, exc.message)


# ---------------------------------------------------------------------------
# THE HANDLERS - each one loads inside the scope, then calls the owner
# ---------------------------------------------------------------------------


def _load_deployment(db: Session, scope: Scope,
                     deployment_id: str) -> AIEmployeeDeployment:
    row = (scope.apply(db.query(AIEmployeeDeployment),
                       AIEmployeeDeployment.organization_id)
           .filter(AIEmployeeDeployment.id == deployment_id).first())
    if row is None:
        # 404-shaped, deliberately. Telling a caller that a deployment exists
        # in another workspace is a disclosure, and it is how ids get
        # enumerated one at a time.
        raise ActionRefused(C.R_RECORD_NOT_FOUND,
                            "That AI employee is not in this workspace.")
    return row


def _pause(db, scope, *, user, target_id, reason, payload, origin_kind,
           origin_id):
    dep = _load_deployment(db, scope, target_id)
    from app.services.ai_deployment import lifecycle as t8_lifecycle
    try:
        t8_lifecycle.pause(db, dep,
                           reason=(reason or "Paused from AI Workforce "
                                             "Command."),
                           actor=user)
    except Exception as exc:                                 # noqa: BLE001
        code = getattr(exc, "code", None) or C.R_LAYER_UNAVAILABLE
        _record(db, scope=scope, action=C.M_PAUSE_EMPLOYEE,
                organization_id=dep.organization_id, outcome=C.OUTCOME_REFUSED,
                user=user, target_kind="deployment", target_id=dep.id,
                employee_id=dep.employee_id, deployment_id=dep.id,
                platform_id=dep.platform_id, refusal_code=str(code),
                reason=reason, detail={"error": str(exc)},
                origin_kind=origin_kind, origin_id=origin_id)
        raise ActionRefused(str(code), str(exc))
    _record(db, scope=scope, action=C.M_PAUSE_EMPLOYEE,
            organization_id=dep.organization_id, outcome=C.OUTCOME_PERFORMED,
            user=user, target_kind="deployment", target_id=dep.id,
            employee_id=dep.employee_id, deployment_id=dep.id,
            platform_id=dep.platform_id, reason=reason,
            origin_kind=origin_kind, origin_id=origin_id)
    return {"performed": True, "action": C.M_PAUSE_EMPLOYEE,
            "state": dep.state,
            "performed_by": C.ACTION_AUTHORITY[C.M_PAUSE_EMPLOYEE]}


def _resume(db, scope, *, user, target_id, reason, payload, origin_kind,
            origin_id):
    dep = _load_deployment(db, scope, target_id)
    from app.services.ai_deployment import lifecycle as t8_lifecycle
    try:
        result = t8_lifecycle.resume(
            db, dep, actor=user,
            reason=(reason or "Resumed from AI Workforce Command."))
    except Exception as exc:                                 # noqa: BLE001
        code = getattr(exc, "code", None) or C.R_LAYER_UNAVAILABLE
        _record(db, scope=scope, action=C.M_RESUME_EMPLOYEE,
                organization_id=dep.organization_id, outcome=C.OUTCOME_REFUSED,
                user=user, target_kind="deployment", target_id=dep.id,
                employee_id=dep.employee_id, deployment_id=dep.id,
                platform_id=dep.platform_id, refusal_code=str(code),
                reason=reason, detail={"error": str(exc)},
                origin_kind=origin_kind, origin_id=origin_id)
        raise ActionRefused(str(code), str(exc))
    _record(db, scope=scope, action=C.M_RESUME_EMPLOYEE,
            organization_id=dep.organization_id, outcome=C.OUTCOME_PERFORMED,
            user=user, target_kind="deployment", target_id=dep.id,
            employee_id=dep.employee_id, deployment_id=dep.id,
            platform_id=dep.platform_id, reason=reason,
            detail=result if isinstance(result, dict) else None,
            origin_kind=origin_kind, origin_id=origin_id)
    # RESUME IS RE-CHECKED, NOT RESTORED. T8 verifies entitlement and
    # readiness on the way back up, so the answer may be "it is ready and
    # somebody still has to start it" - which is reported rather than hidden.
    return {"performed": True, "action": C.M_RESUME_EMPLOYEE,
            "state": dep.state, "result": result,
            "performed_by": C.ACTION_AUTHORITY[C.M_RESUME_EMPLOYEE]}


def _load_thread(db: Session, scope: Scope,
                 thread_id: str) -> AIConversationThread:
    row = (scope.apply(db.query(AIConversationThread),
                       AIConversationThread.organization_id)
           .filter(AIConversationThread.id == thread_id).first())
    if row is None:
        raise ActionRefused(C.R_RECORD_NOT_FOUND,
                            "That conversation is not in this workspace.")
    return row


def _takeover(db, scope, *, user, target_id, reason, payload, origin_kind,
              origin_id):
    thread = _load_thread(db, scope, target_id)
    from app.services.ai_operations import stop as t7_stop
    t7_stop.take_over(db, thread, user_id=getattr(user, "id", None),
                      reason_code=(payload.get("reason_code")
                                   or "manual_takeover"),
                      note=reason)
    _record(db, scope=scope, action=C.M_REQUEST_TAKEOVER,
            organization_id=thread.organization_id,
            outcome=C.OUTCOME_PERFORMED, user=user,
            target_kind="conversation", target_id=thread.id,
            employee_id=thread.employee_id, platform_id=thread.platform_id,
            reason=reason, origin_kind=origin_kind, origin_id=origin_id)
    return {"performed": True, "action": C.M_REQUEST_TAKEOVER,
            "thread_id": thread.id,
            "performed_by": C.ACTION_AUTHORITY[C.M_REQUEST_TAKEOVER]}


def _release(db, scope, *, user, target_id, reason, payload, origin_kind,
             origin_id):
    thread = _load_thread(db, scope, target_id)
    from app.services.ai_operations import stop as t7_stop
    # THE DEFAULT IS THAT THE AI DOES NOT RESUME. T7 makes that choice and T9
    # passes the caller's explicit answer through rather than defaulting it
    # the other way for convenience.
    resumed = bool(payload.get("ai_may_resume", False))
    t7_stop.release(db, thread, user_id=getattr(user, "id", None),
                    ai_may_resume=resumed, note=reason)
    _record(db, scope=scope, action=C.M_RELEASE_TAKEOVER,
            organization_id=thread.organization_id,
            outcome=C.OUTCOME_PERFORMED, user=user,
            target_kind="conversation", target_id=thread.id,
            employee_id=thread.employee_id, platform_id=thread.platform_id,
            reason=reason, detail={"ai_may_resume": resumed},
            origin_kind=origin_kind, origin_id=origin_id)
    return {"performed": True, "action": C.M_RELEASE_TAKEOVER,
            "ai_may_resume": resumed,
            "performed_by": C.ACTION_AUTHORITY[C.M_RELEASE_TAKEOVER]}


def _cancel_objective(db, scope, *, user, target_id, reason, payload,
                      origin_kind, origin_id):
    thread = _load_thread(db, scope, target_id)
    from app.services.ai_operations import constants as O
    from app.services.ai_operations import stop as t7_stop
    stopped = t7_stop.stop_thread(
        db, None, thread, reason=O.STOP_OBJECTIVE_CANCELLED,
        actor_kind=O.ACTOR_HUMAN, actor_id=getattr(user, "id", None),
        detail={"note": reason} if reason else None)
    _record(db, scope=scope, action=C.M_CANCEL_OBJECTIVE,
            organization_id=thread.organization_id,
            outcome=C.OUTCOME_PERFORMED if stopped else C.OUTCOME_REFUSED,
            refusal_code=None if stopped else "already_stopped",
            user=user, target_kind="conversation", target_id=thread.id,
            employee_id=thread.employee_id, platform_id=thread.platform_id,
            reason=reason, origin_kind=origin_kind, origin_id=origin_id)
    # A CANCELLED OBJECTIVE IS NOT A SUCCESS, and nothing here writes an
    # outcome. The ledger counts what already happened; cancelling ends the
    # work and adds no outcome of its own.
    return {"performed": bool(stopped), "action": C.M_CANCEL_OBJECTIVE,
            "already_stopped": not stopped,
            "performed_by": C.ACTION_AUTHORITY[C.M_CANCEL_OBJECTIVE]}


def _load_handoff(db: Session, scope: Scope, handoff_id: str) -> AIHandoff:
    row = (scope.apply(db.query(AIHandoff), AIHandoff.organization_id)
           .filter(AIHandoff.id == handoff_id).first())
    if row is None:
        raise ActionRefused(C.R_RECORD_NOT_FOUND,
                            "That handoff is not in this workspace.")
    return row


def _accept_handoff(db, scope, *, user, target_id, reason, payload,
                    origin_kind, origin_id):
    row = _load_handoff(db, scope, target_id)
    from app.services.workforce import handoff as wf_handoff
    try:
        wf_handoff.accept(db, row, user)
    except PermissionError as exc:
        # T6 CHECKS TENANCY ITSELF and refuses. T9 does not catch that and
        # retry with a wider scope - it records the refusal and passes it on.
        _record(db, scope=scope, action=C.M_ACCEPT_HANDOFF,
                organization_id=row.organization_id,
                outcome=C.OUTCOME_REFUSED, user=user, target_kind="handoff",
                target_id=row.id, employee_id=row.employee_id,
                refusal_code=C.R_TENANT_MISMATCH, detail={"error": str(exc)},
                origin_kind=origin_kind, origin_id=origin_id)
        raise ActionRefused(C.R_TENANT_MISMATCH, str(exc))
    _record(db, scope=scope, action=C.M_ACCEPT_HANDOFF,
            organization_id=row.organization_id, outcome=C.OUTCOME_PERFORMED,
            user=user, target_kind="handoff", target_id=row.id,
            employee_id=row.employee_id, reason=reason,
            origin_kind=origin_kind, origin_id=origin_id)
    return {"performed": True, "action": C.M_ACCEPT_HANDOFF,
            "status": row.status,
            "performed_by": C.ACTION_AUTHORITY[C.M_ACCEPT_HANDOFF]}


def _resolve_handoff(db, scope, *, user, target_id, reason, payload,
                     origin_kind, origin_id):
    row = _load_handoff(db, scope, target_id)
    from app.services.workforce import handoff as wf_handoff
    try:
        wf_handoff.resolve(db, row, user, note=reason)
    except PermissionError as exc:
        _record(db, scope=scope, action=C.M_RESOLVE_HANDOFF,
                organization_id=row.organization_id,
                outcome=C.OUTCOME_REFUSED, user=user, target_kind="handoff",
                target_id=row.id, employee_id=row.employee_id,
                refusal_code=C.R_TENANT_MISMATCH, detail={"error": str(exc)},
                origin_kind=origin_kind, origin_id=origin_id)
        raise ActionRefused(C.R_TENANT_MISMATCH, str(exc))
    _record(db, scope=scope, action=C.M_RESOLVE_HANDOFF,
            organization_id=row.organization_id, outcome=C.OUTCOME_PERFORMED,
            user=user, target_kind="handoff", target_id=row.id,
            employee_id=row.employee_id, reason=reason,
            origin_kind=origin_kind, origin_id=origin_id)
    return {"performed": True, "action": C.M_RESOLVE_HANDOFF,
            "status": row.status,
            "performed_by": C.ACTION_AUTHORITY[C.M_RESOLVE_HANDOFF]}


def _acknowledge_attention(db, scope, *, user, target_id, reason, payload,
                           origin_kind, origin_id):
    from app.services.workforce_intelligence import attention as t9_attention
    row = t9_attention.acknowledge(db, scope, target_id, user=user,
                                   note=reason)
    if row is None:
        raise ActionRefused(C.R_RECORD_NOT_FOUND,
                            "That item is not in this workspace.")
    _record(db, scope=scope, action=C.M_ACKNOWLEDGE_EXCEPTION,
            organization_id=row.organization_id, outcome=C.OUTCOME_PERFORMED,
            user=user, target_kind="attention_item", target_id=row.id,
            employee_id=row.employee_id, platform_id=row.platform_id,
            reason=reason, origin_kind="attention_item", origin_id=row.id)
    return {"performed": True, "action": C.M_ACKNOWLEDGE_EXCEPTION,
            "state": row.state,
            "performed_by": C.ACTION_AUTHORITY[C.M_ACKNOWLEDGE_EXCEPTION]}


def _resolve_attention(db, scope, *, user, target_id, reason, payload,
                       origin_kind, origin_id):
    from app.services.workforce_intelligence import attention as t9_attention
    row = t9_attention.resolve(db, scope, target_id, user=user, note=reason)
    if row is None:
        raise ActionRefused(C.R_RECORD_NOT_FOUND,
                            "That item is not in this workspace.")
    _record(db, scope=scope, action=C.M_RESOLVE_ATTENTION,
            organization_id=row.organization_id, outcome=C.OUTCOME_PERFORMED,
            user=user, target_kind="attention_item", target_id=row.id,
            employee_id=row.employee_id, platform_id=row.platform_id,
            reason=reason, origin_kind="attention_item", origin_id=row.id)
    return {"performed": True, "action": C.M_RESOLVE_ATTENTION,
            "state": row.state,
            "note": ("If the underlying condition is still true, the next "
                     "pass will put this back on your list."),
            "performed_by": C.ACTION_AUTHORITY[C.M_RESOLVE_ATTENTION]}


def _acknowledge_finding(db, scope, *, user, target_id, reason, payload,
                         origin_kind, origin_id):
    from app.services.workforce_intelligence import findings as t9_findings
    row = t9_findings.acknowledge(db, scope, target_id, user=user)
    if row is None:
        raise ActionRefused(C.R_RECORD_NOT_FOUND,
                            "That finding is not in this workspace.")
    _record(db, scope=scope, action=C.M_ACKNOWLEDGE_FINDING,
            organization_id=row.organization_id, outcome=C.OUTCOME_PERFORMED,
            user=user, target_kind="finding", target_id=row.id,
            employee_id=row.employee_id, platform_id=row.platform_id,
            reason=reason, origin_kind=origin_kind, origin_id=origin_id)
    return {"performed": True, "action": C.M_ACKNOWLEDGE_FINDING,
            "state": row.state,
            "performed_by": C.ACTION_AUTHORITY[C.M_ACKNOWLEDGE_FINDING]}


def _acknowledge_contradiction(db, scope, *, user, target_id, reason, payload,
                               origin_kind, origin_id):
    from app.services.workforce_intelligence import reconciliation as t9_rec
    row = t9_rec.acknowledge(db, scope, target_id, user=user)
    if row is None:
        raise ActionRefused(C.R_RECORD_NOT_FOUND,
                            "That contradiction is not in this workspace.")
    _record(db, scope=scope, action=C.M_ACKNOWLEDGE_CONTRADICTION,
            organization_id=row.organization_id, outcome=C.OUTCOME_PERFORMED,
            user=user, target_kind="reconciliation_finding", target_id=row.id,
            employee_id=row.employee_id, platform_id=row.platform_id,
            reason=reason, origin_kind=origin_kind, origin_id=origin_id)
    # ACKNOWLEDGING IS NOT FIXING, and the payload says so. The contradiction
    # is between two other systems and stays true until one of them changes.
    return {"performed": True, "action": C.M_ACKNOWLEDGE_CONTRADICTION,
            "state": row.state,
            "remediation_owner": row.remediation_owner,
            "note": ("Acknowledged. The disagreement is still there - it is "
                     "fixed through %s." % row.remediation_owner),
            "performed_by":
                C.ACTION_AUTHORITY[C.M_ACKNOWLEDGE_CONTRADICTION]}


def _escalate(db, scope, *, user, target_id, reason, payload, origin_kind,
              origin_id):
    from app.services.workforce_intelligence import incidents as t9_incidents
    try:
        return t9_incidents.escalate(db, scope, target_id, user=user,
                                     note=reason)
    except LookupError as exc:
        raise ActionRefused(C.R_RECORD_NOT_FOUND, str(exc))


def _record_review(db, scope, *, user, target_id, reason, payload,
                   origin_kind, origin_id):
    from app.services.workforce_intelligence import review as t9_review
    try:
        result = t9_review.decide(
            db, scope, source_kind=payload.get("source_kind") or "",
            source_id=payload.get("source_id") or target_id or "",
            decision=payload.get("decision") or "", user=user, note=reason)
    except ValueError as exc:
        raise ActionRefused(C.R_NOT_PERMITTED_BY_T9, str(exc))
    except LookupError as exc:
        raise ActionRefused(C.R_RECORD_NOT_FOUND, str(exc))
    _record(db, scope=scope, action=C.M_RECORD_REVIEW,
            organization_id=scope.scope_id or "", outcome=C.OUTCOME_PERFORMED,
            user=user, target_kind="review", target_id=result.get("id"),
            reason=reason, detail={"decision": result.get("decision")},
            origin_kind=origin_kind, origin_id=origin_id)
    return result


_HANDLERS = {
    C.M_PAUSE_EMPLOYEE: _pause,
    C.M_RESUME_EMPLOYEE: _resume,
    C.M_REQUEST_TAKEOVER: _takeover,
    C.M_RELEASE_TAKEOVER: _release,
    C.M_CANCEL_OBJECTIVE: _cancel_objective,
    C.M_ACCEPT_HANDOFF: _accept_handoff,
    C.M_RESOLVE_HANDOFF: _resolve_handoff,
    C.M_ACKNOWLEDGE_EXCEPTION: _acknowledge_attention,
    C.M_RESOLVE_ATTENTION: _resolve_attention,
    C.M_ACKNOWLEDGE_FINDING: _acknowledge_finding,
    C.M_ACKNOWLEDGE_CONTRADICTION: _acknowledge_contradiction,
    C.M_ESCALATE: _escalate,
    C.M_RECORD_REVIEW: _record_review,
}

# EVERY DECLARED ACTION HAS A HANDLER AND EVERY HANDLER HAS AN AUTHORITY.
# Asserted at import so a constant added without a handler - or a handler
# without a named owning system - fails the moment the module loads rather
# than the first time somebody presses the button.
assert set(_HANDLERS) == set(C.MANAGEMENT_ACTIONS), (
    "T9 management actions and handlers have drifted: %s"
    % sorted(set(_HANDLERS) ^ set(C.MANAGEMENT_ACTIONS)))
assert all(a in C.ACTION_AUTHORITY for a in _HANDLERS), (
    "Every management action must name the system that performs it.")


def history(db: Session, scope: Scope, *, limit: int = 100):
    """What has been done through this surface, and by whom."""
    rows = (scope.apply(db.query(AIManagementAction),
                        AIManagementAction.organization_id)
            .order_by(AIManagementAction.requested_at.desc())
            .limit(limit).all())
    return [{
        "id": r.id, "action": r.action, "outcome": r.outcome,
        "refusal_code": r.refusal_code,
        "performed_by_system": r.authority_path,
        "target_kind": r.target_kind, "target_id": r.target_id,
        "employee_id": r.employee_id, "reason": r.reason,
        "requested_by": r.requested_by,
        "at": r.requested_at.isoformat() if r.requested_at else None,
    } for r in rows]
