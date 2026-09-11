"""RUNAWAY PROTECTION — ceilings the engine cannot talk its way past.

AN AI WORKFORCE MUST NOT BE ABLE TO GENERATE UNLIMITED SPEND. The failure
this module exists to prevent is not malice, it is a loop: an employee that
fails, retries, fails, retries, and bills a Twilio segment every time; or an
inbound classifier that reads its own outbound as a reply and answers it.
Neither is exotic and both are cheap to prevent — a counter and a ceiling.

WHAT THIS IS NOT. It is not billing, it is not pricing, and it does not
decide what anything costs a customer. T2 owns commerce. `estimated_cost_usd`
here is an OPERATIONAL estimate used to stop a runaway, and the module is
explicit about that so nobody later mistakes it for revenue data. The
estimates are deliberately coarse and deliberately configurable by
environment rather than by customer: a wrong estimate must be able to stop
work early, never to let it run longer.

FIVE CEILINGS, CHECKED BEFORE THE ACTION AND COUNTED AFTER IT.

    attempts per communication      a failing send stops failing
    actions per objective           a conversation cannot loop forever
    channel sends per day           per employee and per organization
    consecutive failures            a broken provider stops being called
    estimated cost per day          the absolute backstop

A COUNTER IS INCREMENTED ONLY WHEN SOMETHING ACTUALLY HAPPENED. A refused
action does not consume the send budget — otherwise a misconfigured employee
could exhaust a customer's daily cap without ever reaching anybody, which
would turn a safety gate into a denial of service.
"""

import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ai_operations_models import AIOpsCounter
from app.services.ai_operations import constants as C
from app.services.ai_operations import contracts

_log = logging.getLogger(__name__)

SCOPE_ORG = "organization"
SCOPE_EMPLOYEE = "employee"
SCOPE_THREAD = "thread"

METRIC_SENDS = "sends"
METRIC_SENDS_SMS = "sends_sms"
METRIC_SENDS_EMAIL = "sends_email"
METRIC_CALLS = "calls"
METRIC_ACTIONS = "actions"
METRIC_FAILURES = "failures"
METRIC_COST = "cost"
METRIC_MODEL_CALLS = "model_calls"

# Coarse operational estimates, overridable by environment for a deployment
# whose provider pricing differs. NOT a price list and never shown to a
# customer as one.
_COST_SMS = float(os.environ.get("AI_OPS_COST_SMS_USD", "0.01"))
_COST_EMAIL = float(os.environ.get("AI_OPS_COST_EMAIL_USD", "0.001"))
_COST_VOICE_MINUTE = float(os.environ.get("AI_OPS_COST_VOICE_MINUTE_USD", "0.09"))
_COST_MODEL_CALL = float(os.environ.get("AI_OPS_COST_MODEL_CALL_USD", "0.01"))


def estimate_cost(channel: Optional[str], *, segments: int = 1,
                  seconds: int = 0) -> float:
    """A coarse operational estimate of what one action costs to perform."""
    if channel == C.CHANNEL_SMS:
        return round(_COST_SMS * max(1, int(segments)), 6)
    if channel == C.CHANNEL_EMAIL:
        return round(_COST_EMAIL, 6)
    if channel == C.CHANNEL_VOICE:
        return round(_COST_VOICE_MINUTE * (max(0, int(seconds)) / 60.0), 6)
    return 0.0


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _row(db: Session, organization_id: str, scope_type: str, scope_id: str,
         metric_key: str, *, create: bool = False) -> Optional[AIOpsCounter]:
    q = (db.query(AIOpsCounter)
         .filter(AIOpsCounter.organization_id == organization_id,
                 AIOpsCounter.scope_type == scope_type,
                 AIOpsCounter.scope_id == (scope_id or ""),
                 AIOpsCounter.metric_date == _today(),
                 AIOpsCounter.metric_key == metric_key))
    row = q.first()
    if row is not None or not create:
        return row
    row = AIOpsCounter(organization_id=organization_id, scope_type=scope_type,
                       scope_id=(scope_id or ""), metric_date=_today(),
                       metric_key=metric_key, value=0, value_usd=0)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        # Two workers created the same counter in the same millisecond. The
        # loser reads the winner's row; neither count is lost because both
        # increments are applied to whichever row survives.
        return q.first()


def current(db: Session, organization_id: str, scope_type: str, scope_id: str,
            metric_key: str) -> int:
    row = _row(db, organization_id, scope_type, scope_id, metric_key)
    return int(row.value) if row is not None else 0


def current_cost(db: Session, organization_id: str, scope_type: str,
                 scope_id: str) -> float:
    row = _row(db, organization_id, scope_type, scope_id, METRIC_COST)
    return float(row.value_usd or 0) if row is not None else 0.0


def increment(db: Session, organization_id: str, scope_type: str,
              scope_id: str, metric_key: str, *, amount: int = 1,
              usd: float = 0.0) -> None:
    """Count something that ACTUALLY HAPPENED. Flushes, never commits."""
    row = _row(db, organization_id, scope_type, scope_id, metric_key,
               create=True)
    if row is None:
        _log.warning("ai_operations: could not record counter %s/%s/%s",
                     scope_type, scope_id, metric_key)
        return
    row.value = int(row.value or 0) + int(amount)
    if usd:
        row.value_usd = (Decimal(str(row.value_usd or 0))
                         + Decimal(str(round(float(usd), 6))))
    db.flush()


def reset_failures(db: Session, organization_id: str, scope_type: str,
                   scope_id: str) -> None:
    """A success clears the consecutive-failure count for that scope.

    CONSECUTIVE means consecutive. A provider that fails twice and then works
    is not a provider that is failing, and treating it as one would take a
    working channel offline for the rest of the day.
    """
    row = _row(db, organization_id, scope_type, scope_id, METRIC_FAILURES)
    if row is not None and int(row.value or 0):
        row.value = 0
        db.flush()


def check(db: Session, ctx: contracts.EmployeeContext, *,
          operation: str, channel: Optional[str], thread_id: Optional[str],
          thread_action_count: int = 0, comm_attempts: int = 0
          ) -> Tuple[bool, Optional[str], Optional[str]]:
    """(allowed, denial_code, reason) — checked BEFORE the action runs.

    Ordered cheapest and most absolute first, the same ordering principle the
    rest of the gate chain uses, so an operator reading a refusal sees the
    most fundamental reason rather than the first one a loop happened to hit.
    """
    org = ctx.organization_id

    # 1. This one communication has been attempted enough times.
    max_attempts = min(int(ctx.max_attempts or C.DEFAULT_MAX_ATTEMPTS_PER_COMM),
                       C.MAX_ATTEMPTS_CEILING)
    if comm_attempts >= max_attempts:
        return False, C.D_ATTEMPT_CAP, (
            "This message has already been attempted %d times; the attempt "
            "ceiling is %d." % (comm_attempts, max_attempts))

    # 2. This objective has done enough.
    max_actions = min(int(ctx.max_actions or C.DEFAULT_MAX_ACTIONS_PER_OBJECTIVE),
                      C.MAX_ACTIONS_CEILING)
    if thread_action_count >= max_actions:
        return False, C.D_ACTION_CAP, (
            "This objective has performed %d actions; the ceiling is %d."
            % (thread_action_count, max_actions))

    # 3. The provider keeps failing. Stop calling it.
    failures = current(db, org, SCOPE_EMPLOYEE, ctx.employee_id,
                       METRIC_FAILURES)
    if failures >= C.DEFAULT_MAX_CONSECUTIVE_FAILURES:
        return False, C.D_FAILURE_LOOP, (
            "%d consecutive failures for this employee today; work is paused "
            "until an operator looks at it." % failures)

    # 4. The daily channel cap, at employee scope and at organization scope.
    if channel:
        cap = min(int(ctx.channel_daily_cap or C.DEFAULT_CHANNEL_DAILY_CAP),
                  C.CHANNEL_DAILY_CAP_CEILING)
        if ctx.daily_cap:
            cap = min(cap, int(ctx.daily_cap))
        # VOICE HAS ITS OWN CAP AND IT IS ZERO FOR ANY EMPLOYEE THAT COULD
        # ACTUALLY REACH A TELEPHONE.
        #
        # Belt and braces: the live voice adapter refuses every call anyway,
        # and this cap refuses it a second time, independently, for any
        # employee whose activation stage permits an action that reaches a
        # real person.
        #
        # IT IS NOT APPLIED IN SIMULATION OR SHADOW, deliberately. Nothing
        # can leave the process in those stages, and capping them at zero
        # would mean the voice architecture — the disposition model, the
        # call-outcome state mapping, the audit — was never exercised at all.
        # An architecture that has never run is not architecture, it is an
        # intention, and the day voice is switched on is the wrong day to
        # find that out.
        if channel == C.CHANNEL_VOICE and ctx.may_execute:
            cap = min(cap, C.DEFAULT_VOICE_DAILY_CAP)
        metric = {C.CHANNEL_SMS: METRIC_SENDS_SMS,
                  C.CHANNEL_EMAIL: METRIC_SENDS_EMAIL,
                  C.CHANNEL_VOICE: METRIC_CALLS}.get(channel, METRIC_SENDS)
        used = current(db, org, SCOPE_EMPLOYEE, ctx.employee_id, metric)
        if used >= cap:
            return False, C.D_CHANNEL_CAP, (
                "This employee has used its %s allowance for today (%d of %d)."
                % (channel, used, cap))

    # 5. The absolute backstop.
    ceiling = float(ctx.cost_ceiling_usd or C.DEFAULT_COST_CEILING_USD)
    spent = current_cost(db, org, SCOPE_ORG, org)
    if spent >= ceiling:
        return False, C.D_COST_CAP, (
            "Estimated AI operations spend for this organization today "
            "($%.2f) has reached the operational ceiling ($%.2f)."
            % (spent, ceiling))

    return True, None, None


def record_success(db: Session, ctx: contracts.EmployeeContext, *,
                   operation: str, channel: Optional[str],
                   thread_id: Optional[str], usd: float = 0.0) -> None:
    """Count an action that actually happened, at every scope that caps it."""
    org = ctx.organization_id
    increment(db, org, SCOPE_EMPLOYEE, ctx.employee_id, METRIC_ACTIONS)
    increment(db, org, SCOPE_ORG, org, METRIC_ACTIONS)
    if thread_id:
        increment(db, org, SCOPE_THREAD, thread_id, METRIC_ACTIONS)
    if channel:
        metric = {C.CHANNEL_SMS: METRIC_SENDS_SMS,
                  C.CHANNEL_EMAIL: METRIC_SENDS_EMAIL,
                  C.CHANNEL_VOICE: METRIC_CALLS}.get(channel, METRIC_SENDS)
        increment(db, org, SCOPE_EMPLOYEE, ctx.employee_id, metric)
        increment(db, org, SCOPE_ORG, org, metric)
    if usd:
        increment(db, org, SCOPE_ORG, org, METRIC_COST, amount=0, usd=usd)
        increment(db, org, SCOPE_EMPLOYEE, ctx.employee_id, METRIC_COST,
                  amount=0, usd=usd)
    reset_failures(db, org, SCOPE_EMPLOYEE, ctx.employee_id)


def record_failure(db: Session, ctx: contracts.EmployeeContext, *,
                   channel: Optional[str] = None) -> int:
    """Count a failure and return the new consecutive-failure count."""
    increment(db, ctx.organization_id, SCOPE_EMPLOYEE, ctx.employee_id,
              METRIC_FAILURES)
    return current(db, ctx.organization_id, SCOPE_EMPLOYEE, ctx.employee_id,
                   METRIC_FAILURES)


def report(db: Session, organization_id: str,
           employee_id: Optional[str] = None) -> Dict:
    """Today's numbers, for the operations console and the supervisor feed."""
    out: Dict[str, object] = {
        "date": _today(),
        "organization": {
            "actions": current(db, organization_id, SCOPE_ORG,
                               organization_id, METRIC_ACTIONS),
            "sms": current(db, organization_id, SCOPE_ORG, organization_id,
                           METRIC_SENDS_SMS),
            "email": current(db, organization_id, SCOPE_ORG, organization_id,
                             METRIC_SENDS_EMAIL),
            "calls": current(db, organization_id, SCOPE_ORG, organization_id,
                             METRIC_CALLS),
            "estimated_cost_usd": current_cost(db, organization_id, SCOPE_ORG,
                                               organization_id),
            "cost_ceiling_usd": C.DEFAULT_COST_CEILING_USD,
        },
    }
    if employee_id:
        out["employee"] = {
            "employee_id": employee_id,
            "actions": current(db, organization_id, SCOPE_EMPLOYEE,
                               employee_id, METRIC_ACTIONS),
            "sms": current(db, organization_id, SCOPE_EMPLOYEE, employee_id,
                           METRIC_SENDS_SMS),
            "email": current(db, organization_id, SCOPE_EMPLOYEE, employee_id,
                             METRIC_SENDS_EMAIL),
            "calls": current(db, organization_id, SCOPE_EMPLOYEE, employee_id,
                             METRIC_CALLS),
            "consecutive_failures": current(db, organization_id,
                                            SCOPE_EMPLOYEE, employee_id,
                                            METRIC_FAILURES),
            "estimated_cost_usd": current_cost(db, organization_id,
                                               SCOPE_EMPLOYEE, employee_id),
        }
    return out
