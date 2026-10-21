# -*- coding: utf-8 -*-
"""LANE-E-BOOKABOOST (#272): BookaBoost runs its OWN Wholesale workspace on the
shared engine, next to EvoSys's existing one, which must not change.

Pins:
  * an org explicitly set up as a Wholesale workspace (`wholesale_workspace` +
    `wholesale_real_estate`) opens the Wholesale app for a named seat, with the
    same lock (general platform APIs 403, Wholesale shell 200);
  * EvoSys Wholesale is untouched: same experience, same tile, and EvoSys still
    wins the tile for a person who belongs to both;
  * a customer that merely HAS the Wholesale feature, or a NULL ("everything")
    allow-list, is NOT a Wholesale workspace (no lock, no tile);
  * the two tenants never see each other's Wholesale records, and a BookaBoost
    login cannot name the EvoSys workspace to get in.
All records are synthetic.
"""
import itertools
import json
import uuid

import pytest

from app.models.models import Organization, User
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
WHOLESALE_CORE = ["ai_assist", "availability", "booking", "calendar", "compliance", "email", "imports",
                  "leads", "reports", "sms", "users", "wholesale_real_estate"]
PLATFORM_APIS = ["/leads/", "/campaigns/", "/pipeline/forecast", "/outcomes/summary"]
SHELL_APIS = ["/wholesale/settings", "/notifications/", "/settings/profile", "/branding/org",
              "/auth/my-contexts", "/workspace-views"]


def _org(db, name, slug, feats, industry="generic"):
    o = Organization(name=name, slug=slug if slug == "evo-integrated-solutions-llc" else "%s-%s" % (slug, uuid.uuid4().hex[:4]),
                     plan="trial", industry=industry, is_active=True,
                     enabled_features=None if feats is None else json.dumps(feats))
    db.add(o)
    db.commit()
    return o


def _user(db, org, email, role="org_admin"):
    u = User(organization_id=org.id if org else None, email=email, full_name=email.split("@")[0], role=role,
             password_hash=hash_password("Pass12345!"), is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _m(db, u, st, sid, role="org_admin"):
    db.add(Membership(user_id=u.id, scope_type=st, scope_id=sid, role=role, is_active=True))
    db.commit()


def _h(db, u, ws=None):
    h = {"Authorization": "Bearer %s" % create_access_token(u, db)}
    if ws:
        h["X-Workspace-Id"] = ws
    return h


def _seat(db, u, *orgs):
    for o in orgs:
        _m(db, u, "customer_org", o.id)
    _m(db, u, "product", "wholesale", "member")


@pytest.fixture()
def t(db_session):
    db = db_session
    evo = _org(db, "EVO Integrated Solutions LLC", "evo-integrated-solutions-llc", WHOLESALE_CORE)
    bab = _org(db, "BookaBoost Wholesale (TEST)", "bookaboost-wholesale-test", WHOLESALE_CORE + ["wholesale_workspace"])
    # Has the Wholesale FEATURE but is a normal customer, not a Wholesale workspace (like Fiber Cartel).
    crm = _org(db, "Feature-only Customer (TEST)", "feature-only-test", WHOLESALE_CORE)
    # Names the workspace key but not the engine: incoherent -> not a host.
    half = _org(db, "Half Configured (TEST)", "half-test", ["leads", "users", "wholesale_workspace"])
    # Legacy NULL allow-list: "everything" never makes a Wholesale workspace.
    legacy = _org(db, "Legacy Open (TEST)", "legacy-open-test", None)

    evo_ws = _user(db, evo, "evo-wholesale-%d@example.com" % next(_SEQ))
    _seat(db, evo_ws, evo)
    bab_ws = _user(db, bab, "bab-wholesale-%d@example.com" % next(_SEQ))
    _seat(db, bab_ws, bab)
    both = _user(db, None, "operator-%d@example.com" % next(_SEQ))       # home column empty, like an operator
    _seat(db, both, bab, evo)
    probe = _user(db, crm, "probe-%d@example.com" % next(_SEQ))           # seat + non-host workspaces
    _seat(db, probe, crm, half, legacy)
    bab_staff = _user(db, bab, "bab-staff-%d@example.com" % next(_SEQ))   # BookaBoost member, no seat
    _m(db, bab_staff, "customer_org", bab.id)
    return dict(db=db, evo=evo, bab=bab, crm=crm, half=half, legacy=legacy,
                evo_ws=evo_ws, bab_ws=bab_ws, both=both, probe=probe, bab_staff=bab_staff)


def _contexts(client, db, u):
    r = client.get("/auth/my-contexts", headers=_h(db, u))
    assert r.status_code == 200, r.text[:200]
    return r.json()


def _tile(c):
    return [p for p in (c.get("product_contexts") or []) if p.get("key") == "wholesale"]


def test_bookaboost_workspace_opens_wholesale_for_its_seat(client, t):
    db = t["db"]
    c = _contexts(client, db, t["bab_ws"])
    exp = {x["organization_id"]: x["experience"] for x in c["workspace_contexts"]}
    assert exp == {t["bab"].id: "wholesale"}
    tiles = _tile(c)
    assert len(tiles) == 1 and tiles[0]["organization_id"] == t["bab"].id
    # A BookaBoost member WITHOUT a seat gets the normal workspace, no tile.
    c2 = _contexts(client, db, t["bab_staff"])
    assert c2["workspace_contexts"][0]["experience"] == "platform"
    assert _tile(c2) == []


def test_evosys_wholesale_unchanged(client, t):
    db = t["db"]
    c = _contexts(client, db, t["evo_ws"])
    assert {x["organization_id"]: x["experience"] for x in c["workspace_contexts"]} == {t["evo"].id: "wholesale"}
    assert [p["organization_id"] for p in _tile(c)] == [t["evo"].id]
    # Someone in BOTH: EvoSys still owns the tile; both workspaces open Wholesale.
    c2 = _contexts(client, db, t["both"])
    exp = {x["organization_id"]: x["experience"] for x in c2["workspace_contexts"]}
    assert exp[t["evo"].id] == "wholesale" and exp[t["bab"].id] == "wholesale"
    assert [p["organization_id"] for p in _tile(c2)] == [t["evo"].id]


def test_feature_alone_null_list_or_half_config_is_not_a_wholesale_workspace(client, t):
    db = t["db"]
    c = _contexts(client, db, t["probe"])
    exp = {x["organization_id"]: x["experience"] for x in c["workspace_contexts"]}
    assert exp[t["crm"].id] == "platform"
    assert exp[t["half"].id] == "platform"
    assert exp[t["legacy"].id] == "platform"
    assert _tile(c) == []
    # And no lock: the general platform still answers in those workspaces.
    for org in (t["crm"], t["legacy"]):
        assert client.get("/leads/", headers=_h(db, t["probe"], ws=org.id)).status_code == 200, org.slug


def test_bookaboost_wholesale_lock_matches_evosys(client, t):
    db, u, bab = t["db"], t["bab_ws"], t["bab"]
    for path in PLATFORM_APIS:
        r = client.get(path, headers=_h(db, u, ws=bab.id))
        assert r.status_code == 403, (path, r.status_code)
        detail = r.json()["detail"]
        assert "Wholesale" in detail and "EvoSys" not in detail      # brand-neutral for BookaBoost
    for path in SHELL_APIS:
        r = client.get(path, headers=_h(db, u, ws=bab.id))
        assert r.status_code == 200, (path, r.status_code, r.text[:160])
    # Not locked for a BookaBoost member without a seat.
    assert client.get("/leads/", headers=_h(db, t["bab_staff"], ws=bab.id)).status_code == 200


def test_tenants_never_see_each_others_wholesale_records(client, t):
    db = t["db"]
    evo_h = _h(db, t["evo_ws"], ws=t["evo"].id)
    bab_h = _h(db, t["bab_ws"], ws=t["bab"].id)
    r = client.post("/wholesale/buyers", headers=evo_h,
                    json={"company_name": "EvoSys Only Capital (TEST)", "email": "evo-buyer@example.com", "is_test": True})
    assert r.status_code in (200, 201), r.text[:200]
    r = client.post("/wholesale/buyers", headers=bab_h,
                    json={"company_name": "BookaBoost Only Capital (TEST)", "email": "bab-buyer@example.com", "is_test": True})
    assert r.status_code in (200, 201), r.text[:200]

    def names(h):
        r = client.get("/wholesale/buyers", headers=h, params={"include_test": True})
        assert r.status_code == 200, r.text[:200]
        return {b["display_name"] for b in r.json()["buyers"]}

    assert names(evo_h) == {"EvoSys Only Capital (TEST)"}
    assert names(bab_h) == {"BookaBoost Only Capital (TEST)"}


def test_bookaboost_login_cannot_name_the_evosys_workspace(client, t):
    db = t["db"]
    h = _h(db, t["bab_ws"], ws=t["evo"].id)          # forged workspace header
    r = client.get("/wholesale/buyers", headers=h, params={"include_test": True})
    if r.status_code == 200:                        # header ignored -> must still be BookaBoost's own (empty) list
        assert all("EvoSys" not in b["display_name"] for b in r.json()["buyers"])
    else:
        assert r.status_code in (400, 402, 403, 404), r.status_code
    r = client.get("/wholesale/settings", headers=h)
    if r.status_code == 200:
        assert r.json().get("organization_id") in (None, t["bab"].id)
