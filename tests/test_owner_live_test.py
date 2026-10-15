"""The owner's live test contact: the only synthetic contact the setter may
really text, and only at the program's own verified owner number."""
from unittest.mock import patch

from app.models.models import Lead
from app.services import send_source as ss, test_records as TR
from tests.test_sci_appointment_setter import _Fake, _d, setter  # noqa: F401
from tests.test_outreach_program import _h, program  # noqa: F401


def _owner(db, org, phone):
    lead = Lead(organization_id=org.id, first_name="Owner", last_name=TR.OWNER_TEST_LAST_NAME, phone=phone,
                is_test=True, status="sent")
    db.add(lead)
    db.commit()
    return lead


def test_only_the_setter_only_the_owners_number(setter):
    db, org, prog = setter["db"], setter["org"], setter["prog"]           # alert_phone +18435550000
    me = _owner(db, org, "+18435550000")
    assert TR.owner_live_test_send_allowed(db, me, ss.SCI_SETTER)
    assert not TR.owner_live_test_send_allowed(db, me, ss.CADENCE)
    assert TR.owner_live_test_send_allowed(db, me, ss.MANUAL)              # the owner, by hand, to himself
    assert not TR.owner_live_test_send_allowed(db, me, ss.BULK)
    other = _owner(db, org, "+12145550199")
    assert not TR.owner_live_test_send_allowed(db, other, ss.SCI_SETTER)
    plain_test = Lead(organization_id=org.id, first_name="X", last_name="SIM", phone="+18435550000", is_test=True)
    db.add(plain_test)
    db.commit()
    assert not TR.owner_live_test_send_allowed(db, plain_test, ss.SCI_SETTER)


def test_live_test_endpoint_owner_only_and_never_pages_team(setter, client):
    db, prog, admin = setter["db"], setter["prog"], setter["admin"]
    h = _h(db, admin)
    assert client.post("/program/setter/live-test", headers=h, json={"text": "hi"}).status_code == 403
    admin.role = "god_admin"
    db.commit()
    h = dict(h, **{"X-Org-Override": setter["org"].id})
    fake = _Fake([_d("answer", "Thanks for writing. Would a short call or a visit be easier?")])
    from app.services.programs import ai_setter
    with patch.object(ai_setter, "_get_client", return_value=fake), \
         patch("app.services.programs.responses._deliver_staff_alert") as staff, \
         patch("app.services.contact_hours.check", return_value={"permitted": True}), \
         patch("app.services.sms_service.send_sms") as send:
        send.return_value = None
        r = client.post("/program/setter/live-test", headers=h, json={"text": "What is this about?", "reset": True})
    assert r.status_code == 200, r.text
    assert staff.call_count == 0                                           # no team page
    assert send.call_count == 1 and send.call_args.kwargs["send_source"] == ss.SCI_SETTER
    lead = db.query(Lead).filter_by(last_name=TR.OWNER_TEST_LAST_NAME).one()
    assert lead.is_test and lead.phone == "+18435550000"


def test_owner_test_books_for_real_and_runs_auto_with_program_off(setter, monkeypatch):
    from app.services.programs import appointments as A, ai_setter
    from app.services.programs import responses as R
    db, org, prog = setter["db"], setter["org"], setter["prog"]
    prog.ai_setter_mode = "off"
    db.commit()
    me = _owner(db, org, "+18435550000")
    assert TR.is_owner_test_lead(me)
    assert TR.owner_live_test_send_allowed(db, me, ss.APPOINTMENT_FOLLOWUP)
    # program OFF, owner test still answered in AUTO
    fake = _Fake([_d("answer", "Thanks for writing. Would a short call or a visit be easier?")])
    from unittest.mock import patch as _p
    with _p.object(ai_setter, "_get_client", return_value=fake), \
         _p("app.services.contact_hours.check", return_value={"permitted": True}), \
         _p("app.services.sms_service.send_sms") as send:
        R.on_inbound(db, me, "What is this about?", "sms")
    assert send.call_count == 1 and len(fake.calls) == 1
    # booking for the owner test is NOT simulated (it goes to the real calendar path)
    called = {}
    monkeypatch.setattr(A, "still_free", lambda db, prog, slot, *a, **k: True)
    import app.routers.calendar_router as cr

    async def _hook(req, db):
        called["payload"] = await req.json()
        from app.models.models import BookingLink
        link = db.query(BookingLink).filter_by(token=called["payload"]["booking_token"]).one()
        link.status = "booked"
        db.commit()
    monkeypatch.setattr(cr, "booking_confirmed_webhook", _hook)
    from datetime import datetime
    slot = datetime(2030, 1, 8, 10, 0)
    out = A.book(db, prog, me, slot, "visit")
    assert out["status"] == "confirmed" and "TEST" in called["payload"]["appt_label"]


def test_after_cleanup_the_owner_test_never_rebooks_or_follows_up(setter, client, monkeypatch):
    from datetime import datetime, timedelta
    from app.models.program_models import ProgramConversation
    from app.services.programs import ai_setter
    from app.services.programs import responses as R
    db, org, prog, admin = setter["db"], setter["org"], setter["prog"], setter["admin"]
    me = _owner(db, org, "+18435550000")
    conv = ProgramConversation(organization_id=org.id, lead_id=me.id, state="offered", is_test=True,
                               next_followup_at=datetime.utcnow() - timedelta(minutes=1))
    db.add(conv)
    db.commit()
    fake = _Fake([_d("follow_up_later", "Just checking in.")])
    with patch.object(ai_setter, "_get_client", return_value=fake), \
         patch("app.services.sms_service.send_sms") as send:
        ai_setter.followup_sweep(db)
    assert send.call_count == 0 and not fake.calls                         # no follow-up to the owner
    admin.role = "god_admin"
    db.commit()
    h = dict(_h(db, admin), **{"X-Org-Override": org.id})
    assert client.post("/program/setter/live-test/cleanup", headers=h).json()["ok"]
    fake = _Fake([_d("book", "", slot_choice=1)])
    with patch.object(ai_setter, "_get_client", return_value=fake), \
         patch("app.services.contact_hours.check", return_value={"permitted": True}), \
         patch("app.services.sms_service.send_sms") as send:
        R.on_inbound(db, me, "Friday at 9 works", "sms")
    assert send.call_count == 0 and not fake.calls                         # finished test: no AI, no booking
