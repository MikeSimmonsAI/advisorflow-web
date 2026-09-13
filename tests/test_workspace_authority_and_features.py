"""ONE HUMAN, TWO CUSTOMERS, TWO DIFFERENT SETS OF RIGHTS.

THE DEFECT, reproduced live before this file existed. A person who is
org_admin of customer A and an ORDINARY USER of customer B was, inside B:

  * evaluated as an administrator — Users, Reports, Imports, Cadence, Audit
    Log, Tier Config and Organization Settings all opened for them;
  * shown every module in the sidebar even though B had NO features enabled;
  * able to reach every one of those routes by typing the URL.

Two independent causes, and both had to be closed:

  1. `require_admin` read `users.role`. That column is ONE VALUE FOR A WHOLE
     HUMAN — it cannot be true of two workspaces at once — and the dependency
     took neither `request` nor `db`, so the workspace was not even reachable
     from inside the check.
  2. Of twenty-two registered features, THREE were enforced anywhere. An
     organization with `enabled_features = []` was refused on campaigns, crm
     and case_files and served everything else.

What every test here is really asserting is that authority is a property of
(this person, THIS workspace) and never of the person alone.
"""

import json
import uuid

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_access import SCOPE_CUSTOMER_ORG, WORKSPACE_HEADER


# ── fixtures ────────────────────────────────────────────────────────────────

def _org(db, name, *, features=None, platform=None):
    org = Organization(name=name, slug="o-" + uuid.uuid4().hex[:8], plan="standard",
                       industry="energy", is_active=True,
                       platform_id=(platform.id if platform else None))
    if features is not None:
        org.enabled_features = json.dumps(features)
    db.add(org)
    db.commit()
    return org


def _person(db, *, email, role="advisor", home=None):
    u = User(organization_id=(home.id if home else None), email=email,
             password_hash=hash_password("TestPass123!"), full_name="Test Person",
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


def _hdr(db, user, org=None):
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if org is not None:
        h[WORKSPACE_HEADER] = org.id
    return h


ALL_FEATURES = ["leads", "campaigns", "cadences", "crm", "reports", "imports",
                "users", "audit_log", "tier_config", "master_dashboard",
                "branding_settings", "email", "compliance", "availability",
                "crm_connectors", "lead_cleanup"]


@pytest.fixture()
def two_customers(db_session):
    """The exact shape that failed: admin of A, ordinary user of B.

    B has NO features enabled, which is the zero-feature customer.
    """
    plat = Platform(name="EvoSys Pro", slug="evosyspro-" + uuid.uuid4().hex[:6])
    db_session.add(plat)
    db_session.commit()
    a = _org(db_session, "Customer A", features=ALL_FEATURES, platform=plat)
    b = _org(db_session, "Customer B", features=[], platform=plat)
    person = _person(db_session, email="two.workspaces@example.com",
                     role="org_admin", home=a)
    _member(db_session, person, a, "org_admin")
    _member(db_session, person, b, "advisor")
    return {"a": a, "b": b, "person": person, "platform": plat}


# ── 1. the role does not travel between workspaces ──────────────────────────

ADMIN_ROUTES = ["/admin/users", "/reports/overview", "/audit-log/",
                "/tier-definitions/", "/import-batches/"]


def test_admin_of_one_customer_is_not_admin_of_the_other(client, db_session,
                                                         two_customers):
    """THE HEADLINE. Same token, two workspaces, two different answers."""
    person, a, b = two_customers["person"], two_customers["a"], two_customers["b"]
    in_a = client.get("/admin/users", headers=_hdr(db_session, person, a))
    in_b = client.get("/admin/users", headers=_hdr(db_session, person, b))
    assert in_a.status_code == 200, in_a.text
    assert in_b.status_code in (402, 403), in_b.text
    assert in_a.status_code != in_b.status_code


def test_every_admin_surface_refuses_in_the_workspace_they_do_not_administer(
        client, db_session, two_customers):
    person, b = two_customers["person"], two_customers["b"]
    for path in ADMIN_ROUTES:
        r = client.get(path, headers=_hdr(db_session, person, b))
        assert r.status_code in (402, 403, 404), "%s -> %s" % (path, r.status_code)


def test_the_workspace_role_is_what_decides_not_the_user_row(client, db_session,
                                                             two_customers):
    """Proof the fix reads the MEMBERSHIP. The user row still says org_admin;
    promoting the membership in B is what changes the answer."""
    person, b = two_customers["person"], two_customers["b"]
    assert person.role == "org_admin"

    before = client.get("/admin/users", headers=_hdr(db_session, person, b))
    assert before.status_code in (402, 403)

    m = (db_session.query(Membership)
         .filter(Membership.user_id == person.id,
                 Membership.scope_id == b.id).first())
    m.role = "org_admin"
    b.enabled_features = json.dumps(ALL_FEATURES)
    db_session.commit()
    from app.services import workspace_access
    workspace_access.invalidate_workspace_memberships(person)

    after = client.get("/admin/users", headers=_hdr(db_session, person, b))
    assert after.status_code == 200, after.text


def test_asserting_a_workspace_you_do_not_hold_grants_nothing(client, db_session,
                                                              two_customers):
    """A header is a request, not a credential."""
    stranger_org = _org(db_session, "Someone Else", features=ALL_FEATURES)
    person = two_customers["person"]
    r = client.get("/admin/users", headers=_hdr(db_session, person, stranger_org))
    # The selection is discarded, so they fall back to their own home
    # workspace — never to the organization they named.
    assert r.status_code in (200, 402, 403)
    if r.status_code == 200:
        emails = {u["email"] for u in r.json()}
        assert "two.workspaces@example.com" in emails


# ── 2. a single-workspace customer is unchanged ─────────────────────────────

def test_an_ordinary_single_workspace_admin_still_passes(client, db_session):
    """THE REGRESSION THAT MATTERS. Most customers hold exactly one workspace
    and select nothing; they must behave exactly as before."""
    org = _org(db_session, "Only Customer", features=ALL_FEATURES)
    admin = _person(db_session, email="solo.admin@example.com",
                    role="org_admin", home=org)
    r = client.get("/admin/users", headers=_hdr(db_session, admin))
    assert r.status_code == 200, r.text


def test_an_ordinary_single_workspace_advisor_is_still_refused(client, db_session):
    org = _org(db_session, "Only Customer", features=ALL_FEATURES)
    advisor = _person(db_session, email="solo.advisor@example.com",
                      role="advisor", home=org)
    r = client.get("/admin/users", headers=_hdr(db_session, advisor))
    assert r.status_code == 403


# ── 3. entitlements are enforced, not merely registered ─────────────────────

ZERO_FEATURE_ROUTES = [
    ("/leads/", "leads"),
    ("/admin/users", "users"),
    ("/reports/overview", "reports"),
    ("/audit-log/", "audit_log"),
    ("/tier-definitions/", "tier_config"),
    ("/import-batches/", "imports"),
]


def test_a_zero_feature_customer_is_refused_every_gated_module(client, db_session):
    org = _org(db_session, "Zero Feature Co", features=[])
    admin = _person(db_session, email="zero.admin@example.com",
                    role="org_admin", home=org)
    for path, key in ZERO_FEATURE_ROUTES:
        r = client.get(path, headers=_hdr(db_session, admin))
        assert r.status_code in (402, 403, 404), "%s (%s) -> %s" % (path, key, r.status_code)


def test_enabling_one_feature_opens_exactly_that_one(client, db_session):
    org = _org(db_session, "One Feature Co", features=["leads"])
    admin = _person(db_session, email="one.admin@example.com",
                    role="org_admin", home=org)
    opened = client.get("/leads/", headers=_hdr(db_session, admin))
    assert opened.status_code == 200, opened.text
    still_shut = client.get("/reports/overview", headers=_hdr(db_session, admin))
    assert still_shut.status_code in (402, 403, 404)


def test_a_never_configured_customer_keeps_every_module(client, db_session):
    """NULL IS NOT AN EMPTY LIST. An organization nobody has configured is
    legacy-open; one configured with no modules is closed. Collapsing the two
    would either lock out every existing customer or enforce nothing."""
    org = _org(db_session, "Legacy Co", features=None)
    assert org.enabled_features is None
    admin = _person(db_session, email="legacy.admin@example.com",
                    role="org_admin", home=org)
    r = client.get("/leads/", headers=_hdr(db_session, admin))
    assert r.status_code == 200, r.text


def test_god_is_refused_by_no_feature_gate(client, db_session):
    """GOD IS ROOT AUTHORITY AND THIS CHANGE DOES NOT TOUCH IT."""
    org = _org(db_session, "Zero Feature Co", features=[])
    god = _person(db_session, email="god.here@example.com", role="god_admin",
                  home=org)
    for path, _ in ZERO_FEATURE_ROUTES:
        r = client.get(path, headers=_hdr(db_session, god))
        assert r.status_code not in (402,), "%s refused god: %s" % (path, r.status_code)


def test_the_public_marketing_intake_is_not_caught_by_the_leads_gate(
        client, db_session):
    """The `leads` gate is applied to the five AUTHENTICATED lead groups, not
    to the assembler, because the same prefix carries the unauthenticated
    marketing-site intake. Gating that would 403 every public submission."""
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/leads/demo-request" in paths
    r = client.post("/leads/demo-request", json={"first_name": "Dana"})
    # 503 (no destination configured in this database) proves it REACHED the
    # handler. A 401/403 would mean the entitlement gate swallowed it.
    assert r.status_code == 503, r.text


# ── 4. what the browser is told ─────────────────────────────────────────────

def test_branding_states_the_workspace_role_and_its_features(client, db_session,
                                                             two_customers):
    """The sidebar could not hide a module because `enabled_features` was
    never transported, and could not judge a role because only `users.role`
    was. Both are now on the one call the shell makes on load."""
    person, a, b = two_customers["person"], two_customers["a"], two_customers["b"]

    in_a = client.get("/branding/org", headers=_hdr(db_session, person, a)).json()
    in_b = client.get("/branding/org", headers=_hdr(db_session, person, b)).json()

    assert in_a["workspace_role"] == "org_admin"
    assert in_b["workspace_role"] == "advisor"
    assert in_a["organization_id"] == a.id
    assert in_b["organization_id"] == b.id
    assert in_b["enabled_features"] == []
    assert "leads" in in_a["enabled_features"]


def test_a_never_configured_customer_reports_null_features(client, db_session):
    org = _org(db_session, "Legacy Co", features=None)
    admin = _person(db_session, email="legacy2@example.com", role="org_admin",
                    home=org)
    body = client.get("/branding/org", headers=_hdr(db_session, admin)).json()
    assert body["enabled_features"] is None


# ── 5. the team a workspace shows is its own ────────────────────────────────

def test_the_user_list_is_the_active_workspaces_people(client, db_session,
                                                       two_customers):
    """ADVISOR SCOPING. This read the CALLER's home column, so every people
    picker built on it — campaign advisor, lead owner, reassignment — offered
    customer A's staff while the operator stood inside customer B."""
    person, a, b = two_customers["person"], two_customers["a"], two_customers["b"]
    a_only = _person(db_session, email="only.in.a@example.com", home=a)
    _member(db_session, a_only, a, "advisor")
    b_only = _person(db_session, email="only.in.b@example.com", home=b)
    _member(db_session, b_only, b, "advisor")

    b.enabled_features = json.dumps(ALL_FEATURES)
    m = (db_session.query(Membership)
         .filter(Membership.user_id == person.id,
                 Membership.scope_id == b.id).first())
    m.role = "org_admin"
    db_session.commit()
    from app.services import workspace_access
    workspace_access.invalidate_workspace_memberships(person)

    seen = {u["email"] for u in
            client.get("/admin/users", headers=_hdr(db_session, person, b)).json()}
    assert "only.in.b@example.com" in seen
    assert "only.in.a@example.com" not in seen, "another customer's staff leaked"


def test_somebody_seconded_into_a_workspace_appears_in_its_team(client, db_session,
                                                                two_customers):
    """The old query matched the HOMED column, so a person seconded in by
    membership was invisible to the workspace they actually work in."""
    person, b = two_customers["person"], two_customers["b"]
    b.enabled_features = json.dumps(ALL_FEATURES)
    m = (db_session.query(Membership)
         .filter(Membership.user_id == person.id,
                 Membership.scope_id == b.id).first())
    m.role = "org_admin"
    db_session.commit()
    from app.services import workspace_access
    workspace_access.invalidate_workspace_memberships(person)

    seen = {u["email"] for u in
            client.get("/admin/users", headers=_hdr(db_session, person, b)).json()}
    assert "two.workspaces@example.com" in seen
