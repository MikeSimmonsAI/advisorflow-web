"""HOW FAR THE WORKFORCE IS ALLOWED TO GO — and the switch that stops it.

FOUR SCOPES, MINIMUM WINS.

    platform -> brand -> customer -> employee

`resolve()` takes the LOWEST stage any level permits. That direction is the
whole safety argument: a brand cannot grant itself more than the platform
allows, a customer admin cannot promote an employee past what their brand was
given, and the only unilateral move available at any level is to restrict
further. Nobody can widen from below.

A MISSING ROW IS `off`, NOT "INHERIT". An organization nobody has configured is
an organization nobody decided to switch on, and inheriting a permissive parent
would mean creating a customer silently created a customer whose AI employees
could run. Section 33: SAFE BY DEFAULT.

THE KILL SWITCH IS NOT A STAGE. `state = off` is configuration; `kill_switch`
is an incident. They are separate columns because afterwards somebody has to be
able to tell which happened, and because a kill must not be undone by an
unrelated configuration change that happens to rewrite the stage.

WHERE THIS IS READ. `tools.authorize` calls `resolve()` on EVERY tool call, not
once per run. A worker that resolved activation when it claimed its item would
keep sending for the rest of that run after somebody hit the kill switch —
section 34 names that exact failure, so the check is at execution time.
"""

import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.workforce_models import AIEmployee, AIWorkforceActivation
from app.services.workforce import constants as C

_log = logging.getLogger(__name__)

SCOPE_PLATFORM = "platform"
SCOPE_BRAND = "brand"
SCOPE_CUSTOMER = "customer"
SCOPE_EMPLOYEE = "employee"
SCOPE_TYPES = (SCOPE_PLATFORM, SCOPE_BRAND, SCOPE_CUSTOMER, SCOPE_EMPLOYEE)

# The single platform row's scope_id. Empty string rather than NULL so the
# unique constraint on (scope_type, scope_id) actually constrains it — NULLs
# do not collide in a unique index, so a NULL scope_id would permit an
# unlimited number of contradictory platform rows.
PLATFORM_SCOPE_ID = ""


# ── THE ENVIRONMENT-LEVEL BRAKE ─────────────────────────────────────────────
#
# Two variables, both fail-safe, neither of which can turn anything ON.
#
# AI_WORKFORCE_KILL=1 forces every resolution to killed, everywhere, without a
# database write. It exists for the case where the thing that has gone wrong is
# the database, or where an operator needs the engine stopped before they can
# reach God Mode. It is checked FIRST for that reason.
#
# AI_WORKFORCE_LIVE_VOICE is the live-voice switch. It defaults to off and this
# build ships with it unset, which is what makes the statement "no AI voice
# call can be placed by any configuration of this deployment" true rather than
# aspirational.

def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_kill_engaged() -> bool:
    return _env_flag("AI_WORKFORCE_KILL", False)


def live_voice_enabled() -> bool:
    """Is an AI VOICE CALL permitted anywhere in this deployment?

    False in this build, and refused in `tools.authorize` before any provider
    is resolved — so a customer, a brand or an employee configured for voice
    still cannot place a call. The voice architecture, the provider interface,
    the eligibility path and the disposition model all exist and are exercised
    by the simulator against a fake adapter; only the live action is withheld.
    """
    return _env_flag("AI_WORKFORCE_LIVE_VOICE", C.VOICE_LIVE_DEFAULT)


class ResolvedActivation:
    """The answer, with its working shown.

    `chain` carries what each scope said, because "why is this employee off"
    is a question an operator asks constantly and "because it is off" is not an
    answer. The God screen renders this chain verbatim.
    """

    __slots__ = ("state", "killed", "killed_by_scope", "daily_cap",
                 "cohort_limit", "chain", "reason")

    def __init__(self, state, killed=False, killed_by_scope=None,
                 daily_cap=None, cohort_limit=None, chain=None, reason=None):
        self.state = state
        self.killed = killed
        self.killed_by_scope = killed_by_scope
        self.daily_cap = daily_cap
        self.cohort_limit = cohort_limit
        self.chain = chain or []
        self.reason = reason

    @property
    def may_execute(self) -> bool:
        """May a tool that reaches a real person run at all?"""
        return (not self.killed) and self.state in C.EXECUTING_STAGES

    @property
    def may_run(self) -> bool:
        """May the engine run this employee at all, in any mode?"""
        return (not self.killed) and self.state != C.OFF

    @property
    def is_simulation(self) -> bool:
        return self.state == C.SIMULATION

    @property
    def is_shadow(self) -> bool:
        return self.state == C.SHADOW

    def as_dict(self) -> Dict:
        return {
            "state": self.state,
            "killed": self.killed,
            "killed_by_scope": self.killed_by_scope,
            "may_run": self.may_run,
            "may_execute": self.may_execute,
            "daily_cap": self.daily_cap,
            "cohort_limit": self.cohort_limit,
            "reason": self.reason,
            "chain": list(self.chain),
            "live_voice_enabled": live_voice_enabled(),
        }


def _row(db: Session, scope_type: str, scope_id: str
         ) -> Optional[AIWorkforceActivation]:
    return (db.query(AIWorkforceActivation)
            .filter(AIWorkforceActivation.scope_type == scope_type,
                    AIWorkforceActivation.scope_id == (scope_id or ""))
            .first())


def _min_cap(current: Optional[int], candidate: Optional[int]) -> Optional[int]:
    """Caps narrow the same way stages do: the tightest number wins."""
    if candidate is None:
        return current
    if current is None:
        return int(candidate)
    return min(int(current), int(candidate))


def resolve(db: Session, *, employee: Optional[AIEmployee] = None,
            organization_id: Optional[str] = None,
            platform_id: Optional[str] = None) -> ResolvedActivation:
    """Resolve the effective activation for an employee, a customer or a brand.

    Call it with an employee for the full four-scope answer. Called with only
    an organization_id it answers for that customer as a whole, which is what
    the customer's own "Your AI Team" header shows.
    """
    if employee is not None:
        organization_id = employee.organization_id
        platform_id = platform_id or employee.platform_id

    # THE ENVIRONMENT BRAKE COMES FIRST and short-circuits everything. It does
    # not need the database to be readable, which is most of the point of it.
    if env_kill_engaged():
        return ResolvedActivation(
            C.OFF, killed=True, killed_by_scope="environment",
            reason="AI_WORKFORCE_KILL is set in this deployment's environment.",
            chain=[{"scope": "environment", "state": C.OFF, "killed": True}])

    state = C.ACTIVE          # start permissive, then take the minimum
    killed = False
    killed_by = None
    daily_cap = None
    cohort_limit = None
    chain: List[Dict] = []

    wanted = [(SCOPE_PLATFORM, PLATFORM_SCOPE_ID)]
    if platform_id:
        wanted.append((SCOPE_BRAND, platform_id))
    if organization_id:
        wanted.append((SCOPE_CUSTOMER, organization_id))
    if employee is not None:
        wanted.append((SCOPE_EMPLOYEE, employee.id))

    for scope_type, scope_id in wanted:
        row = _row(db, scope_type, scope_id)
        if row is None:
            # NOT CONFIGURED IS OFF. See the module header: inheriting a
            # permissive parent is how an unconfigured customer silently runs.
            #
            # The one exception is the EMPLOYEE scope, whose stage lives on the
            # employee row itself (`activation_state`) so that hiring an
            # employee does not require a second row to exist before it can be
            # described. That column also defaults to "off".
            if scope_type == SCOPE_EMPLOYEE:
                emp_state = (getattr(employee, "activation_state", None)
                             or C.DEFAULT_ACTIVATION)
                chain.append({"scope": scope_type, "scope_id": scope_id,
                              "state": emp_state, "killed": False,
                              "source": "employee.activation_state"})
                if C.ACTIVATION_RANK.get(emp_state, 0) < C.ACTIVATION_RANK[state]:
                    state = emp_state
                continue
            chain.append({"scope": scope_type, "scope_id": scope_id,
                          "state": C.DEFAULT_ACTIVATION, "killed": False,
                          "source": "no row — default"})
            state = C.OFF
            continue

        row_state = (row.state or C.DEFAULT_ACTIVATION)
        if row_state not in C.ACTIVATION_RANK:
            # An unrecognised stage is not a licence. Fail closed and say so.
            _log.warning("workforce activation: unknown stage %r at %s/%s",
                         row_state, scope_type, scope_id)
            row_state = C.OFF
        chain.append({"scope": scope_type, "scope_id": scope_id,
                      "state": row_state, "killed": bool(row.kill_switch),
                      "reason": row.reason, "source": "row"})
        if C.ACTIVATION_RANK[row_state] < C.ACTIVATION_RANK[state]:
            state = row_state
        if row.kill_switch:
            killed = True
            killed_by = killed_by or scope_type
        daily_cap = _min_cap(daily_cap, row.daily_cap)
        cohort_limit = _min_cap(cohort_limit, row.cohort_limit)

    # THE EMPLOYEE'S OWN STAGE STILL APPLIES WHEN A ROW EXISTS FOR IT. The row
    # can only narrow further, never widen past the column.
    if employee is not None:
        emp_state = getattr(employee, "activation_state", None) or C.DEFAULT_ACTIVATION
        if emp_state not in C.ACTIVATION_RANK:
            emp_state = C.OFF
        if C.ACTIVATION_RANK[emp_state] < C.ACTIVATION_RANK[state]:
            state = emp_state
        # A PAUSED EMPLOYEE IS NOT KILLED, AND IS ALSO NOT RUNNING.
        #
        # Pause is reversible and ordinary; kill is an incident. Both stop
        # work, and the difference is what the operator sees afterwards.
        if getattr(employee, "paused_at", None) is not None:
            state = C.OFF
            chain.append({"scope": "employee.pause", "scope_id": employee.id,
                          "state": C.OFF, "killed": False,
                          "reason": getattr(employee, "pause_reason", None)})
        if (getattr(employee, "status", None) or "") != "active":
            state = C.OFF
            chain.append({"scope": "employee.status", "scope_id": employee.id,
                          "state": C.OFF, "killed": False,
                          "reason": "employee status is %s"
                                    % getattr(employee, "status", None)})
        daily_cap = _min_cap(daily_cap, getattr(employee, "daily_work_cap", None))

    if killed:
        state = C.OFF

    return ResolvedActivation(state, killed=killed, killed_by_scope=killed_by,
                              daily_cap=daily_cap, cohort_limit=cohort_limit,
                              chain=chain)


# ── WRITES ──────────────────────────────────────────────────────────────────

def ensure_platform_row(db: Session) -> AIWorkforceActivation:
    """The platform row exists and starts OFF.

    Seeded rather than assumed so God Mode has something to show and so the
    dark-launch state is a fact in the database rather than the absence of one.
    """
    row = _row(db, SCOPE_PLATFORM, PLATFORM_SCOPE_ID)
    if row is None:
        row = AIWorkforceActivation(
            scope_type=SCOPE_PLATFORM, scope_id=PLATFORM_SCOPE_ID,
            state=C.DEFAULT_ACTIVATION, kill_switch=False,
            reason="Seeded disabled. AI Workforce ships dark.")
        db.add(row)
        db.flush()
    return row


def set_state(db: Session, scope_type: str, scope_id: str, state: str, *,
              actor_user_id: Optional[str] = None,
              reason: Optional[str] = None) -> AIWorkforceActivation:
    """Set the stage at one scope. Raises ValueError on an unknown stage.

    Deliberately does NOT validate the parent chain. A customer set to `active`
    under a platform set to `off` is still off, because resolution takes the
    minimum — and refusing the write would mean an operator could not stage a
    customer's configuration ahead of switching the platform on.
    """
    if scope_type not in SCOPE_TYPES:
        raise ValueError("unknown activation scope %r" % scope_type)
    if state not in C.ACTIVATION_RANK:
        raise ValueError("unknown activation stage %r" % state)
    row = _row(db, scope_type, scope_id)
    if row is None:
        row = AIWorkforceActivation(scope_type=scope_type,
                                    scope_id=(scope_id or ""))
        db.add(row)
    row.state = state
    row.reason = reason
    row.updated_by = actor_user_id
    row.updated_at = datetime.utcnow()
    db.flush()
    _log.info("AUDIT: workforce activation %s/%s -> %s by %s (%s)",
              scope_type, scope_id, state, actor_user_id, reason)
    return row


def set_kill_switch(db: Session, scope_type: str, scope_id: str,
                    engaged: bool, *, actor_user_id: Optional[str] = None,
                    reason: Optional[str] = None) -> AIWorkforceActivation:
    """Engage or release the kill switch at one scope.

    Engaging it leaves `state` untouched on purpose: releasing the kill must
    not silently restore a stage nobody re-approved, and keeping the stage
    means the operator can see what it WILL return to before they release.
    """
    if scope_type not in SCOPE_TYPES:
        raise ValueError("unknown activation scope %r" % scope_type)
    row = _row(db, scope_type, scope_id)
    if row is None:
        row = AIWorkforceActivation(scope_type=scope_type,
                                    scope_id=(scope_id or ""),
                                    state=C.DEFAULT_ACTIVATION)
        db.add(row)
    row.kill_switch = bool(engaged)
    row.reason = reason
    row.updated_by = actor_user_id
    row.updated_at = datetime.utcnow()
    db.flush()
    _log.warning("AUDIT: workforce kill switch %s at %s/%s by %s (%s)",
                 "ENGAGED" if engaged else "released", scope_type, scope_id,
                 actor_user_id, reason)
    return row


def scope_report(db: Session, scope_type: str, scope_id: str) -> Dict:
    row = _row(db, scope_type, scope_id)
    return {
        "scope_type": scope_type,
        "scope_id": scope_id or "",
        "state": (row.state if row else C.DEFAULT_ACTIVATION),
        "kill_switch": bool(row.kill_switch) if row else False,
        "daily_cap": row.daily_cap if row else None,
        "cohort_limit": row.cohort_limit if row else None,
        "reason": row.reason if row else "no row — defaults to off",
        "configured": row is not None,
        "updated_at": row.updated_at if row else None,
    }
