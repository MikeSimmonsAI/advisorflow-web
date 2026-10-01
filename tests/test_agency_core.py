# -*- coding: utf-8 -*-
"""Max Life Command backend: entitlement, tenant isolation, distribution, assignment lifecycle."""
import json
from datetime import datetime, timedelta

from agency_support import agency_feature, agent_profile, h, lead, org, user, world  # noqa: F401
from app.models.agency_models import AgencyAssignment
from app.models.models import AuditLogEntry, Lead


def test_feature_required_403_without_entitlement(client, db_session):
    from agency_support import mount
    mount()
    o = org(db_session, "No Agency", features=("leads",))
    u = user(db_session, o, "org_admin", "noent")
    for path in ("/agency/prospects", "/agency/summary", "/agency/agents", "/agency/attention"):
        assert client.get(path, headers=h(db_session, u)).status_code in (402, 403), path  # platform answers 402 "not entitled"
    assert client.post("/agency/ask", headers=h(db_session, u), json={"question": "x"}).status_code in (402, 403)


def test_tenant_isolation_lists_and_ids(client, world):
    db = world["db"]
    mine = lead(db, world["org"], "Mine", needs=["family_protection"], intent="high")
    theirs = lead(db, world["other"], "Theirs", needs=["family_protection"], intent="high")
    hm = h(db, world["mgr"])
    ids = [r["id"] for r in client.get("/agency/prospects", headers=hm).json()["items"]]
    assert mine.id in ids and theirs.id not in ids
    for path in ("/agency/prospects/%s", "/agency/prospects/%s/brief", "/agency/prospects/%s/copilot",
                 "/agency/prospects/%s/recommendation"):
        assert client.get(path % theirs.id, headers=hm).status_code == 404, path
    assert client.post("/agency/prospects/%s/assign" % theirs.id, headers=hm,
                       json={"agent_id": world["maya"].id}).status_code == 404
    # cross-org agent id
    assert client.post("/agency/prospects/%s/assign" % mine.id, headers=hm,
                       json={"agent_id": world["fadv"].id}).status_code == 404
    agents = [a["user_id"] for a in client.get("/agency/agents", headers=hm).json()["items"]]
    assert world["fadv"].id not in agents and world["maya"].id in agents
    # foreign application created in the other org is invisible
    hf = h(db, world["fmgr"])
    r = client.post("/agency/applications", headers=hf, json={"prospect_id": theirs.id,
                                                              "agent_id": world["fadv"].id})
    assert r.status_code == 201
    assert client.get("/agency/applications/%s" % r.json()["id"], headers=hm).status_code == 404
    assert client.get("/agency/applications", headers=hm).json()["total"] == 0


def test_advisor_sees_only_own_prospects(client, world):
    db = world["db"]
    a = lead(db, world["org"], "Own", owner=world["maya"])
    b = lead(db, world["org"], "Other", owner=world["ben"])
    ids = [r["id"] for r in client.get("/agency/prospects", headers=h(db, world["maya"])).json()["items"]]
    assert a.id in ids and b.id not in ids
    assert client.get("/agency/prospects/%s" % b.id, headers=h(db, world["maya"])).status_code == 404


def test_recommendation_reasons_only_from_stored_data(client, world):
    db = world["db"]
    l = lead(db, world["org"], "Fam", state="TX", needs=["family_protection"])
    r = client.get("/agency/prospects/%s/recommendation" % l.id, headers=h(db, world["mgr"])).json()
    assert r["recommended"]["agent_id"] == world["maya"].id
    reasons = " | ".join(r["recommended"]["reasons"])
    assert "TX" in reasons and "Family Protection" in reasons and "12 min" in reasons
    # Ben has no recorded response history -> reported unavailable, never invented
    assert any("response performance" in u and "Ben" in u for u in r["unavailable_factors"])
    assert any("conversion history" in u for u in r["unavailable_factors"])
    ben = next(c for c in r["candidates"] if c["agent_id"] == world["ben"].id)
    assert not any("response" in x for x in ben["reasons"])
    # out-of-state prospect: jurisdiction blocker from stored list
    l2 = lead(db, world["org"], "Cali", state="CA")
    r2 = client.get("/agency/prospects/%s/recommendation" % l2.id, headers=h(db, world["mgr"])).json()
    assert r2["recommended"] is None
    assert all("CA" in " ".join(c["blockers"]) for c in r2["candidates"])
    # no state -> unavailable factor, not a guess
    l3 = lead(db, world["org"], "Nostate", state=None)
    r3 = client.get("/agency/prospects/%s/recommendation" % l3.id, headers=h(db, world["mgr"])).json()
    assert any("prospect state not recorded" in u for u in r3["unavailable_factors"])


def test_assign_accept(client, world):
    db = world["db"]
    l = lead(db, world["org"], "Acc", needs=["family_protection"])
    r = client.post("/agency/prospects/%s/assign" % l.id, headers=h(db, world["mgr"]),
                    json={"agent_id": world["maya"].id, "note": "hot"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["state"] == "offered" and a["expires_at"].endswith("Z") and a["reasons"]
    # advisor may not assign
    assert client.post("/agency/prospects/%s/assign" % l.id, headers=h(db, world["maya"]),
                       json={"agent_id": world["maya"].id}).status_code == 403
    # another agent cannot accept someone else's offer
    assert client.post("/agency/assignments/%s/accept" % a["id"], headers=h(db, world["ben"])).status_code == 404
    r = client.post("/agency/assignments/%s/accept" % a["id"], headers=h(db, world["maya"]))
    assert r.status_code == 200 and r.json()["state"] == "accepted"
    db.expire_all()
    assert db.query(Lead).get(l.id).assigned_to_id == world["maya"].id
    p = client.get("/agency/prospects/%s" % l.id, headers=h(db, world["mgr"])).json()
    assert p["assignment_state"] == "accepted" and p["assigned_agent"]["id"] == world["maya"].id
    assert db.query(AuditLogEntry).filter(AuditLogEntry.action == "agency.assignment.accepted").count() == 1


def test_decline_reassigns_to_next_eligible(client, world):
    db = world["db"]
    l = lead(db, world["org"], "Dec", needs=["family_protection"])
    a = client.post("/agency/prospects/%s/assign" % l.id, headers=h(db, world["mgr"]),
                    json={"agent_id": world["maya"].id}).json()
    r = client.post("/agency/assignments/%s/decline" % a["id"], headers=h(db, world["maya"]),
                    json={"reason": "full calendar"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["declined"]["state"] == "declined" and body["declined"]["decline_reason"] == "full calendar"
    assert body["next"]["state"] == "offered" and body["next"]["agent"]["id"] == world["ben"].id
    assert body["next"]["attempt"] == 2


def test_timeout_sweep_reassigns_then_escalates_with_audit(client, world):
    db = world["db"]
    hm = h(db, world["mgr"])
    client.put("/agency/distribution/config", headers=hm,
               json={"acceptance_timeout_minutes": 5, "escalation_user_id": world["mgr"].id})
    l = lead(db, world["org"], "Slow", needs=["family_protection"])
    a = client.post("/agency/prospects/%s/assign" % l.id, headers=hm, json={"agent_id": world["maya"].id}).json()
    row = db.query(AgencyAssignment).get(a["id"])
    assert (row.expires_at - row.offered_at) == timedelta(minutes=5)
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    assert client.post("/agency/assignments/sweep", headers=h(db, world["maya"])).status_code == 403
    s = client.post("/agency/assignments/sweep", headers=hm).json()
    assert s["timed_out"] == 1 and s["results"][0]["next"]["agent_user_id"] == world["ben"].id
    # Ben times out too -> nobody left eligible -> escalated to configured manager
    nxt = db.query(AgencyAssignment).get(s["results"][0]["next"]["assignment_id"])
    nxt.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    s2 = client.post("/agency/assignments/sweep", headers=hm).json()
    assert s2["results"][0]["next"]["state"] == "escalated"
    assert s2["results"][0]["next"]["agent_user_id"] == world["mgr"].id
    acts = [e.action for e in db.query(AuditLogEntry).filter(AuditLogEntry.organization_id == world["org"].id)]
    assert acts.count("agency.assignment.timed_out") == 2 and "agency.assignment.escalated" in acts
    p = client.get("/agency/prospects?assignment=escalated", headers=hm).json()
    assert [x["id"] for x in p["items"]] == [l.id]


def test_config_put_manager_only_and_validates(client, world):
    db = world["db"]
    assert client.put("/agency/distribution/config", headers=h(db, world["maya"]),
                      json={"stalled_days": 3}).status_code == 403
    assert client.put("/agency/distribution/config", headers=h(db, world["mgr"]),
                      json={"factors_enabled": ["astrology"]}).status_code == 422
    assert client.put("/agency/distribution/config", headers=h(db, world["mgr"]),
                      json={"escalation_user_id": world["fmgr"].id}).status_code == 404
    r = client.put("/agency/distribution/config", headers=h(db, world["mgr"]), json={"stalled_days": 3})
    assert r.json()["stalled_days"] == 3


def test_agent_profile_put_and_workload(client, world):
    db = world["db"]
    for i in range(3):
        lead(db, world["org"], "W%d" % i, owner=world["ben"])
    r = client.put("/agency/agents/%s/profile" % world["ben"].id, headers=h(db, world["mgr"]),
                   json={"max_active": 4, "jurisdictions": ["tx", "ok"]})
    assert r.status_code == 200
    a = r.json()
    assert a["active_count"] == 3 and a["workload_pct"] == 75 and a["jurisdictions"] == ["OK", "TX"]
    assert a["avg_response_minutes"] is None
    assert client.put("/agency/agents/%s/profile" % world["fadv"].id, headers=h(db, world["mgr"]),
                      json={"max_active": 4}).status_code == 404


def test_accept_and_sweep_race_only_one_wins(client, world):
    """The agent accepts while the sweep is timing the same offer out (both read
    state=offered). The conditional claim lets exactly one settle it."""
    from app.services.agency import distribution as dist
    db = world["db"]
    l = lead(db, world["org"], "Race", needs=["family_protection"])
    a = client.post("/agency/prospects/%s/assign" % l.id, headers=h(db, world["mgr"]),
                    json={"agent_id": world["maya"].id}).json()
    row = db.query(AgencyAssignment).get(a["id"])
    stale = AgencyAssignment(id=row.id, state="offered")     # the sweep's stale copy
    assert dist._claim(db, row, "accepted") is True
    assert dist._claim(db, stale, "timed_out") is False
    db.commit()
    db.expire_all()
    assert db.query(AgencyAssignment).get(a["id"]).state == "accepted"
    # A second accept / a decline after acceptance is a clean 409, not a re-offer.
    hm = h(db, world["maya"])
    assert client.post("/agency/assignments/%s/accept" % a["id"], headers=hm).status_code == 409
    assert client.post("/agency/assignments/%s/decline" % a["id"], headers=hm, json={}).status_code == 409
    assert db.query(AgencyAssignment).filter(AgencyAssignment.lead_id == l.id).count() == 1


def test_agent_can_open_a_prospect_offered_to_them_and_accept_it(client, world):
    """Click-through bug: the Accept button lives on the prospect page, but the
    lead is assigned only on accept, so the offered agent got 404 there and no
    advisor could ever accept an offer in the app."""
    db = world["db"]
    l = lead(db, world["org"], "Offered", needs=["family_protection"])
    a = client.post("/agency/prospects/%s/assign" % l.id, headers=h(db, world["mgr"]),
                    json={"agent_id": world["maya"].id}).json()
    hm, hb = h(db, world["maya"]), h(db, world["ben"])
    for path in ("", "/brief", "/copilot"):
        assert client.get("/agency/prospects/%s%s" % (l.id, path), headers=hm).status_code == 200, path
        assert client.get("/agency/prospects/%s%s" % (l.id, path), headers=hb).status_code == 404, path
    got = client.get("/agency/prospects/%s" % l.id, headers=hm).json()
    assert got["assignment_state"] == "offered" and got["status"] and "email" in got
    # The open offer opens READ only - not writes.
    assert client.patch("/agency/prospects/%s/profile" % l.id, headers=hm,
                        json={"preferred_contact": "email"}).status_code == 404
    assert client.post("/agency/assignments/%s/accept" % a["id"], headers=hm).status_code == 200
    assert client.get("/agency/prospects/%s" % l.id, headers=hm).status_code == 200


def test_a_declined_offer_no_longer_opens_the_prospect(client, world):
    db = world["db"]
    l = lead(db, world["org"], "Gone", needs=["family_protection"])
    a = client.post("/agency/prospects/%s/assign" % l.id, headers=h(db, world["mgr"]),
                    json={"agent_id": world["maya"].id}).json()
    hm = h(db, world["maya"])
    assert client.post("/agency/assignments/%s/decline" % a["id"], headers=hm, json={"reason": "full"}).status_code == 200
    assert client.get("/agency/prospects/%s" % l.id, headers=hm).status_code == 404
