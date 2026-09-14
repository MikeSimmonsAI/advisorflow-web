"""CAPABILITIES ARE A PROPERTY OF (PERSON, WORKSPACE) TOO.

`/settings/my-capabilities` is the endpoint the sidebar treats as
authoritative — it exists precisely so the UI stops deciding permissions from
`user.role`. It was still doing two things the rest of the platform had already
stopped doing:

  * resolving the organization from `users.organization_id`, the legacy column,
    while the feature gates beside it had moved to the selected workspace; and
  * testing eligibility against `users.role`, one value for a whole human.

So the one answer the sidebar trusts was computed about a different
organization, with a role that cannot be true of two customers at once.

WHY THESE ASSERT ON `Decision.stage` AND NOT ON THE FINAL LIST. A capability is
held only after BOTH delegation gates pass: God delegates it to the customer,
then the customer's administrator is granted it. An org_admin with no
delegation holds nothing, correctly — so "the list is empty" proves nothing
about the role check, which sits two gates earlier. The stage the decision
stops at is the observable that moves when this seam moves.
"""

import json
import uuid

import pytest

from app.models.models import Organization, User
from app.models.sales_models import Membership
from app.services import capabilities
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_access import SCOPE_CUSTOMER_ORG, WORKSPACE_HEADER

ALL_ON = ["leads", "campaigns", "sms", "voice", "email", "crm", "users",
          "reports", "imports", "cadences", "compliance", "availability",
          "tier_config", "branding_settings", "audit_log", "master_dashboard",
          "lead_cleanup", "ai_assist", "booking", "calendar", "case_files",
          "crm_connectors"]


def _probe():
    """A delegable capability that sits behind a feature, chosen from the
    registry rather than named here so a rename cannot silently skip these."""
    for key in capabilities.ALL_CAPABILITY_KEYS:
        cap = capabilities.CAPABILITIES[key]
        if cap.delegable and cap.requires_feature in ALL_ON:
            return key
    raise AssertionError("no delegable capability with a feature behind it")


PROBE = _probe()


def _org(db, name, features=ALL_ON, delegated=None):
    org = Organization(name=name, slug="o-" + uuid.uuid4().hex[:8], plan="enterprise",
                       is_active=True, enabled_features=json.dumps(features))
    if delegated is not None:
        org.delegated_capabilities = json.dumps(delegated)
    db.add(org)
    db.commit()
    return org


def _person(db, email, role="advisor", home=None):
    u = User(organization_id=(home.id if home else None), email=email,
             password_hash=hash_password("TestPass123!"), full_name="Person",
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _member(db, user, org, role):
    db.add(Membership(user_id=user.id, scope_type=SCOPE_CUSTOMER_ORG,
                      scope_id=org.id, role=role, is_active=True))
    db.commit()


def _stage(db, user, org, key=PROBE):
    """How far the decision got. 'role' means it was refused for being the
    wrong kind of person IN THIS ORGANIZATION."""
    return capabilities.resolve(db, user, org, key).stage


@pytest.fixture()
def two_customers(db_session):
    """Admin of A, ordinary user of B — the shape that keeps finding these."""
    a = _org(db_session, "Customer A")
    b = _org(db_session, "Customer B")
    person = _person(db_session, "caps.two@example.com", role="org_admin", home=a)
    _member(db_session, person, a, "org_admin")
    _member(db_session, person, b, "advisor")
    return {"a": a, "b": b, "person": person}


def _caps(client, db, user, org=None):
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if org is not None:
        h[WORKSPACE_HEADER] = org.id
    r = client.get("/settings/my-capabilities", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ── 1. the role seam ────────────────────────────────────────────────────────

def test_the_same_person_is_eligible_in_a_and_not_in_b(db_session, two_customers):
    """THE HEADLINE. One human, one capability, two organizations, two answers."""
    person, a, b = two_customers["person"], two_customers["a"], two_customers["b"]
    assert _stage(db_session, person, a) != "role", \
        "an administrator of A must clear the role gate in A"
    assert _stage(db_session, person, b) == "role", \
        "an ordinary user of B must be refused as the wrong role in B"


def test_the_user_row_is_not_what_decides(db_session, two_customers):
    """`users.role` still says org_admin and `users.organization_id` still
    points at A. Neither is what answers for B."""
    person = two_customers["person"]
    assert person.role == "org_admin"
    assert person.organization_id == two_customers["a"].id
    assert _stage(db_session, person, two_customers["b"]) == "role"


def test_promoting_the_membership_in_b_is_what_changes_the_answer(
        db_session, two_customers):
    person, b = two_customers["person"], two_customers["b"]
    assert _stage(db_session, person, b) == "role"

    m = (db_session.query(Membership)
         .filter(Membership.user_id == person.id,
                 Membership.scope_id == b.id).first())
    m.role = "org_admin"
    db_session.commit()
    from app.services import workspace_access
    workspace_access.invalidate_workspace_memberships(person)

    assert _stage(db_session, person, b) != "role", \
        "an administrator of B must clear the role gate in B"


def test_an_advisor_is_refused_at_the_role_gate(db_session):
    org = _org(db_session, "Advisor Co")
    advisor = _person(db_session, "caps.advisor@example.com", role="advisor", home=org)
    _member(db_session, advisor, org, "advisor")
    assert _stage(db_session, advisor, org) == "role"


def test_a_customer_with_no_membership_row_falls_back_to_the_column(db_session):
    """A customer that predates memberships keeps working: no row, fall back."""
    org = _org(db_session, "Legacy Co")
    admin = _person(db_session, "caps.legacy@example.com", role="org_admin", home=org)
    assert _stage(db_session, admin, org) != "role"


# ── 2. the workspace seam, through the endpoint the sidebar calls ───────────

def test_the_endpoint_answers_about_the_selected_workspace(client, db_session,
                                                           two_customers):
    person, a, b = two_customers["person"], two_customers["a"], two_customers["b"]
    assert _caps(client, db_session, person, a)["organization_id"] == a.id
    assert _caps(client, db_session, person, b)["organization_id"] == b.id


def test_the_endpoint_falls_back_to_the_column_with_no_header(client, db_session):
    org = _org(db_session, "One Workspace Co")
    admin = _person(db_session, "caps.single@example.com", role="org_admin", home=org)
    _member(db_session, admin, org, "org_admin")
    assert _caps(client, db_session, admin)["organization_id"] == org.id


def test_an_advisor_holds_nothing_through_the_endpoint(client, db_session):
    org = _org(db_session, "Advisor Co 2", delegated=[PROBE])
    advisor = _person(db_session, "caps.adv2@example.com", role="advisor", home=org)
    _member(db_session, advisor, org, "advisor")
    assert _caps(client, db_session, advisor, org)["capabilities"] == []


# ── 3. feature before role, which is the order `resolve` documents ──────────

def test_a_disabled_feature_is_reported_before_the_role(db_session):
    """"Your organization does not have voice" is a truer answer than "you are
    not an administrator" for someone who is in fact an administrator."""
    cap = capabilities.CAPABILITIES[PROBE]
    org = _org(db_session, "No Feature Co",
               features=[k for k in ALL_ON if k != cap.requires_feature],
               delegated=[PROBE])
    advisor = _person(db_session, "caps.nofeat@example.com", role="advisor", home=org)
    _member(db_session, advisor, org, "advisor")
    assert _stage(db_session, advisor, org) == "feature"
