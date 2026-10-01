# -*- coding: utf-8 -*-
"""Max Life Command backend: applications, policies, recruits, appointments."""
from datetime import date, datetime, timedelta

from agency_support import agency_feature, h, lead, world  # noqa: F401
from app.models.agency_models import AgencyApplication, AgencyRecruitMilestone
from app.models.models import AuditLogEntry, Lead


def _app(client, world, l=None):
    db = world["db"]
    l = l or lead(db, world["org"], "Case", owner=world["maya"])
    r = client.post("/agency/applications", headers=h(db, world["mgr"]),
                    json={"prospect_id": l.id, "agent_id": world["maya"].id, "product_category": "term_life"})
    assert r.status_code == 201, r.text
    return r.json()


def test_application_transitions_enforced(client, world):
    db = world["db"]
    a = _app(client, world)
    hm = h(db, world["mgr"])
    assert a["status"] == "draft" and a["carrier"] is None and a["history"][0]["to"] == "draft"
    bad = client.post("/agency/applications/%s/transition" % a["id"], headers=hm, json={"to": "approved"})
    assert bad.status_code == 409
    assert client.post("/agency/applications/%s/transition" % a["id"], headers=hm,
                       json={"to": "nonsense"}).status_code == 422
    for to in ("prepared", "submitted", "underwriting", "requirements_requested"):
        r = client.post("/agency/applications/%s/transition" % a["id"], headers=hm, json={"to": to, "note": to})
        assert r.status_code == 200, (to, r.text)
    body = r.json()
    assert body["status"] == "requirements_requested" and body["submitted_at"]
    assert [x["to"] for x in body["history"]] == ["draft", "prepared", "submitted", "underwriting",
                                                  "requirements_requested"]
    r = client.post("/agency/applications/%s/requirements" % a["id"], headers=hm,
                    json={"label": "Attending physician statement", "due": "2026-10-15"})
    assert r.status_code == 201 and r.json()["requirements"][0]["label"] == "Attending physician statement"
    r = client.post("/agency/applications/%s/documents" % a["id"], headers=hm, json={"name": "app.pdf", "kind": "application"})
    assert r.json()["documents"][0]["name"] == "app.pdf"
    # terminal states reject everything
    client.post("/agency/applications/%s/transition" % a["id"], headers=hm, json={"to": "withdrawn"})
    assert client.post("/agency/applications/%s/transition" % a["id"], headers=hm,
                       json={"to": "underwriting"}).status_code == 409
    assert db.query(AuditLogEntry).filter(AuditLogEntry.action == "agency.application.transition").count() == 5


def test_stalled_flag_uses_configured_threshold(client, world):
    db = world["db"]
    hm = h(db, world["mgr"])
    a = _app(client, world)
    client.post("/agency/applications/%s/transition" % a["id"], headers=hm, json={"to": "prepared"})
    client.post("/agency/applications/%s/transition" % a["id"], headers=hm, json={"to": "submitted"})
    assert client.get("/agency/applications?stalled=true", headers=hm).json()["total"] == 0
    row = db.query(AgencyApplication).get(a["id"])
    row.status_changed_at = datetime.utcnow() - timedelta(days=8)
    db.commit()
    s = client.get("/agency/applications?stalled=true", headers=hm).json()
    assert s["total"] == 1 and s["items"][0]["stalled"] and s["items"][0]["days_in_status"] == 8
    client.put("/agency/distribution/config", headers=hm, json={"stalled_days": 10})
    assert client.get("/agency/applications?stalled=true", headers=hm).json()["total"] == 0


def test_issue_creates_policy_and_review_task(client, world):
    db = world["db"]
    hm = h(db, world["mgr"])
    a = _app(client, world)
    assert client.post("/agency/applications/%s/issue" % a["id"], headers=hm, json={}).status_code == 409
    for to in ("prepared", "submitted", "underwriting", "approved"):
        client.post("/agency/applications/%s/transition" % a["id"], headers=hm, json={"to": to})
    # issued only through /issue
    assert client.post("/agency/applications/%s/transition" % a["id"], headers=hm,
                       json={"to": "issued"}).status_code == 409
    r = client.post("/agency/applications/%s/issue" % a["id"], headers=hm,
                    json={"policy_number": "DEMO-1", "carrier": "Entered Carrier", "effective_date": "2026-01-15"})
    assert r.status_code == 200, r.text
    pol = r.json()["policy"]
    assert r.json()["application"]["status"] == "issued" and r.json()["application"]["policy_id"] == pol["id"]
    assert pol["status"] == "in_force" and pol["annual_review_date"] == "2027-01-15"
    assert pol["client"]["id"] == a["prospect"]["id"]
    db.expire_all()
    assert db.query(Lead).get(a["prospect"]["id"]).status == "client"
    r = client.post("/agency/policies/%s/review-task" % pol["id"], headers=hm, json={"kind": "beneficiary_review"})
    assert r.status_code == 201 and r.json()["open_service_tasks"] == 1
    assert r.json()["tasks"][0]["kind"] == "beneficiary_review"
    lst = client.get("/agency/policies", headers=hm).json()
    assert lst["total"] == 1 and lst["items"][0]["open_service_tasks"] == 1
    assert client.get("/agency/policies/%s" % pol["id"], headers=h(db, world["fmgr"])).status_code == 404


def test_recruit_stage_and_milestones(client, world):
    db = world["db"]
    hm = h(db, world["mgr"])
    r = client.post("/agency/recruits", headers=hm, json={"name": "Rita Recruit", "jurisdiction": "TX"})
    assert r.status_code == 201
    rec = r.json()
    assert rec["stage"] == "lead" and rec["stage_history"][0]["to"] == "lead"
    assert client.post("/agency/recruits/%s/stage" % rec["id"], headers=hm, json={"to": "astronaut"}).status_code == 422
    r = client.post("/agency/recruits/%s/stage" % rec["id"], headers=hm, json={"to": "licensing"})
    assert r.json()["stage"] == "licensing" and r.json()["stage_history"][-1] == {
        "from": "lead", "to": "licensing", "at": r.json()["stage_history"][-1]["at"], "by": world["mgr"].id}
    assert r.json()["near_activation"] is False
    r = client.post("/agency/recruits/%s/milestones" % rec["id"], headers=hm,
                    json={"label": "Pre-licensing course", "status": "done"})
    r = client.post("/agency/recruits/%s/milestones" % rec["id"], headers=hm,
                    json={"label": "State exam", "status": "pending",
                          "due": (date.today() - timedelta(days=2)).isoformat()})
    body = r.json()
    assert body["milestones_total"] == 2 and body["milestones_done"] == 1 and body["milestones_overdue"] == 1
    assert "no government licensing integration" in body["licensing_note"]
    r = client.post("/agency/recruits/%s/stage" % rec["id"], headers=hm, json={"to": "training"})
    assert r.json()["near_activation"] is True
    assert client.get("/agency/recruits?near_activation=true", headers=hm).json()["total"] == 1
    assert client.get("/agency/recruits/%s" % rec["id"], headers=h(db, world["fmgr"])).status_code == 404
    r = client.patch("/agency/recruits/%s" % rec["id"], headers=hm, json={"exam_status": "scheduled"})
    assert r.json()["exam_status"] == "scheduled"


def test_appointments_create_list_patch(client, world):
    db = world["db"]
    hm = h(db, world["mgr"])
    l = lead(db, world["org"], "Appt", owner=world["maya"])
    start = (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0).isoformat() + "Z"
    r = client.post("/agency/appointments", headers=hm, json={"prospect_id": l.id, "agent_id": world["maya"].id,
                                                             "type": "discovery", "medium": "video",
                                                             "starts_at": start})
    assert r.status_code == 201, r.text
    ap = r.json()
    assert ap["status"] == "pending" and ap["prospect"]["id"] == l.id
    assert client.get("/agency/appointments?needs_confirmation=true", headers=hm).json()["total"] == 1
    assert client.get("/agency/appointments?range=upcoming", headers=h(db, world["ben"])).json()["total"] == 0
    assert client.patch("/agency/appointments/%s" % ap["id"], headers=hm, json={"status": "bogus"}).status_code == 422
    r = client.patch("/agency/appointments/%s" % ap["id"], headers=h(db, world["maya"]), json={"status": "confirmed"})
    assert r.json()["status"] == "confirmed"
    assert client.get("/agency/appointments?needs_confirmation=true", headers=hm).json()["total"] == 0
    assert client.patch("/agency/appointments/%s" % ap["id"], headers=h(db, world["fmgr"]),
                        json={"status": "missed"}).status_code == 404
