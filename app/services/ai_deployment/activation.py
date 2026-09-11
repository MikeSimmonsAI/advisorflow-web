"""SWITCHING ONE ON - a request this layer makes, and a gate it never opens.

T8 MAY ASK. T6 AND T7 DECIDE. Nothing in this file writes an activation stage
directly, resolves an adapter, or reasons about whether a tool may run. It
calls `workforce.service.activate`, which writes T6's own column, and T6's
resolver still takes the MINIMUM across platform, brand, customer and employee
afterwards - so a deployment this module moves to ACTIVE under a platform that
is OFF is off, exactly as it should be.

FOUR THINGS MUST ALL BE TRUE, CHECKED AT THE MOMENT OF THE REQUEST:

    1. The deployment is in a state from which switching on is meaningful.
    2. The commercial arrangement is LIVE RIGHT NOW. Not "was when they
       clicked buy" - re-read, because a subscription can have ended between
       the checkout and this call.
    3. Readiness is recomputed HERE. The stored verdict is what a screen
       shows; a stored verdict is an answer from whenever it was stored, and
       an employee that lost its handoff owner overnight must not activate on
       yesterday's answer.
    4. An AUTHORISED HUMAN asked. Not a webhook, not a worker, not a
       reconcile - `actor` is required and a machine actor is refused.

CONTROLLED BEFORE ACTIVE. A deployment that has never run in the controlled
stage cannot go straight to ACTIVE while its brand's term sheet says otherwise,
which it does by default. Dark before live is the shape of every safe launch
this platform has done, and making the brand opt out of it is how it stays the
shape.

WHO MAY SWITCH ON LIVE OPERATION. The same answer T6 already gives on its own
customer surface: a customer administrator may prepare an employee and may not
promote it into a stage that reaches real people. That is not a capability
withheld for its own sake - controlled and active operation starts on an agreed
cohort with a cap and somebody watching. So a customer's request for a live
stage is RECORDED as a request, the deployment stays where it was, and an
authorised platform operator completes it. Nothing about that grants anybody
God authority: it is the absence of it, written down.
"""

import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.ai_deployment_models import AIEmployeeDeployment
from app.models.models import Organization, User
from app.services.ai_deployment import commerce
from app.services.ai_deployment import constants as D
from app.services.ai_deployment import lifecycle, readiness

_log = logging.getLogger(__name__)

# T6's stage names for the two stages that reach real people. Imported by value
# rather than re-spelled so a rename in T6 breaks this loudly.
_STAGE_FOR_STATE = {D.CONTROLLED: "controlled", D.ACTIVE: "active"}


def review_digest(result) -> str:
    """A stable fingerprint of exactly which review items a person saw.

    The KEYS, sorted - not the wording, and not the count. Rewording a check's
    sentence must not invalidate somebody's acknowledgement, and a NEW check
    appearing must. Sorting makes it independent of the order the checks
    happened to run in.
    """
    import hashlib
    keys = sorted(c.key for c in result.review)
    return hashlib.sha256("|".join(keys).encode("utf-8")).hexdigest()[:32]


def review_is_acknowledged(deployment: AIEmployeeDeployment, result) -> bool:
    """Has a person signed off exactly these review items?

    An acknowledgement for a DIFFERENT set of items does not count. That is
    what stops "somebody looked at this once" becoming a permanent waiver for
    whatever goes wrong next.
    """
    if not deployment.review_acknowledged_at:
        return False
    return deployment.review_acknowledged_digest == review_digest(result)


def acknowledge_review(db: Session, deployment: AIEmployeeDeployment, *,
                       actor: Optional[User], note: str = "") -> Dict[str, Any]:
    """Record that an authorised person has seen this employee's review items.

    AN OPERATOR ACTION, like activation itself. A customer administrator
    clearing their own review would be the review deciding nothing - the items
    that reach it are the ones where the platform wants a second pair of eyes
    before real people are contacted.

    Acknowledging changes NO state. It records a signature; the deployment
    still has to be switched on afterwards, by a person, in a separate act.
    """
    if actor is None or not _is_platform_operator(actor):
        raise lifecycle.DeploymentRefused(
            D.R_NOT_AUTHORIZED,
            "Clearing a readiness review is something the platform does, with "
            "the customer.")
    result = readiness.refresh(db, deployment)
    if result.verdict == D.READY_NO:
        raise lifecycle.DeploymentRefused(
            D.R_NOT_READY,
            "There are blocking problems here, not review items. They have to "
            "be fixed rather than acknowledged.")
    from datetime import datetime as _dt
    deployment.review_acknowledged_at = _dt.utcnow()
    deployment.review_acknowledged_by = getattr(actor, "id", None)
    deployment.review_acknowledged_digest = review_digest(result)
    deployment.review_acknowledgement_note = (note or "")[:255] or None
    db.flush()
    commerce.record_event(
        db, deployment, from_state=deployment.state,
        to_state=deployment.state,
        reason="Readiness review acknowledged.",
        actor_kind=D.ACTOR_HUMAN, actor_id=getattr(actor, "id", None),
        detail={"items": sorted(c.key for c in result.review), "note": note})
    return {"acknowledged": True, "items": [c.as_dict() for c in result.review],
            "readiness": result.as_dict()}


def _is_platform_operator(actor: Optional[User]) -> bool:
    """Is this the platform operator rather than a customer administrator?

    ONE QUESTION, ASKED THE WAY THE REST OF THE PLATFORM ASKS IT. `god_admin`
    is the root role; this does not introduce a second one, and there is no
    "AI workforce operator" anywhere in this package.
    """
    return getattr(actor, "role", None) == "god_admin"


def preconditions(db: Session, deployment: AIEmployeeDeployment,
                  target: str) -> Dict[str, Any]:
    """Everything that must be true, evaluated now, with nothing granted.

    Exposed separately from `request` so a screen can show an operator exactly
    what is standing in the way before they press anything, and so a test can
    assert each refusal independently of the write path.
    """
    org = (db.query(Organization)
           .filter(Organization.id == deployment.organization_id).first())
    offer = commerce.resolve_offer(db, org, deployment.template_key) \
        if org is not None else None
    result = readiness.evaluate(db, deployment)
    terms = getattr(offer, "terms", None)
    controlled_first = bool(getattr(terms, "requires_controlled_first", True))
    has_been_controlled = bool(
        deployment.requested_stage == D.CONTROLLED
        or deployment.stage_before_pause == D.CONTROLLED
        or deployment.state == D.CONTROLLED)

    refusals = []
    if deployment.state == D.RETIRED:
        refusals.append((D.R_RETIRED, "This deployment has been retired."))
    if deployment.state == D.SUSPENDED:
        refusals.append((D.R_SUSPENDED,
                         "This employee is stopped because of its commercial "
                         "standing. That has to be settled first."))
    if target not in _STAGE_FOR_STATE:
        refusals.append((D.R_ILLEGAL_TRANSITION,
                         "Only controlled and active operation are switched "
                         "on here."))
    if offer is None or not offer.is_live:
        refusals.append((D.R_NOT_ENTITLED,
                         (getattr(offer, "detail", None)
                          or "This account is not entitled to this AI "
                             "employee.")))
    if result.verdict == D.READY_NO:
        refusals.append((D.R_NOT_READY,
                         "Not ready: %s" % "; ".join(
                             c.detail or c.label for c in result.blocking[:3])))
    elif result.verdict == D.READY_REVIEW \
            and not review_is_acknowledged(deployment, result):
        refusals.append((D.R_REVIEW_REQUIRED,
                         "A person needs to look at this first: %s"
                         % "; ".join(c.detail or c.label
                                     for c in result.review[:3])))
    if (target == D.ACTIVE and controlled_first and not has_been_controlled):
        refusals.append((D.R_CONTROLLED_FIRST,
                         "This employee has to run in the controlled stage "
                         "before it runs at full capacity."))
    if not deployment.employee_id:
        refusals.append((D.R_ENGINE_UNAVAILABLE,
                         "This deployment has no AI employee behind it yet."))

    return {
        "target": target,
        "allowed": not refusals,
        "refusals": [{"code": c, "detail": m} for c, m in refusals],
        "readiness": result.as_dict(),
        "commerce": offer.as_dict() if offer is not None else None,
        "requires_controlled_first": controlled_first,
        "has_run_controlled": has_been_controlled,
        "review_acknowledged": review_is_acknowledged(deployment, result),
        "review_acknowledged_at": (
            deployment.review_acknowledged_at.isoformat()
            if deployment.review_acknowledged_at else None),
    }


def request(db: Session, deployment: AIEmployeeDeployment, target: str, *,
            actor: Optional[User], reason: str = "",
            resuming: bool = False,
            expected_state: Optional[str] = None) -> Dict[str, Any]:
    """Ask for this employee to start working. An explicit, recorded act.

    Returns a dict rather than raising on a policy refusal, because every one
    of these refusals is something a screen has to explain rather than an
    exception somebody has to translate. A genuinely illegal transition still
    raises - that is a programming error, not a business answer.
    """
    if actor is None:
        # A MACHINE MAY NOT SWITCH AN AI EMPLOYEE ON. Every automated caller in
        # this package - reconcile, the webhook hook, the resume path - either
        # carries the human who triggered it or stops short of a live stage.
        raise lifecycle.DeploymentRefused(
            D.R_NOT_AUTHORIZED,
            "Switching an AI employee on is something a person does.")

    pre = preconditions(db, deployment, target)
    if not pre["allowed"]:
        return {"granted": False, "state": deployment.state, **pre}

    if not _is_platform_operator(actor):
        # RECORDED AS A REQUEST, AND NOTHING MOVES. The customer has done their
        # half; the remaining half is an operator agreeing a cohort and a cap.
        commerce.record_event(
            db, deployment, from_state=deployment.state,
            to_state=deployment.state,
            reason="Activation requested: %s" % (target,),
            actor_kind=D.ACTOR_HUMAN, actor_id=getattr(actor, "id", None),
            detail={"requested_stage": target, "reason": reason})
        return {
            "granted": False, "pending_operator": True,
            "state": deployment.state, **pre,
            "message": ("Live operation is switched on by the platform, with "
                        "you, on an agreed group of records. Your request has "
                        "been recorded."),
        }

    stage = _STAGE_FOR_STATE[target]
    ok = _set_engine_stage(db, deployment, stage, actor=actor, reason=reason)
    if not ok:
        return {"granted": False, "state": deployment.state, **pre,
                "message": "The workforce engine would not accept that stage."}

    lifecycle.transition(
        db, deployment, target,
        reason=(reason or ("Resumed to %s." % target if resuming
                           else "Switched on by an operator.")),
        actor_kind=D.ACTOR_HUMAN, actor_id=getattr(actor, "id", None),
        detail={"stage": stage, "resuming": resuming},
        expected_state=expected_state)
    return {"granted": True, "state": deployment.state, **pre}


def _set_engine_stage(db: Session, deployment: AIEmployeeDeployment,
                      stage: str, *, actor: Optional[User],
                      reason: str) -> bool:
    """Ask T6 to move the employee's own stage. T6 owns the column.

    Also clears a T6-level pause, because an employee that was paused in the
    engine and activated here would be a deployment that says ACTIVE above an
    actor that refuses every call - two screens disagreeing about one fact.
    """
    from app.models.workforce_models import AIEmployee
    from app.services.workforce import service as wf_service

    emp = (db.query(AIEmployee)
           .filter(AIEmployee.id == deployment.employee_id,
                   AIEmployee.organization_id == deployment.organization_id)
           .first())
    if emp is None:
        return False
    try:
        if emp.paused_at is not None:
            wf_service.resume(db, emp, actor=actor,
                              reason=reason or "activated by an operator")
        wf_service.activate(db, emp, stage, actor=actor, reason=reason)
    except ValueError as exc:
        _log.warning("ai_deployment: engine refused stage %r (%s)", stage, exc)
        return False
    return True


def stand_down(db: Session, deployment: AIEmployeeDeployment, *,
               actor: Optional[User], reason: str = "") -> Dict[str, Any]:
    """Take a live employee back to READY without retiring or suspending it.

    Distinct from a pause on purpose. A pause remembers where it came from and
    is expected to go back; standing down is a decision that this employee
    should stop being live, and returning it to service is a fresh activation
    with a fresh readiness check.
    """
    if deployment.state not in D.LIVE_STATES:
        raise lifecycle.DeploymentRefused(
            D.R_ILLEGAL_TRANSITION, "This employee is not running.")
    _set_engine_stage(db, deployment, "off", actor=actor,
                      reason=reason or "stood down by an operator")
    lifecycle.stop_operational_work(
        db, deployment, reason="stood down: %s" % (reason or ""))
    lifecycle.transition(db, deployment, D.READY,
                         reason=reason or "Stood down by an operator.",
                         actor_kind=D.ACTOR_HUMAN,
                         actor_id=getattr(actor, "id", None))
    return {"state": deployment.state}


def operational_capability(db: Session) -> Dict[str, Any]:
    """What the layers below would actually let a live employee do today.

    Reported rather than assumed, on the customer's screen and God's, because
    "my AI employee is switched on and nothing is happening" has a factual
    answer and it is almost always one of these lines.
    """
    out: Dict[str, Any] = {"workforce": {}, "operations": {}}
    try:
        from app.services.workforce import activation as wf_activation
        from app.services.workforce import outbound as wf_outbound
        out["workforce"] = {
            "platform": wf_activation.scope_report(
                db, wf_activation.SCOPE_PLATFORM, ""),
            "environment_kill": wf_activation.env_kill_engaged(),
            "live_voice_enabled": wf_activation.live_voice_enabled(),
            "adapters": wf_outbound.adapter_report(),
        }
    except Exception as exc:                                  # noqa: BLE001
        out["workforce"] = {"error": str(exc)[:200]}
    try:
        from app.services.ai_operations import contracts as t7_contracts
        from app.services.ai_operations import flags as t7_flags
        out["operations"] = {"flags": t7_flags.state(),
                             "contracts": t7_contracts.availability(),
                             "t6_present": bool(t7_contracts.T6_PRESENT)}
    except Exception as exc:                                  # noqa: BLE001
        out["operations"] = {"error": str(exc)[:200]}
    return out
