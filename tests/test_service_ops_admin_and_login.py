# -*- coding: utf-8 -*-
"""Service Operations - the EvoSys switches, and REAL sign-in for workers and
buyers: invite -> redeem the one-time setup link -> sign in with email and
password through /auth/login -> use the app -> turned off -> refused.

Synthetic companies and people only; nothing is emailed.
"""
import io
import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.models.models import AuditLogEntry, EmailMessage, User
from app.models.service_ops_models import ServiceJob
from tests.test_acquisition_pilot import _h, pilot  # noqa: F401
from tests.test_service_ops import PNG, _booked, _o, _switch, _u, _won  # noqa: F401

PW = "Synthetic-Test-Passw0rd!"
ORIGIN = {"Origin": "https://app.evosyspro.live"}


def _g(p):
    return _h(p["db"], p["god"])


def _admin(p, body, org=None):
    return p["c"].put("/acquisition-ops/%s/service-ops" % (org or p["shine"]).id, json=body, headers=_g(p))


def _login(p, link, email, *, expect=200):
    assert link.startswith("https://app.evosyspro.live/activate?token="), link      # a full, openable link
    token = parse_qs(urlparse(link).query)["token"][0]
    pv = p["c"].get("/auth/staff-activation", params={"token": token})
    assert pv.status_code == 200, pv.text
    r = p["c"].post("/auth/staff-activation/accept", json={"token": token, "new_password": PW})
    assert r.status_code == 200, r.text
    again = p["c"].post("/auth/staff-activation/accept", json={"token": token, "new_password": PW})
    assert again.status_code >= 400                                                 # single use
    r = p["c"].post("/auth/login", data={"username": email, "password": PW})
    assert r.status_code == expect, r.text
    hdr = {"Authorization": "Bearer %s" % r.json()["access_token"]}
    return hdr, pv.json()


# ── the EvoSys switches ───────────────────────────────────────────────────
def test_switches_dependencies_audit_and_only_evosys(pilot):
    p = pilot
    st = p["c"].get("/acquisition-ops/%s/service-ops" % p["shine"].id, headers=_g(p)).json()
    caps = {c["key"]: c for c in st["capabilities"]}
    assert all(not c["enabled"] for c in caps.values())                       # off by default
    assert caps["advanced_enabled"]["blocked_reason"] == "Turn on Service Operations first"
    assert caps["ops_enabled"]["functions"] and st["while_off"]
    # Company staff can't flip platform features, through any door.
    assert p["c"].put("/acquisition-ops/%s/service-ops" % p["shine"].id, json={"ops_enabled": True},
                      headers=_o(p)).status_code in (401, 403)
    assert _admin(p, {"advanced_enabled": True}).status_code == 409           # dependency enforced
    r = _admin(p, {"ops_enabled": True, "advanced_enabled": True, "buyer_portal_enabled": True})
    assert r.status_code == 200, r.text
    caps = {c["key"]: c for c in r.json()["capabilities"]}
    assert caps["ops_enabled"]["enabled"] and caps["buyer_portal_enabled"]["enabled"]
    assert caps["ops_enabled"]["last_change"]["by"] and caps["ops_enabled"]["last_change"]["to"] is True
    assert "Business name on invoices" in caps["advanced_enabled"]["missing"]
    # No AI key in tests: the AI assistant is refused with the reason, not silently on.
    ai = _admin(p, {"ai_assist_enabled": True})
    assert ai.status_code == 409 and "AI" in ai.json()["detail"]
    row = (p["db"].query(AuditLogEntry).filter(AuditLogEntry.action == "service_ops_switch")
           .order_by(AuditLogEntry.created_at.desc()).first())
    assert json.loads(row.after_state)["ops_enabled"] is True and json.loads(row.before_state)["ops_enabled"] is False
    # The company menu appears for its admin.
    me = p["c"].get("/service-ops/me", headers=_o(p)).json()
    assert me["ops_enabled"] and me["role"] == "company_admin"
    states = p["c"].get("/god/acquisition/service-ops/states", headers=_g(p)).json()["items"]
    assert states[p["shine"].id]["ops"] is True and p["other"].id not in states


def test_turning_off_keeps_every_record_and_asks_about_upcoming_jobs(pilot):
    p = pilot
    _admin(p, {"ops_enabled": True, "advanced_enabled": True, "buyer_portal_enabled": True})
    c = p["c"].post("/service-ops/customers", json={"name": "Keep Me Co", "location": {
        "name": "HQ", "service_category": "window_washing"}}, headers=_o(p)).json()
    loc = p["c"].get("/service-ops/customers/%s" % c["id"], headers=_o(p)).json()["locations"][0]
    job = p["c"].post("/service-ops/jobs", json={"location_id": loc["id"], "local_start": "2030-06-03T09:00"},
                      headers=_o(p)).json()
    off = _admin(p, {"ops_enabled": False})
    assert off.status_code == 409 and "upcoming" in off.json()["detail"]
    off = _admin(p, {"ops_enabled": False, "confirm_upcoming": True})
    assert off.status_code == 200
    caps = {x["key"]: x["enabled"] for x in off.json()["capabilities"]}
    assert caps == {"ops_enabled": False, "advanced_enabled": False, "buyer_portal_enabled": False,
                    "ai_assist_enabled": False}                                # dependents follow it off
    assert p["c"].get("/service-ops/customers", headers=_o(p)).status_code == 402   # access stops
    j = p["db"].query(ServiceJob).get(job["id"])
    assert j is not None and j.status == "scheduled"                         # not cancelled, not started
    assert off.json()["counts"]["customers"] == 1 and off.json()["counts"]["upcoming_jobs"] == 1
    on = _admin(p, {"ops_enabled": True})
    assert on.status_code == 200
    assert p["c"].get("/service-ops/jobs/%s" % job["id"], headers=_o(p)).json()["status"] == "scheduled"


# ── worker: real sign-in through the front door ───────────────────────────
def test_worker_real_login_assigned_work_only_completion_and_revocation(pilot):
    p = pilot
    _admin(p, {"ops_enabled": True})
    # Company admin (not God) invites from the Team page.
    inv = p["c"].post("/service-ops/team", json={"email": "tech@example.com", "full_name": "Tess Tech",
                                                 "role": "field_worker"}, headers={**_o(p), **ORIGIN}).json()
    assert inv["setup"]["state"] == "pending"
    wh, preview = _login(p, inv["setup_url"], "tech@example.com")
    assert preview["agency"] and "administrator" not in json.dumps(preview).lower()
    me = p["c"].get("/service-ops/me", headers=wh).json()
    assert me["role"] == "field_worker" and me["member_id"] == inv["id"]
    # Lands on the worker view: the sales portal refuses this login.
    assert p["c"].get("/acquisition/status", headers=wh).status_code == 403
    assert p["c"].get("/god/acquisition/workforce", headers=wh).status_code in (401, 403)
    assert p["c"].get("/acquisition-ops/%s/service-ops" % p["shine"].id, headers=wh).status_code in (401, 403)
    # Two jobs; only the assigned one is visible.
    c = p["c"].post("/service-ops/customers", json={"name": "Worker Test Co", "location": {
        "name": "Site", "service_category": "cleaning", "access_instructions": "Code 1234"}}, headers=_o(p)).json()
    loc = p["c"].get("/service-ops/customers/%s" % c["id"], headers=_o(p)).json()["locations"][0]
    mine = p["c"].post("/service-ops/jobs", json={"location_id": loc["id"], "local_start": "2030-06-03T09:00",
                                                 "instructions": "Front door only"}, headers=_o(p)).json()
    other = p["c"].post("/service-ops/jobs", json={"location_id": loc["id"], "local_start": "2030-06-04T09:00"},
                        headers=_o(p)).json()
    p["c"].post("/service-ops/jobs/%s/assign" % mine["id"], json={"add": [inv["id"]]}, headers=_o(p))
    listed = p["c"].get("/service-ops/jobs?mine=true", headers=wh).json()["items"]
    assert [j["id"] for j in listed] == [mine["id"]]
    assert p["c"].get("/service-ops/jobs/%s" % other["id"], headers=wh).status_code == 404
    d = p["c"].get("/service-ops/jobs/%s" % mine["id"], headers=wh).json()
    assert d["instructions"] == "Front door only" and d["location_detail"]["access_instructions"] == "Code 1234"
    assert p["c"].post("/service-ops/jobs/%s/start" % mine["id"], headers=wh).json()["status"] == "in_progress"
    for i in d["checklist"]:
        p["c"].post("/service-ops/jobs/%s/checklist" % mine["id"], json={"key": i["key"], "done": True}, headers=wh)
    up = p["c"].post("/service-ops/jobs/%s/photos" % mine["id"], files={"file": ("t.png", io.BytesIO(PNG), "image/png")},
                     headers=wh)
    assert up.status_code == 200, up.text
    done = p["c"].post("/service-ops/jobs/%s/complete" % mine["id"], json={"notes": "Done"}, headers=wh).json()
    assert done["status"] == "completed"
    # The company admin sees the saved result.
    office = p["c"].get("/service-ops/jobs/%s" % mine["id"], headers=_o(p)).json()
    assert office["status"] == "completed" and office["completion_notes"] == "Done" and office["attachments"]
    # Asking for another company's workspace changes nothing: still only their own assigned job.
    _switch(p, org=p["other"])
    oc = p["c"].post("/service-ops/customers", json={"name": "Other Co Customer", "location": {
        "name": "Other site", "service_category": "cleaning"}}, headers=_u(p, p["outsider"], p["other"])).json()
    r = p["c"].get("/service-ops/jobs", headers={**wh, "X-Workspace-Id": p["other"].id})
    assert r.status_code in (402, 403, 404) or [j["id"] for j in r.json()["items"]] == [mine["id"]]
    assert p["c"].get("/service-ops/customers/%s" % oc["id"], headers={**wh, "X-Workspace-Id": p["other"].id}).status_code in (403, 404)
    # Turned off: can't sign in, and an old link can't bring them back.
    p["c"].put("/service-ops/team/%s" % inv["id"], json={"active": False}, headers=_o(p))
    assert p["c"].post("/auth/login", data={"username": "tech@example.com", "password": PW}).status_code in (400, 401, 403)
    assert p["c"].get("/service-ops/jobs?mine=true", headers=wh).status_code in (401, 403)
    assert p["c"].post("/service-ops/team/%s/setup-link" % inv["id"], headers=_o(p)).status_code == 409
    # Back on, fresh link (reset, since they've signed in before), old one dead.
    p["c"].put("/service-ops/team/%s" % inv["id"], json={"active": True}, headers=_o(p))
    re = p["c"].post("/service-ops/team/%s/setup-link" % inv["id"], headers={**_o(p), **ORIGIN}).json()
    assert re["setup_url"].startswith("https://") and re["setup"]["state"] == "pending"
    assert p["db"].query(EmailMessage).count() == 0


def test_staff_need_team_permission_to_invite_and_cannot_create_admins(pilot):
    p = pilot
    _admin(p, {"ops_enabled": True})
    disp = p["c"].post("/service-ops/team", json={"email": "disp@example.com", "full_name": "Dee Dispatch",
                                                  "role": "dispatcher"}, headers={**_o(p), **ORIGIN}).json()
    dh, _ = _login(p, disp["setup_url"], "disp@example.com")
    assert p["c"].post("/service-ops/team", json={"email": "x@example.com", "full_name": "X"}, headers=dh).status_code == 403
    assert p["c"].put("/acquisition-ops/%s/service-ops" % p["shine"].id, json={"buyer_portal_enabled": True},
                      headers=dh).status_code in (401, 403)


# ── buyer: real sign-in, scoped, revocable ────────────────────────────────
def test_buyer_real_login_scope_request_and_revocation(pilot):
    p = pilot
    _admin(p, {"ops_enabled": True, "advanced_enabled": True, "buyer_portal_enabled": True})
    mine = p["c"].post("/service-ops/customers", json={"name": "Pat's Properties", "location": {
        "name": "Tower A", "service_category": "window_washing"}}, headers=_o(p)).json()
    theirs = p["c"].post("/service-ops/customers", json={"name": "Someone Else LLC", "location": {
        "name": "Elsewhere", "service_category": "cleaning"}}, headers=_o(p)).json()
    loc = p["c"].get("/service-ops/customers/%s" % mine["id"], headers=_o(p)).json()["locations"][0]
    oloc = p["c"].get("/service-ops/customers/%s" % theirs["id"], headers=_o(p)).json()["locations"][0]
    up = p["c"].post("/service-ops/jobs", json={"location_id": loc["id"], "local_start": "2030-06-03T09:00"},
                     headers=_o(p)).json()
    hidden = p["c"].post("/service-ops/jobs", json={"location_id": oloc["id"], "local_start": "2030-06-03T11:00"},
                         headers=_o(p)).json()
    inv = p["c"].post("/service-ops/buyers", json={"customer_id": mine["id"], "email": "pat@example.com",
                                                   "full_name": "Pat Manager", "location_ids": [loc["id"]]},
                      headers={**_o(p), **ORIGIN}).json()
    bh, preview = _login(p, inv["setup_url"], "pat@example.com")
    assert preview["service_portal"] and "administrator" not in json.dumps(preview).lower()
    assert p["c"].get("/service-ops/me", headers=bh).json()["is_buyer"] is True
    home = p["c"].get("/service-ops/buyer/home", headers=bh).json()
    assert [l["id"] for l in home["locations"]] == [loc["id"]] and [j["id"] for j in home["upcoming"]] == [up["id"]]
    assert p["c"].get("/service-ops/buyer/visits/%s" % hidden["id"], headers=bh).status_code == 404
    for path in ("/service-ops/jobs", "/service-ops/customers", "/service-ops/team", "/service-ops/invoices",
                 "/acquisition/status"):
        assert p["c"].get(path, headers=bh).status_code in (402, 403), path
    rq = p["c"].post("/service-ops/buyer/requests", json={"kind": "schedule_change", "location_id": loc["id"],
                                                         "body": "TEST: please move to 10am"}, headers=bh)
    assert rq.status_code == 200
    got = p["c"].get("/service-ops/requests", headers=_o(p)).json()["items"]
    assert got[0]["customer_id"] == mine["id"] and got[0]["body"] == "TEST: please move to 10am"
    # Buyer portal switched off by EvoSys: refused.
    _admin(p, {"buyer_portal_enabled": False})
    assert p["c"].get("/service-ops/buyer/home", headers=bh).status_code == 402
    _admin(p, {"buyer_portal_enabled": True})
    assert p["c"].get("/service-ops/buyer/home", headers=bh).status_code == 200
    # Revoked: can't sign in and the old session is refused.
    p["c"].post("/service-ops/buyers/%s/revoke" % inv["id"], headers=_o(p))
    assert p["c"].post("/auth/login", data={"username": "pat@example.com", "password": PW}).status_code in (400, 401, 403)
    assert p["c"].get("/service-ops/buyer/home", headers=bh).status_code in (401, 403)
    assert p["c"].post("/service-ops/buyers/%s/setup-link" % inv["id"], headers=_o(p)).status_code == 409
    assert p["db"].query(EmailMessage).count() == 0
