# -*- coding: utf-8 -*-
"""Insurance Team & Access (#523): invitations and real sign-ins, starter and
custom profiles, overrides, record scope, server enforcement, delegation
ceilings, suspension/removal, offboarding, history and tenant isolation.

Real authentication: new members set their own password through the setup link
and sign in through /auth/login; existing logins accept with a single-use token.
"""
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from agency_support import agency_feature, h, org, user  # noqa: F401
import app.models.agency_access_models  # noqa: F401
import app.models.agency_retention_models  # noqa: F401
from app.models.agency_access_models import AgencyAccessProfile, AgencyInvite, AgencyMemberAccess
from app.models.agency_models import AgencyAppointment
from app.models.agency_retention_models import AgencyRenewal, AgencyRetentionSettings
from app.models.location_models import Location
from app.models.models import AuditLogEntry, Lead, User
from app.services.agency import retention_demo as D

BASE = "/agency/retention"
PW = "Member-Test-Pw-492!"


@pytest.fixture()
def vg(db_session, monkeypatch):
    db = db_session
    D.ensure_demo(db)
    db.commit()
    o = D.find_org(db)
    owner = user(db, o, "org_admin", "vgowner")
    other = org(db, "Second Agency")
    db.add(AgencyRetentionSettings(organization_id=other.id, enabled=True, display_name="Second Agency"))
    other_admin = user(db, other, "org_admin", "otheradmin")
    db.commit()
    sent = []

    def fake_send(to, subject, body, **kw):
        sent.append({"to": to, "subject": subject, "body": body})
        return {"success": True}
    monkeypatch.setattr("app.services.email_service.send_email_via_provider", fake_send)
    locs = {l.name: l.id for l in db.query(Location).filter(Location.organization_id == o.id)}
    profs = {p.template_key: p.id for p in db.query(AgencyAccessProfile).filter(
        AgencyAccessProfile.organization_id == o.id, AgencyAccessProfile.template_key.isnot(None))}
    return dict(db=db, org=o, owner=owner, other=other, other_admin=other_admin, sent=sent, locs=locs, profs=profs)


def _invite(client, vg, email, profile, locs=("Mesquite",), actor=None, **kw):
    body = {"email": email, "full_name": email.split("@")[0].title(), "profile_id": vg["profs"][profile],
            "location_ids": [vg["locs"][l] for l in locs], **kw}
    return client.post(BASE + "/access/invitations", headers=h(vg["db"], actor or vg["owner"]), json=body)


def _activate_and_login(client, link, email):
    token = parse_qs(urlparse(link).query)["token"][0]
    r = client.post("/auth/staff-activation/accept", json={"token": token, "new_password": PW})
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", data={"username": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer %s" % r.json()["access_token"]}


def _member(client, vg, email, profile, locs=("Mesquite",), **kw):
    r = _invite(client, vg, email, profile, locs, **kw)
    assert r.status_code == 201, r.text
    hdr = _activate_and_login(client, r.json()["link"], email)
    me = client.get(BASE + "/me", headers=hdr)
    assert me.status_code == 200, me.text
    return hdr, me.json()


# ───────────────────────────── invitations and sign-in ─────────────────────────────

def test_owner_invites_new_user_who_sets_password_signs_in_and_lands(client, vg):
    db = vg["db"]
    r = _invite(client, vg, "new.producer@example.com", "producer", send=True)
    assert r.status_code == 201, r.text
    inv = r.json()["invite"]
    assert inv["status"] == "pending" and inv["kind"] == "new_login"
    assert vg["sent"] and vg["sent"][0]["to"] == "new.producer@example.com"        # Send = email through the sandbox
    assert PW not in str(r.json()) and "password" not in str(r.json()["invite"]).lower()
    people = client.get(BASE + "/access/people", headers=h(db, vg["owner"])).json()["items"]
    row = next(p for p in people if p["email"] == "new.producer@example.com")
    assert row["invitation"] == "pending"
    hdr = _activate_and_login(client, r.json()["link"], "new.producer@example.com")
    me = client.get(BASE + "/me", headers=hdr).json()
    assert me["dashboard"] == "producer" and me["profile"] == "Producer / Agent" and me["record_scope"] == "assigned"
    assert {n["key"] for n in me["nav"]} >= {"today", "renewals", "clients", "conversations", "appointments"}
    assert "settings" not in {n["key"] for n in me["nav"]} and "import" not in {n["key"] for n in me["nav"]}
    invs = client.get(BASE + "/access/invitations", headers=h(db, vg["owner"])).json()["items"]
    assert next(i for i in invs if i["email"] == "new.producer@example.com")["status"] == "accepted"
    # Launcher: the agency workspace opens the insurance shell for the new login.
    ctx = client.get("/auth/my-contexts", headers=hdr).json()
    assert any(w["organization_id"] == vg["org"].id and w["program_route"] == "/program/insurance"
               for w in ctx["workspace_contexts"])
    assert db.query(AuditLogEntry).filter(AuditLogEntry.action == "agency_access.invite_accepted").count() == 1


def test_existing_login_joins_without_duplicate_and_keeps_other_program(client, vg):
    db = vg["db"]
    home = org(db, "Other Program", features=("leads",))
    u = user(db, home, "advisor", "existing")
    r = _invite(client, vg, u.email, "retention")
    assert r.status_code == 201 and r.json()["invite"]["kind"] == "existing_login"
    token = parse_qs(urlparse(r.json()["link"]).query)["token"][0]
    # A different login cannot use it.
    stranger = user(db, home, "advisor", "stranger")
    assert client.post("/agency-invites/accept", headers=h(db, stranger), json={"token": token}).status_code == 403
    ok = client.post("/agency-invites/accept", headers=h(db, u), json={"token": token})
    assert ok.status_code == 200, ok.text
    assert db.query(User).filter(User.email == u.email).count() == 1                  # no duplicate identity
    # Replay fails.
    assert client.post("/agency-invites/accept", headers=h(db, u), json={"token": token}).status_code == 400
    # Their home program still answers them normally (no agency fence there).
    assert client.get("/leads/", headers=h(db, u)).status_code != 403
    # Inside the agency workspace the agency access applies.
    hw = {**h(db, u), "X-Workspace-Id": vg["org"].id}
    me = client.get(BASE + "/me", headers=hw)
    assert me.status_code == 200 and me.json()["dashboard"] == "retention"
    assert client.get("/leads/", headers=hw).status_code == 403                        # fenced to the agency workspace


def test_expired_revoked_and_resent_invitations(client, vg):
    db = vg["db"]
    home = org(db, "P2", features=("leads",))
    u = user(db, home, "advisor", "exp")
    r = _invite(client, vg, u.email, "producer")
    inv_id = r.json()["invite"]["id"]
    old = parse_qs(urlparse(r.json()["link"]).query)["token"][0]
    inv = db.query(AgencyInvite).get(inv_id)
    inv.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    assert client.post("/agency-invites/accept", headers=h(db, u), json={"token": old}).status_code == 400
    rs = client.post(BASE + "/access/invitations/%s/resend" % inv_id, headers=h(db, vg["owner"]), json={})
    assert rs.status_code == 200
    new = parse_qs(urlparse(rs.json()["link"]).query)["token"][0]
    assert new != old
    assert client.post("/agency-invites/accept", headers=h(db, u), json={"token": old}).status_code == 400  # superseded
    assert client.post(BASE + "/access/invitations/%s/revoke" % inv_id, headers=h(db, vg["owner"])).status_code == 200
    assert client.post("/agency-invites/accept", headers=h(db, u), json={"token": new}).status_code == 400
    stored = db.query(AgencyInvite).get(inv_id)
    assert stored.token_hash is None
    assert not any(new in (e.details or "") or old in (e.details or "") for e in db.query(AuditLogEntry).all())


def test_new_login_revoked_before_activation_cannot_sign_in(client, vg):
    r = _invite(client, vg, "never.used@example.com", "producer")
    client.post(BASE + "/access/invitations/%s/revoke" % r.json()["invite"]["id"], headers=h(vg["db"], vg["owner"]))
    token = parse_qs(urlparse(r.json()["link"]).query)["token"][0]
    assert client.post("/auth/staff-activation/accept", json={"token": token, "new_password": PW}).status_code >= 400


def test_retired_profile_or_shrunk_inviter_is_rechecked_at_acceptance(client, vg):
    db = vg["db"]
    home = org(db, "P3", features=("leads",))
    u = user(db, home, "advisor", "late")
    # A delegated admin (custom member with access:manage) invites, then loses that access.
    mgr_hdr, _ = _member(client, vg, "delegate@example.com", "agency_admin", locs=("Mesquite", "Dallas"))
    r = client.post(BASE + "/access/invitations", headers=mgr_hdr, json={
        "email": u.email, "profile_id": vg["profs"]["producer"], "location_ids": [vg["locs"]["Dallas"]]})
    assert r.status_code == 201, r.text
    token = parse_qs(urlparse(r.json()["link"]).query)["token"][0]
    delegate = db.query(User).filter(User.email == "delegate@example.com").one()
    client.patch(BASE + "/access/people/%s" % delegate.id, headers=h(db, vg["owner"]),
                 json={"profile_id": vg["profs"]["analyst"], "location_ids": [vg["locs"]["Dallas"]]})
    bad = client.post("/agency-invites/accept", headers=h(db, u), json={"token": token})
    assert bad.status_code == 400 and "no longer valid" in bad.text
    assert db.query(AuditLogEntry).filter(AuditLogEntry.action == "agency_access.escalation_denied").count() >= 1


# ───────────────────────────── starter dashboards and enforcement ─────────────────────────────

@pytest.mark.parametrize("profile,dash,allowed,denied", [
    ("agency_admin", "admin", ["/today", "/access/people", "/workflows", "/reports"], []),
    ("location_manager", "manager", ["/today", "/renewals", "/reports"], ["/access/invitations"]),
    ("producer", "producer", ["/today", "/renewals", "/clients"], ["/reports", "/workflows", "/access/profiles"]),
    ("retention", "retention", ["/today", "/conversations"], ["/reports", "/access/invitations"]),
    ("scheduler", "scheduler", ["/appointments", "/today"], ["/reports", "/workflows", "/access/profiles"]),
    ("analyst", "analyst", ["/reports"], ["/today", "/renewals", "/clients", "/conversations"]),
])
def test_each_starter_profile_opens_its_dashboard_and_only_its_routes(client, vg, profile, dash, allowed, denied):
    hdr, me = _member(client, vg, "%s.member@example.com" % profile, profile, locs=("Mesquite",))
    assert me["dashboard"] == dash
    d = client.get(BASE + "/dashboard", headers=hdr)
    assert d.status_code == 200 and d.json()["dashboard"] == dash
    for p in allowed:
        assert client.get(BASE + p, headers=hdr).status_code == 200, (profile, p)
    for p in denied:
        assert client.get(BASE + p, headers=hdr).status_code == 403, (profile, p)
    # Never the platform side, and never another program.
    for p in ("/leads/", "/agency/summary", "/wholesale/deals", "/admin/dashboard", "/campaigns/"):
        assert client.get(p, headers=hdr).status_code == 403, (profile, p)


def test_analyst_aggregate_only_and_no_writes(client, vg):
    hdr, me = _member(client, vg, "analyst2@example.com", "analyst")
    d = client.get(BASE + "/dashboard", headers=hdr).json()
    assert d["aggregate_only"] is True and "queue" not in d
    r = vg["db"].query(AgencyRenewal).filter(AgencyRenewal.organization_id == vg["org"].id).first()
    for path, body in (("/renewals/%s/note" % r.id, {"body": "x"}), ("/renewals/%s/claim" % r.id, {}),
                       ("/access/invitations", {"email": "x@example.com"}), ("/ask", {"question": "x"})):
        assert client.post(BASE + path, headers=hdr, json=body).status_code == 403, path
    assert client.get(BASE + "/clients/%s" % r.lead_id, headers=hdr).status_code == 403


def test_scheduler_can_book_but_cannot_record_outcome_or_assign(client, vg):
    db = vg["db"]
    hdr, _ = _member(client, vg, "va@example.com", "scheduler", locs=("Mesquite",))
    va = db.query(User).filter(User.email == "va@example.com").one()
    # A Mesquite renewal assigned to the VA so it is in their "assigned" scope.
    r = db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == vg["org"].id, AgencyRenewal.outcome.is_(None),
                                       AgencyRenewal.review_appointment_id.is_(None),
                                       AgencyRenewal.location_id == vg["locs"]["Mesquite"]).first()
    owner_h = h(db, vg["owner"])
    assert client.post(BASE + "/renewals/%s/assign" % r.id, headers=owner_h,
                       json={"agent_id": va.id, "reason": "scheduling"}).status_code == 200
    assert client.post(BASE + "/renewals/%s/outcome" % r.id, headers=hdr, json={"outcome": "confirmed"}).status_code == 403
    assert client.post(BASE + "/renewals/%s/assign" % r.id, headers=hdr,
                       json={"agent_id": va.id}).status_code == 403
    slots = client.get(BASE + "/renewals/%s/availability" % r.id, headers=hdr).json()
    assert "slots" in slots
    when = (datetime.utcnow() + timedelta(days=3)).replace(hour=17, minute=0, second=0, microsecond=0)
    b = client.post(BASE + "/renewals/%s/book-review" % r.id, headers=hdr, json={"starts_at": when.isoformat()})
    assert b.status_code == 201, b.text


def test_direct_ids_cannot_bypass_scope(client, vg):
    db = vg["db"]
    hdr, _ = _member(client, vg, "dallas.prod@example.com", "producer", locs=("Dallas",))
    other = db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == vg["org"].id,
                                           AgencyRenewal.location_id == vg["locs"]["Mesquite"]).first()
    for path in ("/renewals/%s" % other.id, "/clients/%s" % other.lead_id, "/clients/%s/summary" % other.lead_id):
        assert client.get(BASE + path, headers=hdr).status_code == 404, path
    assert client.post(BASE + "/renewals/%s/note" % other.id, headers=hdr, json={"body": "x"}).status_code == 404
    items = client.get(BASE + "/renewals", headers=hdr).json()["items"]
    me = db.query(User).filter(User.email == "dallas.prod@example.com").one()
    assert all(i["assigned_user_id"] in (me.id, None) and i["location_name"] == "Dallas" for i in items)
    a = client.post(BASE + "/ask", headers=hdr, json={"question": "Who replied about a price increase?"}).json()
    assert all(rec["household"] != "Miller Household" for rec in a["records"])          # assistant obeys scope


def test_custom_profile_and_personal_overrides(client, vg):
    db = vg["db"]
    owner_h = h(db, vg["owner"])
    p = client.post(BASE + "/access/profiles", headers=owner_h, json={
        "name": "Callback desk", "permissions": {"appointments": ["schedule"], "renewals": ["view"]},
        "record_scope": "locations", "dashboard": "scheduler"}).json()
    assert p["permissions"]["appointments"] == ["view", "schedule"]                 # view implied
    hdr, me = _member(client, vg, "custom@example.com", "producer", locs=("Garland",))
    u = db.query(User).filter(User.email == "custom@example.com").one()
    r = client.patch(BASE + "/access/people/%s" % u.id, headers=owner_h, json={
        "profile_id": p["id"], "grants": {"reports": ["view"]}, "denies": {"appointments": ["schedule"]},
        "location_ids": [vg["locs"]["Garland"]]})
    assert r.status_code == 200, r.text
    me = client.get(BASE + "/me", headers=hdr).json()                                # applies on the next request
    assert me["permissions"].get("reports") == ["view"] and "schedule" not in me["permissions"].get("appointments", [])
    assert client.get(BASE + "/reports", headers=hdr).status_code == 200
    rid = client.get(BASE + "/renewals", headers=hdr).json()["items"][0]["id"]
    assert client.post(BASE + "/renewals/%s/book-review" % rid, headers=hdr,
                       json={"starts_at": (datetime.utcnow() + timedelta(days=2)).isoformat()}).status_code == 403


def test_widening_profile_requires_choice_and_freeze_keeps_members(client, vg):
    db = vg["db"]
    owner_h = h(db, vg["owner"])
    pid = vg["profs"]["producer"]
    _member(client, vg, "frozen@example.com", "producer")
    wider = {"permissions": {"renewals": ["view", "edit", "claim", "outcome", "assign"], "today": ["view"]}}
    r = client.patch(BASE + "/access/profiles/%s" % pid, headers=owner_h, json=wider)
    assert r.status_code == 409 and "affected" in r.text
    r = client.patch(BASE + "/access/profiles/%s" % pid, headers=owner_h, json={**wider, "apply": "freeze"})
    assert r.status_code == 200
    u = db.query(User).filter(User.email == "frozen@example.com").one()
    row = db.query(AgencyMemberAccess).filter(AgencyMemberAccess.user_id == u.id).one()
    assert row.profile_id != pid
    frozen = db.query(AgencyAccessProfile).get(row.profile_id)
    assert "assign" not in frozen.permissions_json


def test_retire_profile_requires_moving_members(client, vg):
    owner_h = h(vg["db"], vg["owner"])
    _member(client, vg, "mover@example.com", "retention")
    pid = vg["profs"]["retention"]
    assert client.post(BASE + "/access/profiles/%s/retire" % pid, headers=owner_h, json={}).status_code == 409
    r = client.post(BASE + "/access/profiles/%s/retire" % pid, headers=owner_h, json={"move_to": vg["profs"]["producer"]})
    assert r.status_code == 200 and r.json()["archived"] is True


def test_manager_cannot_grant_more_than_they_hold(client, vg):
    db = vg["db"]
    mgr_hdr, _ = _member(client, vg, "lm@example.com", "location_manager", locs=("Dallas",))
    owner_h = h(db, vg["owner"])
    lm = db.query(User).filter(User.email == "lm@example.com").one()
    # Not delegated: no access administration at all.
    assert client.post(BASE + "/access/invitations", headers=mgr_hdr,
                       json={"email": "z@example.com", "profile_id": vg["profs"]["producer"]}).status_code == 403
    # Delegated: may invite, but only within their own ceiling.
    client.patch(BASE + "/access/people/%s" % lm.id, headers=owner_h, json={
        "profile_id": vg["profs"]["location_manager"], "grants": {"access": ["view", "manage"], "team": ["manage"]},
        "location_ids": [vg["locs"]["Dallas"]]})
    ok = client.post(BASE + "/access/invitations", headers=mgr_hdr, json={
        "email": "dallas.new@example.com", "profile_id": vg["profs"]["producer"], "location_ids": [vg["locs"]["Dallas"]]})
    assert ok.status_code == 201, ok.text
    for body in ({"email": "a1@example.com", "profile_id": vg["profs"]["agency_admin"], "location_ids": [vg["locs"]["Dallas"]]},
                 {"email": "a2@example.com", "profile_id": vg["profs"]["producer"], "location_ids": [vg["locs"]["Mesquite"]]},
                 {"email": "a3@example.com", "profile_id": vg["profs"]["producer"], "record_scope": "agency",
                  "location_ids": [vg["locs"]["Dallas"]]}):
        assert client.post(BASE + "/access/invitations", headers=mgr_hdr, json=body).status_code == 403, body
    # No self-escalation, and owners are protected.
    assert client.patch(BASE + "/access/people/%s" % lm.id, headers=mgr_hdr,
                        json={"profile_id": vg["profs"]["agency_admin"]}).status_code == 403
    assert client.post(BASE + "/access/people/%s/suspend" % vg["owner"].id, headers=mgr_hdr, json={}).status_code == 403


def test_team_scope_does_not_widen_beyond_locations(client, vg):
    db = vg["db"]
    owner_h = h(db, vg["owner"])
    hdr, _ = _member(client, vg, "teamer@example.com", "retention", locs=("Garland",))
    me = db.query(User).filter(User.email == "teamer@example.com").one()
    mesq_user = db.query(User).filter(User.email == "demo.vg.jlopez@example.com").one()     # works Mesquite
    t = client.post(BASE + "/access/teams", headers=owner_h, json={
        "name": "Cross-office", "location_ids": [vg["locs"]["Garland"]], "member_ids": [me.id, mesq_user.id]})
    assert t.status_code == 201, t.text
    items = client.get(BASE + "/renewals", headers=hdr).json()["items"]
    assert all(i["location_name"] == "Garland" for i in items)


def test_reports_to_cycle_rejected(client, vg):
    db = vg["db"]
    owner_h = h(db, vg["owner"])
    _member(client, vg, "c1@example.com", "producer")
    _member(client, vg, "c2@example.com", "producer")
    a = db.query(User).filter(User.email == "c1@example.com").one()
    b = db.query(User).filter(User.email == "c2@example.com").one()
    loc = [vg["locs"]["Mesquite"]]
    assert client.patch(BASE + "/access/people/%s" % a.id, headers=owner_h, json={
        "profile_id": vg["profs"]["producer"], "reports_to_user_id": b.id, "location_ids": loc}).status_code == 200
    r = client.patch(BASE + "/access/people/%s" % b.id, headers=owner_h, json={
        "profile_id": vg["profs"]["producer"], "reports_to_user_id": a.id, "location_ids": loc})
    assert r.status_code == 400 and "loop" in r.text


# ───────────────────────────── suspension, removal, offboarding ─────────────────────────────

def test_suspend_and_remove_never_fall_back_and_offboarding_moves_work(client, vg):
    db = vg["db"]
    owner_h = h(db, vg["owner"])
    hdr, _ = _member(client, vg, "leaver@example.com", "producer", locs=("Mesquite",))
    leaver = db.query(User).filter(User.email == "leaver@example.com").one()
    taker = db.query(User).filter(User.email == "demo.vg.asingh@example.com").one()
    r = db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == vg["org"].id, AgencyRenewal.outcome.is_(None),
                                       AgencyRenewal.location_id == vg["locs"]["Mesquite"]).first()
    client.post(BASE + "/renewals/%s/assign" % r.id, headers=owner_h, json={"agent_id": leaver.id, "reason": "t"})
    assert client.post(BASE + "/access/people/%s/suspend" % leaver.id, headers=owner_h, json={}).status_code == 200
    for p in ("/renewals", "/today", "/leads/"):
        assert client.get(p if p.startswith("/leads/") else BASE + p, headers=hdr).status_code == 403, p
    assert client.get(BASE + "/me", headers=hdr).status_code == 200                      # sign-in shell only
    # Remove requires handling owned work.
    rm = client.post(BASE + "/access/people/%s/remove" % leaver.id, headers=owner_h, json={})
    assert rm.status_code == 409 and "owned_work" in rm.text
    rm = client.post(BASE + "/access/people/%s/remove" % leaver.id, headers=owner_h, json={"reassign_to": taker.id})
    assert rm.status_code == 200, rm.text
    db.refresh(r)
    assert r.assigned_user_id == taker.id
    d = client.get(BASE + "/clients/%s" % r.lead_id, headers=owner_h).json()
    ev = [e for e in d["timeline"] if e["kind"] == "reassigned"]
    assert ev and ev[-1]["detail"]["from"] == leaver.id                                  # history kept
    assert client.get(BASE + "/renewals", headers=hdr).status_code == 403
    # Reactivation restores only the saved access (and needs an active profile).
    re = client.post(BASE + "/access/people/%s/reactivate" % leaver.id, headers=owner_h, json={})
    assert re.status_code == 200 and re.json()["profile"] == "Producer / Agent"
    assert client.get(BASE + "/me", headers=hdr).json()["record_scope"] == "assigned"


# ───────────────────────────── teams, locations, history, isolation ─────────────────────────────

def test_locations_create_and_archive_requires_moving_work(client, vg):
    owner_h = h(vg["db"], vg["owner"])
    n = client.post(BASE + "/access/locations", headers=owner_h, json={"name": "Sunnyvale"})
    assert n.status_code == 201
    a = client.patch(BASE + "/access/locations/%s" % vg["locs"]["Forney"], headers=owner_h, json={"archive": True})
    assert a.status_code == 409
    a = client.patch(BASE + "/access/locations/%s" % vg["locs"]["Forney"], headers=owner_h,
                     json={"archive": True, "move_work_to": vg["locs"]["Rockwall"]})
    assert a.status_code == 200 and a.json()["archived"] is True
    names = [l["name"] for l in client.get(BASE + "/access/locations", headers=owner_h).json()["items"]]
    assert {"Mesquite", "Dallas", "Garland", "Rockwall", "Sunnyvale"} <= set(names)


def test_history_records_without_secrets(client, vg):
    _member(client, vg, "hist@example.com", "producer")
    items = client.get(BASE + "/access/history", headers=h(vg["db"], vg["owner"])).json()["items"]
    labels = {i["label"] for i in items}
    assert {"Created an invitation", "Accepted an invitation"} <= labels
    assert "agi_" not in str(items) and "token" not in str([i["details"] for i in items]).lower()


def test_other_tenant_cannot_see_or_change_agency_access(client, vg):
    db = vg["db"]
    _member(client, vg, "iso@example.com", "producer")
    ho = h(db, vg["other_admin"])
    people = client.get(BASE + "/access/people", headers=ho).json()["items"]
    assert all(p["email"] != "iso@example.com" for p in people)
    u = db.query(User).filter(User.email == "iso@example.com").one()
    assert client.get(BASE + "/access/people/%s" % u.id, headers=ho).status_code == 404
    assert client.patch(BASE + "/access/people/%s" % u.id, headers=ho,
                        json={"profile_id": None}).status_code in (403, 404)
    assert client.post(BASE + "/access/profiles/%s/retire" % vg["profs"]["producer"], headers=ho,
                       json={}).status_code == 404


def test_every_retention_route_is_mapped():
    from app.main import app
    from app.services.agency import access as AX
    missing = []
    for r in app.routes:
        p = getattr(r, "path", "")
        if not p.startswith("/agency/retention"):
            continue
        sample = p.replace("{action}", "suspend")
        import re as _re
        sample = _re.sub(r"\{[^}]+\}", "abc123", sample)
        for m in getattr(r, "methods", []):
            if m in ("HEAD", "OPTIONS"):
                continue
            if not AX.route_permission(sample, m)[0]:
                missing.append("%s %s" % (m, p))
    assert not missing, missing


def test_admin_login_and_god_are_not_fenced(client, vg):
    db = vg["db"]
    assert client.get(BASE + "/access/people", headers=h(db, vg["owner"])).status_code == 200
    from app.models.models import User as U
    g = U(organization_id=None, email="god@example.com", full_name="God", role="god_admin",
          password_hash="x", is_active=True)
    db.add(g)
    db.commit()
    from app.services.agency import access as AX
    assert AX.fence_user(db, g, "/leads/", "GET") is None
