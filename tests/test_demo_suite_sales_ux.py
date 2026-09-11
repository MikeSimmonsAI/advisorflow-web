"""
DEMO SUITE — how a salesperson gets in, and how they get out.

WHAT WAS MISSING IN LIVE USE
----------------------------
The Suite worked. Nobody could find it: the only way in was typing
`/demo-suite`, and the only way out was the browser's Back button. Neither of
those is a product.

WHAT THIS SUITE DEFENDS

 1. The way IN is the capability, not a role and not a URL somebody remembers.
 2. The opportunity can launch its own demo, and the server decides whether it
    may — the browser never works out its own entitlement or its own tenant.
 3. The way OUT returns to where the presenter came from, and that destination
    is COMPOSED BY THE SERVER. There is no return-URL parameter to abuse, and
    the id that is accepted is authorised before anything is said about it.
 4. Presentation progress is not business state. Walking seven coach steps
    moves no deal, publishes no demo, sends no message and charges nothing.
 5. The demonstration guarantee still holds with a real opportunity in the
    header: context is context, and it is not a door.
"""

import itertools

import pytest

from app.models.demo_suite_models import DemoEnvironment
from app.models.models import Message, Organization, Platform, User
from app.models.sales_models import (
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    BrandSalesOrg, Membership, Opportunity, STAGE_DISCOVERY,
)
from app.services import capabilities
from app.services import demo_environment as denv
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _user(db, role="advisor", name="Person"):
    u = User(organization_id=None, email="dsux%d@example.com" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


def _member(db, user, bso, role=ROLE_SALES_REP):
    db.add(Membership(user_id=user.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=bso.id, role=role, is_active=True))
    db.commit()


def _opp(db, bso, owner, company="Countryside Land Partners"):
    o = Opportunity(brand_sales_org_id=bso.id, owner_user_id=owner.id,
                    company_name=company, contact_name="Sam Prospect",
                    email="sam@example.test", stage=STAGE_DISCOVERY)
    db.add(o)
    db.commit()
    return o


@pytest.fixture()
def world(db_session):
    """Two brands, each with a real sales org and a real deal, plus a built
    demonstration environment on the first. Two brands is the whole point — a
    single-brand fixture cannot fail a cross-brand test."""
    god = _user(db_session, role="god_admin", name="Owner")
    evo = Platform(name="EvoSys Pro", slug="dsux-evo-%d" % next(_SEQ))
    boost = Platform(name="BookaBoost", slug="dsux-boost-%d" % next(_SEQ))
    db_session.add_all([evo, boost])
    db_session.commit()

    evo_sales = BrandSalesOrg(platform_id=evo.id, name="EvoSys Sales",
                              slug="dsux-evo-sales-%d" % next(_SEQ))
    boost_sales = BrandSalesOrg(platform_id=boost.id, name="BookaBoost Sales",
                                slug="dsux-boost-sales-%d" % next(_SEQ))
    db_session.add_all([evo_sales, boost_sales])
    db_session.commit()

    rep = _user(db_session, name="Mike Seller")
    _member(db_session, rep, evo_sales)
    other = _user(db_session, name="Other Seller")
    _member(db_session, other, evo_sales)
    boost_rep = _user(db_session, name="Boost Seller")
    _member(db_session, boost_rep, boost_sales)

    denv.build(db_session, evo.id, actor=god)

    return dict(
        god=god, evo=evo, boost=boost,
        evo_sales=evo_sales, boost_sales=boost_sales,
        rep=rep, other=other, boost_rep=boost_rep,
        opp=_opp(db_session, evo_sales, rep),
        other_opp=_opp(db_session, evo_sales, other, company="Not Mine Ltd"),
        boost_opp=_opp(db_session, boost_sales, boost_rep,
                       company="Other Brand Ltd"),
    )


def _grant(db, user, platform, god, keys=("demo_suite",)):
    capabilities.set_platform_grants(db, user, platform.id, platform.name, god,
                                     list(keys), commit=True)


# ── 1. THE WAY IN ──────────────────────────────────────────────────────────

class TestGettingIn:
    def test_the_nav_flag_is_off_until_the_capability_is_granted(
            self, client, db_session, world):
        me = client.get("/sales/me", headers=_h(db_session, world["rep"])).json()
        assert me["permissions"]["present_demo"] is False
        assert me["demo_suite"]["available"] is False

    def test_granting_the_capability_turns_the_nav_entry_on(
            self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        me = client.get("/sales/me", headers=_h(db_session, world["rep"])).json()
        assert me["permissions"]["present_demo"] is True
        assert me["demo_suite"]["available"] is True
        assert me["demo_suite"]["platform_id"] == world["evo"].id

    def test_the_flag_is_the_same_answer_the_suite_gives(
            self, client, db_session, world):
        """A visible nav item and an open door must not come apart — so the
        flag is read from the same capability the Suite's own routes check."""
        h = _h(db_session, world["rep"])
        me = client.get("/sales/me", headers=h).json()
        ctx = client.get("/demo-suite/%s/context" % world["evo"].id, headers=h)
        assert me["permissions"]["present_demo"] is False
        assert ctx.status_code == 403

        _grant(db_session, world["rep"], world["evo"], world["god"])
        h = _h(db_session, world["rep"])
        me = client.get("/sales/me", headers=h).json()
        ctx = client.get("/demo-suite/%s/context" % world["evo"].id, headers=h)
        assert me["permissions"]["present_demo"] is True
        assert ctx.status_code == 200

    def test_anonymous_is_refused(self, client, world):
        for path in ("/demo-suite/me",
                     "/demo-suite/%s/context" % world["evo"].id,
                     "/sales/opportunities/x/demo-launch"):
            assert client.get(path).status_code in (401, 403)


# ── 2. RUN DEMO, FROM THE DEAL ─────────────────────────────────────────────

class TestRunDemoFromTheOpportunity:
    def test_an_entitled_rep_gets_an_eligible_launch_with_the_brand_resolved(
            self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        r = client.get("/sales/opportunities/%s/demo-launch" % world["opp"].id,
                       headers=_h(db_session, world["rep"]))
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["eligible"] is True
        assert b["platform_id"] == world["evo"].id
        assert b["brand_name"] == "EvoSys Pro"
        assert b["company_name"] == "Countryside Land Partners"
        assert b["contact_name"] == "Sam Prospect"
        assert b["owner_name"] == "Mike Seller"
        # Composed by the server, from the id it just authorised.
        assert b["return_to"] == "/sales/opportunities/%s" % world["opp"].id

    def test_without_the_capability_the_deal_is_not_launchable(
            self, client, db_session, world):
        r = client.get("/sales/opportunities/%s/demo-launch" % world["opp"].id,
                       headers=_h(db_session, world["rep"]))
        assert r.status_code == 200
        assert r.json()["eligible"] is False
        assert "Demo Suite access" in (r.json()["reason"] or "")

    def test_another_reps_deal_is_not_launchable_at_all(
            self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        r = client.get("/sales/opportunities/%s/demo-launch"
                       % world["other_opp"].id,
                       headers=_h(db_session, world["rep"]))
        assert r.status_code in (403, 404)
        assert "Not Mine" not in r.text

    def test_another_brands_deal_is_not_launchable_at_all(
            self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        r = client.get("/sales/opportunities/%s/demo-launch"
                       % world["boost_opp"].id,
                       headers=_h(db_session, world["rep"]))
        assert r.status_code == 404
        assert "Other Brand" not in r.text


# ── 3. THE WAY OUT ─────────────────────────────────────────────────────────

class TestExitAndReturnContext:
    def _ctx(self, client, db, user, platform, opportunity=None):
        url = "/demo-suite/%s/context" % platform.id
        if opportunity is not None:
            url += "?opportunity=" + str(opportunity)
        return client.get(url, headers=_h(db, user))

    def test_launched_from_the_library_exit_goes_to_the_library(
            self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        b = self._ctx(client, db_session, world["rep"], world["evo"]).json()
        assert b["origin"] == "library"
        assert b["return_href"] == "/demo-suite"
        assert b["opportunity_id"] is None

    def test_launched_from_a_deal_exit_goes_back_to_that_deal(
            self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        b = self._ctx(client, db_session, world["rep"], world["evo"],
                      world["opp"].id).json()
        assert b["origin"] == "opportunity"
        assert b["opportunity_id"] == world["opp"].id
        assert b["return_href"] == "/sales/opportunities/%s" % world["opp"].id
        assert b["company_name"] == "Countryside Land Partners"
        assert b["return_label"] == "Countryside Land Partners"

    def test_an_unknown_id_falls_back_to_the_library(
            self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        b = self._ctx(client, db_session, world["rep"], world["evo"],
                      "no-such-opportunity").json()
        assert b["return_href"] == "/demo-suite"
        assert b["opportunity_id"] is None

    def test_another_reps_deal_falls_back_and_leaks_nothing(
            self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        r = self._ctx(client, db_session, world["rep"], world["evo"],
                      world["other_opp"].id)
        assert r.status_code == 200, "a refusal here confirms the id is real"
        b = r.json()
        assert b["return_href"] == "/demo-suite"
        assert b["opportunity_id"] is None
        assert "Not Mine" not in r.text

    def test_another_brands_deal_falls_back(self, client, db_session, world):
        """Presenting EvoSys Pro's world while the header names a BookaBoost
        prospect would be a cross-brand claim on the screen the customer is
        looking at."""
        _grant(db_session, world["boost_rep"], world["evo"], world["god"])
        r = self._ctx(client, db_session, world["boost_rep"], world["evo"],
                      world["boost_opp"].id)
        assert r.status_code == 200
        assert r.json()["return_href"] == "/demo-suite"
        assert "Other Brand" not in r.text


# ── 4. OPEN REDIRECT ───────────────────────────────────────────────────────

class TestTheReturnTargetCannotBeSteered:
    HOSTILE = [
        "https://evil.example.com/harvest",
        "//evil.example.com",
        "http://evil.example.com",
        "/god/platform",
        "javascript:alert(1)",
        "/sales/opportunities/../../god",
        "\\\\evil.example.com",
    ]

    def test_nothing_a_caller_sends_reaches_the_return_target(
            self, client, db_session, world):
        """There is no return-URL parameter at all — the only id-shaped input
        is authorised first, and the path is composed server-side. This proves
        the property rather than the absence of the parameter."""
        _grant(db_session, world["rep"], world["evo"], world["god"])
        h = _h(db_session, world["rep"])
        for hostile in self.HOSTILE:
            r = client.get("/demo-suite/%s/context" % world["evo"].id,
                           params={"opportunity": hostile}, headers=h)
            assert r.status_code == 200, hostile
            href = r.json()["return_href"]
            assert href in ("/demo-suite",), (hostile, href)
            assert "evil.example.com" not in r.text
            assert "javascript:" not in r.text

    def test_every_return_target_is_one_of_two_shapes(
            self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        h = _h(db_session, world["rep"])
        seen = set()
        for opp in (None, world["opp"].id, world["other_opp"].id, "nope"):
            url = "/demo-suite/%s/context" % world["evo"].id
            if opp:
                url += "?opportunity=" + opp
            seen.add(client.get(url, headers=h).json()["return_href"])
        assert seen <= {"/demo-suite",
                        "/sales/opportunities/%s" % world["opp"].id}


# ── 5. PRESENTATION PROGRESS IS NOT BUSINESS STATE ─────────────────────────

class TestNothingRealMoves:
    def _present(self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        return _h(db_session, world["rep"])

    def test_opening_a_demo_from_a_deal_changes_nothing_about_the_deal(
            self, client, db_session, world):
        h = self._present(client, db_session, world)
        before = {"stage": world["opp"].stage,
                  "status": world["opp"].status,
                  "demo_status": world["opp"].demo_status,
                  "demo_url": world["opp"].demo_url}

        client.get("/demo-suite/%s/context?opportunity=%s"
                   % (world["evo"].id, world["opp"].id), headers=h)
        client.get("/demo-suite/%s/world" % world["evo"].id, headers=h)

        db_session.expire_all()
        opp = db_session.query(Opportunity).filter(
            Opportunity.id == world["opp"].id).first()
        assert opp.stage == before["stage"]
        assert opp.status == before["status"]
        assert opp.demo_status == before["demo_status"]
        assert opp.demo_url is None and before["demo_url"] is None

    def test_walking_every_coach_step_moves_no_deal_and_publishes_nothing(
            self, client, db_session, world):
        h = self._present(client, db_session, world)
        scen = client.get("/demo-suite/%s/scenarios" % world["evo"].id,
                          headers=h).json()["scenarios"][0]
        sess = client.get("/demo-suite/%s/scenarios/%s"
                          % (world["evo"].id, scen["key"]), headers=h).json()

        for step in sess["steps"]:
            client.post("/demo-suite/%s/scenarios/%s/step"
                        % (world["evo"].id, scen["key"]),
                        json={"step": step["key"]}, headers=h)

        done = client.get("/demo-suite/%s/scenarios/%s"
                          % (world["evo"].id, scen["key"]), headers=h).json()
        assert all(s["done"] for s in done["steps"]), "the walk did not record"

        db_session.expire_all()
        opp = db_session.query(Opportunity).filter(
            Opportunity.id == world["opp"].id).first()
        # Seven ticks is a presenter's progress, not a delivered demo.
        assert opp.stage == STAGE_DISCOVERY
        assert opp.demo_status in (None, "not_requested")
        assert opp.demo_url is None
        assert opp.demo_ready_at is None

    def test_restart_resets_the_walk_and_only_the_walk(
            self, client, db_session, world):
        h = self._present(client, db_session, world)
        scen = client.get("/demo-suite/%s/scenarios" % world["evo"].id,
                          headers=h).json()["scenarios"][0]
        first = client.get("/demo-suite/%s/scenarios/%s"
                           % (world["evo"].id, scen["key"]),
                           headers=h).json()["steps"][0]
        client.post("/demo-suite/%s/scenarios/%s/step"
                    % (world["evo"].id, scen["key"]),
                    json={"step": first["key"]}, headers=h)
        mid = client.get("/demo-suite/%s/scenarios/%s"
                         % (world["evo"].id, scen["key"]), headers=h).json()
        assert sum(1 for s in mid["steps"] if s["done"]) == 1

        after = client.post("/demo-suite/%s/scenarios/%s/reset"
                            % (world["evo"].id, scen["key"]), headers=h).json()
        assert sum(1 for s in after["steps"] if s["done"]) == 0

        db_session.expire_all()
        opp = db_session.query(Opportunity).filter(
            Opportunity.id == world["opp"].id).first()
        assert opp.stage == STAGE_DISCOVERY

    def test_no_message_ever_leaves_the_real_tenant(
            self, client, db_session, world):
        """The demonstration guarantee, re-proved with a real deal in the
        header: context is context, and it is not a door."""
        h = self._present(client, db_session, world)
        env = db_session.query(DemoEnvironment).filter(
            DemoEnvironment.platform_id == world["evo"].id).first()

        client.get("/demo-suite/%s/context?opportunity=%s"
                   % (world["evo"].id, world["opp"].id), headers=h)

        # A Message is scoped through its Lead, so that is where the tenancy
        # is checked. Every message in the database must belong to a lead in
        # the demonstration workspace — there is no other kind here.
        from app.models.models import Lead
        leads = {l.id: l.organization_id for l in db_session.query(Lead).all()}
        for m in db_session.query(Message).all():
            assert leads.get(m.lead_id) == env.organization_id

        # And no real organisation has a lead carrying a message at all.
        real_org_ids = {o.id for o in db_session.query(Organization)
                        .filter(Organization.is_demo.isnot(True)).all()}
        for m in db_session.query(Message).all():
            assert leads.get(m.lead_id) not in real_org_ids


# ── 6. THE SUITE IS NOT GOD, AND NOT ANOTHER BRAND ─────────────────────────

class TestAccessStaysNarrow:
    def test_demo_access_does_not_grant_god(self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        h = _h(db_session, world["rep"])
        for path in ("/god/demo-suite/environments",
                     "/god/ops/implementations",
                     "/god/launch"):
            assert client.get(path, headers=h).status_code in (401, 403), path

    def test_demo_access_to_one_brand_is_not_access_to_another(
            self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        h = _h(db_session, world["rep"])
        assert client.get("/demo-suite/%s/context" % world["boost"].id,
                          headers=h).status_code == 403
        assert client.get("/demo-suite/%s/world" % world["boost"].id,
                          headers=h).status_code == 403

    def test_presenting_does_not_confer_rebuilding(
            self, client, db_session, world):
        _grant(db_session, world["rep"], world["evo"], world["god"])
        h = _h(db_session, world["rep"])
        r = client.post("/demo-suite/%s/rebuild" % world["evo"].id, headers=h)
        assert r.status_code == 403

    def test_a_manager_of_another_brand_cannot_present_this_one(
            self, client, db_session, world):
        mgr = _user(db_session, name="Boost Manager")
        _member(db_session, mgr, world["boost_sales"], ROLE_SALES_MANAGER)
        r = client.get("/demo-suite/%s/context" % world["evo"].id,
                       headers=_h(db_session, mgr))
        assert r.status_code == 403
