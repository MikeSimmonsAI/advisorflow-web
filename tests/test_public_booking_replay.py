"""POST /public-booking/{slug}/book: replay, conflicting replay, terminal state.

Through the mounted route with the canonical `site` fixture from
test_public_booking_api.py (imported, not duplicated). Existing coverage reused,
not repeated: thin confirmation, a plain resubmit returning the same reference,
taken slot -> 409, bad/foreign codes, spoofed identity fields, sanitising.

NOT YET EXECUTED: written where fastapi/pytest are not installed (py_compile
only). Assertions are grounded in app/services/public_booking.py:
  * the retry guard (`existing_booking`) is keyed on (brand_sales_org_id,
    booking_idempotency_key) and runs before any side effect;
  * `_replay_result` returns the original booking only for the same start, the
    same prospect email and a still-scheduled appointment; otherwise
    BOOK_KEY_CONFLICT -> 409, with no mutation.
Only external adapters are faked: the email senders are recorders.
"""
from datetime import datetime

import pytest

from app.models.sales_models import BrandSalesOrg, Opportunity, OpportunityEvent
from app.models.scheduling_models import (
    AppointmentParticipant, SalesAppointment, APPT_CANCELLED,
)
from app.services import email_service
from app.services import public_booking as pb

from test_public_booking_api import site, _body, _first_slot  # noqa: F401


@pytest.fixture()
def sends(monkeypatch):
    """Record (and swallow) every email path; count internal notifications."""
    calls = {"email": 0, "notify": 0}

    def rec(*a, **k):
        calls["email"] += 1
        return {"ok": False, "status": "recorded"}

    for name in ("send_email", "send_email_via_provider"):
        monkeypatch.setattr(email_service, name, rec, raising=False)
    real_notify = pb.notify_internal

    def spy(*a, **k):
        calls["notify"] += 1
        return real_notify(*a, **k)

    monkeypatch.setattr(pb, "notify_internal", spy)
    return calls


def _book(client, site, payload):
    return client.post("/public-booking/%s/book" % site["slug"], json=payload)


def _counts(db):
    return (db.query(SalesAppointment).count(),
            db.query(AppointmentParticipant).count(),
            db.query(Opportunity).count(),
            db.query(OpportunityEvent).filter(
                OpportunityEvent.event_type == "appointment_booked").count())


def _start(iso):
    return datetime.fromisoformat(iso.rstrip("Z"))


def test_exact_replay_creates_nothing_and_sends_nothing(client, db_session, site, sends):
    payload = _body(code=site["code"], start_utc=_first_slot(client, site))
    first = _book(client, site, payload)
    assert first.status_code == 201 and first.json()["already_booked"] is False
    before, emails, notes = _counts(db_session), sends["email"], sends["notify"]
    assert before[0] == 1 and notes == 1

    again = _book(client, site, payload)
    assert again.status_code == 201
    assert again.json()["already_booked"] is True
    assert again.json()["reference"] == first.json()["reference"]
    assert _counts(db_session) == before
    assert sends["email"] == emails and sends["notify"] == notes


def test_conflicting_replay_different_time_is_refused_without_mutation(
        client, db_session, site, sends):
    r = client.get("/public-booking/%s/slots?code=%s" % (site["slug"], site["code"]))
    slots = r.json()["slots"]
    assert len(slots) >= 2
    payload = _body(code=site["code"], start_utc=slots[0]["start_utc"])
    first = _book(client, site, payload)
    assert first.status_code == 201
    before, notes = _counts(db_session), sends["notify"]

    conflict = _book(client, site, dict(payload, start_utc=slots[1]["start_utc"]))
    assert conflict.status_code == 409
    assert _counts(db_session) == before and sends["notify"] == notes
    appt = db_session.query(SalesAppointment).one()
    assert appt.id == first.json()["reference"]
    assert appt.starts_at == _start(slots[0]["start_utc"])


def test_conflicting_replay_different_prospect_is_refused(client, db_session, site, sends):
    payload = _body(code=site["code"], start_utc=_first_slot(client, site))
    assert _book(client, site, payload).status_code == 201
    before = _counts(db_session)
    r = _book(client, site, dict(payload, email="someone.else@prospect.example"))
    assert r.status_code == 409
    assert "dana@prospect.example" not in r.text and "someone.else" not in r.text
    assert _counts(db_session) == before
    assert db_session.query(SalesAppointment).one().prospect_email == "dana@prospect.example"


def test_replay_of_a_cancelled_booking_is_refused_and_not_reactivated(
        client, db_session, site, sends):
    payload = _body(code=site["code"], start_utc=_first_slot(client, site))
    assert _book(client, site, payload).status_code == 201
    appt = db_session.query(SalesAppointment).one()
    appt.status = APPT_CANCELLED
    db_session.commit()
    before, notes = _counts(db_session), sends["notify"]

    r = _book(client, site, payload)
    assert r.status_code == 409
    db_session.refresh(appt)
    assert appt.status == APPT_CANCELLED
    assert _counts(db_session) == before and sends["notify"] == notes


def test_idempotency_key_is_scoped_to_the_brand(db_session, site):
    """The lookup is per brand_sales_org: another brand's identical key neither
    finds nor collides with this brand's booking."""
    other = BrandSalesOrg(platform_id=site["platform"].id, name="Other Sales",
                          slug="other-bso-replay", timezone="America/Chicago")
    db_session.add(other)
    db_session.commit()
    appt = SalesAppointment(
        brand_sales_org_id=site["bso"].id, title="t",
        starts_at=datetime(2030, 1, 7, 15), ends_at=datetime(2030, 1, 7, 16),
        timezone="America/Chicago", status="scheduled",
        booking_idempotency_key="shared-key")
    db_session.add(appt)
    db_session.commit()
    assert pb.existing_booking(db_session, site["bso"], "shared-key").id == appt.id
    assert pb.existing_booking(db_session, other, "shared-key") is None


def test_a_downstream_provider_failure_leaves_exactly_one_booking(
        client, db_session, site, sends, monkeypatch):
    """Everything after the commit is best-effort by design: a video-provider
    outage must neither lose nor duplicate the booking, and a retry replays."""
    from app.services import appointment_meetings

    def boom(*a, **k):
        raise RuntimeError("synthetic provider outage")

    monkeypatch.setattr(appointment_meetings, "ensure_meeting", boom)
    payload = _body(code=site["code"], start_utc=_first_slot(client, site))
    first = _book(client, site, payload)
    assert first.status_code == 201, first.text
    assert db_session.query(SalesAppointment).count() == 1
    again = _book(client, site, payload)
    assert again.json()["already_booked"] is True
    assert db_session.query(SalesAppointment).count() == 1
