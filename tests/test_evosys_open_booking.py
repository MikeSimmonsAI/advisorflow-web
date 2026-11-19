"""Mike's one booking link (/evosys-sales-public/meet): finds or adds the lead,
never duplicates, never enrolls a new person in outreach, respects Do Not
Contact, never reveals someone else's booked call, and hands back the lead's
own booking page."""
from datetime import datetime, timedelta

import pytest

from app.models.evosys_sales_models import EvoSysOutreachState
from app.models.sales_models import Opportunity, OpportunityEvent
from app.models.scheduling_models import SalesAppointment
from app.services.evosys_sales import booking_page as BP
from app.services.evosys_sales import open_booking as OB
from app.services.evosys_sales import outreach as O

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, manager, manager_headers, _make_sales_user, _add_membership, _token_headers, _SEQ,
)

GOOD = {"name": "Dana Ortiz", "company": "Sparkle Cleaning", "email": "dana@sparkle.test",
        "phone": "(214) 555-0199", "topic": "More commercial contracts"}


@pytest.fixture(autouse=True)
def _env(monkeypatch, brand, manager):
    monkeypatch.setenv("EVOSYS_SALES_BRAND_ID", brand.id)
    monkeypatch.setenv("EVOSYS_OPEN_BOOKING_OWNER_EMAIL", manager.email)


def test_page_is_public_and_private(client):
    r = client.get("/evosys-sales-public/meet")
    assert r.status_code == 200 and "Choose a time" in r.text
    assert r.headers["cache-control"] == "no-store" and "noindex" in r.headers["x-robots-tag"]


def test_new_person_becomes_a_discovery_lead_with_no_outreach(client, db_session, brand, manager):
    r = client.post("/evosys-sales-public/meet", json=GOOD)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "ok" and out["created"] is True
    assert out["redirect"].startswith("/evosys-sales-public/book/")
    o = db_session.query(Opportunity).one()
    assert (o.company_name, o.contact_name, o.email, o.owner_user_id) == ("Sparkle Cleaning", "Dana Ortiz",
                                                                          "dana@sparkle.test", manager.id)
    assert o.stage == "discovery" and o.stage not in O.WORKING_STAGES
    assert o.source_system == OB.SOURCE_SYSTEM
    assert db_session.query(EvoSysOutreachState).count() == 0
    # the redirect is that lead's own signed booking page
    assert BP.read(out["redirect"].rsplit("/", 1)[1]) == o.id
    assert client.get(out["redirect"]).status_code == 200


def test_same_email_or_phone_is_the_same_lead_and_nothing_is_overwritten(client, db_session, brand, manager):
    o = Opportunity(brand_sales_org_id=brand.id, owner_user_id=manager.id, company_name="Sparkle Co (original)",
                    contact_name="Dana O.", email="DANA@sparkle.test", phone="2145550199", stage="contacted",
                    status="open")
    db_session.add(o); db_session.commit()
    a = client.post("/evosys-sales-public/meet", json=GOOD).json()
    b = client.post("/evosys-sales-public/meet", json=dict(GOOD, email="other@x.test")).json()
    assert a["created"] is False and b["created"] is False
    assert db_session.query(Opportunity).count() == 1
    db_session.refresh(o)
    assert o.company_name == "Sparkle Co (original)" and o.email == "DANA@sparkle.test"
    assert db_session.query(OpportunityEvent).filter_by(opportunity_id=o.id, event_type="open_booking_used").count() == 2


def test_existing_booking_is_never_shown_to_whoever_typed_the_email(client, db_session, brand, manager):
    o = Opportunity(brand_sales_org_id=brand.id, owner_user_id=manager.id, company_name="Booked Co",
                    email="dana@sparkle.test", phone="2145550100", stage="contacted", status="open")
    db_session.add(o); db_session.commit()
    start = datetime.utcnow() + timedelta(days=2)
    db_session.add(SalesAppointment(brand_sales_org_id=brand.id, opportunity_id=o.id, title="Business Growth Call",
                                    starts_at=start, ends_at=start + timedelta(minutes=30), status="scheduled"))
    db_session.commit()
    out = client.post("/evosys-sales-public/meet", json=GOOD).json()
    assert out["status"] == "already" and "redirect" not in out
    assert "Booked Co" not in str(out) and start.isoformat()[:10] not in str(out)


def test_do_not_contact_gets_no_booking_page(client, db_session, brand, monkeypatch):
    from app.services import sales_suppression as SDNC
    monkeypatch.setattr(SDNC, "find", lambda db, bso, phone=None, email=None: object())
    out = client.post("/evosys-sales-public/meet", json=GOOD).json()
    assert out["status"] == "error" and "redirect" not in out
    assert db_session.query(Opportunity).count() == 0


def test_validation_and_honeypot(client, db_session):
    r = client.post("/evosys-sales-public/meet", json=dict(GOOD, email="nope", phone="123", name=""))
    assert r.status_code == 422 and set(r.json()["detail"]["errors"]) == {"email", "phone", "name"}
    r = client.post("/evosys-sales-public/meet", json=dict(GOOD, website="spam.example"))
    assert r.json()["status"] == "error"
    assert db_session.query(Opportunity).count() == 0


def test_not_set_up_without_the_calendar_owner(client, db_session, monkeypatch):
    monkeypatch.setenv("EVOSYS_OPEN_BOOKING_OWNER_EMAIL", "nobody@nowhere.test")
    out = client.post("/evosys-sales-public/meet", json=GOOD).json()
    assert out["status"] == "error" and db_session.query(Opportunity).count() == 0


def test_team_can_get_the_link(client, db_session, manager, manager_headers):
    r = client.get("/sales/evosys/open-booking", headers=manager_headers).json()
    assert r["url"].endswith("/evosys-sales-public/meet") and r["ready"] is True
    assert client.get("/sales/evosys/open-booking").status_code == 401
