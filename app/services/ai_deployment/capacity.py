"""PACKAGE AND CAPACITY GUARDS - what buying an AI employee does NOT buy.

SECTION 9 IN ONE SENTENCE: AI employees are a commercial entitlement, not a way
around the package architecture. Buying one raises no ceiling, unlocks no
feature, and does not turn a smaller package into a larger one.

THREE SEPARATE QUESTIONS, AND THEY FAIL IN DIFFERENT DIRECTIONS.

  HOW MANY MAY THEY HOLD?     `max_per_customer` on the brand's term sheet.
                              Unstated means the brand has not capped it,
                              which is the safe default because every
                              individual one still needs entitlement,
                              readiness and an operator.

  WHICH PACKAGES MAY HOLD IT? `eligible_plan_keys`. Unstated means no package
                              restriction FOR AN ORDINARY JOB - and means
                              REFUSED for an advanced one. That asymmetry is
                              the point: silence about an ordinary employee is
                              a brand that did not need to restrict it, and
                              silence about a management or executive
                              capability is a brand that has not decided to
                              sell it down-market.

  DOES THE PLATFORM ENFORCE   `plan_limits` answers this and this module does
  WHAT WAS SOLD?              not touch it. No AI employee grants
                              `max_users` or `max_leads`, and `brand_catalog`
                              already refuses a catalogue item whose
                              entitlement key names a dimension nothing
                              enforces.

WHAT COUNTS AS ADVANCED, AND WHY IT IS A PROPERTY RATHER THAN A LIST OF NAMES.
A job is advanced when it supervises other work rather than doing it - the
platform template says so through its own tool list, because a job whose tools
contain no outward reach and whose role is oversight is by construction a
management capability. Deriving it means a twelfth template added tomorrow is
classified the day it lands rather than the day somebody remembers this file.
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_deployment_models import AIEmployeeDeployment
from app.models.models import Organization
from app.services.ai_deployment import commerce
from app.services.ai_deployment import constants as D

_log = logging.getLogger(__name__)


def is_advanced_capability(template_key: str) -> bool:
    """Is this a management / oversight job rather than a working one?

    Derived from the platform registry: a job that holds no outward-reaching
    tool and whose objective is to report rather than to act is supervision.
    `manager_supervisor` is the one such job today and it is identified by its
    shape, not by its key.
    """
    from app.services.workforce import registry as wf_registry
    spec = wf_registry.template(template_key)
    if spec is None:
        return False
    reaching = [t for t in spec.tool_keys
                if (wf_registry.tool(t) is not None
                    and wf_registry.tool(t).reaches_outside)]
    if reaching:
        return False
    # A job with no channels and no outward tools does not work records; it
    # watches whatever does.
    return not spec.channels


def held_count(db: Session, organization_id: str, template_key: str) -> int:
    """How many live deployments of this job the customer already holds.

    RETIRED ONES DO NOT COUNT, and suspended ones DO. A suspended deployment is
    a seat the customer still occupies - its configuration, its history and its
    actor are all still there - and letting them hire a second one while the
    first is suspended would be a way to hold two on one entitlement.
    """
    return (db.query(AIEmployeeDeployment)
            .filter(AIEmployeeDeployment.organization_id == organization_id,
                    AIEmployeeDeployment.template_key == template_key,
                    AIEmployeeDeployment.state != D.RETIRED)
            .count())


class CapacityAnswer:
    """Whether one more of this job may be hired, and why not if not."""

    __slots__ = ("allowed", "code", "reason", "held", "limit", "advanced",
                 "plan_key")

    def __init__(self, allowed, code=None, reason=None, held=0, limit=None,
                 advanced=False, plan_key=None):
        self.allowed = allowed
        self.code = code
        self.reason = reason
        self.held = held
        self.limit = limit
        self.advanced = advanced
        self.plan_key = plan_key

    def as_dict(self) -> Dict[str, Any]:
        return {"allowed": self.allowed, "code": self.code,
                "reason": self.reason, "held": self.held, "limit": self.limit,
                "advanced_capability": self.advanced, "plan_key": self.plan_key}


def may_hire(db: Session, org: Organization, template_key: str, *,
             terms=None) -> CapacityAnswer:
    """May this customer take one more of this job?

    Answers the PACKAGE and CAPACITY questions only. Entitlement is
    `commerce.resolve_offer`'s question, readiness is `readiness`'s, and
    stacking them here would produce one refusal message that hides the other
    two reasons.
    """
    plan_key = commerce.plan_key_for(db, org)
    if terms is None:
        terms = commerce.terms_for(db, getattr(org, "platform_id", None),
                                   template_key)
    advanced = is_advanced_capability(template_key)
    held = held_count(db, org.id, template_key)

    eligible: List[str] = commerce.json_list(
        getattr(terms, "eligible_plan_keys", None), []) or []

    # ADVANCED CAPABILITY IS TIER-GATED, AND SILENCE IS A REFUSAL.
    #
    # An add-on must not be a cheap route to a management capability the
    # package architecture puts higher up. A brand that genuinely wants to sell
    # oversight to a smaller package says so by naming that package here, which
    # is a decision with a name on it rather than a default.
    if advanced and not eligible:
        return CapacityAnswer(
            False, D.R_PACKAGE_INELIGIBLE,
            "This is a management capability and the brand has not said which "
            "packages may hold it. Buying an add-on does not move a customer "
            "to a higher tier.",
            held=held, advanced=advanced, plan_key=plan_key)

    if eligible and (plan_key or "") not in eligible:
        return CapacityAnswer(
            False, D.R_PACKAGE_INELIGIBLE,
            "This AI employee is available on a different package.",
            held=held, advanced=advanced, plan_key=plan_key)

    limit = getattr(terms, "max_per_customer", None)
    if limit is not None and held >= int(limit):
        return CapacityAnswer(
            False, D.R_CAPACITY_REACHED,
            "This account already has %d of these, which is the most this "
            "brand allows." % held,
            held=held, limit=int(limit), advanced=advanced, plan_key=plan_key)

    return CapacityAnswer(True, held=held,
                          limit=(int(limit) if limit is not None else None),
                          advanced=advanced, plan_key=plan_key)


def report(db: Session, org: Organization) -> Dict[str, Any]:
    """What this customer holds and what their package permits.

    Reported rather than enforced here; the enforcement is `may_hire`. Two
    shapes for the same facts because the screen needs a summary and the gate
    needs an answer, and deriving the summary from repeated gate calls would
    make the screen's numbers depend on the order it asked in.
    """
    from app.services.workforce import registry as wf_registry
    plan_key = commerce.plan_key_for(db, org)
    rows = (db.query(AIEmployeeDeployment)
            .filter(AIEmployeeDeployment.organization_id == org.id)
            .all())
    by_template: Dict[str, int] = {}
    for r in rows:
        if r.state == D.RETIRED:
            continue
        by_template[r.template_key] = by_template.get(r.template_key, 0) + 1

    jobs = []
    for key in wf_registry.ALL_TEMPLATE_KEYS:
        terms = commerce.terms_for(db, getattr(org, "platform_id", None), key)
        limit = getattr(terms, "max_per_customer", None)
        jobs.append({
            "template_key": key,
            "held": by_template.get(key, 0),
            "limit": int(limit) if limit is not None else None,
            "advanced_capability": is_advanced_capability(key),
        })
    return {
        "organization_id": org.id,
        "plan_key": plan_key,
        "total_deployments": sum(by_template.values()),
        "retired": sum(1 for r in rows if r.state == D.RETIRED),
        "jobs": jobs,
        # STATED, because the question "does buying AI employees raise my
        # limits" has one answer and it should be on the screen rather than in
        # a support ticket.
        "note": ("AI employees are an entitlement of their own. Hiring one "
                 "does not raise any package limit and does not enable any "
                 "feature the package does not already include."),
    }


def granted_dimensions() -> List[str]:
    """Which platform limits an AI employee raises. DELIBERATELY EMPTY.

    Exported as a function rather than left implicit so a test can assert it,
    and so the day somebody wants AI capacity to be a grantable dimension they
    have to teach `plan_limits` to enforce it first - which is exactly the rule
    `brand_catalog._entitlement_problems` already keeps for every other item.
    """
    return []
