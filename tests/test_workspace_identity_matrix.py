# -*- coding: utf-8 -*-
"""BUSINESS IDENTITY IS NOT A FEATURE SWITCH - regression matrix (2026-10-21).

Production, 2026-10-21 00:40 CDT: Roofing + Insurance capabilities were
switched on for Restland (industry "funeral"). The server then reported
Restland as experience "roofing" with program_route /program/roofing, and the
launcher showed it as a second "Roofing" card. Identity was read straight
from the switches.

Pinned here:
  * a capability can name the business only when the business's own industry
    agrees, or when it records no specific industry (generic/other/unset);
  * explicit identity markers (SCI program, Wholesale host) are never
    overridden by a capability;
  * every current production identity is unchanged (shapes mirror production);
  * capabilities keep working - the Roofing API still serves a funeral home
    that has the Roofing capability;
  * the launcher list is still exactly the person's memberships: no switch
    adds, renames or grants a workspace.
"""
import itertools
import json
import uuid
from types import SimpleNamespace

import pytest

from app.models.models import Organization, User
from app.models.sales_models import Membership
from app.services import program_experience as PE
from app.services.auth_service import create_access_token, hash_password

ROOF, INS, WRE = "roofing_program", "insurance_agency", "wholesale_real_estate"

# Exact production feature lists (2026-10-21) - shape only, no customer data.
RESTLAND_FULL = ["ai_assist", "audit_log", "availability", "booking", "branding_settings", "cadences", "calendar",
                 "campaigns", "case_files", "compliance", "crm", "crm_connectors", "email", "imports",
                 "insurance_agency", "lead_cleanup", "leads", "master_dashboard", "reports", "roofing_program", "sms",
                 "tier_config", "users", "voice", "wholesale_real_estate"]
ROOFING_CO = ["ai_assist", "availability", "booking", "calendar", "compliance", "imports", "leads", "reports",
              "roofing_program", "sms", "users"]
MAXLIFE = ["leads", "insurance_agency", "booking", "calendar", "availability", "reports"]

SPECIFIC = ["funeral", "cemetery", "energy", "cleaning", "fiber", "real_estate", "roofing", "insurance"]
NEUTRAL = ["generic", "other", "", None]
FEATURE_SETS = [None, [], [ROOF], [INS], [WRE], [ROOF, INS], [ROOF, WRE], [INS, WRE], [ROOF, INS, WRE]]


def _o(industry, features, slug="acme"):
    return SimpleNamespace(id="org-" + uuid.uuid4().hex[:6], slug=slug, industry=industry,
                           enabled_features=None if features is None else json.dumps(features))


USER = SimpleNamespace(id="u-1", role="org_admin")


def _exp(org, is_program=False, named=False):
    return PE.experience_for(None, USER, org, is_program, named_wholesale=named)


# ── 1. The full matrix: no capability can rename a business with its own industry ──

@pytest.mark.parametrize("industry", SPECIFIC + NEUTRAL)
@pytest.mark.parametrize("features", FEATURE_SETS)
def test_matrix_capability_never_renames_a_business(industry, features):
    org = _o(industry, features)
    exp = _exp(org)
    feats = set(features or [])
    ind = (industry or "").lower()
    neutral = ind in ("", "generic", "other")
    if exp == "roofing":
        assert ROOF in feats and (ind == "roofing" or neutral), (industry, features)
    if exp == "insurance":
        assert INS in feats and (ind == "insurance" or neutral), (industry, features)
    if exp == "energy":
        assert ind == "energy"
    # A business with its own specific industry is never shown as another business.
    if ind in SPECIFIC:
        assert exp in ("platform", ind if ind in ("roofing", "insurance", "energy") else "platform"), (industry, features, exp)
    # program_route (the Roofing app as home) follows exactly the same rule.
    assert PE.roofing_identity(org) == (exp == "roofing"), (industry, features)


@pytest.mark.parametrize("industry", SPECIFIC + NEUTRAL)
@pytest.mark.parametrize("features", FEATURE_SETS)
def test_matrix_capability_switches_stay_capabilities(industry, features):
    """Identity is protected WITHOUT removing what the org can do."""
    from app.services.roofing.common import explicitly_enabled
    org = _o(industry, features)
    feats = set(features or [])
    assert explicitly_enabled(org) == (ROOF in feats)
    assert PE._explicit(org, INS) == (INS in feats)


# ── 2. Every production identity, named ──

@pytest.mark.parametrize("label,industry,features,expected", [
    ("Restland, full capability list (the incident)", "funeral", RESTLAND_FULL, "platform"),
    ("Restland, Wholesale only (today's settings)", "funeral",
     [f for f in RESTLAND_FULL if f not in (ROOF, INS)], "platform"),
    ("EvoSys Pro Roofing", "roofing", ROOFING_CO, "roofing"),
    ("Roofing company, program not switched on", "roofing", ["leads", "reports"], "platform"),
    ("Max Life demo agency", "insurance", MAXLIFE, "insurance"),
    ("Insurance org, legacy NULL list", "insurance", None, "platform"),
    ("Atlantis", "energy", ["leads", "reports", "users"], "energy"),
    ("Atlantis with Roofing + Insurance switched on", "energy", ["leads", ROOF, INS], "energy"),
    ("Cleaning company with Roofing switched on", "cleaning", ["leads", ROOF], "platform"),
    ("Generic org with Roofing on (pre-industry setup)", "generic", ROOFING_CO, "roofing"),
    ("Generic org with Insurance on", "generic", MAXLIFE, "insurance"),
    ("Generic org, both - Roofing named first, as before", "generic", [ROOF, INS], "roofing"),
])
def test_production_identities(label, industry, features, expected):
    assert _exp(_o(industry, features)) == expected, label


def test_explicit_markers_beat_capabilities():
    # SCI program with every capability on is still SCI.
    assert _exp(_o("funeral", RESTLAND_FULL), is_program=True) == "sci"
    # EvoSys's Wholesale host with Roofing + Insurance switched on stays Wholesale for a named seat.
    evo = _o("generic", ["leads", WRE, ROOF, INS], slug=PE.WHOLESALE_HOST_SLUG)
    assert _exp(evo, named=True) == "wholesale"
    # ...and without a named seat, the capabilities apply (generic org) - unchanged behaviour.
    assert _exp(evo, named=False) == "roofing"


def test_none_org_is_safe():
    assert PE.roofing_identity(None) is False
    assert PE.insurance_identity(None) is False


# ── 3. Live endpoints: launcher, Roofing API, isolation ──

_SEQ = itertools.count(1)


def _org(db, name, slug, industry, features):
    o = Organization(name=name, slug="%s-%s" % (slug, uuid.uuid4().hex[:4]), plan="trial", industry=industry,
                     is_active=True, enabled_features=None if features is None else json.dumps(features))
    db.add(o)
    db.commit()
    return o


def _user(db, org, role="org_admin"):
    u = User(organization_id=org.id if org else None, email="ident-%d@example.com" % next(_SEQ),
             full_name="Identity Test", role=role, password_hash=hash_password("Pass12345!"),
             is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _m(db, u, org, role="org_admin"):
    db.add(Membership(user_id=u.id, scope_type="customer_org", scope_id=org.id, role=role, is_active=True))
    db.commit()


def _h(db, u, ws=None):
    h = {"Authorization": "Bearer %s" % create_access_token(u, db)}
    if ws:
        h["X-Workspace-Id"] = ws
    return h


@pytest.fixture()
def shape(db_session):
    db = db_session
    rest = _org(db, "Restland Cemetery and Funeral Home", "restland", "funeral", RESTLAND_FULL)
    roof = _org(db, "EvoSys Pro Roofing", "evosys-pro-roofing", "roofing", ROOFING_CO)
    ins = _org(db, "Max Life Demo Agency (DEMO)", "max-life-demo-agency", "insurance", MAXLIFE)
    atl = _org(db, "Atlantis Light & Power", "atlantis-light-power", "energy", ["leads", "reports", "users", ROOF])
    outsider = _org(db, "Other Roofing Co", "other-roofing", "roofing", ROOFING_CO)  # nobody here is a member
    admin = _user(db, rest)
    for o in (rest, roof, ins, atl):
        _m(db, admin, o)
    return {"db": db, "rest": rest, "roof": roof, "ins": ins, "atl": atl, "outsider": outsider, "admin": admin}


def _contexts(client, db, u):
    r = client.get("/auth/my-contexts", headers=_h(db, u))
    assert r.status_code == 200, r.text
    return {w["organization_id"]: w for w in r.json()["workspace_contexts"]}


def test_launcher_keeps_restland_as_restland(client, shape):
    db = shape["db"]
    ws = _contexts(client, db, shape["admin"])
    r = ws[shape["rest"].id]
    assert r["organization_name"] == "Restland Cemetery and Funeral Home"
    assert r["industry"] == "funeral"
    assert r["experience"] == "platform"
    assert r["program_route"] is None
    # Exactly one workspace opens the Roofing app: the roofing company.
    roofing = [k for k, w in ws.items() if w["program_route"] == "/program/roofing" or w["experience"] == "roofing"]
    assert roofing == [shape["roof"].id]
    assert ws[shape["ins"].id]["experience"] == "insurance"
    assert ws[shape["atl"].id]["experience"] == "energy"
    assert ws[shape["atl"].id]["program_route"] is None


def test_launcher_lists_only_memberships(client, shape):
    db = shape["db"]
    ws = _contexts(client, db, shape["admin"])
    assert set(ws) == {shape[k].id for k in ("rest", "roof", "ins", "atl")}
    assert shape["outsider"].id not in ws


def test_roofing_capability_still_works_inside_restland(client, shape):
    """Identity protected, capability preserved: the Roofing API serves Restland."""
    db = shape["db"]
    r = client.get("/roofing-ops/status", headers=_h(db, shape["admin"], ws=shape["rest"].id))
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == shape["rest"].id


def test_capability_does_not_cross_tenants(client, shape):
    """A member of Restland cannot reach another roofing company by selecting it."""
    db = shape["db"]
    r = client.get("/roofing-ops/status", headers=_h(db, shape["admin"], ws=shape["outsider"].id))
    # The server ignores a selection the caller has no membership in (it falls
    # back to the caller's own workspace) or refuses - either way the
    # outsider's roofing company is never served.
    if r.status_code == 200:
        assert r.json()["organization_id"] != shape["outsider"].id
        assert r.json()["organization_id"] == shape["rest"].id
    else:
        assert r.status_code in (402, 403, 404), r.status_code
