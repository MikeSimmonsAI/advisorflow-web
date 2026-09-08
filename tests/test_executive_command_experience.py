"""
THE EXECUTIVE COMMAND EXPERIENCE - scope, isolation, reconciliation, honesty.

WHAT THIS FILE DEFENDS
======================

  1. A PORTFOLIO IS A BOUNDARY, NOT A FILTER. An executive holds one brand.
     Every test here that touches scope is written from the outside - through
     the HTTP client with a real token - because a boundary that only holds
     when the caller is polite is not a boundary.

  2. NO HEADER SUBSTITUTES FOR A GRANT. The owner shell stores
     X-Org-Override when somebody enters a customer. If that header could
     reach an executive route it would either widen a portfolio or silently
     narrow one to whichever customer was last looked at. Both are tested.

  3. OBSERVING IS NOT JOINING. Reading an organization must never write
     anything to the executive's identity and must never create a membership.
     This is the invariant that keeps "Michael can see Restland" from
     becoming "Michael is in Restland".

  4. THE HEADLINE AND THE LIST CANNOT DISAGREE. Every Command Center total is
     asserted equal to the sum of the rows Portfolio returns. A finance-shaped
     screen whose number cannot be counted is worse than no number.

  5. NOTHING IS FILLED IN. A figure the platform cannot compute is null and
     renders as words. Zero is reserved for actual zero. The clearest case is
     revenue: unpriceable and free are opposite facts about a business.
"""

import itertools
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.models.models import Lead, Organization, Platform, User
from app.models.sales_models import (ROLE_BRAND_EXECUTIVE, SCOPE_PLATFORM,
                                     Membership)
from app.services import executive_portfolio as portfolio
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(4000)

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "src"
EXEC_DIR = FRONTEND / "pages" / "executive"


# ═════════════════════════════════════════════════════════════════════════════
# fixtures - two brands, so isolation is testable rather than assumed
# ═════════════════════════════════════════════════════════════════════════════

def _platform(db, name):
    p = Platform(name=name, slug="%s-%d" % (name.lower(), next(_SEQ)))
    db.add(p); db.commit()
    return p


def _org(db, platform, name, *, active=True, created_days_ago=200, plan="starter"):
    o = Organization(
        name=name, slug="org-%d" % next(_SEQ),
        platform_id=platform.id, plan=plan, is_active=active,
        created_at=datetime.utcnow() - timedelta(days=created_days_ago))
    db.add(o); db.commit()
    return o


def _exec_user(db, platform, *, name="Executive"):
    """A brand executive: the BRAND GRANT only, no organization of their own.

    `organization_id` stays None on purpose and is asserted to stay None. An
    executive who acquires one has been made a member of somebody's workspace,
    which is the exact confusion this whole layer exists to prevent.

    A GRANT ALONE IS NO LONGER A PORTFOLIO. It says which brand this person may
    enter; which customers they see inside it comes from `_assign` below. That
    used to be implicit — holding the grant exposed every organization on the
    brand — and the fixtures here reflect the explicit model now.
    """
    u = User(organization_id=None, email="exec%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role="advisor",
             must_change_password=False)
    db.add(u); db.commit()
    db.add(Membership(user_id=u.id, scope_type=SCOPE_PLATFORM,
                      scope_id=platform.id, role=ROLE_BRAND_EXECUTIVE,
                      is_active=True))
    db.commit()
    return u


def _assign(db, user, *orgs):
    """Put organizations into an executive's portfolio, explicitly."""
    from app.models.sales_models import SCOPE_CUSTOMER_ORG
    for o in orgs:
        db.add(Membership(user_id=user.id, scope_type=SCOPE_CUSTOMER_ORG,
                          scope_id=o.id, role=ROLE_BRAND_EXECUTIVE,
                          is_active=True))
    db.commit()
    return user


def _lead(db, org, *, status="new", touched=None, created_days_ago=5):
    l = Lead(organization_id=org.id, first_name="Lead",
             last_name="#%d" % next(_SEQ),
             email="l%d@example.com" % next(_SEQ),
             status=status, last_messaged_at=touched,
             created_at=datetime.utcnow() - timedelta(days=created_days_ago))
    db.add(l); db.commit()
    return l


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


@pytest.fixture()
def brand(db_session):
    """One brand, one executive, two customers - one healthy, one neglected."""
    p = _platform(db_session, "Evo")
    ex = _exec_user(db_session, p)
    good = _org(db_session, p, "Restland Cemetery")
    bad = _org(db_session, p, "WUPA Memorial")
    # Restland is being worked; WUPA has a book nobody has touched.
    for _ in range(3):
        _lead(db_session, good, status="sent",
              touched=datetime.utcnow() - timedelta(days=2))
    for _ in range(60):
        _lead(db_session, bad, status="new", touched=None)
    _assign(db_session, ex, good, bad)
    return {"platform": p, "exec": ex, "good": good, "bad": bad}


@pytest.fixture()
def other_brand(db_session):
    """A second brand entirely. Nothing in it may ever be visible to `brand`."""
    p = _platform(db_session, "Rival")
    ex = _exec_user(db_session, p, name="Rival Executive")
    org = _org(db_session, p, "Somebody Else Funeral Home")
    _assign(db_session, ex, org)
    return {"platform": p, "exec": ex, "org": org}


# ═════════════════════════════════════════════════════════════════════════════
# 1. A PORTFOLIO IS A BOUNDARY
# ═════════════════════════════════════════════════════════════════════════════

def test_an_executive_sees_every_organization_they_oversee(
        client, db_session, brand):
    r = client.get("/executive/portfolio/health",
                   headers=_h(db_session, brand["exec"]))
    assert r.status_code == 200
    names = {o["name"] for o in r.json()["organizations"]}
    assert names == {"Restland Cemetery", "WUPA Memorial"}


def test_an_executive_never_sees_another_brands_organization(
        client, db_session, brand, other_brand):
    r = client.get("/executive/portfolio/health",
                   headers=_h(db_session, brand["exec"]))
    names = {o["name"] for o in r.json()["organizations"]}
    assert "Somebody Else Funeral Home" not in names

    # And the reverse, so this is isolation rather than one lucky ordering.
    r2 = client.get("/executive/portfolio/health",
                    headers=_h(db_session, other_brand["exec"]))
    names2 = {o["name"] for o in r2.json()["organizations"]}
    assert names2 == {"Somebody Else Funeral Home"}


def test_a_direct_url_cannot_reach_outside_the_portfolio(
        client, db_session, brand, other_brand):
    """THE ATTACK IS TYPING AN ID, so the test types an id.

    A real organization id from another brand, pasted into the executive
    drill-down URL, must be indistinguishable from a nonexistent one. A 403
    would confirm the organization exists; a thinner page would leak that it
    does. 404 is the only answer that says nothing.
    """
    r = client.get(
        "/executive/organizations/%s/performance" % other_brand["org"].id,
        headers=_h(db_session, brand["exec"]))
    assert r.status_code == 404

    nonsense = client.get(
        "/executive/organizations/org-does-not-exist/performance",
        headers=_h(db_session, brand["exec"]))
    assert nonsense.status_code == 404
    assert r.json() == nonsense.json()


def test_an_executive_can_open_an_organization_in_their_own_portfolio(
        client, db_session, brand):
    r = client.get(
        "/executive/organizations/%s/performance" % brand["good"].id,
        headers=_h(db_session, brand["exec"]))
    assert r.status_code == 200
    body = r.json()
    assert body["organization"]["name"] == "Restland Cemetery"
    assert body["read_only"] is True


def test_a_user_with_no_executive_grant_is_refused(client, db_session):
    nobody = User(organization_id=None, email="nobody%d@x.com" % next(_SEQ),
                  password_hash=hash_password("x"), full_name="Nobody",
                  role="advisor", must_change_password=False)
    db_session.add(nobody); db_session.commit()
    for url in ("/executive/portfolio", "/executive/portfolio/health"):
        assert client.get(url, headers=_h(db_session, nobody)).status_code == 403


def test_an_executive_cannot_reach_an_owner_route(client, db_session, brand):
    """Executive is not the owner shell with fewer permissions.

    The platform control plane is `require_god`, and an executive grant is not
    a step on the way to it.
    """
    for url in ("/god/stats", "/god/platform-health", "/god/orgs"):
        r = client.get(url, headers=_h(db_session, brand["exec"]))
        assert r.status_code in (401, 403, 404), url


# ═════════════════════════════════════════════════════════════════════════════
# 2. NO HEADER SUBSTITUTES FOR A GRANT
# ═════════════════════════════════════════════════════════════════════════════

def test_a_stale_customer_override_cannot_narrow_an_executive_portfolio(
        client, db_session, brand):
    """THE DEFECT THIS PREVENTS, STATED PLAINLY.

    The owner shell writes X-Org-Override when somebody enters a customer, and
    it used to be attached to every request the app made from anywhere. If an
    executive route honoured it, an executive who had previously looked at one
    customer would silently see a portfolio of one - and would have no way to
    tell that from a portfolio that really had one customer in it.

    The header is sent here deliberately. The answer must not change.
    """
    plain = client.get("/executive/portfolio/health",
                       headers=_h(db_session, brand["exec"])).json()
    with_stale = client.get(
        "/executive/portfolio/health",
        headers={**_h(db_session, brand["exec"]),
                 "X-Org-Override": brand["good"].id}).json()
    assert {o["id"] for o in with_stale["organizations"]} \
        == {o["id"] for o in plain["organizations"]}
    assert with_stale["total"] == plain["total"] == 2


def test_a_forged_override_cannot_widen_a_portfolio_across_brands(
        client, db_session, brand, other_brand):
    r = client.get(
        "/executive/portfolio/health",
        headers={**_h(db_session, brand["exec"]),
                 "X-Org-Override": other_brand["org"].id})
    assert r.status_code == 200
    names = {o["name"] for o in r.json()["organizations"]}
    assert "Somebody Else Funeral Home" not in names


def test_the_switcher_offers_only_the_authorized_portfolio(
        client, db_session, brand, other_brand):
    """Organization switching respects the portfolio, and uses durable ids.

    The picker is built from what the server sends with the page, so there is
    nothing on the client that could resolve an organization by name or reach
    one that was never authorized.
    """
    body = client.get(
        "/executive/organizations/%s/performance" % brand["good"].id,
        headers=_h(db_session, brand["exec"])).json()
    ids = {p["id"] for p in body["portfolio"]}
    assert ids == {brand["good"].id, brand["bad"].id}
    assert other_brand["org"].id not in ids
    for p in body["portfolio"]:
        assert p["id"] and p["name"]


# ═════════════════════════════════════════════════════════════════════════════
# 3. OBSERVING IS NOT JOINING
# ═════════════════════════════════════════════════════════════════════════════

def test_reading_an_organization_creates_no_membership_and_no_identity(
        client, db_session, brand):
    """The invariant that keeps 'can see Restland' from becoming 'is Restland'."""
    before = db_session.query(Membership).count()

    client.get("/executive/portfolio", headers=_h(db_session, brand["exec"]))
    client.get("/executive/portfolio/health", headers=_h(db_session, brand["exec"]))
    client.get("/executive/organizations/%s/performance" % brand["good"].id,
               headers=_h(db_session, brand["exec"]))

    db_session.expire_all()
    after = db_session.query(Membership).count()
    assert after == before, "reading a portfolio created a membership row"

    who = db_session.query(User).filter(User.id == brand["exec"].id).first()
    assert who.organization_id is None, (
        "the executive acquired an organization by looking at one")


def test_every_executive_read_route_is_get_only(client, db_session, brand):
    """READ-ONLY IS ENFORCED BY THERE BEING NOTHING TO CALL.

    Not by a flag the client is trusted to respect. A write verb against these
    paths must not be routed at all.
    """
    head = _h(db_session, brand["exec"])
    for url in ("/executive/portfolio", "/executive/portfolio/health",
                "/executive/organizations/%s/performance" % brand["good"].id):
        # DELETE takes no body in this client, so the verbs are driven through
        # `request` rather than the convenience helpers - the point is the
        # method, not the payload.
        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            r = client.request(verb, url, headers=head)
            assert r.status_code in (404, 405), "%s %s -> %s" % (
                verb, url, r.status_code)


def test_the_performance_endpoint_declares_itself_read_only(
        client, db_session, brand):
    body = client.get(
        "/executive/organizations/%s/performance" % brand["good"].id,
        headers=_h(db_session, brand["exec"])).json()
    assert body["read_only"] is True


# ═════════════════════════════════════════════════════════════════════════════
# 4. THE HEADLINE AND THE LIST CANNOT DISAGREE
# ═════════════════════════════════════════════════════════════════════════════

def test_command_centre_totals_equal_the_sum_of_the_rows(
        client, db_session, brand):
    """The reconciliation promise, asserted rather than trusted."""
    head = _h(db_session, brand["exec"])
    totals = client.get("/executive/portfolio", headers=head).json()["summary"]
    rows = client.get("/executive/portfolio/health",
                      headers=head).json()["organizations"]

    assert totals["organizations"] == len(rows)
    for field in ("leads_total", "leads_never_touched", "leads_worked_recently",
                  "appointments_total", "replies_unreviewed", "held_leads"):
        assert totals[field] == sum(r[field] for r in rows), field


def test_the_attention_count_matches_the_organizations_carrying_one(
        client, db_session, brand):
    head = _h(db_session, brand["exec"])
    body = client.get("/executive/portfolio", headers=head).json()
    rows = client.get("/executive/portfolio/health",
                      headers=head).json()["organizations"]
    assert body["attention_total"] == sum(1 for r in rows if r["attention"])
    assert body["summary"]["needs_attention"] == body["attention_total"]


def test_every_filter_count_matches_what_that_filter_returns(
        client, db_session, brand):
    """A tab that promises six and delivers four is a tab nobody trusts again."""
    head = _h(db_session, brand["exec"])
    base = client.get("/executive/portfolio/health", headers=head).json()
    for f in base["filters"]:
        got = client.get("/executive/portfolio/health?filter=" + f["key"],
                         headers=head).json()
        assert got["shown"] == f["count"], f["key"]
        assert len(got["organizations"]) == f["count"], f["key"]


def test_an_unknown_filter_falls_back_to_all_rather_than_emptying_the_page(
        client, db_session, brand):
    r = client.get("/executive/portfolio/health?filter=nonsense",
                   headers=_h(db_session, brand["exec"])).json()
    assert r["filter"] == "all"
    assert r["shown"] == r["total"] == 2


def test_the_drilldown_and_the_portfolio_agree_about_one_organization(
        client, db_session, brand):
    head = _h(db_session, brand["exec"])
    rows = client.get("/executive/portfolio/health",
                      headers=head).json()["organizations"]
    row = next(r for r in rows if r["id"] == brand["bad"].id)
    drill = client.get(
        "/executive/organizations/%s/performance" % brand["bad"].id,
        headers=head).json()["organization"]
    assert drill["health"] == row["health"]
    assert drill["reason"] == row["reason"]
    assert drill["leads_total"] == row["leads_total"]


# ═════════════════════════════════════════════════════════════════════════════
# 5. NOTHING IS FILLED IN
# ═════════════════════════════════════════════════════════════════════════════

def test_a_rate_with_no_denominator_is_null_and_never_zero(
        client, db_session, brand):
    """Nought replies from nought sends is not a nought percent response rate.

    A screen that renders 0% there is reporting a failure that did not happen.
    """
    rows = client.get("/executive/portfolio/health",
                      headers=_h(db_session, brand["exec"])).json()["organizations"]
    wupa = next(r for r in rows if r["name"] == "WUPA Memorial")
    assert wupa["leads_sent"] == 0
    assert wupa["response_rate"] is None
    assert wupa["conversion_rate"] is None


def test_revenue_that_cannot_be_priced_is_null_and_reported_with_coverage(
        client, db_session, brand):
    """UNPRICEABLE AND FREE ARE OPPOSITE FACTS about a business.

    These organizations have no billing plan in any catalogue, so MRR is null.
    The portfolio total must exclude them AND say how many it excluded, rather
    than folding them in at zero and quietly implying they earn nothing.
    """
    head = _h(db_session, brand["exec"])
    rows = client.get("/executive/portfolio/health",
                      headers=head).json()["organizations"]
    for r in rows:
        assert r["mrr"] is None
        assert r["mrr_available"] is False

    s = client.get("/executive/portfolio", headers=head).json()["summary"]
    assert s["mrr"] is None
    assert s["mrr_priced_organizations"] == 0
    assert s["mrr_unpriced_organizations"] == 2


def test_an_untouched_book_is_reported_as_an_exception_with_its_real_count(
        client, db_session, brand):
    """The condition Mike named: '1,584 leads remain unworked'."""
    rows = client.get("/executive/portfolio/health",
                      headers=_h(db_session, brand["exec"])).json()["organizations"]
    wupa = next(r for r in rows if r["name"] == "WUPA Memorial")
    assert wupa["leads_never_touched"] == 60
    item = next(a for a in wupa["attention"] if a["key"] == "unworked_leads")
    assert item["severity"] == "action_required"
    assert "60" in item["text"]


def test_health_always_carries_a_reason_in_words(client, db_session, brand):
    """A grade nobody can explain is something to argue with, not a tool."""
    rows = client.get("/executive/portfolio/health",
                      headers=_h(db_session, brand["exec"])).json()["organizations"]
    for r in rows:
        assert r["reason"] and len(r["reason"]) > 15, r["name"]
        assert r["health"] in portfolio.HEALTH_LABELS
        assert r["health_label"] == portfolio.HEALTH_LABELS[r["health"]]


def test_a_suspended_organization_says_so_rather_than_looking_quiet(
        db_session, brand):
    sus = _org(db_session, brand["platform"], "Suspended Home", active=False)
    _assign(db_session, brand["exec"], sus)
    rows = portfolio.rows(db_session, brand["platform"].id,
                          org_ids=[brand["good"].id, brand["bad"].id, sus.id])
    row = next(r for r in rows if r["id"] == sus.id)
    assert row["health"] == portfolio.INACTIVE
    assert "suspend" in row["reason"].lower()
    assert any(a["key"] == "suspended" for a in row["attention"])


def test_an_empty_portfolio_is_a_state_and_not_a_wall_of_zeroes(db_session):
    """A brand with no customers yet is normal, not broken."""
    p = _platform(db_session, "Fresh")
    assert portfolio.rows(db_session, p.id, org_ids=[]) == []
    s = portfolio.portfolio(db_session, p.id, org_ids=[])
    assert s["organizations"] == 0
    # The rates have no denominator, so they are absent rather than 0%.
    assert s["response_rate"] is None
    assert s["conversion_rate"] is None
    assert s["mrr"] is None


# ═════════════════════════════════════════════════════════════════════════════
# 6. THE SURFACE IS ITS OWN, AND CANNOT RESTYLE THE OTHERS
# ═════════════════════════════════════════════════════════════════════════════

def _src(rel):
    return (EXEC_DIR / rel).read_text(encoding="utf-8")


def _stripped(text):
    """Source without comments.

    Every absence assertion below runs on this, because the explanations in
    these files quote the things they replaced - "READ ONLY", the customer
    dashboard's own headings - so that a future reader knows what changed.
    Matching raw text would fail against the comment describing the fix, and
    the tempting repair is deleting the explanation.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def test_the_executive_surface_declares_itself_and_carries_its_own_palette():
    css = _src("ExecStyles.jsx")
    assert '[data-surface="executive"]{' in css
    assert '[data-appearance="dark"] [data-surface="executive"]{' in css
    for token in ("--ex-canvas", "--ex-surface", "--ex-ink", "--ex-accent"):
        assert token in css, token


def test_no_executive_rule_can_reach_another_surface():
    """STRUCTURAL ISOLATION, not a convention somebody has to remember.

    Every selector in this sheet is nested under the executive surface or its
    scope class, so a colour changed here cannot reach God Mode or a customer
    workspace. A bare selector would be able to.
    """
    css = _stripped(_src("ExecStyles.jsx"))
    body = css.split("const CSS = `", 1)[1].rsplit("`", 1)[0]
    # Every rule's selector list, taken as the text before each opening brace.
    selectors = re.findall(r"(?:^|\})\s*([^{}@]+)\{", body)
    for sel in selectors:
        sel = sel.strip()
        if not sel or sel.startswith("@") or sel.startswith("/*"):
            continue
        for one in sel.split(","):
            one = one.strip()
            if not one:
                continue
            assert (one.startswith('[data-surface="executive"]')
                    or one.startswith('[data-appearance="dark"] [data-surface="executive"]')
                    or one.startswith(".ex-")
                    or one.startswith("button.ex-")), (
                "selector %r is not scoped to the executive surface" % one)


def test_the_dark_executive_palette_stacks_rather_than_inverting():
    """Cards must sit ABOVE the canvas and fields BELOW their card, or the
    page reads as one flat sheet - which is what 'make it dark' produces."""
    css = _src("ExecStyles.jsx")
    dark = css.split('[data-appearance="dark"] [data-surface="executive"]{', 1)[1] \
              .split("}", 1)[0]

    def val(name):
        return re.search(r"--ex-%s:(#[0-9a-f]{6})" % name, dark).group(1)

    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    assert lum(val("surface")) > lum(val("canvas"))
    assert lum(val("surface3")) > lum(val("surface"))
    assert lum(val("field")) < lum(val("surface"))
    assert lum(val("ink")) > 200


def test_the_executive_nav_contains_no_route_that_does_not_exist():
    """A DEAD NAV ITEM TEACHES THE READER THAT THINGS HERE DO NOT WORK.

    Same defect class as the missing /god/customer-app route: a plausible path
    that falls through to a catch-all looks like a broken click rather than an
    error.
    """
    shell = _src("ExecutiveSuite.jsx")
    app = (FRONTEND / "App.jsx").read_text(encoding="utf-8")
    registered = set(re.findall(r'path="([^"]+)"', app))
    targets = set(re.findall(r"to: '(/executive[^']*)'", shell))
    assert targets, "the executive shell declares no navigation"
    for t in targets:
        assert t in registered, "%s is in the nav and not registered" % t


def test_the_drilldown_is_not_the_customer_dashboard_in_disguise():
    """THE WHOLE POINT OF THE REDESIGN, asserted.

    The page it replaces reused the customer workspace's own operating
    components - "What needs attention now", hot replies to answer, a lead
    queue to work. Those are right for the person picking up the phone and
    wrong for the executive deciding whether the business is working. Hiding
    the buttons and calling the result an executive view is exactly what
    produced the screen this replaces.
    """
    src = _stripped(_src("ExecutiveOrgPerformance.jsx"))
    for borrowed in ("hot_replies", "leads_needing_action", "recent_activity",
                     "observe/overview", "What needs attention now"):
        assert borrowed not in src, borrowed
    # And it reads the executive endpoint, not a tenant one.
    assert "/performance" in src
    assert "X-Executive-Observe" not in src


def test_the_drilldown_compares_against_the_portfolio_rather_than_standing_alone():
    """A rate on its own means nothing; against the portfolio median it means
    something an executive can act on."""
    src = _src("ExecutiveOrgPerformance.jsx")
    assert "comparison" in src
    assert "Delta" in src


def test_the_executive_pages_render_missing_figures_as_words_not_zero():
    """The rule the whole surface turns on, enforced where it is implemented."""
    ui = _src("ExecUI.jsx")
    assert "export function Figure" in ui
    assert "ex-none" in ui
    # Every page uses the primitive rather than formatting a null itself.
    for page in ("ExecutiveCommandCenter.jsx", "ExecutivePortfolio.jsx",
                 "ExecutiveRevenue.jsx", "ExecutiveOrgPerformance.jsx"):
        src = _src(page)
        assert ("Kpi" in src or "Val" in src or "Figure" in src), page


def test_the_old_read_only_banner_is_gone():
    """It shouted a restriction at somebody who was never trying to edit."""
    src = _stripped(_src("ExecutiveOrgPerformance.jsx"))
    assert "READ ONLY" not in src
    assert "#7f1d1d" not in src


def test_the_executive_layer_is_its_own_route_authority():
    """CUSTOMER CONTEXT MUST NOT LEAK INTO THE EXECUTIVE LAYER, and the client
    has to know that before the server has to defend against it.

    The server already refuses to read a customer override on executive routes
    (asserted end-to-end above). This is the other half: the browser must not
    SEND it, and must not render the "VIEWING AS <customer>" banner over a
    page that is not operating as that customer. Two independent defences,
    because the header travelling at all is how the last leak happened.
    """
    src = (FRONTEND / "auth" / "routeAuthority.js").read_text(encoding="utf-8")
    assert "EXECUTIVE" in src
    assert "'/executive'" in src

    body = _stripped(src)
    # Executive is classified BEFORE the CUSTOMER FALLBACK, or it would inherit
    # the customer default and start sending the override again.
    #
    # `rindex`, not `index`: an earlier `return CUSTOMER` is the deliberate
    # /god/customer-app exception, which is customer space and must stay ahead
    # of everything. The one that matters here is the LAST one — the catch-all
    # at the end of classifyRoute that anything unclassified falls into.
    assert body.index("EXECUTIVE_PREFIXES.some") < body.rindex("return CUSTOMER")
    # And it is a distinct class, not an alias for platform - an executive sees
    # one brand, so a banner claiming platform-wide authority would be wrong.
    assert "export const EXECUTIVE" in body


def test_the_executive_shell_offers_the_appearance_control():
    """Executive participates in light / dark / system like every other
    surface. A layer that cannot follow the setting is a layer that looks
    broken the first time somebody changes it."""
    assert "AppearanceToggle" in _src("ExecutiveSuite.jsx")
