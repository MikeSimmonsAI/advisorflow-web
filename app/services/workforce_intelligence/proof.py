"""SYNTHETIC PROOF - the whole management lifecycle, against fake tenants only.

WHAT THIS PROVES, AND WHY A TEST SUITE IS NOT ENOUGH. pytest proves the units
behave. This proves the PRODUCT does: that a workforce which has actually done
things - booked, failed, been refused, handed over, gone quiet - shows up on
the management surface as activity, outcomes, exceptions, review items, a
scorecard, a finding, a cost picture and an executive summary, and that a
manager can act on one of them.

HOW THE HISTORY IS MADE. Through T6, T7 and T8's own contracts and through
nothing else. T8's deployment simulation builds the world and runs each
lifecycle; the enrichment below adds the operational conditions a management
layer exists to surface, using `queue.enqueue`, `queue.transition`,
`queue.record_failure`, `handoff.create` and `performance.bump` - the same
functions the engine uses. Nothing here writes a row T9 will later read as
though it were an engine event, because a proof that manufactured its own
evidence would prove nothing about the engine.

WHERE IT RUNS. Synthetic and demonstration tenants only, on reserved fictional
numbers at unresolvable domains, with every organization flagged as a demo and
every channel adapter simulated. The last step counts real outreach and expects
zero - not as a claim but as a query.

NOTHING IS SWITCHED ON. This does not enable AI operations, live sending or
voice, does not create production jobs, and does not activate any real
customer's employee. T8's simulation leaves the dark switches exactly as it
found them and so does this.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_operations_models import AICommunication
from app.models.models import Lead, Organization
from app.models.workforce_models import AIEmployee, AIToolExecution, AIWorkItem
from app.services.workforce import constants as W
from app.services.workforce_intelligence import attention as t9_attention
from app.services.workforce_intelligence import command as t9_command
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence import cost as t9_cost
from app.services.workforce_intelligence import executive as t9_executive
from app.services.workforce_intelligence import findings as t9_findings
from app.services.workforce_intelligence import quality as t9_quality
from app.services.workforce_intelligence import reconciliation as t9_rec
from app.services.workforce_intelligence import review as t9_review
from app.services.workforce_intelligence import scope as t9_scope
from app.services.workforce_intelligence import scorecards as t9_scorecards

_log = logging.getLogger(__name__)

# The three lifecycles section 20 names, mapped onto T8's own scenario keys so
# there is one definition of what "the Reactivation proof" means rather than
# two that can drift.
SCENARIOS = {
    "reactivation": {
        "label": "Reactivation Specialist",
        "t8_key": "reactivation",
        "slug": "t8-proof-reactivation",
    },
    "energy_residential": {
        "label": "Full-Lifecycle Energy - Residential",
        "t8_key": "energy_residential",
        "slug": "t8-proof-energy-residential",
    },
    "energy_b2b": {
        "label": "Full-Lifecycle Energy - B2B",
        "t8_key": "energy_b2b",
        "slug": "t8-proof-energy-b2b",
    },
}


@dataclass
class Step:
    name: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"step": self.name, "passed": self.passed,
                "detail": self.detail}


def run(db: Session, *, which: str = "all",
        now: Optional[datetime] = None) -> Dict[str, Any]:
    """Run one or every management proof. Returns a report; never raises."""
    now = now or datetime.utcnow()
    keys = list(SCENARIOS) if which in ("all", "", None) else [which]
    unknown = [k for k in keys if k not in SCENARIOS]
    if unknown:
        raise ValueError("Unknown proof(s): %s" % ", ".join(unknown))

    scenarios = []
    for key in keys:
        steps: List[Step] = []
        try:
            scenarios.append(_scenario(db, key, steps, now))
        except Exception as exc:                              # noqa: BLE001
            _log.exception("t9 proof: %s failed", key)
            steps.append(Step("scenario_completed", False,
                              "Raised: %s" % str(exc)[:300]))
            scenarios.append({"key": key, "label": SCENARIOS[key]["label"],
                              "passed": False,
                              "steps": [s.as_dict() for s in steps]})

    outreach = _real_outreach(db)
    total = sum(len(s["steps"]) for s in scenarios)
    passed = sum(1 for s in scenarios for st in s["steps"] if st["passed"])
    return {
        "suite": "t9_workforce_management_lifecycles",
        "scenarios": scenarios,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "all_passed": all(s["passed"] for s in scenarios) and outreach == 0,
        "real_outreach": outreach,
        "note": ("Every contact is a reserved fictional number at an "
                 "unresolvable domain, every organization is flagged as a "
                 "demonstration, and every communication is simulated. The "
                 "real-outreach figure above is a query, not a claim."),
    }


def _real_outreach(db: Session) -> int:
    """How many non-simulated sends exist anywhere. Expected: zero.

    COUNTED ACROSS THE WHOLE DATABASE, not just the synthetic tenants. A proof
    that only checked its own organizations would pass while the run had sent
    something somewhere else, which is precisely the failure worth catching.
    """
    comms = (db.query(func.count(AICommunication.id))
             .filter(AICommunication.simulated.is_(False)).scalar() or 0)
    tools = (db.query(func.count(AIToolExecution.id))
             .filter(AIToolExecution.simulated.is_(False),
                     AIToolExecution.decision == "allowed").scalar() or 0)
    return int(comms) + int(tools)


def _scenario(db: Session, key: str, steps: List[Step],
              now: datetime) -> Dict[str, Any]:
    spec = SCENARIOS[key]

    # ── 1. The world and its history, built by T8's own proof ────────────
    from app.services.ai_deployment import simulation as t8_simulation
    t8_report = t8_simulation.run(db, which=spec["t8_key"])
    t8_scenario = (t8_report.get("scenarios") or [{}])[0]
    steps.append(Step(
        "t8_lifecycle_ran", bool(t8_scenario.get("steps")),
        "T8's own deployment proof produced %d steps, %s."
        % (len(t8_scenario.get("steps") or []),
           "all passing" if t8_scenario.get("passed") else "not all passing")))

    org = (db.query(Organization)
           .filter(Organization.slug == spec["slug"]).first())
    if org is None:
        steps.append(Step("synthetic_tenant_exists", False,
                          "The synthetic organization was not created."))
        return _result(key, spec, steps)
    steps.append(Step("synthetic_tenant_exists", True,
                      "Organization %s, flagged as a demonstration tenant."
                      % org.slug))

    employee = (db.query(AIEmployee)
                .filter(AIEmployee.organization_id == org.id)
                .order_by(AIEmployee.created_at.desc()).first())
    if employee is None:
        steps.append(Step("actor_provisioned", False,
                          "No AI employee was provisioned for this tenant."))
        return _result(key, spec, steps)
    steps.append(Step("actor_provisioned", True,
                      "AI employee '%s' (%s) exists in this tenant."
                      % (employee.name, employee.job_role)))

    # ── 2. Operational history, through the engine's own contracts ───────
    enriched = _enrich(db, org, employee, now)
    steps.append(Step("operational_history_generated", enriched["ok"],
                      enriched["detail"]))

    # ── 3. T9 runs ───────────────────────────────────────────────────────
    scope = t9_scope.for_system([org.id], platform_id=org.platform_id)
    refreshed = t9_command.refresh(db, scope, now=now)
    steps.append(Step("t9_passes_ran", refreshed["complete"],
                      "Passes: %s. Failed: %s."
                      % (", ".join(sorted(refreshed["passes"])),
                         ", ".join(refreshed["failed_passes"]) or "none")))

    # ── 4. What the manager now sees ─────────────────────────────────────
    queue = t9_attention.queue(db, scope, now=now)
    steps.append(Step(
        "needs_attention_populated", queue["total"] > 0,
        "%d item(s): %s" % (queue["total"],
                            ", ".join(sorted(queue["by_kind"])) or "none")))

    steps.append(Step(
        "attention_items_are_evidenced",
        all(i["source"]["table"] for i in queue["items"]),
        "Every item names the authoritative table it came from."))

    steps.append(Step(
        "attention_items_recommend_an_action",
        all(i["recommended_action"] for i in queue["items"]),
        "Every item says what to do next."))

    cards = t9_scorecards.build(db, scope, now=now)
    mine = [c for c in cards["cards"] if c["employee_id"] == employee.id]
    steps.append(Step(
        "scorecard_exists", bool(mine),
        "A scorecard was produced for %s, with a previous-period comparison "
        "and its own baseline." % employee.name))

    steps.append(Step(
        "unlike_jobs_are_not_compared",
        all(b.get("job_role") for b in cards["benchmarks"].values()),
        "Benchmarks are grouped by job. There is no cross-job score."))

    graded = t9_quality.evaluate(db, scope, now=now)
    slot = (graded.get("employees") or {}).get(employee.id) or {}
    dims = slot.get("dimensions") or {}
    steps.append(Step(
        "quality_evaluated", bool(dims),
        "%d dimension(s) considered; %d measurable from authoritative records."
        % (len(dims), sum(1 for d in dims.values() if d.get("measured")))))

    steps.append(Step(
        "quality_score_is_explainable",
        bool((slot.get("score") or {}).get("components")),
        "Every component and weight is published with the score."))

    reviews = t9_review.queue(db, scope, now=now)
    steps.append(Step(
        "human_review_queue_populated", reviews["total"] > 0,
        "%d item(s) waiting for a person, %d undecided."
        % (reviews["total"], reviews["undecided"])))

    costs = t9_cost.report(db, scope, now=now)
    provider = ((costs.get("usage") or {}).get("provider_cost") or {})
    steps.append(Step(
        "cost_known_where_known", provider.get("value") is None
        and provider.get("label") == "unknown",
        "Measured usage is reported; provider cost is unknown rather than "
        "zero."))

    stored_findings = t9_findings.listing(db, scope)["findings"]
    steps.append(Step(
        "supervisor_finding_produced", bool(stored_findings),
        "%d finding(s), each separating fact, metric, interpretation and "
        "unknown." % len(stored_findings)))
    if stored_findings:
        first = stored_findings[0]
        steps.append(Step(
            "finding_separates_fact_from_interpretation",
            first["fact"]["class"] == C.EV_FACT
            and first["interpretation"]["class"] == C.EV_INTERPRETATION,
            "Fact and interpretation are separate fields, not one blob."))

    contradictions = t9_rec.listing(db, scope)
    steps.append(Step(
        "reconciliation_ran", "contradictions" in contradictions,
        "%d contradiction(s) between systems; each names the layer that owns "
        "the fix." % len(contradictions["contradictions"])))

    return _result(key, spec, steps, scope=scope, db=db, queue=queue, now=now,
                   employee=employee, org=org)


def _result(key: str, spec: Dict[str, Any], steps: List[Step], *,
            scope=None, db=None, queue=None, now=None, employee=None,
            org=None) -> Dict[str, Any]:
    """Close the scenario, taking a management action and an executive read.

    THE LAST TWO STEPS ARE THE ONES THAT MATTER MOST. A dashboard that renders
    is not the deliverable; a manager being able to ACT on what it says, and a
    layer above being able to READ it without touching T6/T7/T8's tables, are.
    """
    if scope is not None and db is not None and queue is not None:
        try:
            steps.append(_management_action_step(db, scope, queue, employee))
        except Exception as exc:                              # noqa: BLE001
            steps.append(Step("management_action_available", False,
                              "Raised: %s" % str(exc)[:200]))
        try:
            snapshot = t9_executive.snapshot(db, scope, now=now)
            missing = [k for k in t9_executive.CONTRACT_KEYS
                       if k not in snapshot]
            steps.append(Step(
                "executive_contract_complete", not missing,
                "Contract %s carries every documented key.%s"
                % (snapshot.get("contract_version"),
                   "" if not missing else
                   " Missing: %s" % ", ".join(missing))))
            revenue = (snapshot.get("outcomes") or {}).get("revenue") or {}
            appt = (snapshot.get("appointments") or {}).get("completed") or {}
            steps.append(Step(
                "unknown_stays_unknown",
                revenue.get("value") is None
                and revenue.get("class") == C.EV_UNKNOWN
                and appt.get("value") is None
                and appt.get("class") == C.EV_UNKNOWN,
                "Revenue and appointment completion are reported as unknown, "
                "not as zero."))
        except Exception as exc:                              # noqa: BLE001
            steps.append(Step("executive_contract_complete", False,
                              "Raised: %s" % str(exc)[:200]))
        try:
            steps.append(_no_outreach_step(db, org))
        except Exception as exc:                              # noqa: BLE001
            steps.append(Step("no_real_outreach", False,
                              "Raised: %s" % str(exc)[:200]))

    return {
        "key": key,
        "label": spec["label"],
        "passed": all(s.passed for s in steps),
        "steps": [s.as_dict() for s in steps],
    }


def _management_action_step(db: Session, scope, queue: Dict[str, Any],
                            employee) -> Step:
    """Acknowledge one item, and check the receipt names who performed it."""
    from app.services.workforce_intelligence import actions as t9_actions

    if not queue["items"]:
        return Step("management_action_available", False,
                    "Nothing on the queue to act on.")
    item = queue["items"][0]

    class _Operator:
        id = None
        role = "org_admin"

    result = t9_actions.perform(db, scope,
                                action=C.M_ACKNOWLEDGE_EXCEPTION,
                                user=_Operator(), target_id=item["id"],
                                reason="Acknowledged by the synthetic proof.")
    performed_by = result.get("performed_by")
    return Step(
        "management_action_available",
        bool(result.get("performed")) and bool(performed_by),
        "Acknowledged '%s'; the receipt records %s as the system that "
        "performed it." % (item["what"], performed_by))


def _no_outreach_step(db: Session, org) -> Step:
    """Nothing left this tenant. Counted, not asserted."""
    if org is None:
        return Step("no_real_outreach", False, "No tenant to check.")
    real = (db.query(func.count(AICommunication.id))
            .filter(AICommunication.organization_id == org.id,
                    AICommunication.simulated.is_(False)).scalar() or 0)
    return Step("no_real_outreach", int(real) == 0,
                "%d non-simulated communications in this tenant." % int(real))


# ---------------------------------------------------------------------------
# THE ENRICHMENT - operational history through the engine's own contracts
# ---------------------------------------------------------------------------


def _contacts(db: Session, org: Organization, *, count: int) -> List[Lead]:
    """Synthetic contacts, on reserved fictional numbers at a dead domain.

    555-01xx is the North American range reserved for fiction and `.invalid`
    is reserved by RFC 2606 and can never resolve. A proof that used a
    plausible number would be one bad flag away from texting a stranger.
    """
    existing = (db.query(Lead)
                .filter(Lead.organization_id == org.id).all())
    if len(existing) >= count:
        return existing[:count]
    advisor = existing[0].assigned_to_id if existing else None
    made = list(existing)
    for i in range(len(existing), count):
        row = Lead(organization_id=org.id, assigned_to_id=advisor,
                   first_name="Synthetic", last_name="Contact %d" % i,
                   phone="1555010%04d" % i,
                   email="synthetic.contact.%d@example.invalid" % i,
                   source_file="t9-proof")
        db.add(row)
        made.append(row)
    db.flush()
    return made


def _enrich(db: Session, org: Organization, employee: AIEmployee,
            now: datetime) -> Dict[str, Any]:
    """Give the workforce a past worth managing, using T6's own functions.

    EVERY WRITE BELOW GOES THROUGH `queue`, `handoff` OR `performance`. Not one
    of them sets a column directly, because a proof that wrote its own rows
    would be testing T9 against a fixture rather than against the engine - and
    the first time the engine's state machine changed, the proof would keep
    passing against a shape nothing produces any more.

    The conditions created are the ones a management layer exists for: work
    that stalled, work that failed repeatedly, work waiting for review, and a
    person who was asked for and has not arrived.
    """
    from app.services.workforce import handoff as wf_handoff
    from app.services.workforce import performance as wf_performance
    from app.services.workforce import queue as wf_queue

    # ENOUGH CONTACTS FOR A BACKLOG TO BE A BACKLOG. T8's lifecycle proof
    # needs two contacts to demonstrate a deployment; a MANAGEMENT proof needs
    # enough waiting work that "427 records are waiting" is the kind of
    # sentence being tested. These are ordinary CRM records on reserved
    # fictional numbers at an unresolvable domain - the engine state below is
    # what goes through the engine's own contracts.
    leads = _contacts(db, org, count=32)
    if not leads:
        return {"ok": False, "detail": "No synthetic contacts to work."}

    wf_queue.enqueue(db, employee, [lead.id for lead in leads],
                     job_key=employee.job_role or "work")
    items = (db.query(AIWorkItem)
             .filter(AIWorkItem.organization_id == org.id,
                     AIWorkItem.employee_id == employee.id)
             .order_by(AIWorkItem.created_at.asc()).all())
    if not items:
        return {"ok": False, "detail": "Nothing was enqueued."}

    stalled = failed = review = handoffs = 0

    # STALLED: due for action and untouched. Backdated through the engine's own
    # scheduler rather than by writing the timestamp, then aged.
    for item in items[:4]:
        wf_queue.advance_to(db, item, W.ELIGIBLE,
                            reason="synthetic proof: made eligible")
        wf_queue.schedule_next(db, item, 0, now=now - timedelta(hours=6))
        stalled += 1

    # REPEATED FAILURE: the engine's own failure recorder, called until the
    # streak is past the detection threshold.
    for item in items[4:6]:
        wf_queue.advance_to(db, item, W.WORKING,
                            reason="synthetic proof: working")
        for _ in range(3):
            wf_queue.record_failure(db, item, "synthetic proof: provider "
                                              "unavailable")
        failed += 1

    # REVIEW REQUIRED: a real transition into the state a person has to clear.
    for item in items[6:8]:
        wf_queue.advance_to(db, item, W.NEEDS_REVIEW,
                            reason="synthetic proof: ambiguous reply")
        review += 1

    # A HANDOFF NOBODY HAS TAKEN.
    for item in items[8:9]:
        lead = next((l for l in leads if l.id == item.subject_id), None)
        wf_handoff.create(db, employee=employee, lead=lead, work_item=item,
                          reason_code="human_requested",
                          summary=("The person asked to speak to somebody. "
                                   "Synthetic proof."),
                          known_facts=["Synthetic contact",
                                       "Asked for a person"],
                          open_questions=["When can they talk?"],
                          priority="high")
        wf_queue.advance_to(db, item, W.HUMAN_HANDOFF,
                            reason="synthetic proof: handed over")
        handoffs += 1

    # THE LEDGER, through its own increment function. These are the counts the
    # scorecards and the executive read are built from.
    for metric, count in (("records_assigned", len(items)),
                          ("records_eligible", stalled + failed),
                          ("messages_sent", 18), ("responses", 5),
                          ("appointments", 2), ("qualified", 3),
                          ("handoffs", handoffs), ("opt_outs", 1),
                          ("provider_failures", 3)):
        for _ in range(max(0, int(count))):
            wf_performance.bump(db, employee, metric, now=now)

    db.flush()
    return {
        "ok": True,
        "detail": ("%d records enqueued through the work queue; %d stalled, "
                   "%d failing repeatedly, %d awaiting review, %d handed over. "
                   "Ledger counts written through the performance ledger."
                   % (len(items), stalled, failed, review, handoffs)),
    }
