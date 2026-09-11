"""Support Intelligence — entitlements, the SLA clock, and the ticket lifecycle.

WHAT THESE TESTS ARE PROTECTING
--------------------------------
Three things that are quiet when they break:

  * A RESPONSE TARGET THAT DRIFTS FROM WHAT WAS SOLD. The Support Plan page
    and the SLA engine must read the same entitlement, or we publish one
    promise and measure ourselves against another.

  * A CLOCK THAT MEASURES THE CALENDAR INSTEAD OF THE TEAM. A ticket raised
    at 16:45 on Friday must not be breached by Monday morning.

  * A CUSTOMER BEING CHARGED FOR OUR DEFECT. Technical product support must
    never consume the assistance minutes they paid for.

None of these throws an exception when it goes wrong. They just quietly bill
or breach.
"""

import json
from datetime import datetime, timedelta

import pytest

from app.models.models import Platform
from app.models.support_models import (
    Queue, Severity, SlaState, SupportBrandSettings, TicketCategory,
    TicketStatus,
)
from app.services import support_entitlements, support_sla, support_tickets
from app.services.auth_service import create_access_token


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def platform(db_session):
    row = Platform(name="EvoSys Pro", slug="evosyspro",
                   domain="app.evosyspro.live",
                   support_email="support@evosyspro.live", is_active=True)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def branded_org(db_session, sample_org, platform):
    """A customer of a brand, on a package. The normal case."""
    sample_org.platform_id = platform.id
    sample_org.billing_plan_key = "growth"
    db_session.commit()
    return sample_org


@pytest.fixture()
def support_headers(db_session, branded_org, sample_advisor):
    return {"Authorization": "Bearer %s"
                             % create_access_token(sample_advisor, db_session)}


def _hours(**overrides):
    cfg = dict(support_sla.DEFAULT_HOURS)
    cfg.update(overrides)
    return cfg


# ══════════════════════════════════════════════════════════════════════════
# ENTITLEMENTS
# ══════════════════════════════════════════════════════════════════════════

def test_package_entitlements_follow_the_initial_evosys_rules(db_session,
                                                              branded_org):
    """Starter, Growth and Professional differ in queue, targets and minutes."""
    branded_org.billing_plan_key = "starter"
    starter = support_entitlements.resolve(db_session, branded_org)
    branded_org.billing_plan_key = "growth"
    growth = support_entitlements.resolve(db_session, branded_org)
    branded_org.billing_plan_key = "professional"
    pro = support_entitlements.resolve(db_session, branded_org)

    assert starter["queue"] == Queue.STANDARD
    assert growth["queue"] == Queue.PRIORITY
    assert pro["queue"] == Queue.PRIORITY_PLUS

    assert starter["included_assistance_minutes"] == 0
    assert growth["included_assistance_minutes"] == 30
    assert pro["included_assistance_minutes"] == 60

    assert starter["first_response_minutes"][Severity.P2] == 480
    assert growth["first_response_minutes"][Severity.P2] == 240
    assert pro["first_response_minutes"][Severity.P2] == 120

    # ALL THREE get the same critical target. A Starter outage is an outage.
    assert (starter["first_response_minutes"][Severity.P1]
            == growth["first_response_minutes"][Severity.P1]
            == pro["first_response_minutes"][Severity.P1])


def test_a_question_carries_no_response_commitment(db_session, branded_org):
    """P4 has no target, so no screen can advertise one we never made."""
    entitlement = support_entitlements.resolve(db_session, branded_org)
    assert entitlement["first_response_minutes"][Severity.P4] is None
    assert support_sla.target_for(entitlement, Severity.P4) is None


def test_unconfigured_brand_falls_back_and_says_so(db_session, branded_org):
    """A brand nobody configured gets support, and is REPORTED as a default."""
    entitlement = support_entitlements.resolve(db_session, branded_org)
    assert entitlement["source"] == "default"
    assert entitlement["features"], "an unconfigured brand still gets support"


def test_config_row_overrides_field_by_field(db_session, branded_org, platform):
    """A row that sets one field keeps the package's other fields."""
    support_entitlements.upsert_config(
        db_session, platform_id=platform.id, plan_key="growth",
        values={"queue": Queue.PRIORITY_PLUS})
    db_session.commit()

    entitlement = support_entitlements.resolve(db_session, branded_org)
    assert entitlement["source"] == "config"
    assert entitlement["queue"] == Queue.PRIORITY_PLUS
    # Untouched by the edit, inherited from the package rule.
    assert entitlement["first_response_minutes"][Severity.P2] == 240
    assert entitlement["included_assistance_minutes"] == 30


def test_unknown_package_still_gets_support(db_session, branded_org):
    """A plan key nobody registered resolves to the Starter shape, not nothing."""
    branded_org.billing_plan_key = "some_bespoke_deal"
    entitlement = support_entitlements.resolve(db_session, branded_org)
    assert entitlement["queue"] == Queue.STANDARD
    assert "tickets" in entitlement["features"]


def test_pending_downgrade_does_not_take_away_todays_support(db_session,
                                                             branded_org):
    """A scheduled downgrade has not happened. They keep what they paid for."""
    branded_org.billing_plan_key = "professional"
    branded_org.billing_pending_plan_key = "starter"
    entitlement = support_entitlements.resolve(db_session, branded_org)
    assert entitlement["queue"] == Queue.PRIORITY_PLUS
    assert entitlement["included_assistance_minutes"] == 60


# ══════════════════════════════════════════════════════════════════════════
# BUSINESS HOURS
# ══════════════════════════════════════════════════════════════════════════

def test_friday_evening_ticket_is_not_breached_on_monday_morning():
    """The whole reason targets are in business minutes."""
    cfg = _hours()
    # Friday 2026-09-11, 16:45 America/Chicago == 21:45 UTC.
    raised = datetime(2026, 9, 11, 21, 45)
    due = support_sla.add_business_minutes(raised, 480, cfg)   # 1 business day
    assert due.weekday() == 0, "the target lands on Monday, not Saturday"
    assert support_sla.business_minutes_between(raised, due, cfg) == 480


def test_weekend_contributes_no_business_minutes():
    cfg = _hours()
    friday_evening = datetime(2026, 9, 11, 23, 0)      # after close
    monday_open = datetime(2026, 9, 14, 14, 0)         # 09:00 local
    assert support_sla.business_minutes_between(friday_evening, monday_open,
                                                cfg) == 0


def test_a_configured_holiday_is_not_a_working_day(db_session, platform):
    """A holiday the brand declared stops the clock like a weekend."""
    db_session.add(SupportBrandSettings(
        platform_id=platform.id, timezone="America/Chicago",
        business_days="0,1,2,3,4", business_start="09:00", business_end="17:00",
        holidays_json=json.dumps(["2026-09-14"])))
    db_session.commit()

    cfg = support_sla.hours_for(db_session, platform.id)
    assert cfg["source"] == "config"
    friday_evening = datetime(2026, 9, 11, 23, 0)
    tuesday_open = datetime(2026, 9, 15, 14, 0)
    assert support_sla.business_minutes_between(friday_evening, tuesday_open,
                                                cfg) == 0


def test_a_broken_window_falls_back_rather_than_closing_support(db_session,
                                                                platform):
    """business_end <= business_start would make every day zero minutes long,
    which reads as 'we are never open' and breaches everything at once."""
    db_session.add(SupportBrandSettings(platform_id=platform.id,
                                        business_start="17:00",
                                        business_end="09:00"))
    db_session.commit()
    cfg = support_sla.hours_for(db_session, platform.id)
    assert cfg["business_start_minute"] < cfg["business_end_minute"]


def test_emergency_queue_runs_on_wall_clock():
    """A confirmed outage does not wait for Monday."""
    cfg = _hours()
    assert support_sla.is_continuous(Queue.EMERGENCY, cfg) is True
    assert support_sla.is_continuous(Queue.STANDARD, cfg) is False

    saturday = datetime(2026, 9, 12, 3, 0)
    due = support_sla.add_business_minutes(saturday, 60, cfg, continuous=True)
    assert due == saturday + timedelta(minutes=60)


def test_humanized_targets_use_the_brands_own_working_day():
    """480 minutes is '1 business day' for an 8-hour brand, not always."""
    eight_hour = _hours()
    six_hour = _hours(business_start_minute=9 * 60, business_end_minute=15 * 60)
    assert support_sla._humanize(480, eight_hour) == "1 business day"
    assert support_sla._humanize(480, six_hour) != "1 business day"


# ══════════════════════════════════════════════════════════════════════════
# THE CLOCK ON A REAL TICKET
# ══════════════════════════════════════════════════════════════════════════

def _ticket(db, org, user, **kwargs):
    kwargs.setdefault("subject", "Calendar is not syncing")
    kwargs.setdefault("body", "Bookings are not reaching my calendar.")
    return support_tickets.create_ticket(db, org=org, user=user, **kwargs)


def test_a_new_ticket_gets_a_number_a_queue_and_a_target(db_session, branded_org,
                                                         sample_advisor):
    ticket = _ticket(db_session, branded_org, sample_advisor)
    db_session.commit()

    assert ticket.ticket_number.startswith("SUP-")
    assert ticket.queue == Queue.PRIORITY            # growth
    assert ticket.first_response_due_at is not None
    assert ticket.sla_state == SlaState.WITHIN
    # The commercial terms are SNAPSHOTTED, not looked up later.
    snapshot = json.loads(ticket.entitlement_json)
    assert snapshot["queue"] == Queue.PRIORITY
    assert snapshot["brand_display_name"] == "EvoSys Pro"


def test_only_a_non_internal_agent_message_stops_the_clock(db_session,
                                                           branded_org,
                                                           sample_advisor):
    ticket = _ticket(db_session, branded_org, sample_advisor)
    support_tickets.add_message(db_session, ticket, author_kind="agent",
                                user=sample_advisor, body="Looking into it.",
                                is_internal=True)
    assert ticket.first_response_at is None, "an internal note is not a response"

    support_tickets.add_message(db_session, ticket, author_kind="agent",
                                user=sample_advisor, body="Here's what we found.",
                                is_internal=False)
    assert ticket.first_response_at is not None
    db_session.commit()


def test_status_alone_cannot_fake_a_first_response(db_session, branded_org,
                                                   sample_advisor):
    """Moving a ticket to In Progress is not answering the customer."""
    ticket = _ticket(db_session, branded_org, sample_advisor)
    support_tickets.set_status(db_session, ticket, TicketStatus.IN_PROGRESS,
                               actor=sample_advisor)
    db_session.commit()
    assert ticket.first_response_at is None


def test_waiting_on_customer_pauses_and_a_reply_resumes(db_session, branded_org,
                                                        sample_advisor):
    """The clock stops when the ball is theirs, and the due date moves."""
    start = datetime(2026, 9, 14, 14, 0)              # Monday 09:00 local
    ticket = _ticket(db_session, branded_org, sample_advisor, now=start)
    original_due = ticket.first_response_due_at

    support_tickets.set_status(db_session, ticket,
                               TicketStatus.WAITING_ON_CUSTOMER,
                               actor=sample_advisor,
                               now=start + timedelta(hours=1))
    assert ticket.sla_paused_at is not None
    assert ticket.sla_state == SlaState.PAUSED
    assert ticket.sla_elapsed_minutes == 60

    support_tickets.add_message(db_session, ticket, author_kind="customer",
                                user=sample_advisor, body="Here's a screenshot.",
                                now=start + timedelta(hours=3))
    db_session.commit()

    assert ticket.sla_paused_at is None
    assert ticket.status == TicketStatus.IN_PROGRESS
    # Paused for two business hours, so the target moved out by two.
    assert ticket.first_response_due_at > original_due
    # The elapsed total is NOT reduced by the pause.
    assert ticket.sla_elapsed_minutes == 60


def test_pausing_after_the_first_response_does_nothing(db_session, branded_org,
                                                       sample_advisor):
    """There is no clock left to stop, and the bookkeeping must not pretend."""
    ticket = _ticket(db_session, branded_org, sample_advisor)
    support_tickets.add_message(db_session, ticket, author_kind="agent",
                                user=sample_advisor, body="Answered.",
                                is_internal=False)
    assert support_sla.pause(db_session, ticket) is False
    assert ticket.sla_paused_at is None


def test_a_missed_target_reads_as_breached(db_session, branded_org,
                                           sample_advisor):
    start = datetime(2026, 9, 14, 14, 0)
    ticket = _ticket(db_session, branded_org, sample_advisor, now=start)
    later = ticket.first_response_due_at + timedelta(hours=1)
    result = support_sla.evaluate(db_session, ticket, now=later)
    assert result["state"] == SlaState.BREACHED
    assert result["minutes_over"] is not None


def test_reclassifying_severity_recomputes_the_target(db_session, branded_org,
                                                      sample_advisor):
    """A ticket promoted to critical must not keep a normal ticket's due date."""
    ticket = _ticket(db_session, branded_org, sample_advisor)
    before = ticket.first_response_due_at
    support_tickets.reclassify(db_session, ticket, severity=Severity.P1,
                               actor=sample_advisor, reason="Service is down.")
    db_session.commit()
    assert ticket.severity == Severity.P1
    assert ticket.first_response_due_at < before


def test_reopening_starts_a_new_commitment(db_session, branded_org,
                                           sample_advisor):
    """The old promise was kept or broken already; reusing it means nothing."""
    ticket = _ticket(db_session, branded_org, sample_advisor)
    support_tickets.add_message(db_session, ticket, author_kind="agent",
                                user=sample_advisor, body="Fixed.",
                                is_internal=False)
    support_tickets.resolve_ticket(db_session, ticket, resolution="Reconnected.",
                                   actor=sample_advisor)
    db_session.commit()

    support_tickets.reopen(db_session, ticket, actor=sample_advisor,
                           reason="Still happening.")
    db_session.commit()
    assert ticket.first_response_at is None
    assert ticket.status == TicketStatus.NEW
    assert ticket.resolved_at is None


# ══════════════════════════════════════════════════════════════════════════
# THE COMMERCIAL RULE
# ══════════════════════════════════════════════════════════════════════════

def test_technical_product_support_never_touches_the_allowance():
    """OUR DEFECT IS NOT THEIR CONSULTING TIME. Neither included nor billable."""
    rules = support_entitlements.consumption_for(
        TicketCategory.TECHNICAL_PRODUCT_SUPPORT)
    assert rules == {"counts_against_allowance": False, "billable": False}


def test_assistance_draws_down_and_services_are_billable():
    assert support_entitlements.consumption_for(
        TicketCategory.CUSTOMER_ASSISTANCE)["counts_against_allowance"] is True
    assert support_entitlements.consumption_for(
        TicketCategory.PROFESSIONAL_SERVICES)["billable"] is True


def test_recorded_assistance_respects_the_category(db_session, branded_org,
                                                   sample_advisor):
    """The CALLER does not get to say who pays; the category does."""
    support_entitlements.record_assistance(
        db_session, org=branded_org,
        category=TicketCategory.TECHNICAL_PRODUCT_SUPPORT, minutes=45,
        requested_by=sample_advisor.id)
    support_entitlements.record_assistance(
        db_session, org=branded_org,
        category=TicketCategory.CUSTOMER_ASSISTANCE, minutes=20,
        requested_by=sample_advisor.id)
    db_session.commit()

    summary = support_entitlements.assistance_summary(db_session, branded_org)
    # 45 minutes of technical support are invisible to the allowance.
    assert summary["used_minutes"] == 20
    assert summary["remaining_minutes"] == 10


def test_allowance_does_not_roll_over(db_session, branded_org, sample_advisor):
    """Last period's unused minutes do not appear in this one."""
    now = datetime(2026, 9, 15, 12, 0)
    last_month = datetime(2026, 8, 15, 12, 0)

    entry = support_entitlements.record_assistance(
        db_session, org=branded_org, category=TicketCategory.CUSTOMER_ASSISTANCE,
        minutes=25, requested_by=sample_advisor.id, now=last_month)
    entry.delivered_at = last_month
    db_session.commit()

    summary = support_entitlements.assistance_summary(db_session, branded_org,
                                                      now=now)
    assert summary["used_minutes"] == 0
    assert summary["allowance_minutes"] == 30
    assert summary["rollover"] is False


def test_overage_is_reported_as_overage_not_as_a_negative_balance(
        db_session, branded_org, sample_advisor):
    support_entitlements.record_assistance(
        db_session, org=branded_org, category=TicketCategory.CUSTOMER_ASSISTANCE,
        minutes=50, requested_by=sample_advisor.id)
    db_session.commit()
    summary = support_entitlements.assistance_summary(db_session, branded_org)
    assert summary["remaining_minutes"] == 0
    assert summary["overage_minutes"] == 20


def test_billing_period_follows_the_subscription_not_the_calendar(db_session,
                                                                  branded_org):
    branded_org.billing_current_period_end = datetime(2026, 9, 20, 0, 0)
    period = support_entitlements.billing_period(branded_org,
                                                 now=datetime(2026, 9, 15))
    assert period["start"] == datetime(2026, 8, 20, 0, 0)
    assert period["end"] == datetime(2026, 9, 20, 0, 0)


# ══════════════════════════════════════════════════════════════════════════
# THE CUSTOMER'S OWN SCREENS
# ══════════════════════════════════════════════════════════════════════════

def test_support_plan_page_matches_the_engine(client, support_headers):
    """The page cannot advertise a target the clock does not honour."""
    response = client.get("/support/plan", headers=support_headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["plan"]["queue"] == Queue.PRIORITY
    targets = {row["severity"]: row for row in body["response_targets"]}
    assert targets[Severity.P2]["first_response_minutes"] == 240
    assert targets[Severity.P4]["first_response_minutes"] is None
    assert targets[Severity.P4]["first_response_text"] == \
        "No committed response target"
    assert body["assistance"]["allowance_minutes"] == 30
    assert "first-response targets" in body["commitment_note"]


def test_help_home_is_branded(client, support_headers):
    response = client.get("/support/me", headers=support_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assistant_name"] == "Ask EvoSys Pro"
    assert body["help_center_name"] == "EvoSys Pro Help Centre"
    assert "AdvisorFlow" not in json.dumps(body)


def test_a_brand_can_name_its_own_assistant(client, support_headers, db_session,
                                            platform):
    db_session.add(SupportBrandSettings(platform_id=platform.id,
                                        assistant_name="Ask Evo",
                                        help_center_name="EvoSys Pro Help"))
    db_session.commit()
    body = client.get("/support/me", headers=support_headers).json()
    assert body["assistant_name"] == "Ask Evo"
    assert body["help_center_name"] == "EvoSys Pro Help"


def test_ticket_can_be_raised_read_and_replied_to(client, support_headers):
    created = client.post("/support/tickets", headers=support_headers, json={
        "subject": "Text messages are failing",
        "body": "Nothing has sent since this morning.",
        "category": TicketCategory.TECHNICAL_PRODUCT_SUPPORT,
    })
    assert created.status_code == 201, created.text
    ticket = created.json()
    assert ticket["ticket_number"].startswith("SUP-")
    assert ticket["messages"], "the customer's own words are the first message"

    ticket_id = ticket["id"]
    read = client.get("/support/tickets/%s" % ticket_id, headers=support_headers)
    assert read.status_code == 200

    replied = client.post("/support/tickets/%s/reply" % ticket_id,
                          headers=support_headers,
                          json={"body": "Still failing."})
    assert replied.status_code == 200
    assert len(replied.json()["messages"]) == 2

    listed = client.get("/support/tickets", headers=support_headers).json()
    assert listed["count"] == 1


def test_customer_view_never_carries_an_internal_note(db_session, branded_org,
                                                      sample_advisor):
    """The one filter, asserted directly."""
    ticket = _ticket(db_session, branded_org, sample_advisor)
    support_tickets.add_message(db_session, ticket, author_kind="agent",
                                user=sample_advisor,
                                body="INTERNAL: customer is on the old plan.",
                                is_internal=True)
    db_session.commit()

    view = support_tickets.customer_view(db_session, ticket)
    assert "INTERNAL" not in json.dumps(view, default=str)
    agent = support_tickets.agent_view(db_session, ticket)
    assert any("INTERNAL" in note["body"] for note in agent["internal_notes"])


def test_a_second_report_of_the_same_problem_is_linked_not_duplicated(
        db_session, branded_org, sample_advisor):
    first = _ticket(db_session, branded_org, sample_advisor,
                    signature="calendar.provider_refusing_connection")
    second = _ticket(db_session, branded_org, sample_advisor,
                     signature="calendar.provider_refusing_connection")
    db_session.commit()
    assert second.related_ticket_id == first.id
    assert second.id != first.id, "the customer still gets their own number"
