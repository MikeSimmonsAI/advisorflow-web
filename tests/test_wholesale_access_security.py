# -*- coding: utf-8 -*-
"""#439 final security review: the gaps found in the release pass, each pinned.

- Protected logins (owner/admin, yourself, anyone above a delegated team admin)
  can't be changed, suspended, reactivated or removed - and the directory says so.
- A suspended or removed custom-access login does NOT fall back to an ordinary
  advisor login (the CRM side).
- Custom logins can't change the CRM side's own settings (Twilio, products...).
- Paid lookups (contact batches, RentCast valuation) need the paid permission.
- Records that hang off a deal (approvals, offers...) follow that deal's owner,
  in lists and at their direct address.
- Invitation tokens: wrong, replayed, for someone else, or worth more than the
  sender now holds - all refused.
- Switching workspaces never carries one workspace's access into the other.
Synthetic people only; no email is sent."""
from app.models.models import AuditLogEntry, User
from app.models.wholesale_access_models import WholesaleMemberAccess
from app.models.wholesale_models import WholesaleApproval, WholesaleOffer
from tests.test_bookaboost_invites import _h  # noqa: F401
from tests.test_wholesale_access_builder import _deal, _invite_new, _login_new, _ok, _profile, sent, ws  # noqa: F401
from tests.test_bookaboost_invites import w  # noqa: F401


def _uid(db, email):
    return db.query(User).filter(User.email == email).one().id


def _denied(db):
    return db.query(AuditLogEntry).filter(AuditLogEntry.action == "wholesale_access.escalation_denied").count()


# ── A. protected owner access ───────────────────────────────────────────────
def test_owner_row_is_protected_read_only_and_every_change_is_refused(ws):
    p = ws
    owner_id = p["evo_seat"].id
    members = {m["user_id"]: m for m in _ok(p["c"].get("/wholesale/team/directory", headers=p["owner_h"]))["members"]}
    assert members[owner_id]["protected"]["kind"] == "owner"            # the screen shows View, not Manage
    acc = _ok(p["c"].get("/wholesale/team/members/%s/access" % owner_id, headers=p["owner_h"]))
    assert acc["protected"]["kind"] == "owner" and "God Mode" in acc["protected"]["reason"]
    assert acc["permissions"]                                           # a read-only view has something to show
    for method, path, body in (("put", "access", {"profile_id": None, "grants": {"deals": ["view"]}}),
                               ("post", "suspend", {}), ("post", "reactivate", {}), ("post", "remove", {})):
        r = getattr(p["c"], method)("/wholesale/team/members/%s/%s" % (owner_id, path), headers=p["owner_h"], json=body)
        assert r.status_code == 400 and "protected" in r.text, (path, r.text)


def test_a_delegated_team_admin_cannot_touch_anyone_with_more_access(ws):
    p, db = ws, ws["db"]
    lead_prof = _profile(p, "Team lead", "custom", permissions={"team_admin": ["view", "manage"], "properties": ["view"]},
                         record_scope="assigned")
    lh = _login_new(p, _invite_new(p, "lead2@example.com", lead_prof["id"]), "lead2@example.com")
    big = _profile(p, "Manager", "wholesale_manager")
    small = _profile(p, "Viewer", "custom", permissions={"properties": ["view"]}, record_scope="assigned")
    _invite_new(p, "big@example.com", big["id"])
    _invite_new(p, "small@example.com", small["id"])
    big_id, small_id = _uid(db, "big@example.com"), _uid(db, "small@example.com")
    before = _denied(db)
    people = {m["user_id"]: m for m in _ok(p["c"].get("/wholesale/team/directory", headers=lh))["members"]}
    assert people[big_id]["protected"]["kind"] == "above_you" and people[small_id]["protected"] is None
    for what in ("suspend", "remove"):
        r = p["c"].post("/wholesale/team/members/%s/%s" % (big_id, what), headers=lh, json={})
        assert r.status_code == 400 and "only the owner" in r.text
    assert p["c"].post("/wholesale/team/members/%s/suspend" % p["evo_seat"].id, headers=lh, json={}).status_code == 400
    assert _denied(db) == before + 3                                    # every attempt is on the record
    _ok(p["c"].post("/wholesale/team/members/%s/suspend" % small_id, headers=lh, json={}))   # within their reach
    # a suspended person above them can't be reactivated by them either
    _ok(p["c"].post("/wholesale/team/members/%s/suspend" % big_id, headers=p["owner_h"], json={}))
    assert p["c"].post("/wholesale/team/members/%s/reactivate" % big_id, headers=lh, json={}).status_code == 400


# ── suspended / removed logins don't fall back to the CRM side ─────────────
def test_suspended_and_removed_custom_logins_lose_everything_not_just_wholesale(ws):
    p, db = ws, ws["db"]
    prof = _profile(p, "Field", "field_representative")
    fh = _login_new(p, _invite_new(p, "gone@example.com", prof["id"]), "gone@example.com")
    uid = _uid(db, "gone@example.com")
    crm = ("/leads/import-batches",)
    for path in crm:
        assert p["c"].get(path, headers=fh).status_code == 403
    _ok(p["c"].post("/wholesale/team/members/%s/suspend" % uid, headers=p["owner_h"], json={}))
    for path in crm + ("/wholesale/appointments",):
        r = p["c"].get(path, headers=fh)
        assert r.status_code in (402, 403), (path, r.status_code)
    assert "suspended or removed" in p["c"].get("/leads/import-batches", headers=fh).text
    assert p["c"].patch("/settings/profile", headers=fh, json={"full_name": "x"}).status_code == 403
    shell = _ok(p["c"].get("/branding/org", headers=fh))                # the shell still loads...
    assert shell["wholesale_team"]["ended"] is True and shell["wholesale_team"]["modules"] == []   # ...showing nothing
    _ok(p["c"].post("/wholesale/team/members/%s/reactivate" % uid, headers=p["owner_h"], json={}))
    _ok(p["c"].post("/wholesale/team/members/%s/remove" % uid, headers=p["owner_h"], json={}))
    assert db.query(WholesaleMemberAccess).filter(WholesaleMemberAccess.user_id == uid).count() == 0
    for path in crm + ("/wholesale/appointments",):
        assert p["c"].get(path, headers=fh).status_code in (402, 403), path


def test_legacy_and_plain_advisors_are_not_affected_by_the_ended_seat_rule(ws):
    p, db = ws, ws["db"]
    from app.models.sales_models import Membership
    from app.services.auth_service import hash_password
    adv = User(organization_id=p["evo"].id, email="plain.adv@example.com", full_name="Plain", role="advisor",
               is_active=True, password_hash=hash_password("Pass12345!"), must_change_password=False)
    db.add(adv)
    db.commit()
    db.add(Membership(user_id=adv.id, scope_type="product", scope_id="wholesale", role="member", is_active=False))
    db.commit()
    # an old, non-custom ended seat: still an ordinary login (the ended-seat
    # rule would refuse this profile edit; the plain advisor gets it)
    assert p["c"].patch("/settings/profile", headers=_h(db, adv), json={"full_name": "Plain Two"}).status_code == 200


# ── CRM-side settings ──────────────────────────────────────────────────────
def test_custom_logins_keep_their_profile_but_not_crm_settings(ws):
    p = ws
    prof = _profile(p, "VA", "research_assistant")
    vh = _login_new(p, _invite_new(p, "va.settings@example.com", prof["id"]), "va.settings@example.com")
    for method, path, body in (("put", "/settings/twilio", {}), ("put", "/settings/products", {}),
                               ("patch", "/settings/booking", {}), ("put", "/settings/appointment-types", {}),
                               ("put", "/settings/social-links", {}), ("patch", "/settings/admin/profile/x1", {})):
        r = getattr(p["c"], method)(path, headers=vh, json=body)
        assert r.status_code == 403, path
        # and the custom-access engine itself refuses it (not only this
        # workspace's wholesale-only rule)
        from app.services import wholesale_access as A
        va = p["db"].query(User).filter(User.email == "va.settings@example.com").one()
        assert A.refusal(p["db"], va, p["evo"].id, path, method.upper()) == "This login is for the Wholesale side only.", path
    assert p["c"].get("/settings/admin/profile/x1", headers=vh).status_code == 403
    assert p["c"].patch("/settings/profile", headers=vh, json={"full_name": "VA Renamed"}).status_code == 200
    assert p["c"].get("/settings/profile", headers=vh).status_code == 200


# ── paid lookups ───────────────────────────────────────────────────────────
def test_contact_batches_and_valuation_need_the_paid_lookup_permission(ws):
    p = ws
    from app.services import wholesale_access as A
    assert A.module_for("/wholesale/evosense/contact-batches") == "paid_lookups"
    assert A.module_for("/wholesale/evosense/properties/abc123def4567890/valuation") == "paid_lookups"
    assert A.module_for("/wholesale/offers/abc123def4567890") == "deals"
    prof = _profile(p, "Sellers", "custom", permissions={"sellers": ["view", "create", "edit", "approve"]},
                    record_scope="workspace")
    sh = _login_new(p, _invite_new(p, "sellers@example.com", prof["id"]), "sellers@example.com")
    r = p["c"].post("/wholesale/evosense/contact-batches", headers=sh, json={"source": "list:x", "max_lookups": 1})
    assert r.status_code == 403
    assert p["c"].post("/wholesale/evosense/properties/abc123def4567890/valuation", headers=sh).status_code == 403
    assert p["c"].get("/wholesale/evosense/contact-batches", headers=sh).status_code == 403


# ── records that hang off a deal ───────────────────────────────────────────
def test_approvals_and_offers_follow_their_deal_owner(ws):
    p, db = ws, ws["db"]
    prof = _profile(p, "Deals", "custom", permissions={"deals": ["view", "edit"], "team_board": ["view", "approve"]},
                    record_scope="assigned")
    dh = _login_new(p, _invite_new(p, "dealer@example.com", prof["id"]), "dealer@example.com")
    me = _uid(db, "dealer@example.com")
    mine, _ = _deal(db, p["evo"].id, assigned_to=me, addr="1 Mine Rd")
    theirs, _ = _deal(db, p["evo"].id, assigned_to=p["evo_seat"].id, addr="2 Theirs Rd")
    unassigned, _ = _deal(db, p["evo"].id, assigned_to=None, addr="3 Nobody Rd")
    rows = {}
    for d in (mine, theirs, unassigned):
        a = WholesaleApproval(organization_id=p["evo"].id, deal_id=d.id, kind="offer", status="pending")
        o = WholesaleOffer(organization_id=p["evo"].id, deal_id=d.id)
        db.add_all([a, o])
        db.commit()
        rows[d.id] = (a.id, o.id)
    listed = {a["deal_id"] for a in _ok(p["c"].get("/wholesale/approvals", headers=dh))["approvals"]}
    assert listed == {mine.id}
    for d in (theirs, unassigned):
        a_id, o_id = rows[d.id]
        r = p["c"].post("/wholesale/approvals/%s/decide" % a_id, headers=dh, json={"decision": "approved"})
        assert r.status_code == 403 and "isn't assigned" in r.text
        r = p["c"].patch("/wholesale/offers/%s" % o_id, headers=dh, json={"status": "accepted"})
        assert r.status_code == 403 and "isn't assigned" in r.text
    a_id, o_id = rows[mine.id]
    r = p["c"].patch("/wholesale/offers/%s" % o_id, headers=dh, json={})
    assert "isn't assigned" not in r.text and r.status_code != 403      # the fence lets their own through
    owner_list = {a["deal_id"] for a in _ok(p["c"].get("/wholesale/approvals", headers=p["owner_h"]))["approvals"]}
    assert owner_list == {mine.id, theirs.id, unassigned.id}           # the owner still sees all


# ── invitation tokens ──────────────────────────────────────────────────────
def test_wrong_replayed_and_outgrown_invitation_tokens_are_refused(ws):
    p, db = ws, ws["db"]
    prof = _profile(p, "Buyers", "buyer_disposition")
    rep_h = _h(db, p["bab_rep"])
    for bad in ("", "wsi_not-a-real-token", "stf_wrong-kind-of-token"):
        r = p["c"].post("/wholesale-invites/accept", headers=rep_h, json={"token": bad})
        assert r.status_code == 400, bad
    out = _ok(p["c"].post("/wholesale/team/invites", headers=p["owner_h"],
                          json={"email": "rep@bab.example.com", "profile_id": prof["id"]}))
    token = out["link"].split("token=")[1]
    _ok(p["c"].post("/wholesale-invites/accept", headers=rep_h, json={"token": token}))
    assert p["c"].post("/wholesale-invites/accept", headers=rep_h, json={"token": token}).status_code == 400  # replay
    assert p["c"].post("/wholesale-invites/accept", headers=_h(db, p["bab_owner"]),
                       json={"token": token}).status_code == 400

    # an invitation worth more than its sender holds by the time it's accepted
    lead_prof = _profile(p, "Lead", "custom", permissions={"team_admin": ["view", "manage"], "buyer_intel": ["view"]},
                         record_scope="workspace")
    lh = _login_new(p, _invite_new(p, "lead3@example.com", lead_prof["id"]), "lead3@example.com")
    from app.services.auth_service import hash_password
    outsider = User(organization_id=p["bab"].id, email="outsider@example.com", full_name="Out", role="advisor",
                    is_active=True, password_hash=hash_password("Pass12345!"), must_change_password=False)
    db.add(outsider)
    db.commit()
    from app.models.sales_models import Membership
    db.add(Membership(user_id=outsider.id, scope_type="customer_org", scope_id=p["bab"].id, role="advisor", is_active=True))
    db.commit()
    small = _profile(p, "Buyer view", "custom", permissions={"buyer_intel": ["view"]}, record_scope="workspace", h=lh)
    inv = _ok(p["c"].post("/wholesale/team/invites", headers=lh, json={"email": "outsider@example.com",
                                                                      "profile_id": small["id"]}))
    lead_id = _uid(db, "lead3@example.com")
    _ok(p["c"].put("/wholesale/team/members/%s/access" % lead_id, headers=p["owner_h"],
                   json={"profile_id": lead_prof["id"], "denies": {"buyer_intel": ["view"]}}))   # sender loses it
    before = _denied(db)
    r = p["c"].post("/wholesale-invites/accept", headers=_h(db, outsider), json={"token": inv["link"].split("token=")[1]})
    assert r.status_code == 400 and "no longer valid" in r.text
    assert _denied(db) == before + 1


# ── switching workspaces ───────────────────────────────────────────────────
def test_switching_workspaces_never_carries_access_across(ws):
    p, db, evo, bab = ws, ws["db"], ws["evo"], ws["bab"]
    prof = _profile(p, "EVO VA", "custom", permissions={"properties": ["view"]}, record_scope="assigned")
    out = _ok(p["c"].post("/wholesale/team/invites", headers=p["owner_h"],
                          json={"email": "rep@bab.example.com", "profile_id": prof["id"]}))
    rep_h = _h(db, p["bab_rep"])
    _ok(p["c"].post("/wholesale-invites/accept", headers=rep_h, json={"token": out["link"].split("token=")[1]}))
    in_evo = {**rep_h, "X-Workspace-Id": evo.id}
    in_bab = {**rep_h, "X-Workspace-Id": bab.id}
    # EvoSys: only what the EvoSys profile gives
    assert p["c"].get("/wholesale/properties", headers=in_evo).status_code == 200
    assert p["c"].get("/wholesale/buyers", headers=in_evo).status_code == 403
    assert p["c"].get("/wholesale/appointments", headers=in_evo).status_code == 403
    # BookaBoost: their own BookaBoost seat, untouched by the EvoSys profile
    assert p["c"].get("/wholesale/appointments", headers=in_bab).status_code == 200
    assert p["c"].get("/wholesale/buyers", headers=in_bab).status_code == 200
    # one login, no duplicate user
    assert db.query(User).filter(User.email == "rep@bab.example.com").count() == 1
    # suspending them in EvoSys leaves BookaBoost alone
    _ok(p["c"].post("/wholesale/team/members/%s/suspend" % p["bab_rep"].id, headers=p["owner_h"], json={}))
    assert p["c"].get("/wholesale/properties", headers=in_evo).status_code in (402, 403)
    assert p["c"].get("/wholesale/appointments", headers=in_bab).status_code == 200
