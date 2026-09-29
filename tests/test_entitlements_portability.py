"""BOOKABOOST PORTABILITY PROOF — spec section 22.

`wholesale_real_estate` is a PLATFORM module. These ten steps prove it moves
between brands purely by entitlement configuration, and that a brand-level
decision reaches the brand's organizations on the SERVER (require_feature),
in the NAV (/branding/org), and nowhere else (cross-brand isolation, tenant
data untouched).
"""
import json
import uuid

import pytest

from app.models.models import Lead, Organization, Platform, User
from app.services.auth_service import create_access_token, hash_password

KEY = "wholesale_real_estate"


def _ensure_router():
    from app.main import app
    from app.routers.entitlements_router import router
    if not any(getattr(r, "path", "").startswith("/god/entitlements") for r in app.routes):
        app.include_router(router)


@pytest.fixture()
def world(db_session):
    _ensure_router()
    bb = Platform(name="BookaBoost", slug="bb-%s" % uuid.uuid4().hex[:6])
    evo = Platform(name="EvoSys Pro", slug="evo-%s" % uuid.uuid4().hex[:6])
    db_session.add_all([bb, evo]); db_session.commit()

    def org(name, platform, features):
        o = Organization(name=name, slug="o-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                         industry="real_estate", platform_id=platform.id,
                         enabled_features=None if features is None else json.dumps(features))
        db_session.add(o); db_session.commit()
        return o

    def admin(o):
        u = User(organization_id=o.id, email="a-%s@t.test" % uuid.uuid4().hex[:6],
                 password_hash=hash_password("x"), full_name="Admin " + o.name,
                 role="org_admin", must_change_password=False)
        db_session.add(u); db_session.commit()
        return u

    bb_org = org("BB Wholesaler", bb, None)                  # legacy: everything
    bb_org2 = org("BB Listed", bb, ["leads", KEY])           # explicit allow-list
    evo_org = org("Evo Wholesaler", evo, ["leads", KEY])
    god = User(organization_id=None, email="god-%s@t.test" % uuid.uuid4().hex[:6],
               password_hash=hash_password("x"), full_name="Owner", role="god_admin",
               must_change_password=False)
    db_session.add(god); db_session.commit()
    users = {o.id: admin(o) for o in (bb_org, bb_org2, evo_org)}
    hdr = lambda u: {"Authorization": "Bearer " + create_access_token(u, db_session)}  # noqa: E731
    return dict(bb=bb, evo=evo, bb_org=bb_org, bb_org2=bb_org2, evo_org=evo_org, god=god,
                users=users, hdr=hdr)


def _set(client, w, **body):
    body.setdefault("feature_key", KEY)
    r = client.post("/god/entitlements/overrides", headers=w["hdr"](w["god"]), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _reset(client, w, **body):
    body.setdefault("feature_key", KEY)
    r = client.post("/god/entitlements/overrides/reset", headers=w["hdr"](w["god"]), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _effective(client, w, org):
    r = client.get("/god/entitlements/matrix", headers=w["hdr"](w["god"]),
                   params={"scope": "org", "org_id": org.id})
    assert r.status_code == 200, r.text
    return {f["key"]: f for f in r.json()["features"]}[KEY]


def _api(client, w, org):
    return client.get("/wholesale/settings", headers=w["hdr"](w["users"][org.id])).status_code


def _nav(client, w, org):
    body = client.get("/branding/org", headers=w["hdr"](w["users"][org.id])).json()
    return body["enabled_features"]


def test_bookaboost_portability_ten_steps(client, db_session, world):
    w = world
    bb_org, bb_org2, evo_org = w["bb_org"], w["bb_org2"], w["evo_org"]

    # Seed tenant data so step 9 can prove toggling never touches it.
    lead = Lead(organization_id=bb_org2.id, first_name="Pat", last_name="Seller",
                phone="12145550100")
    db_session.add(lead); db_session.commit()
    lead_id = lead.id
    ws = client.patch("/wholesale/settings", headers=w["hdr"](w["users"][bb_org2.id]),
                      json={"investor_percentage": 61})
    assert ws.status_code == 200, ws.text

    # 1. PLATFORM ENABLE — explicit platform-level enable; every org has it.
    _set(client, w, scope="platform", state="enabled", reason="module GA")
    for o in (bb_org, bb_org2, evo_org):
        assert _effective(client, w, o)["effective_state"] == "enabled"
        assert _api(client, w, o) == 200

    # 2. BRAND INHERITANCE — BookaBoost has no row; it inherits the platform.
    f = _effective(client, w, bb_org)
    assert f["layers"]["brand"]["state"] == "inherited"
    assert f["inherited_from"] == "Platform"

    # 3. BOOKABOOST BRAND DISABLE — both BookaBoost orgs lose it (legacy-all and
    #    explicit allow-list alike): state, server and nav.
    _set(client, w, scope="brand", platform_id=w["bb"].id, state="disabled",
         reason="BookaBoost does not sell wholesale")
    for o in (bb_org, bb_org2):
        f = _effective(client, w, o)
        assert f["effective_state"] == "disabled"
        assert f["inherited_from"] == "Brand (BookaBoost)"
        # 8. BACKEND API refused by require_feature (402, the entitlement code).
        r = client.get("/wholesale/settings", headers=w["hdr"](w["users"][o.id]))
        assert r.status_code == 402 and KEY in r.json()["detail"]
        # 6/7. NAVIGATION + FRONTEND ROUTE: the shell's feature list drops it,
        #      which is what Layout featureKey and ProtectedRoute gate on.
        nav = _nav(client, w, o)
        assert nav is not None and KEY not in nav and "leads" in nav

    # 10. CROSS-BRAND ISOLATION — EvoSys Pro org is untouched.
    assert _effective(client, w, evo_org)["effective_state"] == "enabled"
    assert _api(client, w, evo_org) == 200
    assert KEY in _nav(client, w, evo_org)

    # 4. ORGANIZATION OVERRIDE — brand OFF + org override ON => enabled (mockup).
    body = _set(client, w, scope="org", org_id=bb_org2.id, state="enabled",
                reason="pilot customer")
    assert body["feature"]["effective_state"] == "enabled"
    assert body["feature"]["inherited_from"] == "Organization (Override)"
    assert _api(client, w, bb_org2) == 200
    assert KEY in _nav(client, w, bb_org2)
    assert _api(client, w, bb_org) == 402          # sibling org still off

    # 5. RESET OVERRIDE — back to inherited from the brand, i.e. disabled again.
    body = _reset(client, w, scope="org", org_id=bb_org2.id, reason="pilot over")
    assert body["reset"] is True
    assert body["feature"]["is_inherited"] is True
    assert body["feature"]["effective_state"] == "disabled"
    assert _api(client, w, bb_org2) == 402

    # 9. TENANT DATA — nothing about the tenant's records changed while toggling.
    db_session.expire_all()
    assert db_session.query(Lead).filter(Lead.id == lead_id).count() == 1
    assert db_session.query(Organization).get(bb_org2.id).enabled_features == json.dumps(["leads", KEY])
    _reset(client, w, scope="brand", platform_id=w["bb"].id)
    assert _api(client, w, bb_org2) == 200
    got = client.get("/wholesale/settings", headers=w["hdr"](w["users"][bb_org2.id])).json()
    assert got["investor_percentage"] == 61

    # History recorded every step, append-only, and the audit log has them too.
    ev = client.get("/god/entitlements/history", headers=w["hdr"](w["god"]),
                    params={"org_id": bb_org2.id, "feature_key": KEY}).json()["events"]
    actions = [(e["scope"], e["action"], e["new_state"]) for e in ev]
    assert ("brand", "set", "disabled") in actions
    assert ("org", "set", "enabled") in actions
    assert ("org", "reset", "inherited") in actions
    assert ("brand", "reset", "inherited") in actions
    from app.models.models import AuditLogEntry
    assert db_session.query(AuditLogEntry).filter(
        AuditLogEntry.action.like("entitlement.%")).count() >= 5


def test_brand_disable_does_not_leak_to_other_brand_in_enforcement_functions(db_session, world):
    from app.services import entitlement_resolver as er
    from app.services import entitlements as ent
    w = world
    er.set_override(db_session, scope="brand", scope_id=w["bb"].id, feature_key=KEY,
                    state="disabled", actor_user_id=w["god"].id)
    db_session.commit()
    assert ent.org_has_feature(w["bb_org"], KEY) is False
    assert KEY not in ent.enabled_for(w["bb_org"])
    assert ent.org_has_feature(w["evo_org"], KEY) is True
    assert ent.enabled_for(w["evo_org"]) == ["leads", KEY]      # untouched, legacy value
