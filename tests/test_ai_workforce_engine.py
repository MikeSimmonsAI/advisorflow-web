"""AI WORKFORCE — the engine's own invariants.

WHAT THIS FILE DEFENDS, and each of these is a property rather than a
behaviour, which is why they are asserted directly rather than through a
scenario:

  * the two registries agree with themselves — every registered tool has an
    implementation, every template names only registered tools
  * the state machine has no way out of a terminal state and no unreachable
    state
  * activation resolves to the MINIMUM across four scopes, and a missing row
    is `off` rather than "inherit"
  * authority is the INTERSECTION of template, brand and explicit grant, and
    nothing can widen it
  * the dark-launch defaults are what the deployment actually ships with

The adversarial and end-to-end behaviour lives in the scenario catalogue and
is exercised by tests/test_ai_workforce_simulation.py.
"""

import itertools
import json

import pytest

from app.models.models import Organization, Platform, User
from app.models.workforce_models import (AIEmployee, AIWorkItem,
                                         AIWorkforceActivation)
from app.services.auth_service import hash_password
from app.services.workforce import activation as wf_activation
from app.services.workforce import constants as C
from app.services.workforce import policy as wf_policy
from app.services.workforce import queue as wf_queue
from app.services.workforce import registry as wf_registry
from app.services.workforce import service as wf_service
from app.services.workforce import tools as wf_tools

_SEQ = itertools.count(900000)


# ═══════════════════════════════════════════════════════════════════════════
# THE REGISTRIES
# ═══════════════════════════════════════════════════════════════════════════

def test_every_registered_tool_has_an_implementation():
    """A registered tool with no handler fails the first time an employee asks.

    The gateway refuses it with `unknown_tool` and logs an error, which is the
    right behaviour and a terrible way to find out.
    """
    wf_tools._ensure_handlers_loaded()
    missing = sorted(set(wf_registry.ALL_TOOL_KEYS) - set(wf_tools._HANDLERS))
    assert not missing, "registered with no handler: %s" % missing


def test_no_handler_without_a_registry_entry():
    """A handler nothing can reach is dead code that looks like a feature."""
    wf_tools._ensure_handlers_loaded()
    orphans = sorted(set(wf_tools._HANDLERS) - set(wf_registry.ALL_TOOL_KEYS))
    assert not orphans, "handler with no registry entry: %s" % orphans


def test_templates_only_reference_registered_tools():
    for key in wf_registry.ALL_TEMPLATE_KEYS:
        spec = wf_registry.template(key)
        unknown = [t for t in spec.tool_keys
                   if t not in wf_registry.TOOLS]
        assert not unknown, "%s references unknown tools %s" % (key, unknown)


def test_the_set_of_outward_reaching_tools_is_exactly_this():
    """A NEW TOOL SILENTLY JOINING THIS SET IS THE CHANGE THAT MATTERS MOST.

    Everything in it is refused below the CONTROLLED activation stage. A tool
    added without `reaches_outside` that can in fact reach somebody would run
    in simulation, so this list is pinned rather than derived.
    """
    assert set(wf_registry.EXECUTING_TOOL_KEYS) == {
        "appointment.book",
        "appointment.reschedule",
        "conversation.place_call",
        "conversation.send_email",
        "conversation.send_sms",
    }


def test_every_outreach_job_can_record_an_opt_out():
    """An employee that may text somebody must be able to honour a STOP."""
    for key in wf_registry.ALL_TEMPLATE_KEYS:
        spec = wf_registry.template(key)
        sends = [t for t in spec.tool_keys
                 if (wf_registry.tool(t) or None)
                 and wf_registry.tool(t).reaches_outside
                 and wf_registry.tool(t).channel in (C.CHANNEL_SMS,
                                                     C.CHANNEL_EMAIL)]
        if not sends:
            continue
        assert "lead.mark_do_not_contact" in spec.tool_keys, (
            "%s can send but cannot record an opt-out" % key)


def test_the_supervisor_holds_no_outward_tools():
    spec = wf_registry.template("manager_supervisor")
    reaching = [t for t in spec.tool_keys
                if wf_registry.tool(t).reaches_outside]
    assert not reaching, "the supervisor holds outward tools: %s" % reaching


def test_normalize_cannot_widen_a_bound():
    wide = wf_registry.normalize_tool_keys(
        list(wf_registry.ALL_TOOL_KEYS), bound=["lead.get"])
    assert wide == ["lead.get"]
    channels = wf_registry.normalize_channels(list(C.ALL_CHANNELS),
                                              bound=[C.CHANNEL_EMAIL])
    assert channels == [C.CHANNEL_EMAIL]


# ═══════════════════════════════════════════════════════════════════════════
# THE STATE MACHINE
# ═══════════════════════════════════════════════════════════════════════════

def test_terminal_states_have_no_way_out():
    for state in C.TERMINAL_STATES:
        assert not C.ALLOWED_TRANSITIONS.get(state), (
            "%s has outgoing transitions" % state)


def test_every_state_is_reachable_from_assigned():
    reachable = {C.ASSIGNED}
    changed = True
    while changed:
        changed = False
        for src in list(reachable):
            for dst in C.ALLOWED_TRANSITIONS.get(src, set()):
                if dst not in reachable:
                    reachable.add(dst)
                    changed = True
    assert set(C.ALL_STATES) == reachable


def test_review_cannot_go_straight_back_to_working():
    """Clearing a review must not skip the eligibility check."""
    edges = C.ALLOWED_TRANSITIONS[C.NEEDS_REVIEW]
    assert C.ELIGIBILITY_PENDING in edges
    assert C.WORKING not in edges


def test_every_transition_target_is_a_real_state():
    for src, targets in C.ALLOWED_TRANSITIONS.items():
        assert src in C.ALL_STATES
        for dst in targets:
            assert dst in C.ALL_STATES, "%s -> %s is not a state" % (src, dst)


def test_path_between_refuses_to_leave_a_terminal_state():
    assert wf_queue.path_between(C.DO_NOT_CONTACT, C.WORKING) is None
    assert wf_queue.path_between(C.ASSIGNED, C.APPOINTMENT_BOOKED)


def test_advance_to_walks_only_legal_edges():
    route = wf_queue.path_between(C.ASSIGNED, C.APPOINTMENT_BOOKED)
    state = C.ASSIGNED
    for hop in route:
        assert wf_queue.can_transition(state, hop), "%s -> %s" % (state, hop)
        state = hop
    assert state == C.APPOINTMENT_BOOKED


# ═══════════════════════════════════════════════════════════════════════════
# ACTIVATION — the dark-launch guarantees
# ═══════════════════════════════════════════════════════════════════════════

def test_the_platform_default_is_off():
    assert C.DEFAULT_ACTIVATION == C.OFF


def test_simulation_and_shadow_are_not_executing_stages():
    """Nothing may reach a real person below CONTROLLED."""
    assert C.SIMULATION not in C.EXECUTING_STAGES
    assert C.SHADOW not in C.EXECUTING_STAGES
    assert C.OFF not in C.EXECUTING_STAGES
    assert C.EXECUTING_STAGES == {C.CONTROLLED, C.ACTIVE}


def test_live_voice_is_disabled_in_this_build(monkeypatch):
    monkeypatch.delenv("AI_WORKFORCE_LIVE_VOICE", raising=False)
    assert wf_activation.live_voice_enabled() is False


def test_a_missing_activation_row_is_off_not_inherited(db_session, sample_org):
    """An organization nobody configured is one nobody switched on."""
    wf_activation.set_state(db_session, wf_activation.SCOPE_PLATFORM, "",
                            C.ACTIVE, reason="test")
    resolved = wf_activation.resolve(db_session,
                                     organization_id=sample_org.id)
    assert resolved.state == C.OFF
    assert not resolved.may_execute


def test_activation_takes_the_minimum_across_scopes(db_session, sample_org):
    wf_activation.set_state(db_session, wf_activation.SCOPE_PLATFORM, "",
                            C.ACTIVE, reason="test")
    wf_activation.set_state(db_session, wf_activation.SCOPE_CUSTOMER,
                            sample_org.id, C.SIMULATION, reason="test")
    resolved = wf_activation.resolve(db_session,
                                     organization_id=sample_org.id)
    assert resolved.state == C.SIMULATION


def test_the_kill_switch_outranks_every_stage(db_session, sample_org):
    wf_activation.set_state(db_session, wf_activation.SCOPE_PLATFORM, "",
                            C.ACTIVE, reason="test")
    wf_activation.set_state(db_session, wf_activation.SCOPE_CUSTOMER,
                            sample_org.id, C.ACTIVE, reason="test")
    wf_activation.set_kill_switch(db_session, wf_activation.SCOPE_CUSTOMER,
                                  sample_org.id, True, reason="incident")
    resolved = wf_activation.resolve(db_session,
                                     organization_id=sample_org.id)
    assert resolved.killed
    assert resolved.state == C.OFF
    assert not resolved.may_run


def test_the_environment_kill_needs_no_database(db_session, sample_org,
                                                monkeypatch):
    monkeypatch.setenv("AI_WORKFORCE_KILL", "1")
    resolved = wf_activation.resolve(db_session,
                                     organization_id=sample_org.id)
    assert resolved.killed
    assert resolved.killed_by_scope == "environment"


def test_set_state_refuses_an_unknown_stage(db_session):
    with pytest.raises(ValueError):
        wf_activation.set_state(db_session, wf_activation.SCOPE_PLATFORM, "",
                                "maximum-overdrive")


# ═══════════════════════════════════════════════════════════════════════════
# AUTHORITY — template ∩ brand ∩ explicit grant
# ═══════════════════════════════════════════════════════════════════════════

def _brand(db):
    row = Platform(name="Workforce Brand", slug="wf-brand-%d" % next(_SEQ),
                   is_active=True)
    db.add(row)
    db.commit()
    return row


def _org(db, brand, features=None):
    row = Organization(name="Workforce Customer",
                       slug="wf-org-%d" % next(_SEQ),
                       platform_id=brand.id, plan="standard",
                       enabled_features=json.dumps(
                           features if features is not None
                           else ["leads", "sms", "email", "booking",
                                 "calendar", "crm"]))
    db.add(row)
    db.commit()
    return row


def _admin(db, org):
    row = User(organization_id=org.id,
               email="wfadmin%d@example.invalid" % next(_SEQ),
               password_hash=hash_password("AdminPass123!"),
               full_name="Workforce Admin", role="org_admin",
               must_change_password=False)
    db.add(row)
    db.commit()
    return row


def _hire(db, org, actor, template_key="reactivation_specialist", **kw):
    wf_service.sync_templates(db)
    wf_service.set_offering(db, platform_id=org.platform_id,
                            template_key=template_key, enabled=True)
    emp = wf_service.hire(db, organization_id=org.id,
                          template_key=template_key, actor=actor, **kw)
    db.commit()
    return emp


def test_a_new_employee_starts_off_and_in_draft(db_session):
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    emp = _hire(db_session, org, admin)
    assert emp.status == "draft"
    assert emp.activation_state == C.OFF
    resolved = wf_activation.resolve(db_session, employee=emp)
    assert resolved.state == C.OFF
    assert not resolved.may_run


def test_hiring_writes_an_explicit_grant_per_tool(db_session):
    from app.models.workforce_models import AIEmployeeAuthority
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    emp = _hire(db_session, org, admin)
    grants = (db_session.query(AIEmployeeAuthority)
              .filter(AIEmployeeAuthority.employee_id == emp.id).all())
    assert grants, "hiring granted nothing"
    spec = wf_registry.template("reactivation_specialist")
    assert {g.tool_key for g in grants} <= set(spec.tool_keys)
    assert all(g.granted_by == admin.id for g in grants)


def test_authority_is_default_deny_without_a_grant(db_session):
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    emp = _hire(db_session, org, admin)
    wf_service.set_authority(db_session, emp, ["lead.get"], actor=admin)
    db_session.commit()
    pol = wf_policy.resolve(db_session, emp)
    assert pol.tool_keys == {"lead.get"}
    ok, code, _ = wf_policy.may_use_tool(pol, "conversation.send_sms")
    assert not ok
    assert code == C.DENY_NOT_GRANTED


def test_a_brand_cannot_widen_past_the_template(db_session):
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    wf_service.sync_templates(db_session)
    offering = wf_service.set_offering(
        db_session, platform_id=brand.id,
        template_key="support_specialist", enabled=True,
        tool_keys=["appointment.book", "conversation.send_sms",
                   "conversation.send_email"])
    db_session.commit()
    allowed = json.loads(offering.allowed_tool_keys)
    spec = wf_registry.template("support_specialist")
    assert "appointment.book" not in allowed
    assert set(allowed) <= set(spec.tool_keys)


def test_a_disabled_brand_offering_removes_the_authority(db_session):
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    emp = _hire(db_session, org, admin)
    wf_service.set_offering(db_session, platform_id=brand.id,
                            template_key="reactivation_specialist",
                            enabled=False)
    db_session.commit()
    pol = wf_policy.resolve(db_session, emp)
    assert pol.tool_keys == set()


def test_run_limits_cannot_be_raised_past_the_ceiling(db_session):
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    emp = _hire(db_session, org, admin)
    emp.max_iterations = 10000
    emp.max_tool_calls = 10000
    db_session.commit()
    pol = wf_policy.resolve(db_session, emp)
    assert pol.max_iterations <= C.MAX_ITERATIONS_CEILING
    assert pol.max_tool_calls <= C.MAX_TOOL_CALLS_CEILING


def test_unconfigured_operating_hours_are_not_permission(db_session):
    """`{}` is a customer who never answered the question, not "any time"."""
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    emp = _hire(db_session, org, admin, operating_hours={})
    pol = wf_policy.resolve(db_session, emp)
    ok, why = wf_policy.within_operating_hours(pol)
    assert not ok
    assert "operating hours" in why.lower()


# ═══════════════════════════════════════════════════════════════════════════
# THE QUEUE
# ═══════════════════════════════════════════════════════════════════════════

def test_enqueue_is_idempotent(db_session, sample_lead):
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    emp = _hire(db_session, org, admin)
    first = wf_queue.enqueue(db_session, emp, [sample_lead.id])
    second = wf_queue.enqueue(db_session, emp, [sample_lead.id])
    db_session.commit()
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["skipped_existing"] == 1
    assert (db_session.query(AIWorkItem)
            .filter(AIWorkItem.employee_id == emp.id).count()) == 1


def test_only_one_worker_can_claim_an_item(db_session, sample_lead):
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    emp = _hire(db_session, org, admin)
    wf_queue.enqueue(db_session, emp, [sample_lead.id])
    db_session.commit()
    first = wf_queue.claim(db_session, emp, limit=5)
    second = wf_queue.claim(db_session, emp, limit=5)
    assert len(first) == 1
    assert len(second) == 0


def test_a_stale_claim_token_cannot_write(db_session, sample_lead):
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    emp = _hire(db_session, org, admin)
    wf_queue.enqueue(db_session, emp, [sample_lead.id])
    db_session.commit()
    (item, _token), = wf_queue.claim(db_session, emp, limit=1)
    with pytest.raises(wf_queue.IllegalTransition):
        wf_queue.transition(db_session, item, C.NEEDS_REVIEW,
                            reason="stale worker",
                            claim_token="not-the-token-it-holds")


def test_an_illegal_transition_raises(db_session, sample_lead):
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    emp = _hire(db_session, org, admin)
    wf_queue.enqueue(db_session, emp, [sample_lead.id])
    db_session.commit()
    item = (db_session.query(AIWorkItem)
            .filter(AIWorkItem.employee_id == emp.id).first())
    wf_queue.transition(db_session, item, C.ELIGIBILITY_PENDING)
    wf_queue.transition(db_session, item, C.DO_NOT_CONTACT, reason="opted out")
    with pytest.raises(wf_queue.IllegalTransition):
        wf_queue.transition(db_session, item, C.WORKING, reason="forced")


def test_pausing_an_employee_pauses_its_queue(db_session, sample_lead):
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    emp = _hire(db_session, org, admin)
    wf_queue.enqueue(db_session, emp, [sample_lead.id])
    db_session.commit()
    wf_service.pause(db_session, emp, reason="test", actor=admin)
    db_session.commit()
    item = (db_session.query(AIWorkItem)
            .filter(AIWorkItem.employee_id == emp.id).first())
    assert item.state == C.PAUSED


def test_resuming_returns_items_at_eligibility(db_session, sample_lead):
    brand = _brand(db_session)
    org = _org(db_session, brand)
    admin = _admin(db_session, org)
    emp = _hire(db_session, org, admin)
    wf_queue.enqueue(db_session, emp, [sample_lead.id])
    db_session.commit()
    wf_service.pause(db_session, emp, reason="test", actor=admin)
    wf_service.resume(db_session, emp, actor=admin)
    db_session.commit()
    item = (db_session.query(AIWorkItem)
            .filter(AIWorkItem.employee_id == emp.id).first())
    assert item.state == C.ELIGIBILITY_PENDING
