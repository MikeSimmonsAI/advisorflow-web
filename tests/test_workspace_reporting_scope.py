"""A WORKSPACE REPORTS ON ITS OWN ORGANIZATION, WHOEVER IS READING IT.

THE DEFECT THIS FILE NAMES, FOUND ON A LIVE CLIENT'S REPORTS PAGE. A newly
installed commercial-cleaning customer with zero leads opened Reports inside
its own workspace and read, under a banner saying "God View — All
Organizations": 40 conversations in pipeline, 117 AI auto-responses and 3
flagged for review. None of it was theirs. Every one of those numbers belonged
to every other customer on the platform, added together.

THE CAUSE WAS ONE QUESTION ASKED WRONG, IN SEVEN PLACES:

    is_god = current_user.role == "god_admin"

Role is not scope. A platform owner has that role permanently; where they are
STANDING is a separate fact, carried on the request. `deps.get_current_user`
already models both — an `X-Org-Override` sets `organization_id` to the
customer being entered, and only the absence of one sets `_god_all_orgs`. The
lead queries and `deps.get_platform_org_ids` read the second fact and narrowed
correctly all along. The reporting endpoints read the first and did not narrow
at all, so entering a customer changed every screen in the product except the
one that aggregates.

`lead_scope.god_sees_all_orgs` is now the single answer to "is this reader the
neutral owner, standing outside every customer", and these tests hold the two
halves of it apart:

  * inside a customer, a god admin reads that customer — no banner, no other
    organization's rows, no platform-wide counts;
  * outside every customer, a god admin still reads the whole platform,
    because removing that would be deleting the Command Center's reason to
    exist rather than fixing a leak.
"""
import json
import uuid
from datetime import datetime

import pytest

from app.models.models import CRMContact, Lead, Organization, Platform, User
from app.models.sales_models import Membership
from app.services import lead_scope
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_access import SCOPE_CUSTOMER_ORG

ORG_OVERRIDE_HEADER = "X-Org-Override"

# Everything the client-facing reporting surface is gated on. A workspace
# missing one of these would 402 and the test would pass for the wrong reason.
CLIENT_FEATURES = ["leads", "reports", "users", "master_dashboard",
                   "branding_settings"]


def _org(db, name, platform, industry="cleaning"):
    org = Organization(name=name, slug="r-" + uuid.uuid4().hex[:8],
                       plan="standard", industry=industry, is_active=True,
                       platform_id=platform.id,
                       enabled_features=json.dumps(CLIENT_FEATURES))
    db.add(org)
    db.commit()
    return org


def _user(db, email, *, role, home=None):
    u = User(organization_id=(home.id if home else None), email=email,
             password_hash=hash_password("TestPass123!"),
             full_name="Test Person", role=role, is_active=True,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _lead(db, org, company):
    lead = Lead(organization_id=org.id, first_name=company, last_name="Contact",
                phone="1214777" + uuid.uuid4().hex[:4].translate(
                    str.maketrans("abcdef", "012345")),
                tier="new_lead", status="new",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(lead)
    db.commit()
    return lead


def _contact(db, org, company, stage="contacted"):
    """A CRM contact, which is what `/reports/crm-summary` counts.

    Deliberately a different table from `Lead`: the CRM summary reports on
    `crm_contacts`, and seeding leads instead would make this file pass while
    testing nothing about the endpoint it names.
    """
    c = CRMContact(organization_id=org.id, first_name=company,
                   last_name="Contact", stage=stage, is_archived=False)
    db.add(c)
    db.commit()
    return c


def _auth(db, user, *, inside=None):
    """Headers for this reader, optionally standing inside a customer.

    `X-Org-Override` is the real mechanism — the one the God-Mode "enter
    customer" button sets — rather than a test-only shortcut, so what these
    tests exercise is the path production takes.
    """
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if inside is not None:
        h[ORG_OVERRIDE_HEADER] = inside.id
    return h


@pytest.fixture()
def platform_with_two_customers(db_session):
    platform = Platform(name="EvoSys Pro", slug="ev-" + uuid.uuid4().hex[:6])
    db_session.add(platform)
    db_session.commit()

    cleaning = _org(db_session, "Northbank Facility Care", platform)
    other = _org(db_session, "Restland Memorial", platform, industry="funeral")

    owner = _user(db_session, "owner@evosys.example", role="god_admin")
    staff = _user(db_session, "staff@northbank.example",
                  role="org_admin", home=cleaning)
    db_session.add(Membership(user_id=staff.id, scope_type=SCOPE_CUSTOMER_ORG,
                              scope_id=cleaning.id, role="org_admin",
                              is_active=True))
    db_session.commit()

    _lead(db_session, cleaning, "Northgate Office Park")
    _contact(db_session, cleaning, "Northgate Office Park")
    for name in ("Hillcrest Chapel", "Meadowbrook Home", "Riverside Parlor"):
        _lead(db_session, other, name)
        _contact(db_session, other, name, stage="at_need")

    return {"platform": platform, "cleaning": cleaning, "other": other,
            "owner": owner, "staff": staff}


# ── the predicate itself ────────────────────────────────────────────────────

def test_the_predicate_separates_the_role_from_the_standing_place(db_session,
                                                                  platform_with_two_customers):
    """Both flags, independently, because production sets them independently.

    `deps.get_current_user` expunges the owner's row and then either points
    `organization_id` at the customer being entered OR sets `_god_all_orgs`
    and nulls the column. Reading one and not the other is how a reader who
    is plainly inside a customer still answers "yes, show me everything".
    """
    owner = platform_with_two_customers["owner"]
    staff = platform_with_two_customers["staff"]
    cleaning = platform_with_two_customers["cleaning"]

    # The neutral owner: no customer selected.
    owner._god_all_orgs = True
    owner.organization_id = None
    assert lead_scope.god_sees_all_orgs(owner) is True

    # The same human, standing inside a customer.
    owner._god_all_orgs = False
    owner.organization_id = cleaning.id
    assert lead_scope.god_sees_all_orgs(owner) is False

    # Nobody else is ever the neutral owner, whatever flags they carry.
    staff._god_all_orgs = True
    assert lead_scope.god_sees_all_orgs(staff) is False


# ── inside a customer, a god admin reads that customer ──────────────────────

REPORTING_ENDPOINTS = (
    "/admin/dashboard",
    "/admin/dashboard/metrics",
    "/admin/dashboard/funnel",
    "/reports/crm-summary",
)


@pytest.mark.parametrize("endpoint", REPORTING_ENDPOINTS)
def test_no_reporting_endpoint_claims_a_platform_view_inside_a_workspace(
        endpoint, platform_with_two_customers, db_session, client):
    """THE BANNER, WHICH IS A CLAIM AND WAS A FALSE ONE.

    `is_god_view` is what the Reports page draws "God View — All
    Organizations" from. Inside a customer's workspace it has to be false,
    because the numbers underneath it are — and a label that contradicts its
    own figures is worse than either being wrong alone.
    """
    response = client.get(endpoint, headers=_auth(
        db_session, platform_with_two_customers["owner"],
        inside=platform_with_two_customers["cleaning"]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("is_god_view") is False, (
        "%s claims a platform-wide view from inside one customer's workspace"
        % endpoint)
    assert body.get("organization_id") != "all", (
        "%s reports its scope as every organization from inside one" % endpoint)


def test_the_crm_summary_counts_only_the_workspace_it_is_read_from(
        platform_with_two_customers, db_session, client):
    """One lead in this account, three in somebody else's. A customer with one
    prospect must not read four."""
    body = client.get("/reports/crm-summary", headers=_auth(
        db_session, platform_with_two_customers["owner"],
        inside=platform_with_two_customers["cleaning"])).json()
    assert body["total_contacts"] == 1, \
        "the CRM summary counted another customer's contacts"


def test_the_pipeline_stats_are_this_workspaces_own(
        platform_with_two_customers, db_session, client):
    """The endpoint behind "Total in pipeline", "AI auto-responses" and
    "Flagged for review" — the three figures a zero-lead client read as 40,
    117 and 3."""
    body = client.get("/pipeline/stats", headers=_auth(
        db_session, platform_with_two_customers["owner"],
        inside=platform_with_two_customers["cleaning"])).json()
    assert not body.get("is_god_view"), \
        "/pipeline/stats aggregated the platform inside a customer workspace"
    assert body["total_in_pipeline"] == 0
    assert body["ai_auto_sent"] == 0
    assert body["flagged_count"] == 0


def test_a_customers_own_admin_never_sees_a_platform_view(
        platform_with_two_customers, db_session, client):
    """The same assertions for the person the product is actually for. Their
    role was never `god_admin`, so this passed before — it is here so that a
    future "simplification" of the predicate has to keep passing it."""
    for endpoint in REPORTING_ENDPOINTS:
        body = client.get(endpoint, headers=_auth(
            db_session, platform_with_two_customers["staff"])).json()
        assert body.get("is_god_view") is False, endpoint


# ── outside every customer, the owner still reads the platform ──────────────

def test_the_neutral_owner_still_gets_the_platform_wide_view(
        platform_with_two_customers, db_session, client):
    """THIS IS NOT A LEAK, IT IS THE COMMAND CENTER.

    The correction was to stop role alone from widening a CUSTOMER'S screen.
    An owner who has deliberately left every workspace is asking a different
    question, and the answer to that one is the whole platform. Losing it
    would be removing an administrator capability under cover of a privacy
    fix, which the brief for this work ruled out in as many words.
    """
    owner = platform_with_two_customers["owner"]
    body = client.get("/reports/crm-summary", headers=_auth(db_session, owner)).json()
    assert body["is_god_view"] is True, \
        "the platform owner lost the platform-wide view by leaving a workspace"
    assert body["total_contacts"] == 4, \
        "the platform view no longer spans every customer"

    pipeline = client.get("/pipeline/stats", headers=_auth(db_session, owner)).json()
    assert pipeline.get("is_god_view") is True


def test_entering_a_customer_narrows_and_leaving_it_widens_again(
        platform_with_two_customers, db_session, client):
    """The same reader, the same session, one header apart.

    Stated as a round trip because the property that matters is not either
    number on its own — it is that ENTERING is what narrows and LEAVING is
    what widens, rather than the reader's role deciding once and for all.
    """
    owner = platform_with_two_customers["owner"]
    cleaning = platform_with_two_customers["cleaning"]
    other = platform_with_two_customers["other"]

    def contacts(**kw):
        return client.get("/reports/crm-summary",
                          headers=_auth(db_session, owner, **kw)).json()["total_contacts"]

    assert contacts() == 4
    assert contacts(inside=cleaning) == 1
    assert contacts(inside=other) == 3
    assert contacts() == 4


# ── the shape of the answer, which took the application down ────────────────

def test_the_crm_summary_sends_stage_counts_as_a_list(
        platform_with_two_customers, db_session, client):
    """THE WHITE SCREEN, AS A CONTRACT.

    `stage_counts` was a `{stage: count}` object and the Reports page read it
    as an array: `(stage_counts || []).slice(0, 4)`. An object is truthy, so
    the `|| []` guard never fired, `.slice` is not a function on it, and the
    TypeError escaped render — which in React 18 unmounts the entire tree,
    navigation rail included. Every subsequent client-side navigation was a
    blank page until a full reload.

    The page now normalises whatever arrives, so this is belt and braces. It
    is worth asserting anyway: a list is the shape that carries an ORDER, and
    a stage breakdown without an order is a bar chart in arbitrary sequence.
    """
    body = client.get("/reports/crm-summary", headers=_auth(
        db_session, platform_with_two_customers["owner"],
        inside=platform_with_two_customers["cleaning"])).json()
    assert isinstance(body["stage_counts"], list), \
        "stage_counts is not a list — the Reports page slices it"
    for row in body["stage_counts"]:
        assert set(row) >= {"stage", "count"}, row
