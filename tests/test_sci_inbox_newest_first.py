"""Oct 22 (Mike): the SCI Live inbox is newest first. A message that came in
today is at the top - an older HOT or overdue reply never pushes it down."""
from datetime import datetime, timedelta

from app.services.programs import responses as R
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import setter  # noqa: F401 (fixture)


def test_todays_message_is_at_the_top_above_older_hot_and_overdue(client, setter, sample_advisor):
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    setter["prog"].ai_setter_mode = "off"
    db.commit()
    old_hot = _lead_for(db, org, sample_advisor, "L001", first="Teresita")
    new_one = _lead_for(db, org, sample_advisor, "L002", phone="2145550102", first="George", email="g@example.com")
    for l in (old_hot, new_one):
        l.is_test = False
    db.commit()
    five_days_ago = datetime.utcnow() - timedelta(days=5)
    R.on_inbound(db, old_hot, "Call me, I want to set up a visit", "sms", now=five_days_ago)   # hot, overdue
    R.on_inbound(db, new_one, "Ok I will call you", "sms", now=datetime.utcnow())               # today, low
    inbox = client.get("/program/command", headers=_h(db, admin)).json()["inbox"]
    assert [i["lead_id"] for i in inbox][:2] == [new_one.id, old_hot.id]
    assert inbox[1]["sla_breached"] is True                                     # still flagged, just not on top
    times = [i["received_at"] for i in inbox]
    assert times == sorted(times, reverse=True)
