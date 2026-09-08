"""
EXECUTIVE PORTFOLIO BOUNDARY — a role is not a portfolio.

═══════════════════════════════════════════════════════════════════════════
THE DEFECT THESE TESTS WERE WRITTEN AGAINST
═══════════════════════════════════════════════════════════════════════════

Executive visibility was BRAND-WIDE. `require_brand_executive` resolved a
platform-scoped membership into a Platform, and every executive surface then
ran `Organization.platform_id == platform_id`. Holding an executive grant on a
brand therefore exposed EVERY organization on that brand, and the grant record
had no organization dimension at all — "assign this person Restland only" was
not merely unimplemented, it was inexpressible.

THE CASE THAT MATTERS MOST IS THE SAME-BRAND ONE. Cross-brand isolation was
already correct and already tested; two executives inside ONE white-label brand
seeing each other's entire portfolio was the hole. Every test below that names
Alpha and Beta is that case.

═══════════════════════════════════════════════════════════════════════════
THE SHAPE, HELD CONSTANT ACROSS EVERY TEST
═══════════════════════════════════════════════════════════════════════════

    ONE BRAND, three organizations, three executives:

        Executive A  →  Org 1
        Executive B  →  Org 2, Org 3
        Executive C  →  Org 1, Org 2          (the "Mike" shape)

    A must see exactly Org 1.
    B must see exactly Org 2 and Org 3.
    C must see exactly Org 1 and Org 2.

    And nobody reaches anything else — not through the portfolio, not through
    revenue, not through the drill-down, not by pasting an id, not through a
    stale customer override, not through a legacy endpoint.

WHY EVERY TEST GOES THROUGH THE HTTP CLIENT. A boundary that only holds when
the caller uses the frontend is not a boundary. Frontend filtering is never
security here; these call the API directly with a real token, which is what an
attacker would do.
"""

import itertools
from datetime import datetime, timedelta

import pytest

from app.models.models import Lead, Organization, Platform, User
from app.models.sales_models import (ROLE_BRAND_EXECUTIVE, SCOPE_CUSTOMER_ORG,
                                     SCOPE_PLATFORM, Membership)
from app.services import executive_authority as exec_auth
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(7000)


# ═════════════════════════════════════════════════════════════════════════════
# fixtures
# ═════════════════════════════════════════════════════════════════════════════

def _platform(db, name):
    p = Platform(name=name, slug="%s-%d" % (name.lower(), next(_SEQ)))
    db.add(p); db.commit()
    return p


def _org(db, platform, name):
    o = Organization(name=name, slug="o-%d" % next(_SEQ),
                     platform_id=platform.id, plan="starter", is_active=True,
                     created_at=datetime.utcnow() - timedelta(days=120))
    db.add(o); db.commit()
    return o


def _executive(db, platform, name, orgs):
    """A brand grant PLUS explicit organization assignments.

    Both rows are required and they answer different questions: the
    platform-scoped grant says which BRAND this person may enter, and each
    org-scoped row says which CUSTOMER inside it they may see. Creating only
    the first is precisely the state that used to mean "everything".
    """
    u = User(organization_id=None, email="ex%d@brand.test" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role="advisor",
             must_change_password=False)
    db.add(u); db.commit()
    db.add(Membership(user_id=u.id, scope_type=SCOPE_PLATFORM,
                      scope_id=platform.id, role=ROLE_BRAND_EXECUTIVE,
                      is_active=True))
    db.commit()
    for o in orgs:
        db.add(Membership(user_id=u.id, scope_type=SCOPE_CUSTOMER_ORG,
                          scope_id=o.id, role=ROLE_BRAND_EXECUTIVE,
                          is_active=True))
    db.commit()
    return u


def _owner(db):
    u = User(organization_id=None, email="owner%d@brand.test" % next(_SEQ),
             password_hash=hash_password("x"), full_name="Owner",
             role="god_admin", must_change_password=False)
    db.add(u); db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


@pytest.fixture()
def brand(db_session):
    """ONE brand. Three organizations. Three executives with different
    portfolios. This is the whole point: same brand, different scope."""
    p = _platform(db_session, "Evo")
    o1 = _org(db_session, p, "Org One")
    o2 = _org(db_session, p, "Org Two")
    o3 = _org(db_session, p, "Org Three")
    # A little real data so the surfaces have something to report.
    for org in (o1, o2, o3):
        db_session.add(Lead(organization_id=org.id, first_name="L",
                            last_name="#%d" % next(_SEQ),
                            email="l%d@x.com" % next(_SEQ), status="new"))
    db_session.commit()
    return {
        "platform": p, "o1": o1, "o2": o2, "o3": o3,
        "a": _executive(db_session, p, "Executive A", [o1]),
        "b": _executive(db_session, p, "Executive B", [o2, o3]),
        "c": _executive(db_session, p, "Executive C", [o1, o2]),
        # Granted the brand and assigned NOTHING — the half-configured state.
        "unassigned": _executive(db_session, p, "Executive D", []),
    }


def _names(client, db, user, url="/executive/portfolio/health"):
    r = client.get(url, headers=_h(db, user))
    assert r.status_code == 200, "%s -> %s" % (url, r.status_code)
    return {o["name"] for o in r.json()["organizations"]}


# ═════════════════════════════════════════════════════════════════════════════
# 1. EACH EXECUTIVE SEES EXACTLY THEIR OWN PORTFOLIO
# ═════════════════════════════════════════════════════════════════════════════

def test_executive_a_sees_only_the_one_organization_assigned(
        client, db_session, brand):
    assert _names(client, db_session, brand["a"]) == {"Org One"}


def test_executive_b_sees_exactly_their_two_organizations(
        client, db_session, brand):
    assert _names(client, db_session, brand["b"]) == {"Org Two", "Org Three"}


def test_executive_c_sees_exactly_their_two_organizations(
        client, db_session, brand):
    assert _names(client, db_session, brand["c"]) == {"Org One", "Org Two"}


def test_two_executives_in_the_same_brand_do_not_see_each_others_customers(
        client, db_session, brand):
    """THE DEFECT, STATED AS A TEST.

    A and B hold executive grants on the SAME white-label brand. Before the
    portfolio authority existed, both saw all three organizations, because the
    only filter anywhere was the brand they had in common.
    """
    a = _names(client, db_session, brand["a"])
    b = _names(client, db_session, brand["b"])
    assert a == {"Org One"}
    assert b == {"Org Two", "Org Three"}
    assert a.isdisjoint(b), "same-brand executives are sharing a portfolio"


def test_a_brand_grant_with_no_assignments_sees_nothing(
        client, db_session, brand):
    """A ROLE IS NOT A PORTFOLIO.

    This is the state every executive was in before this pass: granted the
    brand, assigned nothing. The old code read that as "everything". It now
    reads as what it is — an executive who has been half set up — and the
    response says which, so the screen can explain itself rather than looking
    broken.
    """
    r = client.get("/executive/portfolio/health",
                   headers=_h(db_session, brand["unassigned"]))
    assert r.status_code == 200
    body = r.json()
    assert body["organizations"] == []
    assert body["total"] == 0
    assert body["portfolio_source"] == exec_auth.SOURCE_NONE
    assert body["is_owner_view"] is False


def test_the_command_centre_totals_are_the_executives_own_portfolio(
        client, db_session, brand):
    """A headline computed over a wider set than the list behind it would be a
    number the executive is not entitled to."""
    for who, expected in (("a", 1), ("b", 2), ("c", 2), ("unassigned", 0)):
        body = client.get("/executive/portfolio",
                          headers=_h(db_session, brand[who])).json()
        assert body["summary"]["organizations"] == expected, who
        rows = client.get("/executive/portfolio/health",
                          headers=_h(db_session, brand[who])).json()
        assert len(rows["organizations"]) == expected, who


# ═════════════════════════════════════════════════════════════════════════════
# 2. NOBODY REACHES ANYTHING ELSE, ON ANY SURFACE
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("holder,forbidden", [("a", "o2"), ("a", "o3"),
                                              ("b", "o1")])
def test_a_pasted_organization_id_from_another_portfolio_is_refused(
        client, db_session, brand, holder, forbidden):
    """THE ATTACK IS TYPING AN ID, on every route that takes one.

    Same brand, real organization, valid session — and every one of these
    endpoints must answer as though the organization does not exist.
    """
    head = _h(db_session, brand[holder])
    org_id = brand[forbidden].id
    for url in (
        "/executive/organizations/%s/performance" % org_id,
        "/executive/organizations/%s" % org_id,
        "/executive/organizations/%s/observe/overview" % org_id,
    ):
        r = client.get(url, headers=head)
        assert r.status_code == 404, "%s leaked to %s (%s)" % (url, holder,
                                                               r.status_code)


def test_an_unauthorized_id_is_indistinguishable_from_a_nonexistent_one(
        client, db_session, brand):
    """A 403 WOULD CONFIRM THE ORGANIZATION IS REAL, which is exactly what
    somebody probing ids is trying to learn. Status AND body must match."""
    head = _h(db_session, brand["a"])
    real_but_forbidden = client.get(
        "/executive/organizations/%s/performance" % brand["o2"].id, headers=head)
    imaginary = client.get(
        "/executive/organizations/org-does-not-exist-at-all/performance",
        headers=head)
    assert real_but_forbidden.status_code == imaginary.status_code == 404
    assert real_but_forbidden.json() == imaginary.json()


def test_the_legacy_organizations_endpoint_respects_the_portfolio(
        client, db_session, brand):
    """LOCKING THE COMMAND CENTER AND LEAVING THIS OPEN WOULD BE NO FIX.

    `/executive/organizations` predates the portfolio work and would still have
    enumerated every customer on the brand.
    """
    for who, expected in (("a", {"Org One"}),
                          ("b", {"Org Two", "Org Three"}),
                          ("c", {"Org One", "Org Two"})):
        body = client.get("/executive/organizations",
                          headers=_h(db_session, brand[who])).json()
        assert {o["name"] for o in body["organizations"]} == expected, who
        assert body["total"] == len(expected), who


def test_the_legacy_customer_health_endpoint_respects_the_portfolio(
        client, db_session, brand):
    body = client.get("/executive/customer-health",
                      headers=_h(db_session, brand["a"])).json()
    assert {o["name"] for o in body["organizations"]} == {"Org One"}
    assert body["summary"]["total"] == 1


def test_the_legacy_command_center_counts_only_the_portfolio(
        client, db_session, brand):
    """`active_customer_orgs` used to be every organization on the brand — a
    number the executive was not entitled to and could not open."""
    for who, expected in (("a", 1), ("b", 2), ("c", 2), ("unassigned", 0)):
        body = client.get("/executive/command-center",
                          headers=_h(db_session, brand[who])).json()
        assert body["active_customer_orgs"] == expected, who


def test_revenue_is_computed_over_the_executives_own_portfolio(
        client, db_session, brand):
    """Revenue reads the same endpoint the portfolio does, so it inherits the
    same boundary — asserted rather than assumed, because a second revenue
    query is exactly how a surface drifts out of scope."""
    for who, expected in (("a", {"Org One"}),
                          ("b", {"Org Two", "Org Three"})):
        body = client.get("/executive/portfolio/health?filter=all",
                          headers=_h(db_session, brand[who])).json()
        assert {o["name"] for o in body["organizations"]} == expected, who


def test_the_organization_switcher_lists_only_the_authorized_portfolio(
        client, db_session, brand):
    """The picker is built from ids the server sent. If it listed the brand,
    it would hand somebody the ids of customers they cannot open — an
    enumeration leak dressed as a convenience."""
    body = client.get(
        "/executive/organizations/%s/performance" % brand["o1"].id,
        headers=_h(db_session, brand["a"])).json()
    assert {p["id"] for p in body["portfolio"]} == {brand["o1"].id}

    body_c = client.get(
        "/executive/organizations/%s/performance" % brand["o1"].id,
        headers=_h(db_session, brand["c"])).json()
    assert {p["id"] for p in body_c["portfolio"]} == {brand["o1"].id,
                                                     brand["o2"].id}


def test_no_executive_filter_can_widen_the_portfolio(client, db_session, brand):
    """Every Portfolio Health filter is applied to an already-scoped set."""
    head = _h(db_session, brand["a"])
    base = client.get("/executive/portfolio/health", headers=head).json()
    for f in base["filters"]:
        got = client.get("/executive/portfolio/health?filter=" + f["key"],
                         headers=head).json()
        assert {o["name"] for o in got["organizations"]} <= {"Org One"}, f["key"]


# ═════════════════════════════════════════════════════════════════════════════
# 3. NO HEADER, NO ROLE AND NO OTHER BRAND CHANGES THE ANSWER
# ═════════════════════════════════════════════════════════════════════════════

def test_a_stale_customer_override_cannot_widen_a_portfolio(
        client, db_session, brand):
    """PRESERVED FROM THE PREVIOUS PASS, and now the stronger version.

    The owner shell stores X-Org-Override when somebody enters a customer. Here
    it names an organization that belongs to a DIFFERENT executive in the SAME
    brand. It must change nothing.
    """
    head = _h(db_session, brand["a"])
    plain = client.get("/executive/portfolio/health", headers=head).json()
    forged = client.get("/executive/portfolio/health",
                        headers={**head, "X-Org-Override": brand["o2"].id}).json()
    assert {o["name"] for o in forged["organizations"]} == {"Org One"}
    assert forged["total"] == plain["total"] == 1

    # And it cannot open the drill-down either.
    r = client.get("/executive/organizations/%s/performance" % brand["o2"].id,
                   headers={**head, "X-Org-Override": brand["o2"].id})
    assert r.status_code == 404


def test_a_customer_workspace_membership_does_not_become_a_portfolio(
        client, db_session, brand):
    """Being a MEMBER of a workspace is a different thing from overseeing it.

    Executive A is given an ordinary customer-org membership in Org Three — the
    kind a staff account holds. It carries a sales role, not an executive one,
    and must not appear in an executive portfolio. Visibility is assignment,
    never inference from some other row that happens to name an organization.
    """
    from app.models.sales_models import ROLE_SALES_REP
    db_session.add(Membership(user_id=brand["a"].id,
                              scope_type=SCOPE_CUSTOMER_ORG,
                              scope_id=brand["o3"].id,
                              role=ROLE_SALES_REP, is_active=True))
    db_session.commit()

    assert _names(client, db_session, brand["a"]) == {"Org One"}
    r = client.get("/executive/organizations/%s/performance" % brand["o3"].id,
                   headers=_h(db_session, brand["a"]))
    assert r.status_code == 404


def test_an_assignment_in_another_brand_grants_nothing(client, db_session, brand):
    """An assignment row carries no brand of its own.

    If one ever points at an organization in a different brand — a mistake, a
    moved customer, a stale row after a re-platforming — honouring it would let
    a portfolio cross a brand boundary, which is the one thing white-label
    isolation cannot survive.
    """
    other = _platform(db_session, "Rival")
    foreign = _org(db_session, other, "Rival Customer")
    db_session.add(Membership(user_id=brand["a"].id,
                              scope_type=SCOPE_CUSTOMER_ORG,
                              scope_id=foreign.id,
                              role=ROLE_BRAND_EXECUTIVE, is_active=True))
    db_session.commit()

    assert _names(client, db_session, brand["a"]) == {"Org One"}
    r = client.get("/executive/organizations/%s/performance" % foreign.id,
                   headers=_h(db_session, brand["a"]))
    assert r.status_code == 404


def test_a_revoked_assignment_stops_working_immediately(client, db_session, brand):
    """Visibility is recomputed from `is_active` on every request, so there is
    no cached portfolio that could outlive a removal."""
    assert _names(client, db_session, brand["c"]) == {"Org One", "Org Two"}

    exec_auth.unassign(db_session, executive_user_id=brand["c"].id,
                       organization_id=brand["o2"].id)

    assert _names(client, db_session, brand["c"]) == {"Org One"}
    r = client.get("/executive/organizations/%s/performance" % brand["o2"].id,
                   headers=_h(db_session, brand["c"]))
    assert r.status_code == 404


def test_removal_deactivates_rather_than_deleting_so_history_survives(
        db_session, brand):
    """The record that access once existed is the thing an audit needs most."""
    before = db_session.query(Membership).filter(
        Membership.user_id == brand["c"].id,
        Membership.scope_type == SCOPE_CUSTOMER_ORG).count()

    exec_auth.unassign(db_session, executive_user_id=brand["c"].id,
                       organization_id=brand["o2"].id)

    after_rows = db_session.query(Membership).filter(
        Membership.user_id == brand["c"].id,
        Membership.scope_type == SCOPE_CUSTOMER_ORG).all()
    assert len(after_rows) == before, "the assignment row was deleted"
    revoked = [m for m in after_rows if m.scope_id == brand["o2"].id]
    assert revoked and revoked[0].is_active is False


# ═════════════════════════════════════════════════════════════════════════════
# 4. THE OWNER IS A DIFFERENT AUTHORITY, AND SAYS SO
# ═════════════════════════════════════════════════════════════════════════════

def test_the_owner_sees_the_whole_brand_they_selected_and_it_is_labelled(
        client, db_session, brand):
    """THE OWNER OWNS THE ESTATE.

    Narrowing the platform owner to an assigned list would mean they could be
    locked out of their own customers by an assignment nobody made, and would
    need a fake row per organization to see what is already theirs. So they are
    brand-wide WITHIN the brand they selected — the same single-platform
    isolation every other executive query has.

    `is_owner_view` says which path produced the answer, so this is a stated
    difference rather than a silent one.
    """
    owner = _owner(db_session)
    head = {**_h(db_session, owner), "X-Brand-Override": brand["platform"].id}
    body = client.get("/executive/portfolio/health", headers=head).json()
    assert {o["name"] for o in body["organizations"]} == {"Org One", "Org Two",
                                                          "Org Three"}
    assert body["is_owner_view"] is True
    assert body["portfolio_source"] == exec_auth.SOURCE_OWNER


def test_the_owner_is_still_confined_to_the_selected_brand(
        client, db_session, brand):
    other = _platform(db_session, "Rival")
    foreign = _org(db_session, other, "Rival Customer")
    owner = _owner(db_session)
    head = {**_h(db_session, owner), "X-Brand-Override": brand["platform"].id}
    body = client.get("/executive/portfolio/health", headers=head).json()
    assert "Rival Customer" not in {o["name"] for o in body["organizations"]}
    r = client.get("/executive/organizations/%s/performance" % foreign.id,
                   headers=head)
    assert r.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
# 5. ASSIGNMENT IS OWNER-ONLY AND AUDITED
# ═════════════════════════════════════════════════════════════════════════════

def test_an_executive_cannot_widen_their_own_portfolio(
        client, db_session, brand):
    """The management endpoints are require_god. An executive calling them is
    refused, and nothing in the Executive Suite calls them at all."""
    head = _h(db_session, brand["a"])
    for url, body in (
        ("/executive/admin/portfolio/assign",
         {"user_id": brand["a"].id, "organization_id": brand["o2"].id}),
        ("/executive/admin/portfolio/unassign",
         {"user_id": brand["b"].id, "organization_id": brand["o2"].id}),
    ):
        r = client.post(url, headers=head, json=body)
        assert r.status_code in (401, 403), url

    r = client.get("/executive/admin/executives", headers=head)
    assert r.status_code in (401, 403)

    # And nothing changed.
    assert _names(client, db_session, brand["a"]) == {"Org One"}


def test_the_owner_can_assign_and_the_change_takes_effect_at_once(
        client, db_session, brand):
    owner = _owner(db_session)
    ohead = _h(db_session, owner)

    r = client.post("/executive/admin/portfolio/assign", headers=ohead,
                    json={"user_id": brand["a"].id,
                          "organization_id": brand["o3"].id})
    assert r.status_code == 200
    assert r.json()["status"] == "assigned"
    assert _names(client, db_session, brand["a"]) == {"Org One", "Org Three"}

    # Idempotent: assigning again does not create a second row.
    again = client.post("/executive/admin/portfolio/assign", headers=ohead,
                        json={"user_id": brand["a"].id,
                              "organization_id": brand["o3"].id})
    assert again.json()["status"] == "already_assigned"

    r2 = client.post("/executive/admin/portfolio/unassign", headers=ohead,
                     json={"user_id": brand["a"].id,
                           "organization_id": brand["o3"].id})
    assert r2.status_code == 200
    assert _names(client, db_session, brand["a"]) == {"Org One"}


def test_the_management_screen_lists_the_whole_brand_with_ticks(
        client, db_session, brand):
    """A checklist that only listed what was already ticked could never be used
    to add anything."""
    owner = _owner(db_session)
    body = client.get("/executive/admin/portfolio/%s?platform_id=%s"
                      % (brand["c"].id, brand["platform"].id),
                      headers=_h(db_session, owner)).json()
    assert {o["name"] for o in body["organizations"]} == {"Org One", "Org Two",
                                                          "Org Three"}
    assigned = {o["name"] for o in body["organizations"] if o["assigned"]}
    assert assigned == {"Org One", "Org Two"}
    assert body["assigned_count"] == 2
    assert body["has_brand_grant"] is True


def test_the_management_screen_flags_an_assignment_with_no_brand_grant(
        client, db_session, brand):
    """Ticking boxes for somebody who cannot enter the suite achieves nothing,
    and the screen has to say so rather than letting an owner think they have
    set somebody up."""
    stranger = User(organization_id=None, email="nb%d@x.com" % next(_SEQ),
                    password_hash=hash_password("x"), full_name="No Grant",
                    role="advisor", must_change_password=False)
    db_session.add(stranger); db_session.commit()

    owner = _owner(db_session)
    body = client.get("/executive/admin/portfolio/%s?platform_id=%s"
                      % (stranger.id, brand["platform"].id),
                      headers=_h(db_session, owner)).json()
    assert body["has_brand_grant"] is False


def test_assignment_is_audited_with_who_granted_it(db_session, brand):
    """`granted_by` and `created_at` are what answer 'who gave them this, and
    when' long after everyone has forgotten."""
    owner = _owner(db_session)
    exec_auth.assign(db_session, executive_user_id=brand["a"].id,
                     organization_id=brand["o3"].id,
                     granted_by_user_id=owner.id)
    row = (db_session.query(Membership)
           .filter(Membership.user_id == brand["a"].id,
                   Membership.scope_type == SCOPE_CUSTOMER_ORG,
                   Membership.scope_id == brand["o3"].id,
                   Membership.role == ROLE_BRAND_EXECUTIVE).first())
    assert row is not None
    assert row.granted_by == owner.id
    assert row.created_at is not None


# ═════════════════════════════════════════════════════════════════════════════
# 6. THE SERVICE CANNOT BE CALLED WITHOUT A SCOPE
# ═════════════════════════════════════════════════════════════════════════════

def test_the_portfolio_service_refuses_to_run_unscoped(db_session, brand):
    """A DEFAULT WOULD EVENTUALLY BE USED, and the failure is silent and total:
    every customer on the brand rendered as somebody's portfolio. So `org_ids`
    has no default and omitting it is a TypeError, not a wide answer."""
    from app.services import executive_portfolio as portfolio
    with pytest.raises(TypeError):
        portfolio.rows(db_session, brand["platform"].id)
    with pytest.raises(TypeError):
        portfolio.portfolio(db_session, brand["platform"].id)


def test_an_empty_scope_means_empty_and_never_unfiltered(db_session, brand):
    """Reading an empty list as 'no filter' is exactly the bug being fixed."""
    from app.services import executive_portfolio as portfolio
    assert portfolio.rows(db_session, brand["platform"].id, org_ids=[]) == []
    assert portfolio.portfolio(db_session, brand["platform"].id,
                               org_ids=[])["organizations"] == 0


def test_no_executive_surface_scopes_itself_by_brand_alone():
    """THE REGRESSION GUARD FOR THE WHOLE DEFECT CLASS.

    Every organization query on an EXECUTIVE-FACING route must be narrowed by
    the authorized id set as well as the platform. A future endpoint that
    filters on `platform_id` alone would reintroduce brand-wide visibility on
    one surface while every other one stayed correct — the hardest kind of hole
    to notice, because the product would look fixed everywhere you checked.

    THE ONE EXEMPTION, AND WHY IT IS NARROW. The owner's management screen
    lists the WHOLE brand on purpose: it is a checklist, and a checklist that
    only showed what was already ticked could never be used to add anything.
    That exemption is granted by `Depends(require_god)` and by nothing else, so
    a route cannot quietly opt out of the boundary without also demanding owner
    authority — and this test asserts the exempt routes really are god-gated
    rather than taking the marker on trust.
    """
    import ast
    import re
    from pathlib import Path

    path = (Path(__file__).resolve().parents[1]
            / "app" / "routers" / "executive_router.py")
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()

    checked = 0
    exempt = 0
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = "\n".join(lines[node.lineno - 1:node.end_lineno])
        # Strip docstrings and comments so prose quoting the old pattern
        # cannot satisfy — or trip — the check.
        body = re.sub(r'"""[\s\S]*?"""', "", body)
        body = re.sub(r"^\s*#.*$", "", body, flags=re.M)

        if "db.query(Organization)" not in body and \
           "db.query(Organization.id)" not in body and \
           "db.query(func.count(Organization.id))" not in body:
            continue

        if "require_god" in body:
            exempt += 1
            continue

        checked += 1
        for m in re.finditer(r"db\.query\([\s\S]{0,60}?Organization[\s\S]{0,500}?"
                             r"\.(all|scalar)\(\)", body):
            chunk = m.group(0)
            if "Organization.platform_id" not in chunk:
                continue
            assert "Organization.id.in_(" in chunk, (
                "%s scopes an Organization query by brand alone:\n%s"
                % (node.name, chunk))

    assert checked >= 3, (
        "expected several executive-facing organization queries to check; "
        "found %d — the scan is probably no longer matching" % checked)
    assert exempt >= 1, (
        "the owner's management screen should be the exempt one; found none")


# ═════════════════════════════════════════════════════════════════════════════
# 7. THE ASSIGNMENT SURFACE EXISTS AND IS REACHABLE
# ═════════════════════════════════════════════════════════════════════════════

def _fe(rel):
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "frontend" / "src"
            / rel).read_text(encoding="utf-8")


def test_the_owner_has_a_screen_for_this_and_it_is_wired_up():
    """AN AUTHORITY NOBODY CAN ADMINISTER IS NOT FINISHED.

    Before this pass there was no UI anywhere that managed executive access —
    the grant endpoint existed and nothing called it. Per-organization access
    that could only be set by a developer writing rows is exactly the failure
    mode this platform keeps refusing, so the screen ships with the rule.
    """
    import re
    page = _fe("pages/god/GodExecutiveAccess.jsx")
    app = _fe("App.jsx")
    shell = _fe("pages/GodShell.jsx")

    # It calls the real endpoints.
    for endpoint in ("/executive/admin/executives",
                     "/executive/admin/portfolio/",
                     "/executive/admin/portfolio/assign",
                     "/executive/admin/portfolio/unassign"):
        assert endpoint in page, endpoint

    # The route is registered, and the nav points at a route that exists —
    # the same dead-link check every other surface gets.
    assert 'path="/god/executive-access"' in app
    assert "GodExecutiveAccess" in app
    assert "'/god/executive-access'" in shell
    registered = set(re.findall(r'path="([^"]+)"', app))
    for target in re.findall(r"path: '(/god[^']*)'", shell):
        base = target.split("#")[0]
        assert base in registered, "%s is in the God nav and not registered" % base


def test_the_management_screen_shows_the_whole_brand_not_just_assignments():
    """A checklist that only listed what was already ticked could never be used
    to add anything — which is obvious, and is the trap a "current portfolio"
    view falls straight into."""
    import re
    page = re.sub(r"/\*[\s\S]*?\*/", "", _fe("pages/god/GodExecutiveAccess.jsx"))
    page = re.sub(r"^\s*//.*$", "", page, flags=re.M)
    assert "organizations.map" in page
    assert 'type="checkbox"' in page
    # And it re-reads from the server after a change rather than flipping local
    # state, so a checkbox can never disagree with what the person can see.
    assert "loadDetail(selected.user_id" in page
