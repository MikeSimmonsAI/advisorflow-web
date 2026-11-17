"""EvoSys Sales: a lead who books through the existing request-demo flow shows
in Appointments and their follow-up stops (PR #663). Uses the real public
booking endpoint, unchanged."""
from datetime import datetime, timedelta

import pytest

from app.models.evosys_sales_models import EvoSysOutreachState
from app.models.sales_models import Opportunity
from app.services.evosys_sales import outreach as O

from tests.test_public_booking_api import site, _body, _first_slot  # noqa: F401
from tests.test_sales_workspace import _token_headers


class Sender:
    def __init__(self):
        self.calls = []

    def __call__(self, to, msg, s, opp_id):
        self.calls.append(to)
        return {"success": True}


def test_booking_from_the_email_link_lands_in_appointments_and_stops_follow_up(
        client, db_session, site, monkeypatch):
    bso = site["bso"]
    monkeypatch.setenv("EVOSYS_SALES_BRAND_ID", bso.id)
    lead = Opportunity(brand_sales_org_id=bso.id, company_name="Prospect Co", contact_name="Dana Prospect",
                       email="dana@prospect.example", phone="+12145550199", stage="prospect", status="open")
    db_session.add(lead); db_session.commit()
    s = O.get_settings(db_session, bso.id)
    s.mailing_address, s.offer = "100 Main St, Dallas, TX 75201", "Free for 30 days."
    s.approved_at, s.sending_on = datetime.utcnow(), True
    db_session.commit()
    now = datetime.utcnow().replace(hour=16, minute=0) + timedelta(days=(7 - datetime.utcnow().weekday()) % 7 or 7)
    snd = Sender()
    O.run_pass(db_session, now, snd)
    assert snd.calls == ["dana@prospect.example"]

    start = _first_slot(client, site)
    r = client.post("/public-booking/%s/book" % site["slug"], json=_body(code=site["code"], start_utc=start))
    assert r.status_code == 201, r.text

    headers = _token_headers(site["direct"], db_session)
    appts = client.get("/sales/evosys/appointments", headers=headers).json()
    assert [a["company"] for a in appts["upcoming"]] == ["Prospect Co"]
    assert appts["upcoming"][0]["lead_id"] == lead.id

    snd.calls.clear()
    O.run_pass(db_session, now + timedelta(days=3), snd)
    assert snd.calls == []
    st = db_session.get(EvoSysOutreachState, lead.id)
    db_session.refresh(st)
    assert st.stop_reason == "booked"
