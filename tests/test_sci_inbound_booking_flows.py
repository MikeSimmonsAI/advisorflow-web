"""Synthetic inbound-reply routing and booking-confirm flows (SCI).

Local only: in-memory SQLite from conftest, Twilio refused by the autouse
no_real_twilio_calls fixture, no network, no credentials.

NOT YET EXECUTED: written where pytest/fastapi were not installed (py_compile
only). Assertions are grounded in source read for this file:
  * inbound_mailbox_router.poll_now -> inbound_mailbox_service.poll_mailbox;
    with fetch=None it calls _access_token_rw(box) -> (token, can_write) and
    _fetch(token, since). Those two are the Graph boundary and are faked here.
  * poll_mailbox result: {"mailbox", "checked", "matched", "errors"}; a match
    stores one Reply on the lead, sets lead.status "replied", writes one
    InboundMailboxMessage(outcome="matched", organization_id, lead_id).
  * calendar_router.confirm_booking is token-authorized (no login); it calls
    calendar_service.create_calendar_event_for_booking (provider boundary:
    calendar_service._get_calendar_service) then appointment_flow_service.
    on_booking_confirmed (senders _send_sms_safe / _send_email_safe).
  * confirm_booking now refuses cancelled/expired links (409), returns the
    existing event for a same-time repeat (200, already_confirmed) and 409s a
    different-time repeat. Before this checkpoint a repeat inserted a second
    calendar event and a cancelled booking could be re-booked.

Already covered elsewhere and not duplicated: routing/ambiguity/own-mail/moved
message dedupe via poll_mailbox (test_inbound_mailbox.py); cancel persistence
and cross-org cancel (test_sci_desktop_synthetic_flows.py, test_calendar_router.py).
"""
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
from app.models.models import BookingLink, EmailMessage, Lead, Organization, Reply, User
from app.services import appointment_flow_service as flow
from app.services import calendar_service
from app.services import email_service
from app.services import inbound_mailbox_service as S
from app.services.auth_service import create_access_token, hash_password

PW = "TestPass123!"
MAILBOX = "support@synthetic-sci.test"


def _status(obj):
    return getattr(obj.status, "value", obj.status)


# ── Inbound reply routing through the canonical route ───────────────────

@pytest.fixture()
def outbound_calls(monkeypatch):
    """Refuse and record every outbound path reachable from an inbound poll:
    raw HTTP and the email provider. Twilio client construction is already
    refused by conftest."""
    import httpx
    calls = []

    def refuse(name):
        def f(*a, **k):
            calls.append(name)
            raise AssertionError("outbound call during inbound handling: %s" % name)
        return f

    for fn in ("get", "post", "put", "patch", "delete", "request"):
        monkeypatch.setattr(httpx, fn, refuse("httpx.%s" % fn))
    monkeypatch.setattr(email_service, "send_email_via_provider", refuse("send_email_via_provider"))
    monkeypatch.setattr(email_service, "send_email", refuse("send_email"))
    return calls


@pytest.fixture()
def inbound_world(db_session, sample_advisor):
    a = Organization(name="Synthetic Inbound A", slug="syn-in-a-%s" % uuid.uuid4().hex[:6],
                     is_active=True, from_email=MAILBOX)
    b = Organization(name="Synthetic Inbound B", slug="syn-in-b-%s" % uuid.uuid4().hex[:6],
                     is_active=True, from_email=MAILBOX)
    db_session.add_all([a, b])
    db_session.commit()
    lead_a = Lead(organization_id=a.id, first_name="Pat", last_name="A", email="pat@example.test",
                  status="sent", assigned_to_id=sample_advisor.id)
    lead_b = Lead(organization_id=b.id, first_name="Pat", last_name="B", email="pat@example.test",
                  status="new")
    box = InboundMailbox(address=MAILBOX, is_active=True)
    db_session.add_all([lead_a, lead_b, box])
    db_session.commit()
    # Only workspace A emailed this person; the mailbox is shared by A and B.
    db_session.add(EmailMessage(lead_id=lead_a.id, sender_id=sample_advisor.id,
                                subject="Your planning guide", body_html="b", status="sent",
                                sent_at=datetime.utcnow() - timedelta(days=1)))
    db_session.commit()
    return SimpleNamespace(a=a, b=b, lead_a=lead_a, lead_b=lead_b, box=box)


@pytest.fixture()
def god_hdr(db_session):
    g = User(organization_id=None, email="god-%s@synthetic.test" % uuid.uuid4().hex[:6],
             password_hash=hash_password(PW), full_name="Synthetic Owner", role="god_admin",
             must_change_password=False, is_active=True)
    db_session.add(g)
    db_session.commit()
    return {"Authorization": "Bearer " + create_access_token(g, db_session)}


def _graph_msg(gid, imid, body="Yes, please call me."):
    t = (datetime.utcnow() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": gid, "internetMessageId": imid, "subject": "Re: Your planning guide",
            "from": {"emailAddress": {"address": "PAT@example.test"}}, "receivedDateTime": t,
            "body": {"content": body}, "bodyPreview": body[:50]}


def test_poll_now_routes_a_reply_to_the_emailed_workspace_only_and_replays_safely(
        client, db_session, inbound_world, god_hdr, outbound_calls, monkeypatch):
    w = inbound_world
    inbox = [_graph_msg("g-1", "<syn-1@mail.test>")]
    monkeypatch.setattr(S, "_access_token_rw", lambda box: ("synthetic-token", False))
    monkeypatch.setattr(S, "_fetch", lambda token, since: inbox)

    url = "/god/email/inbound-mailboxes/%s/poll-now" % w.box.id
    first = client.post(url, headers=god_hdr)
    assert first.status_code == 200, first.text
    assert first.json()["result"] == {"mailbox": MAILBOX, "checked": 1, "matched": 1, "errors": 0}

    # Attached to the lead we emailed, in the workspace we emailed from.
    assert db_session.query(Reply).filter(Reply.lead_id == w.lead_a.id).count() == 1
    row = db_session.query(InboundMailboxMessage).one()
    assert (row.outcome, row.lead_id, row.organization_id) == ("matched", w.lead_a.id, w.a.id)
    # Same address in the other workspace, never emailed: receives nothing.
    assert db_session.query(Reply).filter(Reply.lead_id == w.lead_b.id).count() == 0
    db_session.refresh(w.lead_b)
    assert _status(w.lead_b) == "new"

    # Replay, then the same message re-delivered under a new Graph id (a move).
    assert client.post(url, headers=god_hdr).status_code == 200
    inbox[:] = [_graph_msg("g-1-moved", "<syn-1@mail.test>")]
    assert client.post(url, headers=god_hdr).status_code == 200
    assert db_session.query(Reply).count() == 1

    assert outbound_calls == []


def test_poll_now_is_platform_owner_only(client, inbound_world, auth_headers):
    url = "/god/email/inbound-mailboxes/%s/poll-now" % inbound_world.box.id
    assert client.post(url, headers=auth_headers).status_code in (401, 403)
    assert client.post(url).status_code == 401


# ── Booking confirmation: only the calendar provider client is faked ────
# POST /calendar/confirm-booking is token-authorized (the BookingLink token is
# the credential). The real route, create_calendar_event_for_booking and
# on_booking_confirmed run; _get_calendar_service returns a deterministic fake
# and the customer senders are recorders, so nothing leaves the process.

class FakeCalendar:
    def __init__(self):
        self.inserted = []
        self._next = None

    def events(self):
        return self

    def insert(self, calendarId, body):
        self.inserted.append((calendarId, body))
        n = len(self.inserted)
        self._next = {"id": "fake-event-%d" % n, "htmlLink": "https://calendar.invalid/e/%d" % n}
        return self

    def execute(self):
        return self._next


@pytest.fixture()
def booking_world(db_session, sample_advisor, sample_lead, monkeypatch):
    cal = FakeCalendar()
    sent = []
    monkeypatch.setattr(calendar_service, "_get_calendar_service", lambda advisor: cal)
    monkeypatch.setattr(flow, "_send_sms_safe", lambda advisor, to, body: sent.append(("sms", to)))
    monkeypatch.setattr(flow, "_send_email_safe", lambda to, subject, html: sent.append(("email", to)))
    sample_advisor.google_calendar_connected = True
    sample_advisor.google_calendar_id = "synthetic-cal"
    b = BookingLink(lead_id=sample_lead.id, user_id=sample_advisor.id, status="pending")
    db_session.add(b)
    db_session.commit()
    return SimpleNamespace(cal=cal, sent=sent, booking=b, lead=sample_lead, advisor=sample_advisor)


def _confirm(client, token, when="2026-11-03T15:00:00"):
    return client.post("/calendar/confirm-booking", json={"booking_token": token, "booked_datetime": when})


def test_confirm_booking_persists_and_hands_off_without_sending(client, db_session, booking_world):
    w = booking_world
    r = _confirm(client, w.booking.token)
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True and r.json()["event_id"] == "fake-event-1"
    db_session.refresh(w.booking)
    db_session.refresh(w.lead)
    assert w.booking.status == "confirmed" and w.booking.calendar_event_id == "fake-event-1"
    assert w.booking.booked_time.isoformat() == "2026-11-03T15:00:00"
    assert _status(w.lead) == "booked"
    assert len(w.cal.inserted) == 1 and w.cal.inserted[0][0] == "synthetic-cal"
    # The handoff ran (confirmation addressed to the lead) but only into the recorder.
    assert ("email", w.lead.email) in w.sent


def test_confirm_booking_repeat_is_idempotent_and_never_double_books(client, db_session, booking_world):
    w = booking_world
    assert _confirm(client, w.booking.token).status_code == 200
    sent_after_first = list(w.sent)
    again = _confirm(client, w.booking.token)
    assert again.status_code == 200 and again.json()["event_id"] == "fake-event-1"
    assert len(w.cal.inserted) == 1
    assert w.sent == sent_after_first      # no second confirmation to the family
    assert _confirm(client, w.booking.token, "2026-11-04T09:00:00").status_code == 409
    db_session.refresh(w.booking)
    assert w.booking.calendar_event_id == "fake-event-1"
    assert w.booking.booked_time.isoformat() == "2026-11-03T15:00:00"


def test_confirm_booking_cannot_revive_a_cancelled_booking(client, db_session, booking_world):
    w = booking_world
    w.booking.status = "cancelled"
    db_session.commit()
    assert _confirm(client, w.booking.token).status_code == 409
    db_session.refresh(w.booking)
    assert w.booking.status == "cancelled" and w.booking.calendar_event_id is None
    assert w.cal.inserted == [] and w.sent == []


def test_confirm_booking_without_a_connected_calendar_changes_nothing(client, db_session, booking_world):
    w = booking_world
    w.advisor.google_calendar_connected = False
    db_session.commit()
    assert _confirm(client, w.booking.token).status_code == 400
    db_session.refresh(w.booking)
    assert w.booking.status == "pending" and w.booking.booked_time is None
    assert w.cal.inserted == [] and w.sent == []


def test_other_workspace_cannot_cancel_a_confirmed_booking(client, db_session, booking_world):
    w = booking_world
    assert _confirm(client, w.booking.token).status_code == 200
    org = Organization(name="Synthetic Other Org", slug="syn-other-%s" % uuid.uuid4().hex[:6],
                       plan="standard", industry="funeral")
    db_session.add(org)
    db_session.commit()
    other = User(organization_id=org.id, email="other-%s@synthetic.test" % uuid.uuid4().hex[:6],
                 password_hash=hash_password(PW), full_name="Other Advisor", role="advisor",
                 must_change_password=False)
    db_session.add(other)
    db_session.commit()
    hb = {"Authorization": "Bearer " + create_access_token(other, db_session)}
    sent_before = list(w.sent)
    assert client.post("/calendar/cancel-booking/%s" % w.booking.id, headers=hb).status_code == 404
    db_session.refresh(w.booking)
    assert w.booking.status == "confirmed"
    assert w.sent == sent_before


# ── Vercel booking app webhook: POST /calendar/booking-confirmed ────────
# Token-authorized, no login. These cases return before any calendar/SMS/email
# adapter is reached, so nothing can leave the process. The first-confirmation
# path is covered by test_webhook_first_confirmation_* at the end of this file,
# with the Google calendar client, advisor email and lead-SMS client faked.

def _webhook(client, token, slot="2026-11-03T15:00"):
    return client.post("/calendar/booking-confirmed", json={"booking_token": token, "slot_display": slot})


def test_webhook_exact_replay_is_idempotent_and_mutates_nothing(client, db_session, booking_world):
    w = booking_world
    w.booking.status = "booked"
    w.booking.booked_time = datetime(2026, 11, 3, 15, 0)
    db_session.commit()
    r = _webhook(client, w.booking.token)
    assert r.status_code == 200 and r.json()["idempotent_replay"] is True
    assert r.json()["booking_id"] == w.booking.id
    db_session.refresh(w.booking)
    assert w.booking.status == "booked" and w.booking.booked_time.isoformat() == "2026-11-03T15:00:00"
    assert w.cal.inserted == [] and w.sent == []


def test_webhook_cannot_revive_cancelled_or_expired_booking(client, db_session, booking_world):
    w = booking_world
    for terminal in ("cancelled", "expired"):
        w.booking.status = terminal
        db_session.commit()
        assert _webhook(client, w.booking.token).status_code == 409
        db_session.refresh(w.booking)
        db_session.refresh(w.lead)
        assert w.booking.status == terminal and w.booking.calendar_event_id is None
        assert _status(w.lead) != "booked"
        assert w.cal.inserted == [] and w.sent == []


def test_webhook_requires_booking_token(client):
    assert client.post("/calendar/booking-confirmed", json={"slot_display": "2026-11-03T15:00"}).status_code == 400


@pytest.fixture()
def webhook_world(db_session, booking_world, monkeypatch):
    """First-confirmation adapters, all deterministic: Google calendar (the
    FakeCalendar already patched in), advisor notification email, lead SMS
    client. Advisor Twilio and Resend stay unconfigured and therefore skip."""
    from app.routers import calendar_router
    from app.services import sms_service
    w = booking_world
    w.advisor.google_oauth_refresh_token_encrypted = "synthetic-not-a-secret"
    w.advisor.microsoft_365_connected = False
    w.advisor.twilio_account_sid = None
    db_session.commit()
    w.advisor_emails, w.lead_sms = [], []

    class _Msgs:
        def create(self, body, from_, to):
            w.lead_sms.append((from_, to))

    monkeypatch.setattr(calendar_router, "_send_booking_notification_email",
                        lambda advisor, lead, label, slot, db=None: w.advisor_emails.append(lead.id))
    monkeypatch.setattr(sms_service, "_resolve_twilio_creds",
                        lambda advisor, db: (SimpleNamespace(messages=_Msgs()), "+15550000001", None))
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    return w


def test_webhook_first_confirmation_is_one_event_one_transition_one_handoff(client, db_session, webhook_world):
    w = webhook_world
    r = _webhook(client, w.booking.token)
    assert r.status_code == 200, r.text
    assert "idempotent_replay" not in r.json()
    db_session.refresh(w.booking)
    db_session.refresh(w.lead)
    assert w.booking.status == "booked" and _status(w.lead) == "booked"
    assert w.booking.booked_time.isoformat() == "2026-11-03T15:00:00"
    assert len(w.cal.inserted) == 1
    assert w.advisor_emails == [w.lead.id]
    assert w.lead_sms == [("+15550000001", w.lead.phone)]

    # An exact replay afterwards adds nothing.
    again = _webhook(client, w.booking.token)
    assert again.json()["idempotent_replay"] is True
    assert len(w.cal.inserted) == 1 and len(w.advisor_emails) == 1 and len(w.lead_sms) == 1
