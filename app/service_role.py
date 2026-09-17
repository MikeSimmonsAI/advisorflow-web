"""WHICH SERVICE IS THIS, AND WHAT IS IT ALLOWED TO RUN?

THE DEFECT THIS EXISTS TO CLOSE
-------------------------------
`advisorflow-backend` and `advisorflow-voice` run the SAME ASGI app -
`uvicorn app.main:app` - and the startup handler started five background
schedulers unconditionally. So both processes ran all five. Measured in
production, over 24 hours:

    ai_conversation_loop        1,436 runs   (720 expected at 2 min)
    review_request_loop            96 runs   ( 48 expected at 30 min)
    cadence_loop                   48 runs   ( 24 expected at 1 hr)
    support_intelligence_loop       9 runs   (  4 expected at 6 hr)

Every loop at exactly 2x. Visible at row level in `job_runs` as pairs of
identical runs 1-2 seconds apart. Roughly 3,100 scheduled executions a day
against a 256 MB database with nobody logged in.

Two processes racing on the same rows is not merely wasteful. The AI
conversation loop reads `pipeline_conversations` where `next_send_at <= now`
and sends from it; two runners a second apart is a double-send waiting for the
gap between read and write to widen under load.

WHY A ROLE AND NOT A BOOLEAN PER LOOP
-------------------------------------
Five booleans is five chances to set four of them. The question being asked is
not "should this process run the cadence loop" - it is "WHAT IS THIS PROCESS
FOR", which is one fact with one answer. Everything else is derived from it
here, in one table, where the whole ownership map can be read at once.

FAIL CLOSED
-----------
A process whose role is unknown starts NO optional scheduler. Not the safest
option for uptime - the safest option for CUSTOMERS. Every loop below can
eventually send somebody a text message or an email, and a process that cannot
say what it is has no business doing that. The refusal is logged once, loudly,
naming the variable to set.

This follows `SKIP_STARTUP_MIGRATIONS`, which this codebase already uses to
stop `advisorflow-voice` changing the schema: an explicit environment variable
per service, declared in render.yaml. Same convention, wider question.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List

from app.models.job_models import JobName, LOOP_JOB_NAMES

logger = logging.getLogger(__name__)

# ── the roles ───────────────────────────────────────────────────────────────

ROLE_BACKEND = "backend"   # the customer-facing API. Owns platform schedulers.
ROLE_VOICE = "voice"       # Twilio media-stream websockets, and nothing else.
ROLE_JOB = "job"           # a cron container or one-off job script.
ROLE_UNKNOWN = "unknown"   # could not be determined. Starts nothing optional.

KNOWN_ROLES = (ROLE_BACKEND, ROLE_VOICE, ROLE_JOB)

ENV_VAR = "SERVICE_ROLE"


# ── THE OWNERSHIP TABLE ─────────────────────────────────────────────────────
#
# ONE OWNER PER SCHEDULED TASK. The whole point.
#
# Every loop is currently owned by the backend. That is deliberate and it is
# not the end state:
#
#   ai_conversation_loop   A dedicated cron (advisorflow-ai-conversation)
#                          already exists - and cannot do this work today,
#                          because it has no OPENAI_API_KEY and no sender
#                          configured. Handing it ownership now would stop AI
#                          conversations in production. It becomes the owner by
#                          changing one line here, once its configuration is
#                          fixed and its fallback behaviour is safe.
#
#   cadence_loop           A dedicated cron (advisorflow-cadence-job) exists
#                          but runs DAILY where this runs hourly, and cadence
#                          sending is globally disabled pending an activation
#                          decision. Moving ownership is part of that decision,
#                          not part of this cleanup.
#
#   review_request_loop    No cron service exists. app/crons/review_request_cron
#   support_intelligence   .py is a module, not a deployed service.
#   session_cleanup        No cron service exists.
#
# What this pass fixes is DUPLICATION, not placement: voice stops running
# platform schedulers, so every number above halves. Where the work should
# eventually live is recorded here so the answer is not lost.
SCHEDULER_OWNER: Dict[str, str] = {
    JobName.AI_CONVERSATION:     ROLE_BACKEND,
    JobName.CADENCE_LOOP:        ROLE_BACKEND,
    JobName.REVIEW_REQUEST:      ROLE_BACKEND,
    JobName.SUPPORT_INTELLIGENCE: ROLE_BACKEND,
    JobName.SESSION_CLEANUP:     ROLE_BACKEND,
}

# A guard rather than a comment: a loop added to JobName without an owner here
# is a loop whose ownership nobody decided, and the honest default for that is
# "nothing runs it" rather than "every web process runs it".
UNASSIGNED = tuple(n for n in LOOP_JOB_NAMES if n not in SCHEDULER_OWNER)


def normalize(raw) -> str:
    value = (raw or "").strip().lower()
    return value if value in KNOWN_ROLES else ROLE_UNKNOWN


def current_role() -> str:
    """This process's declared role, or ROLE_UNKNOWN.

    Read fresh from the environment on every call rather than cached at import:
    the test suite sets it per test, and a value frozen at import time would
    make the first test to touch it decide for all the others.
    """
    return normalize(os.environ.get(ENV_VAR))


def is_known() -> bool:
    return current_role() in KNOWN_ROLES


def owns(job_name: str) -> bool:
    """May THIS process run that scheduled task?

    False for every job when the role is unknown, and False for a job with no
    declared owner. Both are the fail-closed answer.
    """
    role = current_role()
    if role not in KNOWN_ROLES:
        return False
    return SCHEDULER_OWNER.get(job_name) == role


def startup_plan() -> Dict[str, object]:
    """What this process will and will not start, and why.

    Returned as data rather than printed, so the startup handler can log it,
    a test can assert it, and a diagnostic endpoint can show it without any of
    them re-deriving the rule.
    """
    role = current_role()
    known = role in KNOWN_ROLES
    start: List[str] = []
    skip: List[str] = []
    for name in LOOP_JOB_NAMES:
        (start if (known and SCHEDULER_OWNER.get(name) == role) else skip).append(name)
    return {
        "role": role,
        "known": known,
        "variable": ENV_VAR,
        "start": start,
        "skip": skip,
        "owners": dict(SCHEDULER_OWNER),
        "unassigned": list(UNASSIGNED),
    }


def log_startup_plan() -> Dict[str, object]:
    """Say out loud what this process decided. Once, at startup."""
    plan = startup_plan()
    if not plan["known"]:
        logger.warning(
            "%s is not set to one of %s. This process will start NO background "
            "schedulers. That is the fail-closed default: every one of them can "
            "eventually message a customer, and a process that cannot say what "
            "it is has no business doing that. Set %s=backend on the API "
            "service, %s=voice on the voice service.",
            ENV_VAR, ", ".join(KNOWN_ROLES), ENV_VAR, ENV_VAR)
    else:
        logger.info("%s=%s — starting %d scheduler(s): %s; not starting: %s",
                    ENV_VAR, plan["role"], len(plan["start"]),
                    ", ".join(plan["start"]) or "none",
                    ", ".join(plan["skip"]) or "none")
    if plan["unassigned"]:
        logger.warning(
            "These scheduled tasks have no declared owner and will run nowhere: "
            "%s. Add them to SCHEDULER_OWNER.", ", ".join(plan["unassigned"]))
    return plan
