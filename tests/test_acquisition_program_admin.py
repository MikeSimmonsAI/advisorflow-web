# -*- coding: utf-8 -*-
"""#452 (owner architecture 2026-11-03): EvoSys Acquisition administration is a
PROGRAM grant, not God. The production shape of platform@evosyspro.live is an
org_admin of EVO's own org holding a named Wholesale seat (so the Wholesale lock
applies). With the Acquisition admin grant it runs the master workspace; it
never gets God Mode, other platforms, or the power to make admins."""
import os

from app.models.models import Platform
from tests.test_acquisition_pilot import _h, pilot, wholesale_login  # noqa: F401


def _platform(db):
    if db.query(Platform).filter(Platform.slug == "evosyspro").first() is None:
        db.add(Platform(name="EvoSys Pro", slug="evosyspro"))
        db.commit()


def _grant(p, u):
    r = p["c"].post("/god/acquisition/admins", json={"email": u.email}, headers=_h(p["db"], p["god"]))
    assert r.status_code == 200 and r.json()["action"] == "granted", r.text


def test_program_admin_runs_the_master_workspace_without_god(pilot, wholesale_login):
    p, u = pilot, wholesale_login
    db, c = p["db"], p["c"]
    _platform(db)
    h = _h(db, u)
    assert c.get("/god/acquisition/hub", headers=h).status_code == 403          # before: nothing
    _grant(p, u)
    for path in ("/god/acquisition/hub", "/god/acquisition/directory", "/god/acquisition/onboarding-options",
                 "/god/acquisition/master/prospects", "/god/acquisition/followups", "/god/acquisition/report",
                 "/intake/platform-pool/batches"):
        assert c.get(path, headers=h).status_code == 200, path
    cl = c.get("/acquisition-ops/clients", headers=h).json()
    assert cl["master"] is True and cl["global_owner"] is False and cl["me"]["access"] == "admin"
    ov = c.get("/acquisition-ops/%s/overview" % p["shine"].id, headers=h).json()
    assert ov["master"] is True and ov["is_god"] is True                         # master shell, not God
    r = c.post("/god/acquisition/clients", json={"name": "Admin Made Co (synthetic)"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "created"
    # Program Access lists the Acquisition Control Center card for the admin
    cards = [x for x in (c.get("/auth/my-contexts", headers=h).json().get("acquisition_ops") or []) if x.get("hub")]
    assert cards and cards[0]["path"] == "/acquisition-ops"


def test_program_admin_is_not_god(pilot, wholesale_login):
    p, u = pilot, wholesale_login
    db, c = p["db"], p["c"]
    _grant(p, u)
    h = _h(db, u)
    db.refresh(u)
    assert u.role != "god_admin"
    # no God Mode, no paid discovery, no making admins, other platforms closed
    for path in ("/god/stats", "/god/orgs", "/god/acquisition/admins"):
        assert c.get(path, headers=h).status_code == 403, path
    assert c.post("/god/acquisition/admins", json={"email": u.email}, headers=h).status_code == 403
    assert c.post("/god/acquisition/%s/discovery/run" % p["shine"].id, json={"segment": "office", "city": "Dallas"},
                  headers=h).status_code == 403
    # still a Wholesale-locked login everywhere else
    assert c.get("/leads", headers=h).status_code in (403, 404)


def test_revoking_the_grant_closes_the_master_workspace(pilot, wholesale_login):
    p, u = pilot, wholesale_login
    db, c = p["db"], p["c"]
    _grant(p, u)
    assert c.get("/god/acquisition/hub", headers=_h(db, u)).status_code == 200
    assert c.delete("/god/acquisition/admins/%s" % u.id, headers=_h(db, p["god"])).status_code == 200
    assert c.get("/god/acquisition/hub", headers=_h(db, u)).status_code == 403


def test_bootstrap_grants_only_named_non_god_logins(pilot, wholesale_login, monkeypatch):
    p, u = pilot, wholesale_login
    from app.services.acquisition import admins as AD
    monkeypatch.setenv(AD.ENV, "%s, nobody@nowhere.example, %s" % (u.email.upper(), p["god"].email))
    out = AD.bootstrap(p["db"])
    assert [r["email"] for r in out] == [u.email] and AD.is_admin_grant(p["db"], u)
    from app.models.models import User
    row = AD._rows(p["db"]).filter_by(user_id=u.id).one()
    assert row.granted_by is None or p["db"].query(User).get(row.granted_by) is not None   # FK-safe
    assert AD.bootstrap(p["db"]) == []                                     # idempotent
    assert not AD.is_admin_grant(p["db"], p["god"])                        # God untouched


def test_three_service_industries_on_one_engine(pilot):
    p = pilot
    db, c, g = p["db"], p["c"], _h(p["db"], p["god"])
    _platform(db)
    opts = c.get("/god/acquisition/onboarding-options", headers=g).json()
    cats = {x["key"]: x for x in opts["categories"]}
    assert {"cleaning", "window_washing", "pest_control"} <= set(cats)
    made = {}
    for cat, svc, city in (("cleaning", "office", "Irving"), ("window_washing", "storefront_windows", "Plano"),
                           ("pest_control", "restaurant_pest", "Frisco")):
        r = c.post("/god/acquisition/clients", json={"name": "Synthetic %s Co" % cat, "service_category": cat,
                                                    "services": [svc], "cities": [city], "states": ["TX"]}, headers=g)
        assert r.status_code == 200 and r.json()["status"] == "created", r.text
        made[cat] = r.json()["organization_id"]
    d = {x["organization_id"]: x for x in c.get("/god/acquisition/directory", headers=g).json()["items"]}
    from app.models.models import Organization
    for cat, oid in made.items():
        assert db.query(Organization).get(oid).industry == cat
        assert d[oid]["paused"] is True and d[oid]["pilot_started_at"] is None
        ov = c.get("/acquisition-ops/%s/overview" % oid, headers=g).json()
        assert ov["service_category"] == cat
    # services and territory stay separate
    pw = c.get("/acquisition-ops/%s/overview" % made["pest_control"], headers=g).json()["profile"]
    ww = c.get("/acquisition-ops/%s/overview" % made["window_washing"], headers=g).json()["profile"]
    assert pw["service_area"] == ["Frisco"] and ww["service_area"] == ["Plano"]
    assert "Restaurant & food service" in pw["services"] and "Restaurant & food service" not in ww["services"]
    # a retry without a category never re-labels an existing company
    c.post("/god/acquisition/clients", json={"name": "Synthetic window_washing Co"}, headers=g)
    assert db.query(Organization).get(made["window_washing"]).industry == "window_washing"
    # unknown category refused; non-service businesses still can't become clients
    assert c.post("/god/acquisition/clients", json={"name": "X", "service_category": "rocketry"}, headers=g).status_code == 422
    assert c.post("/god/acquisition/%s/enable" % p["funeral"].id, headers=g).status_code == 409
