# -*- coding: utf-8 -*-
"""S8 — Max Life Command gap closure: SQL-side prospect filtering/pagination,
the /agency/conversations list, and profile PATCH stays stated-facts-only."""
from datetime import datetime, timedelta

from app.models.agency_models import AgencyAssignment, AgencyCopilotEvent
from app.models.models import Message
from app.services.agency import queries as Q
from agency_support import agency_feature, h, lead, reply, world  # noqa: F401


def test_prospect_pagination_total_and_pages_in_sql(client, world):
    db, o, mgr = world["db"], world["org"], world["mgr"]
    for i in range(7):
        lead(db, o, first="Page%d" % i, intent="high" if i % 2 == 0 else "low")
    lead(db, world["other"], first="Foreign", intent="high")
    hd = h(db, mgr)
    r1 = client.get("/agency/prospects?per_page=3&page=1", headers=hd).json()
    r3 = client.get("/agency/prospects?per_page=3&page=3", headers=hd).json()
    assert r1["total"] == 7 and len(r1["items"]) == 3 and r1["page"] == 1
    assert len(r3["items"]) == 1
    seen = set()
    for pg in (1, 2, 3):
        seen |= {x["id"] for x in client.get("/agency/prospects?per_page=3&page=%d" % pg, headers=hd).json()["items"]}
    assert len(seen) == 7  # stable order, no overlap, no foreign rows
    hi = client.get("/agency/prospects?intent=high&per_page=2", headers=hd).json()
    assert hi["total"] == 4 and len(hi["items"]) == 2
    assert all(x["intent_level"] == "high" for x in hi["items"])


def test_assignment_unassigned_uncontacted_filters_match_python_rule(client, world):
    db, o, mgr, maya = world["db"], world["org"], world["mgr"], world["maya"]
    a = lead(db, o, first="Offered")
    b = lead(db, o, first="Owned", owner=maya)
    c = lead(db, o, first="Fresh")
    d = lead(db, o, first="TimedOut")
    now = datetime.utcnow()
    db.add(AgencyAssignment(organization_id=o.id, lead_id=a.id, agent_user_id=maya.id, state="offered",
                            attempt=1, created_at=now))
    db.add(AgencyAssignment(organization_id=o.id, lead_id=d.id, agent_user_id=maya.id, state="offered",
                            attempt=1, created_at=now - timedelta(minutes=5)))
    db.add(AgencyAssignment(organization_id=o.id, lead_id=d.id, agent_user_id=maya.id, state="timed_out",
                            attempt=2, created_at=now))
    db.add(Message(lead_id=c.id, sender_id=mgr.id, body="hello"))
    db.commit()
    hd = h(db, mgr)
    ids = lambda path: {x["id"] for x in client.get(path, headers=hd).json()["items"]}  # noqa: E731
    assert ids("/agency/prospects?assignment=offered") == {a.id}
    assert ids("/agency/prospects?assignment=accepted") == {b.id}
    assert ids("/agency/prospects?assignment=timed_out") == {d.id}
    assert ids("/agency/prospects?assignment=unassigned") == {c.id}
    assert ids("/agency/prospects?unassigned=true") == {c.id, d.id}
    assert c.id not in ids("/agency/prospects?uncontacted=true")
    # summary counts reconcile with the list totals
    summ = client.get("/agency/summary", headers=hd).json()["counts"]
    tot = client.get("/agency/prospects?unassigned=true", headers=hd).json()["total"]
    assert summ["unassigned"]["count"] == tot == 2


def test_conversations_list_scoped_and_ordered(client, world):
    db, o, mgr = world["db"], world["org"], world["mgr"]
    quiet = lead(db, o, first="Quiet")  # noqa: F841  (no conversation -> not listed)
    asked = lead(db, o, first="Asked")
    answered = lead(db, o, first="Answered")
    sim = lead(db, o, first="Sim")
    foreign = lead(db, world["other"], first="Foreign")
    reply(db, asked, "Does term life cover my mortgage?")
    reply(db, answered, "Call me tomorrow")
    db.add(Message(lead_id=answered.id, sender_id=mgr.id, body="Will do",
                   sent_at=datetime.utcnow() + timedelta(seconds=5)))
    db.add(AgencyCopilotEvent(organization_id=o.id, lead_id=sim.id, kind="simulated_send",
                              body="Simulated hello", simulated=True,
                              created_at=datetime.utcnow()))
    reply(db, foreign, "foreign reply")
    db.commit()
    hd = h(db, mgr)
    r = client.get("/agency/conversations", headers=hd)
    assert r.status_code == 200, r.text
    body = r.json()
    got = {x["prospect"]["id"]: x for x in body["items"]}
    assert set(got) == {asked.id, answered.id, sim.id} and body["total"] == 3
    assert got[asked.id]["awaiting_reply"] is True and got[asked.id]["inbound_count"] == 1
    assert got[answered.id]["awaiting_reply"] is False
    assert got[sim.id]["last_message"]["simulated"] is True
    aw = client.get("/agency/conversations?awaiting_reply=true", headers=hd).json()
    assert [x["prospect"]["id"] for x in aw["items"]] == [asked.id]
    one = client.get("/agency/conversations?per_page=1&page=2", headers=hd).json()
    assert one["total"] == 3 and len(one["items"]) == 1
    # the foreign workspace never sees ours
    fr = client.get("/agency/conversations", headers=h(db, world["fmgr"])).json()
    assert {x["prospect"]["id"] for x in fr["items"]} == {foreign.id}


def test_conversations_advisor_sees_only_own(client, world):
    db, o, maya = world["db"], world["org"], world["maya"]
    mine = lead(db, o, first="Mine", owner=maya)
    theirs = lead(db, o, first="Theirs", owner=world["ben"])
    reply(db, mine, "hi")
    reply(db, theirs, "hi")
    got = client.get("/agency/conversations", headers=h(db, maya)).json()
    assert {x["prospect"]["id"] for x in got["items"]} == {mine.id}


def test_profile_patch_partial_keeps_other_stated_fields(client, world):
    db, o, mgr = world["db"], world["org"], world["mgr"]
    l = lead(db, o, first="Prof")
    hd = h(db, mgr)
    r = client.patch("/agency/prospects/%s/profile" % l.id, headers=hd,
                     json={"household": {"spouse": True, "children": 2}, "financial_goals": ["protect income"]})
    assert r.status_code == 200, r.text
    r = client.patch("/agency/prospects/%s/profile" % l.id, headers=hd, json={"preferred_contact": "email"})
    p = r.json()
    assert p["household"] == {"spouse": True, "children": 2}
    assert p["financial_goals"] == ["protect income"] and p["preferred_contact"] == "email"
    assert client.patch("/agency/prospects/%s/profile" % l.id, headers=hd,
                        json={"intent_level": "certain"}).status_code == 422
