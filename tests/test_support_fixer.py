"""Support Intelligence — the fixer, correlation, and the daily brief.

THE CLAIM UNDER TEST
--------------------
The auto-fixer is the part of this system that can change a customer's state
without a person in the loop. Everything that makes that acceptable is
mechanical, and mechanical things can be asserted:

    a repair runs only under an authority the SERVER resolved
    a repair is FIXED only when a second, independent read says so
    a repair that could not be verified is reported as not fixed
    every execution leaves a before-state, an after-state and an audit row
    an approval-class repair cannot be run, delegated or pre-approved
    an engineering-class repair cannot be executed at all

And the intelligence layer above it:

    the same fault at two customers becomes ONE incident
    the same fault at two BRANDS is classified as ours, not theirs
    a customer is told about an incident in words that name nobody else
    a rate computed from zero attempts is NULL, not 100%
"""

import json
from datetime import datetime, timedelta

import pytest

from app.models.calendar_models import CalendarConnection
from app.models.models import AuditLogEntry, Organization, Platform, User
from app.models.support_models import (
    AuthorizationSource, Cause, FixStatus, IncidentStatus, RiskClass,
    SupportFixPolicy, SupportFixRun, SupportIncident, SupportIssueSignature,
    TicketCategory,
)
from app.services import (
    support_brief, support_diagnostics, support_incidents, support_knowledge,
    support_remediation, support_tickets,
)
from app.services.auth_service import hash_password


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def brand_a(db_session):
    row = Platform(name="EvoSys Pro", slug="evosyspro", is_active=True)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def brand_b(db_session):
    row = Platform(name="BookaBoost", slug="bookaboost", is_active=True)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def org_a(db_session, sample_org, brand_a):
    sample_org.platform_id = brand_a.id
    sample_org.billing_plan_key = "growth"
    db_session.commit()
    return sample_org


@pytest.fixture()
def org_b(db_session, brand_b):
    org = Organization(name="Second Customer", slug="second-customer",
                       plan="standard", platform_id=brand_b.id,
                       billing_plan_key="starter")
    db_session.add(org)
    db_session.commit()
    return org


@pytest.fixture()
def user_b(db_session, org_b):
    user = User(organization_id=org_b.id, email="user@second.test",
                password_hash=hash_password("TestPass123!"),
                full_name="Second Advisor", role="advisor",
                must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture()
def god(db_session):
    user = User(organization_id=None, email="owner@advisorflow.test",
                password_hash=hash_password("GodPass123!"), full_name="Owner",
                role="god_admin", must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return user


def _ticket(db, org, user, **kwargs):
    kwargs.setdefault("subject", "Calendar is broken")
    kwargs.setdefault("body", "Nothing syncs.")
    ticket = support_tickets.create_ticket(db, org=org, user=user, **kwargs)
    db.commit()
    return ticket


def _stuck_calendar(db, user, *, failures=5, connected=True):
    conn = CalendarConnection(user_id=user.id, provider="microsoft",
                              is_connected=connected, calendar_scope_ok=True,
                              failure_count=failures,
                              last_error="AADSTS700082 token expired",
                              last_error_at=datetime.utcnow(),
                              busy_fetched_at=datetime.utcnow())
    db.add(conn)
    db.commit()
    return conn


# ══════════════════════════════════════════════════════════════════════════
# THE REGISTRY
# ══════════════════════════════════════════════════════════════════════════

def test_every_registered_repair_is_completely_specified():
    """A repair with no explanation is a button a support engineer cannot
    honestly press, and a customer cannot be told what happened to them."""
    assert support_remediation.REGISTRY, "the registry is not empty"
    for key, remediation in support_remediation.REGISTRY.items():
        assert remediation.risk_class in RiskClass.ALL, key
        assert remediation.customer_explanation.strip(), key
        assert remediation.technical_explanation.strip(), key
        assert remediation.description.strip(), key
        assert callable(remediation.validate), key
        assert callable(remediation.snapshot), key
        assert callable(remediation.verify), key


def test_the_registry_is_the_whole_menu():
    """"No dead buttons", made checkable: every action a screen can offer has
    to be a registry entry, because there is nothing else to call."""
    listed = {r["action_key"] for r in support_remediation.list_registry()}
    assert listed == set(support_remediation.REGISTRY)
    for entry in support_remediation.list_registry():
        if entry["risk_class"] == RiskClass.ENGINEERING:
            assert entry["executable"] is False
        else:
            assert entry["executable"] is True


# ══════════════════════════════════════════════════════════════════════════
# SAFE AUTOMATIC REPAIRS
# ══════════════════════════════════════════════════════════════════════════

def test_a_safe_repair_executes_verifies_and_records_both_states(db_session,
                                                                 org_a,
                                                                 sample_advisor):
    ticket = _ticket(db_session, org_a, sample_advisor)
    # Corrupt the cached SLA state so there is something real to repair.
    ticket.sla_state = "breached"
    db_session.commit()

    ctx = support_remediation.FixContext(db_session, org=org_a, ticket=ticket)
    run = support_remediation.execute_fix(
        db_session, "sla.recompute_ticket_sla", ctx, requested_by_kind="ai",
        detection_source="test")
    db_session.commit()

    assert run.status == FixStatus.FIXED
    assert run.verified is True
    assert run.authorization_source == AuthorizationSource.POLICY_AUTO
    assert json.loads(run.before_state_json)["sla_state"] == "breached"
    assert json.loads(run.after_state_json)["sla_state"] != "breached"
    assert run.duration_ms is not None


def test_an_execution_writes_the_platforms_own_audit_row(db_session, org_a,
                                                         sample_advisor):
    """Not a second audit system — the ledger an administrator already reads."""
    ticket = _ticket(db_session, org_a, sample_advisor)
    ticket.sla_state = "breached"
    ctx = support_remediation.FixContext(db_session, org=org_a, ticket=ticket,
                                          actor=sample_advisor)
    support_remediation.execute_fix(db_session, "sla.recompute_ticket_sla", ctx)
    db_session.commit()

    rows = (db_session.query(AuditLogEntry)
            .filter(AuditLogEntry.action == "support.fix_applied").all())
    assert rows, "an applied repair is auditable"
    assert rows[0].target_type == "support_fix_run"
    assert rows[0].before_state and rows[0].after_state


def test_a_repair_with_nothing_to_do_says_so_instead_of_running(db_session,
                                                                org_a,
                                                                sample_advisor):
    """"There is nothing to fix" is a better answer than "you may not fix
    nothing" — which is why validation runs before authority."""
    ticket = _ticket(db_session, org_a, sample_advisor)
    ctx = support_remediation.FixContext(db_session, org=org_a, ticket=ticket)
    run = support_remediation.execute_fix(
        db_session, "ticket.resume_stalled_sla_clock", ctx)
    db_session.commit()
    assert run.status == FixStatus.RECOMMENDED
    assert run.verified is False
    assert "not paused" in run.verification_message


def test_a_repair_that_cannot_be_verified_is_not_reported_as_fixed(
        db_session, org_a, sample_advisor, monkeypatch):
    """THE WHOLE POINT OF A SEPARATE `verified` COLUMN.

    The execution is left working and only the verifier is made to fail, which
    is exactly the dangerous case: something changed, and we cannot show that
    it helped.
    """
    ticket = _ticket(db_session, org_a, sample_advisor)
    ticket.sla_state = "breached"
    remediation = support_remediation.REGISTRY["sla.recompute_ticket_sla"]
    monkeypatch.setattr(remediation, "verify",
                        lambda ctx: (False, "The condition is still present.", {}))

    ctx = support_remediation.FixContext(db_session, org=org_a, ticket=ticket)
    run = support_remediation.execute_fix(db_session, "sla.recompute_ticket_sla",
                                          ctx)
    db_session.commit()

    assert run.status == FixStatus.FAILED
    assert run.verified is False
    summary = support_remediation.run_summary(run)
    assert summary["fixed"] is False


def test_a_verifier_that_raises_is_a_failure_not_a_success(db_session, org_a,
                                                           sample_advisor,
                                                           monkeypatch):
    ticket = _ticket(db_session, org_a, sample_advisor)
    ticket.sla_state = "breached"

    def _boom(ctx):
        raise RuntimeError("verification exploded")

    monkeypatch.setattr(support_remediation.REGISTRY["sla.recompute_ticket_sla"],
                        "verify", _boom)
    ctx = support_remediation.FixContext(db_session, org=org_a, ticket=ticket)
    run = support_remediation.execute_fix(db_session, "sla.recompute_ticket_sla",
                                          ctx)
    db_session.commit()
    assert run.status == FixStatus.FAILED
    assert run.verified is False
    assert "verification exploded" in (run.error_summary or "")


# ══════════════════════════════════════════════════════════════════════════
# CONTROLLED REPAIRS
# ══════════════════════════════════════════════════════════════════════════

def test_a_controlled_repair_will_not_run_without_a_policy(db_session, org_a,
                                                           sample_advisor):
    """Default is OFF, everywhere, always."""
    _stuck_calendar(db_session, sample_advisor)
    ctx = support_remediation.FixContext(db_session, org=org_a)
    run = support_remediation.execute_fix(
        db_session, "calendar.mark_reauthorization_required", ctx,
        requested_by_kind="ai")
    db_session.commit()

    assert run.status == FixStatus.APPROVAL_REQUIRED
    assert run.verified is False
    conn = db_session.query(CalendarConnection).first()
    assert conn.is_connected is True, "nothing was changed"


def test_a_controlled_repair_runs_where_god_enabled_it(db_session, org_a,
                                                       sample_advisor, brand_a):
    _stuck_calendar(db_session, sample_advisor)
    db_session.add(SupportFixPolicy(
        action_key="calendar.mark_reauthorization_required",
        platform_id=brand_a.id, auto_execute=True, is_active=True))
    db_session.commit()

    ctx = support_remediation.FixContext(db_session, org=org_a)
    run = support_remediation.execute_fix(
        db_session, "calendar.mark_reauthorization_required", ctx,
        requested_by_kind="ai")
    db_session.commit()

    assert run.status == FixStatus.FIXED
    assert run.verified is True
    assert run.authorization_source == AuthorizationSource.POLICY_CONTROLLED
    conn = db_session.query(CalendarConnection).first()
    assert conn.is_connected is False
    assert "Reconnect" in (conn.last_error or "")


def test_a_policy_rate_limit_is_enforced(db_session, org_a, sample_advisor,
                                         brand_a):
    _stuck_calendar(db_session, sample_advisor)
    db_session.add(SupportFixPolicy(
        action_key="calendar.mark_reauthorization_required",
        platform_id=brand_a.id, auto_execute=True, max_runs_per_day=1,
        is_active=True))
    db_session.commit()

    ctx = support_remediation.FixContext(db_session, org=org_a)
    first = support_remediation.execute_fix(
        db_session, "calendar.mark_reauthorization_required", ctx)
    db_session.commit()
    assert first.verified is True

    # Put the connection back into the stuck state so validation passes again
    # and the RATE LIMIT is what stops the second run, not the validator.
    conn = db_session.query(CalendarConnection).first()
    conn.is_connected = True
    db_session.commit()

    second = support_remediation.execute_fix(
        db_session, "calendar.mark_reauthorization_required",
        support_remediation.FixContext(db_session, org=org_a))
    db_session.commit()
    assert second.status == FixStatus.APPROVAL_REQUIRED
    assert "maximum number of times today" in second.verification_message


def test_a_controlled_repair_refuses_when_nothing_is_stuck(db_session, org_a,
                                                           sample_advisor,
                                                           brand_a):
    _stuck_calendar(db_session, sample_advisor, failures=0)
    db_session.add(SupportFixPolicy(
        action_key="calendar.mark_reauthorization_required",
        platform_id=brand_a.id, auto_execute=True, is_active=True))
    db_session.commit()

    run = support_remediation.execute_fix(
        db_session, "calendar.mark_reauthorization_required",
        support_remediation.FixContext(db_session, org=org_a))
    db_session.commit()
    assert run.status == FixStatus.RECOMMENDED
    assert db_session.query(CalendarConnection).first().is_connected is True


def test_reseeding_tiers_refuses_to_overwrite_a_real_configuration(db_session,
                                                                   org_a):
    """The guard that makes this safe to register at all. `sample_org` is a
    provisioned organization and already has its tier definitions."""
    run = support_remediation.execute_fix(
        db_session, "org.reseed_tier_definitions",
        support_remediation.FixContext(db_session, org=org_a,
                                        is_god=True),
        requested_by_kind="god")
    db_session.commit()
    assert run.status == FixStatus.RECOMMENDED
    assert "already has" in run.verification_message


def test_reseeding_tiers_fills_a_genuine_void(db_session, org_b, god):
    """org_b was created directly and was never provisioned."""
    run = support_remediation.execute_fix(
        db_session, "org.reseed_tier_definitions",
        support_remediation.FixContext(db_session, org=org_b, actor=god,
                                        is_god=True),
        requested_by_kind="god")
    db_session.commit()
    assert run.status == FixStatus.FIXED
    assert run.verified is True
    assert json.loads(run.verification_result_json)["tier_definition_count"] > 0


# ══════════════════════════════════════════════════════════════════════════
# APPROVAL AND ENGINEERING CLASSES
# ══════════════════════════════════════════════════════════════════════════

def test_an_approval_class_repair_is_prepared_not_run(db_session, org_a,
                                                      sample_advisor):
    org_a.stripe_subscription_id = "sub_test_123"
    ticket = _ticket(db_session, org_a, sample_advisor)
    ctx = support_remediation.FixContext(db_session, org=org_a, ticket=ticket)
    run = support_remediation.propose_fix(
        db_session, "billing.resync_subscription_mirror", ctx,
        detection_source="ask_ai")
    db_session.commit()

    assert run.status == FixStatus.APPROVAL_REQUIRED
    assert run.verified is False
    # The evidence is captured at proposal time, so the approver sees what was
    # true when it was diagnosed.
    assert run.before_state_json is not None


def test_only_god_can_approve(db_session, org_a, sample_advisor):
    """Authority is checked in the registry, independently of any route."""
    org_a.stripe_subscription_id = "sub_test_123"
    remediation = support_remediation.REGISTRY["billing.resync_subscription_mirror"]

    as_customer = support_remediation.authorization_for(
        db_session, remediation,
        support_remediation.FixContext(db_session, org=org_a, is_god=False),
        requested_by_kind="customer")
    assert as_customer.allowed is False
    assert as_customer.stage == "approval_required"

    as_god = support_remediation.authorization_for(
        db_session, remediation,
        support_remediation.FixContext(db_session, org=org_a, is_god=True),
        requested_by_kind="god")
    assert as_god.allowed is True
    assert as_god.source == AuthorizationSource.GOD_APPROVAL


def test_rejecting_a_proposal_records_who_and_why(db_session, org_a, god):
    org_a.stripe_subscription_id = "sub_test_123"
    ctx = support_remediation.FixContext(db_session, org=org_a)
    run = support_remediation.propose_fix(
        db_session, "billing.resync_subscription_mirror", ctx)
    db_session.commit()

    support_remediation.reject_proposal(db_session, run, god_user=god,
                                        note="Stripe is already correct.")
    db_session.commit()
    assert run.status == FixStatus.ESCALATED
    assert run.authorized_by == god.id
    assert run.approval_note == "Stripe is already correct."


def test_an_engineering_repair_can_never_execute(db_session, org_a, god):
    """The platform does not modify or deploy its own application code."""
    run = support_remediation.execute_fix(
        db_session, "platform.engineering_fix_required",
        support_remediation.FixContext(db_session, org=org_a, actor=god,
                                        is_god=True),
        requested_by_kind="god")
    db_session.commit()
    assert run.status == FixStatus.RECOMMENDED
    assert run.verified is False

    with pytest.raises(support_remediation.FixRefused):
        support_remediation.approve_and_execute(
            db_session, run, god_user=god, org=org_a)


def test_an_unknown_repair_is_refused_loudly(db_session, org_a):
    with pytest.raises(support_remediation.FixRefused):
        support_remediation.execute_fix(
            db_session, "rm_minus_rf",
            support_remediation.FixContext(db_session, org=org_a))


# ══════════════════════════════════════════════════════════════════════════
# CORRELATION
# ══════════════════════════════════════════════════════════════════════════

SIGNATURE = "calendar.provider_refusing_connection"


def test_one_customer_alone_is_not_an_incident(db_session, org_a,
                                               sample_advisor):
    _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    result = support_incidents.correlate(db_session)
    db_session.commit()
    assert result["incidents_opened"] == []
    assert db_session.query(SupportIncident).count() == 0


def test_the_same_fault_at_two_customers_becomes_one_incident(db_session, org_a,
                                                              sample_advisor,
                                                              org_b, user_b):
    """Five tickets, one investigation. This is the whole point of the layer."""
    _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    _ticket(db_session, org_b, user_b, signature=SIGNATURE)
    result = support_incidents.correlate(db_session)
    db_session.commit()

    assert len(result["incidents_opened"]) == 1
    incident = db_session.query(SupportIncident).first()
    assert incident.incident_number.startswith("INC-")
    assert incident.organizations_affected == 2
    assert incident.platforms_affected == 2


def test_the_same_fault_at_two_brands_is_classified_as_ours(db_session, org_a,
                                                            sample_advisor,
                                                            org_b, user_b):
    """Two independent companies do not misconfigure the same thing in the
    same hour. A shared dependency does."""
    _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    _ticket(db_session, org_b, user_b, signature=SIGNATURE)
    support_incidents.correlate(db_session)
    db_session.commit()

    incident = db_session.query(SupportIncident).first()
    assert incident.classification == Cause.PLATFORM_DEFECT
    assert incident.likely_root_cause, "a root cause worth reading"
    assert incident.recommended_remediation, "a concrete next step"
    assert incident.suggested_validation, "how to check the fix"
    assert incident.risk_assessment, "what the fix might break"


def test_correlating_twice_updates_rather_than_duplicates(db_session, org_a,
                                                          sample_advisor,
                                                          org_b, user_b):
    _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    _ticket(db_session, org_b, user_b, signature=SIGNATURE)
    support_incidents.correlate(db_session)
    second = support_incidents.correlate(db_session)
    db_session.commit()
    assert second["incidents_opened"] == []
    assert len(second["incidents_updated"]) == 1
    assert db_session.query(SupportIncident).count() == 1


def test_tickets_are_tied_to_the_incident_they_belong_to(db_session, org_a,
                                                         sample_advisor, org_b,
                                                         user_b):
    first = _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    support_incidents.correlate(db_session)
    _ticket(db_session, org_b, user_b, signature=SIGNATURE)
    support_incidents.correlate(db_session)
    db_session.commit()
    db_session.refresh(first)
    assert first.incident_id is not None


def test_a_suspected_incident_says_nothing_to_customers(db_session, org_a,
                                                        sample_advisor, org_b,
                                                        user_b):
    """Telling a customer we MIGHT have a problem, before anyone confirmed it,
    is worse than silence and is not reversible."""
    _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    _ticket(db_session, org_b, user_b, signature=SIGNATURE)
    support_incidents.correlate(db_session)
    db_session.commit()

    assert support_incidents.customer_statement(db_session, org=org_a,
                                                services=["calendar"]) is None


def test_an_acknowledged_incident_says_only_what_is_safe(db_session, org_a,
                                                         sample_advisor, org_b,
                                                         user_b, god):
    _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    _ticket(db_session, org_b, user_b, signature=SIGNATURE)
    support_incidents.correlate(db_session)
    incident = db_session.query(SupportIncident).first()
    support_incidents.acknowledge(db_session, incident, user=god)
    db_session.commit()

    statement = support_incidents.customer_statement(db_session, org=org_a,
                                                     services=["calendar"])
    assert statement
    lowered = statement.lower()
    # No counts, no other brand, no admission that other customers exist.
    for forbidden in ("bookaboost", "evosys", "customers", "organizations",
                      "2", "advisorflow"):
        assert forbidden not in lowered


def test_signatures_are_low_cardinality_or_absent():
    """A fingerprint derived from free text would be unique per customer, and
    a count that is always one is worse than no grouping at all."""
    assert support_incidents.signature_for("calendar",
                                           ["calendar.provider_refusing_connection"]) \
        == "calendar.provider_refusing_connection"
    assert support_incidents.signature_for(None, None, None) is None
    assert support_incidents.signature_for("calendar", None, Cause.UNKNOWN) is None
    assert support_incidents.signature_for("calendar", None,
                                           Cause.THIRD_PARTY_PROVIDER) == \
        "calendar.third_party_provider"


def test_recurrence_is_counted_by_customer_not_by_occurrence(db_session, org_a,
                                                             sample_advisor,
                                                             org_b, user_b):
    _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    _ticket(db_session, org_b, user_b, signature=SIGNATURE)
    db_session.commit()

    row = (db_session.query(SupportIssueSignature)
           .filter(SupportIssueSignature.signature == SIGNATURE).first())
    assert row.occurrence_count == 3
    assert row.organizations_affected == 2, "'how many customers', not 'how many times'"


def test_the_recurring_report_says_what_the_numbers_mean(db_session, org_a,
                                                         sample_advisor):
    for _ in range(3):
        _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    db_session.commit()

    report = support_incidents.recurring_report(db_session, days=7)
    assert report["signatures"]
    entry = report["signatures"][0]
    assert "Seen 3 time(s)" in entry["narrative"]


def test_a_working_repair_on_a_returning_problem_becomes_an_engineering_candidate(
        db_session, org_a, sample_advisor):
    """A fix that keeps working on a problem that keeps coming back is
    treating a symptom, and that is exactly when it stops being a win."""
    row = SupportIssueSignature(signature=SIGNATURE, title="Calendar refused",
                                service="calendar", occurrence_count=25,
                                organizations_affected=4, platforms_affected=2,
                                auto_fixed_count=25,
                                first_seen_at=datetime.utcnow(),
                                last_seen_at=datetime.utcnow())
    db_session.add(row)
    db_session.commit()

    support_incidents.observe(db_session, signature=SIGNATURE,
                              service="calendar", org=org_a)
    db_session.commit()
    db_session.refresh(row)
    assert row.symptom_only_remediation is True
    assert row.engineering_candidate is True


def test_the_learning_loop_proposes_and_never_publishes(db_session, org_a,
                                                        sample_advisor):
    for _ in range(4):
        _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    db_session.commit()

    result = support_knowledge.scan_for_candidates(db_session, days=7)
    db_session.commit()
    assert result["candidates"], "a recurring problem raises a proposal"

    candidates = support_knowledge.open_candidates(db_session)
    assert all(c.status == "proposed" for c in candidates)
    # Nothing was published as a side effect of noticing.
    assert support_knowledge.list_articles(db_session, platform_id=None) == []


# ══════════════════════════════════════════════════════════════════════════
# THE DAILY BRIEF
# ══════════════════════════════════════════════════════════════════════════

def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def test_a_quiet_day_reports_no_success_rate_rather_than_a_perfect_one(
        db_session):
    """A rate computed from zero attempts is not 100%."""
    brief = support_brief.generate(db_session, day=_today())
    db_session.commit()
    view = support_brief.brief_view(brief)
    assert view["auto_fix_success_rate"] is None
    assert view["detected"] == 0


def test_the_first_brief_has_no_trend(db_session):
    """A baseline of zero would render every first day as infinite improvement."""
    brief = support_brief.generate(db_session, day=_today())
    db_session.commit()
    view = support_brief.brief_view(brief)
    assert view["trend"] is None
    assert view["has_baseline"] is False


def test_a_second_brief_compares_against_the_previous_one(db_session, org_a,
                                                          sample_advisor):
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    support_brief.generate(db_session, day=yesterday)
    _ticket(db_session, org_a, sample_advisor)
    brief = support_brief.generate(db_session, day=_today())
    db_session.commit()

    view = support_brief.brief_view(brief)
    assert view["has_baseline"] is True
    assert view["trend"]["compared_to"] == yesterday
    assert view["trend"]["deltas"]["tickets_opened"]["change"] == 1


def test_the_brief_separates_auto_fixed_from_merely_attempted(db_session, org_a,
                                                              sample_advisor,
                                                              monkeypatch):
    """"Fixed" is `verified`. A repair that ran and was not checked counts as
    a failure here, deliberately."""
    ticket = _ticket(db_session, org_a, sample_advisor)
    ticket.sla_state = "breached"
    ctx = support_remediation.FixContext(db_session, org=org_a, ticket=ticket)
    support_remediation.execute_fix(db_session, "sla.recompute_ticket_sla", ctx)

    other = _ticket(db_session, org_a, sample_advisor, subject="Second")
    other.sla_state = "breached"
    monkeypatch.setattr(support_remediation.REGISTRY["sla.recompute_ticket_sla"],
                        "verify", lambda ctx: (False, "Still wrong.", {}))
    support_remediation.execute_fix(
        db_session, "sla.recompute_ticket_sla",
        support_remediation.FixContext(db_session, org=org_a, ticket=other))
    db_session.commit()

    view = support_brief.brief_view(
        support_brief.generate(db_session, day=_today()))
    assert view["auto_fixed"] == 1
    assert view["auto_fix_failed"] == 1
    assert view["auto_fix_success_rate"] == 50


def test_a_prepared_repair_does_not_drag_the_success_rate_down(db_session,
                                                               org_a,
                                                               sample_advisor):
    """A proposal waiting for approval never attempted anything."""
    org_a.stripe_subscription_id = "sub_test_123"
    ticket = _ticket(db_session, org_a, sample_advisor)
    ticket.sla_state = "breached"
    support_remediation.execute_fix(
        db_session, "sla.recompute_ticket_sla",
        support_remediation.FixContext(db_session, org=org_a, ticket=ticket))
    support_remediation.propose_fix(
        db_session, "billing.resync_subscription_mirror",
        support_remediation.FixContext(db_session, org=org_a, ticket=ticket))
    db_session.commit()

    view = support_brief.brief_view(
        support_brief.generate(db_session, day=_today()))
    assert view["auto_fix_success_rate"] == 100
    assert view["awaiting_god_approval"] == 1


def test_the_brief_recommends_actions_not_homework(db_session, org_a,
                                                   sample_advisor, org_b,
                                                   user_b):
    _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    _ticket(db_session, org_b, user_b, signature=SIGNATURE)
    support_incidents.correlate(db_session)
    db_session.commit()

    view = support_brief.brief_view(
        support_brief.generate(db_session, day=_today()))
    assert view["multi_org_incidents"] == 1
    actions = view["recommended_actions"]
    assert actions, "an incident nobody has acknowledged is an action"
    assert any(a["kind"] == "incident" for a in actions)
    assert all(a.get("action") for a in actions)


def test_the_whole_nightly_pass_runs_in_order(db_session, org_a, sample_advisor,
                                              org_b, user_b):
    """Refresh, correlate, learn, THEN write — a brief generated first would
    be a document about yesterday's understanding of yesterday."""
    _ticket(db_session, org_a, sample_advisor, signature=SIGNATURE)
    _ticket(db_session, org_b, user_b, signature=SIGNATURE)
    result = support_brief.run_daily_intelligence(db_session, day=_today())

    assert "sla_refresh" in result
    assert result["correlation"]["incidents_opened"], \
        "correlation ran before the brief counted incidents"
    assert result["briefs"], "a platform brief plus one per brand"
    platform_brief = result["briefs"][0]
    assert platform_brief["multi_org_incidents"] == 1
