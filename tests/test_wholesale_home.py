# -*- coding: utf-8 -*-
"""#439 role dashboards: GET /wholesale/home is composed from EFFECTIVE access.

Proves, with synthetic data only:
  - every panel exists only when the permission it needs is held, and every
    number/row is narrowed to the person's record visibility;
  - a starter profile changes the ORDER, not the access: an Acquisition
    Wholesaler granted Buyer Intelligence gets the buyer panel and shortcut
    with no code change; a take-away removes them;
  - the restricted VA, the restricted investor profile, the manager, the
    research assistant and the owner each get the right composition;
  - the owner can preview a profile; nobody else can;
  - EvoSys and BookaBoost never see each other's records;
  - a suspended login is refused;
  - no network call (no provider) is made building any dashboard.
"""
import socket
import time
from datetime import datetime, timedelta

import pytest

from app.models.evosense_models import EvoSenseFollowUp
from app.models.models import User
from app.models.wholesale_models import WholesaleApproval
from app.models.wholesale_ops_models import WholesaleCanvassDoor, WholesaleCanvassRoute, WholesaleFieldAppointment, WholesaleSellerCallback
from tests.test_bookaboost_invites import _h, w  # noqa: F401
from tests.test_wholesale_access_builder import _deal, _invite_new, _login_new, _ok, _profile, sent, ws  # noqa: F401


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any outbound connection while building a dashboard fails the test."""
    real = socket.socket.connect

    def guard(self, addr):
        if isinstance(addr, tuple) and addr[0] not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError("dashboard tried to reach %r" % (addr,))
        return real(self, addr)
    monkeypatch.setattr(socket.socket, "connect", guard)


def _person(p, email, template, **kw):
    prof = _profile(p, "%s %s" % (template, email.split("@")[0]), template, **kw)
    h = _login_new(p, _invite_new(p, email, prof["id"]), email)
    return prof, h, p["db"].query(User).filter(User.email == email).one()


def _home(p, h, **params):
    return _ok(p["c"].get("/wholesale/home", headers=h, params=params or None))


def _keys(home):
    return [x["key"] for x in home["panels"]]


def _actions(home):
    return [a["key"] for a in home["actions"]]


def _seed_work(db, org_id, uid, other_id):
    now = datetime.utcnow()
    mine, mine_prop = _deal(db, org_id, assigned_to=uid, addr="10 Mine St")
    theirs, _ = _deal(db, org_id, assigned_to=other_id, addr="20 Theirs St")
    db.add_all([
        WholesaleSellerCallback(organization_id=org_id, due_at=now - timedelta(hours=1), assigned_to_id=uid, notes="mine"),
        WholesaleSellerCallback(organization_id=org_id, due_at=now - timedelta(hours=1), assigned_to_id=other_id, notes="theirs"),
        WholesaleFieldAppointment(organization_id=org_id, starts_at=now + timedelta(days=1), assigned_to_id=uid,
                                  status="scheduled", address="10 Mine St"),
        WholesaleFieldAppointment(organization_id=org_id, starts_at=now + timedelta(days=1), assigned_to_id=other_id,
                                  status="scheduled", address="20 Theirs St"),
        EvoSenseFollowUp(organization_id=org_id, property_id="evo-x", due_at=now - timedelta(hours=2), assigned_to_id=uid,
                         status="due", note="call seller"),
        WholesaleApproval(organization_id=org_id, deal_id=mine.id, kind="offer", status="pending"),
        WholesaleApproval(organization_id=org_id, deal_id=theirs.id, kind="offer", status="pending"),
    ])
    db.commit()
    return mine, theirs


def test_acquisition_wholesaler_sees_only_their_records_and_gains_buyers_by_override(ws):
    p, db = ws, ws["db"]
    prof, h, me = _person(p, "francis.dash@example.com", "acquisition_wholesaler")
    mine, theirs = _seed_work(db, p["evo"].id, me.id, p["evo_seat"].id)
    home = _home(p, h)
    assert home["focus"] == "acquisition" and home["record_scope"] == "assigned"
    panels = {x["key"]: x for x in home["panels"]}
    assert [r["deal_id"] for r in panels["opportunities"]["rows"]] == [mine.id]      # not theirs
    assert panels["opportunities"]["count"] == 1
    assert panels["callbacks"]["due_today"] == 1                                    # only their callback
    assert panels["appointments"]["week"] == 1
    assert "buyers" not in panels and "buyers" not in _actions(home)                # no Buyer Intelligence yet
    assert "team" not in panels and "providers" not in panels and "deal_room" not in panels
    kpi = {k["key"]: k["value"] for k in home["kpis"]}
    assert kpi["open_deals"] == 1                                                   # one calculation, shown twice
    # the owner grants Buyer Intelligence: the panel and shortcut appear, no code change
    _ok(p["c"].put("/wholesale/team/members/%s/access" % me.id, headers=p["owner_h"],
                   json={"profile_id": prof["id"], "grants": {"buyer_intel": ["view"]}}))
    home2 = _home(p, h)
    assert "buyers" in _keys(home2) and "buyers" in _actions(home2)
    assert home2["focus"] == "acquisition"                                          # same layout, more access
    # ...and taking Contracts away removes the shortcut AND the server refuses it
    _ok(p["c"].put("/wholesale/team/members/%s/access" % me.id, headers=p["owner_h"],
                   json={"profile_id": prof["id"], "grants": {"buyer_intel": ["view"]}, "denies": {"contracts": ["view"]}}))
    home3 = _home(p, h)
    assert "closing" not in _actions(home3) and "contracts" not in home3["modules"]
    assert p["c"].get("/wholesale/contract-templates", headers=h).status_code == 403


def test_restricted_va_gets_a_task_queue_and_nothing_administrative(ws):
    p, db = ws, ws["db"]
    _, h, me = _person(p, "keisha.dash@example.com", "virtual_assistant")
    _seed_work(db, p["evo"].id, me.id, p["evo_seat"].id)
    home = _home(p, h)
    assert home["focus"] == "va" and home["record_scope"] == "assigned"
    keys = _keys(home)
    assert keys[:3] == ["tasks", "callbacks", "appointments"]
    for forbidden in ("providers", "team", "deal_room", "buyers", "outreach", "reviews", "pipeline", "opportunities"):
        assert forbidden not in keys, forbidden
    tasks = {x["key"]: x for x in home["panels"]}["tasks"]
    assert {t["kind"] for t in tasks["items"]} == {"follow_up", "callback"}
    assert all("theirs" not in t["title"] for t in tasks["items"])
    acts = _actions(home)
    assert "providers" not in acts and "assign" not in acts and "deals" not in acts
    for path in ("/wholesale/settings", "/wholesale/evosense/controls", "/wholesale/skip-trace/providers",
                 "/wholesale/team/directory", "/god/customers", "/wholesale/buyers/imports/x1234567890abcdef/errors.csv"):
        assert p["c"].get(path, headers=h).status_code in (403, 404), path


def test_restricted_investor_profile_sees_only_assigned_deals_and_no_owner_contacts(ws):
    p, db = ws, ws["db"]
    _, h, me = _person(p, "investor.dash@example.com", "investor_restricted")
    mine, theirs = _seed_work(db, p["evo"].id, me.id, p["evo_seat"].id)
    home = _home(p, h)
    assert home["focus"] == "investor"
    panels = {x["key"]: x for x in home["panels"]}
    rows = panels["opportunities"]["rows"]
    assert [r["deal_id"] for r in rows] == [mine.id]
    assert all(r["owner"] is None for r in rows)                      # no seller/owner data without that access
    for forbidden in ("callbacks", "tasks", "team", "providers", "research", "discovery", "routes"):
        assert forbidden not in panels, forbidden
    assert p["c"].get("/wholesale/deals/%s" % theirs.id, headers=h).status_code == 403


def test_manager_sees_team_reviews_and_activity_owner_sees_everything(ws):
    p, db = ws, ws["db"]
    _, h, me = _person(p, "manager.dash@example.com", "wholesale_manager")
    _seed_work(db, p["evo"].id, me.id, p["evo_seat"].id)
    home = _home(p, h)
    assert home["focus"] == "manager"
    keys = _keys(home)
    assert keys[0] == "team" and "reviews" in keys and "activity" in keys
    names = [r["name"] for r in {x["key"]: x for x in home["panels"]}["team"]["rows"]]
    assert any("Manager" in (n or "") or "manager" in (n or "").lower() for n in names) or names
    owner = _home(p, p["owner_h"])
    assert owner["focus"] == "manager" and owner["record_scope"] == "workspace"
    reviews = {x["key"]: x for x in owner["panels"]}["reviews"]
    assert reviews["count"] == 2 and reviews["can_decide"] is True


def test_research_assistant_gets_research_and_providers_only_when_granted(ws):
    p = ws
    prof, h, me = _person(p, "nina.dash@example.com", "research_assistant")
    home = _home(p, h)
    assert home["focus"] == "research" and "research" in _keys(home)
    assert "providers" not in _keys(home)                              # paid lookups not in the starter
    _ok(p["c"].put("/wholesale/team/members/%s/access" % me.id, headers=p["owner_h"],
                   json={"profile_id": prof["id"], "grants": {"paid_lookups": ["view"]}}))
    prov = {x["key"]: x for x in _home(p, h)["panels"]}["providers"]
    states = {r["key"]: r["state"] for r in prov["rows"]}
    assert states["propstream"].startswith("IMPORT ONLY")             # truthful, never "Live"
    assert all(not s.startswith("LIVE") for s in states.values())


def test_owner_can_preview_a_profile_and_nobody_else_can(ws):
    p = ws
    prof, h, me = _person(p, "preview.dash@example.com", "field_representative")
    pv = _home(p, p["owner_h"], preview_profile_id=prof["id"])
    assert pv["preview"]["profile_id"] == prof["id"] and pv["focus"] == "field"
    assert "routes" in _keys(pv) and "team" not in _keys(pv)
    r = p["c"].get("/wholesale/home", headers=h, params={"preview_profile_id": prof["id"]})
    assert r.status_code == 403
    bab = p["c"].get("/wholesale/home", headers=p["bab_h"], params={"preview_profile_id": prof["id"]})
    assert bab.status_code == 404                                     # another workspace's profile isn't there


def test_field_rep_sees_their_route_and_stops(ws):
    p, db = ws, ws["db"]
    _, h, me = _person(p, "tasha.dash@example.com", "field_representative")
    route = WholesaleCanvassRoute(organization_id=p["evo"].id, name="North loop", assigned_to_id=me.id,
                                  route_date=__import__("app.services.wholesale_appointments", fromlist=["day_window"]).day_window()[0], status="open")
    other = WholesaleCanvassRoute(organization_id=p["evo"].id, name="Someone else", assigned_to_id=p["evo_seat"].id,
                                  route_date=__import__("app.services.wholesale_appointments", fromlist=["day_window"]).day_window()[0], status="open")
    db.add_all([route, other])
    db.flush()
    db.add_all([WholesaleCanvassDoor(organization_id=p["evo"].id, route_id=route.id, address="1 Door St", sort=1),
                WholesaleCanvassDoor(organization_id=p["evo"].id, route_id=route.id, address="2 Door St", sort=2)])
    db.commit()
    home = _home(p, h)
    assert home["focus"] == "field" and _keys(home)[0] == "routes"
    routes = {x["key"]: x for x in home["panels"]}["routes"]
    assert [r["name"] for r in routes["routes"]] == ["North loop"] and routes["doors_left"] == 2
    assert [s["address"] for s in routes["stops"]] == ["1 Door St", "2 Door St"]
    assert "route" in _actions(home)


def test_brands_never_see_each_others_records_and_suspended_logins_are_refused(ws):
    p, db = ws, ws["db"]
    _, h, me = _person(p, "evo.only.dash@example.com", "wholesale_manager")
    _seed_work(db, p["evo"].id, me.id, p["evo_seat"].id)
    bab = _home(p, p["bab_h"])
    opp = {x["key"]: x for x in bab["panels"]}.get("opportunities")
    assert opp is None or opp["count"] == 0
    rev = {x["key"]: x for x in bab["panels"]}.get("reviews")
    assert rev is None or rev["count"] == 0
    _ok(p["c"].post("/wholesale/team/members/%s/suspend" % me.id, headers=p["owner_h"], json={}))
    assert p["c"].get("/wholesale/home", headers=h).status_code in (402, 403)


def test_dashboard_is_quick_and_does_not_queue_work(ws, monkeypatch):
    p, db = ws, ws["db"]
    _, h, me = _person(p, "perf.dash@example.com", "acquisition_wholesaler")
    for i in range(40):
        _deal(db, p["evo"].id, assigned_to=me.id if i % 2 else p["evo_seat"].id, addr="%d Load St" % i)
    from app.services.evosense import hunt as hunter
    monkeypatch.setattr(hunter, "start_background", lambda *a, **k: (_ for _ in ()).throw(AssertionError("job queued")),
                        raising=False)
    t0 = time.perf_counter()
    home = _home(p, h)
    took = time.perf_counter() - t0
    owner_t0 = time.perf_counter()
    _home(p, p["owner_h"])
    owner_took = time.perf_counter() - owner_t0
    assert {x["key"]: x for x in home["panels"]}["opportunities"]["count"] == 20
    assert took < 2.0 and owner_took < 2.0, (took, owner_took)
    print("DASHBOARD_TIMING restricted=%.3fs owner=%.3fs" % (took, owner_took))
