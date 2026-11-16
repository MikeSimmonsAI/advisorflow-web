# -*- coding: utf-8 -*-
"""P0 2026-11-15: "Open Client Portal" for an EvoSys Acquisition admin.

Production shape: platform@evosyspro.live is an org_admin of EVO's own org with
a named Wholesale seat and the Acquisition admin grant - NOT God. The button
called the God-only context endpoint; the Wholesale lock refused it first with
"This workspace runs Wholesale. That screen is not part of Wholesale."
"""
from app.models.sales_models import Membership
from tests.test_acquisition_pilot import _h, pilot, wholesale_login  # noqa: F401
from tests.test_acquisition_program_admin import _grant, _platform

WHOLESALE_REFUSAL = "This workspace runs Wholesale. That screen is not part of Wholesale."


def _as(db, u, org=None, ws=None):
    h = _h(db, u, ws)
    if org:
        h["X-Org-Override"] = org
    return h


def _members(db, u):
    return db.query(Membership).filter(Membership.user_id == u.id).count()


def _setup(p, u):
    db, c = p["db"], p["c"]
    _platform(db)
    _grant(p, u)
    # a second service company, and Service Operations on for both
    assert c.post("/god/acquisition/%s/enable" % p["other"].id, headers=_h(db, p["god"])).status_code == 200
    for org in (p["shine"], p["other"]):
        r = c.put("/acquisition-ops/%s/service-ops" % org.id, json={"ops_enabled": True}, headers=_h(db, p["god"]))
        assert r.status_code == 200, r.text
    return db, c, u.organization_id      # the admin's own (Wholesale) workspace = the stale selection


def test_root_cause_god_entry_is_refused_by_the_wholesale_lock(pilot, wholesale_login):
    p, u = pilot, wholesale_login
    db, c, evo = _setup(p, u)
    r = c.post("/god/platform/context/customer/%s" % p["shine"].id, json={}, headers=_as(db, u, ws=evo))
    assert r.status_code == 403 and r.json()["detail"] == WHOLESALE_REFUSAL
    # and the old header alone never put a non-God login in the client
    r = c.get("/acquisition/status", headers=_as(db, u, ws=evo))
    assert r.status_code == 403


def test_admin_opens_the_client_portal_with_the_right_identity(pilot, wholesale_login):
    p, u = pilot, wholesale_login
    db, c, evo = _setup(p, u)
    shine = p["shine"]
    before = _members(db, u)
    r = c.post("/acquisition-ops/%s/client-portal" % shine.id, json={}, headers=_as(db, u, ws=evo))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["context"]["customer"] == {"id": shine.id, "name": shine.name}
    assert body["memberships_before"] == body["memberships_after"] == before
    assert body["landing"] == "/program/acquisition"
    # the portal, as this company - even with the stale Wholesale workspace still sent
    h = _as(db, u, org=shine.id, ws=evo)
    st = c.get("/acquisition/status", headers=h)
    assert st.status_code == 200, st.text
    assert st.json()["organization_id"] == shine.id and st.json()["organization_name"] == shine.name
    me = c.get("/service-ops/me", headers=h).json()
    assert me["ops_enabled"] is True and me["role"] == "company_admin"
    assert c.get("/service-ops/team", headers=h).status_code == 200
    br = c.get("/branding/org", headers=h)
    assert br.status_code == 200
    # the client's narrow portal only: nothing of the platform or Wholesale through it
    assert c.get("/leads", headers=h).status_code in (403, 404)
    # entering granted nothing, and was audited
    assert _members(db, u) == before
    from app.models.models import AuditLogEntry
    assert db.query(AuditLogEntry).filter(AuditLogEntry.organization_id == shine.id,
                                         AuditLogEntry.action == "acquisition_client_portal_enter").count() == 1
    # back to the Acquisition Control Center: the master screens still answer
    assert c.post("/acquisition-ops/client-portal/exit", json={"organization_id": shine.id},
                  headers=_as(db, u, ws=evo)).status_code == 200
    assert c.get("/acquisition-ops/clients", headers=_as(db, u, ws=evo)).status_code == 200
    assert c.get("/god/acquisition/directory", headers=_as(db, u, ws=evo)).status_code == 200
    # and the Wholesale workspace is still Wholesale-locked exactly as before
    assert c.get("/leads", headers=_as(db, u, ws=evo)).status_code in (403, 404)


def test_switching_between_two_companies_keeps_them_apart(pilot, wholesale_login):
    p, u = pilot, wholesale_login
    db, c, evo = _setup(p, u)
    shine, other = p["shine"], p["other"]
    hs, ho = _as(db, u, org=shine.id, ws=evo), _as(db, u, org=other.id, ws=evo)
    r = c.post("/service-ops/customers", json={"name": "Shine Only Customer"}, headers=hs)
    assert r.status_code == 200, r.text
    assert c.post("/acquisition-ops/%s/client-portal" % other.id, json={}, headers=_as(db, u, ws=evo)).status_code == 200
    assert c.get("/acquisition/status", headers=ho).json()["organization_id"] == other.id
    names_other = [x["name"] for x in c.get("/service-ops/customers", headers=ho).json()["items"]]
    names_shine = [x["name"] for x in c.get("/service-ops/customers", headers=hs).json()["items"]]
    assert "Shine Only Customer" in names_shine and "Shine Only Customer" not in names_other
    assert c.get("/service-ops/customers/%s" % r.json()["id"], headers=ho).status_code in (403, 404)


def test_unauthorized_logins_stay_blocked(pilot, wholesale_login):
    p, u = pilot, wholesale_login
    db, c = p["db"], p["c"]
    _platform(db)
    shine = p["shine"]
    # no Acquisition admin grant: neither the entry nor the header opens anything
    assert c.post("/acquisition-ops/%s/client-portal" % shine.id, json={}, headers=_h(db, u)).status_code == 403
    assert c.get("/acquisition/status", headers=_as(db, u, org=shine.id)).status_code == 403
    # another company's own admin can't use it to reach Shine
    out = p["outsider"]
    assert c.post("/acquisition-ops/%s/client-portal" % shine.id, json={}, headers=_h(db, out)).status_code == 403
    st = c.get("/acquisition/status", headers=_as(db, out, org=shine.id))
    assert st.status_code != 200 or st.json().get("organization_id") != shine.id
    # with the grant, the header still never opens a non-acquisition company or a non-portal API
    _grant(p, u)
    assert c.post("/acquisition-ops/%s/client-portal" % p["funeral"].id, json={},
                  headers=_h(db, u)).status_code == 409
    assert c.get("/acquisition/status", headers=_as(db, u, org=p["funeral"].id)).status_code == 403
    assert c.get("/leads", headers=_as(db, u, org=shine.id)).status_code in (403, 404)
    # and it is still not God
    assert c.get("/god/orgs", headers=_as(db, u, org=shine.id)).status_code == 403
