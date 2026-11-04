# -*- coding: utf-8 -*-
"""#439 Wholesale custom access: profiles, per-person access, invitations and
the server fence - for EvoSys Pro AND BookaBoost on one engine.

Sandbox: EvoSys owner + a Francis-style wholesaler + a manager + a restricted
VA + an existing BookaBoost login + a second organization. Synthetic only;
no email is sent (the provider is replaced and counted)."""
import json

import pytest

from app.models.models import AuditLogEntry, User
from app.models.sales_models import Membership
from app.models.wholesale_access_models import WholesaleAccessProfile, WholesaleMemberAccess, WholesaleTeamInvite
from app.models.wholesale_models import WholesaleDeal, WholesaleProperty
from app.services import wholesale_access as A
from app.services.auth_service import hash_password
from tests.test_bookaboost_invites import PASSWORD, _activate_and_login, _h, w  # noqa: F401


@pytest.fixture()
def sent(monkeypatch):
    """Every email the code tries to send lands here instead of a provider."""
    out = []

    def fake(to, subject, body, **kw):
        out.append({"to": to, "subject": subject, "body": body})
        return {"success": True, "provider_message_id": "test"}
    monkeypatch.setattr("app.services.email_service.send_email_via_provider", fake)
    return out


@pytest.fixture()
def ws(client, w, sent):
    db, evo, bab = w["db"], w["evo"], w["bab"]
    owner_h = _h(db, w["evo_seat"], ws=evo.id)
    # BookaBoost's own admin
    bab_owner = User(organization_id=bab.id, email="owner@bab.example.com", full_name="BAB Owner", role="org_admin",
                     is_active=True, password_hash=hash_password("Pass12345!"), must_change_password=False)
    # an existing BookaBoost advisor who will also be invited into EvoSys
    bab_rep = User(organization_id=bab.id, email="rep@bab.example.com", full_name="BAB Rep", role="advisor",
                   is_active=True, password_hash=hash_password(PASSWORD), must_change_password=False)
    db.add_all([bab_owner, bab_rep])
    db.commit()
    db.add_all([Membership(user_id=bab_owner.id, scope_type="customer_org", scope_id=bab.id, role="org_admin", is_active=True),
                Membership(user_id=bab_rep.id, scope_type="customer_org", scope_id=bab.id, role="advisor", is_active=True),
                Membership(user_id=bab_rep.id, scope_type="product", scope_id="wholesale:%s" % bab.id, role="member",
                           is_active=True)])
    db.commit()
    return dict(w, owner_h=owner_h, bab_owner=bab_owner, bab_rep=bab_rep,
                bab_h=_h(db, bab_owner, ws=bab.id), sent=sent, c=client)


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:400])
    return r.json()


def _profile(p, name, template, h=None, **kw):
    return _ok(p["c"].post("/wholesale/team/access/profiles", headers=h or p["owner_h"],
                           json=dict({"name": name, "template_key": template}, **kw)))


def _invite_new(p, email, profile_id, h=None, **kw):
    out = _ok(p["c"].post("/wholesale/team/invites", headers=h or p["owner_h"],
                          json=dict({"email": email, "full_name": "Test Person", "profile_id": profile_id,
                                     "base_url": "https://app.evosyspro.live"}, **kw)))
    return out


def _login_new(p, out, email):
    return _activate_and_login(p["c"], out["link"], email)


def _deal(db, org_id, assigned_to=None, addr="1 Test St"):
    prop = WholesaleProperty(organization_id=org_id, street_address=addr, city="Dallas", state="TX", zip_code="75201",
                             assigned_to_id=assigned_to)
    db.add(prop)
    db.flush()
    d = WholesaleDeal(organization_id=org_id, property_id=prop.id, stage="new_property", assigned_to_id=assigned_to)
    db.add(d)
    db.commit()
    return d, prop


# ── the map is complete and honest ──────────────────────────────────────────
def test_every_wholesale_route_maps_to_a_module_and_toggles_come_from_real_routes():
    from app.main import app
    unmapped = [r.path for r in app.routes if getattr(r, "path", "").startswith("/wholesale/") and A.module_for(r.path) is None]
    assert unmapped == []
    cat = {c["key"]: c for c in A.catalog(app.routes)}
    assert "multifamily" not in cat                              # its screen is the Discovery Inbox (Acquisition Command)
    assert "export" in cat["buyer_intel"]["actions"]             # the one area with real exports
    assert "export" not in cat["deals"]["actions"]               # no fake Export toggle
    assert A.action_for("/wholesale/deals/abc123def456ghi789/stage", "POST") == "edit"
    assert A.action_for("/wholesale/deals/abc123def456ghi789/package/publish", "POST") == "publish"
    assert A.module_for("/wholesale/evosense/properties/abc123def456ghi789/outreach") == "sellers"


def test_effective_access_is_profile_plus_grants_minus_denies():
    eff = A.combine({"deals": ["view", "edit"], "properties": ["view"]}, {"buyer_intel": ["view"]}, {"deals": ["edit"]})
    assert eff == {"deals": ["view"], "properties": ["view"], "buyer_intel": ["view"]}
    assert A.combine({"deals": ["view", "edit"]}, {}, {"deals": ["view"]}) == {}      # no view = nothing
    assert A.clean({"deals": ["edit"], "bogus": ["view"], "settings": ["fly"]}) == {"deals": ["view", "edit"]}
    assert A.clean({"deals": ["edit"]}, add_view=False) == {"deals": ["edit"]}         # a take-away keeps View


# ── profiles ────────────────────────────────────────────────────────────────
def test_profiles_create_duplicate_edit_with_disclosure_and_retire(ws):
    p = ws
    va = _profile(p, "Research VA", "research_assistant")
    assert va["record_scope"] == "workspace" and "properties" in va["permissions"]
    copy = _ok(p["c"].post("/wholesale/team/access/profiles/%s/duplicate" % va["id"], headers=p["owner_h"], json={}))
    assert copy["name"] == "Research VA (copy)" and copy["permissions"] == va["permissions"]
    _invite_new(p, "va1@example.com", va["id"])                        # one person now on the profile
    url = "/wholesale/team/access/profiles/%s" % va["id"]
    wider = dict(va["permissions"], deals=["view", "edit"])
    r = p["c"].put(url, headers=p["owner_h"], json={"permissions": wider})
    assert r.status_code == 409 and r.json()["detail"]["impact"]["members"] == 1        # disclosed, not applied
    assert "Deal creation and management: View" in r.json()["detail"]["impact"]["adds"]
    kept = _ok(p["c"].put(url, headers=p["owner_h"], json={"permissions": wider, "keep_existing_members": True}))
    assert kept["impact"]["kept_on"].startswith("Research VA (before")
    va1 = p["db"].query(User).filter(User.email == "va1@example.com").one()
    row = p["db"].query(WholesaleMemberAccess).filter(WholesaleMemberAccess.user_id == va1.id).one()
    assert row.profile_id != va["id"]                                    # they did NOT get the new access
    r = p["c"].post("/wholesale/team/access/profiles/%s/retire" % row.profile_id, headers=p["owner_h"], json={})
    assert r.status_code == 400                                          # people on it: pick where they go
    _ok(p["c"].post("/wholesale/team/access/profiles/%s/retire" % row.profile_id, headers=p["owner_h"],
                    json={"move_to": copy["id"]}))
    p["db"].refresh(row)
    assert row.profile_id == copy["id"]


# ── a restricted wholesaler, end to end ─────────────────────────────────────
def test_invite_new_wholesaler_restricted_by_module_action_and_records(ws):
    p, db = ws, ws["db"]
    prof = _profile(p, "Acquisition", "acquisition_wholesaler")
    out = _invite_new(p, "francis.test@example.com", prof["id"])
    assert out["link"].startswith("https://app.evosyspro.live/activate?token=")
    assert out["delivery"] is None and p["sent"] == []                   # nothing emailed without Send
    fh = _login_new(p, out, "francis.test@example.com")
    francis = db.query(User).filter(User.email == "francis.test@example.com").one()
    mine, _ = _deal(db, p["evo"].id, assigned_to=francis.id, addr="10 Mine St")
    theirs, theirs_prop = _deal(db, p["evo"].id, assigned_to=p["evo_seat"].id, addr="20 Theirs St")
    g = lambda path: p["c"].get(path, headers=fh).status_code
    assert g("/wholesale/appointments") == 200
    assert g("/wholesale/deals/%s" % mine.id) == 200
    assert g("/wholesale/deals/%s" % theirs.id) == 403                 # not assigned to them
    assert g("/wholesale/properties/%s/photos" % theirs_prop.id) == 403
    ids = [d["id"] for d in p["c"].get("/wholesale/deals", headers=fh).json().get("deals", p["c"].get("/wholesale/deals", headers=fh).json().get("items", []))]
    assert mine.id in ids and theirs.id not in ids                       # the list is narrowed too
    for path in ("/wholesale/buyers", "/wholesale/settings", "/wholesale/funding/partners", "/wholesale/team",
                 "/wholesale/team/directory", "/wholesale/deal-room/overview"):
        assert g(path) == 403, path
    assert p["c"].post("/wholesale/skip-trace/estimate", headers=fh, json={}).status_code == 403   # paid lookup
    assert g("/leads/import-batches") == 403                             # the CRM side
    assert g("/god/customers") == 403                                    # never God Mode
    assert g("/wholesale/buyers/imports/x1234567890abcdef/errors.csv") == 403   # export


def test_permission_changes_apply_on_the_next_request_and_suspend_revokes(ws):
    p, db = ws, ws["db"]
    prof = _profile(p, "Field", "field_representative")
    out = _invite_new(p, "field1@example.com", prof["id"])
    fh = _login_new(p, out, "field1@example.com")
    uid = db.query(User).filter(User.email == "field1@example.com").one().id
    assert p["c"].get("/wholesale/buyers", headers=fh).status_code == 403
    _ok(p["c"].put("/wholesale/team/members/%s/access" % uid, headers=p["owner_h"],
                   json={"profile_id": prof["id"], "grants": {"buyer_intel": ["view"]}}))
    assert p["c"].get("/wholesale/buyers", headers=fh).status_code == 200          # same session, new access
    _ok(p["c"].put("/wholesale/team/members/%s/access" % uid, headers=p["owner_h"],
                   json={"profile_id": prof["id"], "denies": {"appointments": ["view"]}}))
    assert p["c"].get("/wholesale/buyers", headers=fh).status_code == 403
    assert p["c"].get("/wholesale/appointments", headers=fh).status_code == 403
    _ok(p["c"].post("/wholesale/team/members/%s/suspend" % uid, headers=p["owner_h"]))
    assert p["c"].get("/wholesale/canvass/routes", headers=fh).status_code in (402, 403)
    _ok(p["c"].post("/wholesale/team/members/%s/reactivate" % uid, headers=p["owner_h"]))
    assert p["c"].get("/wholesale/canvass/routes", headers=fh).status_code == 200


def test_remove_reassigns_owned_work_and_ends_access(ws):
    p, db = ws, ws["db"]
    prof = _profile(p, "Mgr", "wholesale_manager")
    a = _invite_new(p, "leaver@example.com", prof["id"])
    b = _invite_new(p, "taker@example.com", prof["id"])
    lh = _login_new(p, a, "leaver@example.com")
    _login_new(p, b, "taker@example.com")
    leaver = db.query(User).filter(User.email == "leaver@example.com").one()
    taker = db.query(User).filter(User.email == "taker@example.com").one()
    d, prop = _deal(db, p["evo"].id, assigned_to=leaver.id)
    work = _ok(p["c"].get("/wholesale/team/members/%s/access" % leaver.id, headers=p["owner_h"]))["owned_work"]
    assert work["deals"] == 1 and work["properties"] == 1
    out = _ok(p["c"].post("/wholesale/team/members/%s/remove" % leaver.id, headers=p["owner_h"],
                          json={"reassign_to": taker.id}))
    assert out["moved"]["deals"] == 1
    db.refresh(d)
    db.refresh(prop)
    assert d.assigned_to_id == taker.id and prop.assigned_to_id == taker.id
    assert p["c"].get("/wholesale/deals", headers=lh).status_code in (402, 403)


# ── invitations ─────────────────────────────────────────────────────────────
def test_existing_login_from_the_other_brand_accepts_and_gets_only_this_access(ws):
    p, db, evo, bab = ws, ws["db"], ws["evo"], ws["bab"]
    prof = _profile(p, "Buyers", "buyer_disposition")
    out = _ok(p["c"].post("/wholesale/team/invites", headers=p["owner_h"],
                          json={"email": "rep@bab.example.com", "profile_id": prof["id"], "base_url": "https://app.evosyspro.live"}))
    assert out["invite"]["kind"] == "existing_login" and "/wholesale-invite?token=wsi_" in out["link"]
    token = out["link"].split("token=")[1]
    rep_h = _h(db, p["bab_rep"])
    other = _h(db, p["bab_owner"])
    assert p["c"].post("/wholesale-invites/accept", headers=other, json={"token": token}).status_code == 400  # wrong login
    _ok(p["c"].post("/wholesale-invites/accept", headers=rep_h, json={"token": token}))
    assert p["c"].post("/wholesale-invites/accept", headers=rep_h, json={"token": token}).status_code == 400  # used
    in_evo = {**rep_h, "X-Workspace-Id": evo.id}
    in_bab = {**rep_h, "X-Workspace-Id": bab.id}
    assert p["c"].get("/wholesale/buyers", headers=in_evo).status_code == 200
    assert p["c"].get("/wholesale/appointments", headers=in_evo).status_code == 403       # not in the profile
    assert p["c"].get("/wholesale/appointments", headers=in_bab).status_code == 200       # BookaBoost seat unchanged
    names = [m["email"] for m in _ok(p["c"].get("/wholesale/team/directory", headers=p["owner_h"]))["members"]]
    assert "rep@bab.example.com" in names
    bab_names = [m["email"] for m in _ok(p["c"].get("/wholesale/team/directory", headers=p["bab_h"]))["members"]]
    assert "rep@bab.example.com" in bab_names and "wholesale@evo.example.com" not in bab_names


def test_duplicate_expired_cancelled_and_resent_invitations(ws):
    p, db = ws, ws["db"]
    prof = _profile(p, "VA", "research_assistant")
    first = _invite_new(p, "dup@example.com", prof["id"])
    r = p["c"].post("/wholesale/team/invites", headers=p["owner_h"], json={"email": "dup@example.com", "profile_id": prof["id"]})
    assert r.status_code == 400 and "pending invitation" in r.text
    again = _ok(p["c"].post("/wholesale/team/invites/%s/resend" % first["invite"]["id"], headers=p["owner_h"], json={}))
    assert again["link"] != first["link"]
    assert p["c"].post("/auth/staff-activation/accept", json={"token": first["link"].split("token=")[1],
                                                              "new_password": PASSWORD}).status_code != 200   # old link dead
    _ok(p["c"].post("/wholesale/team/invites/%s/cancel" % first["invite"]["id"], headers=p["owner_h"]))
    assert p["c"].post("/auth/staff-activation/accept", json={"token": again["link"].split("token=")[1],
                                                              "new_password": PASSWORD}).status_code != 200   # cancelled
    # existing-login invitation that expired
    ex = _ok(p["c"].post("/wholesale/team/invites", headers=p["owner_h"], json={"email": "rep@bab.example.com",
                                                                              "profile_id": prof["id"]}))
    inv = db.query(WholesaleTeamInvite).filter(WholesaleTeamInvite.id == ex["invite"]["id"]).one()
    from datetime import datetime, timedelta
    inv.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    r = p["c"].post("/wholesale-invites/accept", headers=_h(db, p["bab_rep"]), json={"token": ex["link"].split("token=")[1]})
    assert r.status_code == 400 and "expired" in r.text
    listed = {i["email"]: i["status"] for i in _ok(p["c"].get("/wholesale/team/invites", headers=p["owner_h"]))["items"]}
    assert listed["rep@bab.example.com"] == "expired" and listed["dup@example.com"] == "cancelled"


def test_send_uses_the_brand_and_is_explicit(ws, monkeypatch):
    p = ws
    from app.services import launch_invitation as LI
    monkeypatch.setattr(LI, "_sending_identity", lambda db, org, brand: type("I", (), {"from_email": "support@brand.example.com"})())
    prof = _profile(p, "VA", "research_assistant", h=p["bab_h"])
    out = _ok(p["c"].post("/wholesale/team/invites", headers=p["bab_h"],
                          json={"email": "new.bab@example.com", "profile_id": prof["id"], "send": True,
                                "base_url": "https://app.bookaboost.live"}))
    assert out["link"].startswith("https://app.bookaboost.live/activate?token=")
    assert out["delivery"]["state"] == "sent" and len(p["sent"]) == 1
    assert "BookaBoost" in p["sent"][0]["subject"] and "EvoSys" not in p["sent"][0]["body"]


# ── escalation, isolation, God Mode ─────────────────────────────────────────
def test_a_delegated_team_admin_cannot_grant_beyond_their_own_access(ws):
    p, db = ws, ws["db"]
    admin_prof = _profile(p, "Team admin", "custom", permissions={"team_admin": ["view", "manage"], "properties": ["view"]},
                          record_scope="assigned")
    out = _invite_new(p, "lead@example.com", admin_prof["id"])
    lh = _login_new(p, out, "lead@example.com")
    ok = p["c"].post("/wholesale/team/access/profiles", headers=lh,
                     json={"name": "Viewer", "permissions": {"properties": ["view"]}, "record_scope": "assigned"})
    assert ok.status_code == 200
    bad = p["c"].post("/wholesale/team/access/profiles", headers=lh,
                      json={"name": "Too much", "permissions": {"buyer_intel": ["view"]}})
    assert bad.status_code == 400 and "don't have" in bad.text
    wide = p["c"].post("/wholesale/team/access/profiles", headers=lh,
                       json={"name": "Wide", "permissions": {"properties": ["view"]}, "record_scope": "workspace"})
    assert wide.status_code == 400
    assert db.query(AuditLogEntry).filter(AuditLogEntry.action == "wholesale_access.escalation_denied").count() == 2
    me = db.query(User).filter(User.email == "lead@example.com").one()
    assert p["c"].put("/wholesale/team/members/%s/access" % me.id, headers=lh,
                      json={"profile_id": p["db"].query(WholesaleAccessProfile).filter_by(name="Wholesale Manager").first().id
                            if False else admin_prof["id"]}).status_code == 400     # own access: refused


def test_admins_and_platform_logins_cannot_be_given_or_invited_into_custom_access(ws):
    p = ws
    prof = _profile(p, "VA", "research_assistant")
    for email in ("owner@bab.example.com", "god@platform.example.com"):
        r = p["c"].post("/wholesale/team/invites", headers=p["owner_h"], json={"email": email, "profile_id": prof["id"]})
        assert r.status_code == 400 and "admin or platform" in r.text
    r = p["c"].put("/wholesale/team/members/%s/access" % p["evo_seat"].id, headers=p["owner_h"], json={"profile_id": prof["id"]})
    assert r.status_code == 400


def test_workspaces_never_see_or_touch_each_others_profiles_people_or_invites(ws):
    p, db = ws, ws["db"]
    evo_prof = _profile(p, "EVO only", "research_assistant")
    _invite_new(p, "evo.va@example.com", evo_prof["id"])
    evo_va = db.query(User).filter(User.email == "evo.va@example.com").one()
    assert [x["name"] for x in _ok(p["c"].get("/wholesale/team/access/profiles", headers=p["bab_h"]))["items"]] == []
    assert p["c"].put("/wholesale/team/access/profiles/%s" % evo_prof["id"], headers=p["bab_h"],
                      json={"name": "Stolen"}).status_code == 404
    assert p["c"].put("/wholesale/team/members/%s/access" % evo_va.id, headers=p["bab_h"],
                      json={"profile_id": evo_prof["id"]}).status_code == 404
    assert _ok(p["c"].get("/wholesale/team/invites", headers=p["bab_h"]))["items"] == []
    bab_prof = _profile(p, "BAB VA", "research_assistant", h=p["bab_h"])
    r = p["c"].post("/wholesale/team/invites", headers=p["bab_h"], json={"email": "x@example.com", "profile_id": evo_prof["id"]})
    assert r.status_code == 404
    assert bab_prof["id"] != evo_prof["id"]


def test_legacy_team_roles_and_admins_keep_their_access(ws):
    p, db = ws, ws["db"]
    legacy = User(organization_id=p["evo"].id, email="legacy@example.com", full_name="Legacy", role="advisor",
                  is_active=True, password_hash=hash_password("Pass12345!"), must_change_password=False)
    db.add(legacy)
    db.commit()
    db.add(Membership(user_id=legacy.id, scope_type="product", scope_id="wholesale", role="member", is_active=True))
    db.commit()
    lh = _h(db, legacy)
    assert p["c"].get("/wholesale/settings", headers=lh).status_code == 200           # full access, as before
    assert p["c"].get("/wholesale/team/directory", headers=lh).status_code == 403     # still not a team admin
    assert p["c"].get("/wholesale/settings", headers=p["owner_h"]).status_code == 200
    assert A.effective(db, legacy, p["evo"].id)["mode"] == "legacy"


def test_preview_and_history(ws):
    p = ws
    prof = _profile(p, "Disp", "buyer_disposition")
    pv = _ok(p["c"].post("/wholesale/team/access/preview", headers=p["owner_h"],
                         json={"profile_id": prof["id"], "denies": {"deal_room": ["publish"]}, "grants": {"funding": ["view"]}}))
    by = {m["module"]: m for m in pv["modules"]}
    assert "publish" not in by["deal_room"]["actions"] and by["funding"]["actions"] == ["view"]
    assert by["settings"]["summary"] == "No access" and "God Mode" in pv["never"]
    _invite_new(p, "hist@example.com", prof["id"])
    acts = [h["action"] for h in _ok(p["c"].get("/wholesale/team/history", headers=p["owner_h"]))["items"]]
    assert "profile_created" in acts and "invited" in acts
