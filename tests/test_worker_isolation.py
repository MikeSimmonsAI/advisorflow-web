"""Background passes: one bad record must not poison the pass, and a send must
never be repeated because its bookkeeping commit failed.

Each test drives the real pass function with its provider mocked (nothing here
reaches Twilio, Resend, Graph or SMTP) and:

  * makes the FIRST item fail - where possible in a way that leaves the shared
    SQLAlchemy session needing a rollback (a failed flush) - and proves the
    remaining items are still processed, and
  * for the claim-before-send fixes (review requests, AI touches, sales
    reminders), simulates a failure AFTER the provider accepted the send and
    proves a second pass does not send again.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError

from app.models.models import (BookingFollowup, BookingLink, EmailMessage, Lead,
                               Organization, PipelineConversation, Platform,
                               Reply, User)


def _poison(db):
    """Leave the session in the state a failed flush leaves it in."""
    db.add(Platform(name=None, slug="poison-%s" % datetime.utcnow().timestamp()))
    db.flush()


def _lead(db, org, advisor, n, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="Pat%d" % n, last_name="Family",
                email=kw.pop("email", "family%d@example.com" % n),
                phone="1214555700%d" % n, status=kw.pop("status", "new"), **kw)
    db.add(lead)
    db.commit()
    return lead


class _CommitFailsOnce:
    """Wraps session.commit so the next commit after `arm()` rolls back and
    raises, the way a lost connection at commit time does."""

    def __init__(self, db, monkeypatch):
        self.db = db
        self.armed = False
        self.real = db.commit
        monkeypatch.setattr(db, "commit", self)

    def arm(self):
        self.armed = True

    def __call__(self):
        if self.armed:
            self.armed = False
            self.db.rollback()
            raise OperationalError("COMMIT", {}, Exception("connection lost"))
        return self.real()


# ═══════════════════════════════════════════════════════════════════════════
# 1. review_request_cron
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def review_env(db_session, sample_org, sample_advisor, monkeypatch):
    from app.crons import review_request_cron as rc
    from app.services import public_identity, wholesale_sms
    from app.utils.crypto import encrypt_value
    import twilio.rest

    leads = [_lead(db_session, sample_org, sample_advisor, i) for i in (1, 2)]
    bookings = []
    for lead in leads:
        b = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                        booked_time=datetime.utcnow() - timedelta(hours=2))
        db_session.add(b)
        bookings.append(b)
    db_session.commit()
    token_enc = encrypt_value("tok")

    def eligible(db):
        # The production SELECT is Postgres-only (NOW() - INTERVAL); this is
        # the same eligibility, read from the same table.
        out = []
        for b in db.query(BookingLink).filter(
                BookingLink.review_request_sent_at.is_(None)).order_by(
                BookingLink.id).all():
            lead = db.query(Lead).filter(Lead.id == b.lead_id).one()
            out.append(SimpleNamespace(
                booking_id=b.id, lead_id=lead.id, user_id=b.user_id,
                first_name=lead.first_name, phone=lead.phone,
                organization_id=lead.organization_id, org_name="Org",
                twilio_account_sid="ACx", twilio_auth_token_encrypted=token_enc,
                twilio_phone_number="+12145551111",
                twilio_messaging_service_sid=None))
        return out

    sent = []
    env = SimpleNamespace(sent=sent, fail_send=set(), bookings=bookings,
                          leads=leads, on_send=None)

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = self

        def create(self, **kw):
            if kw["to"] in env.fail_send:
                raise RuntimeError("provider 500")
            sent.append(kw["to"])
            if env.on_send:
                env.on_send()

    monkeypatch.setattr(rc, "_eligible_rows", eligible)
    monkeypatch.setattr(twilio.rest, "Client", FakeClient)
    monkeypatch.setattr(wholesale_sms, "refusal_for_phone", lambda *a, **k: None)

    def survey_url(db, org_id, token):
        return "https://example.test/survey/%s" % token
    monkeypatch.setattr(public_identity, "survey_url", survey_url)
    env.rc = rc
    env.engine = db_session.get_bind()
    return env


def test_review_request_one_bad_row_does_not_stop_the_others(
        review_env, db_session, monkeypatch):
    from app.services import public_identity
    first = min(b.id for b in review_env.bookings)   # processed first

    def survey_url(db, org_id, token, _calls=[]):
        _calls.append(1)
        if len(_calls) == 1:
            _poison(db)
        return "https://example.test/survey/%s" % token
    monkeypatch.setattr(public_identity, "survey_url", survey_url)

    assert review_env.rc.run_review_request_cron(review_env.engine) == 1
    assert len(review_env.sent) == 1
    db_session.expire_all()
    failed = db_session.query(BookingLink).filter(BookingLink.id == first).one()
    assert failed.review_request_sent_at is None, "the failed row was not claimed"


def test_review_request_commit_failure_after_send_does_not_resend(
        review_env, db_session):
    from sqlalchemy.orm import Session

    real_commit = Session.commit
    state = {"armed": False}

    def commit(self):
        if state["armed"]:
            state["armed"] = False
            self.rollback()
            raise OperationalError("COMMIT", {}, Exception("connection lost"))
        return real_commit(self)

    review_env.on_send = lambda: state.update(armed=True)
    with patch.object(Session, "commit", commit):
        first = review_env.rc.run_review_request_cron(review_env.engine)
        second = review_env.rc.run_review_request_cron(review_env.engine)
    assert first == 2 and second == 0
    assert len(review_env.sent) == 2, "each family texted exactly once"
    db_session.expire_all()
    assert all(b.review_request_sent_at is not None
               for b in db_session.query(BookingLink).all())


def test_review_request_failed_send_stays_claimed_and_is_recorded(
        review_env, db_session):
    review_env.fail_send.add(review_env.leads[0].phone)
    assert review_env.rc.run_review_request_cron(review_env.engine) == 1
    assert review_env.rc.run_review_request_cron(review_env.engine) == 0
    assert review_env.sent == [review_env.leads[1].phone]
    db_session.expire_all()
    fu = db_session.query(BookingFollowup).filter(
        BookingFollowup.lead_id == review_env.leads[0].id).one()
    assert fu.survey_link_sent is False and "provider 500" in fu.error
    ok = db_session.query(BookingFollowup).filter(
        BookingFollowup.lead_id == review_env.leads[1].id).one()
    assert ok.survey_link_sent is True


# ═══════════════════════════════════════════════════════════════════════════
# 2. ai_conversation_service.process_scheduled_touches
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def ai_env(db_session, sample_org, sample_advisor, monkeypatch):
    from app.services import ai_conversation_service as acs
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("RESEND_API_KEY", "test-key-not-real")
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")
    convs = []
    for i in (1, 2):
        lead = _lead(db_session, sample_org, sample_advisor, i)
        conv = PipelineConversation(
            organization_id=sample_org.id, lead_id=lead.id,
            advisor_id=sample_advisor.id, stage="outreach_sent", paused=False,
            flagged=False, touch_number=1,
            started_at=datetime.utcnow() - timedelta(days=1),
            next_send_at=datetime.utcnow() - timedelta(minutes=10 - i))
        db_session.add(conv)
        db_session.commit()
        convs.append(conv)
    sent = []
    monkeypatch.setattr(acs, "_send_email_resend",
                        lambda db, adv, to, subj, html: sent.append(to))
    return SimpleNamespace(acs=acs, convs=convs, sent=sent)


def _touch_email(db, lead, advisor, touch_number, mode=None, actor=None):
    return {"subject": "Hello", "body": "Hi %s" % lead.first_name,
            "should_stop": False, "escalate": False, "source": "ai",
            "touch_number": touch_number}


def test_ai_touch_one_bad_conversation_does_not_stop_the_others(
        ai_env, db_session, sample_org):
    acs = ai_env.acs
    first_lead = ai_env.convs[0].lead_id

    def gen(db, lead, advisor, touch_number, mode=None, actor=None):
        if lead.id == first_lead:
            _poison(db)
        return _touch_email(db, lead, advisor, touch_number)

    with patch.object(acs, "generate_touch_email", gen):
        out = acs.process_scheduled_touches(db_session, org_id=sample_org.id)
    assert out["sent"] == 1 and out["errors"] == 1
    assert ai_env.sent == ["family2@example.com"]


def test_ai_touch_commit_failure_after_send_does_not_resend(
        ai_env, db_session, sample_org, monkeypatch):
    acs = ai_env.acs
    commit = _CommitFailsOnce(db_session, monkeypatch)
    real_send = acs._send_email_resend

    def send_then_fail_commit(db, adv, to, subj, html):
        real_send(db, adv, to, subj, html)
        commit.arm()          # _send_touch's own commit, right after the send
    monkeypatch.setattr(acs, "_send_email_resend", send_then_fail_commit)

    with patch.object(acs, "generate_touch_email", _touch_email):
        acs.process_scheduled_touches(db_session, org_id=sample_org.id)
        # The next pass of the 2-minute loop.
        acs.process_scheduled_touches(db_session, org_id=sample_org.id)
    assert sorted(ai_env.sent) == ["family1@example.com", "family2@example.com"]
    assert len(ai_env.sent) == 2, "no conversation was mailed twice"


def test_ai_touch_generation_refusal_still_leaves_it_due(
        ai_env, db_session, sample_org):
    """Unchanged behaviour: a touch that cannot have been sent releases its
    claim, so the conversation stays due for the next pass."""
    acs = ai_env.acs
    before = {c.id: c.next_send_at for c in ai_env.convs}
    with patch.object(acs, "generate_touch_email", return_value={
            "subject": "x", "body": "y", "should_stop": False, "escalate": False,
            "source": "fallback", "generation_failed": True}):
        acs.process_scheduled_touches(db_session, org_id=sample_org.id)
    db_session.expire_all()
    for c in db_session.query(PipelineConversation).all():
        assert c.next_send_at == before[c.id]
    assert ai_env.sent == []


# ═══════════════════════════════════════════════════════════════════════════
# 3. email_poller_service
# ═══════════════════════════════════════════════════════════════════════════

def test_email_poller_one_bad_email_does_not_lose_the_others(
        db_session, sample_org, sample_advisor, monkeypatch):
    from app.services import email_poller_service as ep
    from app.services import ai_conversation_service as acs
    from app.services import pipeline_service

    sample_advisor.microsoft_365_connected = True
    sample_advisor.microsoft_oauth_refresh_token_encrypted = "enc"
    db_session.commit()
    l1 = _lead(db_session, sample_org, sample_advisor, 1)
    l2 = _lead(db_session, sample_org, sample_advisor, 2)
    emails = [{"id": "m%d" % i, "categories": [],
               "from": {"emailAddress": {"address": lead.email}},
               "receivedDateTime": "2026-09-01T12:00:00Z",
               "body": {"content": "Reply from %d" % i}}
              for i, lead in ((1, l1), (2, l2))]
    tagged = []
    monkeypatch.setattr(ep, "_get_fresh_access_token", lambda adv: "tok")
    monkeypatch.setattr(ep, "_fetch_recent_emails", lambda tok: emails)
    monkeypatch.setattr(ep, "_mark_email_processed",
                        lambda tok, mid, tag="x": tagged.append(mid))
    monkeypatch.setattr(pipeline_service, "process_inbound_reply",
                        lambda *a, **k: None)

    def handler(db, lead, advisor, body):
        if lead.id == l1.id:
            # A pending row that cannot be flushed: fails at this email's
            # commit and leaves the session needing a rollback.
            db.add(Reply(lead_id=None, body="bad"))
        return {"action": "handled"}
    monkeypatch.setattr(acs, "handle_inbound_reply", handler)

    out = ep.poll_inbox_for_replies(db_session, sample_advisor.id)
    assert out["matched"] == 1 and out["errors"] == 1
    assert tagged == ["m2"], "only the committed email is tagged processed"
    db_session.expire_all()
    assert [r.lead_id for r in db_session.query(Reply).all()] == [l2.id]


def test_email_poller_one_bad_advisor_does_not_stop_the_others(
        db_session, sample_org, sample_advisor, second_advisor, monkeypatch):
    from app.services import email_poller_service as ep
    for adv in (sample_advisor, second_advisor):
        adv.microsoft_365_connected = True
        adv.microsoft_oauth_refresh_token_encrypted = "enc"
    db_session.commit()
    polled = []

    def poll(db, advisor_id):
        polled.append(advisor_id)
        if len(polled) == 1:
            _poison(db)
        db.query(User).count()          # a poisoned session would raise here
        return {"checked": 1, "matched": 1, "errors": 0}
    monkeypatch.setattr(ep, "poll_inbox_for_replies", poll)

    out = ep.poll_all_orgs(db_session)
    assert len(polled) == 2
    assert out["advisors_polled"] == 1 and out["errors"] == 1
    polled.clear()
    out = ep.poll_all_advisors(db_session, sample_org.id)
    assert out["matched"] == 1 and out["errors"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# 4. sales_appointment_reminders.process_due
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def reminder_env(db_session, monkeypatch):
    from app.models.scheduling_models import (AppointmentReminder,
                                              SalesAppointment, REMINDER_24H)
    from app.services import sales_appointment_reminders as reminders
    now = datetime(2026, 9, 1, 12, 0)
    rows = []
    for i in (1, 2):
        starts = now + timedelta(hours=24, minutes=i)
        appt = SalesAppointment(brand_sales_org_id="bso", title="Demo %d" % i,
                                starts_at=starts, ends_at=starts + timedelta(hours=1),
                                prospect_email="p%d@example.com" % i)
        db_session.add(appt)
        db_session.flush()
        r = AppointmentReminder(appointment_id=appt.id, kind=REMINDER_24H,
                                target_starts_at=starts,
                                scheduled_for=now - timedelta(minutes=10 - i))
        db_session.add(r)
        rows.append(r)
    db_session.commit()
    delivered = []
    env = SimpleNamespace(reminders=reminders, rows=rows, now=now,
                          delivered=delivered, before_deliver=None, after_deliver=None)

    def deliver(db, appt, row):
        if env.before_deliver:
            env.before_deliver(db, appt)
        delivered.append(appt.prospect_email)
        if env.after_deliver:
            env.after_deliver()
    monkeypatch.setattr(reminders, "_deliver", deliver)
    return env


def test_reminders_one_bad_row_does_not_stop_the_others(reminder_env, db_session):
    from app.models.scheduling_models import (AppointmentReminder, REMINDER_FAILED,
                                              REMINDER_SENT)

    def before(db, appt):
        if appt.prospect_email == "p1@example.com":
            _poison(db)
    reminder_env.before_deliver = before
    report = reminder_env.reminders.process_due(db_session, now=reminder_env.now)
    assert report["sent"] == 1 and report["failed"] == 1
    assert reminder_env.delivered == ["p2@example.com"]
    db_session.expire_all()
    by_id = {r.id: r.status for r in db_session.query(AppointmentReminder).all()}
    assert by_id[reminder_env.rows[0].id] == REMINDER_FAILED
    assert by_id[reminder_env.rows[1].id] == REMINDER_SENT


def test_reminders_commit_failure_after_send_does_not_resend(
        reminder_env, db_session, monkeypatch):
    from app.models.scheduling_models import AppointmentReminder
    commit = _CommitFailsOnce(db_session, monkeypatch)
    # Only the first delivery's bookkeeping commit is lost.
    reminder_env.after_deliver = lambda: (
        commit.arm() if len(reminder_env.delivered) == 1 else None)
    first = reminder_env.reminders.process_due(db_session, now=reminder_env.now)
    second = reminder_env.reminders.process_due(
        db_session, now=reminder_env.now + timedelta(minutes=15))
    assert sorted(reminder_env.delivered) == ["p1@example.com", "p2@example.com"]
    assert len(reminder_env.delivered) == 2, "no prospect emailed twice"
    assert second["examined"] == 0
    db_session.expire_all()
    statuses = sorted(r.status for r in db_session.query(AppointmentReminder).all())
    # The row whose outcome commit was lost says so; it is not pending.
    assert statuses == ["sending", "sent"]


# ═══════════════════════════════════════════════════════════════════════════
# 5. support_brief.run_daily_intelligence
# ═══════════════════════════════════════════════════════════════════════════

def test_support_brief_one_platform_failure_does_not_cost_the_others(
        db_session, monkeypatch):
    from app.services import support_brief, support_tickets
    platforms = [Platform(name="P%d" % i, slug="p%d" % i) for i in (1, 2, 3)]
    db_session.add_all(platforms)
    db_session.commit()
    bad = platforms[0].id
    real_generate = support_brief.generate

    def generate(db, *, day=None, platform_id=None, now=None):
        if platform_id == bad:
            _poison(db)
        return real_generate(db, day=day, platform_id=platform_id, now=now)
    monkeypatch.setattr(support_brief, "generate", generate)

    def sla_boom(db, **kw):
        _poison(db)
    monkeypatch.setattr(support_tickets, "refresh_open_sla_states", sla_boom)

    out = support_brief.run_daily_intelligence(db_session)
    assert out["sla_refresh"] == {"error": True}
    assert out["correlation"] != {"error": True}, "later steps still ran"
    # platform-wide + the two healthy brands
    assert len(out["briefs"]) == 3


# ═══════════════════════════════════════════════════════════════════════════
# 6. evosense scheduler contactability backfill
# ═══════════════════════════════════════════════════════════════════════════

def test_contactability_backfill_one_bad_property_does_not_stop_the_batch(
        db_session, sample_org, monkeypatch):
    from app.models.evosense_models import EvoSenseProperty
    from app.services import contactability as CB
    from app.services.evosense import scheduler
    props = [EvoSenseProperty(organization_id=sample_org.id,
                              street_address="%d Test Ln" % i, state="TX")
             for i in (1, 2, 3)]
    db_session.add_all(props)
    db_session.commit()
    seen = []

    def refresh(db, prop, strategy=None):
        seen.append(prop.id)
        if len(seen) == 1:
            _poison(db)
        prop.contactability_detail = "{}"
    monkeypatch.setattr(CB, "refresh_evosense", refresh)

    assert scheduler.backfill_contactability(db_session, org_id=sample_org.id) == 2
    assert len(seen) == 3
    db_session.expire_all()
    done = db_session.query(EvoSenseProperty).filter(
        EvoSenseProperty.contactability_detail.isnot(None)).count()
    assert done == 2


# ═══════════════════════════════════════════════════════════════════════════
# 7. post_appointment_service.check_and_send_followups
# ═══════════════════════════════════════════════════════════════════════════

def test_post_appointment_one_bad_booking_does_not_stop_the_others(
        db_session, sample_org, sample_advisor, ai_background_on, monkeypatch):
    from app.services import post_appointment_service as pas
    for i in (1, 2):
        lead = _lead(db_session, sample_org, sample_advisor, i)
        db_session.add(BookingLink(lead_id=lead.id, user_id=sample_advisor.id,
                                   status="booked",
                                   booked_time=datetime.utcnow() - timedelta(hours=1)))
    db_session.commit()
    calls = []

    def send(db, booking, lead, advisor):
        calls.append(booking.id)
        if len(calls) == 1:
            _poison(db)
    monkeypatch.setattr(pas, "_send_followup", send)
    assert pas.check_and_send_followups(db_session) == 1
    assert len(calls) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 8. wholesale_exceptions.sweep_all
# ═══════════════════════════════════════════════════════════════════════════

def test_wholesale_sweep_all_one_bad_org_lookup_does_not_stop_the_pass(
        db_session, monkeypatch):
    from app.services import entitlements, wholesale_exceptions as wx
    orgs = [Organization(name="O%d" % i, slug="o%d" % i) for i in (1, 2)]
    db_session.add_all(orgs)
    db_session.commit()
    ids = sorted(o.id for o in orgs)
    swept = []

    def has_feature(org, feature):
        if org.id == ids[0]:
            raise RuntimeError("entitlements unavailable")
        return True
    monkeypatch.setattr(wx, "_wholesale_org_ids", lambda db: ids)
    monkeypatch.setattr(entitlements, "org_has_feature", has_feature)
    monkeypatch.setattr(wx, "sweep", lambda db, org_id, *a, **k:
                        swept.append(org_id) or {"stale": 1})
    report = wx.sweep_all(db_session)
    assert swept == [ids[1]]
    assert report == {"orgs": 1, "raised": 1, "failed": 1}
