"""The exception queue: a VA sees ONLY what is assigned to them; the owner sees
escalations; every change is audited; nothing crosses organizations."""
import pytest

from app.models.models import Organization, User, UserCapabilityGrant
from app.models.wholesale_models import WholesaleBuyer, WholesaleEvent, WholesaleWorkException
from app.services.auth_service import create_access_token, hash_password


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:400])
    return r.json()


def _user(db, org, role, email, grant=False):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("Pass12345!"),
             full_name=email.split("@")[0], role=role, must_change_password=False)
    db.add(u)
    db.commit()
    if grant:
        db.add(UserCapabilityGrant(user_id=u.id, organization_id=org.id, scope_type="customer_org",
                                   scope_id=org.id, capability="exception_queue_work", is_active=True))
        db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


@pytest.fixture
def people(db_session, sample_org):
    admin = _user(db_session, sample_org, "org_admin", "owner@ex.test")
    va = _user(db_session, sample_org, "advisor", "va@ex.test", grant=True)
    va2 = _user(db_session, sample_org, "advisor", "va2@ex.test", grant=True)
    plain = _user(db_session, sample_org, "advisor", "plain@ex.test")
    return admin, va, va2, plain


@pytest.fixture
def seeded(client, db_session, sample_org, people):
    admin = people[0]
    ok(client.post("/wholesale/properties", headers=_h(db_session, admin),
                   json={"street_address": "12 Nobody Owns St", "city": "Dallas", "state": "TX",
                         "zip_code": "75215"}))
    db_session.add(WholesaleBuyer(organization_id=sample_org.id, company_name="No Box Capital",
                                  email="nb@buyer.test"))
    db_session.commit()
    return ok(client.post("/wholesale/exceptions/sweep", headers=_h(db_session, admin)))


def test_sweep_raises_what_the_data_shows_and_is_idempotent(client, db_session, people, seeded):
    admin = people[0]
    assert seeded["raised"] == {"verify_owner": 1, "buyer_criteria_verification": 1}
    again = ok(client.post("/wholesale/exceptions/sweep", headers=_h(db_session, admin)))
    assert again["raised"] == {}
    items = ok(client.get("/wholesale/exceptions?scope=all", headers=_h(db_session, admin)))["items"]
    owner = next(i for i in items if i["kind"] == "verify_owner")
    assert owner["subject"]["label"].startswith("12 Nobody Owns St")


def test_a_va_sees_only_their_own_assigned_exceptions(client, db_session, people, seeded):
    admin, va, va2, plain = people
    items = ok(client.get("/wholesale/exceptions?scope=all", headers=_h(db_session, admin)))["items"]
    first, second = items[0], items[1]
    ok(client.post("/wholesale/exceptions/%s/assign" % first["id"], headers=_h(db_session, admin),
                   json={"assigned_to_id": va.id}))
    ok(client.post("/wholesale/exceptions/%s/assign" % second["id"], headers=_h(db_session, admin),
                   json={"assigned_to_id": va2.id}))
    mine = ok(client.get("/wholesale/exceptions?scope=all", headers=_h(db_session, va)))
    assert mine["manager"] is False and mine["scope"] == "mine"
    assert [i["id"] for i in mine["items"]] == [first["id"]]
    # the other VA's item is invisible, not merely read-only
    r = client.post("/wholesale/exceptions/%s/resolve" % second["id"], headers=_h(db_session, va),
                    json={"outcome": "complete"})
    assert r.status_code == 404
    # a VA cannot assign, sweep or raise
    assert client.post("/wholesale/exceptions/%s/assign" % first["id"], headers=_h(db_session, va),
                       json={"assigned_to_id": va.id}).status_code == 403
    assert client.post("/wholesale/exceptions/sweep", headers=_h(db_session, va)).status_code == 403
    # someone with no grant has no queue at all
    assert client.get("/wholesale/exceptions", headers=_h(db_session, plain)).status_code == 403


def test_outcomes_and_escalation_back_to_the_owner(client, db_session, people, seeded):
    admin, va, _, _ = people
    items = ok(client.get("/wholesale/exceptions?scope=all", headers=_h(db_session, admin)))["items"]
    a, b = items[0]["id"], items[1]["id"]
    for x in (a, b):
        ok(client.post("/wholesale/exceptions/%s/assign" % x, headers=_h(db_session, admin),
                       json={"assigned_to_id": va.id}))
    r = client.post("/wholesale/exceptions/%s/resolve" % a, headers=_h(db_session, va),
                    json={"outcome": "unable_to_verify"})
    assert r.status_code == 422                                 # say what you found
    done = ok(client.post("/wholesale/exceptions/%s/resolve" % a, headers=_h(db_session, va),
                          json={"outcome": "unable_to_verify", "note": "County site down; no deed found"}))
    assert done["status"] == "unable_to_verify" and done["resolved_at"]
    esc = ok(client.post("/wholesale/exceptions/%s/resolve" % b, headers=_h(db_session, va),
                         json={"outcome": "escalate", "note": "Two people claim to own it"}))
    assert esc["status"] == "escalated" and esc["assigned_to_id"] is None
    assert ok(client.get("/wholesale/exceptions?scope=all", headers=_h(db_session, va)))["items"] == []
    owner_view = ok(client.get("/wholesale/exceptions?scope=escalated", headers=_h(db_session, admin)))
    assert [i["id"] for i in owner_view["items"]] == [b]
    actions = {e.action for e in db_session.query(WholesaleEvent).all()}
    assert {"exception.raised", "exception.assigned", "exception.resolved", "exception.updated"} <= actions
    bad = client.post("/wholesale/exceptions/%s/resolve" % b, headers=_h(db_session, admin),
                      json={"outcome": "made_up"})
    assert bad.status_code == 422


def test_nothing_crosses_organizations(client, db_session, people, seeded):
    admin = people[0]
    other = Organization(name="Other Exceptions Org", slug="other-exc", plan="enterprise")
    db_session.add(other)
    db_session.commit()
    stranger = _user(db_session, other, "org_admin", "boss@other-exc.test")
    ex_id = db_session.query(WholesaleWorkException).first().id
    r = client.get("/wholesale/exceptions?scope=all", headers=_h(db_session, stranger))
    if r.status_code == 200:
        assert r.json()["items"] == []
    for url, body in (("/wholesale/exceptions/%s/resolve" % ex_id, {"outcome": "complete"}),
                      ("/wholesale/exceptions/%s/assign" % ex_id, {"assigned_to_id": None})):
        assert client.post(url, headers=_h(db_session, stranger), json=body).status_code in (403, 404)
    # and an admin cannot hand an item to someone outside the workspace
    r = client.post("/wholesale/exceptions/%s/assign" % ex_id, headers=_h(db_session, admin),
                    json={"assigned_to_id": stranger.id})
    assert r.status_code == 422


def test_sweep_feeds_callbacks_and_title_flags(client, db_session, sample_org, people):
    from app.models.models import Lead, Reply, ReplyClassification
    from app.models.wholesale_models import WholesaleSellerProfile
    from app.services.evosense import hunt as HU
    admin = people[0]
    prop = ok(client.post("/wholesale/properties", headers=_h(db_session, admin),
                          json={"street_address": "8 Callback Ct", "city": "Dallas", "state": "TX",
                                "zip_code": "75215"}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"], headers=_h(db_session, admin),
                   json={"first_name": "Cal", "last_name": "Back", "phone": "2145550166"}))
    prof = db_session.query(WholesaleSellerProfile).filter(
        WholesaleSellerProfile.property_id == prop["id"]).one()
    db_session.add(Reply(lead_id=prof.lead_id, body="call me after 5 please",
                         classification=ReplyClassification.CALLBACK))
    db_session.commit()
    HU.add_manual(db_session, sample_org.id, {"street_address": "9 Estate Way", "city": "Dallas",
                                              "state": "TX", "zip_code": "75215",
                                              "owner_name": "JONES MARY ESTATE OF"}, user=admin)
    db_session.commit()
    out = ok(client.post("/wholesale/exceptions/sweep", headers=_h(db_session, admin)))
    assert out["raised"].get("seller_callback_requested") == 1
    assert out["raised"].get("title_ownership_anomaly", 0) >= 1
    items = ok(client.get("/wholesale/exceptions?scope=all", headers=_h(db_session, admin)))["items"]
    cb = next(i for i in items if i["kind"] == "seller_callback_requested")
    assert cb["priority"] == 10 and "call me after 5" in cb["detail"] and cb["subject"]["label"] == "Cal Back"
    title = next(i for i in items if i["kind"] == "title_ownership_anomaly")
    assert title["subject"]["label"].upper().startswith("9 ESTATE")
    assert ok(client.post("/wholesale/exceptions/sweep", headers=_h(db_session, admin)))["raised"] == {}
