"""The commercial HTTP surface — isolation, authority, resume, and refusals.

Auth dependencies are NOT overridden. Every request below carries a real JWT
for a real user, so `get_current_user`, the workspace scope resolution and the
commercial authority table all execute for real. An isolation test that stubs
authentication proves nothing about isolation.

EVERY REFUSAL ALSO ASSERTS THAT NOTHING CHANGED.
"""

import itertools
from datetime import date

import pytest

from app.models.commercial_models import (
    AG_TERMS_REQUIRED, CommercialAgreement, CommercialTerm,
    OnboardingMilestoneOverride, TYPE_REVENUE_SHARE,
)
from app.models.implementation_models import (
    Implementation, ImplementationMilestone, MILESTONE_PENDING,
)
from app.models.models import Organization, Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, Opportunity, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    SCOPE_BRAND_SALES_ORG,
)
from app.services.auth_service import create_access_token, hash_password
from app.services.commercial import agreements as ag

_SEQ = itertools.count(1)


def _platform(db, name):
    p = Platform(name=name, slug="p-%d" % next(_SEQ), short_name="P",
                 tagline="t", support_email="s@example.test")
    db.add(p)
    db.commit()
    return p


def _bso(db, plat):
    b = BrandSalesOrg(platform_id=plat.id, name="%s Sales" % plat.name,
                      slug="bso-%d" % next(_SEQ))
    db.add(b)
    db.commit()
    return b


def _org(db, plat, name):
    o = Organization(name=name, slug="o-%d" % next(_SEQ), platform_id=plat.id,
                     plan="standard", is_active=True)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, label, platform_id=None):
    u = User(organization_id=(org.id if org else None),
             email="%s-%d@test.local" % (label, next(_SEQ)),
             password_hash=hash_password("TestPass123!"),
             full_name=label.title(), role=role, must_change_password=False)
    if platform_id is not None:
        u.platform_id = platform_id
    db.add(u)
    db.commit()
    return u


def _member(db, user, bso, role):
    db.add(Membership(user_id=user.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=bso.id, role=role, is_active=True))
    db.commit()


def _impl(db, org, plat, bso, opp=None):
    im = Implementation(organization_id=org.id, platform_id=plat.id,
                        brand_sales_org_id=bso.id,
                        opportunity_id=(opp.id if opp else "opp-%d" % next(_SEQ)),
                        status="not_started")
    db.add(im)
    db.commit()
    for i, key in enumerate(["business_profile", "customer_users", "calendar",
                             "lead_import", "launch"]):
        db.add(ImplementationMilestone(implementation_id=im.id, key=key,
                                       label=key.replace("_", " ").title(),
                                       position=i, is_required=(key != "lead_import"),
                                       status=MILESTONE_PENDING))
    db.commit()
    return im


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


@pytest.fixture()
def world(db_session):
    plat_a = _platform(db_session, "Brand One")
    plat_b = _platform(db_session, "Brand Two")
    bso_a, bso_b = _bso(db_session, plat_a), _bso(db_session, plat_b)
    org_a = _org(db_session, plat_a, "Alpha Utilities")
    org_b = _org(db_session, plat_b, "Beta Energy")

    god = _user(db_session, None, "god_admin", "owner")
    manager_a = _user(db_session, None, "user", "mgr-a", platform_id=plat_a.id)
    rep_a = _user(db_session, None, "user", "rep-a", platform_id=plat_a.id)
    manager_b = _user(db_session, None, "user", "mgr-b", platform_id=plat_b.id)
    _member(db_session, manager_a, bso_a, ROLE_SALES_MANAGER)
    _member(db_session, rep_a, bso_a, ROLE_SALES_REP)
    _member(db_session, manager_b, bso_b, ROLE_SALES_MANAGER)

    admin_a = _user(db_session, org_a, "org_admin", "alpha-admin")
    admin_b = _user(db_session, org_b, "org_admin", "beta-admin")

    opp_a = Opportunity(brand_sales_org_id=bso_a.id, company_name="Alpha Utilities",
                        contact_name="A Contact", email="a@example.test",
                        phone="555-0101")
    db_session.add(opp_a)
    db_session.commit()

    impl_a = _impl(db_session, org_a, plat_a, bso_a, opp_a)
    impl_b = _impl(db_session, org_b, plat_b, bso_b)

    agr_a = ag.create(db_session, god, platform_id=plat_a.id,
                      brand_sales_org_id=bso_a.id, organization_id=org_a.id,
                      opportunity_id=opp_a.id, implementation_id=impl_a.id,
                      agreement_type=TYPE_REVENUE_SHARE,
                      name="Alpha arrangement", effective_date=date.today())
    agr_b = ag.create(db_session, god, platform_id=plat_b.id,
                      brand_sales_org_id=bso_b.id, organization_id=org_b.id,
                      implementation_id=impl_b.id,
                      agreement_type=TYPE_REVENUE_SHARE,
                      name="Beta arrangement", effective_date=date.today())
    db_session.commit()

    return locals()


# ── access ──────────────────────────────────────────────────────────────────

class TestAccess:
    def test_anonymous_is_refused_everywhere(self, client, world):
        paths = [("get", "/commercial/config"), ("get", "/commercial/me"),
                 ("get", "/commercial/me/onboarding"),
                 ("get", "/commercial/agreements"),
                 ("get", "/commercial/agreements/%s" % world["agr_a"].id),
                 ("get", "/commercial/onboarding/%s" % world["org_a"].id)]
        for method, path in paths:
            r = getattr(client, method)(path)
            assert r.status_code in (401, 403), "%s %s was open" % (method, path)

    def test_customer_sees_their_own_arrangement_in_plain_language(
            self, client, db_session, world):
        r = client.get("/commercial/me", headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 200
        body = r.json()
        assert body["has_agreement"] is True
        assert body["headline"] == "Revenue share"
        assert body["status_line"] == "Some terms still need to be completed."
        # no internal vocabulary, no economics
        assert "allocation" not in body
        assert "blocking" not in body
        keys = {q["key"] for q in body["questions"]}
        assert "share_basis" in keys
        assert "allocation_rule" not in keys      # internal only
        assert "attribution_rule" not in keys

    def test_a_customer_cannot_read_the_internal_view_of_their_own_agreement(
            self, client, db_session, world):
        r = client.get("/commercial/agreements/%s" % world["agr_a"].id,
                       headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 404


# ── tenant and brand isolation ──────────────────────────────────────────────

class TestIsolation:
    def test_one_customer_cannot_see_anothers_agreement(
            self, client, db_session, world):
        r = client.get("/commercial/me", headers=_h(db_session, world["admin_b"]))
        assert r.status_code == 200
        assert r.json()["id"] == world["agr_b"].id

        r2 = client.get("/commercial/agreements/%s" % world["agr_a"].id,
                        headers=_h(db_session, world["admin_b"]))
        assert r2.status_code == 404

    def test_a_manager_of_another_brand_gets_404_not_403(
            self, client, db_session, world):
        r = client.get("/commercial/agreements/%s" % world["agr_a"].id,
                       headers=_h(db_session, world["manager_b"]))
        assert r.status_code == 404

    def test_the_agreement_list_never_crosses_a_brand(
            self, client, db_session, world):
        r = client.get("/commercial/agreements",
                       headers=_h(db_session, world["manager_a"]))
        assert r.status_code == 200
        ids = {a["id"] for a in r.json()["agreements"]}
        assert world["agr_a"].id in ids
        assert world["agr_b"].id not in ids

    def test_an_org_id_from_another_brand_is_not_addressable(
            self, client, db_session, world):
        r = client.get("/commercial/onboarding/%s" % world["org_b"].id,
                       headers=_h(db_session, world["manager_a"]))
        assert r.status_code == 404

    def test_a_customer_cannot_reach_the_staff_onboarding_surface(
            self, client, db_session, world):
        r = client.get("/commercial/onboarding/%s" % world["org_a"].id,
                       headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 404

    def test_a_cross_tenant_write_changes_nothing(self, client, db_session, world):
        before = (db_session.query(CommercialTerm)
                  .filter(CommercialTerm.agreement_id == world["agr_a"].id).count())
        r = client.put("/commercial/agreements/%s/terms/share_basis"
                       % world["agr_a"].id,
                       json={"value": "gross_collections"},
                       headers=_h(db_session, world["manager_b"]))
        assert r.status_code == 404
        after = (db_session.query(CommercialTerm)
                 .filter(CommercialTerm.agreement_id == world["agr_a"].id).count())
        assert after == before


# ── authority ───────────────────────────────────────────────────────────────

class TestAuthority:
    def _party(self, client, db_session, world):
        r = client.post("/commercial/agreements/%s/parties" % world["agr_a"].id,
                        json={"party_key": "provider",
                              "display_name": "Provider Side",
                              "party_type": "platform_brand"},
                        headers=_h(db_session, world["manager_a"]))
        assert r.status_code == 201
        return r.json()["allocation"]["parties"][0]["party_id"]

    def test_a_rep_cannot_add_a_party_and_none_is_added(
            self, client, db_session, world):
        r = client.post("/commercial/agreements/%s/parties" % world["agr_a"].id,
                        json={"party_key": "provider",
                              "display_name": "Provider Side",
                              "party_type": "platform_brand"},
                        headers=_h(db_session, world["rep_a"]))
        assert r.status_code == 403
        view = client.get("/commercial/agreements/%s" % world["agr_a"].id,
                          headers=_h(db_session, world["manager_a"])).json()
        assert view["allocation"]["parties"] == []

    def test_a_rep_cannot_change_a_percentage(self, client, db_session, world):
        pid = self._party(client, db_session, world)
        r = client.put("/commercial/agreements/%s/parties/%s/allocation"
                       % (world["agr_a"].id, pid),
                       json={"percent": 75},
                       headers=_h(db_session, world["rep_a"]))
        assert r.status_code == 403
        view = client.get("/commercial/agreements/%s" % world["agr_a"].id,
                          headers=_h(db_session, world["manager_a"])).json()
        assert view["allocation"]["parties"][0]["percent"] is None

    def test_a_rep_can_record_what_the_customer_told_them(
            self, client, db_session, world):
        r = client.put("/commercial/agreements/%s/terms/settlement_frequency"
                       % world["agr_a"].id,
                       json={"value": "monthly"},
                       headers=_h(db_session, world["rep_a"]))
        assert r.status_code == 200
        assert r.json()["term"]["value"] == "monthly"

    def test_a_rep_cannot_answer_a_money_term(self, client, db_session, world):
        r = client.put("/commercial/agreements/%s/terms/setup_fee"
                       % world["agr_a"].id,
                       json={"value": 0},
                       headers=_h(db_session, world["rep_a"]))
        assert r.status_code == 403
        rows = (db_session.query(CommercialTerm)
                .filter(CommercialTerm.agreement_id == world["agr_a"].id,
                        CommercialTerm.key == "setup_fee").all())
        assert rows == []

    def test_a_rep_cannot_approve_and_the_status_does_not_move(
            self, client, db_session, world):
        r = client.post("/commercial/agreements/%s/approve" % world["agr_a"].id,
                        json={}, headers=_h(db_session, world["rep_a"]))
        assert r.status_code == 403
        db_session.refresh(world["agr_a"])
        assert world["agr_a"].status == AG_TERMS_REQUIRED
        assert world["agr_a"].approved_at is None

    def test_only_god_may_define_a_question(self, client, db_session, world):
        body = {"key": "custom_thing", "label": "A brand question",
                "kind": "text", "audience": "internal"}
        r = client.put("/commercial/question-definitions/custom_thing",
                       json=body, headers=_h(db_session, world["manager_a"]))
        assert r.status_code == 403
        r2 = client.put("/commercial/question-definitions/custom_thing",
                        json=body, headers=_h(db_session, world["god"]))
        assert r2.status_code == 200


# ── the customer's own questions ────────────────────────────────────────────

class TestCustomerQuestions:
    def test_a_customer_answers_their_own_business_question(
            self, client, db_session, world):
        r = client.put("/commercial/me/questions/share_basis",
                       json={"value": "gross_collections"},
                       headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 200
        answered = {q["key"]: q for q in r.json()["questions"]}
        assert answered["share_basis"]["answered"] is True
        assert answered["share_basis"]["value_label"] == "Gross collections"

    def test_a_customer_cannot_answer_an_internal_question(
            self, client, db_session, world):
        r = client.put("/commercial/me/questions/allocation_rule",
                       json={"value": "full_allocation"},
                       headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 404
        rows = (db_session.query(CommercialTerm)
                .filter(CommercialTerm.agreement_id == world["agr_a"].id,
                        CommercialTerm.key == "allocation_rule").all())
        assert rows == []

    def test_an_invented_answer_is_refused_and_stored_nowhere(
            self, client, db_session, world):
        r = client.put("/commercial/me/questions/settlement_frequency",
                       json={"value": "whenever"},
                       headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 400
        rows = (db_session.query(CommercialTerm)
                .filter(CommercialTerm.agreement_id == world["agr_a"].id,
                        CommercialTerm.key == "settlement_frequency").all())
        assert rows == []

    def test_a_stale_answer_is_refused_and_the_first_one_stands(
            self, client, db_session, world):
        first = client.put("/commercial/me/questions/settlement_frequency",
                           json={"value": "monthly", "revision": 0},
                           headers=_h(db_session, world["admin_a"]))
        assert first.status_code == 200

        # a second editor who loaded the page before the first answer
        stale = client.put("/commercial/me/questions/settlement_frequency",
                           json={"value": "weekly", "revision": 0},
                           headers=_h(db_session, world["admin_a"]))
        assert stale.status_code == 409

        now = client.get("/commercial/me",
                         headers=_h(db_session, world["admin_a"])).json()
        current = {q["key"]: q for q in now["questions"]}["settlement_frequency"]
        assert current["value"] == "monthly"

    def test_answers_survive_leaving_and_coming_back(
            self, client, db_session, world):
        """R — a customer returns to an unfinished onboarding."""
        client.put("/commercial/me/questions/share_basis",
                   json={"value": "net_collections"},
                   headers=_h(db_session, world["admin_a"]))
        client.put("/commercial/me/questions/payment_recipient",
                   json={"value": "provider_entity"},
                   headers=_h(db_session, world["admin_a"]))

        later = client.get("/commercial/me",
                           headers=_h(db_session, world["admin_a"])).json()
        answered = {q["key"]: q["value"] for q in later["questions"]
                    if q["answered"]}
        assert answered["share_basis"] == "net_collections"
        assert answered["payment_recipient"] == "provider_entity"
        assert later["open_question_count"] >= 1
        assert later["answered_question_count"] == len(answered)


# ── onboarding proceeds regardless ──────────────────────────────────────────

class TestOnboardingProceeds:
    def test_the_customer_flow_is_not_blocked_by_missing_terms(
            self, client, db_session, world):
        r = client.get("/commercial/me/onboarding",
                       headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 200
        steps = {s["key"]: s for s in r.json()["steps"]}
        assert steps["commercial_terms"]["status"] == "terms_required"
        for key in ("workspace_setup", "users_roles", "calendar_booking",
                    "lead_data_intake", "ai_workforce_setup", "integrations"):
            assert steps[key]["status"] != "blocked", key
        # the approval step is internal and is not shown to the customer
        assert "agreement_approval" not in steps

    def test_staff_see_why_each_thing_is_blocked_not_merely_that_it_is(
            self, client, db_session, world):
        r = client.get("/commercial/onboarding/%s" % world["org_a"].id,
                       headers=_h(db_session, world["manager_a"]))
        assert r.status_code == 200
        body = r.json()
        blocking = body["agreement"]["blocking"]
        assert blocking["agreement_activation"]["allowed"] is False
        assert blocking["agreement_activation"]["reasons"]
        assert any("has not been answered" in x
                   for x in blocking["agreement_activation"]["reasons"])

    def test_a_revoked_entitlement_does_not_break_onboarding(
            self, client, db_session, world):
        """Q — entitlement revoked mid-onboarding.

        Entitlements are T2's and T8's business. Losing one changes what the
        customer can USE; it does not delete their commercial arrangement or
        stop the implementation being worked on.
        """
        org = world["org_a"]
        org.enabled_features = "[]"
        org.billing_status = "past_due"
        db_session.commit()

        r = client.get("/commercial/onboarding/%s" % org.id,
                       headers=_h(db_session, world["manager_a"]))
        assert r.status_code == 200
        assert r.json()["agreement"]["id"] == world["agr_a"].id

        r2 = client.get("/commercial/me", headers=_h(db_session, world["admin_a"]))
        assert r2.status_code == 200
        assert r2.json()["has_agreement"] is True


# ── overrides over HTTP ─────────────────────────────────────────────────────

class TestOverrideRoutes:
    def test_a_prior_demo_is_recorded_with_a_decider_and_a_reason(
            self, client, db_session, world):
        r = client.post("/commercial/onboarding/%s/overrides" % world["org_a"].id,
                        json={"item_kind": "demo", "item_key": "demo",
                              "mode": "completed_previously",
                              "reason": "Demo was given before this onboarding "
                                        "workflow existed."},
                        headers=_h(db_session, world["manager_a"]))
        assert r.status_code == 201
        body = r.json()
        assert body["mode"] == "completed_previously"
        assert body["decided_by_user_id"] == world["manager_a"].id
        assert body["previously_completed_on"] is None

        flow = client.get("/commercial/onboarding/%s" % world["org_a"].id,
                          headers=_h(db_session, world["manager_a"])).json()
        step = {s["key"]: s for s in flow["steps"]}["demo_discovery"]
        assert step["status"] == "completed_previously"

    def test_an_override_without_a_reason_is_refused_and_none_is_written(
            self, client, db_session, world):
        r = client.post("/commercial/onboarding/%s/overrides" % world["org_a"].id,
                        json={"item_kind": "milestone", "item_key": "lead_import",
                              "mode": "waived", "reason": "  "},
                        headers=_h(db_session, world["manager_a"]))
        assert r.status_code == 400
        assert db_session.query(OnboardingMilestoneOverride).count() == 0

    def test_a_rep_cannot_waive_a_step(self, client, db_session, world):
        r = client.post("/commercial/onboarding/%s/overrides" % world["org_a"].id,
                        json={"item_kind": "milestone", "item_key": "lead_import",
                              "mode": "waived", "reason": "Not needed."},
                        headers=_h(db_session, world["rep_a"]))
        assert r.status_code == 403
        assert db_session.query(OnboardingMilestoneOverride).count() == 0

    def test_a_customer_cannot_waive_their_own_onboarding_step(
            self, client, db_session, world):
        r = client.post("/commercial/onboarding/%s/overrides" % world["org_a"].id,
                        json={"item_kind": "milestone", "item_key": "calendar",
                              "mode": "not_applicable", "reason": "We are fine."},
                        headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 404
        assert db_session.query(OnboardingMilestoneOverride).count() == 0


# ── settlement over HTTP ────────────────────────────────────────────────────

class TestSettlementRoutes:
    def test_a_preview_on_an_unfinished_agreement_produces_no_figures(
            self, client, db_session, world):
        r = client.post("/commercial/agreements/%s/settlement/preview"
                        % world["agr_a"].id,
                        json={"period_start": "2026-01-01",
                              "period_end": "2026-01-31"},
                        headers=_h(db_session, world["manager_a"]))
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "review_required"
        assert body["basis_cents"] is None
        assert body["lines"] == []
        assert body["blocked_reasons"]

    def test_distribution_is_refused_over_http_for_everybody(
            self, client, db_session, world):
        """Not "you may not" — NOBODY may. The owner gets the same refusal as
        a brand manager, because there is no role behind which a payout rail
        appears."""
        for actor in ("manager_a", "god"):
            r = client.post("/commercial/agreements/%s/settlement/any/distribute"
                            % world["agr_a"].id,
                            headers=_h(db_session, world[actor]))
            assert r.status_code == 409, actor
            assert "does not distribute funds" in r.json()["detail"]

    def test_readiness_names_what_is_missing(self, client, db_session, world):
        r = client.get("/commercial/agreements/%s/settlement/readiness"
                       % world["agr_a"].id,
                       headers=_h(db_session, world["manager_a"]))
        assert r.status_code == 200
        body = r.json()
        assert body["agreement_ready"] is False
        assert body["payout_execution_supported"] is False
        assert body["agreement_reasons"]


# ── audit ───────────────────────────────────────────────────────────────────

class TestAudit:
    def test_every_material_change_is_on_the_platform_audit_log(
            self, client, db_session, world):
        client.put("/commercial/agreements/%s/terms/settlement_frequency"
                   % world["agr_a"].id, json={"value": "monthly"},
                   headers=_h(db_session, world["manager_a"]))
        client.post("/commercial/agreements/%s/parties" % world["agr_a"].id,
                    json={"party_key": "provider", "display_name": "Provider",
                          "party_type": "platform_brand"},
                    headers=_h(db_session, world["manager_a"]))
        client.post("/commercial/onboarding/%s/overrides" % world["org_a"].id,
                    json={"item_kind": "demo", "item_key": "demo",
                          "mode": "completed_previously",
                          "reason": "Given before this workflow existed."},
                    headers=_h(db_session, world["manager_a"]))

        r = client.get("/commercial/agreements/%s/audit" % world["agr_a"].id,
                       headers=_h(db_session, world["manager_a"]))
        assert r.status_code == 200
        actions = {e["action"] for e in r.json()["entries"]}
        assert "commercial_agreement_created" in actions
        assert "commercial_term_answered" in actions
        assert "commercial_party_added" in actions
        assert "onboarding_milestone_overridden" in actions
        for entry in r.json()["entries"]:
            assert entry["actor_user_id"]
