# -*- coding: utf-8 -*-
"""BookaBoost vs EvoSys: independent, workspace-scoped Wholesale seats (#272).

EvoSys keeps the legacy product row `wholesale`; the second tenant (BookaBoost)
has its own `wholesale:<org_id>` rows. Neither tenant's admin can grant, revoke
or read the other's seats. All orgs and people are synthetic.
"""
import itertools

import pytest

from app.models.sales_models import Membership
from app.services import product_access as PA
from app.services import wholesale_team as T
from tests.test_bookaboost_wholesale_tenant import (  # noqa: F401  (fixture + helpers)
    _contexts, _h, _m, _tile, _user, t)

_N = itertools.count(1)


def _rows(db, key):
    return db.query(Membership).filter(Membership.scope_type == "product", Membership.scope_id == key,
                                       Membership.is_active.is_(True)).all()


def _ids(db, key):
    return {m.user_id for m in _rows(db, key)}


def test_seat_keys_are_per_tenant(t):
    assert PA.seat_key(t["evo"]) == "wholesale"
    assert PA.seat_key(t["bab"]) == "wholesale:%s" % t["bab"].id
    assert PA.seat_key(t["crm"]) == "wholesale"          # not an explicit second tenant
    assert PA.seat_key(None) == "wholesale"


def test_bookaboost_admin_grant_and_revoke_never_touch_evosys_seats(t):
    db = t["db"]
    evo_before = _ids(db, "wholesale")
    rep = _user(db, t["bab"], "bab-rep-%d@example.com" % next(_N), role="advisor")
    T.add_or_update(db, t["bab"].id, t["bab_admin"], rep.id, T.MEMBER)
    assert _ids(db, "wholesale") == evo_before                       # EvoSys untouched
    assert rep.id in _ids(db, "wholesale:" + t["bab"].id)
    T.remove(db, t["bab"].id, t["bab_admin"], rep.id)
    assert _ids(db, "wholesale") == evo_before
    assert rep.id not in _ids(db, "wholesale:" + t["bab"].id)


def test_bookaboost_admin_cannot_grant_or_revoke_an_evosys_person(t):
    db = t["db"]
    with pytest.raises(LookupError):
        T.add_or_update(db, t["bab"].id, t["bab_admin"], t["evo_admin"].id, T.MEMBER)
    with pytest.raises(LookupError):
        T.remove(db, t["bab"].id, t["bab_admin"], t["evo_ws"].id)
    assert t["evo_ws"].id in _ids(db, "wholesale")                   # seat still active


def test_team_view_is_scoped_to_its_own_workspace_and_seats(t):
    db = t["db"]
    view = T.team(db, t["bab"].id)
    ids = {m["user_id"] for m in view["members"]} | {m["user_id"] for m in view["not_on_team"]}
    assert t["evo_ws"].id not in ids and t["evo_admin"].id not in ids
    # A legacy EvoSys seat on a BookaBoost person is NOT a BookaBoost team seat.
    assert t["bab_ws"].id not in {m["user_id"] for m in view["members"]}


def test_evosys_seat_does_not_open_bookaboost_and_vice_versa(t):
    db = t["db"]
    rep = _user(db, t["bab"], "bab-rep2-%d@example.com" % next(_N), role="advisor")
    _m(db, rep, "customer_org", t["bab"].id, role="advisor")
    assert not PA.may_use_in(db, rep, t["bab"], PA.WHOLESALE)
    _m(db, rep, "product", "wholesale", "member")                    # legacy EvoSys seat
    assert not PA.may_use_in(db, rep, t["bab"], PA.WHOLESALE)        # still closed in BookaBoost
    T.add_or_update(db, t["bab"].id, t["bab_admin"], rep.id, T.MEMBER)
    assert PA.may_use_in(db, rep, t["bab"], PA.WHOLESALE)
    only = _user(db, t["bab"], "bab-only-%d@example.com" % next(_N), role="advisor")
    T.add_or_update(db, t["bab"].id, t["bab_admin"], only.id, T.MEMBER)
    assert not PA.may_use_in(db, only, t["evo"], PA.WHOLESALE)


def test_bookaboost_seats_do_not_turn_on_the_evosys_lock(t):
    db = t["db"]
    before = PA.lock_on(db)
    rep = _user(db, t["bab"], "bab-rep3-%d@example.com" % next(_N), role="advisor")
    T.add_or_update(db, t["bab"].id, t["bab_admin"], rep.id, T.MEMBER)
    assert PA.lock_on(db) == before
    assert PA.lock_on(db, "wholesale:" + t["bab"].id)


def test_roles_are_judged_per_workspace(t):
    db = t["db"]
    rep = _user(db, t["bab"], "bab-field-%d@example.com" % next(_N), role="advisor")
    _m(db, rep, "product", "wholesale", T.CANVASSER)                 # legacy EvoSys canvasser seat
    assert T.role_of(db, rep) is None                                # home workspace is BookaBoost
    T.add_or_update(db, t["bab"].id, t["bab_admin"], rep.id, T.FIELD_SPECIALIST)
    assert T.role_of(db, rep) == T.FIELD_SPECIALIST


def test_legacy_evosys_seat_gives_no_bookaboost_tile_or_experience(client, t):
    db = t["db"]
    p = _user(db, None, "cross-%d@example.com" % next(_N), role="advisor")
    _m(db, p, "customer_org", t["bab"].id, role="advisor")
    _m(db, p, "product", "wholesale", "member")
    c = _contexts(client, db, p)
    exp = {x["organization_id"]: x["experience"] for x in c["workspace_contexts"]}
    assert exp.get(t["bab"].id) != "wholesale"
    assert _tile(c) == []


def test_bookaboost_seat_holder_gets_bookaboost_tile_only(client, t):
    db = t["db"]
    p = _user(db, t["bab"], "bab-seat-%d@example.com" % next(_N), role="advisor")
    _m(db, p, "customer_org", t["bab"].id, role="advisor")
    T.add_or_update(db, t["bab"].id, t["bab_admin"], p.id, T.MEMBER)
    tiles = _tile(_contexts(client, db, p))
    assert len(tiles) == 1 and tiles[0]["organization_id"] == t["bab"].id


def test_http_team_admin_cannot_cross_tenants(client, t):
    db = t["db"]
    h = _h(db, t["bab_admin"], t["bab"].id)
    r = client.get("/wholesale/team", headers=h)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    emails = {m["email"] for m in body["members"] + body["not_on_team"]}
    assert t["evo_admin"].email not in emails and t["evo_ws"].email not in emails
    r = client.post("/wholesale/team/members", headers=h,
                    json={"user_id": t["evo_admin"].id, "role": "member", "confirm_lock": True})
    assert r.status_code == 404
    r = client.delete("/wholesale/team/members/%s" % t["evo_ws"].id, headers=h)
    assert r.status_code == 404
    assert t["evo_ws"].id in _ids(db, "wholesale")
