"""Hierarchical entitlement resolver: zero-override equivalence, precedence,
dependencies, narrowing scopes, explain, history and God-only permissions."""
import json
import uuid

import pytest

from app.models.location_models import Location
from app.models.models import Organization, Platform, User
from app.services import entitlement_resolver as er
from app.services import entitlements as ent
from app.services.auth_service import create_access_token, hash_password


def _ensure_router():
    from app.main import app
    from app.routers.entitlements_router import router
    if not any(getattr(r, "path", "").startswith("/god/entitlements") for r in app.routes):
        app.include_router(router)


def _old_org_has_feature(org, key):
    """The pre-hierarchy implementation, verbatim in behaviour."""
    allowed = ent.legacy_enabled_for(org)
    if allowed is None:
        return True
    return key in allowed


def _mk_org(db, features, plan="standard", platform=None, raw=None):
    o = Organization(name="Org %s" % uuid.uuid4().hex[:5], slug="s-%s" % uuid.uuid4().hex[:8],
                     plan=plan, platform_id=platform.id if platform else None)
    if raw is not None:
        o.enabled_features = raw
    elif features is not None:
        o.enabled_features = json.dumps(features)
    db.add(o); db.commit()
    return o


def _user(db, org, role="org_admin"):
    u = User(organization_id=org.id if org else None,
             email="u-%s@t.test" % uuid.uuid4().hex[:6], password_hash=hash_password("x"),
             full_name="U", role=role, must_change_password=False)
    db.add(u); db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


@pytest.fixture()
def brand(db_session):
    p = Platform(name="BookaBoost", slug="bb-%s" % uuid.uuid4().hex[:6])
    db_session.add(p); db_session.commit()
    return p


@pytest.fixture()
def god(db_session):
    return _user(db_session, None, role="god_admin")


# ── 1. No override rows => byte-for-byte the old answers ────────────────────

def test_zero_overrides_is_identical_to_legacy_for_every_feature(db_session, client, brand):
    orgs = [
        _mk_org(db_session, None),                                   # NULL = everything
        _mk_org(db_session, []),                                     # explicit none
        _mk_org(db_session, ["leads", "campaigns", "wholesale_real_estate"]),
        _mk_org(db_session, ent.plan_features("growth"), plan="growth"),
        _mk_org(db_session, ["wholesale_real_estate"]),              # legacy dependency gap
        _mk_org(db_session, None, raw="{not json"),                  # corrupt
        _mk_org(db_session, None, raw=""),                           # empty string
        _mk_org(db_session, None, platform=brand),                   # branded, legacy-all
        _mk_org(db_session, ["leads"], platform=brand),
    ]
    for o in orgs:
        legacy = ent.legacy_enabled_for(o)
        got = ent.enabled_for(o)
        assert got == legacy and type(got) is type(legacy), o.enabled_features
        assert json.dumps(ent.feature_report(o), sort_keys=True, default=str) == \
            json.dumps(_legacy_report(o), sort_keys=True, default=str)
        for k in ent.ALL_FEATURE_KEYS:
            assert ent.org_has_feature(o, k) == _old_org_has_feature(o, k), (k, o.enabled_features)
    # And the nav payload: /branding/org returns what it computed before.
    for o in orgs:
        u = _user(db_session, o)
        body = client.get("/branding/org", headers=_h(db_session, u)).json()
        assert body["enabled_features"] == _legacy_branding(o)


def _legacy_report(o):
    allowed = ent.legacy_enabled_for(o)
    preset = ent.plan_features(o.plan)
    return {
        "mode": "all" if allowed is None else "allow_list",
        "enabled": list(ent.ALL_FEATURE_KEYS) if allowed is None else allowed,
        "available": [{"key": k, "label": ent.FEATURES[k],
                       "enabled": True if allowed is None else (k in allowed),
                       "requires": list(ent.REQUIRES.get(k, ()))} for k in ent.ALL_FEATURE_KEYS],
        "enabled_count": len(ent.ALL_FEATURE_KEYS) if allowed is None else len(allowed),
        "plan": o.plan, "plan_preset": preset,
        "below_plan": ([] if allowed is None or preset is None
                       else sorted(k for k in preset if k not in allowed)),
        "dependency_gaps": ent.dependency_gaps(allowed),
    }


def _legacy_branding(o):
    raw = o.enabled_features
    if raw:
        try:
            loaded = json.loads(raw)
            return [f for f in loaded if isinstance(f, str)] if isinstance(loaded, list) else []
        except (ValueError, TypeError):
            return None
    if raw is not None:
        return []
    return None


def test_role_or_user_rows_alone_do_not_change_org_level_answers(db_session, god):
    o = _mk_org(db_session, ["leads", "campaigns"])
    er.set_override(db_session, scope="role", scope_id="advisor", org_id=o.id,
                    feature_key="campaigns", state="disabled", actor_user_id=god.id)
    db_session.commit()
    assert ent.enabled_for(o) == ["leads", "campaigns"]
    assert ent.org_has_feature(o, "campaigns") is True


# ── 2. Precedence ───────────────────────────────────────────────────────────

def test_precedence_platform_brand_org(db_session, brand, god):
    o = _mk_org(db_session, None, platform=brand)
    k = "campaigns"
    er.set_override(db_session, scope="platform", scope_id=None, feature_key=k,
                    state="disabled", actor_user_id=god.id)
    assert ent.org_has_feature(o, k) is False
    er.set_override(db_session, scope="brand", scope_id=brand.id, feature_key=k,
                    state="enabled", actor_user_id=god.id)
    assert ent.org_has_feature(o, k) is True                    # brand beats platform
    er.set_override(db_session, scope="org", scope_id=o.id, feature_key=k,
                    state="disabled", actor_user_id=god.id)
    assert ent.org_has_feature(o, k) is False                   # org beats brand
    # Allow-list still matters when the org has no override of its own.
    o2 = _mk_org(db_session, ["leads"], platform=brand)
    assert ent.org_has_feature(o2, k) is False
    assert er.evaluate(db_session, k, org=o2)["inherited_from"] == "Organization plan / allow-list"


def test_narrow_scopes_cannot_widen(db_session, god):
    o = _mk_org(db_session, ["leads"])
    er.set_override(db_session, scope="role", scope_id="advisor", org_id=o.id,
                    feature_key="campaigns", state="enabled", actor_user_id=god.id)
    res = er.evaluate(db_session, "campaigns", org=o, level="role", role="advisor")
    assert res["enabled"] is False
    assert res["layers"][-1]["state"] == "capped_by_organization"


def test_role_and_user_overrides_are_enforced_by_require_feature(client, db_session, god):
    _ensure_router()
    o = _mk_org(db_session, ["leads", "wholesale_real_estate"])
    admin = _user(db_session, o, "org_admin")
    adv = _user(db_session, o, "advisor")
    assert client.get("/wholesale/settings", headers=_h(db_session, adv)).status_code == 200
    r = client.post("/god/entitlements/overrides", headers=_h(db_session, god),
                    json={"scope": "role", "org_id": o.id, "role": "advisor",
                          "feature_key": "wholesale_real_estate", "state": "disabled"})
    assert r.status_code == 200, r.text
    assert client.get("/wholesale/settings", headers=_h(db_session, adv)).status_code == 403
    assert client.get("/wholesale/settings", headers=_h(db_session, admin)).status_code == 200
    nav = client.get("/branding/org", headers=_h(db_session, adv)).json()["enabled_features"]
    assert "wholesale_real_estate" not in nav and "leads" in nav
    r = client.post("/god/entitlements/overrides", headers=_h(db_session, god),
                    json={"scope": "user", "org_id": o.id, "user_id": admin.id,
                          "feature_key": "wholesale_real_estate", "state": "disabled"})
    assert r.status_code == 200, r.text
    assert client.get("/wholesale/settings", headers=_h(db_session, admin)).status_code == 403


# ── 3. Dependencies ─────────────────────────────────────────────────────────

def test_dependency_disabled_by_override_blocks_dependents(db_session, brand, god):
    o = _mk_org(db_session, None, platform=brand)
    er.set_override(db_session, scope="brand", scope_id=brand.id, feature_key="leads",
                    state="disabled", actor_user_id=god.id)
    res = er.evaluate(db_session, "wholesale_real_estate", org=o)
    assert res["state"] == "blocked_by_dependency" and res["enabled"] is False
    assert res["dependencies"][0]["key"] == "leads"
    assert res["dependencies"][0]["blocks_access"] is True
    assert ent.org_has_feature(o, "wholesale_real_estate") is False
    assert ent.org_has_feature(o, "crm") is True                 # unrelated feature


def test_legacy_allow_list_gap_is_reported_not_enforced(db_session, god):
    o = _mk_org(db_session, ["wholesale_real_estate"])
    # An unrelated override forces the resolver path for this org.
    er.set_override(db_session, scope="org", scope_id=o.id, feature_key="reports",
                    state="enabled", actor_user_id=god.id)
    res = er.evaluate(db_session, "wholesale_real_estate", org=o)
    assert res["enabled"] is True
    assert res["dependencies_met"] is False
    assert res["dependencies"][0]["blocks_access"] is False
    assert ent.org_has_feature(o, "wholesale_real_estate") is True


# ── 4. Requires setup from real configuration only ──────────────────────────

def test_requires_setup_only_from_real_signals(db_session, sample_org):
    # sample_org has stored Twilio credentials => sms is not "requires setup".
    assert er.evaluate(db_session, "sms", org=sample_org)["state"] == "enabled"
    bare = _mk_org(db_session, None)
    res = er.evaluate(db_session, "sms", org=bare)
    assert res["state"] == "requires_setup" and res["enabled"] is True
    assert res["setup"]["probe"] == "twilio"
    # A feature with no truthful signal never claims it.
    assert er.evaluate(db_session, "ai_assist", org=bare)["setup"] is None


# ── 5. API: explain, history, bulk, permission, isolation ───────────────────

def test_explain_history_and_bulk(client, db_session, brand, god):
    _ensure_router()
    o = _mk_org(db_session, None, platform=brand)
    H = _h(db_session, god)
    r = client.post("/god/entitlements/bulk", headers=H, json={
        "action": "set", "scope": "brand", "platform_id": brand.id, "state": "disabled",
        "feature_keys": ["campaigns", "cadences"], "reason": "not sold"})
    assert r.status_code == 200 and r.json()["changed"] == 2
    ex = client.get("/god/entitlements/explain", headers=H,
                    params={"feature_key": "campaigns", "scope": "org", "org_id": o.id}).json()
    assert ex["state"] == "disabled"
    assert [s["layer"] for s in ex["steps"]] == ["platform", "brand", "org"]
    assert ex["steps"][1]["override"]["state"] == "disabled"
    assert ex["steps"][1]["override"]["actor"] == "U"
    assert ex["server_enforcement"]["org_has_feature"] is False
    m = client.get("/god/entitlements/matrix", headers=H,
                   params={"scope": "brand", "platform_id": brand.id}).json()
    assert m["kpis"]["overrides"] == 2 and m["kpis"]["total"] == len(ent.FEATURES)
    r = client.post("/god/entitlements/bulk", headers=H, json={
        "action": "reset", "scope": "brand", "platform_id": brand.id,
        "feature_keys": ["campaigns", "cadences", "crm"]})
    assert r.json()["changed"] == 2                              # crm had nothing to reset
    ev = client.get("/god/entitlements/history", headers=H,
                    params={"platform_id": brand.id}).json()["events"]
    assert len(ev) == 4 and {e["action"] for e in ev} == {"set", "reset"}
    cat = client.get("/god/entitlements/catalog", headers=H).json()
    assert {f["key"] for f in cat["features"]} == set(ent.FEATURES)
    assert cat["plan_presets"]["growth"] == ent.PLAN_FEATURES["growth"]


def test_non_god_is_refused_everywhere(client, db_session, sample_org):
    _ensure_router()
    for role in ("org_admin", "super_admin", "advisor"):
        u = _user(db_session, sample_org, role)
        H = _h(db_session, u)
        assert client.get("/god/entitlements/catalog", headers=H).status_code == 403
        assert client.get("/god/entitlements/matrix", headers=H,
                          params={"scope": "org", "org_id": sample_org.id}).status_code == 403
        assert client.post("/god/entitlements/overrides", headers=H, json={
            "scope": "org", "org_id": sample_org.id, "feature_key": "sms",
            "state": "enabled"}).status_code == 403
        assert client.get("/god/entitlements/history", headers=H).status_code == 403


def test_scope_targets_must_belong_to_the_org(client, db_session, god):
    _ensure_router()
    a = _mk_org(db_session, None)
    b = _mk_org(db_session, None)
    loc_b = Location(organization_id=b.id, name="B site")
    db_session.add(loc_b); db_session.commit()
    user_b = _user(db_session, b)
    H = _h(db_session, god)
    assert client.get("/god/entitlements/matrix", headers=H, params={
        "scope": "workspace", "org_id": a.id, "workspace_id": loc_b.id}).status_code == 404
    assert client.post("/god/entitlements/overrides", headers=H, json={
        "scope": "user", "org_id": a.id, "user_id": user_b.id, "feature_key": "sms",
        "state": "disabled"}).status_code == 404
    assert client.get("/god/entitlements/matrix", headers=H, params={
        "scope": "org", "org_id": "nope"}).status_code == 404
    assert client.post("/god/entitlements/overrides", headers=H, json={
        "scope": "org", "org_id": a.id, "feature_key": "not_a_feature",
        "state": "enabled"}).status_code == 400


# ── 6. Editors round-trip the STORED allow-list, never the resolved one ─────

def _cc_toggle(client, H, org_id, key):
    """Exactly what the Control Center EntitlementsTab switch does."""
    cc = client.get("/god/customers/%s/control-center" % org_id, headers=H).json()
    enabled = [t["key"] for t in cc["enabled_tools"]["tools"] if t["enabled"]]
    nxt = [k for k in enabled if k != key] if key in enabled else enabled + [key]
    r = client.put("/god/customers/%s/features" % org_id, headers=H, json={"enabled": nxt})
    assert r.status_code == 200, r.text
    return r.json()


def test_brand_disable_is_not_baked_into_allow_list_by_an_unrelated_toggle(
        client, db_session, brand, god):
    _ensure_router()
    o = _mk_org(db_session, None, platform=brand)       # legacy: everything
    H = _h(db_session, god)
    K = "wholesale_real_estate"
    client.post("/god/entitlements/overrides", headers=H, json={
        "scope": "brand", "platform_id": brand.id, "feature_key": K, "state": "disabled"})
    rep = client.get("/god/customers/%s/features" % o.id, headers=H).json()
    assert rep["mode"] == "all" and K in rep["enabled"]          # stored list
    assert K not in rep["effective"]["enabled"]                  # resolved, separate
    assert K in rep["effective"]["differs_from_allow_list"]
    _cc_toggle(client, H, o.id, "reports")                       # unrelated switch off
    db_session.expire_all()
    stored = json.loads(db_session.query(Organization).get(o.id).enabled_features)
    assert K in stored and "reports" not in stored
    client.post("/god/entitlements/overrides/reset", headers=H, json={
        "scope": "brand", "platform_id": brand.id, "feature_key": K})
    db_session.expire_all()
    assert ent.org_has_feature(db_session.query(Organization).get(o.id), K) is True


def test_org_override_is_not_copied_into_allow_list(client, db_session, god):
    _ensure_router()
    o = _mk_org(db_session, ["leads"])
    H = _h(db_session, god)
    K = "campaigns"
    client.post("/god/entitlements/overrides", headers=H, json={
        "scope": "org", "org_id": o.id, "feature_key": K, "state": "enabled"})
    assert ent.org_has_feature(o, K) is True
    _cc_toggle(client, H, o.id, "reports")                       # unrelated switch on
    db_session.expire_all()
    stored = json.loads(db_session.query(Organization).get(o.id).enabled_features)
    assert stored == ["leads", "reports"]
    client.post("/god/entitlements/overrides/reset", headers=H, json={
        "scope": "org", "org_id": o.id, "feature_key": K})
    db_session.expire_all()
    assert ent.org_has_feature(db_session.query(Organization).get(o.id), K) is False


# ── 7. Query budget ─────────────────────────────────────────────────────────

def _count_queries(db, fn):
    from sqlalchemy import event
    n = {"q": 0}
    eng = db.get_bind()

    def _cb(*a, **k):
        n["q"] += 1
    event.listen(eng, "before_cursor_execute", _cb)
    try:
        fn()
    finally:
        event.remove(eng, "before_cursor_execute", _cb)
    return n["q"]


def test_enforcement_query_budget(db_session, brand, god):
    o = _mk_org(db_session, None, platform=brand)
    u = _user(db_session, o, "advisor")
    er.set_override(db_session, scope="brand", scope_id=brand.id,
                    feature_key="campaigns", state="disabled", actor_user_id=god.id)
    er.set_override(db_session, scope="role", scope_id="advisor", org_id=o.id,
                    feature_key="reports", state="disabled", actor_user_id=god.id)
    db_session.commit()
    o = db_session.query(Organization).get(o.id)
    u = db_session.query(User).get(u.id)
    er.load_overrides(db_session, o)                             # warm table probe
    # Org-level check: exactly one override query, no brand-label lookup.
    assert _count_queries(db_session, lambda: ent.org_has_feature(o, "campaigns")) == 1
    # Nav: one override query + a bounded role lookup, not one per feature.
    feats = []
    q = _count_queries(db_session, lambda: feats.append(ent.nav_features(db_session, o, u, None)))
    assert q <= 4, q
    assert "campaigns" not in feats[0] and "reports" not in feats[0] and "leads" in feats[0]
