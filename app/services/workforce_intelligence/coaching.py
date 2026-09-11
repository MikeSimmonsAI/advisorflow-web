"""COACHING - suggestions for a person, and nothing that happens by itself.

THE WHOLE OF SECTION 9 IN ONE PROPERTY: a recommendation produced here is a
STRING AND A LINK. There is no execute path, no "apply" function, no handler
that turns a recommendation into a change. The Supervisor may recommend; a
person decides; the owning system performs.

THAT SEPARATION IS WHY THIS MODULE IS SHORT. A coaching engine that could act
would need authority, an audit trail and its own gates - and would be the
second control plane the mission forbids. One that only writes sentences needs
none of those, and the sentences are more useful for it: "check this
employee's handoff destination" is advice a person can weigh, while an AI that
silently changed a handoff destination is a change nobody made.

THE CONSEQUENCE ALLOW-LIST IS ENFORCED, NOT DOCUMENTED. Every recommendation
declares a `consequence`, and `_check` refuses any value outside
`QUALITY_ALLOWED_CONSEQUENCES`. Grant a tool, change authority, invent
consent, enable a channel, enable voice, switch on live sending, alter
billing, change entitlement, bypass readiness, touch God authority - none of
these is in that tuple, so none can be produced here even by a mistake in a
generator. An assertion at import proves the tuple has not grown.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence import findings as t9_findings
from app.services.workforce_intelligence import quality as t9_quality
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)

# Consequences that would be CHANGES rather than suggestions. Named so a test
# can assert none of them ever appears, and so the intent is legible.
FORBIDDEN_CONSEQUENCES = (
    "grant_tool", "change_authority", "record_consent", "enable_channel",
    "enable_voice", "enable_live_send", "change_billing",
    "change_entitlement", "skip_readiness", "change_god_authority",
    "activate_employee",
)

assert not (set(FORBIDDEN_CONSEQUENCES)
            & set(C.QUALITY_ALLOWED_CONSEQUENCES)), (
    "A consequence T9 must never produce has been added to the allow-list.")


def _check(consequence: str) -> str:
    if consequence not in C.QUALITY_ALLOWED_CONSEQUENCES:
        raise ValueError(
            "T9 may not recommend '%s'. Recommendations are limited to: %s"
            % (consequence, ", ".join(C.QUALITY_ALLOWED_CONSEQUENCES)))
    return consequence


def _rec(*, code: str, headline: str, why: str, consequence: str,
         employee_id: Optional[str] = None, action: str = "",
         evidence: Optional[Dict] = None,
         drilldown: Optional[Dict] = None) -> Dict[str, Any]:
    return {
        "code": code,
        "headline": headline,
        "why": why,
        "what_to_do": action,
        "consequence": _check(consequence),
        "employee_id": employee_id,
        "evidence": evidence or {},
        "drilldown": drilldown or {},
        "class": C.EV_RECOMMENDATION,
        # SAID ON EVERY ONE, because a suggestion that looks like an action is
        # a suggestion somebody assumes was taken.
        "applied_automatically": False,
        "note": ("A suggestion for a person. AdvisorFlow does not change an "
                 "employee, a channel, an entitlement or an activation stage "
                 "on the strength of this."),
    }


def recommendations(db: Session, scope: Scope, *,
                    window_key: str = C.DEFAULT_WINDOW,
                    now: Optional[datetime] = None,
                    thresholds: Optional[Dict] = None) -> Dict[str, Any]:
    """What a manager might do next, drawn from findings and quality.

    NOTHING NEW IS DETECTED HERE. Every recommendation is a reading of a
    finding or a quality exception that already exists with its own evidence,
    so a recommendation can always be traced back to rows. A coaching engine
    with its own detectors would be a third opinion about the same workforce.
    """
    now = now or datetime.utcnow()
    out: List[Dict[str, Any]] = []

    stored = t9_findings.listing(db, scope, limit=200)["findings"]
    for f in stored:
        mapped = _FROM_FINDING.get(f["code"])
        if mapped is None:
            continue
        consequence, action = mapped
        out.append(_rec(
            code="coach.%s" % f["code"],
            headline=f["headline"],
            why=(f.get("interpretation") or {}).get("text") or "",
            consequence=consequence,
            employee_id=f.get("employee_id"),
            action=(f.get("recommendation") or {}).get("text") or action,
            evidence={"finding_id": f["id"],
                      "facts": (f.get("fact") or {}).get("items", []),
                      "metrics": (f.get("metric") or {}).get("items", [])},
            drilldown=f.get("drilldown") or {}))

    graded = t9_quality.evaluate(db, scope, window_key=window_key, now=now)
    for emp_id, slot in (graded.get("employees") or {}).items():
        for key, dim in (slot.get("dimensions") or {}).items():
            if not dim.get("measured") or not dim.get("violations"):
                continue
            critical = key in C.QUALITY_CRITICAL
            out.append(_rec(
                code="coach.quality.%s" % key,
                headline=("%s: %s" % (slot.get("name") or emp_id,
                                      dim.get("label") or key)),
                why=("%d exception%s were found in this dimension."
                     % (len(dim["violations"]),
                        "" if len(dim["violations"]) == 1 else "s")),
                consequence=("recommend_pause" if critical
                             else "recommend_review"),
                employee_id=emp_id,
                action=("Look at these before this employee sends anything "
                        "else." if critical else
                        "Review these when you next have time."),
                evidence={"dimension": key,
                          "calculation": dim.get("calculation"),
                          "examples": dim["violations"][:5],
                          "total": len(dim["violations"])},
                drilldown={"view": "quality", "employee_id": emp_id,
                           "dimension": key}))

    return {
        "generated_at": now.isoformat(),
        "recommendations": out,
        "total": len(out),
        "allowed_consequences": list(C.QUALITY_ALLOWED_CONSEQUENCES),
        "policy": (
            "AdvisorFlow's Supervisor recommends and never acts. Anything "
            "consequential - authority, channels, voice, live sending, "
            "billing, entitlement, readiness or activation - is changed only "
            "through the system that owns it, by a person, with that system's "
            "own checks and audit."),
    }


# Finding code -> (consequence, default advice). A finding with no entry
# produces no recommendation, which is the right default: not everything worth
# saying is something to do.
_FROM_FINDING = {
    C.F_ELIGIBLE_BACKLOG: ("recommend_configuration_change",
                           "Check this employee's audience and start it."),
    C.F_REVIEW_BACKLOG: ("recommend_review",
                         "Clear the review queue."),
    C.F_CONVERSION_DECLINE: ("recommend_investigation",
                             "Compare the two periods before changing "
                             "anything."),
    C.F_RESPONSE_DECLINE: ("recommend_investigation",
                           "Check whether the audience or channel changed."),
    C.F_PROVIDER_FAILURES_UP: ("recommend_investigation",
                               "Check the provider configuration."),
    C.F_DENIAL_CONCENTRATION: ("recommend_configuration_change",
                               "Check channels, feature flags and "
                               "entitlement."),
    C.F_VOICE_WITHOUT_YIELD: ("recommend_investigation",
                              "Compare messaging results against calls."),
    C.F_NO_OUTCOMES: ("recommend_review",
                      "Read one conversation end to end."),
    C.F_IDLE_EMPLOYEE: ("recommend_configuration_change",
                        "Give it an audience, or pause it."),
    C.F_HANDOFF_LATENCY: ("recommend_configuration_change",
                          "Set a handoff destination."),
    C.F_SAME_STAGE_FAILURE: ("recommend_investigation",
                             "Look at the stage that keeps failing."),
    C.F_COST_PER_OUTCOME: ("recommend_investigation",
                           "Compare effort against outcomes."),
    C.F_BLOCKED_LONGER: ("recommend_review",
                         "Decide what happens to the blocked work."),
    C.F_OPT_OUT_RISE: ("recommend_review",
                       "Read the messages that preceded the opt-outs."),
}
