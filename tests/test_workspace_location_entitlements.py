"""WORKSPACE (LOCATION) ENTITLEMENT OVERRIDES ARE ENFORCED — not decorative.

Spec section 15. The acceptance matrix, exactly:

    Org has Wholesale ON.  Workspace A inherits ON.  Workspace B explicitly OFF.
    User in A: ALLOW (API 200, nav shows).
    User in B: API DENY (402, the same status as an organization-level feature
               refusal), nav HIDES (enabled_features and platform.offered both
               drop it), direct URL denied (frontend half:
               tests/frontend/workspaceLocationNav.test.mjs).

Plus the rules that stop the header being a bypass or a widening:
  * no header + exactly one location  -> that location (implicit)
  * no header + several locations     -> most restrictive across all of them
  * a header naming a location the caller is not assigned to, another tenant's
    location, or an inactive one -> 403 on gated APIs; ignored + `rejected`
    on /branding/org (the shell must still load)
  * org_admin may select any location of their organization; god is not
    narrowed unless previewing a location.
"""
import itertools

import pytest

from app.models.location_models import Location, UserLocation
from app.models.models import Organization, Platform, User
from app.services import entitlement_resolver as er
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_location import LOCATION_HEADER

_SEQ = itertools.count(1)
GATED = "/wholesale/properties"
KEY = "wholesale_real_estate"


def _user(db, org, role="advisor", name=None):
    n = next(_SEQ)
    u = User(organization_id=org.id if org else None, email="wl%d@example.com" % n,
             password_hash=hash_password("x"), full_name=name or ("WL Person %d" % n),
             role=role, must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


def _loc(db, org, name, active=True):
    l = Location(organization_id=org.id, name=name, is_active=active)
    db.add(l)
    db.commit()
    return l


def _assign(db, user, *locs):
    for l in locs:
        db.add(UserLocation(user_id=user.id, location_id=l.id, organization_id=l.organization_id))
    db.commit()


def _h(db, u, location=None):
    h = {"Authorization": "Bearer " + create_access_token(u, db)}
    if location:
        h[LOCATION_HEADER] = location
    return h


@pytest.fixture()
def world(db_session):
    plat = Platform(name="EvoSys Pro", slug="evosyspro")
    db_session.add(plat)
    db_session.flush()
    org = Organization(name="WL Customer", slug="wl-customer", plan="standard",
                       platform_id=plat.id, industry="wholesale_real_estate",
                       enabled_features=None)       # Wholesale ON (legacy: everything)
    other = Organization(name="WL Other Tenant", slug="wl-other", plan="standard",
                         platform_id=plat.id, enabled_features=None)
    db_session.add_all([org, other])
    db_session.commit()
    a = _loc(db_session, org, "Workspace A")
    b = _loc(db_session, org, "Workspace B")
    foreign = _loc(db_session, other, "Other Tenant HQ")
    # Workspace B explicitly OFF; A has no row (inherits ON).
    er.set_override(db_session, scope="workspace", scope_id=b.id, org_id=org.id,
                    feature_key=KEY, state="disabled", actor_user_id=None, reason="test")
    db_session.commit()
    ua = _user(db_session, org, name="User In A")
    ub = _user(db_session, org, name="User In B")
    um = _user(db_session, org, name="User In A And B")
    un = _user(db_session, org, name="User In No Location")
    adm = _user(db_session, org, role="org_admin", name="WL Admin")
    _assign(db_session, ua, a)
    _assign(db_session, ub, b)
    _assign(db_session, um, a, b)
    return dict(org=org, other=other, a=a, b=b, foreign=foreign,
                ua=ua, ub=ub, um=um, un=un, adm=adm, plat=plat)


def _nav(client, db, u, location=None):
    r = client.get("/branding/org", headers=_h(db, u, location))
    assert r.status_code == 200, r.text
    return r.json()


def _shows_wholesale(body):
    f = body["enabled_features"]
    return (f is None or KEY in f) and bool(body["platform"]["offered"]["wholesale"])


# ── The spec matrix ─────────────────────────────────────────────────────────

def test_user_in_workspace_a_is_allowed_api_and_nav(client, db_session, world):
    w = world
    assert client.get(GATED, headers=_h(db_session, w["ua"])).status_code == 200
    body = _nav(client, db_session, w["ua"])
    assert _shows_wholesale(body)
    loc = body["workspace_location"]
    assert loc["mode"] == "implicit_single"
    assert loc["selected"]["id"] == w["a"].id
    assert loc["enforced"] is True


def test_user_in_workspace_b_is_denied_api_and_nav_hides(client, db_session, world):
    w = world
    r = client.get(GATED, headers=_h(db_session, w["ub"]))
    # Same status as an organization-level feature refusal.
    assert r.status_code == 402, r.text
    assert "Workspace B" in r.json()["detail"]
    body = _nav(client, db_session, w["ub"])
    assert KEY not in body["enabled_features"]
    assert body["platform"]["offered"]["wholesale"] is False
    # Everything else the organization has is still offered - B narrows one key.
    assert "leads" in body["enabled_features"]


def test_workspace_b_denial_matches_the_organization_level_status(client, db_session, world):
    """'Same status code as other feature denials.'"""
    w = world
    er.reset_override(db_session, scope="workspace", scope_id=w["b"].id, org_id=w["org"].id,
                      feature_key=KEY, actor_user_id=None)
    er.set_override(db_session, scope="org", scope_id=w["org"].id, feature_key=KEY,
                    state="disabled", actor_user_id=None, reason="test")
    db_session.commit()
    org_level = client.get(GATED, headers=_h(db_session, w["ua"])).status_code
    assert org_level == 402
    er.reset_override(db_session, scope="org", scope_id=w["org"].id, feature_key=KEY,
                      actor_user_id=None)
    er.set_override(db_session, scope="workspace", scope_id=w["b"].id, org_id=w["org"].id,
                    feature_key=KEY, state="disabled", actor_user_id=None, reason="test")
    db_session.commit()
    assert client.get(GATED, headers=_h(db_session, w["ub"])).status_code == org_level


def test_resetting_workspace_b_restores_access(client, db_session, world):
    w = world
    er.reset_override(db_session, scope="workspace", scope_id=w["b"].id, org_id=w["org"].id,
                      feature_key=KEY, actor_user_id=None)
    db_session.commit()
    assert client.get(GATED, headers=_h(db_session, w["ub"])).status_code == 200
    assert _shows_wholesale(_nav(client, db_session, w["ub"]))


def test_workspace_cannot_grant_what_the_organization_lacks(client, db_session, world):
    w = world
    er.set_override(db_session, scope="org", scope_id=w["org"].id, feature_key=KEY,
                    state="disabled", actor_user_id=None, reason="test")
    er.set_override(db_session, scope="workspace", scope_id=w["a"].id, org_id=w["org"].id,
                    feature_key=KEY, state="enabled", actor_user_id=None, reason="test")
    db_session.commit()
    assert client.get(GATED, headers=_h(db_session, w["ua"])).status_code == 402
    assert not _shows_wholesale(_nav(client, db_session, w["ua"]))


# ── The header cannot be a bypass ───────────────────────────────────────────

def test_user_in_b_cannot_select_a_location_they_are_not_assigned_to(client, db_session, world):
    w = world
    r = client.get(GATED, headers=_h(db_session, w["ub"], w["a"].id))
    assert r.status_code == 403
    # The shell still loads, ignores the selection, stays narrowed to B, and
    # says the selection was rejected so the client can clear it.
    body = _nav(client, db_session, w["ub"], w["a"].id)
    assert body["workspace_location"]["rejected"] is True
    assert body["workspace_location"]["selected"]["id"] == w["b"].id
    assert not _shows_wholesale(body)


def test_another_tenants_location_is_refused(client, db_session, world):
    w = world
    for u in (w["ua"], w["adm"], w["un"]):
        assert client.get(GATED, headers=_h(db_session, u, w["foreign"].id)).status_code == 403
    assert client.get(GATED, headers=_h(db_session, w["ua"], "no-such-id")).status_code == 403


def test_an_inactive_location_cannot_be_selected(client, db_session, world):
    w = world
    w["a"].is_active = False
    db_session.commit()
    assert client.get(GATED, headers=_h(db_session, w["adm"], w["a"].id)).status_code == 403


def test_multi_location_user_with_no_selection_gets_the_most_restrictive_answer(
        client, db_session, world):
    w = world
    assert client.get(GATED, headers=_h(db_session, w["um"])).status_code == 402
    body = _nav(client, db_session, w["um"])
    assert body["workspace_location"]["mode"] == "all_assigned_most_restrictive"
    assert not _shows_wholesale(body)
    # Selecting a location they work at gives that location's answer.
    assert client.get(GATED, headers=_h(db_session, w["um"], w["a"].id)).status_code == 200
    assert _shows_wholesale(_nav(client, db_session, w["um"], w["a"].id))
    assert client.get(GATED, headers=_h(db_session, w["um"], w["b"].id)).status_code == 402
    # The selector lists exactly their two locations.
    avail = {l["id"] for l in body["workspace_location"]["available"]}
    assert avail == {w["a"].id, w["b"].id}


def test_person_with_no_location_is_at_organization_level(client, db_session, world):
    w = world
    assert client.get(GATED, headers=_h(db_session, w["un"])).status_code == 200
    body = _nav(client, db_session, w["un"])
    assert body["workspace_location"]["mode"] == "organization"
    assert body["workspace_location"]["available"] == []


def test_org_admin_may_select_any_location_of_their_organization(client, db_session, world):
    w = world
    assert client.get(GATED, headers=_h(db_session, w["adm"])).status_code == 200
    assert client.get(GATED, headers=_h(db_session, w["adm"], w["a"].id)).status_code == 200
    assert client.get(GATED, headers=_h(db_session, w["adm"], w["b"].id)).status_code == 402
    body = _nav(client, db_session, w["adm"], w["b"].id)
    assert body["workspace_location"]["mode"] == "selected"
    assert not _shows_wholesale(body)


def test_other_features_are_untouched_by_a_wholesale_workspace_override(
        client, db_session, world):
    w = world
    body = _nav(client, db_session, w["ub"])
    from app.services.entitlements import ALL_FEATURE_KEYS
    assert set(body["enabled_features"]) == set(ALL_FEATURE_KEYS) - {KEY}


def test_no_workspace_rows_means_the_old_answer(client, db_session, world):
    """An organization with no workspace rows answers exactly as before
    (legacy null list), whatever location its people work at."""
    w = world
    er.reset_override(db_session, scope="workspace", scope_id=w["b"].id, org_id=w["org"].id,
                      feature_key=KEY, actor_user_id=None)
    db_session.commit()
    body = _nav(client, db_session, w["ub"])
    assert body["enabled_features"] is None


# ── God Mode says it is enforced ────────────────────────────────────────────

def _god(db):
    g = User(organization_id=None, email="wl-god%d@example.com" % next(_SEQ),
             password_hash=hash_password("x"), full_name="God", role="god_admin",
             must_change_password=False, is_active=True)
    db.add(g)
    db.commit()
    return g


def test_god_matrix_and_explain_say_workspace_overrides_are_enforced(client, db_session, world):
    w = world
    h = _h(db_session, _god(db_session))
    m = client.get("/god/entitlements/matrix", headers=h,
                   params={"scope": "workspace", "org_id": w["org"].id,
                           "workspace_id": w["b"].id})
    assert m.status_code == 200, m.text
    body = m.json()
    assert body["enforced_at_request_time"] is True
    assert "not yet" not in (body["note"] or "")
    assert "enforced" in body["note"].lower()
    row = next(f for f in body["features"] if f["key"] == KEY)
    assert row["enabled"] is False
    e = client.get("/god/entitlements/explain", headers=h,
                   params={"feature_key": KEY, "scope": "workspace", "org_id": w["org"].id,
                           "workspace_id": w["b"].id}).json()
    assert e["server_enforcement"]["workspace_has_feature"] is False
    assert e["server_enforcement"]["enforced"] is True
    e2 = client.get("/god/entitlements/explain", headers=h,
                    params={"feature_key": KEY, "scope": "user", "org_id": w["org"].id,
                            "user_id": w["ub"].id, "workspace_id": w["b"].id}).json()
    assert e2["enabled"] is False


def test_god_is_not_narrowed_without_a_selection(client, db_session, world):
    w = world
    g = _god(db_session)
    from app.services import entitlements as ent
    assert ent.feature_allowed_for_request(db_session, w["org"], g, KEY) is True


# ── The header must be able to reach the server ─────────────────────────────

def test_branding_only_advertises_the_header_when_cors_allows_it(client, db_session, world):
    from app.main import app
    from app.services.workspace_location import header_allowed_by_cors

    class _R:
        pass
    r = _R()
    r.app = app
    body = _nav(client, db_session, world["ua"])
    expected = LOCATION_HEADER if header_allowed_by_cors(r) else None
    assert body["workspace_location"]["header"] == expected


def test_cors_allow_list_includes_the_location_header():
    """GATE: the browser sends X-Workspace-Location only when this passes.
    Requires the main.py BROWSER_HEADERS snippet in /tmp/claude-0/migr2/XD.md."""
    from app.main import BROWSER_HEADERS
    assert LOCATION_HEADER in BROWSER_HEADERS


# ── Non-raising helper used by code that decides rather than refuses ────────

def test_user_has_feature_applies_the_workspace_layer(db_session, world):
    from app.services import entitlements as ent
    org = world["org"]
    assert ent.user_has_feature(db_session, org, world["ua"], KEY) is True
    assert ent.user_has_feature(db_session, org, world["ub"], KEY) is False
    # Several locations, none selected: most restrictive.
    assert ent.user_has_feature(db_session, org, world["um"], KEY) is False
    # Other modules untouched.
    assert ent.user_has_feature(db_session, org, world["ub"], "leads") is True


# ── Review finding: a HOME org_admin is not an admin of a second workspace ──

def test_home_org_admin_is_only_an_advisor_in_a_second_workspace(client, db_session, world):
    from app.models.sales_models import Membership
    from app.services.workspace_access import SCOPE_CUSTOMER_ORG, WORKSPACE_HEADER
    w = world
    home = Organization(name="WL Home Org", slug="wl-home", plan="standard",
                        platform_id=w["plat"].id, enabled_features=None)
    db_session.add(home)
    db_session.commit()
    boss = _user(db_session, home, role="org_admin", name="Home Admin")
    db_session.add(Membership(user_id=boss.id, scope_type=SCOPE_CUSTOMER_ORG,
                              scope_id=w["org"].id, role="advisor", is_active=True))
    db_session.commit()
    _assign(db_session, boss, w["b"])

    def h(location=None):
        hh = _h(db_session, boss, location)
        hh[WORKSPACE_HEADER] = w["org"].id
        return hh

    # A location of B's org they are NOT assigned to: refused (no admin bypass).
    assert client.get(GATED, headers=h(w["a"].id)).status_code == 403
    # No header: evaluated at their assigned location (B, off), not org level.
    assert client.get(GATED, headers=h()).status_code == 402
    body = client.get("/branding/org", headers=h()).json()
    assert body["organization_id"] == w["org"].id
    assert body["workspace_location"]["mode"] != "organization"
    assert {l["id"] for l in body["workspace_location"]["available"]} == {w["b"].id}
    assert not _shows_wholesale(body)


def test_no_membership_in_the_active_org_never_borrows_the_home_role(db_session, world):
    """The gap itself: users.role describes the HOME org only. Resolving a
    location context in another org the person holds no membership in must not
    treat a home org_admin as that org's administrator."""
    from fastapi import HTTPException
    from app.services import entitlements as ent
    from app.services import workspace_location as wl
    w = world
    home = Organization(name="WL Home Org 2", slug="wl-home-2", plan="standard",
                        platform_id=w["plat"].id, enabled_features=None)
    db_session.add(home)
    db_session.commit()
    boss = _user(db_session, home, role="org_admin", name="Home Admin 2")
    _assign(db_session, boss, w["b"])      # a location assignment, no membership

    class _Req:
        def __init__(self, loc=None):
            self.headers = {LOCATION_HEADER: loc} if loc else {}
            self.state = type("S", (), {})()

    assert wl.workspace_role_in(db_session, boss, w["org"].id) is None
    assert wl.workspace_role_in(db_session, boss, home.id) == "org_admin"
    with pytest.raises(HTTPException) as e:
        wl.resolve(db_session, boss, w["org"].id, _Req(w["a"].id), strict=True)
    assert e.value.status_code == 403
    ctx = wl.resolve(db_session, boss, w["org"].id, _Req(), strict=True)
    assert ctx.location_ids == (w["b"].id,)
    assert ent.user_has_feature(db_session, w["org"], boss, KEY, _Req()) is False
    # god is still exempt.
    god = _user(db_session, None, role="god_admin", name="WL God")
    assert wl.workspace_role_in(db_session, god, w["org"].id) == "god_admin"
