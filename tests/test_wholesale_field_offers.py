"""Field offers: the rep sees only the most they may offer (never the fee or
investor %), offers inside the limit stand, offers above it wait for an
admin, and the admin's answer goes back to the rep."""
from app.models.models import Notification, NotificationType, User


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:300])
    return r.json()


def _setup(client, admin_h, rep, manager):
    ok(client.post("/wholesale/team/members", headers=admin_h,
                   json={"user_id": manager.id, "role": "manager", "confirm_lock": True}))
    ok(client.post("/wholesale/team/members", headers=admin_h,
                   json={"user_id": rep.id, "role": "field_specialist", "reports_to_user_id": manager.id}))
    return ok(client.post("/wholesale/appointments", headers=admin_h, json={
        "starts_at": "2026-10-12T10:00:00-05:00", "address": "44 Offer Rd, Dallas, TX",
        "assigned_to_id": rep.id}))


def test_the_rep_sees_only_their_max_offer(client, admin_auth_headers, auth_headers, sample_advisor, second_advisor):
    a = _setup(client, admin_auth_headers, sample_advisor, second_advisor)
    q = ok(client.post("/wholesale/appointments/%s/offer-quote" % a["id"], headers=auth_headers,
                       json={"arv": "200,000", "repairs": "30000"}))
    # 200k x 70% - 30k repairs - 6k costs (3%) - 10k fee = 94k
    assert q["max_offer"] == 94000
    assert "mao" not in q and "offer_limit_pct" not in q and "fee" not in str(q)
    admin_view = ok(client.post("/wholesale/appointments/%s/offer-quote" % a["id"], headers=admin_auth_headers,
                                json={"arv": 200000, "repairs": 30000}))
    assert admin_view["mao"] == 94000 and admin_view["offer_limit_pct"] == 100


def test_inside_the_limit_stands_above_it_waits_for_an_admin(
        client, admin_auth_headers, auth_headers, sample_advisor, second_advisor, db_session):
    a = _setup(client, admin_auth_headers, sample_advisor, second_advisor)
    base = "/wholesale/appointments/%s/offers" % a["id"]
    fine = ok(client.post(base, headers=auth_headers, json={"arv": 200000, "repairs": 30000, "offer": 90000}))
    assert fine["status"] == "within" and "mao" not in fine
    high = ok(client.post(base, headers=auth_headers, json={"arv": 200000, "repairs": 30000, "offer": 99000,
                                                            "note": "Seller won't go below 99"}))
    assert high["status"] == "pending"
    admin = db_session.query(User).filter(User.role == "org_admin").first()
    bell = db_session.query(Notification).filter(Notification.user_id == admin.id,
                                                 Notification.type == NotificationType.WHOLESALE_OFFER).all()
    assert bell and "$99,000" in bell[0].message
    # The rep cannot approve their own, nor see the queue
    assert client.get("/wholesale/appointments/offers/pending", headers=auth_headers).status_code == 403
    assert client.post("/wholesale/appointments/offers/%s/decide" % high["id"], headers=auth_headers,
                       json={"approve": True}).status_code == 403
    queue = ok(client.get("/wholesale/appointments/offers/pending", headers=admin_auth_headers))["items"]
    assert [x["id"] for x in queue] == [high["id"]] and queue[0]["mao"] == 94000
    done = ok(client.post("/wholesale/appointments/offers/%s/decide" % high["id"], headers=admin_auth_headers,
                          json={"approve": True, "note": "OK, go to 99"}))
    assert done["status"] == "approved"
    told = db_session.query(Notification).filter(Notification.user_id == sample_advisor.id,
                                                 Notification.type == NotificationType.WHOLESALE_OFFER).all()
    assert told and "approved" in told[0].message
    again = client.post("/wholesale/appointments/offers/%s/decide" % high["id"], headers=admin_auth_headers,
                        json={"approve": False})
    assert again.status_code == 409
    mine = ok(client.get(base, headers=auth_headers))["items"]
    assert {x["status"] for x in mine} == {"within", "approved"} and all("mao" not in x for x in mine)


def test_the_limit_is_yours_to_set(client, admin_auth_headers, auth_headers, sample_advisor, second_advisor):
    a = _setup(client, admin_auth_headers, sample_advisor, second_advisor)
    assert client.put("/wholesale/team/rules", headers=auth_headers, json={"offer_limit_pct": 105}).status_code == 403
    assert ok(client.put("/wholesale/team/rules", headers=admin_auth_headers,
                         json={"offer_limit_pct": 90}))["offer_limit_pct"] == 90
    q = ok(client.post("/wholesale/appointments/%s/offer-quote" % a["id"], headers=auth_headers,
                       json={"arv": 200000, "repairs": 30000}))
    assert q["max_offer"] == 84500                       # 90% of 94k = 84.6k, rounded down to $500
    assert client.put("/wholesale/team/rules", headers=admin_auth_headers,
                      json={"offer_limit_pct": 300}).status_code == 422


def test_a_rep_can_raise_their_opening_offer_by_ten_percent_at_most(
        client, admin_auth_headers, auth_headers, sample_advisor, second_advisor, db_session):
    a = _setup(client, admin_auth_headers, sample_advisor, second_advisor)
    rules = ok(client.get("/wholesale/team/rules", headers=admin_auth_headers))
    assert rules["raise_limit_pct"] == 10 and "10%" in rules["explained"]
    quote = lambda: ok(client.post("/wholesale/appointments/%s/offer-quote" % a["id"], headers=auth_headers,
                                   json={"arv": 300000, "repairs": 0}))
    # 300k x 70% - 9k costs (3%) - 10k fee = 191k; no opening offer yet
    q = quote()
    assert q["max_offer"] == 191000 and q["opening_offer"] is None and not q["capped_by_raise"]
    base = "/wholesale/appointments/%s/offers" % a["id"]
    send = lambda n: ok(client.post(base, headers=auth_headers, json={"arv": 300000, "repairs": 0, "offer": n}))
    assert send(150000)["status"] == "within"                   # the opening offer
    q = quote()
    assert q["opening_offer"] == 150000 and q["max_offer"] == 165000 and q["capped_by_raise"]
    assert "mao" not in q
    assert send(165000)["status"] == "within"                   # +10%: still theirs to make
    assert send(166000)["status"] == "pending"                  # more than that: Mike decides
    admin = db_session.query(User).filter(User.role == "org_admin").first()
    bell = [n.message for n in db_session.query(Notification).filter(
        Notification.user_id == admin.id, Notification.type == NotificationType.WHOLESALE_OFFER).all()]
    assert any("10% above their $150,000 opening offer" in m for m in bell)
    # Mike can loosen or drop the raise rule
    ok(client.put("/wholesale/team/rules", headers=admin_auth_headers,
                  json={"offer_limit_pct": 100, "raise_limit_pct": 100}))
    assert quote()["max_offer"] == 191000
    ok(client.put("/wholesale/team/rules", headers=admin_auth_headers,
                  json={"offer_limit_pct": 100, "raise_limit_pct": 0}))
    assert quote()["max_offer"] == 150000                       # no raises without asking
    assert client.put("/wholesale/team/rules", headers=admin_auth_headers,
                      json={"offer_limit_pct": 100, "raise_limit_pct": 150}).status_code == 422
