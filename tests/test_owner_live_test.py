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
    assert not TR.owner_live_test_send_allowed(db, me, ss.MANUAL)
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
