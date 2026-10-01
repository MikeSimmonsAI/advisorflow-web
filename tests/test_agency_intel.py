# -*- coding: utf-8 -*-
"""Max Life Command backend: opportunity brief, copilot, simulate-send, attention, summary, Ask EvoAI."""
import json
from datetime import date, datetime, timedelta

from agency_support import agency_feature, h, lead, reply, world  # noqa: F401
from app.models.agency_models import AgencyCopilotEvent, AgencyPolicy, AgencyProspectProfile, AgencyTask
from app.models.models import Message


def test_brief_separates_facts_inferences_insufficient(client, world):
    db = world["db"]
    l = lead(db, world["org"], "Brief", state="TX", needs=["family_protection"], intent="high")
    client.patch("/agency/prospects/%s/profile" % l.id, headers=h(db, world["mgr"]),
                 json={"household": {"spouse": True, "children": 2}, "retirement_interest": True})
    reply(db, l, "Hi, I have two kids. What would this cost?")
    b = client.get("/agency/prospects/%s/brief" % l.id, headers=h(db, world["mgr"])).json()
    assert set(["facts", "inferences", "insufficient", "disclaimer"]) <= set(b)
    assert all(f["source_field"] for f in b["facts"])
    fields = {f["source_field"] for f in b["facts"]}
    assert {"lead.state", "profile.need_categories", "profile.household", "profile.intent_level"} <= fields
    assert all(i["basis"] and i["confidence"] in ("low", "medium", "high") for i in b["inferences"])
    assert any("profile.household" in i["basis"] for i in b["inferences"])
    assert any("Existing coverage" in q for q in b["insufficient"])
    assert b["category"] == "Family Protection" and b["intent_level"] == "high"
    assert b["generated_by"] == "rules" and b["suggested_agent"]["id"] == world["maya"].id
    text = json.dumps(b).lower()
    for banned in ("suitab", "you qualify", "approved for", "guarantee"):
        assert banned not in text
    # empty record -> insufficient, no invented facts
    bare = lead(db, world["org"], "Bare", state=None)
    b2 = client.get("/agency/prospects/%s/brief" % bare.id, headers=h(db, world["mgr"])).json()
    assert b2["intent_level"] is None and b2["category"] is None and len(b2["insufficient"]) >= 4
    assert not any(f["source_field"].startswith("profile.") for f in b2["facts"])


def test_copilot_employer_coverage_objection(client, world):
    db = world["db"]
    l = lead(db, world["org"], "Obj", owner=world["maya"])
    reply(db, l, "I already have life insurance through work. Why would I need anything else?")
    c = client.get("/agency/prospects/%s/copilot" % l.id, headers=h(db, world["maya"])).json()
    labels = [d["label"] for d in c["detected"]]
    assert "Employer coverage concern" in labels
    obj = next(d for d in c["detected"] if d["label"] == "Employer coverage concern")
    assert obj["type"] == "objection" and "through work" in obj["quote"]
    assert "Why would I need anything else?" in c["unanswered"]
    assert c["suggested_reply"].rstrip().endswith("?")      # ends in a discovery question
    assert c["reply_framing"].startswith("education + discovery")
    low = (c["suggested_reply"] + c["next_best_question"]).lower()
    for banned in ("you need", "you should buy", "you qualify", "suitab", "recommend you"):
        assert banned not in low
    assert "employer" in c["next_best_question"].lower()
    assert c["generated_by"] == "rules"
    # health question recommends human takeover
    reply(db, l, "Will my diabetes medication be a problem?")
    c2 = client.get("/agency/prospects/%s/copilot" % l.id, headers=h(db, world["maya"])).json()
    assert c2["recommend_human_takeover"] and c2["takeover_reasons"]


def test_simulate_send_creates_no_provider_call(client, world, monkeypatch):
    db = world["db"]
    import app.services.sms_service as sms  # noqa: F401
    calls = []
    monkeypatch.setattr("twilio.rest.Client", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("twilio")), raising=False)
    l = lead(db, world["org"], "Sim", owner=world["maya"])
    r = client.post("/agency/prospects/%s/copilot/simulate-send" % l.id, headers=h(db, world["maya"]),
                    json={"body": "Thanks for reaching out!"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["simulated"] is True and e["provider_called"] is False
    assert calls == []
    assert db.query(Message).filter(Message.lead_id == l.id).count() == 0
    assert db.query(AgencyCopilotEvent).filter(AgencyCopilotEvent.lead_id == l.id).one().simulated is True
    conv = client.get("/agency/prospects/%s" % l.id, headers=h(db, world["maya"])).json()["conversation"]
    assert conv[-1]["simulated"] is True and conv[-1]["channel"] == "simulated"
    # simulated send is not "contact"
    p = client.get("/agency/prospects?uncontacted=true", headers=h(db, world["maya"])).json()
    assert l.id in [x["id"] for x in p["items"]]
    assert client.post("/agency/prospects/%s/takeover" % l.id, headers=h(db, world["maya"])).json()["human_takeover"]
    r = client.post("/agency/prospects/%s/automation" % l.id, headers=h(db, world["maya"]), json={"paused": False})
    assert r.json() == {"automation_paused": False}


def _seed_attention(db, world):
    o = world["org"]
    hot = lead(db, o, "Hot", needs=["family_protection"], intent="high", created_ago_min=500)
    unassigned = lead(db, o, "Lonely")
    task_lead = lead(db, o, "Tasky", owner=world["ben"])
    db.add(AgencyTask(organization_id=o.id, title="Call back", lead_id=task_lead.id,
                      assigned_user_id=world["ben"].id, due_at=datetime.utcnow() - timedelta(days=1)))
    pol = AgencyPolicy(organization_id=o.id, lead_id=task_lead.id, agent_user_id=world["ben"].id,
                       status="in_force", annual_review_date=date.today() + timedelta(days=5))
    db.add(pol)
    db.commit()
    return hot, unassigned, task_lead, pol


def test_attention_items_link_to_real_records(client, world):
    db = world["db"]
    hot, unassigned, task_lead, pol = _seed_attention(db, world)
    # foreign org noise must never appear
    lead(db, world["other"], "Foreign", intent="high", needs=["family_protection"], created_ago_min=500)
    items = client.get("/agency/attention", headers=h(db, world["mgr"])).json()["items"]
    kinds = {i["kind"] for i in items}
    assert {"high_intent_past_response_target", "unassigned_prospect", "overdue_task",
            "annual_review_due"} <= kinds
    hm = h(db, world["mgr"])
    for i in items:
        lk = i["link"]
        assert lk["path"].endswith(lk["id"])
        api = {"prospect": "/agency/prospects/%s", "policy": "/agency/policies/%s",
               "application": "/agency/applications/%s", "recruit": "/agency/recruits/%s",
               "appointment": "/agency/appointments/%s"}.get(lk["type"])
        if api:
            assert client.get(api % lk["id"], headers=hm).status_code == 200, lk
    assert any(i["link"]["id"] == hot.id for i in items)
    assert any(i["link"]["id"] == pol.id for i in items)


def test_summary_counts_reconcile_with_lists(client, world):
    db = world["db"]
    _seed_attention(db, world)
    hm = h(db, world["mgr"])
    s = client.get("/agency/summary", headers=hm).json()["counts"]
    for key, c in s.items():
        if key == "attention":
            total = client.get(c["link"], headers=hm).json()["total"]
        else:
            total = client.get(c["link"] + ("&" if "?" in c["link"] else "?") + "per_page=200",
                               headers=hm).json()["total"]
        assert total == c["count"], (key, c)
    assert s["prospects"]["count"] == 3 and s["unassigned"]["count"] == 2


def test_ask_supported_and_unsupported(client, world):
    db = world["db"]
    hot, *_ = _seed_attention(db, world)
    hm = h(db, world["mgr"])
    r = client.post("/agency/ask", headers=hm,
                    json={"question": "Show every high-intent family protection opportunity not contacted."}).json()
    assert r["supported"] and r["intent"] == "uncontacted_high_intent"
    assert [i["link"]["id"] for i in r["items"]] == [hot.id]
    r = client.post("/agency/ask", headers=hm, json={"question": "Which agents have capacity?"}).json()
    assert r["supported"] and {i["link"]["id"] for i in r["items"]} == {world["maya"].id, world["ben"].id}
    for q, intent in (("What applications are stalled?", "stalled_applications"),
                      ("What needs my attention today?", "attention_today"),
                      ("Show recruiting candidates close to activation.", "recruits_near_activation"),
                      ("Which appointments need confirmation?", "appointments_need_confirmation"),
                      ("Which prospects should be reassigned?", "prospects_to_reassign")):
        r = client.post("/agency/ask", headers=hm, json={"question": q}).json()
        assert r["supported"] and r["intent"] == intent, q
    r = client.post("/agency/ask", headers=hm, json={"question": "What will the S&P 500 do next year?"}).json()
    assert r["supported"] is False and r["items"] == [] and len(r["supported_questions"]) >= 7
    assert r["suggestions"]
