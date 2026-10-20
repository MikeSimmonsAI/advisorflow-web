# -*- coding: utf-8 -*-
"""Issue #261 - wholesale@ gets org_admin on Roofing through the EXISTING
multi-workspace system; GOD MODE keeps authority over every organization.

Mirrors the production shape found on 2026-10-20 (names only, no customer data):
  wholesale@  users.role=org_admin, home org = EVO Integrated Solutions LLC
              customer_org org_admin: EVO Integrated Solutions, Atlantis Light &
              Power, Service Corporation International; product 'wholesale' member
The grant goes through GOD MODE -> Manage Access (access_management.apply),
which writes ONE audited Membership row and touches nothing else.
"""
import itertools
import json
import uuid

import pytest

from app.models.models import Organization, User
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
ROOF_FEATURES = ["leads", "imports", "booking", "calendar", "availability", "reports", "users",
                 "branding_settings", "roofing_program"]


def _org(db, name, industry="generic", features=("leads",)):
    o = Organization(name=name, slug="ra-%s" % uuid.uuid4().hex[:8], plan="enterprise", industry=industry,
                     is_active=True, enabled_features=json.dumps(list(features)))
    db.add(o)
    db.commit()
    return o


def _user(db, org, email, role):
    u = User(organization_id=org.id if org else None, email=email, full_name=email.split("@")[0],
             password_hash=hash_password("Pass12345!"), role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _member(db, u, scope_type, scope_id, role):
    m = Membership(user_id=u.id, scope_type=scope_type, scope_id=scope_id, role=role, is_active=True)
    db.add(m)
    db.commit()
    return m


def _h(db, u, ws=None, override=None):
    h = {"Authorization": "Bearer %s" % create_access_token(u, db)}
    if ws:
        h["X-Workspace-Id"] = ws
    if override:
        h["X-Org-Override"] = override
    return h


def _snapshot(db, u):
    return sorted((m.scope_type, m.scope_id, m.role, bool(m.is_active))
                  for m in db.query(Membership).filter(Membership.user_id == u.id).all())


@pytest.fixture()
def prod_shape(db_session):
    db = db_session
    evo = _org(db, "EVO Integrated Solutions LLC", features=("leads", "wholesale_real_estate"))
    atl = _org(db, "Atlantis Light & Power", industry="energy")
    sci = _org(db, "Service Corporation International", industry="funeral")
    roof = _org(db, "EvoSys Pro Roofing", industry="roofing", features=ROOF_FEATURES)
    other_roof = _org(db, "Another Roofing Co", industry="roofing", features=ROOF_FEATURES)
    god = _user(db, None, "owner-%d@example.com" % next(_SEQ), "god_admin")
    w = _user(db, evo, "wholesale-%d@example.com" % next(_SEQ), "org_admin")
    for o in (evo, atl, sci):
        _member(db, w, "customer_org", o.id, "org_admin")
    _member(db, w, "product", "wholesale", "member")
    outsider = _user(db, other_roof, "outsider-%d@example.com" % next(_SEQ), "org_admin")
    _member(db, outsider, "customer_org", other_roof.id, "org_admin")
    from app.services.roofing import setup as S
    S.setup(db, roof, god.id)
    S.setup(db, other_roof, god.id)
    return dict(db=db, evo=evo, atl=atl, sci=sci, roof=roof, other_roof=other_roof, god=god, w=w, outsider=outsider)


def _grant(db, w, roof, god):
    from app.services import access_management as am
    ops = [{"op": "add_membership", "scope_type": "customer_org", "scope_id": roof.id, "role": "org_admin"}]
    return am.preview(db, w, ops), am.apply(db, w, ops, god)


def test_grant_is_one_audited_membership_and_preserves_everything(prod_shape):
    db, w, roof, god = prod_shape["db"], prod_shape["w"], prod_shape["roof"], prod_shape["god"]
    before = _snapshot(db, w)
    home, role, email, pw = w.organization_id, w.role, w.email, w.password_hash
    prev, out = _grant(db, w, roof, god)
    after = _snapshot(db, w)
    added = sorted(set(after) - set(before))
    assert added == [("customer_org", roof.id, "org_admin", True)]
    assert set(before) <= set(after)                       # zero loss of existing access
    db.refresh(w)
    assert (w.organization_id, w.role, w.email, w.password_hash) == (home, role, email, pw)
    assert db.query(User).filter(User.email == email).count() == 1   # no duplicate account
    # idempotent: a second apply does not add a second row
    from app.services import access_management as am
    am.apply(db, w, [{"op": "add_membership", "scope_type": "customer_org", "scope_id": roof.id,
                      "role": "org_admin"}], god)
    assert _snapshot(db, w) == after
    # audited
    from app.models.models import AuditLogEntry
    rows = db.query(AuditLogEntry).filter(AuditLogEntry.target_id == w.id,
                                          AuditLogEntry.action == "access.manage").all()
    assert rows and all(r.actor_user_id == god.id for r in rows)


def test_wholesale_switches_into_roofing_and_back(client, prod_shape):
    db, w, roof, god = prod_shape["db"], prod_shape["w"], prod_shape["roof"], prod_shape["god"]
    # before the grant: selecting Roofing is thrown away -> lands in home org -> roofing refuses
    assert client.get("/roofing-ops/status", headers=_h(db, w, ws=roof.id)).status_code in (402, 403)
    _grant(db, w, roof, god)
    s = client.get("/roofing-ops/status", headers=_h(db, w, ws=roof.id))
    assert s.status_code == 200, s.text
    assert s.json()["organization_id"] == roof.id and s.json()["can_manage"] is True
    for path in ("/roofing-ops/mission", "/roofing-ops/dashboard", "/roofing-ops/inbox", "/roofing-ops/pipeline",
                 "/roofing-ops/leads", "/roofing-ops/playbooks", "/roofing-ops/qualification", "/roofing-ops/team"):
        r = client.get(path, headers=_h(db, w, ws=roof.id))
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
    # org_admin can configure Roofing
    assert client.put("/roofing-ops/profile", json={"company_name": "EvoSys Pro Roofing"},
                      headers=_h(db, w, ws=roof.id)).status_code == 200
    # ...and is still not the platform owner
    assert client.post("/roofing-ops/text-switch", json={"on": True}, headers=_h(db, w, ws=roof.id)).status_code == 403
    assert client.post("/god/roofing/setup", json={"organization_id": roof.id}, headers=_h(db, w)).status_code == 403
    # the other workspaces still resolve as themselves
    from app.services import workspace_access as wa
    for o in (prod_shape["evo"], prod_shape["atl"], prod_shape["sci"], roof):
        assert wa.has_workspace(w, db, o.id)
    # selecting Atlantis is NOT roofing - the roofing API refuses there
    assert client.get("/roofing-ops/status", headers=_h(db, w, ws=prod_shape["atl"].id)).status_code in (402, 403)


def test_roofing_is_not_opened_for_unrelated_orgs_or_users(client, prod_shape):
    db, w, roof, god, outsider = (prod_shape[k] for k in ("db", "w", "roof", "god", "outsider"))
    _grant(db, w, roof, god)
    # another roofing company's admin cannot select our Roofing workspace: it falls back to their own
    s = client.get("/roofing-ops/status", headers=_h(db, outsider, ws=roof.id)).json()
    assert s["organization_id"] == prod_shape["other_roof"].id
    # wholesale@ cannot reach the other roofing company at all
    s2 = client.get("/roofing-ops/status", headers=_h(db, w, ws=prod_shape["other_roof"].id))
    assert s2.status_code in (402, 403)     # header discarded -> home org EVO -> not roofing
    # the grant did not turn roofing on anywhere else
    for o in (prod_shape["evo"], prod_shape["atl"], prod_shape["sci"]):
        db.refresh(o)
        assert "roofing_program" not in json.loads(o.enabled_features)


def test_god_mode_keeps_authority_over_roofing_and_everything(client, prod_shape):
    db, god, roof = prod_shape["db"], prod_shape["god"], prod_shape["roof"]
    _grant(db, prod_shape["w"], roof, god)
    r = client.get("/roofing-ops/mission", headers=_h(db, god, override=roof.id))
    assert r.status_code == 200, r.text
    st = client.get("/roofing-ops/status", headers=_h(db, god, override=roof.id)).json()
    assert st["organization_id"] == roof.id and st["can_manage"] is True
    # only god can flip the workspace text switch, and god_admin is unchanged
    assert client.get("/roofing-ops/text-switch", headers=_h(db, god, override=roof.id)).json()["can_change"] is True
    god_id = god.id
    assert db.query(User).filter(User.id == god_id).one().role == "god_admin"
    assert db.query(User).filter(User.role == "god_admin").count() == 1
    # god's control plane still reads the grantee's footprint across every org
    from app.services import access_management as am
    fp = am.footprint(db, prod_shape["w"])
    names = json.dumps(fp)
    for o in ("EVO Integrated Solutions LLC", "Atlantis Light & Power", "Service Corporation International",
              "EvoSys Pro Roofing"):
        assert o in names
