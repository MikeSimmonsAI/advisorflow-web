"""STARTING A LAUNCH FOR A CUSTOMER WHO ALREADY EXISTS.

THE PRODUCTION DEFECT THIS FILE LOCKS SHUT
==========================================
A real customer existed in production under a real brand, and did not appear
in God Mode → Customer Launches at all. Not as "not started" — not at all. The
operator reasonably concluded the customer had never been created.

Three things were true at once:

  1. `Implementation.opportunity_id` was NOT NULL with a foreign key into
     `opportunities`, so the ONLY way to hold a launch record was to have been
     converted from a Won deal.
  2. The only code that created an Implementation was the Won → Provision
     path. Operator-created customers got an Organization and nothing else.
  3. Customer Launches was built by listing implementations, so a customer
     without one was omitted rather than shown as unstarted.

Together: a customer created by an operator could never be onboarded, and the
screen whose whole job is to report who has not been onboarded was the one
place they could not be seen.

WHAT THE FIX MUST NEVER BECOME
==============================
Starting a launch must stay a checklist and nothing else. The moment it
invites somebody, sends something, configures billing, seeds data or marks a
milestone done, an internal act has reached outside the company or has lied
about progress. `TestStartCreatesNothingElse` counts every one of those.
"""

import itertools

import pytest

from app.models.implementation_models import (
    Implementation, ImplementationMilestone, IMPL_NOT_STARTED,
    MILESTONE_PENDING,
)
from app.models.launch_intake_models import (
    LaunchIntakeFile, LaunchIntakeStep, LaunchIntakeSubmission,
)
from app.models.models import AuditLogEntry, Organization, Platform, User
from app.models.sales_models import Opportunity
from app.services import implementation_service as impl_svc
from app.services import industry_templates
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _platform(db, name="Brand One"):
    p = Platform(name=name, slug="p-%d" % next(_SEQ), short_name="B1",
                 tagline="t", support_email="s@example.test")
    db.add(p)
    db.commit()
    return p


def _org(db, plat, name, industry=None):
    kwargs = {} if industry is None else {"industry": industry}
    o = Organization(name=name, slug="o-%d" % next(_SEQ), platform_id=plat.id,
                     plan="standard", is_active=True, **kwargs)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, label):
    u = User(organization_id=(org.id if org else None),
             email="%s-%d@test.local" % (label, next(_SEQ)),
             password_hash=hash_password("TestPass123!"),
             full_name=label.title(), role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


@pytest.fixture()
def world(db_session):
    """A customer that exists and has never been started. The real shape of
    the defect: nothing is wrong with the customer, only with what the
    platform is able to record about them."""
    db = db_session
    plat = _platform(db)
    return {
        "plat": plat,
        "org": _org(db, plat, "Existing Customer", industry="energy"),
        "other": _org(db, plat, "Another Customer"),
        "god": _user(db, None, "god_admin", "owner"),
    }


def _counts(db):
    return {
        "users": db.query(User).count(),
        "orgs": db.query(Organization).count(),
        "opportunities": db.query(Opportunity).count(),
        "implementations": db.query(Implementation).count(),
        "intake_steps": db.query(LaunchIntakeStep).count(),
        "intake_files": db.query(LaunchIntakeFile).count(),
        "submissions": db.query(LaunchIntakeSubmission).count(),
    }


# ════════════════════════════════════════════════════════════════════════════
# 1. THE CUSTOMER IS VISIBLE EVEN BEFORE THEY ARE STARTED
# ════════════════════════════════════════════════════════════════════════════

class TestUnstartedCustomersAreVisible:
    def test_a_customer_with_no_launch_still_appears_in_the_list(
            self, client, db_session, world):
        """THE BUG, DIRECTLY. This list was built from implementations, so a
        customer who had never been given one vanished from the one screen
        that reports who has not been onboarded."""
        body = client.get("/god/launch",
                          headers=_h(db_session, world["god"])).json()
        names = [r["organization_name"] for r in body["launches"]]
        assert "Existing Customer" in names
        row = next(r for r in body["launches"]
                   if r["organization_name"] == "Existing Customer")
        assert row["launch_started"] is False
        assert row["implementation_id"] is None

    def test_an_unstarted_customer_reports_zero_and_never_invents_progress(
            self, client, db_session, world):
        body = client.get("/god/launch",
                          headers=_h(db_session, world["god"])).json()
        row = next(r for r in body["launches"]
                   if r["organization_name"] == "Existing Customer")
        assert row["intake_pct"] == 0
        assert row["overall_pct"] == 0
        assert row["implementation_pct"] == 0
        assert row["implementation_settled"] == 0
        assert row["intake_state"] == "not_started"

    def test_the_row_says_what_is_actually_wrong(self, client, db_session,
                                                  world):
        body = client.get("/god/launch",
                          headers=_h(db_session, world["god"])).json()
        row = next(r for r in body["launches"]
                   if r["organization_name"] == "Existing Customer")
        assert any("not been started" in b.lower() for b in row["blockers"])

    def test_the_list_counts_them_so_nobody_has_to_scroll_to_notice(
            self, client, db_session, world):
        body = client.get("/god/launch",
                          headers=_h(db_session, world["god"])).json()
        assert body["not_started_count"] == 2

    def test_the_customers_own_brand_is_named_not_guessed(
            self, client, db_session, world):
        body = client.get("/god/launch",
                          headers=_h(db_session, world["god"])).json()
        row = next(r for r in body["launches"]
                   if r["organization_name"] == "Existing Customer")
        assert row["brand_name"] == "Brand One"
        assert row["platform_id"] == world["plat"].id


# ════════════════════════════════════════════════════════════════════════════
# 2. STARTING ONE
# ════════════════════════════════════════════════════════════════════════════

class TestStart:
    def test_a_customer_with_no_deal_can_be_given_a_launch(
            self, client, db_session, world):
        """The constraint that made this impossible: opportunity_id was NOT
        NULL, so a customer who never came from a deal could not hold a launch
        record at all."""
        r = client.post("/god/launch/%s/start" % world["org"].id, json={},
                        headers=_h(db_session, world["god"]))
        assert r.status_code == 200, r.text
        assert r.json()["created"] is True

        db_session.expire_all()
        impl = impl_svc.for_organization(db_session, world["org"].id)
        assert impl is not None
        assert impl.opportunity_id is None, "a deal was invented to satisfy the key"
        assert impl.organization_id == world["org"].id
        assert impl.platform_id == world["plat"].id
        assert impl.status == IMPL_NOT_STARTED

    def test_no_fake_opportunity_appears_in_the_pipeline(
            self, client, db_session, world):
        """A synthetic deal would sit in the sales pipeline, be counted in Won
        metrics and be attributed to a rep who sold nothing."""
        before = db_session.query(Opportunity).count()
        client.post("/god/launch/%s/start" % world["org"].id, json={},
                    headers=_h(db_session, world["god"]))
        db_session.expire_all()
        assert db_session.query(Opportunity).count() == before == 0

    def test_it_seeds_the_same_checklist_the_sales_path_seeds(
            self, client, db_session, world):
        from app.services.provisioning import milestone_template
        client.post("/god/launch/%s/start" % world["org"].id, json={},
                    headers=_h(db_session, world["god"]))
        db_session.expire_all()
        impl = impl_svc.for_organization(db_session, world["org"].id)
        rows = (db_session.query(ImplementationMilestone)
                .filter(ImplementationMilestone.implementation_id == impl.id)
                .order_by(ImplementationMilestone.position).all())
        assert [m.key for m in rows] == [m["key"] for m in milestone_template(None)]

    def test_every_milestone_starts_pending(self, client, db_session, world):
        """A launch record that arrives with progress on it is a lie told to
        the person reading the percentage."""
        client.post("/god/launch/%s/start" % world["org"].id, json={},
                    headers=_h(db_session, world["god"]))
        db_session.expire_all()
        impl = impl_svc.for_organization(db_session, world["org"].id)
        rows = (db_session.query(ImplementationMilestone)
                .filter(ImplementationMilestone.implementation_id == impl.id).all())
        assert rows
        assert {m.status for m in rows} == {MILESTONE_PENDING}

    def test_nobody_is_made_the_owner_by_clicking(self, client, db_session,
                                                   world):
        """Selling, provisioning and implementing are different jobs."""
        client.post("/god/launch/%s/start" % world["org"].id, json={},
                    headers=_h(db_session, world["god"]))
        db_session.expire_all()
        impl = impl_svc.for_organization(db_session, world["org"].id)
        assert impl.owner_user_id is None

    def test_starting_twice_does_not_create_a_second_or_reset_the_first(
            self, client, db_session, world):
        head = _h(db_session, world["god"])
        first = client.post("/god/launch/%s/start" % world["org"].id, json={},
                            headers=head).json()
        # The customer gets on with their intake.
        db_session.add(LaunchIntakeStep(
            implementation_id=first["implementation_id"],
            organization_id=world["org"].id, step_key="company",
            answers={"legalName": "Stated by the customer"}))
        db_session.commit()

        second = client.post("/god/launch/%s/start" % world["org"].id, json={},
                             headers=head).json()
        assert second["created"] is False
        assert second["implementation_id"] == first["implementation_id"]

        db_session.expire_all()
        assert db_session.query(Implementation).count() == 1
        rows = db_session.query(LaunchIntakeStep).all()
        assert len(rows) == 1
        assert rows[0].answers == {"legalName": "Stated by the customer"}

    def test_the_act_is_audited_against_the_customer(self, client, db_session,
                                                     world):
        client.post("/god/launch/%s/start" % world["org"].id,
                    json={"reason": "Bringing an existing customer onboard"},
                    headers=_h(db_session, world["god"]))
        db_session.expire_all()
        rows = (db_session.query(AuditLogEntry).filter(
            AuditLogEntry.action == "implementation_started_for_existing_customer"
        ).all())
        assert len(rows) == 1
        assert rows[0].organization_id == world["org"].id
        assert rows[0].actor_user_id == world["god"].id

    def test_after_starting_the_customer_is_listed_as_started(
            self, client, db_session, world):
        head = _h(db_session, world["god"])
        client.post("/god/launch/%s/start" % world["org"].id, json={},
                    headers=head)
        body = client.get("/god/launch", headers=head).json()
        row = next(r for r in body["launches"]
                   if r["organization_name"] == "Existing Customer")
        assert row["launch_started"] is True
        assert row["implementation_id"]
        # And still honestly at zero — starting is not progress.
        assert row["intake_pct"] == 0

    def test_the_onboarding_experience_becomes_reachable(
            self, client, db_session, world):
        """The point of the whole exercise: before, the preview 404'd because
        there was no launch record to compose from."""
        head = _h(db_session, world["god"])
        before = client.get("/launch-experience/preview/" + world["org"].id,
                            headers=head)
        assert before.status_code == 404

        client.post("/god/launch/%s/start" % world["org"].id, json={},
                    headers=head)

        after = client.get("/launch-experience/preview/" + world["org"].id,
                           headers=head)
        assert after.status_code == 200
        assert after.json()["preview_context"]["read_only"] is True


# ════════════════════════════════════════════════════════════════════════════
# 3. STARTING CREATES NOTHING ELSE
# ════════════════════════════════════════════════════════════════════════════

class TestStartCreatesNothingElse:
    def test_it_invites_nobody_and_creates_no_user(self, client, db_session,
                                                    world):
        before = sorted(u.email for u in db_session.query(User).all())
        r = client.post("/god/launch/%s/start" % world["org"].id, json={},
                        headers=_h(db_session, world["god"]))
        assert r.json()["invitation_sent"] is False
        db_session.expire_all()
        assert sorted(u.email for u in db_session.query(User).all()) == before

    def test_it_configures_no_billing(self, client, db_session, world):
        r = client.post("/god/launch/%s/start" % world["org"].id, json={},
                        headers=_h(db_session, world["god"]))
        assert r.json()["billing_configured"] is False
        db_session.expire_all()
        impl = impl_svc.for_organization(db_session, world["org"].id)
        assert impl.billing_status == "not_configured"
        assert impl.recurring_amount in (None, 0)
        assert impl.package_id is None

    def test_it_seeds_no_intake_data(self, client, db_session, world):
        r = client.post("/god/launch/%s/start" % world["org"].id, json={},
                        headers=_h(db_session, world["god"]))
        assert r.json()["data_seeded"] is False
        db_session.expire_all()
        assert db_session.query(LaunchIntakeStep).count() == 0
        assert db_session.query(LaunchIntakeFile).count() == 0
        assert db_session.query(LaunchIntakeSubmission).count() == 0

    def test_the_only_rows_it_adds_are_the_launch_and_its_checklist(
            self, client, db_session, world):
        before = _counts(db_session)
        client.post("/god/launch/%s/start" % world["org"].id, json={},
                    headers=_h(db_session, world["god"]))
        db_session.expire_all()
        after = _counts(db_session)
        assert after["implementations"] == before["implementations"] + 1
        for key in ("users", "orgs", "opportunities", "intake_steps",
                    "intake_files", "submissions"):
            assert after[key] == before[key], key

    def test_it_does_not_touch_the_customers_own_record(self, client,
                                                        db_session, world):
        org = world["org"]
        before = (org.name, org.slug, org.industry, org.plan, org.platform_id)
        client.post("/god/launch/%s/start" % org.id, json={},
                    headers=_h(db_session, world["god"]))
        db_session.expire_all()
        org = db_session.query(Organization).filter(
            Organization.id == world["org"].id).first()
        assert (org.name, org.slug, org.industry, org.plan,
                org.platform_id) == before


# ════════════════════════════════════════════════════════════════════════════
# 4. AUTHORITY
# ════════════════════════════════════════════════════════════════════════════

class TestAuthority:
    def test_anonymous_cannot_start_a_launch(self, client, world):
        r = client.post("/god/launch/%s/start" % world["org"].id, json={})
        assert r.status_code in (401, 403)

    def test_a_customer_admin_cannot_start_their_own_launch(
            self, client, db_session, world):
        """Starting a launch is a control-plane act. A customer deciding they
        are being onboarded is not the same as the company deciding it."""
        admin = _user(db_session, world["org"], "org_admin", "customer-admin")
        r = client.post("/god/launch/%s/start" % world["org"].id, json={},
                        headers=_h(db_session, admin))
        assert r.status_code in (403, 404)
        db_session.expire_all()
        assert db_session.query(Implementation).count() == 0

    def test_an_unknown_customer_is_not_confirmed_to_exist(
            self, client, db_session, world):
        r = client.post("/god/launch/does-not-exist/start", json={},
                        headers=_h(db_session, world["god"]))
        assert r.status_code == 404
        db_session.expire_all()
        assert db_session.query(Implementation).count() == 0


# ════════════════════════════════════════════════════════════════════════════
# 5. IT DOES NOT HAPPEN AGAIN
# ════════════════════════════════════════════════════════════════════════════

class TestNewCustomersArriveOnboardable:
    def test_creating_a_customer_gives_them_a_launch(self, client, db_session,
                                                     world):
        """The durable half of the fix. A customer who exists is a customer
        with a launch — otherwise the next one is invisible too."""
        r = client.post("/god/customers",
                        json={"name": "Brand New Co",
                              "platform_id": world["plat"].id,
                              "industry": "roofing"},
                        headers=_h(db_session, world["god"]))
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["launch"]["created"] is True
        assert body["launch"]["invitation_sent"] is False

        org_id = body["customer"]["id"]
        db_session.expire_all()
        assert impl_svc.for_organization(db_session, org_id) is not None

    def test_a_new_customer_is_immediately_previewable(self, client,
                                                       db_session, world):
        head = _h(db_session, world["god"])
        org_id = client.post("/god/customers",
                             json={"name": "Previewable Co",
                                   "platform_id": world["plat"].id,
                                   "industry": "energy"},
                             headers=head).json()["customer"]["id"]
        r = client.get("/launch-experience/preview/" + org_id, headers=head)
        assert r.status_code == 200
        assert r.json()["experience"]["industry"]["key"] == "energy"

    def test_creating_a_customer_still_invites_nobody(self, client,
                                                      db_session, world):
        before = db_session.query(User).count()
        client.post("/god/customers",
                    json={"name": "Quiet Co", "platform_id": world["plat"].id},
                    headers=_h(db_session, world["god"]))
        db_session.expire_all()
        assert db_session.query(User).count() == before

    def test_an_unstated_industry_is_neutral_and_never_a_vertical(
            self, client, db_session, world):
        """THE OTHER SHIPPED DEFECT, in this router's own contract: the create
        payload defaulted `industry` to "funeral", so every customer created
        without stating a business type became a funeral home."""
        from app.routers.customers_router import CustomerCreate
        assert CustomerCreate.model_fields["industry"].default == \
            industry_templates.GENERIC_KEY

        org_id = client.post("/god/customers",
                             json={"name": "Unstated Co",
                                   "platform_id": world["plat"].id},
                             headers=_h(db_session, world["god"])
                             ).json()["customer"]["id"]
        db_session.expire_all()
        org = db_session.query(Organization).filter(
            Organization.id == org_id).first()
        assert org.industry == industry_templates.GENERIC_KEY
        assert "funeral" not in (org.industry or "")
