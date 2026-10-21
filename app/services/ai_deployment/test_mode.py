"""SAFE TEST MODE - what a customer's "Run test" and "Start working" buttons do.

WHY THIS EXISTS. Everything below it already worked in pieces: T8 could offer,
hire and configure an AI employee; T6 could run one; the T6 queue worker could
claim its work. What a customer could NOT do was the obvious thing - press a
button, watch their AI worker do its job on a pretend lead, and then leave it
running safely. Live operation (CONTROLLED / ACTIVE) is, correctly, something
only a platform operator switches on, and it can reach real people. There was
no customer-reachable mode in between. This module is that mode.

WHAT "TEST MODE" IS, EXACTLY. The AI employee's own stage is SIMULATION:

    - the engine, the eligibility gate, the tool gateway, the queue and the
      worker are all the REAL ones - nothing here is a stand-in;
    - every outward action resolves to a SIMULATED adapter (SIMULATION is not
      in EXECUTING_STAGES, so `outbound._assert_may_execute` refuses a real
      send no matter what), so nobody is ever contacted;
    - it only ever works PRACTICE records (workforce/practice.py) in a real
      customer's account. A real lead in a real account is refused by the
      eligibility gate while the stage is SIMULATION - test mode is not a quiet
      way to run AI over somebody's customer list.

WHO MAY TURN IT ON. A customer administrator, for their own account, once:
the job is offered and on their plan, setup has no blocking gaps, test mode is
allowed for their brand and account (the T6 scope chain), and no kill switch
is engaged. "Allowed for their account" is the platform's decision and stays
with the platform; this module never raises a scope.

WHAT IT NEVER DOES. Change a deployment into CONTROLLED/ACTIVE, raise any
activation scope, touch a real lead, set AI_OPERATIONS_LIVE_SEND, or schedule
anything. Starting test mode in an environment where the background worker is
not running is refused with that reason rather than reported as working.

LANGUAGE. Every string a customer can see is written for an eighth-grade
reader: what happened, why, and what to do next. No internal names.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_deployment_models import AIEmployeeDeployment
from app.models.models import Organization, User
from app.services.ai_deployment import commerce
from app.services.ai_deployment import constants as D
from app.services.ai_deployment import readiness

_log = logging.getLogger(__name__)

WORKER_JOB_NAME = "ai_workforce_worker"
# The worker is scheduled every 5 minutes; three missed passes means it is not
# running, rather than merely between passes.
WORKER_FRESH_MINUTES = 15

# Customer-facing status words. One of these, always.
S_NOT_SET_UP = "not_set_up"
S_READY_TO_TEST = "ready_to_test"
S_TESTING = "test_mode_on"
S_PAUSED = "paused"
S_STOPPED = "stopped"
S_LIVE = "live"
S_RETIRED = "retired"

STATUS_WORDS = {
    S_NOT_SET_UP: "Needs setup",
    S_READY_TO_TEST: "Ready to test",
    S_TESTING: "Working in test mode",
    S_PAUSED: "Paused",
    S_STOPPED: "Stopped",
    S_LIVE: "Working with real customers",
    S_RETIRED: "Retired",
}

LIVE_OFF_NOTE = ("Live messages are turned off. Test mode is ready. Your AI "
                 "worker practices on pretend leads and never texts, emails "
                 "or calls a real person.")

# Readiness checks that only matter for LIVE contact. In test mode nothing is
# sent, so a missing phone number or calendar link is not a reason to refuse a
# practice run - it is shown to the customer as "needed later for live".
_LIVE_ONLY_CHECKS = frozenset({
    "sms_sender", "email_sender", "calendar_connected", "voice_permitted",
    "operations_layer", "scopes_permit_running", "deployment_live",
    "inbound_enabled", "kill_switch_clear", "outreach_shape_stated",
})

# Checks the CUSTOMER can fix in setup, and which setup step fixes them.
_CUSTOMER_FIXABLE = {
    "configuration_complete": "business",
    "channel_chosen": "business",
    "handoff_destination": "handoff",
    "handoff_no_loop": "handoff",
    "handoff_owner_active": "handoff",
    "booking_owner": "handoff",
}

_PLAIN = {
    "configuration_complete": (
        "Some setup questions are not answered yet.",
        "Your AI worker needs these answers to know how to do its job."),
    "channel_chosen": (
        "You have not picked how your AI worker may reach people.",
        "It needs to know if it may text, email, or both."),
    "handoff_destination": (
        "Nobody is picked to take over when a person is needed.",
        "Your AI worker must always have a real person to pass leads to."),
    "handoff_no_loop": (
        "The person picked to take over would send work back to the AI.",
        "That would make leads go around in a circle."),
    "handoff_owner_active": (
        "The person picked to take over can't sign in right now.",
        "Leads passed to them would not be seen."),
    "booking_owner": (
        "Nobody is picked to own new appointments.",
        "Appointments need a real person's calendar."),
    "features_enabled": (
        "A feature this AI worker needs is turned off for your account.",
        "Your plan or settings do not include it yet."),
    "entitlement": (
        "This AI worker is not part of your plan yet.",
        "It has to be added to your account first."),
    "brand_offers_it": (
        "This AI worker is not offered on your account.",
        "Your provider has not made it available."),
    "package_eligible": (
        "Your plan does not include this AI worker.",
        "It needs a different plan or an add-on."),
}


# ── small readers ────────────────────────────────────────────────────────────

def _employee(db: Session, dep: AIEmployeeDeployment):
    from app.models.workforce_models import AIEmployee
    if not dep.employee_id:
        return None
    return (db.query(AIEmployee)
            .filter(AIEmployee.id == dep.employee_id,
                    AIEmployee.organization_id == dep.organization_id)
            .first())


def _blocker(code: str, what: str, why: str, next_step: str, *,
             who: str = "customer", fix: Optional[str] = None) -> Dict:
    """One reason it can't work, in the three-part shape every screen shows."""
    return {"code": code, "what": what, "why": why, "next": next_step,
            "who": who, "fix": fix}


def scope_allows_test_mode(db: Session, dep: AIEmployeeDeployment) -> Dict:
    """Do the platform, brand and account scopes allow SIMULATION?

    Reads the T6 chain WITHOUT the employee scope - the employee's own stage is
    the thing test mode is about to set, so asking it would be circular.
    """
    from app.services.workforce import activation as wf_activation
    from app.services.workforce import constants as WC
    r = wf_activation.resolve(db, organization_id=dep.organization_id,
                              platform_id=dep.platform_id)
    allowed = (not r.killed) and (WC.ACTIVATION_RANK.get(r.state, 0)
                                  >= WC.ACTIVATION_RANK[WC.SIMULATION])
    return {"allowed": allowed, "state": r.state, "killed": r.killed,
            "killed_by": r.killed_by_scope, "chain": r.chain}


def worker_health(db: Session, *, now: Optional[datetime] = None) -> Dict:
    """Is the background AI worker actually running? From its own ledger rows.

    A worker that has never recorded a pass is reported as not running, not as
    healthy-but-idle. Only a SUCCESSFUL pass inside the freshness window counts.
    """
    from app.models.job_models import JobRun
    now = now or datetime.utcnow()
    last = (db.query(JobRun).filter(JobRun.job_name == WORKER_JOB_NAME)
            .order_by(JobRun.started_at.desc()).first())
    last_ok = (db.query(JobRun)
               .filter(JobRun.job_name == WORKER_JOB_NAME,
                       JobRun.status == "success")
               .order_by(JobRun.started_at.desc()).first())

    def _naive(dt):
        return dt.replace(tzinfo=None) if dt is not None and dt.tzinfo else dt

    ok_at = _naive(last_ok.started_at) if last_ok else None
    running = bool(ok_at and now - ok_at <= timedelta(
        minutes=WORKER_FRESH_MINUTES))
    return {
        "running": running,
        "last_run_at": _naive(last.started_at).isoformat() + "Z"
        if last else None,
        "last_status": last.status if last else None,
        "last_success_at": ok_at.isoformat() + "Z" if ok_at else None,
        "last_metrics": (last.metrics if last else None),
        "fresh_minutes": WORKER_FRESH_MINUTES,
    }


def status(db: Session, dep: AIEmployeeDeployment) -> str:
    from app.services.workforce import constants as WC
    if dep.state == D.RETIRED:
        return S_RETIRED
    if dep.state in D.LIVE_STATES:
        return S_LIVE
    emp = _employee(db, dep)
    if emp is None or dep.state in (D.SELECTED, D.CONFIGURING):
        return S_NOT_SET_UP
    if emp.status == "disabled":
        return S_STOPPED
    if emp.activation_state == WC.SIMULATION:
        return S_PAUSED if emp.paused_at is not None else S_TESTING
    if _config_flag(dep, "test_mode_stopped"):
        return S_STOPPED
    return S_READY_TO_TEST


def _config_flag(dep, key) -> Any:
    try:
        return (json.loads(dep.config or "{}") or {}).get("_test_mode", {}).get(key)
    except (TypeError, ValueError):
        return None


def _set_config_flag(dep, key, value) -> None:
    try:
        cfg = json.loads(dep.config or "{}") or {}
    except (TypeError, ValueError):
        cfg = {}
    tm = cfg.get("_test_mode") or {}
    tm[key] = value
    cfg["_test_mode"] = tm
    dep.config = json.dumps(cfg)


# ── WHY ISN'T IT WORKING ─────────────────────────────────────────────────────

def diagnose(db: Session, dep: AIEmployeeDeployment, *,
             action: str = "start",
             now: Optional[datetime] = None) -> Dict[str, Any]:
    """Every reason this AI worker can't test or start, from real state.

    `action` is "test" (the Run test button) or "start" (Start working). Start
    additionally needs the background worker to be running, because test mode
    left on with no worker would be a screen that says "working" while nothing
    happens.
    """
    blockers: List[Dict] = []
    later: List[Dict] = []

    if dep.state == D.RETIRED:
        blockers.append(_blocker(
            "retired", "This AI worker was retired.",
            "Retired AI workers keep their history but can't work again.",
            "Set up a new one from the Set Up tab."))
        return _diag_out(db, dep, blockers, later, action, now)

    if dep.state == D.SUSPENDED:
        blockers.append(_blocker(
            "suspended", "This AI worker is on hold because of your account.",
            "Its plan or add-on is not active right now.",
            "Ask your administrator to check your plan.", who="admin"))

    org = (db.query(Organization)
           .filter(Organization.id == dep.organization_id).first())
    offer = commerce.resolve_offer(db, org, dep.template_key) \
        if org is not None else None
    if offer is None or not offer.is_live:
        blockers.append(_blocker(
            "not_on_plan", "This AI worker is not part of your plan yet.",
            "It has to be added to your account before it can work.",
            "Ask your administrator to add it.", who="admin"))

    emp = _employee(db, dep)
    if emp is None:
        blockers.append(_blocker(
            "not_set_up", "Setup is not finished.",
            "Your AI worker is created when you finish the setup questions.",
            "Finish the questions in Set Up.", fix="business"))

    result = readiness.evaluate(db, dep)
    for check in result.checks:
        if check.passed or check.severity == D.SEVERITY_INFO:
            continue
        if check.key in _LIVE_ONLY_CHECKS:
            if check.severity == D.SEVERITY_BLOCKING:
                later.append({"code": check.key, "what": check.label,
                              "detail": check.detail})
            continue
        if check.severity != D.SEVERITY_BLOCKING:
            continue
        what, why = _PLAIN.get(check.key, (
            "Something your AI worker needs is missing.",
            check.detail or check.label))
        step = _CUSTOMER_FIXABLE.get(check.key)
        if step:
            blockers.append(_blocker(check.key, what, why,
                                     "Open Set Up and fix this answer.",
                                     fix=step))
        else:
            blockers.append(_blocker(check.key, what, why,
                                     "Ask your administrator.", who="admin"))

    scopes = scope_allows_test_mode(db, dep)
    if scopes["killed"]:
        blockers.append(_blocker(
            "safety_stop", "All AI work is stopped for safety right now.",
            "Someone pressed the emergency stop.",
            "Ask your administrator when it will be turned back on.",
            who="admin"))
    elif not scopes["allowed"]:
        blockers.append(_blocker(
            "test_mode_not_allowed",
            "Test mode is not turned on for your account yet.",
            "Your provider has to allow AI workers to practice on your "
            "account first.",
            "Ask your administrator to turn on test mode.", who="admin"))

    if emp is not None and emp.paused_at is not None and action == "test":
        blockers.append(_blocker(
            "paused", "Your AI worker is paused.",
            "A paused AI worker does no work, not even tests.",
            "Press Resume, then run the test again."))

    if action == "start":
        health = worker_health(db, now=now)
        if not health["running"]:
            blockers.append(_blocker(
                "worker_not_running",
                "The system that runs AI workers is not running.",
                ("It has not checked in during the last %d minutes, so work "
                 "would sit and wait." % WORKER_FRESH_MINUTES),
                "Ask your administrator to start the AI worker service.",
                who="admin"))

    return _diag_out(db, dep, blockers, later, action, now)


def _diag_out(db, dep, blockers, later, action, now) -> Dict[str, Any]:
    st = status(db, dep)
    return {
        "action": action,
        "ok": not blockers,
        "status": st,
        "status_label": STATUS_WORDS.get(st, st),
        "blockers": blockers,
        "customer_can_fix": [b for b in blockers if b["who"] == "customer"],
        "needs_administrator": any(b["who"] == "admin" for b in blockers),
        "needed_later_for_live": later,
        "live_messages": False,
        "live_messages_note": LIVE_OFF_NOTE,
    }


# ── RUN TEST ─────────────────────────────────────────────────────────────────

_PRACTICE_REPLIES = {
    "lead_qualifier": ("Yes, I'm interested. I'd like a quote this week and "
                       "I'm the one who decides."),
    "appointment_setter": ("Yes, I'd like to set up a time to talk. Weekday "
                           "mornings work best for me."),
}
_DEFAULT_REPLY = "Yes, I'm interested. Please tell me more."

_TOOL_WORDS = {
    "appointment.book": "Booked a practice appointment",
    "appointment.reschedule": "Moved a practice appointment",
    "calendar.get_availability": "Looked up open appointment times",
    "conversation.get_history": "Read the conversation so far",
    "conversation.place_call": "Placed a practice call",
    "conversation.prepare_email": "Wrote an email",
    "conversation.prepare_sms": "Wrote a text",
    "conversation.send_email": "Sent a practice email",
    "conversation.send_sms": "Sent a practice text",
    "customer.get_context": "Looked up the customer",
    "employee.mark_exhausted": "Stopped after the allowed number of tries",
    "employee.mark_qualified": "Marked the lead as ready",
    "employee.pause_work_item": "Paused this lead",
    "employee.request_review": "Asked a person to look",
    "employee.wait_for_response": "Waited for an answer",
    "handoff.create": "Passed the lead to a person",
    "knowledge.search": "Checked your business information",
    "lead.add_note": "Added a note",
    "lead.get": "Looked up the lead",
    "lead.get_context": "Looked up the lead",
    "lead.mark_bad_contact": "Marked the contact details as not working",
    "lead.mark_do_not_contact": "Marked the lead as do not contact",
    "lead.mark_not_interested": "Marked the lead as not interested",
    "lead.update_qualification": "Updated what it learned about the lead",
    "memory.remember": "Remembered a detail",
    "opportunity.create": "Opened a sales opportunity",
    "opportunity.get": "Looked up the sales opportunity",
    "opportunity.update": "Updated the sales opportunity",
}

_STATE_WORDS = {
    "assigned": "Got the lead",
    "eligibility_pending": "Checked if the lead may be contacted",
    "eligible": "Lead is OK to contact",
    "working": "Started working the lead",
    "waiting_for_response": "Waiting for the lead to answer",
    "qualified": "Lead is ready - qualified",
    "appointment_booked": "Appointment booked",
    "human_handoff": "Passed to a person",
    "not_interested": "Lead said no",
    "do_not_contact": "Lead asked not to be contacted",
    "bad_contact": "No good way to reach the lead",
    "needs_review": "Needs a person to look",
    "exhausted": "Ran out of tries",
    "failed": "Something went wrong",
    "paused": "Paused",
}


def _good_endings(job_role: Optional[str] = None):
    """Which endings mean THIS job did its job.

    Job-specific on purpose. An Appointment Setter that hands the lead to a
    person because the calendar had no open times did NOT pass - it is the
    customer's calendar that needs fixing, and the test has to say so.
    """
    from app.services.workforce import constants as WC
    by_job = {
        "lead_qualifier": frozenset({WC.QUALIFIED, WC.HUMAN_HANDOFF}),
        "appointment_setter": frozenset({WC.APPOINTMENT_BOOKED}),
    }
    return by_job.get(job_role or "", frozenset({
        WC.QUALIFIED, WC.APPOINTMENT_BOOKED, WC.HUMAN_HANDOFF}))


def _within_hours_moment(db: Session, emp, now: datetime) -> datetime:
    """`now`, or the next moment inside the worker's own working hours.

    A test pressed at 9 pm should still test the job, not report that 9 pm is
    outside working hours. The report says which time the test pretended it
    was, so nothing is hidden.
    """
    from app.services.workforce import policy as wf_policy
    pol = wf_policy.resolve(db, emp)
    probe = now
    for _ in range(4 * 24 * 8):            # 15-minute steps, up to 8 days
        ok, _why = wf_policy.within_operating_hours(pol, probe)
        if ok:
            return probe
        probe = probe + timedelta(minutes=15)
    return now


def _load_worker():
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "jobs", "run_ai_workforce_worker.py")
    spec = importlib.util.spec_from_file_location("run_ai_workforce_worker",
                                                  path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_test(db: Session, dep: AIEmployeeDeployment, *, actor: Optional[User],
             now: Optional[datetime] = None) -> Dict[str, Any]:
    """Create one pretend lead, let the REAL worker process it, report truly.

    The caller commits. Nothing here can reach a real person: the record is a
    practice record, the stage is SIMULATION and the adapters are simulated.
    """
    from app.models.models import Reply
    from app.models.workforce_models import (AIEmployeeRun, AIHandoff,
                                             AIToolExecution, AIWorkItem,
                                             AIWorkItemEvent)
    from app.services.workforce import constants as WC
    from app.services.workforce import outbound as wf_outbound
    from app.services.workforce import practice as wf_practice
    from app.services.workforce import queue as wf_queue
    from app.services.workforce import service as wf_service

    diag = diagnose(db, dep, action="test", now=now)
    if not diag["ok"]:
        return {"verdict": "NEEDS_ATTENTION",
                "headline": "NEEDS ATTENTION - Here is what to fix.",
                "ran": False, "problems": diag["blockers"],
                "diagnosis": diag}

    emp = _employee(db, dep)
    real_now = now or datetime.utcnow()
    test_now = _within_hours_moment(db, emp, real_now)
    stage_before = emp.activation_state
    if stage_before != WC.SIMULATION:
        wf_service.activate(db, emp, WC.SIMULATION, actor=actor,
                            reason="Run test (practice record only)")

    worker = _load_worker()
    lead = wf_practice.create_practice_lead(
        db, organization_id=dep.organization_id,
        assigned_to_id=emp.handoff_user_id)
    wf_queue.enqueue(db, emp, [lead.id], subject_type="lead",
                     job_key=emp.job_role, actor_kind=WC.ACTOR_HUMAN,
                     actor_id=getattr(actor, "id", None))
    item = (db.query(AIWorkItem)
            .filter(AIWorkItem.employee_id == emp.id,
                    AIWorkItem.subject_id == lead.id).one())
    # Due at the moment the test pretends it is (see _within_hours_moment).
    item.next_action_at = test_now
    db.flush()

    passes = []
    replied = False
    try:
        with wf_outbound.use_simulated_adapters():
            passes.append(worker.run_pass(db, employee_id=emp.id,
                                          organization_id=emp.organization_id,
                                          now=test_now))
            db.flush()
            if item.state == WC.WAITING_FOR_RESPONSE:
                # The pretend lead answers. A Reply row, exactly as an inbound
                # text would leave, and the item is woken the way a reply
                # wakes it - then the SAME worker runs again.
                body = _PRACTICE_REPLIES.get(emp.job_role, _DEFAULT_REPLY)
                db.add(Reply(lead_id=lead.id, body=body, source="sms",
                             received_at=test_now + timedelta(minutes=1)))
                item.last_inbound_at = test_now + timedelta(minutes=1)
                item.next_action_at = test_now + timedelta(minutes=1)
                db.flush()
                replied = True
                passes.append(worker.run_pass(
                    db, employee_id=emp.id,
                    organization_id=emp.organization_id,
                    now=test_now + timedelta(minutes=2)))
                db.flush()
    finally:
        if stage_before != WC.SIMULATION:
            wf_service.activate(db, emp, stage_before, actor=actor,
                                reason="Run test finished; back to %s"
                                       % stage_before)

    runs = (db.query(AIEmployeeRun)
            .filter(AIEmployeeRun.work_item_id == item.id)
            .order_by(AIEmployeeRun.started_at.asc()).all())
    tools = (db.query(AIToolExecution)
             .filter(AIToolExecution.work_item_id == item.id)
             .order_by(AIToolExecution.created_at.asc(),
                       AIToolExecution.sequence.asc()).all())
    events = (db.query(AIWorkItemEvent)
              .filter(AIWorkItemEvent.work_item_id == item.id)
              .order_by(AIWorkItemEvent.created_at.asc()).all())
    handoffs = (db.query(AIHandoff)
                .filter(AIHandoff.work_item_id == item.id).all())

    tried = [{"action": _TOOL_WORDS.get(t.tool_key, t.tool_key),
              "tool": t.tool_key,
              "allowed": t.decision == "allowed",
              "result": t.status,
              "practice_only": bool(t.simulated) or t.decision != "allowed",
              "refused_because": (t.denial_reason or t.denial_code)
              if t.decision != "allowed" else None,
              "run_id": t.run_id}
             for t in tools]
    steps = [{"step": ("Read the lead's answer and kept going"
                       if (e.reason or "") == "picked up again"
                       else _STATE_WORDS.get(e.to_state, e.to_state)),
              "state": e.to_state, "reason": e.reason,
              "at": e.created_at.isoformat() + "Z" if e.created_at else None}
             for e in events]
    claimed = sum(p.get("claimed", 0) for p in passes)
    errors = sum(p.get("errors", 0) for p in passes)
    ended = item.state
    good = (ended in _good_endings(emp.job_role) and claimed >= 1
            and errors == 0 and runs
            and all(r.status == "completed" for r in runs))

    if good:
        verdict, headline = "PASS", "PASS - Your AI worker completed the test."
        problems = []
    else:
        verdict = "NEEDS_ATTENTION"
        headline = "NEEDS ATTENTION - Here is what to fix."
        problems = [_explain_ending(item, tools, runs, claimed, errors,
                                    handoffs=handoffs)]

    report = {
        "verdict": verdict,
        "headline": headline,
        "ran": True,
        "practice_lead": {"id": lead.id,
                          "name": "%s %s" % (lead.first_name, lead.last_name),
                          "note": "A pretend person. Nobody real was "
                                  "contacted."},
        "work_item_id": item.id,
        "ended_as": _STATE_WORDS.get(ended, ended),
        "ended_state": ended,
        "pretend_reply_sent": replied,
        "pretend_reply": (_PRACTICE_REPLIES.get(emp.job_role, _DEFAULT_REPLY)
                          if replied else None),
        "steps": steps,
        "what_it_tried": tried,
        "refused": [t for t in tried if not t["allowed"]],
        "handoffs": [{"id": h.id, "summary": h.summary,
                      "to_user_id": h.assigned_to_user_id,
                      "status": h.status} for h in handoffs],
        "run_ids": [r.id for r in runs],
        "practice_appointment": _practice_appointment(tools),
        "worker_passes": passes,
        "tested_as_if": test_now.isoformat() + "Z",
        "tested_outside_hours": test_now != real_now,
        "problems": problems,
        "live_messages_note": LIVE_OFF_NOTE,
    }
    _set_config_flag(dep, "last_test", {
        "at": real_now.isoformat() + "Z", "verdict": verdict,
        "work_item_id": item.id, "run_ids": report["run_ids"]})
    commerce.record_event(
        db, dep, from_state=dep.state, to_state=dep.state,
        reason="Test run: %s" % verdict, actor_kind=D.ACTOR_HUMAN,
        actor_id=getattr(actor, "id", None),
        detail={"work_item_id": item.id, "run_ids": report["run_ids"],
                "ended_state": ended})
    db.flush()
    return report


def _practice_appointment(tools) -> Optional[Dict[str, Any]]:
    for t in tools:
        if t.tool_key == "appointment.book" and t.decision == "allowed":
            try:
                data = json.loads(t.result_summary or "{}")
            except (TypeError, ValueError):
                data = {}
            return {"starts_at": data.get("starts_at"),
                    "starts_at_local": data.get("starts_at_local"),
                    "timezone": data.get("timezone"),
                    "with": data.get("advisor_name"),
                    "practice": bool(data.get("practice")),
                    "on_a_real_calendar": bool(data.get("calendar_synced"))}
    return None


def _explain_ending(item, tools, runs, claimed, errors, *,
                    handoffs=()) -> Dict:
    from app.services.workforce import constants as WC
    if item.state == WC.HUMAN_HANDOFF and any(
            (h.reason_code or "") == "appointment_requires_human"
            for h in handoffs):
        return _blocker(
            "no_open_times", "Your AI worker could not find an open time.",
            "The calendar it books into has no open times in the next week.",
            "Open Availability and set the days and hours people can book. "
            "Then run the test again.", fix="availability")
    if claimed == 0:
        return _blocker(
            "not_picked_up", "Your AI worker did not pick up the test lead.",
            "It may be paused, stopped, or not allowed to work right now.",
            "Check the Needs You tab, then run the test again.")
    if errors or any(r.status != "completed" for r in runs):
        return _blocker(
            "run_failed", "Your AI worker hit an error during the test.",
            "Something inside the system went wrong. Nothing was sent.",
            "Run the test again. If it happens again, ask your "
            "administrator.", who="admin")
    if item.state == WC.WAITING_FOR_RESPONSE:
        return _blocker(
            "still_waiting", "Your AI worker is still waiting for an answer.",
            "It reached out but did not finish the job yet.",
            "Run the test again in a minute.")
    denied = [t for t in tools if t.decision != "allowed"]
    if denied:
        t = denied[0]
        return _blocker(
            "action_refused", "Your AI worker was stopped from doing a step.",
            "The safety rules said no: %s" % (t.denial_reason
                                               or t.denial_code or "no reason"),
            "Check your setup answers. If they look right, ask your "
            "administrator.")
    return _blocker(
        "unexpected_ending",
        "The test ended as: %s." % _STATE_WORDS.get(item.state, item.state),
        "That is not the result this job should give.",
        "Check your setup answers (what makes a good lead), then test again.")


# ── START / PAUSE / RESUME / STOP ────────────────────────────────────────────

def start(db: Session, dep: AIEmployeeDeployment, *,
          actor: Optional[User], now: Optional[datetime] = None
          ) -> Dict[str, Any]:
    """Leave the AI worker running in test mode. Practice records only."""
    from app.services.workforce import constants as WC
    from app.services.workforce import service as wf_service
    diag = diagnose(db, dep, action="start", now=now)
    if not diag["ok"]:
        return {"started": False, **diag}
    emp = _employee(db, dep)
    if emp.paused_at is not None:
        wf_service.resume(db, emp, actor=actor, reason="Start working")
    if emp.status == "disabled":
        emp.status = "active"
    wf_service.activate(db, emp, WC.SIMULATION, actor=actor,
                        reason="Start working (test mode)")
    _set_config_flag(dep, "test_mode_stopped", False)
    commerce.record_event(
        db, dep, from_state=dep.state, to_state=dep.state,
        reason="Started working in test mode", actor_kind=D.ACTOR_HUMAN,
        actor_id=getattr(actor, "id", None), detail={"stage": WC.SIMULATION})
    db.flush()
    return {"started": True, **diagnose(db, dep, action="start", now=now)}


def pause(db: Session, dep: AIEmployeeDeployment, *, actor: Optional[User],
          reason: str = "Paused by the customer") -> Dict[str, Any]:
    from app.services.workforce import service as wf_service
    emp = _employee(db, dep)
    if emp is None:
        return {"ok": False, "status": status(db, dep)}
    if dep.state in D.LIVE_STATES:
        from app.services.ai_deployment import lifecycle
        lifecycle.pause(db, dep, reason=reason, actor=actor)
    elif emp.paused_at is None:
        wf_service.pause(db, emp, reason=reason, actor=actor)
        commerce.record_event(db, dep, from_state=dep.state,
                              to_state=dep.state, reason="Paused",
                              actor_kind=D.ACTOR_HUMAN,
                              actor_id=getattr(actor, "id", None))
    db.flush()
    return {"ok": True, "status": status(db, dep)}


def resume(db: Session, dep: AIEmployeeDeployment, *,
           actor: Optional[User]) -> Dict[str, Any]:
    from app.services.workforce import service as wf_service
    emp = _employee(db, dep)
    if emp is None:
        return {"ok": False, "status": status(db, dep)}
    if dep.state == D.PAUSED:
        from app.services.ai_deployment import lifecycle
        out = lifecycle.resume(db, dep, actor=actor)
        db.flush()
        return {"ok": True, "status": status(db, dep), "detail": out}
    if emp.paused_at is not None:
        wf_service.resume(db, emp, actor=actor, reason="Resumed by the customer")
        commerce.record_event(db, dep, from_state=dep.state,
                              to_state=dep.state, reason="Resumed",
                              actor_kind=D.ACTOR_HUMAN,
                              actor_id=getattr(actor, "id", None))
    db.flush()
    return {"ok": True, "status": status(db, dep)}


def stop(db: Session, dep: AIEmployeeDeployment, *, actor: Optional[User],
         reason: str = "Stopped by the customer") -> Dict[str, Any]:
    """Stop test-mode work. History is kept; Start working turns it back on."""
    from app.services.workforce import constants as WC
    from app.services.workforce import queue as wf_queue
    from app.services.workforce import service as wf_service
    emp = _employee(db, dep)
    if emp is None:
        return {"ok": False, "status": status(db, dep)}
    if dep.state in D.LIVE_STATES:
        from app.services.ai_deployment import activation as t8_activation
        t8_activation.stand_down(db, dep, actor=actor, reason=reason)
    else:
        wf_service.activate(db, emp, WC.OFF, actor=actor, reason=reason)
        if emp.paused_at is not None:
            wf_service.resume(db, emp, actor=actor,
                              reason="cleared by stop")
        wf_queue.pause_all(db, employee_id=emp.id, reason="stopped: %s"
                           % reason, actor_id=getattr(actor, "id", None))
    _set_config_flag(dep, "test_mode_stopped", True)
    commerce.record_event(db, dep, from_state=dep.state, to_state=dep.state,
                          reason="Stopped", actor_kind=D.ACTOR_HUMAN,
                          actor_id=getattr(actor, "id", None))
    db.flush()
    return {"ok": True, "status": status(db, dep)}


# ── WATCH IT WORK ────────────────────────────────────────────────────────────

_BOARD_GROUPS = (
    ("working_now", "Working now"),
    ("waiting", "Waiting"),
    ("needs_you", "Needs you"),
    ("completed", "Completed"),
    ("appointments", "Appointments"),
    ("handoffs", "Ready for a person"),
)


def _board_group(state: str) -> str:
    from app.services.workforce import constants as WC
    if state in (WC.ASSIGNED, WC.ELIGIBILITY_PENDING, WC.ELIGIBLE, WC.WORKING):
        return "working_now"
    if state in (WC.WAITING_FOR_RESPONSE, WC.PAUSED):
        return "waiting"
    if state in (WC.NEEDS_REVIEW, WC.FAILED):
        return "needs_you"
    if state == WC.APPOINTMENT_BOOKED:
        return "appointments"
    if state in (WC.HUMAN_HANDOFF, WC.QUALIFIED):
        return "handoffs"
    return "completed"


def work_board(db: Session, dep: AIEmployeeDeployment, *,
               limit: int = 50) -> Dict[str, Any]:
    """What this AI worker is doing, from its own work items. No estimates.

    Every number is a count of rows; an empty group is shown as zero because
    zero is what the rows say, and `has_any_work` lets the screen say "nothing
    yet" instead of drawing a board of zeros as if it were activity.
    """
    from app.models.models import Lead
    from app.models.workforce_models import (AIEmployeeRun, AIHandoff,
                                             AIWorkItem)
    emp = _employee(db, dep)
    counts = {k: 0 for k, _ in _BOARD_GROUPS}
    if emp is None:
        return {"groups": [{"key": k, "label": l, "count": 0}
                           for k, l in _BOARD_GROUPS],
                "items": [], "has_any_work": False, "recent_runs": [],
                "open_handoffs": 0}
    items = (db.query(AIWorkItem)
             .filter(AIWorkItem.employee_id == emp.id,
                     AIWorkItem.organization_id == dep.organization_id)
             .order_by(AIWorkItem.updated_at.desc())
             .limit(500).all())
    lead_ids = [i.subject_id for i in items if i.subject_type == "lead"]
    leads = {}
    if lead_ids:
        for row in (db.query(Lead)
                    .filter(Lead.organization_id == dep.organization_id,
                            Lead.id.in_(lead_ids[:500])).all()):
            leads[str(row.id)] = row
    rows = []
    for it in items:
        group = _board_group(it.state)
        counts[group] += 1
        lead = leads.get(str(it.subject_id))
        if len(rows) < limit:
            rows.append({
                "id": it.id,
                "group": group,
                "state": it.state,
                "status": _STATE_WORDS.get(it.state, it.state),
                "who": ("%s %s" % (lead.first_name or "", lead.last_name or "")
                        ).strip() if lead else "A record",
                "practice": bool(lead is not None and
                                 getattr(lead, "is_test", False)),
                "updated_at": (it.updated_at.isoformat() + "Z"
                               if it.updated_at else None),
            })
    runs = (db.query(AIEmployeeRun)
            .filter(AIEmployeeRun.employee_id == emp.id,
                    AIEmployeeRun.organization_id == dep.organization_id)
            .order_by(AIEmployeeRun.started_at.desc()).limit(10).all())
    open_handoffs = (db.query(AIHandoff)
                     .filter(AIHandoff.employee_id == emp.id,
                             AIHandoff.organization_id == dep.organization_id,
                             AIHandoff.status.in_(("open", "pending",
                                                   "assigned")))
                     .count())
    return {
        "groups": [{"key": k, "label": l, "count": counts[k]}
                   for k, l in _BOARD_GROUPS],
        "items": rows,
        "has_any_work": bool(items),
        "open_handoffs": open_handoffs,
        "last_run_at": (runs[0].started_at.isoformat() + "Z"
                        if runs and runs[0].started_at else None),
        "recent_runs": [{"id": r.id, "status": r.status,
                         "summary": r.summary, "work_item_id": r.work_item_id,
                         "practice": r.mode == "simulation",
                         "at": (r.started_at.isoformat() + "Z"
                                if r.started_at else None)}
                        for r in runs],
    }


def summary(db: Session, dep: AIEmployeeDeployment, *,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything the customer's AI worker page needs, in one payload."""
    test_diag = diagnose(db, dep, action="test", now=now)
    start_diag = diagnose(db, dep, action="start", now=now)
    st = status(db, dep)
    health = worker_health(db, now=now)
    return {
        "deployment_id": dep.id,
        "name": dep.display_name,
        "job": dep.template_key,
        "status": st,
        "status_label": STATUS_WORDS.get(st, st),
        "can_run_test": test_diag["ok"],
        "can_start": start_diag["ok"] and st != S_TESTING,
        "can_pause": st in (S_TESTING, S_LIVE),
        "can_resume": st == S_PAUSED,
        "can_stop": st in (S_TESTING, S_PAUSED, S_LIVE),
        "test_blockers": test_diag["blockers"],
        "start_blockers": start_diag["blockers"],
        "needed_later_for_live": test_diag["needed_later_for_live"],
        "last_test": _config_flag(dep, "last_test"),
        "worker_running": health["running"],
        "live_messages": False,
        "live_messages_note": LIVE_OFF_NOTE,
        "board": work_board(db, dep),
    }


# ── THE ONE CUSTOMER FRONT DOOR: /ai-workforce ───────────────────────────────

# Jobs whose whole customer journey (setup -> Run test -> Start working ->
# results) has an end-to-end acceptance test on the real worker. A job not in
# this set is shown as "Coming soon", never as something that works.
VERIFIED_JOBS = ("lead_qualifier", "appointment_setter")

JOB_CARDS = (
    ("lead_qualifier", "Lead Qualifier",
     "Finds out which leads are interested and ready.",
     "Your leads, what makes a good lead for you, and a person to pass "
     "ready leads to."),
    ("appointment_setter", "Appointment Setter",
     "Helps schedule appointments.",
     "Your open times, the kind of appointment, and whose calendar to use."),
    ("follow_up_specialist", "Follow-Up Specialist",
     "Follows up when people have not responded.",
     "Your leads, how often to follow up, and a person to pass replies to."),
    ("customer_care_agent", "Customer Care",
     "Helps answer routine customer questions.",
     "Answers to your common questions and a person for anything else."),
)

# A finished-work count below this gives a rate that would mislead (one
# qualified out of one is "100%"). Below it the screen says N/A and why.
MIN_FOR_RATE = 5


def job_cards(db: Session, organization_id: str) -> List[Dict[str, Any]]:
    from app.services.ai_deployment import catalog as t8_catalog
    by_key = {r["template_key"]: r
              for r in t8_catalog.customer_catalog(db, organization_id)}
    cards = []
    for key, title, does, needs in JOB_CARDS:
        row = by_key.get(key) or {}
        comm = (row.get("commerce") or {}).get("commercial_state")
        held = [d for d in row.get("deployments", [])]
        if key not in VERIFIED_JOBS:
            availability, label, why = (
                "coming_soon", "Coming soon",
                "This AI worker is still being tested. It is not ready for "
                "your account yet.")
        elif held:
            availability, label, why = (
                "set_up", "Already set up",
                "You already have this AI worker. Open it below.")
        elif row.get("can_hire"):
            availability, label, why = ("available", "Available",
                                        "Ready to set up.")
        elif comm in (D.COMM_AVAILABLE, D.COMM_PENDING, D.COMM_LAPSED):
            availability, label, why = (
                "not_on_plan", "Not on your plan",
                "Ask your administrator to add it to your plan.")
        else:
            availability, label, why = (
                "not_offered", "Not available",
                "This AI worker is not offered on your account.")
        cards.append({"key": key, "title": title, "does": does,
                      "needs": needs, "availability": availability,
                      "availability_label": label, "why": why,
                      "can_set_up": availability == "available",
                      "deployment_ids": [d["id"] for d in held]})
    return cards


def results(db: Session, organization_id: str) -> Dict[str, Any]:
    """Outcomes counted from work items. Practice and real kept apart."""
    from app.models.ai_deployment_models import AIEmployeeDeployment
    from app.models.models import Lead
    from app.models.workforce_models import AIHandoff, AIWorkItem
    from app.services.workforce import constants as WC

    emp_ids = [r[0] for r in
               db.query(AIEmployeeDeployment.employee_id)
               .filter(AIEmployeeDeployment.organization_id
                       == organization_id,
                       AIEmployeeDeployment.employee_id.isnot(None)).all()]
    out = {}
    for kind in ("practice", "real"):
        out[kind] = {"finished": 0, "qualified": 0, "appointments": 0,
                     "handed_to_person": 0, "said_no": 0,
                     "could_not_reach": 0, "needs_a_person": 0,
                     "in_progress": 0}
    if emp_ids:
        rows = (db.query(AIWorkItem.state, Lead.is_test)
                .outerjoin(Lead, Lead.id == AIWorkItem.subject_id)
                .filter(AIWorkItem.organization_id == organization_id,
                        AIWorkItem.employee_id.in_(emp_ids)).all())
        for state, is_test in rows:
            b = out["practice" if is_test else "real"]
            if state in WC.TERMINAL_STATES:
                b["finished"] += 1
            if state == WC.QUALIFIED:
                b["qualified"] += 1
            elif state == WC.APPOINTMENT_BOOKED:
                b["appointments"] += 1
            elif state == WC.HUMAN_HANDOFF:
                b["handed_to_person"] += 1
            elif state in (WC.NOT_INTERESTED, WC.DO_NOT_CONTACT):
                b["said_no"] += 1
            elif state in (WC.BAD_CONTACT, WC.EXHAUSTED):
                b["could_not_reach"] += 1
            elif state in (WC.NEEDS_REVIEW, WC.FAILED):
                b["needs_a_person"] += 1
            elif state not in WC.TERMINAL_STATES:
                b["in_progress"] += 1
        open_h = (db.query(AIHandoff)
                  .filter(AIHandoff.organization_id == organization_id,
                          AIHandoff.employee_id.in_(emp_ids),
                          AIHandoff.status == "open").count())
    else:
        open_h = 0
    for kind, b in out.items():
        good = b["qualified"] + b["appointments"]
        if b["finished"] >= MIN_FOR_RATE:
            b["ready_rate"] = {"value": round(100.0 * good / b["finished"]),
                               "shown": True,
                               "how": "%d ready out of %d finished"
                                      % (good, b["finished"])}
        else:
            b["ready_rate"] = {"value": None, "shown": False,
                               "how": "Not measured yet: a fair rate needs "
                                      "at least %d finished leads (now %d)."
                                      % (MIN_FOR_RATE, b["finished"])}
    out["open_handoffs"] = open_h
    out["note"] = ("Practice results come from pretend leads in test mode. "
                   "Real results come from real leads only.")
    return out


def needs_you(db: Session, organization_id: str,
              summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Everything waiting on a person: setup gaps, reviews, open handoffs."""
    from app.models.models import Lead
    from app.models.workforce_models import AIHandoff, AIWorkItem
    from app.services.workforce import constants as WC
    out = []
    for s in summaries:
        if s["status"] in (S_RETIRED,):
            continue
        for b in (s["test_blockers"] if s["status"] != S_TESTING
                  else s["start_blockers"]):
            if b["code"] == "worker_not_running" and s["status"] != S_TESTING:
                continue
            out.append({"kind": "setup", "worker": s["name"],
                        "deployment_id": s["deployment_id"], **b})
    emp_names = {}
    from app.models.ai_deployment_models import AIEmployeeDeployment
    for d in (db.query(AIEmployeeDeployment)
              .filter(AIEmployeeDeployment.organization_id
                      == organization_id).all()):
        if d.employee_id:
            emp_names[d.employee_id] = (d.display_name, d.id)
    if emp_names:
        for h in (db.query(AIHandoff)
                  .filter(AIHandoff.organization_id == organization_id,
                          AIHandoff.employee_id.in_(list(emp_names)),
                          AIHandoff.status == "open")
                  .order_by(AIHandoff.created_at.desc()).limit(50).all()):
            lead = (db.query(Lead)
                    .filter(Lead.id == h.subject_id,
                            Lead.organization_id == organization_id).first())
            name, dep_id = emp_names.get(h.employee_id, ("AI worker", None))
            out.append({
                "kind": "handoff", "worker": name, "deployment_id": dep_id,
                "code": "handoff", "who": "customer",
                "what": "%s is ready for a person." % (
                    ("%s %s" % (lead.first_name or "", lead.last_name or ""))
                    .strip() if lead else "A lead"),
                "why": h.summary or "Your AI worker passed this lead to you.",
                "next": "Call or message them, then mark it done.",
                "practice": bool(lead is not None and lead.is_test),
                "handoff_id": h.id, "fix": None})
        for it in (db.query(AIWorkItem)
                   .filter(AIWorkItem.organization_id == organization_id,
                           AIWorkItem.employee_id.in_(list(emp_names)),
                           AIWorkItem.state.in_((WC.NEEDS_REVIEW,
                                                 WC.FAILED)))
                   .order_by(AIWorkItem.updated_at.desc()).limit(50).all()):
            name, dep_id = emp_names.get(it.employee_id, ("AI worker", None))
            out.append({
                "kind": "review", "worker": name, "deployment_id": dep_id,
                "code": it.state, "who": "customer",
                "what": "Your AI worker stopped and needs you to look.",
                "why": it.state_reason or "It was not sure what to do.",
                "next": "Open the lead and decide what happens next.",
                "work_item_id": it.id, "fix": None})
    return out


def home(db: Session, organization_id: str, *,
         now: Optional[datetime] = None) -> Dict[str, Any]:
    """The whole AI Workforce page for one workspace, in one call."""
    from app.services.ai_deployment import lifecycle
    deps = [d for d in lifecycle.list_for_org(db, organization_id,
                                              include_retired=False)]
    summaries = [summary(db, d, now=now) for d in deps]
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    return {
        "business_name": getattr(org, "name", None),
        "jobs": job_cards(db, organization_id),
        "workers": summaries,
        "needs_you": needs_you(db, organization_id, summaries),
        "results": results(db, organization_id),
        "live_messages": False,
        "live_messages_note": LIVE_OFF_NOTE,
    }
