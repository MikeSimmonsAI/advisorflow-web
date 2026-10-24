"""Issue #297 - add ONE homeowner by hand, then the whole inspection journey
from that record (no spreadsheet, no booking needed for an on-site visit)."""
import os

from app.models.models import BookingLink, CadenceState, Lead, Message
from app.models.roofing_models import RoofingInspection, RoofingOpportunity
from app.services.roofing import common as C
from tests.test_roofing_inspections import PNG, _photo
from tests.test_roofing_program import h, world  # noqa: F401 (fixture)

NEW = {"first_name": "Rosa", "last_name": "Delgado", "phone": "(214) 555-7781", "street_address": "4417 Bluebonnet Ln",
       "city": "Plano", "state": "tx", "zip_code": "75023", "roof_issue": "Leak over the kitchen after the storm",
       "service_type": "repair", "urgency": "high", "roof_age": 14, "source": "Phone call",
       "notes": "Gate code 2210"}


def _add(client, db, u, **kw):
    return client.post("/roofing-ops/leads", headers=h(db, u), json={**NEW, **kw})


def test_add_one_homeowner_is_a_real_roofing_lead_and_sends_nothing(client, world):
    db, rep = world["db"], world["a_rep"]
    before = (db.query(Message).count(), db.query(CadenceState).count(), db.query(BookingLink).count())
    r = _add(client, db, rep)
    assert r.status_code == 201, r.text
    lid = r.json()["lead_id"]
    l = db.get(Lead, lid)
    assert l.organization_id == world["a"].id and l.assigned_to_id == rep.id
    assert (l.street_address, l.city, l.state, l.zip_code) == ("4417 Bluebonnet Ln", "Plano", "TX", "75023")
    assert l.tier is None and l.source == "manual" and l.source_detail == "Phone call"
    assert not l.sms_consent                              # consent is never assumed
    f = C.lead_fields(l)                                  # the importer's own field names
    assert f["roof_issue"].startswith("Leak") and f["service_type"] == "repair" and f["urgency"] == "high"
    assert f["roof_age"] == 14
    assert (db.query(Message).count(), db.query(CadenceState).count(), db.query(BookingLink).count()) == before
    assert db.query(RoofingOpportunity).filter(RoofingOpportunity.lead_id == lid).count() == 1
    # shows up in the list, in search, and in the record (with the note)
    items = client.get("/roofing-ops/leads?q=bluebonnet", headers=h(db, rep)).json()["items"]
    assert [i["lead_id"] for i in items] == [lid]
    ws = client.get("/roofing-ops/leads/%s" % lid, headers=h(db, rep)).json()
    assert ws["lead"]["address"] and ws["roofing"]["roof_issue"] and ws["notes"][0]["body"] == "Gate code 2210"
    # another company never sees it
    assert client.get("/roofing-ops/leads/%s" % lid, headers=h(db, world["b_owner"])).status_code == 404
    assert not client.get("/roofing-ops/leads?q=bluebonnet", headers=h(db, world["b_owner"])).json()["items"]


def test_possible_duplicate_is_shown_not_merged(client, world):
    db, rep = world["db"], world["a_rep"]
    first = _add(client, db, rep).json()["lead_id"]
    r = _add(client, db, rep, phone=None, email=None)      # same street + ZIP
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "POSSIBLE_DUPLICATE" and d["duplicates"][0]["lead_id"] == first
    r = _add(client, db, rep, phone=None, email=None, allow_duplicate=True)
    assert r.status_code == 201 and r.json()["lead_id"] != first
    # the other company's identical homeowner is not a duplicate of ours
    assert _add(client, db, world["b_owner"]).status_code == 201


def test_rules_minimum_details_choices_and_assignment(client, world):
    db, rep, owner = world["db"], world["a_rep"], world["a_owner"]
    assert client.post("/roofing-ops/leads", headers=h(db, rep), json={"first_name": "No Contact"}).status_code == 400
    assert _add(client, db, rep, urgency="whenever", street_address="1 X St").status_code == 400
    assert _add(client, db, rep, owner_id=world["a_rep2"].id, street_address="2 X St").status_code == 403
    r = _add(client, db, owner, owner_id=world["a_rep2"].id, street_address="3 X St", phone=None)
    assert r.status_code == 201 and db.get(Lead, r.json()["lead_id"]).assigned_to_id == world["a_rep2"].id
    assert _add(client, db, owner, owner_id=world["b_owner"].id, street_address="4 X St").status_code == 404


def test_journey_add_start_unscheduled_document_complete_estimate_won(client, world):
    db, rep = world["db"], world["a_rep"]
    lid = _add(client, db, rep).json()["lead_id"]
    # on site now: an unscheduled inspection - no booking is invented
    ins = client.post("/roofing-ops/inspections", headers=h(db, rep), json={"lead_id": lid}).json()
    assert ins["scheduled_at"] is None and ins["booking_link_id"] is None and ins["started_at"]
    assert db.query(BookingLink).filter(BookingLink.lead_id == lid).count() == 0
    # opening it again continues the same draft
    assert client.post("/roofing-ops/inspections", headers=h(db, rep), json={"lead_id": lid}).json()["id"] == ins["id"]
    iid = ins["id"]
    client.patch("/roofing-ops/inspections/%s" % iid, headers=h(db, rep), json={
        "roof_condition": "poor", "severity": "moderate", "damage": ["leak", "flashing"],
        "notes": "Failed flashing at the chimney.", "recommendation": "repair", "insurance_claim": "not_discussed"})
    _photo(client, db, rep, iid, data=PNG + os.urandom(4))
    r = client.post("/roofing-ops/inspections/%s/complete" % iid, headers=h(db, rep)).json()
    assert r["status"] == "completed" and r["report_number"] and len(r["photos"]) == 1
    ws = client.get("/roofing-ops/leads/%s" % lid, headers=h(db, rep)).json()
    assert ws["inspections"][0]["id"] == iid and ws["inspections"][0]["status"] == "completed"
    assert ws["opportunity"]["stage"] == "inspected"
    hand = lambda b: client.post("/roofing-ops/inspections/%s/handoff" % iid, headers=h(db, rep), json=b)  # noqa: E731
    hand({"action": "prepare_estimate", "estimate_amount": 4200})
    hand({"action": "record_estimate_given"})
    r = hand({"action": "mark_won"}).json()
    assert r["opportunity"]["stage"] == "won"
    assert db.query(RoofingInspection).filter(RoofingInspection.lead_id == lid).count() == 1
