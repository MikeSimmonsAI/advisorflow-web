"""ONE OPERATOR, MANY CLEANING COMPANIES, AND NO CUSTOMER SEEING ANOTHER.

THE BUSINESS THIS DESCRIBES. A commercial-cleaning growth operator runs the
prospecting for a portfolio of cleaning companies. Each company logs into its
own account and sees its own prospects, its own outreach, its own follow-up
queue and its own booked walkthroughs. The operator sees all of them, one at a
time, by standing inside whichever workspace they are working on.

THE FAILURE THAT WOULD MAKE THAT A PRODUCT NOBODY CAN SELL is not an error
message. It is one client seeing a line of another client's pipeline — the
operator's whole proposition is "your work, privately, where you can see it",
and it survives exactly one breach.

WHAT IS DELIBERATELY NOT HERE. No god-level portfolio table, no per-operator
roll-up of "his" customers, no new scoping concept. The portfolio IS the
memberships that already exist, and the isolation IS `lead_scope`. That is the
claim under test: that going from five cleaning companies to two hundred is
rows, not architecture.
"""
import json
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.models import BookingLink, Lead, Organization, Platform, User
from app.models.sales_models import Membership
from app.services import industry_templates
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_access import SCOPE_CUSTOMER_ORG, WORKSPACE_HEADER

CLIENT_FEATURES = ["leads", "reports", "users", "master_dashboard",
                   "branding_settings"]


def _cleaning_company(db, name, platform, *, industry="cleaning"):
    """A customer of this operator. No configuration of its own, on purpose:
    everything its workspace shows comes from its industry."""
    org = Organization(name=name, slug="c-" + uuid.uuid4().hex[:8],
                       plan="standard", industry=industry, is_active=True,
                       platform_id=platform.id,
                       enabled_features=json.dumps(CLIENT_FEATURES))
    db.add(org)
    db.commit()
    return org


def _person(db, email, *, role="advisor", home=None):
    u = User(organization_id=(home.id if home else None), email=email,
             password_hash=hash_password("TestPass123!"),
             full_name="Test Person", role=role, is_active=True,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _member(db, user, org, role="org_admin"):
    db.add(Membership(user_id=user.id, scope_type=SCOPE_CUSTOMER_ORG,
                      scope_id=org.id, role=role, is_active=True))
    db.commit()


def _hdr(db, user, org=None):
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if org is not None:
        h[WORKSPACE_HEADER] = org.id
    return h


def _prospect(db, org, company, *, owner=None, tier="new_lead"):
    lead = Lead(organization_id=org.id, first_name=company, last_name="Contact",
                phone="1214555" + uuid.uuid4().hex[:4].translate(
                    str.maketrans("abcdef", "012345")),
                tier=tier, status="new",
                assigned_to_id=(owner.id if owner else None),
                created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(lead)
    db.commit()
    return lead


def _walkthrough(db, lead, user, status="booked", when=None):
    link = BookingLink(lead_id=lead.id, user_id=user.id, status=status,
                       booked_time=when or (datetime.utcnow() + timedelta(days=2)),
                       appt_label="Walkthrough")
    db.add(link)
    db.commit()
    return link


@pytest.fixture()
def portfolio(db_session):
    """Two cleaning companies, a client user in each, and the operator.

    The operator is deliberately NOT a god admin and NOT a super admin. The
    portfolio has to work through ordinary memberships, because that is the
    claim: no elevated role, no new table.
    """
    platform = Platform(name="EvoSys Pro", slug="ev-" + uuid.uuid4().hex[:6])
    db_session.add(platform)
    db_session.commit()

    north = _cleaning_company(db_session, "Northbank Facility Care", platform)
    ridge = _cleaning_company(db_session, "Ridgeline Janitorial", platform)

    north_staff = _person(db_session, "staff@northbank.example",
                          role="org_admin", home=north)
    _member(db_session, north_staff, north, "org_admin")

    ridge_staff = _person(db_session, "staff@ridgeline.example",
                          role="org_admin", home=ridge)
    _member(db_session, ridge_staff, ridge, "org_admin")

    operator = _person(db_session, "operator@portfolio.example",
                       role="advisor", home=north)
    _member(db_session, operator, north, "org_admin")
    _member(db_session, operator, ridge, "org_admin")

    _prospect(db_session, north, "Northgate Office Park", owner=north_staff)
    _prospect(db_session, north, "Trinity Daycare", owner=north_staff)
    _prospect(db_session, ridge, "Ironwood Medical Plaza", owner=ridge_staff)

    return {"platform": platform, "north": north, "ridge": ridge,
            "north_staff": north_staff, "ridge_staff": ridge_staff,
            "operator": operator}


def _names(body):
    return sorted(i["values"]["name"] for i in body["items"])


# ── a client sees its own account and nothing else ──────────────────────────

def test_a_client_sees_only_its_own_prospects(portfolio, db_session, client):
    body = client.get("/workspace-views/prospects",
                      headers=_hdr(db_session, portfolio["north_staff"])).json()
    assert body["total"] == 2
    assert _names(body) == ["Northgate Office Park Contact", "Trinity Daycare Contact"]

    other = client.get("/workspace-views/prospects",
                       headers=_hdr(db_session, portfolio["ridge_staff"])).json()
    assert other["total"] == 1
    assert _names(other) == ["Ironwood Medical Plaza Contact"]


def test_a_client_sees_only_its_own_walkthroughs(portfolio, db_session, client):
    _walkthrough(db_session,
                 _prospect(db_session, portfolio["north"], "Larkspur Retail"),
                 portfolio["north_staff"])
    _walkthrough(db_session,
                 _prospect(db_session, portfolio["ridge"], "Cedar Point Clinic"),
                 portfolio["ridge_staff"])

    north = client.get("/workspace-views/walkthroughs",
                       headers=_hdr(db_session, portfolio["north_staff"])).json()
    assert _names(north) == ["Larkspur Retail Contact"]

    ridge = client.get("/workspace-views/walkthroughs",
                       headers=_hdr(db_session, portfolio["ridge_staff"])).json()
    assert _names(ridge) == ["Cedar Point Clinic Contact"]


def test_a_client_sees_only_its_own_leads_on_the_lead_page(portfolio, db_session, client):
    body = client.get("/leads/?page=1&page_size=50",
                      headers=_hdr(db_session, portfolio["ridge_staff"])).json()
    orgs = {item.get("organization_id") for item in body.get("items", [])}
    assert orgs <= {portfolio["ridge"].id}, \
        "a client's lead list carried another customer's records"


def test_a_client_cannot_reach_another_customer_by_asking_for_it(
        portfolio, db_session, client):
    """THE ATTACK, NOT THE ACCIDENT. The workspace is chosen by a header the
    browser sends, so the header is untrusted input. Naming a customer this
    person has no membership in must not widen anything."""
    response = client.get(
        "/workspace-views/prospects",
        headers=_hdr(db_session, portfolio["north_staff"], portfolio["ridge"]))
    if response.status_code == 200:
        assert _names(response.json()) == ["Northgate Office Park Contact",
                                           "Trinity Daycare Contact"], \
            "a forged workspace header moved this client into another customer"
    else:
        assert response.status_code in (400, 403, 404)


def test_a_clients_activity_feed_is_its_own(portfolio, db_session, client):
    """VA Activity is the existing activity feed, scoped by the same rule. A
    client reading the operator's work on another client's account is the same
    breach as reading their prospects."""
    response = client.get("/activity/today",
                          headers=_hdr(db_session, portfolio["north_staff"]))
    assert response.status_code == 200
    body = response.json()
    assert body["organization_id"] == portfolio["north"].id


# ── the operator's portfolio is memberships, not a new concept ──────────────

def test_the_operator_reaches_each_customer_one_at_a_time(
        portfolio, db_session, client):
    """Standing inside a customer shows that customer. Standing inside the
    next one shows the next one. There is no view that shows both, because a
    combined list is exactly the thing a client must never be one bug away
    from."""
    north = client.get("/workspace-views/prospects",
                       headers=_hdr(db_session, portfolio["operator"],
                                    portfolio["north"])).json()
    ridge = client.get("/workspace-views/prospects",
                       headers=_hdr(db_session, portfolio["operator"],
                                    portfolio["ridge"])).json()

    assert _names(north) == ["Northgate Office Park Contact",
                             "Trinity Daycare Contact"]
    assert _names(ridge) == ["Ironwood Medical Plaza Contact"]
    assert set(_names(north)) & set(_names(ridge)) == set()


def test_the_operator_needs_no_elevated_role(portfolio, db_session):
    """`users.role` is one value for a whole human and cannot be true of two
    workspaces at once. The portfolio works because the MEMBERSHIPS say so —
    which is why a god or super admin is not required, and why nobody had to
    be given one."""
    operator = portfolio["operator"]
    assert operator.role not in ("god_admin", "super_admin")
    scopes = {m.scope_id for m in db_session.query(Membership).filter(
        Membership.user_id == operator.id,
        Membership.scope_type == SCOPE_CUSTOMER_ORG,
        Membership.is_active == True).all()}  # noqa: E712
    assert scopes == {portfolio["north"].id, portfolio["ridge"].id}


def test_losing_a_membership_closes_that_customer(portfolio, db_session, client):
    """The portfolio shrinks the same way it grows. If access outlived the
    membership, offboarding a client would be a manual cleanup nobody
    remembers to do."""
    operator, ridge = portfolio["operator"], portfolio["ridge"]
    membership = db_session.query(Membership).filter(
        Membership.user_id == operator.id,
        Membership.scope_id == ridge.id).first()
    membership.is_active = False
    db_session.commit()

    response = client.get("/workspace-views/prospects",
                          headers=_hdr(db_session, operator, ridge))
    if response.status_code == 200:
        assert "Ironwood Medical Plaza Contact" not in _names(response.json()), \
            "a revoked membership still reads that customer's prospects"
    else:
        assert response.status_code in (400, 403, 404)


# ── the two-hundredth customer costs what the first did ─────────────────────

def test_a_new_cleaning_company_needs_no_configuration_of_its_own(
        portfolio, db_session, client):
    """THE SCALING CLAIM, STATED AS A TEST.

    A company created with nothing but a name and an industry opens a
    workspace with the right screens, in the right vocabulary, isolated from
    every other customer. No file, no column, no code — which is what "five to
    two hundred" has to mean if it is to mean anything.
    """
    newcomer = _cleaning_company(db_session, "Summit Building Services",
                                 portfolio["platform"])
    assert newcomer.workspace_views is None, \
        "a new customer should inherit its screens, not be handed a copy"

    staff = _person(db_session, "staff@summit.example", role="org_admin",
                    home=newcomer)
    _member(db_session, staff, newcomer, "org_admin")
    _prospect(db_session, newcomer, "Brookfield Logistics")

    listing = client.get("/workspace-views",
                         headers=_hdr(db_session, staff)).json()
    assert [v["key"] for v in listing["views"]] == [
        "prospects", "follow-up", "walkthroughs"]

    body = client.get("/workspace-views/prospects",
                      headers=_hdr(db_session, staff)).json()
    assert _names(body) == ["Brookfield Logistics Contact"]

    # And the customers who were already there are unchanged by its arrival.
    north = client.get("/workspace-views/prospects",
                       headers=_hdr(db_session, portfolio["north_staff"])).json()
    assert north["total"] == 2


def test_the_vertical_is_the_industry_not_the_customer(portfolio, db_session):
    """Two different cleaning companies, no shared configuration, identical
    screens — because the screens belong to the trade."""
    a = industry_templates.workspace_views(portfolio["north"].industry)
    b = industry_templates.workspace_views(portfolio["ridge"].industry)
    assert [v["key"] for v in a] == [v["key"] for v in b]
    assert [v["label"] for v in a] == ["Prospects", "Follow-Up", "Walkthroughs"]
