"""Regression tests for four audited tenant-scope defects.

1. tier_definitions_router: a super_admin (brand-scoped operator) could pass
   ?org_id / body.org_id for ANY organization - read, create, seed, reset
   (destructive) - and update/delete skipped the org check for super_admin.
2. evosense_router `_admin`: checked the account-global `users.role` while the
   org acted on is the SELECTED workspace, so org_admin-of-A / advisor-of-B
   could run admin actions in B.
3. setup_router: a super_admin could mint a setup link for any user on any
   brand; the org_admin comparison matched NULL == NULL.
4. audit_log / cadence_template / settings (appointment types, products):
   authorized against the selected workspace but read/wrote the HOME org.

What every test asserts: authority is (this person, THIS workspace / THIS
brand), and out-of-scope records answer 404, never a quiet success.
"""
import itertools
import json
import uuid

import pytest

from app.models.models import (AuditLogEntry, CadenceTemplate, Organization, Platform,
                               TierDefinition, User)
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_access import SCOPE_CUSTOMER_ORG, WORKSPACE_HEADER

_SEQ = itertools.count(1)


# ── builders ────────────────────────────────────────────────────────────────

def _platform(db, name):
    p = Platform(name=name, slug="brand-%s" % uuid.uuid4().hex[:8], short_name=name[:2],
                 tagline="t", support_email="support@%s.test" % uuid.uuid4().hex[:6])
    db.add(p)
    db.commit()
    return p


def _org(db, name, platform=None, industry="energy"):
    o = Organization(name=name, slug="o-%s" % uuid.uuid4().hex[:8], plan="standard",
                     industry=industry, is_active=True,
                     platform_id=(platform.id if platform else None))
    db.add(o)
    db.commit()
    return o


def _user(db, role, org=None, platform=None, label="u"):
    u = User(organization_id=(org.id if org else None),
             platform_id=(platform.id if platform else None),
             email="%s-%d-%s@test.local" % (label, next(_SEQ), uuid.uuid4().hex[:6]),
             password_hash=hash_password("TestPass123!"), full_name=label.title(),
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _member(db, user, org, role):
    m = Membership(user_id=user.id, scope_type=SCOPE_CUSTOMER_ORG,
                   scope_id=org.id, role=role, is_active=True)
    db.add(m)
    db.commit()
    return m


def _h(db, user, workspace=None):
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if workspace is not None:
        h[WORKSPACE_HEADER] = workspace.id
    return h


def _tiers(db, org_id):
    return (db.query(TierDefinition)
            .filter(TierDefinition.organization_id == org_id).all())


# ════════════════════════════════════════════════════════════════════════════
# 1. TIER DEFINITIONS - brand boundary for super_admin
# ════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def brands(db_session):
    from app.services.tier_config_service import seed_default_tier_definitions
    brand_a = _platform(db_session, "Brand A")
    brand_b = _platform(db_session, "Brand B")
    org_a = _org(db_session, "Customer In A", brand_a)
    org_b = _org(db_session, "Customer In B", brand_b)
    seed_default_tier_definitions(db_session, org_a.id, industry="energy")
    db_session.commit()
    super_b = _user(db_session, "super_admin", org=org_b, platform=brand_b, label="superb")
    super_a = _user(db_session, "super_admin", org=org_a, platform=brand_a, label="supera")
    god = _user(db_session, "god_admin", label="god")
    return {"brand_a": brand_a, "brand_b": brand_b, "org_a": org_a, "org_b": org_b,
            "super_b": super_b, "super_a": super_a, "god": god}


def test_foreign_super_admin_cannot_read_org_a_tiers(client, db_session, brands):
    r = client.get("/tier-definitions", params={"org_id": brands["org_a"].id},
                   headers=_h(db_session, brands["super_b"]))
    assert r.status_code == 404, r.text


def test_foreign_super_admin_cannot_create_tier_in_org_a(client, db_session, brands):
    before = len(_tiers(db_session, brands["org_a"].id))
    r = client.post("/tier-definitions", headers=_h(db_session, brands["super_b"]),
                    json={"tier_key": "planted", "tier_label": "Planted", "track_key": "t",
                          "track_label": "T", "org_id": brands["org_a"].id})
    assert r.status_code == 404, r.text
    db_session.expire_all()
    assert len(_tiers(db_session, brands["org_a"].id)) == before


def test_foreign_super_admin_cannot_update_or_delete_org_a_tier(client, db_session, brands):
    tier = _tiers(db_session, brands["org_a"].id)[0]
    label = tier.tier_label
    h = _h(db_session, brands["super_b"])
    r = client.put("/tier-definitions/%s" % tier.id, headers=h, json={"tier_label": "Hijacked"})
    assert r.status_code == 404, r.text
    r = client.delete("/tier-definitions/%s" % tier.id, headers=h)
    assert r.status_code == 404, r.text
    db_session.expire_all()
    still = db_session.query(TierDefinition).filter(TierDefinition.id == tier.id).first()
    assert still is not None and still.tier_label == label


def test_foreign_super_admin_cannot_seed_or_reset_org_a(client, db_session, brands):
    ids_before = {t.id for t in _tiers(db_session, brands["org_a"].id)}
    h = _h(db_session, brands["super_b"])
    r = client.post("/tier-definitions/seed-defaults", headers=h,
                    params={"org_id": brands["org_a"].id})
    assert r.status_code == 404, r.text
    r = client.post("/tier-definitions/reset-defaults", headers=h,
                    params={"org_id": brands["org_a"].id, "industry": "funeral"})
    assert r.status_code == 404, r.text
    db_session.expire_all()
    assert {t.id for t in _tiers(db_session, brands["org_a"].id)} == ids_before, \
        "the destructive reset must not have run"


def test_own_brand_super_admin_still_reaches_org_a(client, db_session, brands):
    r = client.get("/tier-definitions", params={"org_id": brands["org_a"].id},
                   headers=_h(db_session, brands["super_a"]))
    assert r.status_code == 200, r.text
    assert r.json() and all(t["organization_id"] == brands["org_a"].id for t in r.json())


def test_god_keeps_full_tier_access_everywhere(client, db_session, brands):
    org_a = brands["org_a"]
    h = _h(db_session, brands["god"])
    r = client.get("/tier-definitions", params={"org_id": org_a.id}, headers=h)
    assert r.status_code == 200, r.text
    assert len(r.json()) == len(_tiers(db_session, org_a.id))

    r = client.post("/tier-definitions", headers=h,
                    json={"tier_key": "god_made", "tier_label": "God Made", "track_key": "t",
                          "track_label": "T", "org_id": org_a.id})
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    assert r.json()["organization_id"] == org_a.id

    r = client.put("/tier-definitions/%s" % new_id, headers=h, json={"tier_label": "Renamed"})
    assert r.status_code == 200, r.text
    r = client.delete("/tier-definitions/%s" % new_id, headers=h)
    assert r.status_code == 200, r.text

    r = client.post("/tier-definitions/seed-defaults", headers=h, params={"org_id": org_a.id})
    assert r.status_code == 200, r.text
    r = client.post("/tier-definitions/reset-defaults", headers=h,
                    params={"org_id": org_a.id, "industry": "energy"})
    assert r.status_code == 200, r.text


# ════════════════════════════════════════════════════════════════════════════
# 2. EVOSENSE - admin check follows the selected workspace
# ════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def split_person(db_session):
    """org_admin of A (home), ordinary advisor of B - same platform."""
    plat = _platform(db_session, "Shared Brand")
    a = _org(db_session, "Workspace A", plat, industry="real_estate")
    b = _org(db_session, "Workspace B", plat, industry="real_estate")
    person = _user(db_session, "org_admin", org=a, label="split")
    _member(db_session, person, a, "org_admin")
    _member(db_session, person, b, "advisor")
    return {"a": a, "b": b, "person": person, "platform": plat}


def test_evosense_admin_action_refused_in_workspace_where_only_advisor(client, db_session,
                                                                        split_person):
    p, a, b = split_person["person"], split_person["a"], split_person["b"]
    body = {"org_daily_budget_cents": 12345}
    in_b = client.patch("/wholesale/evosense/controls", headers=_h(db_session, p, b), json=body)
    assert in_b.status_code == 403, in_b.text
    in_a = client.patch("/wholesale/evosense/controls", headers=_h(db_session, p, a), json=body)
    assert in_a.status_code == 200, in_a.text


def test_evosense_admin_helper_is_workspace_aware(db_session, split_person, monkeypatch):
    """Helper-level: `_admin` answers from the membership in the selected
    workspace, via the same ambient request svc.write_org_id reads."""
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.routers import evosense_router as ER
    from app.services import request_context

    p, a, b = split_person["person"], split_person["a"], split_person["b"]

    def _req(org):
        scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"",
                 "headers": [(WORKSPACE_HEADER.lower().encode(), org.id.encode())]}
        return Request(scope)

    tok = request_context.set_current_request(_req(b))
    try:
        with pytest.raises(HTTPException) as e:
            ER._admin(p, db_session)
        assert e.value.status_code == 403
    finally:
        request_context.reset_current_request(tok)

    tok = request_context.set_current_request(_req(a))
    try:
        ER._admin(p, db_session)  # does not raise
    finally:
        request_context.reset_current_request(tok)


# ════════════════════════════════════════════════════════════════════════════
# 3. SETUP LINKS - scoped to the caller's brand / workspace
# ════════════════════════════════════════════════════════════════════════════

def test_foreign_super_admin_cannot_mint_setup_link_for_other_brand_user(client, db_session,
                                                                         brands):
    victim = _user(db_session, "advisor", org=brands["org_a"], label="victim")
    r = client.post("/admin/setup-link/%s" % victim.id, headers=_h(db_session, brands["super_b"]))
    assert r.status_code == 404, r.text
    # Own-brand operator and god still can.
    r = client.post("/admin/setup-link/%s" % victim.id, headers=_h(db_session, brands["super_a"]))
    assert r.status_code == 200, r.text
    r = client.post("/admin/setup-link/%s" % victim.id, headers=_h(db_session, brands["god"]))
    assert r.status_code == 200, r.text


def test_super_admin_cannot_mint_setup_link_for_brand_sales_identity(client, db_session, brands):
    """organization_id NULL targets belong to the God control plane."""
    orphan = _user(db_session, "advisor", org=None, platform=brands["brand_b"], label="orphan")
    r = client.post("/admin/setup-link/%s" % orphan.id, headers=_h(db_session, brands["super_b"]))
    assert r.status_code == 404, r.text


def test_org_admin_with_null_org_cannot_mint_link_for_null_org_user(client, db_session):
    """The NULL == NULL hole: org_admin without an org matched every
    org-less user."""
    admin = _user(db_session, "org_admin", org=None, label="nullorgadmin")
    target = _user(db_session, "advisor", org=None, label="nullorgtarget")
    r = client.post("/admin/setup-link/%s" % target.id, headers=_h(db_session, admin))
    assert r.status_code in (403, 404), r.text
    assert "link" not in r.json()


def test_org_admin_setup_link_for_another_org_user_is_404(client, db_session, split_person):
    p = split_person["person"]
    other = _org(db_session, "Elsewhere")
    outsider = _user(db_session, "advisor", org=other, label="outsider")
    r = client.post("/admin/setup-link/%s" % outsider.id, headers=_h(db_session, p))
    assert r.status_code == 404, r.text
    colleague = _user(db_session, "advisor", org=split_person["a"], label="colleague")
    r = client.post("/admin/setup-link/%s" % colleague.id, headers=_h(db_session, p))
    assert r.status_code == 200, r.text


# ════════════════════════════════════════════════════════════════════════════
# 4. AUDIT LOG / CADENCE / SETTINGS - data follows the selected workspace
# ════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def double_admin(db_session):
    """org_admin of A (home) AND of B."""
    plat = _platform(db_session, "Double Brand")
    a = _org(db_session, "Home A", plat)
    b = _org(db_session, "Selected B", plat)
    person = _user(db_session, "org_admin", org=a, label="double")
    _member(db_session, person, a, "org_admin")
    _member(db_session, person, b, "org_admin")
    return {"a": a, "b": b, "person": person}


def test_audit_log_returns_the_selected_workspace_entries(client, db_session, double_admin):
    from app.routers.audit_log_router import log_action
    p, a, b = double_admin["person"], double_admin["a"], double_admin["b"]
    log_action(db_session, a.id, p.id, action="home.only", target_type="x", target_id="1")
    log_action(db_session, b.id, p.id, action="selected.only", target_type="x", target_id="2")

    r = client.get("/audit-log", headers=_h(db_session, p, b))
    assert r.status_code == 200, r.text
    actions = {e["action"] for e in r.json()["entries"]}
    assert "selected.only" in actions
    assert "home.only" not in actions
    assert {e["organization_id"] for e in r.json()["entries"]} == {b.id}

    r = client.get("/audit-log/actions", headers=_h(db_session, p, b))
    assert r.status_code == 200, r.text
    assert "selected.only" in r.json()["actions"]
    assert "home.only" not in r.json()["actions"]

    # No header: home org, exactly as before.
    r = client.get("/audit-log", headers=_h(db_session, p))
    assert r.status_code == 200, r.text
    assert {e["organization_id"] for e in r.json()["entries"]} == {a.id}


def test_audit_log_refused_in_workspace_where_only_advisor(client, db_session, split_person):
    p, b = split_person["person"], split_person["b"]
    r = client.get("/audit-log", headers=_h(db_session, p, b))
    assert r.status_code in (402, 403), r.text


def test_cadence_template_is_written_to_the_selected_workspace(client, db_session, double_admin):
    p, a, b = double_admin["person"], double_admin["a"], double_admin["b"]
    r = client.post("/cadence-templates/", headers=_h(db_session, p, b),
                    json={"name": "Selected Workspace Cadence", "touches": []})
    assert r.status_code in (200, 201), r.text
    t = db_session.query(CadenceTemplate).filter(CadenceTemplate.id == r.json()["id"]).first()
    assert t.organization_id == b.id

    # Visible from B, not found from home A.
    assert client.get("/cadence-templates/%s" % t.id,
                      headers=_h(db_session, p, b)).status_code == 200
    assert client.get("/cadence-templates/%s" % t.id,
                      headers=_h(db_session, p, a)).status_code == 404


def test_settings_products_follow_the_selected_workspace(client, db_session, double_admin):
    p, a, b = double_admin["person"], double_admin["a"], double_admin["b"]
    body = {"products": [{"key": "only_b", "label": "Only B"}]}
    r = client.put("/settings/products", headers=_h(db_session, p, b), json=body)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert "only_b" in (db_session.get(Organization, b.id).products or "")
    assert "only_b" not in (db_session.get(Organization, a.id).products or "")


def test_settings_admin_writes_refused_where_only_advisor(client, db_session, split_person):
    p, a, b = split_person["person"], split_person["a"], split_person["b"]
    h_b = _h(db_session, p, b)
    assert client.put("/settings/products", headers=h_b,
                      json={"products": [{"key": "x", "label": "X"}]}).status_code == 403
    assert client.delete("/settings/products", headers=h_b).status_code == 403
    assert client.put("/settings/appointment-types", headers=h_b,
                      json={"appointment_types": ["X"]}).status_code == 403
    assert client.delete("/settings/appointment-types", headers=h_b).status_code == 403
    # Admin in A: allowed.
    assert client.put("/settings/appointment-types", headers=_h(db_session, p, a),
                      json={"appointment_types": ["Only A"]}).status_code == 200


def test_foreign_super_admin_cannot_edit_or_read_another_brands_user_profile(client, db_session,
                                                                             brands):
    """Editing a victim's email is an account takeover via the reset link."""
    victim = _user(db_session, "advisor", org=brands["org_a"], label="profvictim")
    before = victim.email
    r = client.patch("/settings/admin/profile/%s" % victim.id, headers=_h(db_session, brands["super_b"]),
                     json={"email": "attacker@evil.test"})
    assert r.status_code == 404, r.text
    assert client.get("/settings/admin/profile/%s" % victim.id,
                      headers=_h(db_session, brands["super_b"])).status_code == 404
    db_session.refresh(victim)
    assert victim.email == before
    r = client.patch("/settings/admin/profile/%s" % victim.id, headers=_h(db_session, brands["super_a"]),
                     json={"full_name": "Renamed By Own Brand"})
    assert r.status_code == 200, r.text


def test_org_admin_cannot_edit_an_elevated_account_in_its_org(client, db_session, brands):
    admin = _user(db_session, "org_admin", org=brands["org_a"], label="oadm")
    r = client.patch("/settings/admin/profile/%s" % brands["super_a"].id, headers=_h(db_session, admin),
                     json={"email": "x@evil.test"})
    assert r.status_code == 404, r.text
