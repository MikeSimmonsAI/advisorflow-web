"""Overnight Oct 19: a home visit a family asked for is impossible to miss in
SCI Live - badge, top of the inbox, its own filter, a home-screen task - and
the advisor marks it arranged (nothing is booked or sent by that)."""
import json

from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import _Fake, _d, _run, setter  # noqa: F401 (fixture)
from tests.test_sci_decline_alternatives import YES_HOME, _location
from app.services.programs import ai_setter as S


def _home_request(db, org, adv, ref="L001", phone="2145550101"):
    lead = _lead_for(db, org, adv, ref, phone=phone)
    _location(db, lead, home_visits=True)
    _run(db, lead, "Can someone come to my house?", _Fake([_d("handoff", YES_HOME)]))
    return lead


def test_home_visit_is_badged_first_filtered_and_a_task(client, setter, sample_advisor):
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    other = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Hilda")
    _run(db, other, "Yes I would like to set up a time to come in", _Fake([_d("offer_times", "Would {TIMES} work?")]))
    lead = _home_request(db, org, sample_advisor)
    h = _h(db, admin)
    home = client.get("/program/command", headers=h).json()
    first = home["inbox"][0]
    assert first["lead_id"] == lead.id and first["badge"] == "Home visit" and first["home_visit"] is True
    assert home["filter_counts"]["home_visits"] == 1
    only = client.get("/program/command?filter=home_visits", headers=h).json()["inbox"]
    assert [i["lead_id"] for i in only] == [lead.id]
    tasks = [t for t in home["tasks"] if t["kind"] == "home_visit"]
    assert [t["lead_id"] for t in tasks] == [lead.id]
    fam = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    assert fam["home_visit"]["waiting"] is True


def test_marking_it_arranged_clears_the_task_and_books_nothing(client, setter, sample_advisor):
    from app.models.models import BookingLink, Message
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    lead = _home_request(db, org, sample_advisor)
    sent_before = db.query(Message).filter_by(lead_id=lead.id).count()
    h = _h(db, admin)
    r = client.post("/program/command/home-visit", headers=h, json={"lead_id": lead.id})
    assert r.status_code == 200 and r.json()["home_visit"]["done"] is True
    home = client.get("/program/command", headers=h).json()
    assert not [t for t in home["tasks"] if t["kind"] == "home_visit"]
    assert home["filter_counts"]["home_visits"] == 0
    assert db.query(BookingLink).filter_by(lead_id=lead.id).count() == 0
    assert db.query(Message).filter_by(lead_id=lead.id).count() == sent_before
    fam = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    assert fam["home_visit"]["waiting"] is False and fam["home_visit"]["done_by"]


def test_another_workspace_cannot_mark_it(client, setter, sample_advisor):
    from app.models.models import Organization, User
    from tests.test_outreach_program import hash_password
    db, org = setter["db"], setter["org"]
    lead = _home_request(db, org, sample_advisor)
    other = Organization(name="Other Co", slug="other-co-hv")
    db.add(other)
    db.commit()
    u = User(organization_id=other.id, email="x@other.example.com", password_hash=hash_password("Pass12345!"),
             full_name="X", role="org_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    r = client.post("/program/command/home-visit", headers=_h(db, u), json={"lead_id": lead.id})
    assert r.status_code in (403, 404)
    conv = S.conversation_for(db, lead, create=False)
    db.refresh(conv)
    assert not (S.memory_of(conv).get("home_visit") or {}).get("done")
