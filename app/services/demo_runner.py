"""Seed, advance and reset a demo scenario.

EVERY PUBLIC FUNCTION HERE CALLS `environment.require_demo()` FIRST. That is
the second lock on the door: the routes are already 404 outside the demo
environment, and the scripts already refuse, but a function that writes demo
records into a database must not depend on its callers remembering. If this
module is ever imported somewhere new, it stays safe by construction.

RESET IS A DELETE BY PREFIX, AND THAT IS WHY IT IS PROVABLE
-----------------------------------------------------------
Every record a scenario creates has an id starting `demo-`. Reset deletes rows
whose id carries that prefix, in child-before-parent order. The extent of the
operation is therefore knowable by inspection rather than by trusting a list of
tables to have been kept up to date - and a scenario that forgot to prefix an
id would show up as a leftover in the idempotency test rather than as a silent
duplicate at the next presentation.

It also means reset CANNOT touch a real record even if it were somehow run
against a database holding one, because a real record's id is a bare uuid with
no prefix. That is a second, independent reason the delete is safe, on top of
the environment guard.
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.demo_models import (
    DemoScenarioState, DemoEvent, DEMO_ID_PREFIX,
    SCENARIO_EMPTY, SCENARIO_READY, SCENARIO_RUNNING, SCENARIO_COMPLETE,
)
from app.services import environment as env
from app.services import demo_scenarios as registry

log = logging.getLogger(__name__)

# Child-before-parent. A row here may reference a row further down the list,
# never one further up, so deleting in this order never trips a foreign key.
#
# Tables are named as strings and deleted with raw SQL on purpose: several of
# them (appointment_case_files) have no ORM model at all, and a mix of ORM
# deletes and raw deletes would be two mechanisms to keep correct instead of
# one. A table that does not exist in this database is skipped, which is what
# lets the same reset run against SQLite and Postgres.
#
# ROWS THE REAL SERVICES CREATED DO NOT CARRY OUR PREFIX.
# `proposal_service.create_proposal()` mints its own uuid, as it should — the
# scenario is not allowed to reach in and rename it. So a prefix sweep alone
# leaves proposals, their tokens, blocks and portal events behind, which is
# exactly what the idempotency test caught: a second seed produced four
# proposals instead of two.
#
# The fix is to delete by OWNERSHIP as well as by prefix. Each rule below says
# "delete rows in this table whose <column> points at a demo-owned parent".
# Applied before the prefix sweep, child-before-parent, it collects everything
# a real service created on a demo scenario's behalf without ever touching a
# row whose parent is a genuine record.
CASCADE_RULES = (
    # brand-sales records created by proposal_service / pricing_approvals
    ("portal_events", "opportunity_id"),
    ("proposal_tokens", "proposal_id"),
    ("proposal_blocks", "proposal_id"),
    ("proposal_files", "proposal_id"),
    ("pricing_approval_requests", "opportunity_id"),
    ("proposals", "opportunity_id"),
    ("discovery_records", "opportunity_id"),
    ("opportunity_events", "opportunity_id"),
    ("sales_appointment_participants", "appointment_id"),
    ("sales_appointments", "opportunity_id"),
    ("sales_meeting_types", "brand_sales_org_id"),
    ("memberships", "user_id"),
    # customer-tenant records
    ("appointment_case_files", "lead_id"),
    ("lead_outcomes", "lead_id"),
    ("messages", "lead_id"),
    ("replies", "lead_id"),
    ("email_messages", "lead_id"),
    ("voice_calls", "lead_id"),
    ("cadence_states", "lead_id"),
    ("pipeline_conversations", "lead_id"),
    ("booking_links", "lead_id"),
    ("advisor_availability_blocks", "advisor_id"),
    ("availability_profiles", "user_id"),
)

DELETE_ORDER = (
    # customer tenant tree
    "appointment_case_files",
    "lead_outcomes",
    "messages",
    "replies",
    "email_messages",
    "voice_calls",
    "voice_call_campaigns",
    "cadence_states",
    "pipeline_conversations",
    "booking_links",
    "advisor_availability_blocks",
    "leads",
    # brand sales tree
    "portal_events",
    "proposal_tokens",
    "proposal_blocks",
    "proposal_files",
    "pricing_approval_requests",
    "proposals",
    "sales_appointment_participants",
    "sales_appointments",
    "opportunity_events",
    "discovery_records",
    "opportunities",
    "sales_meeting_types",
    "brand_packages",
    "memberships",
    # integration + identity
    "integration_request_logs",
    "integration_credentials",
    "availability_profiles",
    "availability_windows",
    "availability_blocks",
    "calendar_connections",
    "external_busy_blocks",
    "appointment_meetings",
    "users",
    "organizations",
    "brand_sales_orgs",
    "platforms",
)


def _state(db: Session, scenario) -> DemoScenarioState:
    row = (db.query(DemoScenarioState)
           .filter(DemoScenarioState.scenario_key == scenario.key).first())
    if row is None:
        row = DemoScenarioState(
            scenario_key=scenario.key, domain=scenario.domain,
            status=SCENARIO_EMPTY, current_step=0,
            total_steps=len(scenario.steps()))
        db.add(row)
        db.flush()
    row.total_steps = len(scenario.steps())
    return row


def audit(db: Session, scenario_key: Optional[str], action: str,
          success: bool = True, detail: str = None, step_index: int = None,
          step_label: str = None, provider: str = None,
          operator=None) -> None:
    db.add(DemoEvent(
        scenario_key=scenario_key, action=action,
        step_index=step_index, step_label=step_label,
        operator_user_id=getattr(operator, "id", None),
        operator_email=getattr(operator, "email", None),
        simulated_provider=provider, success=success,
        detail=(detail or "")[:2000],
        occurred_at=datetime.utcnow()))


# ── reset ───────────────────────────────────────────────────────────────────

def _table_exists(db: Session, table: str) -> bool:
    try:
        db.execute(text("SELECT 1 FROM %s LIMIT 1" % table))
        return True
    except Exception:
        db.rollback()
        return False


def _like(prefix: str) -> str:
    r"""A LIKE pattern that matches `prefix` literally, then anything.

    Scenario keys contain underscores (`brand_sales`, `speed_to_lead`), and in
    SQL LIKE an underscore is a single-character wildcard. Left unescaped,
    `demo-brand_sales-%` would also match `demo-brandXsales-...`. No such key
    exists today, which is exactly why this would go unnoticed until somebody
    added one — so the underscores are escaped and the pattern is anchored with
    an explicit ESCAPE clause at every call site.
    """
    return prefix.replace("\\", "\\\\").replace("_", r"\_").replace("%", r"\%") + "%"


def _sweep(db: Session, pattern: str) -> dict:
    """Delete everything matching `pattern`, cascade rules first.

    `pattern` is a SQL LIKE: `demo-%` for everything, `demo-<key>-%` for one
    scenario. Both passes are needed - see the CASCADE_RULES comment - and
    cascades run first because a child row's parent may be removed by the
    prefix pass that follows.
    """
    removed = {}
    total = 0

    def run(sql, params, table):
        nonlocal total
        try:
            n = db.execute(text(sql), params).rowcount or 0
            if n:
                removed[table] = removed.get(table, 0) + n
                total += n
            db.commit()
        except Exception as e:
            db.rollback()
            log.warning("demo reset: %s skipped (%s)", table, e)

    for table, column in CASCADE_RULES:
        if _table_exists(db, table):
            run("DELETE FROM %s WHERE %s LIKE :p ESCAPE '\\'" % (table, column),
                {"p": pattern}, table)

    for table in DELETE_ORDER:
        if _table_exists(db, table):
            run("DELETE FROM %s WHERE id LIKE :p ESCAPE '\\'" % table,
                {"p": pattern}, table)

    return {"removed": removed, "total": total}


def reset_scenario(db: Session, key: str, operator=None) -> dict:
    """Remove ONE scenario's records, leaving any other loaded scenario alone.

    This is what lets an operator hold the customer story and the sales story
    in the demo at the same time and move between them, which is the whole
    point of having two. Every id a scenario mints starts `demo-<key>-`, so one
    scenario's extent is as knowable as all of them together.
    """
    env.require_demo()
    scenario = registry.get(key)
    if scenario is None:
        return {"ok": False, "error": "Unknown scenario %r." % key}

    out = _sweep(db, _like("%s%s-" % (DEMO_ID_PREFIX, key)))
    row = _state(db, scenario)
    row.status = SCENARIO_EMPTY
    row.current_step = 0
    row.last_reset_at = datetime.utcnow()
    audit(db, key, "reset", True,
          "Removed %d records for %s." % (out["total"], scenario.name),
          operator=operator)
    db.commit()
    return {"ok": True, "scenario": key, **out}


def reset_all(db: Session, operator=None) -> dict:
    """Remove every demo-owned record. Deterministic and complete.

    Returns a per-table count so the operator can see what went, and so the
    idempotency test can assert that a second reset removes nothing.
    """
    env.require_demo()
    out = _sweep(db, _like(DEMO_ID_PREFIX))

    # Scenario state is reset, not deleted, so the control panel keeps its
    # list of scenarios and simply shows every one of them as empty.
    for row in db.query(DemoScenarioState).all():
        row.status = SCENARIO_EMPTY
        row.current_step = 0
        row.last_reset_at = datetime.utcnow()
    audit(db, None, "reset", True, "Removed %d demo records." % out["total"],
          operator=operator)
    db.commit()
    return {"ok": True, **out}


# ── seed ────────────────────────────────────────────────────────────────────

def seed_scenario(db: Session, key: str, operator=None) -> dict:
    """Reset THIS scenario, then build its starting world.

    Its own reset runs first, unconditionally: seeding on top of an existing
    copy is how you get two of everything, and a presentation is the worst
    place to discover that.

    It resets only this scenario, not the whole demo, so an operator can hold
    the customer story and the sales story loaded at once and move between
    them. `reset_all` is still there as the between-presentations wipe.
    """
    env.require_demo()
    scenario = registry.get(key)
    if scenario is None:
        return {"ok": False, "error": "Unknown scenario %r." % key}

    reset_scenario(db, key, operator=operator)

    now = datetime.utcnow()
    try:
        summary = scenario.seed(db, now)
    except Exception as e:
        db.rollback()
        log.exception("demo seed failed for %s", key)
        audit(db, key, "seed", False, str(e)[:500], operator=operator)
        db.commit()
        return {"ok": False, "error": "Seed failed: %s" % e}

    row = _state(db, scenario)
    row.status = SCENARIO_READY
    row.current_step = 0
    row.seeded_at = now
    row.operator_user_id = getattr(operator, "id", None)
    audit(db, key, "seed", True, "Seeded %s." % scenario.name, operator=operator)
    db.commit()
    return {"ok": True, "scenario": scenario.describe(), "summary": summary,
            "state": state_out(db, scenario)}


# ── advance ─────────────────────────────────────────────────────────────────

def advance_scenario(db: Session, key: str, operator=None,
                     step_key: str = None) -> dict:
    """Run the next step, or a named one.

    A named step may only be run if it is the next one. Letting an operator
    skip ahead would produce records whose prerequisites do not exist - an
    accepted proposal that was never sent - and the product would then render
    something it could never actually be in.
    """
    env.require_demo()
    scenario = registry.get(key)
    if scenario is None:
        return {"ok": False, "error": "Unknown scenario %r." % key}

    row = _state(db, scenario)
    if not row.is_seeded():
        return {"ok": False, "error": "Seed the scenario before advancing it."}

    steps = scenario.steps()
    idx = row.current_step
    if idx >= len(steps):
        return {"ok": False, "error": "This scenario is already complete.",
                "state": state_out(db, scenario)}

    step = steps[idx]
    if step_key and step_key != step.key:
        return {"ok": False,
                "error": "Next step is %r, not %r. Steps run in order."
                         % (step.key, step_key)}

    now = datetime.utcnow()
    try:
        detail = step.handler(db, now)
    except Exception as e:
        db.rollback()
        log.exception("demo step %s/%s failed", key, step.key)
        audit(db, key, "advance", False, str(e)[:500], step_index=idx,
              step_label=step.label, provider=step.provider, operator=operator)
        db.commit()
        return {"ok": False, "error": "Step failed: %s" % e,
                "state": state_out(db, scenario)}

    row.current_step = idx + 1
    row.status = (SCENARIO_COMPLETE if row.current_step >= len(steps)
                  else SCENARIO_RUNNING)
    row.last_advanced_at = now
    row.operator_user_id = getattr(operator, "id", None)
    audit(db, key, "advance", True, detail, step_index=idx,
          step_label=step.label, provider=step.provider, operator=operator)
    db.commit()
    return {"ok": True, "step": step.to_dict(idx, True), "result": detail,
            "state": state_out(db, scenario)}


# ── read ────────────────────────────────────────────────────────────────────

def state_out(db: Session, scenario) -> dict:
    row = _state(db, scenario)
    steps = scenario.steps()
    done = row.current_step
    next_step = steps[done].to_dict(done, False) if done < len(steps) else None
    return {
        "scenario": scenario.describe(),
        "status": row.status,
        "current_step": done,
        "total_steps": len(steps),
        "seeded_at": row.seeded_at.isoformat() if row.seeded_at else None,
        "last_advanced_at": (row.last_advanced_at.isoformat()
                             if row.last_advanced_at else None),
        "next_step": next_step,
        "steps": [s.to_dict(i, i < done) for i, s in enumerate(steps)],
    }


def overview(db: Session) -> dict:
    """Everything the control panel needs in one call."""
    env.require_demo()
    from app.services import demo_firewall as fw
    scenarios = []
    for s in registry.all_scenarios():
        scenarios.append(state_out(db, s))
    db.commit()
    recent = (db.query(DemoEvent)
              .order_by(DemoEvent.occurred_at.desc()).limit(20).all())
    return {
        "environment": env.banner_payload(),
        "scenarios": scenarios,
        "firewall": {
            "installed": fw.is_installed(),
            # Surfaced so a missing simulation shows up as something the
            # operator can see, rather than as an empty screen they cannot
            # explain mid-presentation.
            "blocked_attempts": fw.blocked_attempts()[-10:],
        },
        "recent_events": [{
            "scenario": e.scenario_key, "action": e.action,
            "step": e.step_label, "provider": e.simulated_provider,
            "success": e.success, "detail": (e.detail or "")[:200],
            "at": e.occurred_at.isoformat(),
        } for e in recent],
    }
