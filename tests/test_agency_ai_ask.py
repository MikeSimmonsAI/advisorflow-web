# -*- coding: utf-8 -*-
"""Ask EvoAI (S14): scored parser incl. typos, new intents mapped to real
queries, filter disclosure, DEMO labelling, insufficient information,
suggestions for unsupported questions, tenant + role isolation."""
from datetime import date, datetime, timedelta

import pytest

from agency_support import agency_feature, h, lead, world  # noqa: F401
from app.models.agency_models import (AgencyApplication, AgencyAppointment, AgencyAssignment,
                                      AgencyPolicy, AgencyRecruit, AgencyTask)
from app.services.agency import ask as A
from app.services.agency.common import DEFAULT_RECRUIT_STAGES

AGENTS = [{"user_id": "m", "name": "Maya", "is_demo": False}, {"user_id": "b", "name": "Ben", "is_demo": False}]


@pytest.mark.parametrize("q,intent", [
    ("Show every high-intent family protection opportunity not contacted.", "uncontacted_high_intent"),
    ("high intnet familly protection leads not contacted", "uncontacted_high_intent"),
    ("Wich agnets have capacty?", "agents_with_capacity"),
    ("which advisors have room", "agents_with_capacity"),
    ("show me apps that are stuk", "stalled_applications"),
    ("whats needs my atention today", "attention_today"),
    ("apointments needing confirmaton", "appointments_need_confirmation"),
    ("Which prospects should be re-assigned?", "prospects_to_reassign"),
    ("pending offers", "offers_awaiting_acceptance"),
    ("which handoffs are waiting for acceptance", "offers_awaiting_acceptance"),
    ("offers that timed out", "offers_timed_out"),
    ("overdue follow-ups", "overdue_tasks"),
    ("which tasks are late", "overdue_tasks"),
    ("annual reviews due this month", "reviews_due_month"),
    ("which apps are waiting on the client", "applications_awaiting_client"),
    ("applications in underwriting", "applications_by_status"),
    ("recruits in licensing", "recruits_by_stage"),
    ("which candidates are almost active agents", "recruits_near_activation"),
    ("who should get Jordan Rivera", "recommend_agent_for_prospect"),
    ("What does Maya have?", "agent_open_work"),
    ("whats on Mayas plate", "agent_open_work"),
    ("unassigned leads", "unassigned_prospects"),
    ("retirement prospects in TX", "prospects_filtered"),
    ("prospects in texas", "prospects_filtered"),
    ("appointments today", "appointments_today"),
])
def test_parser_intents_and_typos(q, intent):
    got, parsed, ents = A.parse(q, ("maya", "ben"), DEFAULT_RECRUIT_STAGES, AGENTS)
    assert got == intent, (q, got, parsed.text)


def test_entity_extraction():
    _, _, e = A.parse("Show high-intent retirement prospects in TX.", (), DEFAULT_RECRUIT_STAGES, [])
    assert e["state"] == "TX" and e["need"] == "retirement" and e["intent_level"] == "high"
    # "in"/"or"/"me" lowercase are words, not states
    _, _, e = A.parse("show me prospects in or near the office", (), DEFAULT_RECRUIT_STAGES, [])
    assert e["state"] is None


def _seed(db, w):
    o, maya, ben = w["org"], w["maya"], w["ben"]
    now = datetime.utcnow()
    tx_ret = lead(db, o, "Rita", "Retire", state="TX", needs=["retirement"], intent="high", owner=maya)
    ok_fp = lead(db, o, "Omar", "Family", state="OK", needs=["family_protection"], intent="medium")
    nostate = lead(db, o, "Nora", "Nostate", state=None)
    jordan = lead(db, o, "Jordan", "Rivera", state="TX", needs=["family_protection"], intent="high", is_test=True)
    offered = lead(db, o, "Olive", "Offered", owner=None)
    timed = lead(db, o, "Tim", "Timed")
    db.add_all([
        AgencyAssignment(organization_id=o.id, lead_id=offered.id, agent_user_id=ben.id, state="offered",
                         expires_at=now + timedelta(minutes=20)),
        AgencyAssignment(organization_id=o.id, lead_id=timed.id, agent_user_id=ben.id, state="timed_out"),
        AgencyApplication(organization_id=o.id, lead_id=tx_ret.id, agent_user_id=maya.id,
                          status="awaiting_client", status_changed_at=now - timedelta(days=2)),
        AgencyApplication(organization_id=o.id, lead_id=ok_fp.id, agent_user_id=ben.id,
                          status="underwriting", status_changed_at=now - timedelta(days=1)),
        AgencyAppointment(organization_id=o.id, lead_id=tx_ret.id, agent_user_id=maya.id,
                          starts_at=now + timedelta(days=2), status="pending"),
        AgencyTask(organization_id=o.id, title="Call Rita", lead_id=tx_ret.id, assigned_user_id=maya.id,
                   due_at=now - timedelta(days=1)),
        AgencyTask(organization_id=o.id, title="Ben chore", lead_id=ok_fp.id, assigned_user_id=ben.id,
                   due_at=now - timedelta(days=2)),
        AgencyRecruit(organization_id=o.id, name="Lena Licensing", stage="licensing", recruiter_user_id=maya.id),
        AgencyRecruit(organization_id=o.id, name="Ian Interview", stage="interview"),
        AgencyPolicy(organization_id=o.id, lead_id=tx_ret.id, agent_user_id=maya.id, status="in_force",
                     annual_review_date=date.today()),
        AgencyPolicy(organization_id=o.id, lead_id=ok_fp.id, agent_user_id=ben.id, status="in_force",
                     annual_review_date=None),
    ])
    db.commit()
    # foreign-org look-alikes that must never appear
    lead(w["db"], w["other"], "Jordan", "Rivera", state="TX", needs=["retirement"], intent="high")
    db.add(AgencyApplication(organization_id=w["other"].id, lead_id=lead(db, w["other"], "Far").id,
                             status="awaiting_client", status_changed_at=now))
    db.commit()
    return dict(tx_ret=tx_ret, ok_fp=ok_fp, nostate=nostate, jordan=jordan, offered=offered, timed=timed)


def _ask(client, db, u, q):
    r = client.post("/agency/ask", headers=h(db, u), json={"question": q})
    assert r.status_code == 200, r.text
    return r.json()


def _ids(r):
    return [i["link"]["id"] for i in r["items"]]


def test_new_intents_return_real_records_with_filters(client, world):
    db, mgr = world["db"], world["mgr"]
    s = _seed(db, world)

    r = _ask(client, db, mgr, "retirement prospects in TX")
    assert r["intent"] == "prospects_filtered" and _ids(r) == [s["tx_ret"].id]
    assert r["filter"]["params"] == {"need": "retirement", "state": "TX"}
    # the disclosed filter IS the list endpoint: same rows
    lst = client.get(r["filter"]["api"], headers=h(db, mgr)).json()
    assert [x["id"] for x in lst["items"]] == _ids(r)
    assert any("no state recorded" in x for x in r["insufficient"])

    r = _ask(client, db, mgr, "which apps are waiting on the client?")
    assert r["intent"] == "applications_awaiting_client" and len(r["items"]) == 1
    assert r["filter"]["api"] == "/agency/applications?status=awaiting_client"
    assert r["items"][0]["link"]["type"] == "application"

    r = _ask(client, db, mgr, "pending offers")
    assert r["intent"] == "offers_awaiting_acceptance" and _ids(r) == [s["offered"].id]
    r = _ask(client, db, mgr, "offers that timed out")
    assert _ids(r) == [s["timed"].id]

    r = _ask(client, db, mgr, "overdue follow ups")
    assert r["intent"] == "overdue_tasks" and len(r["items"]) == 2
    assert all(i["link"]["type"] == "prospect" for i in r["items"])

    r = _ask(client, db, mgr, "Which annual reviews are due this month?")
    assert r["intent"] == "reviews_due_month" and len(r["items"]) == 1
    assert r["filter"]["params"]["review_month"] == date.today().strftime("%Y-%m")
    assert client.get(r["filter"]["api"], headers=h(db, mgr)).json()["total"] == 1
    assert any("no annual review date" in x for x in r["insufficient"])

    r = _ask(client, db, mgr, "recruits in licensing")
    assert r["intent"] == "recruits_by_stage" and [i["label"].split(" - ")[0] for i in r["items"]] == ["Lena Licensing"]

    r = _ask(client, db, mgr, "What does Maya have?")
    assert r["intent"] == "agent_open_work" and r["entities"]["agent"]["id"] == world["maya"].id
    kinds = sorted({i["kind"] for i in r["items"]})
    assert kinds == ["application", "appointment", "prospect", "task"]
    assert "Ben chore" not in str(r["items"])


def test_recommendation_intent_demo_label_and_insufficient(client, world):
    db, mgr = world["db"], world["mgr"]
    s = _seed(db, world)
    r = _ask(client, db, mgr, "Who should get Jordan Rivera?")
    assert r["intent"] == "recommend_agent_for_prospect" and r["status"] == "answered"
    assert r["items"][0]["link"]["id"] == s["jordan"].id       # this org's Jordan only
    assert r["items"][0]["is_demo"] is True and r["demo_data"] is True
    assert "DEMO" in r["answered_from"]
    assert r["items"][1]["link"]["id"] == world["maya"].id      # TX + family protection
    assert r["filter"]["api"] == "/agency/prospects/%s/recommendation" % s["jordan"].id
    assert any("conversion history" in x for x in r["insufficient"])

    r = _ask(client, db, mgr, "who should get Zebediah Nobody")
    assert r["status"] == "insufficient_information" and r["items"] == []

    r = _ask(client, db, mgr, "What premium does Jordan pay?")
    assert r["supported"] is True and r["status"] == "insufficient_information"
    assert r["answer"].startswith("Insufficient information") and r["items"] == []
    r = _ask(client, db, mgr, "Does Rita qualify for coverage?")
    assert r["status"] == "insufficient_information" and "eligib" in " ".join(r["insufficient"]).lower()


def test_unsupported_returns_closest_suggestions(client, world):
    db, mgr = world["db"], world["mgr"]
    r = _ask(client, db, mgr, "What will the S&P 500 do next year?")
    assert r["supported"] is False and r["status"] == "unsupported" and r["items"] == []
    assert len(r["suggestions"]) == 4 and set(r["suggestions"]) <= set(r["supported_questions"])
    r = _ask(client, db, mgr, "tell me about the appointments situation")
    assert r["supported"] is False
    assert any("ppointment" in s for s in r["suggestions"][:2])


def test_tenant_and_role_isolation(client, world):
    db = world["db"]
    s = _seed(db, world)
    # foreign manager sees none of org A's records, even by exact name
    r = _ask(client, db, world["fmgr"], "Who should get Jordan Rivera?")
    assert s["jordan"].id not in str(r)
    r = _ask(client, db, world["fmgr"], "which apps are waiting on the client")
    assert len(r["items"]) == 1 and r["items"][0]["link"]["id"] not in str(s)
    r = _ask(client, db, world["fmgr"], "What does Maya have?")
    assert r["status"] == "insufficient_information" and r["items"] == []
    # an advisor cannot read another agent's book through Ask
    r = _ask(client, db, world["ben"], "What does Maya have?")
    assert r["items"] == [] and r["status"] == "insufficient_information"
    r = _ask(client, db, world["ben"], "overdue tasks")
    assert [i["label"].split(" (")[0] for i in r["items"]] == ["Ben chore"]
