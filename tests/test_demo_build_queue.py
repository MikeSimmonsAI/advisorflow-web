"""
DEMOS TO BUILD — the queue, and the workspace a job opens into.

THE DEFECT THIS SUITE EXISTS FOR
--------------------------------
Discovery completed → Request Demo → the opportunity correctly moved to Demo
Build, the demo was correctly marked Requested, and the "Demos to build" count
on /sales/proposals correctly went up. Then nothing: the count was an integer
from a per-rep rollup, no endpoint anywhere returned the individual demo jobs,
and "Open demo build" expanded a section on the page you were already on. A
request could be made and never reached again.

So the guarantees are:

 1. A requested demo appears as an INDIVIDUAL job, not only in a count.
 2. The job carries what a builder needs to act: company, contact, sales owner,
    requested date, status, builder, target date.
 3. The job is OPENABLE — the workspace endpoint answers for it.
 4. The workspace hands over discovery RENDERED, so nothing is retyped.
 5. Assigning a builder and setting a target go through the endpoint that
    already existed, and both surfaces see the result.
 6. Authority and tenant isolation are exactly the pipeline's, unchanged.
 7. Marking ready and publishing do not happen by accident.
"""
import itertools

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, Opportunity, DiscoveryRecord,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    STAGE_DISCOVERY, STAGE_DEMO_BUILD, STAGE_PROSPECT,
    DEMO_REQUESTED, DEMO_IN_PROGRESS, DEMO_READY,
)
from app.services import discovery_schema as ds
from app.services.auth_service import hash_password, create_access_token

_SEQ = itertools.count(1)


# ── fixtures (same shape as tests/test_opportunity_seller_ux.py) ────────────

@pytest.fixture()
def platform(db_session):
    p = Platform(name="Provider Brand", slug="dq-%d" % next(_SEQ))
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def brand(db_session, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="Brand Sales",
                      slug="dq-sales-%d" % next(_SEQ))
    db_session.add(b)
    db_session.commit()
    return b


@pytest.fixture()
def brand_b(db_session, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="Other Sales",
                      slug="dq-sales-b-%d" % next(_SEQ))
    db_session.add(b)
    db_session.commit()
    return b


def _user(db, name="Sales Person"):
    u = User(organization_id=None, email="dq%d@example.test" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name,
             role="advisor", must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _member(db, user, brand, role):
    m = Membership(user_id=user.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=brand.id, role=role, is_active=True)
    db.add(m)
    db.commit()
    return m


def _h(user, db):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def _opp(db, brand, owner=None, company="Countryside Land Partners",
         stage=STAGE_PROSPECT, contact="Sam Buyer"):
    o = Opportunity(brand_sales_org_id=brand.id,
                    owner_user_id=owner.id if owner else None,
                    company_name=company, contact_name=contact,
                    email="sam@example.test", stage=stage)
    db.add(o)
    db.commit()
    return o


@pytest.fixture()
def manager(db_session, brand):
    u = _user(db_session, "Team Manager")
    _member(db_session, u, brand, ROLE_SALES_MANAGER)
    return u


@pytest.fixture()
def rep(db_session, brand):
    u = _user(db_session, "Mike Seller")
    _member(db_session, u, brand, ROLE_SALES_REP)
    return u


@pytest.fixture()
def other_rep(db_session, brand):
    u = _user(db_session, "Other Seller")
    _member(db_session, u, brand, ROLE_SALES_REP)
    return u


def _request_demo(client, db, user, opp):
    """The real flow: move the deal to Demo Build, exactly as the UI does."""
    r = client.patch("/sales/opportunities/" + opp.id,
                     json={"stage": "demo_build"}, headers=_h(user, db))
    assert r.status_code == 200, r.text
    return r


# ── the queue exists at all ────────────────────────────────────────────────

class TestTheQueueHasRows:
    def test_requesting_a_demo_puts_an_individual_job_in_the_queue(
            self, client, db_session, brand, rep):
        opp = _opp(db_session, brand, rep, stage=STAGE_DISCOVERY)
        _request_demo(client, db_session, rep, opp)

        r = client.get("/sales/demo-queue?brand_sales_org_id=" + brand.id,
                       headers=_h(rep, db_session))
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["summary"]["total"] == 1
        assert len(body["jobs"]) == 1, "the count moved but no job appeared"
        job = body["jobs"][0]
        assert job["opportunity_id"] == opp.id
        assert job["company_name"] == "Countryside Land Partners"

    def test_the_job_carries_what_a_builder_needs_to_act(
            self, client, db_session, brand, rep):
        opp = _opp(db_session, brand, rep, stage=STAGE_DISCOVERY)
        _request_demo(client, db_session, rep, opp)

        job = client.get("/sales/demo-queue?brand_sales_org_id=" + brand.id,
                         headers=_h(rep, db_session)).json()["jobs"][0]
        for key in ("company_name", "contact_name", "sales_owner_name",
                    "requested_at", "demo_status", "demo_status_label",
                    "builder_user_id", "due_at", "discovery_answered",
                    "discovery_required", "requirements_captured"):
            assert key in job, "the queue cannot show %s" % key
        assert job["contact_name"] == "Sam Buyer"
        assert job["sales_owner_name"] == "Mike Seller"
        assert job["demo_status"] == DEMO_REQUESTED
        assert job["requested_at"] is not None
        # The two things the reported failure showed on screen.
        assert job["builder_user_id"] is None
        assert job["due_at"] is None

    def test_a_deal_with_no_demo_requested_is_not_in_the_queue(
            self, client, db_session, brand, rep):
        _opp(db_session, brand, rep, company="Not Asked Yet",
             stage=STAGE_DISCOVERY)
        body = client.get("/sales/demo-queue?brand_sales_org_id=" + brand.id,
                          headers=_h(rep, db_session)).json()
        assert body["summary"]["total"] == 0
        assert body["jobs"] == []

    def test_the_summary_and_the_rows_are_the_same_rows(
            self, client, db_session, brand, manager, rep, other_rep):
        a = _opp(db_session, brand, rep, company="A Co", stage=STAGE_DISCOVERY)
        b = _opp(db_session, brand, rep, company="B Co", stage=STAGE_DISCOVERY)
        c = _opp(db_session, brand, other_rep, company="C Co", stage=STAGE_DISCOVERY)
        for o, who in ((a, rep), (b, rep), (c, other_rep)):
            _request_demo(client, db_session, who, o)
        # One of them gets a builder, so by_builder has two buckets.
        client.patch("/sales/opportunities/" + a.id,
                     json={"demo_owner_user_id": rep.id},
                     headers=_h(manager, db_session))

        body = client.get("/sales/demo-queue?brand_sales_org_id=" + brand.id,
                          headers=_h(manager, db_session)).json()
        assert body["summary"]["total"] == 3
        assert len(body["jobs"]) == 3
        assert sum(x["count"] for x in body["summary"]["by_builder"]) == 3
        names = {x["name"]: x["count"] for x in body["summary"]["by_builder"]}
        assert names["Mike Seller"] == 1
        assert names["Unassigned"] == 2
        assert body["summary"]["unassigned"] == 2


# ── authority and isolation ────────────────────────────────────────────────

class TestAuthority:
    def test_anonymous_is_refused(self, client, brand):
        assert client.get("/sales/demo-queue").status_code in (401, 403)

    def test_a_rep_sees_their_own_book_and_a_manager_sees_the_brand(
            self, client, db_session, brand, manager, rep, other_rep):
        mine = _opp(db_session, brand, rep, company="Mine Co",
                    stage=STAGE_DISCOVERY)
        theirs = _opp(db_session, brand, other_rep, company="Theirs Co",
                      stage=STAGE_DISCOVERY)
        _request_demo(client, db_session, rep, mine)
        _request_demo(client, db_session, other_rep, theirs)

        rep_jobs = client.get("/sales/demo-queue?brand_sales_org_id=" + brand.id,
                              headers=_h(rep, db_session)).json()["jobs"]
        assert {j["company_name"] for j in rep_jobs} == {"Mine Co"}

        mgr_jobs = client.get("/sales/demo-queue?brand_sales_org_id=" + brand.id,
                              headers=_h(manager, db_session)).json()["jobs"]
        assert {j["company_name"] for j in mgr_jobs} == {"Mine Co", "Theirs Co"}

    def test_another_brands_demos_are_invisible(
            self, client, db_session, brand, brand_b, manager):
        other_user = _user(db_session, "Brand B Manager")
        _member(db_session, other_user, brand_b, ROLE_SALES_MANAGER)
        theirs = _opp(db_session, brand_b, other_user, company="Brand B Deal",
                      stage=STAGE_DISCOVERY)
        _request_demo(client, db_session, other_user, theirs)

        jobs = client.get("/sales/demo-queue?brand_sales_org_id=" + brand.id,
                          headers=_h(manager, db_session)).json()["jobs"]
        assert all(j["company_name"] != "Brand B Deal" for j in jobs)

    def test_the_workspace_refuses_another_reps_deal(
            self, client, db_session, brand, rep, other_rep):
        theirs = _opp(db_session, brand, other_rep, company="Not Yours",
                      stage=STAGE_DISCOVERY)
        _request_demo(client, db_session, other_rep, theirs)
        r = client.get("/sales/opportunities/" + theirs.id + "/demo-build",
                       headers=_h(rep, db_session))
        assert r.status_code in (403, 404)


# ── filters ────────────────────────────────────────────────────────────────

class TestFilters:
    def _two(self, client, db, brand, manager, rep):
        a = _opp(db, brand, rep, company="Assigned Co", stage=STAGE_DISCOVERY)
        b = _opp(db, brand, rep, company="Unassigned Co", stage=STAGE_DISCOVERY)
        _request_demo(client, db, rep, a)
        _request_demo(client, db, rep, b)
        client.patch("/sales/opportunities/" + a.id,
                     json={"demo_owner_user_id": rep.id}, headers=_h(manager, db))
        return a, b

    def test_unassigned(self, client, db_session, brand, manager, rep):
        self._two(client, db_session, brand, manager, rep)
        jobs = client.get("/sales/demo-queue?brand_sales_org_id=%s&builder=unassigned"
                          % brand.id, headers=_h(manager, db_session)).json()["jobs"]
        assert {j["company_name"] for j in jobs} == {"Unassigned Co"}

    def test_me(self, client, db_session, brand, manager, rep):
        self._two(client, db_session, brand, manager, rep)
        jobs = client.get("/sales/demo-queue?brand_sales_org_id=%s&builder=me"
                          % brand.id, headers=_h(rep, db_session)).json()["jobs"]
        assert {j["company_name"] for j in jobs} == {"Assigned Co"}

    def test_a_finished_demo_is_hidden_unless_asked_for(
            self, client, db_session, brand, manager, rep):
        a = _opp(db_session, brand, rep, company="Done Co", stage=STAGE_DISCOVERY)
        _request_demo(client, db_session, rep, a)
        client.patch("/sales/opportunities/" + a.id,
                     json={"demo_status": DEMO_READY}, headers=_h(manager, db_session))

        body = client.get("/sales/demo-queue?brand_sales_org_id=" + brand.id,
                          headers=_h(manager, db_session)).json()
        # Still in demo_build stage, so it is still outstanding work.
        assert body["summary"]["total"] == 1

        moved = client.patch("/sales/opportunities/" + a.id,
                             json={"stage": "demo_proposal"},
                             headers=_h(manager, db_session))
        assert moved.status_code == 200
        body = client.get("/sales/demo-queue?brand_sales_org_id=" + brand.id,
                          headers=_h(manager, db_session)).json()
        assert body["summary"]["total"] == 0
        assert body["jobs"] == []

        with_done = client.get(
            "/sales/demo-queue?brand_sales_org_id=%s&include_done=true" % brand.id,
            headers=_h(manager, db_session)).json()
        assert [j["company_name"] for j in with_done["jobs"]] == ["Done Co"]
        # A finished demo is findable but is NOT counted as outstanding work.
        assert with_done["summary"]["total"] == 0


# ── the workspace ──────────────────────────────────────────────────────────

class TestBuilderWorkspace:
    def _with_discovery(self, db, brand, rep):
        opp = _opp(db, brand, rep, stage=STAGE_DISCOVERY)
        d = DiscoveryRecord(
            opportunity_id=opp.id,
            lead_sources="Land listing sites and word of mouth",
            demo_requirements="Show the enquiry landing and the follow-up",
            structured_json=ds.dump({
                "fields": {"team_size": {"choice": "2_5"}},
                "legacy": {},
            }),
        )
        db.add(d)
        db.commit()
        return opp

    def test_the_job_is_openable_and_hands_over_discovery(
            self, client, db_session, brand, rep):
        opp = self._with_discovery(db_session, brand, rep)
        _request_demo(client, db_session, rep, opp)

        r = client.get("/sales/opportunities/" + opp.id + "/demo-build",
                       headers=_h(rep, db_session))
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["opportunity"]["company_name"] == "Countryside Land Partners"
        assert body["demo"]["status"] == DEMO_REQUESTED
        handoff = {h["key"]: h["value"] for h in body["discovery"]["handoff"]}
        assert "lead_sources" in handoff
        assert "listing sites" in (handoff["lead_sources"] or "")
        # RENDERED, not raw. The browser never sees a bare option value.
        assert (handoff.get("team_size") or "") != "2_5"
        assert body["discovery"]["progress"]["required"] > 0

    def test_the_builder_never_has_to_retype_what_discovery_captured(
            self, client, db_session, brand, rep):
        """Every discovery question reaches the workspace, answered or not."""
        opp = self._with_discovery(db_session, brand, rep)
        _request_demo(client, db_session, rep, opp)
        body = client.get("/sales/opportunities/" + opp.id + "/demo-build",
                          headers=_h(rep, db_session)).json()
        keys = {h["key"] for h in body["discovery"]["handoff"]}
        for expected in ("lead_sources", "demo_requirements"):
            assert expected in keys
        # And the demo requirements carried forward onto the opportunity when
        # the demo was requested, so the brief is on the record too.
        assert (body["demo"]["requirements"] or "").startswith("Show the enquiry")

    def test_the_workspace_offers_the_team_and_the_statuses(
            self, client, db_session, brand, manager, rep):
        opp = _opp(db_session, brand, rep, stage=STAGE_DISCOVERY)
        _request_demo(client, db_session, rep, opp)
        body = client.get("/sales/opportunities/" + opp.id + "/demo-build",
                          headers=_h(manager, db_session)).json()
        assert any(t["full_name"] == "Mike Seller" for t in body["team"])
        assert {s["value"] for s in body["statuses"]} >= {
            "requested", "in_progress", "ready"}
        assert body["can_manage_demo"] is True

    def test_a_rep_who_is_neither_manager_nor_builder_gets_no_controls(
            self, client, db_session, brand, rep):
        opp = _opp(db_session, brand, rep, stage=STAGE_DISCOVERY)
        _request_demo(client, db_session, rep, opp)
        body = client.get("/sales/opportunities/" + opp.id + "/demo-build",
                          headers=_h(rep, db_session)).json()
        assert body["can_manage_demo"] is False

    def test_assigning_the_builder_makes_them_able_to_manage_it(
            self, client, db_session, brand, manager, rep):
        opp = _opp(db_session, brand, rep, stage=STAGE_DISCOVERY)
        _request_demo(client, db_session, rep, opp)
        client.patch("/sales/opportunities/" + opp.id,
                     json={"demo_owner_user_id": rep.id},
                     headers=_h(manager, db_session))
        body = client.get("/sales/opportunities/" + opp.id + "/demo-build",
                          headers=_h(rep, db_session)).json()
        assert body["demo"]["owner_name"] == "Mike Seller"
        assert body["can_manage_demo"] is True


# ── the round trip the defect report describes ─────────────────────────────

class TestTheWholeFlow:
    def test_discovery_to_request_to_queue_to_build_to_ready(
            self, client, db_session, brand, manager, rep):
        opp = _opp(db_session, brand, rep, stage=STAGE_DISCOVERY)
        db_session.add(DiscoveryRecord(
            opportunity_id=opp.id,
            demo_requirements="Show lead capture end to end"))
        db_session.commit()

        # 1. Request the demo.
        _request_demo(client, db_session, rep, opp)

        # 2. It is an individual job in the queue.
        job = client.get("/sales/demo-queue?brand_sales_org_id=" + brand.id,
                         headers=_h(manager, db_session)).json()["jobs"][0]
        assert job["opportunity_id"] == opp.id
        assert job["builder_name"] is None
        assert job["due_at"] is None

        # 3. It opens.
        ws = client.get("/sales/opportunities/" + opp.id + "/demo-build",
                        headers=_h(manager, db_session))
        assert ws.status_code == 200

        # 4. Assign a builder and a target through the endpoint that already
        #    existed — no second write path was created for this screen.
        r = client.patch("/sales/opportunities/" + opp.id,
                         json={"demo_owner_user_id": rep.id,
                               "demo_due_at": "2030-02-01T00:00:00",
                               "demo_status": DEMO_IN_PROGRESS},
                         headers=_h(manager, db_session))
        assert r.status_code == 200, r.text

        # 5. Both surfaces see it.
        job = client.get("/sales/demo-queue?brand_sales_org_id=" + brand.id,
                         headers=_h(manager, db_session)).json()["jobs"][0]
        assert job["builder_name"] == "Mike Seller"
        assert job["due_at"] is not None
        assert job["demo_status"] == DEMO_IN_PROGRESS
        ws = client.get("/sales/opportunities/" + opp.id + "/demo-build",
                        headers=_h(rep, db_session)).json()
        assert ws["demo"]["owner_name"] == "Mike Seller"
        assert ws["demo"]["due_at"] is not None

        # 6. Ready is a deliberate act, and nothing before it claimed ready.
        assert ws["demo"]["ready_at"] is None
        r = client.patch("/sales/opportunities/" + opp.id,
                         json={"demo_status": DEMO_READY},
                         headers=_h(rep, db_session))
        assert r.status_code == 200
        ws = client.get("/sales/opportunities/" + opp.id + "/demo-build",
                        headers=_h(rep, db_session)).json()
        assert ws["demo"]["status"] == DEMO_READY
        assert ws["demo"]["ready_at"] is not None

        # 7. Nothing was published to the prospect by any of that.
        assert ws["demo"]["url"] is None

    def test_no_second_demo_record_was_created(
            self, client, db_session, brand, rep):
        """The queue reads the opportunity's own columns and nothing else."""
        opp = _opp(db_session, brand, rep, stage=STAGE_DISCOVERY)
        _request_demo(client, db_session, rep, opp)

        # Change the column directly; the queue must follow it.
        db_session.query(Opportunity).filter(Opportunity.id == opp.id).update(
            {"demo_status": DEMO_IN_PROGRESS})
        db_session.commit()

        job = client.get("/sales/demo-queue?brand_sales_org_id=" + brand.id,
                         headers=_h(rep, db_session)).json()["jobs"][0]
        assert job["demo_status"] == DEMO_IN_PROGRESS
