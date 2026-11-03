# -*- coding: utf-8 -*-
"""#452 P0 owner-access regression (2026-11-03).

Production evidence: the owner's god session never reached /acquisition-ops;
the Operator view came from a DIFFERENT login (an org_admin holding a legacy
operator grant on one client). These tests pin the server contract the shell
relies on: a god_admin is always the owner of the Acquisition Control Center,
whatever stale headers it carries; an operator is always restricted; a client
login is confined to its portal; 403 never masquerades as 401."""
import json

from tests.test_acquisition_pilot import _h, _org, _user, pilot  # noqa: F401


def test_god_is_owner_on_every_entry_path(pilot):
    p = pilot
    db, c, god = p["db"], p["c"], p["god"]
    # A/C. direct open and refresh: same answer every time, with the server's identity
    for _ in range(2):
        r = c.get("/acquisition-ops/clients", headers=_h(db, god))
        assert r.status_code == 200 and r.json()["is_god"] is True
        assert r.json()["me"]["access"] == "owner" and r.json()["me"]["email"] == god.email
    # B. Program Access lists the Acquisition Control Center card for the owner
    ctx = c.get("/auth/my-contexts", headers=_h(db, god)).json()
    cards = [x for x in (ctx.get("acquisition_ops") or []) if x.get("hub")]
    assert cards and cards[0]["path"] == "/acquisition-ops"
    # 5. stale context carried from a previous tenant never demotes the owner
    stale = dict(_h(db, god), **{"X-Org-Override": p["shine"].id, "X-Workspace-Id": p["shine"].id})
    r = c.get("/acquisition-ops/clients", headers=stale)
    assert r.status_code == 200 and r.json()["is_god"] is True
    assert c.get("/acquisition-ops/%s/overview" % p["shine"].id, headers=stale).json()["is_god"] is True


def test_god_reaches_directory_and_add_company(pilot):
    p = pilot
    db, c, god = p["db"], p["c"], p["god"]
    from app.models.models import Platform
    if db.query(Platform).filter(Platform.slug == "evosyspro").first() is None:   # exists in production
        db.add(Platform(name="EvoSys Pro", slug="evosyspro"))
        db.commit()
    g = _h(db, god)
    # D. directory, E. the Add Cleaning Company wizard's data + create
    assert c.get("/god/acquisition/directory", headers=g).status_code == 200
    opts = c.get("/god/acquisition/onboarding-options", headers=g).json()
    assert opts["services"] and opts["offers"]
    r = c.post("/god/acquisition/clients", json={"name": "A+ Cleaning (synthetic test)"}, headers=g)
    assert r.status_code == 200 and r.json()["status"] == "created"
    again = c.post("/god/acquisition/clients", json={"name": "A+ Cleaning (synthetic test)"}, headers=g).json()
    assert again["status"] == "existing" and again["organization_id"] == r.json()["organization_id"]
    d = [x for x in c.get("/god/acquisition/directory", headers=g).json()["items"] if x["organization_id"] == r.json()["organization_id"]][0]
    assert d["paused"] is True and d["pilot_started_at"] is None and d["invitation"]["status"] == "not_sent"
    # F. entering that client keeps the owner the owner
    ov = c.get("/acquisition-ops/%s/overview" % r.json()["organization_id"], headers=g).json()
    assert ov["is_god"] is True


def test_operator_stays_restricted_and_is_named(pilot):
    p = pilot
    db, c = p["db"], p["c"]
    va_home = _org(db, "Operator Home Org", industry="other")
    va = _user(db, va_home, "org_admin", "org_admin")
    assert c.post("/god/acquisition/%s/operators" % p["shine"].id, json={"email": va.email, "permissions": ["research"]},
                  headers=_h(db, p["god"])).status_code == 200
    h = _h(db, va)
    r = c.get("/acquisition-ops/clients", headers=h).json()
    # G. restricted, and the server says exactly who this is
    assert r["is_god"] is False and r["me"]["access"] == "operator" and r["me"]["email"] == va.email
    assert [x["organization_id"] for x in r["items"]] == [p["shine"].id]
    for path in ("/god/acquisition/hub", "/god/acquisition/directory", "/god/acquisition/onboarding-options"):
        # J. an authorization refusal is 403, never 401 (the shell signs out only on 401)
        assert c.get(path, headers=h).status_code == 403
    assert c.post("/god/acquisition/clients", json={"name": "X"}, headers=h).status_code == 403


def test_client_login_confined_and_bad_tokens_are_401(pilot):
    p = pilot
    db, c = p["db"], p["c"]
    h = _h(db, p["owner"], p["shine"].id)           # the cleaning company's own login
    # H. its portal works; the operations and master APIs do not
    assert c.get("/acquisition/status", headers=h).status_code == 200
    for path in ("/acquisition-ops/clients", "/god/acquisition/directory"):
        assert c.get(path, headers=h).status_code == 403
    assert c.get("/leads", headers=h).status_code in (403, 404)
    # I. a missing or invalid credential is a genuine 401
    assert c.get("/acquisition-ops/clients").status_code == 401
    assert c.get("/acquisition-ops/clients", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_switching_clients_never_mixes_tenants(pilot, db_session):
    p = pilot
    from app.services.acquisition import engine as E
    db, c, g = p["db"], p["c"], _h(p["db"], p["god"])
    p["other"].enabled_features = json.dumps(list(E.CLIENT_FEATURES))
    db_session.commit()
    c.post("/acquisition-ops/%s/prospects" % p["shine"].id, json={"business_name": "Shine Only Office"}, headers=g)
    # K. each client's operations answer for that client only
    a = c.get("/acquisition-ops/%s/prospects" % p["shine"].id, headers=g).json()["items"]
    b = c.get("/acquisition-ops/%s/prospects" % p["other"].id, headers=g).json()["items"]
    assert any(x["business_name"] == "Shine Only Office" for x in a)
    assert not any(x["business_name"] == "Shine Only Office" for x in b)
    assert c.get("/acquisition-ops/%s/overview" % p["other"].id, headers=g).json()["organization_id"] == p["other"].id
