"""
Seller Opportunity UX — the guarantees the rebuild is allowed to be judged on.

WHAT THIS SUITE DEFENDS
-----------------------
The Opportunity page stopped being fourteen textareas and became structured
controls with progressive disclosure. That is a PRESENTATION change, and the
whole risk of a presentation change is that it quietly eats data or quietly
widens authority. So:

 1. An opportunity carrying only legacy long-form discovery still renders, and
    still counts as answered.
 2. Structured answers save and reload with the ticks intact.
 3. The long-form text a rep wrote before any of this existed survives the same
    field being re-answered structurally.
 4. Progress is real: answered / required / what is missing.
 5. The demo summary still carries status, builder, due date and link.
 6. `can_manage_demo` is the SERVER's answer — a rep who is neither manager nor
    the assigned builder does not get the internal demo controls.
 7. The proposal projection still answers.
 8. The billing block on the payload is untouched — setup and subscription are
    still two separate obligations resolved by the billing engine.
 9. Stage behaviour is unchanged, including the demo-requirements carry-forward.
10. Tenant and authority boundaries are exactly what they were.
11. A browser cannot invent option values, question keys, or a way to null a
    column it never showed anybody.
"""
import itertools
import json
import pytest

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, Opportunity, DiscoveryRecord,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    STAGE_PROSPECT, STAGE_DISCOVERY, STAGE_DEMO_BUILD,
)
from app.services import discovery_schema as ds
from app.services.auth_service import hash_password, create_access_token

_SEQ = itertools.count(1)


# ═══════════════════════════════════════════════════════════
# Fixtures — same shape as tests/test_sales_workspace.py
# ═══════════════════════════════════════════════════════════

@pytest.fixture()
def platform(db_session):
    p = Platform(name="EvoSys Pro", slug="oppux-%d" % next(_SEQ))
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def brand(db_session, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="EvoSys Sales",
                      slug="oppux-sales-%d" % next(_SEQ))
    db_session.add(b)
    db_session.commit()
    return b


@pytest.fixture()
def brand_b(db_session, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="Other Sales",
                      slug="oppux-sales-b-%d" % next(_SEQ))
    db_session.add(b)
    db_session.commit()
    return b


def _user(db, email=None):
    u = User(organization_id=None,
             email=email or "oppux%d@evosys.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name="Sales Person",
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


def _headers(user, db):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def _opp(db, brand, owner=None, company="Acme Facilities", stage=STAGE_PROSPECT):
    o = Opportunity(brand_sales_org_id=brand.id,
                    owner_user_id=owner.id if owner else None,
                    company_name=company, stage=stage)
    db.add(o)
    db.commit()
    return o


@pytest.fixture()
def rep(db_session):
    return _user(db_session)


@pytest.fixture()
def rep_headers(db_session, rep, brand):
    _member(db_session, rep, brand, ROLE_SALES_REP)
    return _headers(rep, db_session)


@pytest.fixture()
def manager(db_session):
    return _user(db_session, email="oppuxmgr%d@evosys.live" % next(_SEQ))


@pytest.fixture()
def manager_headers(db_session, manager, brand):
    _member(db_session, manager, brand, ROLE_SALES_MANAGER)
    return _headers(manager, db_session)


LEGACY_TEXT = ("They get most work from word of mouth and a Google Business "
               "listing that nobody has touched in two years. Long form, typed "
               "in a hurry, and it is the only record of that call.")


# ═══════════════════════════════════════════════════════════
# 1. Legacy discovery still renders
# ═══════════════════════════════════════════════════════════

class TestLegacyDiscoveryRenders:
    def test_opportunity_with_only_longform_discovery_loads(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep, company="Legacy Co")
        db_session.add(DiscoveryRecord(opportunity_id=opp.id,
                                       lead_sources=LEGACY_TEXT,
                                       business_description="Two vans, one office."))
        db_session.commit()

        r = client.get("/sales/opportunities/%s" % opp.id, headers=rep_headers)
        assert r.status_code == 200
        disc = r.json()["discovery"]
        # The prose is still there, unchanged, under its own key.
        assert disc["lead_sources"] == LEGACY_TEXT
        # And nothing was invented on its behalf.
        assert disc["structured"] == {}
        assert disc["legacy"] == {}

    def test_longform_counts_towards_progress(
            self, client, db_session, brand, rep, rep_headers):
        """A deal captured before structured discovery is not 'unstarted'."""
        opp = _opp(db_session, brand, owner=rep)
        db_session.add(DiscoveryRecord(opportunity_id=opp.id,
                                       lead_sources=LEGACY_TEXT))
        db_session.commit()
        prog = client.get("/sales/opportunities/%s" % opp.id,
                          headers=rep_headers).json()["discovery"]["progress"]
        assert prog["answered"] == 1
        assert prog["required"] == len(ds.REQUIRED_KEYS)
        assert "lead_sources" not in [m["key"] for m in prog["missing"]]

    def test_schema_is_served_to_the_screen(self, client, db_session, brand,
                                            rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep)
        body = client.get("/sales/opportunities/%s" % opp.id,
                          headers=rep_headers).json()
        schema = body["discovery_schema"]
        assert len(schema) == len(ds.SCHEMA)
        keys = [f["key"] for f in schema]
        # Every question maps onto a column discovery already had.
        assert set(keys) == {k for k, _ in DiscoveryRecord.FIELDS}
        multi = [f for f in schema if f["control"] == "multi"]
        assert multi and all(f.get("options") for f in multi)


# ═══════════════════════════════════════════════════════════
# 2. Structured answers save and reload
# ═══════════════════════════════════════════════════════════

class TestStructuredRoundTrip:
    def test_save_and_reload(self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep, stage=STAGE_DISCOVERY)
        r = client.put(
            "/sales/opportunities/%s/discovery" % opp.id,
            headers=rep_headers,
            json={"structured": {
                "lead_sources": {"options": ["website", "referrals", "other"],
                                 "other": "Chamber of commerce"},
                "team_size": {"options": ["6-10"]},
            }})
        assert r.status_code == 200
        disc = r.json()["discovery"]
        assert disc["structured"]["lead_sources"]["options"] == [
            "website", "referrals", "other"]
        assert disc["structured"]["lead_sources"]["other"] == "Chamber of commerce"

        # Reloading the record gives back the same ticks.
        again = client.get("/sales/opportunities/%s" % opp.id,
                           headers=rep_headers).json()["discovery"]
        assert again["structured"] == disc["structured"]

    def test_structured_answer_is_rendered_into_the_legacy_column(
            self, client, db_session, brand, rep, rep_headers):
        """Everything downstream reads prose. It must keep finding prose."""
        opp = _opp(db_session, brand, owner=rep)
        body = client.put(
            "/sales/opportunities/%s/discovery" % opp.id, headers=rep_headers,
            json={"structured": {"bottlenecks": {
                "options": ["missed_calls", "slow_response"],
                "note": "Phones ring out after 5pm."}}}).json()
        text = body["discovery"]["bottlenecks"]
        assert "Missed calls" in text
        assert "Slow lead response" in text
        assert "Phones ring out after 5pm." in text

    def test_composite_question_round_trips(self, client, db_session, brand,
                                            rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep)
        body = client.put(
            "/sales/opportunities/%s/discovery" % opp.id, headers=rep_headers,
            json={"structured": {"appointment_process": {"parts": {
                "books_today": {"options": ["yes"]},
                "who_books": {"options": ["receptionist"]},
                "volume_month": "40",
            }}}}).json()
        disc = body["discovery"]
        assert disc["structured"]["appointment_process"]["parts"][
            "books_today"]["options"] == ["yes"]
        assert "Appointments per month: 40" in disc["appointment_process"]

    def test_mark_complete_still_stamps_the_lifecycle(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep)
        body = client.put(
            "/sales/opportunities/%s/discovery" % opp.id, headers=rep_headers,
            json={"structured": {"team_size": {"options": ["2-5"]}},
                  "mark_complete": True}).json()
        assert body["discovery"]["completed_at"] is not None
        assert body["lifecycle"]["discovery_completed_at"] is not None

    def test_longform_body_still_accepted(self, client, db_session, brand,
                                          rep, rep_headers):
        """The old shape is not broken. A client may still send strings."""
        opp = _opp(db_session, brand, owner=rep)
        body = client.put("/sales/opportunities/%s/discovery" % opp.id,
                          headers=rep_headers,
                          json={"business_goals": "Book more funerals"}).json()
        assert body["discovery"]["business_goals"] == "Book more funerals"


# ═══════════════════════════════════════════════════════════
# 3. Nothing long-form is lost
# ═══════════════════════════════════════════════════════════

class TestLegacyPreserved:
    def test_prior_longform_is_snapshotted_not_destroyed(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep)
        db_session.add(DiscoveryRecord(opportunity_id=opp.id,
                                       lead_sources=LEGACY_TEXT))
        db_session.commit()

        body = client.put(
            "/sales/opportunities/%s/discovery" % opp.id, headers=rep_headers,
            json={"structured": {"lead_sources": {"options": ["website"]}}}).json()
        disc = body["discovery"]
        # The column now carries the structured sentence …
        assert disc["lead_sources"] == "Website"
        # … and the paragraph that used to be there is still readable.
        assert disc["legacy"]["lead_sources"] == LEGACY_TEXT

    def test_second_structured_save_does_not_overwrite_the_snapshot(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep)
        db_session.add(DiscoveryRecord(opportunity_id=opp.id,
                                       lead_sources=LEGACY_TEXT))
        db_session.commit()
        url = "/sales/opportunities/%s/discovery" % opp.id
        client.put(url, headers=rep_headers,
                   json={"structured": {"lead_sources": {"options": ["website"]}}})
        body = client.put(url, headers=rep_headers,
                          json={"structured": {"lead_sources": {
                              "options": ["google"]}}}).json()
        assert body["discovery"]["legacy"]["lead_sources"] == LEGACY_TEXT
        assert body["discovery"]["lead_sources"] == "Google"

    def test_untouched_questions_are_never_nulled(
            self, client, db_session, brand, rep, rep_headers):
        """A form that posts every key must not erase the thirteen it did not
        show anybody. This is the failure mode that would lose real notes."""
        opp = _opp(db_session, brand, owner=rep)
        db_session.add(DiscoveryRecord(opportunity_id=opp.id,
                                       business_description=LEGACY_TEXT,
                                       current_process="They call back when they can."))
        db_session.commit()
        empty_everything = {k: {} for k, _ in DiscoveryRecord.FIELDS}
        empty_everything["team_size"] = {"options": ["11-25"]}
        body = client.put("/sales/opportunities/%s/discovery" % opp.id,
                          headers=rep_headers,
                          json={"structured": empty_everything}).json()
        disc = body["discovery"]
        assert disc["business_description"] == LEGACY_TEXT
        assert disc["current_process"] == "They call back when they can."
        assert disc["team_size"] == "11–25"


# ═══════════════════════════════════════════════════════════
# 4. Progress
# ═══════════════════════════════════════════════════════════

class TestProgress:
    def test_progress_counts_and_names_what_is_missing(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep)
        url = "/sales/opportunities/%s/discovery" % opp.id
        body = client.put(url, headers=rep_headers, json={"structured": {
            "lead_sources": {"options": ["website"]},
            "team_size": {"options": ["2-5"]},
        }}).json()
        prog = body["discovery"]["progress"]
        assert prog["answered"] == 2
        assert prog["required"] == len(ds.REQUIRED_KEYS)
        assert prog["complete"] is False
        missing = [m["key"] for m in prog["missing"]]
        assert "lead_sources" not in missing
        assert "demo_requirements" in missing
        assert all(m["label"] for m in prog["missing"])

    def test_progress_completes_when_every_required_question_is_answered(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep)
        payload = {}
        for key in ds.REQUIRED_KEYS:
            spec = ds.BY_KEY[key]
            if spec["control"] == "composite":
                first = spec["parts"][0]
                payload[key] = {"parts": {
                    first["key"]: {"options": [first["options"][0]["value"]]}}}
            else:
                payload[key] = {"options": [spec["options"][0]["value"]]}
        body = client.put("/sales/opportunities/%s/discovery" % opp.id,
                          headers=rep_headers,
                          json={"structured": payload}).json()
        prog = body["discovery"]["progress"]
        assert prog["complete"] is True
        assert prog["answered"] == prog["required"]
        assert prog["missing"] == []


# ═══════════════════════════════════════════════════════════
# 5 & 6. Demo summary, and who may drive the build
# ═══════════════════════════════════════════════════════════

class TestDemoSummaryAndAuthority:
    def test_demo_summary_fields_are_all_present(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep, stage=STAGE_DEMO_BUILD)
        opp.demo_status = "in_progress"
        opp.demo_url = "https://demo.example.com/acme"
        db_session.commit()
        demo = client.get("/sales/opportunities/%s" % opp.id,
                          headers=rep_headers).json()["demo"]
        for key in ("status", "owner_user_id", "owner_name", "requested_at",
                    "due_at", "ready_at", "requirements", "url", "notes"):
            assert key in demo
        assert demo["status"] == "in_progress"
        assert demo["url"] == "https://demo.example.com/acme"

    def test_plain_rep_does_not_get_the_internal_demo_controls(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep, stage=STAGE_DEMO_BUILD)
        body = client.get("/sales/opportunities/%s" % opp.id,
                          headers=rep_headers).json()
        assert body["can_manage_demo"] is False

    def test_the_assigned_builder_does(self, client, db_session, brand, rep,
                                       rep_headers):
        opp = _opp(db_session, brand, owner=rep, stage=STAGE_DEMO_BUILD)
        opp.demo_owner_user_id = rep.id
        db_session.commit()
        body = client.get("/sales/opportunities/%s" % opp.id,
                          headers=rep_headers).json()
        assert body["can_manage_demo"] is True

    def test_a_manager_does(self, client, db_session, brand, manager_headers):
        opp = _opp(db_session, brand, stage=STAGE_DEMO_BUILD)
        body = client.get("/sales/opportunities/%s" % opp.id,
                          headers=manager_headers).json()
        assert body["can_manage_demo"] is True

    def test_hiding_a_control_is_not_the_access_control(
            self, client, db_session, brand, brand_b, rep, rep_headers):
        """A rep from another brand is refused by the endpoint, not by the UI."""
        other = _opp(db_session, brand_b, company="Hidden Corp")
        r = client.patch("/sales/opportunities/%s" % other.id,
                         headers=rep_headers, json={"demo_notes": "mine now"})
        assert r.status_code in (403, 404)


# ═══════════════════════════════════════════════════════════
# 7 & 8. Proposal projection and the billing block
# ═══════════════════════════════════════════════════════════

class TestProposalAndBilling:
    def test_closing_projection_still_answers(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep)
        r = client.get("/sales/opportunities/%s/closing" % opp.id,
                       headers=rep_headers)
        assert r.status_code == 200
        body = r.json()
        for key in ("proposal", "portal", "warnings", "next_action"):
            assert key in body

    def test_billing_block_is_untouched(self, client, db_session, brand, rep,
                                        rep_headers):
        """This thread did not rewrite the billing engine, and the payload it
        produces has to prove it."""
        opp = _opp(db_session, brand, owner=rep)
        body = client.get("/sales/opportunities/%s" % opp.id,
                          headers=rep_headers).json()
        assert "billing" in body
        assert "implementation_fee" in body
        assert "billing_option" in body


# ═══════════════════════════════════════════════════════════
# 9. Lifecycle behaviour is unchanged
# ═══════════════════════════════════════════════════════════

class TestLifecycleUnchanged:
    def test_moving_to_demo_build_stamps_and_carries_requirements_forward(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep, stage=STAGE_DISCOVERY)
        client.put("/sales/opportunities/%s/discovery" % opp.id,
                   headers=rep_headers,
                   json={"structured": {"demo_requirements": {
                       "options": ["sms_automation", "calendar_booking"]}}})
        body = client.patch("/sales/opportunities/%s" % opp.id,
                            headers=rep_headers,
                            json={"stage": STAGE_DEMO_BUILD}).json()
        assert body["stage"] == STAGE_DEMO_BUILD
        assert body["lifecycle"]["demo_requested_at"] is not None
        assert body["demo"]["status"] == "requested"
        # The structured answer arrives at the builder as readable requirements.
        assert "SMS automation" in body["demo"]["requirements"]

    def test_stage_move_is_still_recorded_on_the_timeline(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep)
        body = client.patch("/sales/opportunities/%s" % opp.id,
                            headers=rep_headers,
                            json={"stage": STAGE_DISCOVERY}).json()
        kinds = [e["event_type"] for e in body["timeline"]]
        assert "stage_changed" in kinds


# ═══════════════════════════════════════════════════════════
# 10. Tenant and authority boundaries
# ═══════════════════════════════════════════════════════════

class TestBoundaries:
    def test_discovery_requires_auth(self, client, db_session, brand):
        opp = _opp(db_session, brand)
        r = client.put("/sales/opportunities/%s/discovery" % opp.id,
                       json={"structured": {}})
        assert r.status_code == 401

    def test_cross_brand_discovery_write_is_refused(
            self, client, db_session, brand, brand_b, rep, rep_headers):
        other = _opp(db_session, brand_b, company="Hidden Corp")
        r = client.put("/sales/opportunities/%s/discovery" % other.id,
                       headers=rep_headers,
                       json={"structured": {"team_size": {"options": ["1"]}}})
        assert r.status_code in (403, 404)

    def test_cross_brand_read_is_refused(self, client, db_session, brand,
                                         brand_b, rep, rep_headers):
        other = _opp(db_session, brand_b, company="Hidden Corp")
        r = client.get("/sales/opportunities/%s" % other.id, headers=rep_headers)
        assert r.status_code in (403, 404)


# ═══════════════════════════════════════════════════════════
# 11. The browser is not trusted
# ═══════════════════════════════════════════════════════════

class TestSanitisation:
    def test_unknown_option_values_are_dropped(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep)
        body = client.put("/sales/opportunities/%s/discovery" % opp.id,
                          headers=rep_headers,
                          json={"structured": {"lead_sources": {
                              "options": ["website", "smuggled_value"]}}}).json()
        assert body["discovery"]["structured"]["lead_sources"]["options"] == ["website"]

    def test_unknown_question_keys_are_ignored(
            self, client, db_session, brand, rep, rep_headers):
        opp = _opp(db_session, brand, owner=rep)
        body = client.put("/sales/opportunities/%s/discovery" % opp.id,
                          headers=rep_headers,
                          json={"structured": {
                              "team_size": {"options": ["1"]},
                              "secret_admin_flag": {"options": ["yes"]}}}).json()
        assert "secret_admin_flag" not in body["discovery"]["structured"]

    def test_single_choice_keeps_only_one(self, client, db_session, brand, rep,
                                          rep_headers):
        opp = _opp(db_session, brand, owner=rep)
        body = client.put("/sales/opportunities/%s/discovery" % opp.id,
                          headers=rep_headers,
                          json={"structured": {"team_size": {
                              "options": ["1", "2-5", "100+"]}}}).json()
        assert body["discovery"]["structured"]["team_size"]["options"] == ["1"]

    def test_unparseable_side_car_reads_as_empty_rather_than_failing(
            self, client, db_session, brand, rep, rep_headers):
        """Discovery must never fail to LOAD because of how it was stored."""
        opp = _opp(db_session, brand, owner=rep)
        db_session.add(DiscoveryRecord(opportunity_id=opp.id,
                                       structured_json="{not json at all"))
        db_session.commit()
        r = client.get("/sales/opportunities/%s" % opp.id, headers=rep_headers)
        assert r.status_code == 200
        assert r.json()["discovery"]["structured"] == {}


# ═══════════════════════════════════════════════════════════
# The adapter itself
# ═══════════════════════════════════════════════════════════

class TestDiscoverySchemaModule:
    def test_every_question_maps_to_a_real_column(self):
        columns = {k for k, _ in DiscoveryRecord.FIELDS}
        assert {f["key"] for f in ds.SCHEMA} <= columns

    def test_render_puts_other_text_in_place_of_the_word_other(self):
        out = ds.render("lead_sources",
                        {"options": ["website", "other"], "other": "Yelp"})
        assert out == "Website, Yelp"

    def test_render_is_empty_for_an_empty_answer(self):
        assert ds.render("lead_sources", {}) == ""
        assert ds.render("lead_sources", None) == ""
        assert ds.render("no_such_field", {"options": ["x"]}) == ""

    def test_load_tolerates_every_broken_shape(self):
        for raw in (None, "", "[]", "null", "{oops", '"a string"'):
            state = ds.load(raw)
            assert state == {"fields": {}, "legacy": {}}

    def test_dump_load_round_trip(self):
        state = {"fields": {"team_size": {"options": ["2-5"]}},
                 "legacy": {"team_size": "about five"}}
        assert ds.load(ds.dump(state)) == state
        # And it is plain JSON, readable by anything.
        assert json.loads(ds.dump(state))["legacy"]["team_size"] == "about five"

    def test_free_text_is_length_capped(self):
        out = ds.sanitize({"bottlenecks": {"options": ["other"],
                                           "other": "x" * 5000,
                                           "note": "y" * 5000}})
        assert len(out["bottlenecks"]["other"]) == 500
        assert len(out["bottlenecks"]["note"]) == 2000

    def test_auto_migrate_carries_the_new_column(self):
        import app.auto_migrate as am
        assert ("discovery_records", "structured_json", "TEXT") in am.COLUMNS_TO_ADD
