"""THE AI WORKFORCE SWITCHBOARD - one place to turn safe test mode on or off.

WHY THIS EXISTS. Before it, making an AI worker usable for one account took
five separate admin acts across three screens (brand offering, commercial
terms, a catalogue item, a purchase, and three activation scopes) and the God
screen asked for a raw brand ID nobody could see. Production logs show the
owner typing brand NAMES into that box and getting 404 every time, so nothing
was ever switched on. This module does the whole thing in one call, picked by
brand and account, and reports the result in plain words.

WHAT "ON" MEANS HERE: SAFE TEST MODE, NOTHING MORE.

    - the brand OFFERS the verified jobs (test_mode.VERIFIED_JOBS);
    - the commercial terms say the job is INCLUDED with every package
      (commerce.ALL_PLANS) - no catalogue price is invented and no purchase
      row is faked. Many real accounts have no package on file, so naming
      plans would leave them out;
    - the platform, brand and each chosen account scope are raised to
      SIMULATION (never lowered if something is already higher, never a kill
      switch cleared).

SIMULATION reaches nobody: outward tools are refused outside CONTROLLED /
ACTIVE, and in a real account test mode works practice records only
(workforce/practice.py). Real customer messages stay a separate operator act
(CONTROLLED stage + AI_OPERATIONS_LIVE_SEND), which nothing here touches.

IDEMPOTENT. Running `enable` twice changes nothing the second time. That is
what lets the backend re-apply its startup brand list (app/main.py) at every
start, so accounts created since the last deploy are covered.
"""

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import Organization, Platform
from app.services.ai_deployment import commerce
from app.services.ai_deployment import constants as D

_log = logging.getLogger(__name__)

def _verified_jobs() -> tuple:
    from app.services.ai_deployment import test_mode
    return tuple(test_mode.VERIFIED_JOBS)


def _raise_scope(db: Session, scope_type: str, scope_id: str, *,
                 actor_user_id: Optional[str], reason: str) -> Dict[str, Any]:
    """Raise one scope to SIMULATION unless it is already at or above it."""
    from app.services.workforce import activation as wf_activation
    from app.services.workforce import constants as WC
    before = wf_activation.scope_report(db, scope_type, scope_id or "")
    rank = WC.ACTIVATION_RANK.get(before.get("state") or WC.OFF, 0)
    if rank >= WC.ACTIVATION_RANK[WC.SIMULATION]:
        return {"scope": scope_type, "id": scope_id, "state": before["state"],
                "changed": False, "killed": bool(before.get("kill_switch"))}
    wf_activation.set_state(db, scope_type, scope_id or "", WC.SIMULATION,
                            actor_user_id=actor_user_id, reason=reason)
    return {"scope": scope_type, "id": scope_id, "state": WC.SIMULATION,
            "changed": True, "killed": bool(before.get("kill_switch"))}


def _brand_orgs(db: Session, platform_id: str) -> List[Organization]:
    return (db.query(Organization)
            .filter(Organization.platform_id == platform_id)
            .order_by(Organization.name.asc()).all())


def enable(db: Session, *, platform_id: str,
           organization_ids: Optional[Iterable[str]] = None,
           jobs: Optional[Iterable[str]] = None,
           actor_user_id: Optional[str] = None,
           reason: str = "AI Workforce switchboard: safe test mode") -> Dict:
    """Turn safe test mode on for a brand and some (default: all) of its
    accounts. The caller commits."""
    from app.services.workforce import activation as wf_activation
    from app.services.workforce import service as wf_service
    from app.models.ai_deployment_models import AIOfferingTerms

    brand = db.query(Platform).filter(Platform.id == platform_id).first()
    if brand is None:
        raise ValueError("No such brand.")
    verified = _verified_jobs()
    jobs = [j for j in (jobs or verified) if j in verified]
    if not jobs:
        raise ValueError("None of those jobs is ready to switch on.")

    orgs = _brand_orgs(db, platform_id)
    if organization_ids is not None:
        wanted = {str(i) for i in organization_ids}
        foreign = wanted - {str(o.id) for o in orgs}
        if foreign:
            raise ValueError("Those accounts are not under this brand.")
        orgs = [o for o in orgs if str(o.id) in wanted]

    plans = [commerce.ALL_PLANS]

    wf_service.sync_templates(db)
    job_rows = []
    for key in jobs:
        wf_service.set_offering(db, platform_id=platform_id,
                                template_key=key, enabled=True,
                                actor_user_id=actor_user_id)
        terms = commerce.terms_for(db, platform_id, key)
        if terms is None:
            terms = AIOfferingTerms(platform_id=platform_id, template_key=key)
            db.add(terms)
            terms.commercial_mode = D.MODE_INCLUDED
            terms.requires_controlled_first = True
            terms.allowed_channels = json.dumps(["sms", "email"])
            terms.max_per_customer = 2
        included = commerce.json_list(terms.included_plan_keys, []) or []
        added = [p for p in plans if p not in included]
        if added:
            terms.included_plan_keys = json.dumps(included + added)
        eligible = commerce.json_list(terms.eligible_plan_keys, []) or []
        if eligible:
            terms.eligible_plan_keys = json.dumps(
                eligible + [p for p in plans if p not in eligible])
        terms.is_available = True
        terms.updated_by = actor_user_id
        job_rows.append({"job": key, "plans_added": added})
    db.flush()

    scopes = [_raise_scope(db, wf_activation.SCOPE_PLATFORM, "",
                           actor_user_id=actor_user_id,
                           reason="%s (simulation only; reaches nobody)"
                                  % reason),
              _raise_scope(db, wf_activation.SCOPE_BRAND, platform_id,
                           actor_user_id=actor_user_id, reason=reason)]
    for o in orgs:
        scopes.append(_raise_scope(db, wf_activation.SCOPE_CUSTOMER, o.id,
                                   actor_user_id=actor_user_id,
                                   reason=reason))
    db.flush()
    _log.info("AUDIT: AI workforce safe test mode enabled for brand %s, "
              "%d account(s), jobs %s, by %s", platform_id, len(orgs), jobs,
              actor_user_id)
    return {"brand": {"id": brand.id, "name": brand.name},
            "accounts": [{"id": o.id, "name": o.name} for o in orgs],
            "jobs": job_rows, "scopes": scopes,
            "changed": any(s["changed"] for s in scopes)
            or any(j["plans_added"] for j in job_rows)}


def disable(db: Session, *, platform_id: str,
            organization_ids: Optional[Iterable[str]] = None,
            actor_user_id: Optional[str] = None,
            reason: str = "AI Workforce switchboard: turned off") -> Dict:
    """Turn test mode off. Whole brand when no accounts are named.

    Lowers the customer scopes to OFF (and the brand scope too when the whole
    brand is turned off). Every AI worker in those accounts stops at its next
    step; their records and history are kept. The caller commits.
    """
    from app.services.workforce import activation as wf_activation
    from app.services.workforce import constants as WC
    from app.services.workforce import queue as wf_queue
    from app.models.workforce_models import AIEmployee

    brand = db.query(Platform).filter(Platform.id == platform_id).first()
    if brand is None:
        raise ValueError("No such brand.")
    orgs = _brand_orgs(db, platform_id)
    whole_brand = organization_ids is None
    if not whole_brand:
        wanted = {str(i) for i in organization_ids}
        orgs = [o for o in orgs if str(o.id) in wanted]
    for o in orgs:
        wf_activation.set_state(db, wf_activation.SCOPE_CUSTOMER, o.id, WC.OFF,
                                actor_user_id=actor_user_id, reason=reason)
        for emp in (db.query(AIEmployee)
                    .filter(AIEmployee.organization_id == o.id).all()):
            wf_queue.pause_all(db, employee_id=emp.id, reason=reason,
                               actor_id=actor_user_id)
    if whole_brand:
        wf_activation.set_state(db, wf_activation.SCOPE_BRAND, platform_id,
                                WC.OFF, actor_user_id=actor_user_id,
                                reason=reason)
    db.flush()
    return {"brand": {"id": brand.id, "name": brand.name},
            "accounts_turned_off": [o.name for o in orgs],
            "whole_brand": whole_brand}


def status(db: Session) -> Dict[str, Any]:
    """Every brand and account, and in plain words whether AI can work there."""
    from app.models.ai_deployment_models import AIEmployeeDeployment
    from app.services.ai_deployment import test_mode
    from app.services.workforce import activation as wf_activation
    from app.services.workforce import constants as WC

    verified = _verified_jobs()
    platform_row = wf_activation.scope_report(db, wf_activation.SCOPE_PLATFORM,
                                              "")
    dep_counts: Dict[str, int] = {}
    for org_id, in (db.query(AIEmployeeDeployment.organization_id)
                    .filter(AIEmployeeDeployment.state != D.RETIRED).all()):
        dep_counts[org_id] = dep_counts.get(org_id, 0) + 1

    brands = []
    for brand in db.query(Platform).order_by(Platform.name.asc()).all():
        brand_row = wf_activation.scope_report(db, wf_activation.SCOPE_BRAND,
                                               brand.id)
        accounts = []
        for org in _brand_orgs(db, brand.id):
            r = wf_activation.resolve(db, organization_id=org.id,
                                      platform_id=brand.id)
            test_ok = (not r.killed) and (
                WC.ACTIVATION_RANK.get(r.state, 0)
                >= WC.ACTIVATION_RANK[WC.SIMULATION])
            jobs = {}
            for key in verified:
                offer = commerce.resolve_offer(db, org, key)
                jobs[key] = {"on_plan": offer.is_live,
                             "state": offer.state,
                             "label": D.COMMERCIAL_LABELS.get(offer.state,
                                                              offer.state)}
            ready = test_ok and all(j["on_plan"] for j in jobs.values())
            accounts.append({
                "id": org.id, "name": org.name,
                "plan": commerce.plan_key_for(db, org),
                "demo": bool(getattr(org, "is_demo", False)),
                "test_mode": test_ok,
                "stage": r.state, "killed": r.killed,
                "jobs": jobs,
                "ai_workers": dep_counts.get(org.id, 0),
                "summary": ("Ready: customers can set up and test AI workers."
                            if ready else
                            ("Stopped by the emergency switch." if r.killed
                             else "Off.")),
            })
        brands.append({
            "id": brand.id, "name": brand.name, "slug": brand.slug,
            "brand_stage": brand_row.get("state"),
            "accounts": accounts,
            "accounts_ready": sum(1 for a in accounts
                                  if a["summary"].startswith("Ready")),
        })
    return {
        "platform_stage": platform_row.get("state"),
        "platform_killed": bool(platform_row.get("kill_switch")),
        "environment_kill": wf_activation.env_kill_engaged(),
        "jobs_ready": list(verified),
        "worker": test_mode.worker_health(db),
        "real_messages": _live_send_on(),
        "brands": brands,
    }


def _live_send_on() -> bool:
    """T7's own real-message switch, read through T7 (not a second copy)."""
    try:
        from app.services.ai_operations import flags
        return bool(flags.live_send_enabled())
    except Exception:                                        # noqa: BLE001
        return False


def apply_brands(db: Session, entries: Iterable[str]) -> List[Dict]:
    """Turn safe test mode on for these brands (ids or slugs). Idempotent.

    The backend calls this at startup with the brands its deployment lists
    (app/main.py), so accounts created since the last start are picked up.
    Unknown entries are logged and skipped. Lowering is never done from here -
    turning a brand off is an explicit act in God Mode.
    """
    entries = [e.strip() for e in entries if e and e.strip()]
    out = []
    for entry in entries:
        brand = (db.query(Platform)
                 .filter((Platform.id == entry) | (Platform.slug == entry))
                 .first())
        if brand is None:
            _log.warning("AI workforce startup brands: no brand %r; skipped",
                         entry)
            continue
        try:
            res = enable(db, platform_id=brand.id,
                         reason="Turned on at startup (deployment setting)")
            db.commit()
            out.append({"brand": brand.name, "accounts": len(res["accounts"]),
                        "changed": res["changed"]})
        except Exception:                                    # noqa: BLE001
            db.rollback()
            _log.exception("AI workforce startup brands: enabling %s failed",
                           entry)
    return out


# ── THE GOD MODE CONTROL PAGE ────────────────────────────────────────────────

def emergency_stop(db: Session, *, on: bool, actor_user_id: Optional[str],
                   reason: str) -> Dict[str, Any]:
    """The platform-wide stop. ON: every AI worker everywhere stops at its
    next step and queued work is parked. OFF: work may resume (each AI worker
    still needs its own account switched on). The caller commits."""
    from app.models.workforce_models import AIEmployee
    from app.services.workforce import activation as wf_activation
    from app.services.workforce import queue as wf_queue
    wf_activation.set_kill_switch(db, wf_activation.SCOPE_PLATFORM, "",
                                  bool(on), actor_user_id=actor_user_id,
                                  reason=reason)
    parked = 0
    if on:
        for emp in db.query(AIEmployee).all():
            parked += wf_queue.pause_all(db, employee_id=emp.id,
                                         reason="emergency stop: %s" % reason,
                                         actor_id=actor_user_id)
    else:
        for emp in (db.query(AIEmployee)
                    .filter(AIEmployee.status == "active",
                            AIEmployee.paused_at.is_(None)).all()):
            wf_queue.resume_all(db, employee_id=emp.id,
                                reason="emergency stop released",
                                actor_id=actor_user_id)
    db.flush()
    return {"stopped": bool(on), "work_parked": parked}


def control_summary(db: Session, *, now=None) -> Dict[str, Any]:
    """Everything the owner needs on one screen, in plain words."""
    from datetime import datetime, timedelta
    from app.models.ai_deployment_models import AIEmployeeDeployment
    from app.models.models import Lead
    from app.models.workforce_models import (AIEmployee, AIHandoff,
                                             AIToolExecution, AIWorkItem)
    from app.services.ai_deployment import test_mode
    from app.services.workforce import constants as WC

    now = now or datetime.utcnow()
    st = status(db)
    stopped = bool(st["environment_kill"] or st["platform_killed"])
    accounts = [a for b in st["brands"] for a in b["accounts"]]
    on = sum(1 for a in accounts if a["summary"].startswith("Ready"))
    worker = st["worker"]

    org_names = {o.id: o.name for o in db.query(Organization).all()}
    emp_rows = db.query(AIEmployee).all()
    emp_names = {e.id: e.name for e in emp_rows}
    deps = (db.query(AIEmployeeDeployment)
            .filter(AIEmployeeDeployment.state != D.RETIRED).all())

    counts = {"working_now": 0, "waiting": 0, "needs_a_person": 0,
              "finished": 0}
    for (state,) in db.query(AIWorkItem.state).all():
        if state in (WC.ASSIGNED, WC.ELIGIBILITY_PENDING, WC.ELIGIBLE,
                     WC.WORKING):
            counts["working_now"] += 1
        elif state in (WC.WAITING_FOR_RESPONSE, WC.PAUSED):
            counts["waiting"] += 1
        elif state in (WC.NEEDS_REVIEW, WC.FAILED):
            counts["needs_a_person"] += 1
        elif state in WC.TERMINAL_STATES:
            counts["finished"] += 1
    open_handoffs = db.query(AIHandoff).filter(
        AIHandoff.status == "open").count()

    since = now - timedelta(days=7)
    recent = []
    rows = (db.query(AIToolExecution)
            .filter(AIToolExecution.created_at >= since)
            .order_by(AIToolExecution.created_at.desc()).limit(25).all())
    lead_ids = {r.target_id for r in rows if r.target_type == "lead"}
    practice_ids = set()
    if lead_ids:
        practice_ids = {lid for (lid,) in db.query(Lead.id).filter(
            Lead.id.in_(list(lead_ids)), Lead.is_test.is_(True)).all()}
    words = test_mode._TOOL_WORDS
    for r in rows:
        recent.append({
            "at": r.created_at.isoformat() + "Z" if r.created_at else None,
            "account": org_names.get(r.organization_id, "Unknown account"),
            "worker": emp_names.get(r.employee_id, "AI worker"),
            "what": words.get(r.tool_key, r.tool_key),
            "result": (("Not done: %s" % (r.denial_reason
                                          or "a safety rule said no"))[:140]
                       if r.decision != "allowed"
                       else ("Practice only - nothing sent" if r.simulated
                             else "Done")),
            "practice": bool(r.simulated) or r.target_id in practice_ids,
        })

    problems = []
    if stopped:
        problems.append("The emergency stop is ON. No AI worker will do "
                        "anything until you release it.")
    if not worker["running"]:
        problems.append("The background worker has not checked in for over "
                        "%d minutes. New work will wait. It restarts with "
                        "every deploy; if this stays, press Run worker now "
                        "and check the error shown." % worker["fresh_minutes"])
    elif worker.get("last_status") == "error":
        problems.append("The background worker's last pass had an error.")
    if counts["needs_a_person"]:
        problems.append("%d lead(s) are waiting for a person to decide what "
                        "happens next." % counts["needs_a_person"])

    if stopped:
        headline = "Stopped. The emergency stop is on."
    elif on == 0:
        headline = "Off. No account has AI workers turned on."
    else:
        headline = ("Working in safe test mode for %d of %d accounts. "
                    "Real messages are %s." % (
                        on, len(accounts),
                        "ON" if st["real_messages"] else "OFF"))

    return {
        "headline": headline,
        "jobs": _job_overview(db, emp_rows, deps, now),
        "stopped": stopped,
        "stopped_by_environment": bool(st["environment_kill"]),
        "real_messages": st["real_messages"],
        "worker": worker,
        "accounts_on": on,
        "accounts_total": len(accounts),
        "ai_workers": len(deps),
        "work": counts,
        "open_handoffs": open_handoffs,
        "problems": problems,
        "recent": recent,
        "switchboard": st,
    }


def _job_overview(db: Session, emp_rows, deps, now) -> List[Dict[str, Any]]:
    """Every job in the catalog, honestly labelled.

    `proven` comes from test_mode.VERIFIED_JOBS (an end-to-end acceptance test
    on the real worker). A job not in that set is "Not proven yet" however many
    workers hold it. Counts are rows: AI workers holding the job, how many of
    those are deployed, and their runs in the last 7 days. Read-only.
    """
    from datetime import timedelta
    from app.models.workforce_models import AIEmployeeRun
    from app.services.ai_deployment import test_mode
    deployed_emp = {d.employee_id for d in deps if d.employee_id}
    by_role: Dict[str, Dict[str, int]] = {}
    role_of = {}
    for e in emp_rows:
        role_of[e.id] = e.job_role
        b = by_role.setdefault(e.job_role or "", {"workers": 0, "deployed": 0})
        b["workers"] += 1
        if e.id in deployed_emp:
            b["deployed"] += 1
    runs: Dict[str, Dict[str, int]] = {}
    try:
        for emp_id, st_, n in (db.query(AIEmployeeRun.employee_id,
                                        AIEmployeeRun.status,
                                        func.count())
                               .filter(AIEmployeeRun.started_at
                                       >= now - timedelta(days=7))
                               .group_by(AIEmployeeRun.employee_id,
                                         AIEmployeeRun.status).all()):
            r = runs.setdefault(role_of.get(emp_id) or "", {"completed": 0,
                                                           "failed": 0})
            if st_ == "completed":
                r["completed"] += int(n)
            elif st_ in ("failed", "error"):
                r["failed"] += int(n)
    except Exception:                                        # noqa: BLE001
        db.rollback()
        runs = {}
    out = []
    for key, name, what, _needs in test_mode.JOB_CARDS:
        b = by_role.get(key, {"workers": 0, "deployed": 0})
        out.append({"key": key, "name": name, "what": what,
                    "proven": key in test_mode.VERIFIED_JOBS,
                    "prerequisite": test_mode.JOB_PREREQUISITES.get(key),
                    "workers": b["workers"], "deployed": b["deployed"],
                    "runs_7d": runs.get(key, {"completed": 0, "failed": 0})})
    return out
