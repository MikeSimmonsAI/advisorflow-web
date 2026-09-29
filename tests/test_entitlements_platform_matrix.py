"""The Feature Entitlements page now lands on the PLATFORM scope with no query
params, so GET /god/entitlements/matrix?scope=platform must answer the full
feature matrix (no org, no brand) and stay God-only."""
import uuid

from app.services import entitlements as ent
from tests.test_entitlements_resolver import _ensure_router, _h, _user


def test_platform_scope_matrix_returns_every_feature(client, db_session):
    _ensure_router()
    god = _user(db_session, None, role="god_admin")
    H = _h(db_session, god)
    r = client.get("/god/entitlements/matrix", headers=H, params={"scope": "platform"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"]["scope"] == "platform"
    assert {f["key"] for f in body["features"]} == set(ent.FEATURES)
    k = body["kpis"]
    assert k["total"] == len(ent.FEATURES)
    assert k["overrides"] == 0 and k["inherited"] == k["total"]


def test_platform_override_shows_in_platform_matrix(client, db_session):
    _ensure_router()
    god = _user(db_session, None, role="god_admin")
    H = _h(db_session, god)
    r = client.post("/god/entitlements/bulk", headers=H, json={
        "action": "set", "scope": "platform", "state": "disabled",
        "feature_keys": ["campaigns"], "reason": "qa %s" % uuid.uuid4().hex[:4]})
    assert r.status_code == 200 and r.json()["changed"] == 1
    body = client.get("/god/entitlements/matrix", headers=H, params={"scope": "platform"}).json()
    row = next(f for f in body["features"] if f["key"] == "campaigns")
    assert row["effective_state"] == "disabled"
    assert body["kpis"]["overrides"] == 1


def test_platform_matrix_is_god_only(client, db_session, sample_org):
    _ensure_router()
    admin = _user(db_session, sample_org, role="org_admin")
    r = client.get("/god/entitlements/matrix", headers=_h(db_session, admin),
                   params={"scope": "platform"})
    assert r.status_code == 403
