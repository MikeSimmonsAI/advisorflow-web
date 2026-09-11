"""THE DEPLOYMENT LIFECYCLE - and the two guarantees that make it safe.

IDEMPOTENCY IS A DATABASE CONSTRAINT, NOT A CHECK. Selecting a job produces a
deployment keyed by (organization_id, provisioning_key). Two clicks on Hire,
a retried request and a duplicated provisioning worker all carry the same key,
and the second one loses at `uq_ai_deployment_provisioning_key` rather than at
a query somebody wrote correctly. `select` catches that loss and returns the
row the winner created, so the caller sees success either way - which is what
idempotent means, as opposed to "usually only happens once".

TRANSITIONS ARE CONDITIONAL UPDATES, NOT READ-THEN-WRITE. `transition` moves a
deployment only if it is still in the state the caller believed it was in.
Two admins on two screens, a webhook arriving mid-edit and a stale browser tab
all reduce to the same shape: one UPDATE matches a row and the other matches
none, and the one that matched none is told so instead of overwriting.

WHAT THIS MODULE DOES NOT DO. It does not decide readiness (readiness.py), it
does not decide entitlement (commerce.py, which asks T2), and it does not
switch anything on (activation.py, which asks T6). It owns where a deployment
is and what creating or retiring one means.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ai_deployment_models import AIEmployeeDeployment
from app.models.models import Organization, User
from app.services.ai_deployment import commerce
from app.services.ai_deployment import constants as D

_log = logging.getLogger(__name__)


class DeploymentRefused(RuntimeError):
    """A precondition failed. `code` is one of constants.R_*."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class StaleDeployment(DeploymentRefused):
    """Somebody else moved this deployment while this request was in flight."""

    def __init__(self, message: str):
        super().__init__(D.R_STALE_VIEW, message)


class IllegalTransition(DeploymentRefused):
    def __init__(self, message: str):
        super().__init__(D.R_ILLEGAL_TRANSITION, message)


# ---------------------------------------------------------------------------
# READING
# ---------------------------------------------------------------------------

def get(db: Session, organization_id: str,
        deployment_id: str) -> Optional[AIEmployeeDeployment]:
    """Load INSIDE this tenant. The filter is in the query, not after it.

    A check made on a row already loaded is a check somebody can forget; a
    filter that never returns the row cannot be. The answer for another
    customer's deployment is the same None as for one that does not exist, so
    this cannot be used to find out which ids are real.
    """
    return (db.query(AIEmployeeDeployment)
            .filter(AIEmployeeDeployment.id == deployment_id,
                    AIEmployeeDeployment.organization_id == organization_id)
            .first())


def list_for_org(db: Session, organization_id: str, *,
                 include_retired: bool = True) -> List[AIEmployeeDeployment]:
    q = (db.query(AIEmployeeDeployment)
         .filter(AIEmployeeDeployment.organization_id == organization_id))
    if not include_retired:
        q = q.filter(AIEmployeeDeployment.state != D.RETIRED)
    return q.order_by(AIEmployeeDeployment.created_at.asc()).all()


# ---------------------------------------------------------------------------
# SELECTING - the first half of hiring
# ---------------------------------------------------------------------------

def select(db: Session, *, org: Organization, template_key: str,
           actor: Optional[User] = None, provisioning_key: Optional[str] = None,
           display_name: Optional[str] = None) -> AIEmployeeDeployment:
    """Record that this customer wants this job. Idempotent, and off.

    NOTHING IS CREATED IN THE ENGINE HERE. A selection is an intention; the
    actor is created by `provision`, once there is a configuration worth giving
    it. Creating the actor at selection would mean an abandoned setup left a
    real AI employee sitting in a customer's workspace.

    THE COMMERCIAL GATE IS APPLIED, AND IT IS NOT THE ONLY ONE. A customer who
    is not entitled cannot select; a customer who is entitled still has to
    configure, pass readiness and be switched on by somebody. Selling is not
    activating - section 3.
    """
    from app.services.ai_deployment import capacity

    offer = commerce.resolve_offer(db, org, template_key)
    if offer.state == D.COMM_NOT_OFFERED:
        raise DeploymentRefused(
            D.R_NOT_OFFERED,
            offer.detail or "That AI employee is not offered here.")
    if offer.state == D.COMM_PENDING:
        raise DeploymentRefused(
            D.R_ENTITLEMENT_PENDING,
            "A checkout for this AI employee is open and has not been paid. "
            "Opening a checkout is not paying for one.")
    if not offer.is_live:
        raise DeploymentRefused(
            D.R_NOT_ENTITLED,
            offer.detail or "This account is not entitled to that AI employee.")

    cap = capacity.may_hire(db, org, template_key, terms=offer.terms)
    if not cap.allowed:
        raise DeploymentRefused(cap.code, cap.reason)

    key = (provisioning_key or "").strip() or ("sel-%s" % uuid.uuid4().hex)
    existing = (db.query(AIEmployeeDeployment)
                .filter(AIEmployeeDeployment.organization_id == org.id,
                        AIEmployeeDeployment.provisioning_key == key)
                .first())
    if existing is not None:
        return existing

    row = AIEmployeeDeployment(
        organization_id=org.id,
        platform_id=getattr(org, "platform_id", None),
        template_key=template_key,
        offering_id=getattr(offer.offering, "id", None),
        display_name=(display_name
                      or getattr(offer.offering, "display_name", None)
                      or template_key.replace("_", " ").title())[:120],
        state=D.SELECTED,
        commercial_state=offer.state,
        commercial_detail=(offer.detail or "")[:255] or None,
        commercial_checked_at=datetime.utcnow(),
        entitlement_key=offer.entitlement_key,
        catalog_item_key=offer.catalog_item_key,
        purchase_id=offer.purchase_id,
        provisioning_key=key,
        config=json.dumps({}),
        requested_by=getattr(actor, "id", None))
    db.add(row)
    try:
        # A SAVEPOINT, so losing the race does not poison the caller's
        # transaction. Without it the IntegrityError would leave the session
        # unusable and the "just read the winner's row" recovery could not run.
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        db.expunge(row)
        winner = (db.query(AIEmployeeDeployment)
                  .filter(AIEmployeeDeployment.organization_id == org.id,
                          AIEmployeeDeployment.provisioning_key == key)
                  .first())
        if winner is None:                                    # pragma: no cover
            raise
        _log.info("ai_deployment: duplicate selection %s collapsed onto %s",
                  key, winner.id)
        return winner

    commerce.record_event(db, row, from_state=None, to_state=D.SELECTED,
                          reason="Selected from the catalogue.",
                          actor_kind=D.ACTOR_HUMAN,
                          actor_id=getattr(actor, "id", None),
                          detail={"template_key": template_key,
                                  "provisioning_key": key})
    return row


# ---------------------------------------------------------------------------
# PROVISIONING - creating the actor, exactly once
# ---------------------------------------------------------------------------

def provision(db: Session, deployment: AIEmployeeDeployment, *,
              actor: Optional[User] = None) -> Dict[str, Any]:
    """Create the T6 employee behind this deployment. IDEMPOTENT.

    Called on every configuration save rather than once at a moment somebody
    has to remember, which is only safe because it is idempotent: a deployment
    that already names an employee returns that employee untouched.

    THE ACTOR IS CREATED THROUGH T6's OWN `hire`, never by writing
    `ai_employees` here. That function is what grants the authority rows, and
    an employee created around it would be one with no grants and no audit
    line - the exact shape T6 warns about.
    """
    from app.services.workforce import service as wf_service
    from app.services.ai_deployment import configuration as cfg_mod

    if deployment.state == D.RETIRED:
        raise DeploymentRefused(D.R_RETIRED,
                                "This deployment has been retired.")
    if deployment.employee_id:
        # ALREADY DONE. Re-running the configuration push is safe and is what
        # makes "call provision on every save" correct.
        cfg_mod.sync_to_employee(db, deployment)
        return {"employee_id": deployment.employee_id, "created": False}

    config = cfg_mod.config_of(deployment)
    channels = [c for c in (config.get("channels") or []) if isinstance(c, str)]
    hours = config.get("working_hours") or config.get("hours") or {}
    handoff_user = config.get("handoff_user_id") or config.get("handoff_to")

    try:
        emp = wf_service.hire(
            db, organization_id=deployment.organization_id,
            template_key=deployment.template_key,
            name=deployment.display_name, actor=actor,
            config=config,
            channels=channels or None,
            operating_hours=hours if isinstance(hours, dict) else None,
            timezone=config.get("timezone"),
            handoff_user_id=handoff_user or None,
            handoff_queue=config.get("handoff_team") or None,
            audience_criteria=(config.get("audience")
                               if isinstance(config.get("audience"), dict)
                               else None),
            knowledge_binding=({"kinds": config["knowledge_kinds"]}
                               if isinstance(config.get("knowledge_kinds"),
                                             list)
                               and config.get("knowledge_kinds") else None))
    except ValueError as exc:
        raise DeploymentRefused(D.R_ENGINE_UNAVAILABLE, str(exc))

    deployment.employee_id = emp.id
    deployment.updated_at = datetime.utcnow()
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        # TWO PROVISIONERS, ONE ACTOR. `uq_ai_deployment_employee` refused the
        # second claim. The savepoint above has already rolled ITSELF back, so
        # the caller's transaction is intact - re-reading is enough, and a
        # `db.rollback()` here would throw away the hire this request had
        # legitimately done on the way in.
        db.expire(deployment)
        return {"employee_id": deployment.employee_id, "created": False,
                "raced": True}

    commerce.record_event(
        db, deployment, from_state=deployment.state, to_state=deployment.state,
        reason="AI employee created. It starts switched off.",
        actor_kind=D.ACTOR_SYSTEM, actor_id=getattr(actor, "id", None),
        detail={"employee_id": emp.id})
    return {"employee_id": emp.id, "created": True}


# ---------------------------------------------------------------------------
# TRANSITIONS
# ---------------------------------------------------------------------------

_TIMESTAMP_FIELDS = {
    D.PAUSED: ("paused_at", "paused_by"),
    D.RETIRED: ("retired_at", "retired_by"),
}


def transition(db: Session, deployment: AIEmployeeDeployment, to_state: str, *,
               reason: str = "", actor_kind: str = D.ACTOR_HUMAN,
               actor_id: Optional[str] = None,
               detail: Optional[Dict] = None,
               expected_state: Optional[str] = None) -> AIEmployeeDeployment:
    """Move a deployment. Refuses an edge the table does not contain.

    `expected_state` is the caller's view of the world - a browser tab that has
    been open for ten minutes, or a worker that read the row a second ago. When
    it is given and does not match, the move is refused as stale rather than
    applied on top of whatever happened in between.
    """
    if to_state not in D.ROW_STATES:
        raise IllegalTransition("Unknown deployment state %r." % to_state)

    current = deployment.state
    if expected_state is not None and expected_state != current:
        raise StaleDeployment(
            "This employee is %s now, not %s. Reload and try again."
            % (D.STATE_LABELS.get(current, current),
               D.STATE_LABELS.get(expected_state, expected_state)))

    allowed = D.ALLOWED_TRANSITIONS.get(current, set())
    if to_state not in allowed:
        raise IllegalTransition(
            "An employee that is %s cannot become %s."
            % (D.STATE_LABELS.get(current, current),
               D.STATE_LABELS.get(to_state, to_state)))

    values: Dict[str, Any] = {
        "state": to_state,
        "state_reason": (reason or "")[:255] or None,
        "updated_at": datetime.utcnow(),
    }
    if to_state == D.PAUSED:
        values["paused_at"] = datetime.utcnow()
        values["paused_by"] = actor_id
        # WHERE TO COME BACK TO, recorded now rather than inferred later.
        if current in D.LIVE_STATES:
            values["stage_before_pause"] = current
    if to_state == D.SUSPENDED:
        values["suspended_at"] = datetime.utcnow()
        values["suspend_reason"] = (reason or "")[:255] or None
        if current in D.LIVE_STATES:
            values["stage_before_pause"] = current
    if to_state == D.RETIRED:
        values["retired_at"] = datetime.utcnow()
        values["retired_by"] = actor_id
        values["retire_reason"] = (reason or "")[:255] or None
    if to_state in (D.READY, D.CONFIGURING, D.VALIDATION_REQUIRED):
        values["paused_at"] = None
        values["paused_by"] = None
        values["suspended_at"] = None
    if to_state in D.LIVE_STATES:
        values["activated_at"] = datetime.utcnow()
        values["activated_by"] = actor_id
        values["requested_stage"] = to_state
        values["paused_at"] = None
        values["paused_by"] = None

    # THE CONDITIONAL UPDATE. `state == current` is the whole race guarantee:
    # a second writer that moved this row first leaves zero rows matching, and
    # this caller finds out instead of overwriting them.
    changed = (db.query(AIEmployeeDeployment)
               .filter(AIEmployeeDeployment.id == deployment.id,
                       AIEmployeeDeployment.state == current)
               .update(values, synchronize_session=False))
    if not changed:
        db.expire(deployment)
        raise StaleDeployment(
            "This employee changed while that was being saved. Reload and "
            "try again.")
    db.expire(deployment)
    db.flush()

    commerce.record_event(db, deployment, from_state=current,
                          to_state=to_state, reason=reason,
                          actor_kind=actor_kind, actor_id=actor_id,
                          detail=detail)
    return deployment


# ---------------------------------------------------------------------------
# STOPPING WORK
# ---------------------------------------------------------------------------

def stop_operational_work(db: Session, deployment: AIEmployeeDeployment, *,
                          reason: str, disable_actor: bool = False) -> Dict[str, int]:
    """Stop this employee's work now, without deleting any of it.

    THREE THINGS, AND NONE OF THEM IS A DELETE.

      1. The T6 employee's own stage goes to `off` and it is paused, so the
         next tool call is refused at execution time rather than at the next
         schedule - which is the distinction T6's own header insists on.
      2. Its queued work items are paused, so the screens stop showing records
         as "working" while nothing is happening.
      3. T7's scheduled actions for it are cancelled, so nothing that was
         already booked fires later against an employee nobody is paying for.
         An orphan schedule is the specific failure section 12 names.

    Every one of these is best-effort and logged rather than raised: a stop
    that could fail its caller would be a stop that does not happen when the
    caller is a webhook.
    """
    out = {"work_items_paused": 0, "scheduled_actions_cancelled": 0}
    if not deployment.employee_id:
        return out

    try:
        from app.models.workforce_models import AIEmployee
        from app.services.workforce import constants as WC
        from app.services.workforce import queue as wf_queue
        from app.services.workforce import service as wf_service

        emp = (db.query(AIEmployee)
               .filter(AIEmployee.id == deployment.employee_id,
                       AIEmployee.organization_id == deployment.organization_id)
               .first())
        if emp is not None:
            emp.activation_state = WC.OFF
            if disable_actor:
                wf_service.disable(db, emp, reason=reason)
            else:
                if emp.paused_at is None:
                    wf_service.pause(db, emp, reason=reason, pause_queue=True)
                else:
                    out["work_items_paused"] = wf_queue.pause_all(
                        db, employee_id=emp.id, reason=reason)
            db.flush()
    except Exception as exc:                                  # noqa: BLE001
        _log.warning("ai_deployment: could not stop T6 work for %s (%s)",
                     deployment.id, exc)

    out["scheduled_actions_cancelled"] = cancel_scheduled_actions(
        db, deployment, reason=reason)
    return out


def cancel_scheduled_actions(db: Session, deployment: AIEmployeeDeployment, *,
                             reason: str) -> int:
    """Cancel T7's pending follow-ups for this employee. Never deletes one.

    A cancelled action keeps its row and its history; what changes is that it
    will not fire. T7's own follow-up path re-evaluates at EXECUTION time and
    would refuse anyway - this is the second lock, and it is the one that stops
    a queue of work sitting there looking live on an operations console.
    """
    if not deployment.employee_id:
        return 0
    try:
        from app.models.ai_operations_models import AIScheduledAction
    except Exception:                                         # noqa: BLE001
        return 0
    try:
        rows = (db.query(AIScheduledAction)
                .filter(AIScheduledAction.organization_id
                        == deployment.organization_id,
                        AIScheduledAction.employee_id == deployment.employee_id)
                .all())
        count = 0
        for row in rows:
            status = (getattr(row, "status", "") or "").lower()
            if status in ("cancelled", "canceled", "done", "executed",
                          "failed"):
                continue
            row.status = "cancelled"
            row.status_detail = (reason or "")[:255] or None
            count += 1
        db.flush()
        return count
    except Exception as exc:                                  # noqa: BLE001
        _log.info("ai_deployment: scheduled-action cancel skipped (%s)", exc)
        return 0


# ---------------------------------------------------------------------------
# OPERATOR ACTIONS
# ---------------------------------------------------------------------------

def pause(db: Session, deployment: AIEmployeeDeployment, *, reason: str,
          actor: Optional[User] = None,
          expected_state: Optional[str] = None) -> AIEmployeeDeployment:
    """Stop this employee. Reversible, and it says where it came back from."""
    if deployment.state == D.RETIRED:
        raise DeploymentRefused(D.R_RETIRED, "This deployment is retired.")
    transition(db, deployment, D.PAUSED,
               reason=reason or "Paused by an operator.",
               actor_kind=D.ACTOR_HUMAN, actor_id=getattr(actor, "id", None),
               expected_state=expected_state)
    stop_operational_work(db, deployment, reason="deployment paused: %s"
                                                 % (reason or ""))
    return deployment


def resume(db: Session, deployment: AIEmployeeDeployment, *,
           actor: Optional[User] = None,
           reason: str = "Resumed by an operator.") -> Dict[str, Any]:
    """Bring a paused employee back - to where it was, and only if it still may.

    RE-CHECKED, NOT RESTORED BLINDLY. Entitlement may have lapsed while it was
    paused, and its configuration may no longer pass. A resume that trusted the
    stage it recorded would be the mechanism by which a customer whose
    subscription ended got their AI employee back.
    """
    from app.services.ai_deployment import activation as t8_activation
    from app.services.ai_deployment import readiness as t8_readiness

    if deployment.state != D.PAUSED:
        raise DeploymentRefused(
            D.R_ILLEGAL_TRANSITION,
            "This employee is not paused.")

    org = (db.query(Organization)
           .filter(Organization.id == deployment.organization_id).first())
    offer = commerce.resolve_offer(db, org, deployment.template_key)
    if not offer.is_live:
        transition(db, deployment, D.SUSPENDED,
                   reason=offer.detail or "Entitlement is no longer live.",
                   actor_kind=D.ACTOR_COMMERCE)
        return {"state": deployment.state, "resumed": False,
                "detail": offer.detail}

    result = t8_readiness.refresh(db, deployment)
    target = deployment.stage_before_pause
    if result.verdict == D.READY_NO or target not in D.LIVE_STATES:
        transition(db, deployment,
                   D.VALIDATION_REQUIRED if result.verdict == D.READY_NO
                   else D.READY,
                   reason=("Resumed; not returned to live operation because "
                           "%s." % ("it was not live before it was paused"
                                    if target not in D.LIVE_STATES
                                    else "it is no longer ready")),
                   actor_kind=D.ACTOR_HUMAN,
                   actor_id=getattr(actor, "id", None))
        return {"state": deployment.state, "resumed": True,
                "readiness": result.verdict, "returned_to_live": False}

    # Back to the stage it was paused from, through T6's own activation path.
    #
    # WHICH MAY REFUSE, and the answer is reported rather than assumed. A
    # scope above this employee can have been lowered while it was paused, or a
    # review item can have appeared that nobody has signed off - and a resume
    # that reported success while the employee sat there paused would be the
    # worst of both, because nobody would go and look.
    granted = t8_activation.request(db, deployment, target, actor=actor,
                                    reason=reason, resuming=True)
    returned = deployment.state in D.LIVE_STATES
    if not returned:
        transition(db, deployment, D.READY,
                   reason="Resumed; not returned to live operation because "
                          "something above it does not currently permit it.",
                   actor_kind=D.ACTOR_HUMAN,
                   actor_id=getattr(actor, "id", None),
                   detail={"refusals": granted.get("refusals")})
    return {"state": deployment.state, "resumed": True,
            "readiness": result.verdict, "returned_to_live": returned,
            "refusals": granted.get("refusals")}


def configure(db: Session, deployment: AIEmployeeDeployment,
              answers: Dict[str, Any], *, actor: Optional[User] = None,
              merge: bool = True) -> Dict[str, Any]:
    """Save business answers, create the actor if needed, and re-verdict.

    ONE CALL DOES THE WHOLE STEP because the alternative is three calls a
    caller can do in the wrong order. Saving without provisioning leaves a
    configured deployment with nothing behind it; provisioning without
    re-evaluating leaves a screen showing yesterday's verdict beside today's
    answers.

    IT NEVER SWITCHES ANYTHING ON. The best outcome of this function is READY,
    which means somebody may now make a separate, recorded decision to start
    the employee working.
    """
    from app.services.ai_deployment import configuration as cfg_mod
    from app.services.ai_deployment import readiness as t8_readiness

    if deployment.state == D.RETIRED:
        raise DeploymentRefused(D.R_RETIRED,
                                "This deployment has been retired.")
    if deployment.state == D.SUSPENDED:
        raise DeploymentRefused(
            D.R_SUSPENDED,
            "This employee is stopped because of its commercial standing. "
            "That has to be settled before it can be set up.")

    was_live = deployment.state in D.LIVE_STATES
    applied = cfg_mod.apply(db, deployment, answers, actor=actor, merge=merge)
    provisioned = provision(db, deployment, actor=actor)
    result = t8_readiness.refresh(db, deployment)

    # A LIVE EMPLOYEE STAYS LIVE WHILE IT IS STILL READY, and drops out of live
    # operation the moment its own configuration stops passing. Editing an
    # employee's hours should not stop it; deleting its handoff owner should.
    if was_live:
        if result.verdict == D.READY_NO:
            from app.services.ai_deployment import activation as t8_activation
            t8_activation.stand_down(
                db, deployment, actor=actor,
                reason="Configuration changed and no longer passes readiness.")
        return {"state": deployment.state, "readiness": result.verdict,
                "problems": applied["problems"],
                "employee_id": provisioned.get("employee_id"),
                "configuration_version": applied["version"]}

    # READY COVERS TWO VERDICTS, AND VALIDATION_REQUIRED COVERS ONE.
    #
    # `validation_required` means something is BROKEN - a required answer
    # missing, a handoff owner who left, a feature the workspace does not have.
    # `review_required` means everything works and a person should look before
    # real people are contacted, which is a different state of the world and
    # belongs on the readiness verdict rather than in the lifecycle. Collapsing
    # them would make "needs attention" mean both "you have work to do" and
    # "we have work to do", and a customer cannot act on the second one.
    target = (D.VALIDATION_REQUIRED if result.verdict == D.READY_NO
              else D.READY)
    if deployment.state == D.SELECTED and target == D.READY:
        # SELECTED cannot reach READY in one edge - the table says so, and it
        # is right: a deployment configured in a single save still passed
        # through being configured, and the event log should show that it did.
        transition(db, deployment, D.CONFIGURING,
                   reason="Setup started.", actor_kind=D.ACTOR_HUMAN,
                   actor_id=getattr(actor, "id", None))
    if deployment.state != target:
        transition(db, deployment, target,
                   reason=("Ready to start." if target == D.READY
                           else "Setup saved; something is still needed."),
                   actor_kind=D.ACTOR_HUMAN,
                   actor_id=getattr(actor, "id", None),
                   detail={"readiness": result.verdict})
    return {"state": deployment.state, "readiness": result.verdict,
            "problems": applied["problems"],
            "employee_id": provisioned.get("employee_id"),
            "configuration_version": applied["version"]}
