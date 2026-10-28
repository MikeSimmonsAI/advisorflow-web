# -*- coding: utf-8 -*-
"""#272: who counts as "this workspace's people" on every Wholesale picker and
alert, server setting names kept from tenants, and the prepared (never run)
seat conversion script.

A second Wholesale tenant (BookaBoost Wholesale) is reached by people through a
workspace MEMBERSHIP, not only by their home org. Every list and picker must
include them, and must never include EvoSys's people. EvoSys's own org keeps
its old home-org rule exactly. All records synthetic.
"""
import itertools

import pytest

from app.models.models import User
from app.models.sales_models import Membership

from test_bookaboost_isolation import _h, _m, _user, world  # noqa: F401  (world is a fixture)

_SEQ = itertools.count(1)


@pytest.fixture()
def ppl(db_session, world):
    """A BookaBoost person who belongs ONLY by membership (home org elsewhere -
    here, EvoSys's org, the hardest case), and a membership-only BookaBoost
    admin with no home org."""
    db, evo, bab = db_session, world["evo"], world["bab"]
    rep = _user(db, evo, "bab-rep-%d@example.com" % next(_SEQ), role="advisor")
    _m(db, rep, "customer_org", bab.id, role="advisor")
    madmin = _user(db, None, "bab-madmin-%d@example.com" % next(_SEQ), role="advisor")
    _m(db, madmin, "customer_org", bab.id, role="org_admin")
    return dict(world, rep=rep, madmin=madmin)


# ── the one helper ──────────────────────────────────────────────────────────

def test_workspace_people_are_home_org_plus_members_for_bookaboost(db_session, ppl):
    from app.services import wholesale_team as T
    ids = {u.id for u in T.people_q(db_session, ppl["bab"].id).all()}
    assert {ppl["bab_admin"].id, ppl["bab_plain"].id, ppl["rep"].id, ppl["madmin"].id} <= ids
    assert ppl["evo_user"].id not in ids and ppl["evo_plain"].id not in ids and ppl["operator"].id not in ids


def test_evosys_people_rule_is_unchanged(db_session, ppl):
    """EvoSys's own org: home-org users only, as before (an operator who only
    holds an EvoSys membership is not on EvoSys's people lists)."""
    from app.services import wholesale_team as T
    ids = {u.id for u in T.people_q(db_session, ppl["evo"].id).all()}
    homes = {u.id for u in db_session.query(User).filter(User.organization_id == ppl["evo"].id).all()}
    assert ids == homes
    assert ppl["operator"].id not in ids


def test_admins_are_judged_by_their_role_in_this_workspace(db_session, ppl):
    from app.services import wholesale_team as T
    bab_admins = {u.id for u in T.admins_q(db_session, ppl["bab"].id, ("org_admin",)).all()}
    assert ppl["madmin"].id in bab_admins and ppl["bab_admin"].id in bab_admins
    # rep is an EvoSys org_admin? no - but an EvoSys admin who is only an
    # advisor in BookaBoost must not be a BookaBoost admin:
    both = _user(db_session, ppl["evo"], "evo-admin-bab-adv-%d@example.com" % next(_SEQ), role="org_admin")
    _m(db_session, both, "customer_org", ppl["bab"].id, role="advisor")
    bab_admins = {u.id for u in T.admins_q(db_session, ppl["bab"].id, ("org_admin",)).all()}
    assert both.id not in bab_admins and ppl["evo_plain"].id not in bab_admins
    god = _user(db_session, None, "god-%d@example.com" % next(_SEQ), role="god_admin")
    _m(db_session, god, "customer_org", ppl["bab"].id, role="org_admin")
    assert god.id not in {u.id for u in T.admins_q(db_session, ppl["bab"].id, ("org_admin",)).all()}


# ── every picker / recipient list uses it ────────────────────────────────────

def test_settings_assignees_list_and_patch_use_workspace_people(client, db_session, ppl):
    h = _h(db_session, ppl["bab_admin"], ws=ppl["bab"].id)
    r = client.get("/wholesale/settings/assignees", headers=h)
    assert r.status_code == 200, r.text[:300]
    ids = {i["id"] for i in r.json()["items"]}
    assert ppl["rep"].id in ids and ppl["madmin"].id in ids
    assert ppl["evo_user"].id not in ids and ppl["operator"].id not in ids
    ok = client.patch("/wholesale/settings", headers=h, json={"inquiry_assignee_id": ppl["rep"].id})
    assert ok.status_code == 200, ok.text[:300]
    bad = client.patch("/wholesale/settings", headers=h, json={"inquiry_assignee_id": ppl["evo_user"].id})
    assert bad.status_code == 400


def test_evosys_settings_assignees_never_list_bookaboost_people(client, db_session, ppl):
    h = _h(db_session, ppl["evo_user"], ws=ppl["evo"].id)
    r = client.get("/wholesale/settings/assignees", headers=h)
    assert r.status_code == 200, r.text[:300]
    ids = {i["id"] for i in r.json()["items"]}
    assert ppl["bab_admin"].id not in ids and ppl["madmin"].id not in ids
    bad = client.patch("/wholesale/settings", headers=h, json={"inquiry_assignee_id": ppl["madmin"].id})
    assert bad.status_code == 400


def test_alert_recipients_are_the_right_workspaces_people(db_session, ppl):
    from app.services import wholesale_notify as N
    db = db_session
    bab = set(N.recipients(db, ppl["bab"].id))
    assert ppl["madmin"].id in bab and ppl["bab_admin"].id in bab
    assert not bab & {ppl["evo_user"].id, ppl["evo_plain"].id, ppl["operator"].id}
    assert N.recipients(db, ppl["bab"].id, assignee_id=ppl["rep"].id) == [ppl["rep"].id]
    # An EvoSys person named as BookaBoost's assignee is ignored -> admins.
    assert ppl["evo_user"].id not in N.recipients(db, ppl["bab"].id, assignee_id=ppl["evo_user"].id)
    evo = set(N.recipients(db, ppl["evo"].id))
    assert not evo & {ppl["bab_admin"].id, ppl["madmin"].id}
    assert ppl["bab_admin"].id not in N.recipients(db, ppl["evo"].id, assignee_id=ppl["bab_admin"].id)


def test_callback_owner_deal_room_staff_sms_and_signer_stay_in_workspace(db_session, ppl):
    from fastapi import HTTPException

    from app.services import wholesale_deal_room_setup as DRS
    from app.services import wholesale_ops as OPS
    from app.services import wholesale_sms as WS
    from app.services import wholesale_team as T
    db, bab, evo = db_session, ppl["bab"], ppl["evo"]
    assert OPS.assignable_user(db, bab.id, ppl["rep"].id) == ppl["rep"].id
    with pytest.raises(HTTPException):
        OPS.assignable_user(db, bab.id, ppl["evo_plain"].id)
    with pytest.raises(HTTPException):
        OPS.assignable_user(db, evo.id, ppl["madmin"].id)
    staff = {p["user_id"] for p in DRS.staff_people(db, bab.id)}
    assert ppl["rep"].id in staff and ppl["madmin"].id in staff and ppl["evo_user"].id not in staff
    assert ppl["bab_admin"].id not in {p["user_id"] for p in DRS.staff_people(db, evo.id)}

    class _Lead:
        assigned_to_id = None
        organization_id = bab.id
    who = WS.attribution_user_id(db, _Lead())
    assert who in {u.id for u in T.admins_q(db, bab.id, ("org_admin",)).all()}
    _Lead.organization_id = evo.id
    assert WS.attribution_user_id(db, _Lead()) not in (ppl["bab_admin"].id, ppl["madmin"].id)


def test_appointment_reps_are_this_workspaces_people_only(client, db_session, ppl):
    db = db_session
    admin_h = _h(db, ppl["bab_admin"], ws=ppl["bab"].id)
    assert client.post("/wholesale/team/members", headers=admin_h,
                       json={"user_id": ppl["rep"].id, "role": "field_specialist"}).status_code == 200
    from app.services import wholesale_appointments as A
    reps = {r["user_id"] for r in A.reps(db, ppl["bab"].id, ppl["bab_admin"])}
    assert ppl["rep"].id in reps
    assert not reps & {ppl["evo_user"].id, ppl["operator"].id, ppl["evo_plain"].id}
    evo_reps = {r["user_id"] for r in A.reps(db, ppl["evo"].id, ppl["evo_user"])}
    assert not evo_reps & {ppl["bab_admin"].id, ppl["madmin"].id}
    # The rep's BookaBoost field role is BookaBoost's only.
    from app.services import wholesale_team as T
    assert T.role_of(db, ppl["rep"], ppl["bab"].id) == "field_specialist"
    assert T.role_of(db, ppl["rep"], ppl["evo"].id) is None


# ── invitations ──────────────────────────────────────────────────────────────

def test_only_a_bookaboost_admin_can_invite_into_bookaboost(client, db_session, ppl):
    db, bab = db_session, ppl["bab"]
    body = {"email": "new-%d@example.com" % next(_SEQ), "full_name": "New", "role": "advisor"}
    r = client.post("/admin/users", headers=_h(db, ppl["bab_plain"], ws=bab.id), json=body)
    assert r.status_code == 403, r.text[:300]
    # An EvoSys admin naming BookaBoost's workspace holds no membership there:
    # whatever happens, nobody lands in BookaBoost.
    body2 = {"email": "new-%d@example.com" % next(_SEQ), "full_name": "New", "role": "advisor"}
    client.post("/admin/users", headers=_h(db, ppl["evo_plain"], ws=bab.id), json=body2)
    db.expire_all()
    u = db.query(User).filter(User.email == body2["email"]).first()
    assert u is None or u.organization_id != bab.id
    assert u is None or not db.query(Membership).filter(
        Membership.user_id == u.id, Membership.scope_type == "customer_org",
        Membership.scope_id == bab.id).first()


# ── server setting names ─────────────────────────────────────────────────────

def test_env_name_hiding_unit():
    from app.services import env_name_privacy as ENV

    class Tenant:
        role = "org_admin"

    class God:
        role = "god_admin"
    payload = {"providers": [{"key": "attom", "missing_env": ["ATTOM_API_KEY"],
                              "why": "credential not set on the platform: ATTOM_API_KEY"},
                             {"key": "x", "env": "MEDIA_STORAGE_BACKEND", "why": "needs MEDIA_STORAGE_BACKEND"}]}
    hidden = ENV.for_viewer(Tenant(), payload)
    text = repr(hidden)
    assert "ATTOM_API_KEY" not in text and "MEDIA_STORAGE_BACKEND" not in text
    assert hidden["providers"][0]["missing_env"] == [] and hidden["providers"][1]["env"] is None
    assert ENV.for_viewer(God(), payload) is payload


def test_evaluator_and_settings_screens_show_no_setting_names_to_tenants(client, db_session, ppl, monkeypatch):
    from app.services import env_name_privacy as ENV
    names = ENV._registry_names()
    assert names, "no provider registry names found - the check below would prove nothing"
    for k in names:
        monkeypatch.delenv(k, raising=False)
    for who, org in ((ppl["bab_admin"], ppl["bab"]), (ppl["evo_user"], ppl["evo"])):
        h = _h(db_session, who, ws=org.id)
        for path in ("/wholesale/evosense/provider-evaluations/setup",
                     "/wholesale/evosense/providers", "/wholesale/settings",
                     "/wholesale/enrichment/providers"):
            r = client.get(path, headers=h)
            assert r.status_code == 200, (path, r.status_code, r.text[:200])
            leaked = [n for n in names if n in r.text]
            assert not leaked, "%s shows setting names to a tenant admin: %s" % (path, leaked)


# ── the prepared seat conversion (dry-run default; never run in production) ──

def test_seat_conversion_script_is_dry_run_and_never_touches_evosys_rows(db_session, ppl):
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "convert_second_tenant_wholesale_seats.py")
    spec = importlib.util.spec_from_file_location("convert_seats", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    db = db_session
    # A BookaBoost person who was put on "the" team before seats were per workspace.
    old = _user(db, ppl["bab"], "old-seat-%d@example.com" % next(_SEQ), role="advisor")
    _m(db, old, "customer_org", ppl["bab"].id, role="advisor")
    _m(db, old, "product", "wholesale", "canvasser")
    before = {(m.user_id, m.scope_id, m.is_active, m.role) for m in db.query(Membership).all()}
    rows = mod.plan(db)
    assert [r["user_id"] for r in rows] == [old.id]
    assert rows[0]["scope_id"] == "wholesale:%s" % ppl["bab"].id and rows[0]["role"] == "canvasser"
    assert {(m.user_id, m.scope_id, m.is_active, m.role) for m in db.query(Membership).all()} == before
    assert mod.apply(db, rows) == 1
    after = {(m.user_id, m.scope_id, m.is_active, m.role) for m in db.query(Membership).all()}
    assert before <= after and len(after - before) == 1           # legacy rows untouched
    assert mod.plan(db) == []                                       # idempotent


# ── a BookaBoost admin never gains GOD ───────────────────────────────────────

def test_a_bookaboost_admin_never_gains_god(client, db_session, ppl):
    db, bab = db_session, ppl["bab"]
    admin = ppl["bab_admin"]
    h = _h(db, admin, ws=bab.id)
    for path in ("/god/customers", "/god/users"):
        assert client.get(path, headers=h).status_code in (401, 403, 404), path
    assert client.post("/god/customers/%s/users" % bab.id, headers=h,
                       json={"email": "x-%d@example.com" % next(_SEQ), "full_name": "X",
                             "role": "org_admin"}).status_code in (401, 403, 404)
    for target in (admin, ppl["bab_plain"]):
        r = client.patch("/admin/users/%s" % target.id, headers=h, json={"role": "god_admin"})
        assert r.status_code >= 400, r.text[:200]
    # Seat and team changes cannot carry a platform role either.
    assert client.post("/wholesale/team/members", headers=h,
                       json={"user_id": ppl["bab_plain"].id, "role": "god_admin"}).status_code >= 400
    db.expire_all()
    assert db.query(User).filter(User.role == "god_admin", User.id.in_(
        [admin.id, ppl["bab_plain"].id, ppl["rep"].id, ppl["madmin"].id])).count() == 0
