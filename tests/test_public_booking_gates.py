"""THREE SWITCHES, AND NONE OF THEM IMPLIES ANOTHER.

WHAT WENT WRONG AND WHY THIS FILE EXISTS.

The public booking feature first shipped with its three outbound paths - the
customer's booking confirmation, the internal notification to the booked sales
people, and the 24-hour/1-hour reminders - all running on STAFF_ESCALATION.
That variable already belonged to the AI conversation service's internal
escalation alerts.

Two separate problems, in opposite directions:

  * Turning on booking mail would have turned on an unrelated feature. An
    operator who wanted prospects to receive their confirmations would, in the
    same edit, have enabled escalation alerts nobody had asked about.

  * The three booking paths could not be separated from each other. "Let the
    sales team start getting notified about website bookings" and "start
    emailing prospects automatically" are different sentences with different
    blast radii, and a deployment has to be able to say one without the other.

`outbound_email_gate`'s own module comment already states the rule this broke:
one switch per source, no master switch, because two paths that share a
variable can only ever be turned on together. So this is the code being brought
back into line with its own stated design, not a new idea.

WHAT THESE TESTS HOLD. Every cell of the matrix: each switch on alone, and the
assertion that the other two paths stay shut. A regression here is not a
cosmetic one - it is an email reaching a customer because somebody enabled
something else.

NOTHING REACHES A PROVIDER. `send_email_via_provider` is patched in every test
that could plausibly get that far, and asserted never to have been called.
"""

import itertools
from datetime import datetime, timedelta

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER,
    ROLE_SALES_REP,
)
from app.models.scheduling_models import (
    AvailabilityWindow, MeetingType, SalesAppointment, AppointmentReminder,
    APPT_SCHEDULED, CONF_PENDING, LEADERSHIP_REPORTING_CHAIN, REMINDER_24H,
    REMINDER_FAILED, REMINDER_SENT,
)
from app.services import availability as av
from app.services import outbound_email_gate as gate
from app.services import public_booking as pb
from app.services import sales_appointment_reminders as reminders
from app.services import sales_booking_codes as codes
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)
TZ = "America/Chicago"

ENV_CONFIRMATION = "OUTBOUND_EMAIL_PUBLIC_BOOKING_CONFIRMATION"
ENV_INTERNAL     = "OUTBOUND_EMAIL_PUBLIC_BOOKING_INTERNAL"
ENV_REMINDERS    = "OUTBOUND_EMAIL_PUBLIC_BOOKING_REMINDERS"
ENV_STAFF        = "OUTBOUND_EMAIL_STAFF_ESCALATION"

ALL_BOOKING_ENV = (ENV_CONFIRMATION, ENV_INTERNAL, ENV_REMINDERS)


@pytest.fixture(autouse=True)
def _every_switch_off(monkeypatch):
    """Start every test from the shipped state: all four variables unset.

    Autouse and explicit rather than relying on the environment being clean,
    because a test that passes only because nobody exported a variable is a
    test that will one day pass for the wrong reason.
    """
    for name in ALL_BOOKING_ENV + (ENV_STAFF,):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def no_provider(monkeypatch):
    """Patch the sender and assert it is never reached. Returns the call list."""
    calls = []
    import app.services.email_service as es

    def _explode(*a, **k):
        calls.append((a, k))
        return {"success": True, "provider_message_id": "test", "error": None}

    monkeypatch.setattr(es, "send_email_via_provider", _explode)
    return calls


# ═══════════════════════════════════════════════════════════════════════════
# The switches themselves
# ═══════════════════════════════════════════════════════════════════════════

def test_all_three_default_off():
    """The shipped state, asserted directly."""
    assert gate.source_enabled(gate.PUBLIC_BOOKING_CONFIRMATION) is False
    assert gate.source_enabled(gate.PUBLIC_BOOKING_INTERNAL) is False
    assert gate.source_enabled(gate.PUBLIC_BOOKING_REMINDERS) is False


def test_each_source_maps_to_its_own_variable():
    assert gate._ENV_BY_SOURCE[gate.PUBLIC_BOOKING_CONFIRMATION] == ENV_CONFIRMATION
    assert gate._ENV_BY_SOURCE[gate.PUBLIC_BOOKING_INTERNAL] == ENV_INTERNAL
    assert gate._ENV_BY_SOURCE[gate.PUBLIC_BOOKING_REMINDERS] == ENV_REMINDERS


def test_no_two_gated_sources_share_a_variable():
    """The rule the module states about itself, asserted for every source.

    A shared variable is how the original defect happened, and it would not be
    visible in any behavioural test until somebody turned one on in production.
    """
    names = list(gate._ENV_BY_SOURCE.values())
    assert len(names) == len(set(names)), (
        "two gated sources share an environment variable: %s"
        % sorted(n for n in names if names.count(n) > 1))


@pytest.mark.parametrize("env,source", [
    (ENV_CONFIRMATION, gate.PUBLIC_BOOKING_CONFIRMATION),
    (ENV_INTERNAL,     gate.PUBLIC_BOOKING_INTERNAL),
    (ENV_REMINDERS,    gate.PUBLIC_BOOKING_REMINDERS),
])
def test_a_switch_enables_only_itself(monkeypatch, env, source):
    monkeypatch.setenv(env, "true")
    assert gate.source_enabled(source) is True
    for other in gate.PUBLIC_BOOKING_SOURCES:
        if other == source:
            continue
        assert gate.source_enabled(other) is False, (
            "%s also enabled %s" % (env, other))
    assert gate.source_enabled(gate.STAFF_ESCALATION) is False


def test_staff_escalation_no_longer_controls_any_booking_path(monkeypatch):
    """THE REGRESSION THIS FILE IS NAMED FOR.

    With the OLD coupling every one of these would have been True.
    """
    monkeypatch.setenv(ENV_STAFF, "true")
    assert gate.source_enabled(gate.STAFF_ESCALATION) is True
    assert gate.source_enabled(gate.PUBLIC_BOOKING_CONFIRMATION) is False
    assert gate.source_enabled(gate.PUBLIC_BOOKING_INTERNAL) is False
    assert gate.source_enabled(gate.PUBLIC_BOOKING_REMINDERS) is False


def test_booking_switches_do_not_enable_staff_escalation(monkeypatch):
    """And the other direction: enabling all three booking paths must not
    switch on the unrelated feature they used to share."""
    for name in ALL_BOOKING_ENV:
        monkeypatch.setenv(name, "true")
    assert gate.source_enabled(gate.STAFF_ESCALATION) is False


def test_no_booking_switch_touches_any_customer_outreach_source(monkeypatch):
    """Cadence, bulk AI and the rest are somebody else's decision entirely."""
    from app.services import send_source as _src
    for name in ALL_BOOKING_ENV:
        monkeypatch.setenv(name, "true")
    for other in (_src.BULK_AI, _src.VOICE_BOOKING_LINK,
                  _src.PIPELINE_AUTO_REPLY, _src.APPOINTMENT_FOLLOWUP):
        assert gate.source_enabled(other) is False


def test_the_refusal_names_the_variable_an_operator_must_set(monkeypatch):
    """A queue row saying "disabled" with no clue which switch is useless."""
    with pytest.raises(gate.EmailSendDisabled) as err:
        gate.gate_transactional_email("a@b.example", purpose="x",
                                      source=gate.PUBLIC_BOOKING_REMINDERS)
    assert ENV_REMINDERS in str(err.value)


def test_a_missing_recipient_is_a_value_error_not_a_disabled_error():
    """Two different problems. One is configuration; the other is data."""
    with pytest.raises(ValueError) as err:
        gate.gate_transactional_email(None, purpose="x",
                                      source=gate.PUBLIC_BOOKING_INTERNAL)
    assert not isinstance(err.value, gate.EmailSendDisabled)


def test_an_enabled_source_with_a_recipient_passes(monkeypatch):
    monkeypatch.setenv(ENV_INTERNAL, "true")
    assert gate.gate_transactional_email(
        "a@b.example", purpose="x",
        source=gate.PUBLIC_BOOKING_INTERNAL) is None


def test_gate_staff_email_still_behaves_exactly_as_before(monkeypatch):
    """It delegates now. Its callers and its messages must not have moved."""
    with pytest.raises(gate.EmailSendDisabled):
        gate.gate_staff_email("a@b.example", purpose="escalation")
    with pytest.raises(ValueError) as err:
        gate.gate_staff_email("", purpose="escalation")
    assert "No notification address on file" in str(err.value)
    monkeypatch.setenv(ENV_STAFF, "true")
    assert gate.gate_staff_email("a@b.example", purpose="escalation") is None


# ═══════════════════════════════════════════════════════════════════════════
# End to end: the switches govern the paths they claim to
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def site(db_session):
    n = next(_SEQ)
    platform = Platform(name="Acme Suite", slug="gate-plat-%d" % n)
    db_session.add(platform)
    db_session.commit()
    org = Organization(name="Acme Intake", slug="gate-intake-%d" % n,
                       platform_id=platform.id, industry="general")
    db_session.add(org)
    db_session.commit()
    platform.public_intake_organization_id = org.id
    db_session.commit()
    bso = BrandSalesOrg(platform_id=platform.id, name="Acme Sales",
                        slug="gate-bso-%d" % n, timezone=TZ)
    db_session.add(bso)
    db_session.commit()

    def _u(name):
        u = User(organization_id=None, email="gate%d@t.live" % next(_SEQ),
                 password_hash=hash_password("x"), full_name=name,
                 role="advisor", must_change_password=False)
        db_session.add(u)
        db_session.commit()
        return u

    def _seat(u, role, reports_to=None):
        m = Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                       scope_id=bso.id, role=role, is_active=True,
                       reports_to_user_id=(reports_to.id if reports_to else None))
        db_session.add(m)
        db_session.commit()
        return m

    def _prof(u):
        p = av.get_or_create_profile(db_session, u, default_timezone=TZ)
        p.timezone = TZ
        p.min_notice_minutes = 0
        p.buffer_before_minutes = p.buffer_after_minutes = 0
        p.booking_horizon_days = 365
        db_session.query(AvailabilityWindow).filter(
            AvailabilityWindow.profile_id == p.id).delete(synchronize_session=False)
        for dow in range(5):
            db_session.add(AvailabilityWindow(profile_id=p.id, day_of_week=dow,
                                              start_minute=9 * 60,
                                              end_minute=17 * 60))
        db_session.commit()

    senior, direct, rep = _u("Senior"), _u("Direct"), _u("Rep")
    _seat(senior, ROLE_SALES_MANAGER)
    _seat(direct, ROLE_SALES_MANAGER, senior)
    rep_seat = _seat(rep, ROLE_SALES_REP, direct)
    for u in (senior, direct, rep):
        _prof(u)

    # requires_video False so the confirmation is not held back waiting for a
    # join link — this file is about GATES, and a test that never reaches the
    # gate proves nothing about it.
    mt = MeetingType(brand_sales_org_id=bso.id, key="discovery_demo",
                     name="Discovery + Demo", duration_minutes=60,
                     requires_video=False, is_active=True, public_bookable=True,
                     leadership_policy=LEADERSHIP_REPORTING_CHAIN,
                     owner_required=True, leadership_minimum=1,
                     leadership_depth=2, include_additional_leaders=True)
    db_session.add(mt)
    db_session.commit()
    codes.issue_code(db_session, rep_seat)
    db_session.commit()
    return {"platform": platform, "org": org, "bso": bso, "mt": mt, "rep": rep}


FORM = {"full_name": "Dana Prospect", "company": "Prospect Co",
        "email": "dana@prospect.example", "phone": "+12145550199",
        "industry": "Home services", "primary_challenge": "lead_followup",
        "primary_challenge_label": "Following up with leads consistently",
        "prospect_timezone": "America/New_York"}


def _book(db, site, key):
    day = (datetime.utcnow() + timedelta(days=7)).date()
    while day.weekday() != 0:
        day += timedelta(days=1)
    found = pb.public_slots(db, site["bso"], site["mt"], site["rep"],
                            av.local_to_utc(day, 0, TZ),
                            av.local_to_utc(day, 23 * 60, TZ))
    return pb.book(db, platform=site["platform"], bso=site["bso"],
                   intake_org=site["org"], meeting_type=site["mt"],
                   owner=site["rep"], owner_source=codes.OWNER_FROM_CODE,
                   starts_at=found["slots"][0]["starts_at"], form=dict(FORM),
                   idempotency_key=key)


def test_with_everything_off_nothing_is_sent_and_both_say_so(
        db_session, site, no_provider):
    result = _book(db_session, site, "all-off")
    assert result.ok
    assert result.artifacts["confirmation"]["status"] == "delivery_disabled"
    assert result.artifacts["notification"]["email"]["status"] == "delivery_disabled"
    assert no_provider == []


def test_staff_escalation_alone_sends_nothing(db_session, site, no_provider,
                                              monkeypatch):
    """THE POINT OF THE WHOLE CHANGE. Under the old coupling this single
    variable would have sent both the customer confirmation and the internal
    notification."""
    monkeypatch.setenv(ENV_STAFF, "true")
    result = _book(db_session, site, "staff-only")
    assert result.artifacts["confirmation"]["status"] == "delivery_disabled"
    assert result.artifacts["notification"]["email"]["status"] == "delivery_disabled"
    assert no_provider == [], (
        "OUTBOUND_EMAIL_STAFF_ESCALATION still reaches a booking send path")


def test_confirmation_switch_sends_the_confirmation_and_not_the_notification(
        db_session, site, no_provider, monkeypatch):
    monkeypatch.setenv(ENV_CONFIRMATION, "true")
    result = _book(db_session, site, "conf-only")
    assert result.artifacts["confirmation"]["sent"] is True
    assert result.artifacts["notification"]["email"]["status"] == "delivery_disabled"
    recipients = [k.get("to_email") for _a, k in no_provider]
    assert recipients == ["dana@prospect.example"], (
        "the confirmation switch sent to somebody other than the prospect: %s"
        % recipients)


def test_internal_switch_notifies_staff_and_leaves_the_customer_alone(
        db_session, site, no_provider, monkeypatch):
    monkeypatch.setenv(ENV_INTERNAL, "true")
    result = _book(db_session, site, "internal-only")
    assert result.artifacts["confirmation"]["status"] == "delivery_disabled"
    assert result.artifacts["notification"]["email"]["sent"] is True
    recipients = [k.get("to_email") for _a, k in no_provider]
    assert "dana@prospect.example" not in recipients, (
        "the INTERNAL switch emailed the customer")
    assert site["rep"].email in recipients


def test_reminders_switch_enables_neither_of_the_other_two(
        db_session, site, no_provider, monkeypatch):
    monkeypatch.setenv(ENV_REMINDERS, "true")
    result = _book(db_session, site, "reminders-only")
    assert result.artifacts["confirmation"]["status"] == "delivery_disabled"
    assert result.artifacts["notification"]["email"]["status"] == "delivery_disabled"
    assert no_provider == []


def test_a_reminder_is_refused_until_its_own_switch_is_on(
        db_session, site, no_provider):
    result = _book(db_session, site, "rem-off")
    row = db_session.query(AppointmentReminder).filter(
        AppointmentReminder.appointment_id == result.appointment.id,
        AppointmentReminder.kind == REMINDER_24H).first()
    report = reminders.process_due(
        db_session, now=row.scheduled_for + timedelta(minutes=5), send=True)
    db_session.refresh(row)
    assert report["sent"] == 0 and report["failed"] == 1
    assert row.status == REMINDER_FAILED
    assert ENV_REMINDERS in (row.detail or ""), (
        "the reminder row does not name the switch an operator must set")
    assert no_provider == []


def test_the_confirmation_switch_does_not_release_reminders(
        db_session, site, no_provider, monkeypatch):
    monkeypatch.setenv(ENV_CONFIRMATION, "true")
    result = _book(db_session, site, "conf-not-rem")
    no_provider.clear()
    row = db_session.query(AppointmentReminder).filter(
        AppointmentReminder.appointment_id == result.appointment.id,
        AppointmentReminder.kind == REMINDER_24H).first()
    reminders.process_due(db_session,
                          now=row.scheduled_for + timedelta(minutes=5), send=True)
    db_session.refresh(row)
    assert row.status == REMINDER_FAILED
    assert no_provider == []


def test_the_reminders_switch_releases_reminders(
        db_session, site, no_provider, monkeypatch):
    result = _book(db_session, site, "rem-on")
    monkeypatch.setenv(ENV_REMINDERS, "true")
    no_provider.clear()
    row = db_session.query(AppointmentReminder).filter(
        AppointmentReminder.appointment_id == result.appointment.id,
        AppointmentReminder.kind == REMINDER_24H).first()
    report = reminders.process_due(
        db_session, now=row.scheduled_for + timedelta(minutes=5), send=True)
    db_session.refresh(row)
    assert report["sent"] == 1
    assert row.status == REMINDER_SENT
    assert [k.get("to_email") for _a, k in no_provider] == ["dana@prospect.example"]


def test_the_ics_invitation_follows_the_internal_switch(
        db_session, site, no_provider, monkeypatch):
    """An emailed .ics goes to the booked staff, so it is an internal
    notification and must not be released by the customer-facing switch."""
    monkeypatch.setenv(ENV_CONFIRMATION, "true")
    result = _book(db_session, site, "ics-conf")
    assert result.artifacts["calendar"].get("email_invites") == "delivery_disabled"

    monkeypatch.setenv(ENV_INTERNAL, "true")
    second = _book(db_session, site, "ics-internal")
    assert second.artifacts["calendar"].get("email_invites") != "delivery_disabled"


# ═══════════════════════════════════════════════════════════════════════════
# The internal resend path must not have moved
# ═══════════════════════════════════════════════════════════════════════════

def test_the_internal_resend_keeps_the_gate_it_always_had(db_session, site,
                                                          no_provider, monkeypatch):
    """A salesperson pressing "resend confirmation" inside the product is a
    different act from the public flow sending one automatically, and it keeps
    STAFF_ESCALATION. Changing it would have been a behaviour change nobody
    asked for."""
    from app.services import demo_confirmation
    result = _book(db_session, site, "resend")
    appt = result.appointment
    no_provider.clear()

    with pytest.raises(gate.EmailSendDisabled):
        demo_confirmation.send(db_session, appt)
    assert no_provider == []

    monkeypatch.setenv(ENV_STAFF, "true")
    demo_confirmation.send(db_session, appt)
    assert [k.get("to_email") for _a, k in no_provider] == ["dana@prospect.example"]


def test_the_public_confirmation_switch_does_not_release_the_internal_resend(
        db_session, site, no_provider, monkeypatch):
    """And the reverse, so the two really are independent."""
    from app.services import demo_confirmation
    result = _book(db_session, site, "resend-2")
    no_provider.clear()
    monkeypatch.setenv(ENV_CONFIRMATION, "true")
    with pytest.raises(gate.EmailSendDisabled):
        demo_confirmation.send(db_session, result.appointment)
    assert no_provider == []
