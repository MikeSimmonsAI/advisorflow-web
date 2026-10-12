"""The purchase agreement from the kitchen table, and the owner-permission
link from the rep's phone. Guard rails: the price is the rep's standing
offer, the company signer is an admin (never the rep), and a rep can only
do this on a property from their own appointment."""
from app.models.models import User


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:400])
    return r.json()


def _setup(client, admin_h, rep, sample_org, db_session):
    from app.models.evosense_models import EvoSenseProperty
    ok(client.post("/wholesale/team/members", headers=admin_h,
                   json={"user_id": rep.id, "role": "field_specialist", "confirm_lock": True}))
    prop = EvoSenseProperty(organization_id=sample_org.id, street_address="88 Contract Way", city="Dallas",
                            state="TX", zip_code="75204", county="Dallas", is_test=True)
    db_session.add(prop); db_session.commit()
    a = ok(client.post("/wholesale/appointments", headers=admin_h, json={
        "starts_at": "2026-10-12T10:00:00-05:00", "evosense_property_id": prop.id, "assigned_to_id": rep.id}))
    return a, prop


def test_no_agreement_without_a_standing_offer(client, admin_auth_headers, auth_headers, sample_advisor,
                                               sample_org, db_session):
    a, _ = _setup(client, admin_auth_headers, sample_advisor, sample_org, db_session)
    r = client.post("/wholesale/appointments/%s/agreement" % a["id"], headers=auth_headers, json={})
    assert r.status_code == 409 and "offer" in r.json()["detail"].lower()
    # an offer still waiting for approval does not count
    ok(client.post("/wholesale/appointments/%s/offers" % a["id"], headers=auth_headers,
                   json={"arv": 200000, "repairs": 30000, "offer": 120000}))
    assert client.post("/wholesale/appointments/%s/agreement" % a["id"], headers=auth_headers,
                       json={}).status_code == 409


def test_preview_uses_the_offer_and_an_admin_signs(client, admin_auth_headers, auth_headers, sample_advisor,
                                                   sample_org, db_session):
    a, _ = _setup(client, admin_auth_headers, sample_advisor, sample_org, db_session)
    ok(client.post("/wholesale/appointments/%s/offers" % a["id"], headers=auth_headers,
                   json={"arv": 200000, "repairs": 30000, "offer": 90000}))
    p = ok(client.post("/wholesale/appointments/%s/agreement" % a["id"], headers=auth_headers, json={
        "seller_name": "Pat Q Seller", "seller_email": "pat@example.test"}))
    admin = db_session.query(User).filter(User.role == "org_admin").first()
    assert p["price"] == 90000 and p["company_signer"] == (admin.full_name or admin.email)
    assert "$90,000" in p["html"] and "1101.0045" in p["html"]
    assert sample_advisor.full_name not in p["html"]                 # the rep never signs for the company
    assert any("Title company" in m for m in p["missing"]) and "values" not in p
    r = client.post("/wholesale/appointments/%s/agreement" % a["id"], headers=auth_headers,
                    json={"action": "send", "seller_name": "Pat Q Seller"})
    assert r.status_code == 409 and "Fill these in first" in r.json()["detail"]


def test_the_rep_can_send_the_permission_link_for_their_own_property_only(
        client, admin_auth_headers, auth_headers, sample_advisor, sample_org, db_session):
    from app.models.evosense_models import EvoSenseProperty
    a, prop = _setup(client, admin_auth_headers, sample_advisor, sample_org, db_session)
    r = client.post("/wholesale/evosense/properties/%s/permission-link" % prop.id, headers=auth_headers,
                    json={"phone": "(214) 555-0142", "owner_name": "Pat Seller"})
    assert r.status_code != 403, r.text[:300]
    other = EvoSenseProperty(organization_id=sample_org.id, street_address="1 Not Mine", city="Dallas",
                             state="TX", zip_code="75204", is_test=True)
    db_session.add(other); db_session.commit()
    r2 = client.post("/wholesale/evosense/properties/%s/permission-link" % other.id, headers=auth_headers,
                     json={"phone": "(214) 555-0142"})
    assert r2.status_code == 403



def test_sending_moves_it_into_deal_operations_and_uses_the_normal_send(
        client, admin_auth_headers, auth_headers, sample_advisor, sample_org, db_session):
    a, prop = _setup(client, admin_auth_headers, sample_advisor, sample_org, db_session)
    ok(client.post("/wholesale/appointments/%s/offers" % a["id"], headers=auth_headers,
                   json={"arv": 200000, "repairs": 30000, "offer": 90000}))
    out = ok(client.post("/wholesale/appointments/%s/agreement" % a["id"], headers=auth_headers, json={
        "action": "send", "seller_name": "Pat Q Seller", "seller_email": "pat@example.test",
        "earnest_money": 1000, "option_days": 10, "title_company": "Lone Star Title",
        "closing_date": "2026-11-20"}))
    db_session.refresh(prop)
    assert prop.promoted_deal_id and out["deal_id"] == prop.promoted_deal_id
    # Whether it went out depends on an e-sign service being connected; either
    # way the answer says so plainly and nothing pretends it was sent.
    assert isinstance(out["sent"], bool) and out["message"]
