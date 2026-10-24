"""The joints between Wholesale stages that the 2026-10-24 workflow audit
found broken. Each test walks ONE test property across the joint through the
API and checks the record the next stage reads:

* booking a visit sets the seller's appointment (what the Morning board and
  the deal room read); moving / cancelling it follows; a double booking is one
* an offer made at the visit that stands goes into the deal's offer history
* a TEST deal never emails a signing link to anyone outside the workspace
* closing an assigned deal needs the assignment approval, like the stage does
"""
from app.models.models import Lead
from app.models.wholesale_models import WholesaleDeal, WholesaleOffer, WholesaleSellerProfile

ES = "/wholesale/evosense"


def ok(r, what=""):
    assert r.status_code in (200, 201), "%s: %s %s" % (what, r.status_code, r.text[:400])
    return r.json()


def _deal(client, H, street="77 Joint Test Ln"):
    """A TEST finding taken to a deal the way an operator does it."""
    pid = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": street, "city": "Dallas", "state": "TX", "zip_code": "75216", "county": "Dallas",
        "property_type": "single_family", "square_feet": 1400, "estimated_value": 210000,
        "signals": ["VACANT", "TAX_DELINQUENT"], "is_test": True}), "finding")["property_id"]
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={"name": "Test Seller Joint"}), "owner")
    ok(client.post(ES + "/properties/%s/contacts" % pid, headers=H,
                   json={"kind": "phone", "value": "2145550199", "person_name": "Test Seller Joint"}), "contact")
    ok(client.post(ES + "/properties/%s/reply" % pid, headers=H, json={
        "text": "Yes I'd sell, it's empty. Around $150,000, need to close fast.", "delivery": "manual_entry"}))
    pr = ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}), "promote")
    return pid, pr["deal_id"]


def _profile(db, deal_id):
    deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    return deal, (db.query(WholesaleSellerProfile)
                  .filter(WholesaleSellerProfile.lead_id == deal.seller_lead_id).first())


def test_booking_a_visit_sets_the_sellers_appointment_and_follows_changes(
        client, auth_headers, admin_auth_headers, db_session):
    pid, deal_id = _deal(client, auth_headers)
    body = {"starts_at": "2026-11-05T10:00:00-06:00", "deal_id": deal_id, "evosense_property_id": pid}
    a = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json=body), "book")
    db_session.expire_all()
    deal, prof = _profile(db_session, deal_id)
    assert a["deal_id"] == deal_id and a["lead_id"] == deal.seller_lead_id and a["is_test"] is True
    assert prof.appointment_status == "scheduled" and prof.appointment_at is not None
    # the same visit booked twice (double click / retry) is one appointment
    again = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json=body), "book again")
    assert again["id"] == a["id"]
    # moved: the seller's time moves with it
    ok(client.patch("/wholesale/appointments/%s" % a["id"], headers=admin_auth_headers,
                    json={"starts_at": "2026-11-06T14:00:00-06:00"}), "reschedule")
    db_session.expire_all()
    _, prof = _profile(db_session, deal_id)
    assert prof.appointment_status == "scheduled" and prof.appointment_at.day == 6
    # cancelled: the seller no longer shows a standing appointment
    ok(client.patch("/wholesale/appointments/%s" % a["id"], headers=admin_auth_headers,
                    json={"status": "cancelled"}), "cancel")
    db_session.expire_all()
    _, prof = _profile(db_session, deal_id)
    assert prof.appointment_status == "cancelled"


def test_an_offer_that_stands_at_the_visit_goes_into_the_deals_offers(
        client, auth_headers, admin_auth_headers, db_session):
    pid, deal_id = _deal(client, auth_headers, street="78 Joint Test Ln")
    a = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": "2026-11-05T10:00:00-06:00", "deal_id": deal_id, "evosense_property_id": pid}))
    o = ok(client.post("/wholesale/appointments/%s/offers" % a["id"], headers=admin_auth_headers,
                       json={"arv": 200000, "repairs": 30000, "offer": 90000}), "field offer")
    assert o["status"] == "within"
    rows = db_session.query(WholesaleOffer).filter(WholesaleOffer.deal_id == deal_id).all()
    assert len(rows) == 1 and float(rows[0].amount) == 90000 and rows[0].status == "presented"
    assert "field offer %s" % o["id"] in rows[0].notes
    deal = db_session.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    assert float(deal.proposed_offer) == 90000
    offers = ok(client.get("/wholesale/deals/%s/offers" % deal_id, headers=auth_headers))
    assert any(float(x["amount"]) == 90000 for x in (offers.get("offers") or offers.get("items") or offers))


def test_a_test_deal_never_emails_a_signing_link_outside_the_workspace(
        client, auth_headers, db_session):
    _, deal_id = _deal(client, auth_headers, street="79 Joint Test Ln")
    r = client.post("/wholesale/deals/%s/contracts/purchase_agreement/send" % deal_id, headers=auth_headers,
                    json={"values": {"seller_email": "someone.real@gmail.com", "price": 140000}})
    assert r.status_code == 409 and "test deal" in r.json()["detail"]
    assert "someone.real@gmail.com" in r.json()["detail"]
    # the preview (read-only) still works for the same deal
    ok(client.post("/wholesale/deals/%s/contracts/purchase_agreement/preview" % deal_id, headers=auth_headers,
                   json={"values": {"seller_email": "someone.real@gmail.com"}}), "preview")


def test_closing_an_assigned_deal_needs_the_assignment_approval(client, auth_headers, db_session):
    _, deal_id = _deal(client, auth_headers, street="80 Joint Test Ln")
    ok(client.patch("/wholesale/deals/%s/contract" % deal_id, headers=auth_headers, json={
        "contract_price": 140000, "contract_status": "signed", "contract_date": "2026-10-20"}), "contract")
    buyer = ok(client.post("/wholesale/buyers", headers=auth_headers, json={
        "company_name": "Joint Test Buyer", "email": "joint.buyer@example.com", "is_test": True}))
    ok(client.post("/wholesale/deals/%s/assign" % deal_id, headers=auth_headers, json={
        "buyer_id": buyer["id"], "buyer_price": 152000, "assignment_fee": 12000}), "assign")
    blocked = client.post("/wholesale/deals/%s/close" % deal_id, headers=auth_headers,
                          json={"closing_date": "2026-11-20"})
    assert blocked.status_code == 409 and "assignment" in blocked.json()["detail"]
    deal = db_session.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    assert deal.stage != "closed"
    aa = ok(client.post("/wholesale/deals/%s/approvals" % deal_id, headers=auth_headers,
                        json={"kind": "assignment", "amount": 12000}))
    ok(client.post("/wholesale/approvals/%s/decide" % aa["id"], headers=auth_headers, json={"approve": True}))
    ok(client.post("/wholesale/deals/%s/close" % deal_id, headers=auth_headers,
                   json={"closing_date": "2026-11-20"}), "close after approval")
    db_session.expire_all()
    assert db_session.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first().stage == "closed"
    assert db_session.query(Lead).count() >= 1
