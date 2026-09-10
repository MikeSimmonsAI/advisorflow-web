"""
Launch Engine delivery — integrations, testing, training, blockers, and the
gate between all of it and Live.

THE STANDARD, unchanged from test_launch_engine.py: a refusal is only proved
by asserting that NOTHING CHANGED. Auth is never overridden; every request
below carries a real JWT and executes the real dependency chain.

THE ONE THAT MATTERS MOST is TestGoLiveGate::test_a_customer_cannot_make
_themselves_live — the whole point of a gate is that the person it stands in
front of cannot open it.
"""

import io
import itertools
import pathlib

import pytest

from app.models.implementation_models import (
    Implementation, ImplementationMilestone, IMPL_LIVE, MILESTONE_DONE,
    MILESTONE_PENDING,
)
from app.models.launch_delivery_models import (
    APPROVAL_CUSTOMER, APPROVAL_PROVIDER, BLOCKER_OPEN, CHECK_NOT_TESTED,
    CHECK_PASS, INT_VERIFIED,
    ImplementationApproval, ImplementationBlocker, ImplementationCheck,
    ImplementationIntegration, ImplementationTraining, LaunchTemplate,
)
from app.models.models import Organization, Platform, User
from app.services import launch_intake, launch_template
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _org(db, plat, name):
    o = Organization(name=name,
                     slug="%s-%d" % (name.lower().replace(" ", "-"), next(_SEQ)),
                     platform_id=plat.id, plan="standard", is_active=True)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, email):
    u = User(organization_id=(org.id if org else None),
             email="%s-%d@test.local" % (email, next(_SEQ)),
             password_hash=hash_password("TestPass123!"),
             full_name=email.title(), role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _impl(db, org, plat):
    im = Implementation(organization_id=org.id, platform_id=plat.id,
                        opportunity_id="opp-%d" % next(_SEQ),
                        status="not_started")
    db.add(im)
    db.commit()
    return im


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


@pytest.fixture()
def world(db_session):
    """Two customers under one brand — a single tenant cannot fail isolation."""
    plat = Platform(name="Provider Brand", slug="brand-%d" % next(_SEQ),
                    short_name="PB", support_email="launch@example.test")
    db_session.add(plat)
    db_session.commit()

    org_a = _org(db_session, plat, "Alpha Power")
    org_b = _org(db_session, plat, "Beta Energy")
    return {
        "plat": plat, "org_a": org_a, "org_b": org_b,
        "impl_a": _impl(db_session, org_a, plat),
        "impl_b": _impl(db_session, org_b, plat),
        "admin_a": _user(db_session, org_a, "org_admin", "alpha-admin"),
        "admin_b": _user(db_session, org_b, "org_admin", "beta-admin"),
        "god": _user(db_session, None, "god_admin", "owner"),
    }


def _delivery(client, db, world, org):
    """Open the staff surface, which is what seeds the programme."""
    return client.get("/god/launch/%s/delivery" % org.id,
                      headers=_h(db, world["god"]))


# ── seeding ─────────────────────────────────────────────────────────────────

class TestSeeding:
    def test_the_programme_appears_on_first_staff_open(self, client, db_session, world):
        assert db_session.query(ImplementationIntegration).count() == 0
        r = _delivery(client, db_session, world, world["org_a"])
        assert r.status_code == 200
        body = r.json()
        assert len(body["integrations"]) == len(launch_template.DEFAULT_INTEGRATIONS)
        assert len(body["checks"]) == len(launch_template.DEFAULT_CHECKS)
        assert len(body["training"]) == len(launch_template.DEFAULT_TRAINING)

    def test_seeding_twice_adds_nothing_and_disturbs_nothing(
            self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        row = (db_session.query(ImplementationIntegration)
               .filter(ImplementationIntegration.implementation_id
                       == world["impl_a"].id).first())
        client.patch("/god/launch/%s/integrations/%s"
                     % (world["org_a"].id, row.id),
                     json={"status": INT_VERIFIED},
                     headers=_h(db_session, world["god"]))

        before = db_session.query(ImplementationIntegration).count()
        _delivery(client, db_session, world, world["org_a"])
        assert db_session.query(ImplementationIntegration).count() == before

        db_session.expire_all()
        again = (db_session.query(ImplementationIntegration)
                 .filter(ImplementationIntegration.id == row.id).first())
        assert again.status == INT_VERIFIED, "seeding reset work already done"

    def test_every_seeded_row_carries_the_owning_organization(
            self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        for model in (ImplementationIntegration, ImplementationCheck,
                      ImplementationTraining):
            rows = db_session.query(model).all()
            assert rows
            assert {r.organization_id for r in rows} == {world["org_a"].id}


# ── brand configuration ─────────────────────────────────────────────────────

class TestWhiteLabelTemplate:
    def test_a_brand_can_replace_the_programme(self, client, db_session, world):
        r = client.put("/god/launch-templates/%s" % world["plat"].id,
                       json={"name": "Lean launch",
                             "config": {"integrations": [
                                 {"key": "portal", "label": "Partner portal",
                                  "required": True}],
                                 "checks": [], "training": []}},
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        assert r.json()["resolved"]["integrations"][0]["key"] == "portal"

        body = _delivery(client, db_session, world, world["org_a"]).json()
        assert [i["key"] for i in body["integrations"]] == ["portal"]
        assert body["checks"] == []

    def test_a_partial_template_keeps_the_rest_of_the_default(
            self, client, db_session, world):
        client.put("/god/launch-templates/%s" % world["plat"].id,
                   json={"config": {"golive": {"customer_signoff": False}}},
                   headers=_h(db_session, world["god"]))
        resolved = launch_template.resolve(db_session, world["plat"].id)
        assert resolved["golive"]["customer_signoff"] is False
        assert resolved["golive"]["uat_complete"] is True
        assert len(resolved["integrations"]) == len(launch_template.DEFAULT_INTEGRATIONS)

    def test_a_rubbish_template_falls_back_rather_than_emptying_the_gate(
            self, db_session, world):
        db_session.add(LaunchTemplate(platform_id=world["plat"].id,
                                      config={"golive": "not a dict",
                                              "integrations": "nonsense"}))
        db_session.commit()
        resolved = launch_template.resolve(db_session, world["plat"].id)
        assert resolved["golive"] == launch_template.DEFAULT_GOLIVE
        # A string is not a list of rows, so it cleans to nothing — visible on
        # the screen rather than silently reverting to somebody else's default.
        assert resolved["integrations"] == []

    def test_a_brand_with_no_row_still_gets_a_programme(self, db_session, world):
        assert db_session.query(LaunchTemplate).count() == 0
        assert launch_template.resolve(db_session, world["plat"].id)["golive"] \
            == launch_template.DEFAULT_GOLIVE

    def test_only_god_may_read_or_write_a_brand_template(
            self, client, db_session, world):
        h = _h(db_session, world["admin_a"])
        assert client.get("/god/launch-templates/%s" % world["plat"].id,
                          headers=h).status_code == 403
        assert client.put("/god/launch-templates/%s" % world["plat"].id,
                          json={"config": {}}, headers=h).status_code == 403
        assert db_session.query(LaunchTemplate).count() == 0

    def test_an_unknown_brand_is_a_404(self, client, db_session, world):
        assert client.get("/god/launch-templates/nope",
                          headers=_h(db_session, world["god"])).status_code == 404


# ── access and isolation ────────────────────────────────────────────────────

class TestAccess:
    def test_anonymous_is_refused_everywhere(self, client, world):
        for method, path in [
                ("get", "/launch/me/delivery"),
                ("get", "/god/launch/%s/delivery" % world["org_a"].id),
                ("get", "/god/launch/%s/readiness" % world["org_a"].id),
                ("post", "/god/launch/%s/blockers" % world["org_a"].id),
                ("get", "/god/launch-templates/%s" % world["plat"].id)]:
            r = getattr(client, method)(path)
            assert r.status_code in (401, 403), "%s %s was open" % (method, path)

    def test_a_customer_cannot_reach_the_staff_delivery_surface(
            self, client, db_session, world):
        r = client.get("/god/launch/%s/delivery" % world["org_a"].id,
                       headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 403

    def test_a_customer_sees_only_their_own_delivery(self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        r = client.get("/launch/me/delivery", headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 200
        assert r.json()["counts"]["checks_total"] > 0

        # Beta has no programme seeded, so theirs is empty rather than Alpha's.
        r = client.get("/launch/me/delivery", headers=_h(db_session, world["admin_b"]))
        assert r.status_code == 200
        assert r.json()["counts"]["checks_total"] == 0

    def test_one_customer_cannot_act_on_anothers_row(self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        check = (db_session.query(ImplementationCheck)
                 .filter(ImplementationCheck.organization_id == world["org_a"].id)
                 .first())
        client.patch("/god/launch/%s/checks/%s" % (world["org_a"].id, check.id),
                     json={"status": CHECK_PASS},
                     headers=_h(db_session, world["god"]))

        r = client.post("/launch/me/checks/%s/approve" % check.id,
                        headers=_h(db_session, world["admin_b"]))
        assert r.status_code == 404
        db_session.expire_all()
        again = db_session.query(ImplementationCheck).filter(
            ImplementationCheck.id == check.id).first()
        assert again.customer_approved_at is None, "the refusal still wrote"

    def test_staff_cannot_reach_a_row_through_the_wrong_customer(
            self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        _delivery(client, db_session, world, world["org_b"])
        a_check = (db_session.query(ImplementationCheck)
                   .filter(ImplementationCheck.organization_id == world["org_a"].id)
                   .first())
        r = client.patch("/god/launch/%s/checks/%s" % (world["org_b"].id, a_check.id),
                         json={"status": CHECK_PASS},
                         headers=_h(db_session, world["god"]))
        assert r.status_code == 404
        db_session.expire_all()
        assert db_session.query(ImplementationCheck).filter(
            ImplementationCheck.id == a_check.id).first().status == CHECK_NOT_TESTED

    def test_an_org_outside_scope_404s_rather_than_confirming_it_exists(
            self, client, db_session, world):
        r = client.get("/god/launch/does-not-exist/delivery",
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 404


# ── integrations ────────────────────────────────────────────────────────────

class TestIntegrations:
    def _first(self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        return (db_session.query(ImplementationIntegration)
                .filter(ImplementationIntegration.organization_id
                        == world["org_a"].id).first())

    def test_verified_records_who_and_when_and_moving_off_it_clears_that(
            self, client, db_session, world):
        row = self._first(client, db_session, world)
        url = "/god/launch/%s/integrations/%s" % (world["org_a"].id, row.id)
        h = _h(db_session, world["god"])

        r = client.patch(url, json={"status": INT_VERIFIED}, headers=h)
        assert r.status_code == 200
        assert r.json()["verified_at"] is not None
        assert r.json()["settled"] is True

        r = client.patch(url, json={"status": "testing"}, headers=h)
        assert r.json()["verified_at"] is None, "a stale proof survived"
        assert r.json()["settled"] is False

    def test_an_unknown_status_is_refused_and_writes_nothing(
            self, client, db_session, world):
        row = self._first(client, db_session, world)
        r = client.patch("/god/launch/%s/integrations/%s" % (world["org_a"].id, row.id),
                         json={"status": "definitely-not-a-status"},
                         headers=_h(db_session, world["god"]))
        assert r.status_code == 400
        db_session.expire_all()
        assert db_session.query(ImplementationIntegration).filter(
            ImplementationIntegration.id == row.id).first().status == "required"

    def test_a_connection_can_be_added_for_one_customer_only(
            self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        r = client.post("/god/launch/%s/integrations" % world["org_a"].id,
                        json={"key": "billing_portal", "label": "Billing portal"},
                        headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        rows = db_session.query(ImplementationIntegration).filter(
            ImplementationIntegration.key == "billing_portal").all()
        assert len(rows) == 1
        assert rows[0].organization_id == world["org_a"].id

    def test_a_duplicate_key_is_refused(self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        body = {"key": "billing_portal", "label": "Billing portal"}
        h = _h(db_session, world["god"])
        client.post("/god/launch/%s/integrations" % world["org_a"].id, json=body,
                    headers=h)
        r = client.post("/god/launch/%s/integrations" % world["org_a"].id, json=body,
                        headers=h)
        assert r.status_code == 400


# ── UAT ─────────────────────────────────────────────────────────────────────

class TestUAT:
    def _check(self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        return (db_session.query(ImplementationCheck)
                .filter(ImplementationCheck.organization_id == world["org_a"].id)
                .first())

    def test_our_verdict_and_the_customers_approval_are_separate_facts(
            self, client, db_session, world):
        row = self._check(client, db_session, world)
        client.patch("/god/launch/%s/checks/%s" % (world["org_a"].id, row.id),
                     json={"status": CHECK_PASS},
                     headers=_h(db_session, world["god"]))
        db_session.expire_all()
        row = db_session.query(ImplementationCheck).filter(
            ImplementationCheck.id == row.id).first()
        assert row.status == CHECK_PASS
        assert row.customer_approved_at is None, "our test approved for them"

        r = client.post("/launch/me/checks/%s/approve" % row.id,
                        headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 200
        db_session.expire_all()
        assert db_session.query(ImplementationCheck).filter(
            ImplementationCheck.id == row.id).first().customer_approved_at is not None

    def test_a_customer_cannot_approve_something_nobody_has_tested(
            self, client, db_session, world):
        row = self._check(client, db_session, world)
        r = client.post("/launch/me/checks/%s/approve" % row.id,
                        headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 409
        db_session.expire_all()
        assert db_session.query(ImplementationCheck).filter(
            ImplementationCheck.id == row.id).first().customer_approved_at is None

    def test_a_retest_withdraws_the_customers_approval(
            self, client, db_session, world):
        row = self._check(client, db_session, world)
        url = "/god/launch/%s/checks/%s" % (world["org_a"].id, row.id)
        h = _h(db_session, world["god"])
        client.patch(url, json={"status": CHECK_PASS}, headers=h)
        client.post("/launch/me/checks/%s/approve" % row.id,
                    headers=_h(db_session, world["admin_a"]))
        client.patch(url, json={"status": "retest"}, headers=h)
        db_session.expire_all()
        again = db_session.query(ImplementationCheck).filter(
            ImplementationCheck.id == row.id).first()
        assert again.customer_approved_at is None, \
            "an approval of a state the customer never saw survived"


# ── training ────────────────────────────────────────────────────────────────

class TestTraining:
    def _training(self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        return (db_session.query(ImplementationTraining)
                .filter(ImplementationTraining.organization_id == world["org_a"].id)
                .first())

    def test_schedule_attendees_complete_then_acknowledge(
            self, client, db_session, world):
        row = self._training(client, db_session, world)
        url = "/god/launch/%s/training/%s" % (world["org_a"].id, row.id)
        h = _h(db_session, world["god"])

        r = client.patch(url, json={"scheduled_at": "2030-01-05T15:00:00",
                                    "attendees": [{"name": "A Person",
                                                   "email": "a@example.test"},
                                                  {"name": ""}]},
                         headers=h)
        assert r.status_code == 200
        assert len(r.json()["attendees"]) == 1, "a nameless attendee was kept"

        # Not delivered yet, so there is nothing to acknowledge.
        assert client.post("/launch/me/training/%s/acknowledge" % row.id,
                           headers=_h(db_session, world["admin_a"])).status_code == 409

        client.patch(url, json={"completed": True}, headers=h)
        r = client.post("/launch/me/training/%s/acknowledge" % row.id,
                        headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 200

    def test_un_completing_a_session_withdraws_its_acknowledgement(
            self, client, db_session, world):
        row = self._training(client, db_session, world)
        url = "/god/launch/%s/training/%s" % (world["org_a"].id, row.id)
        h = _h(db_session, world["god"])
        client.patch(url, json={"completed": True}, headers=h)
        client.post("/launch/me/training/%s/acknowledge" % row.id,
                    headers=_h(db_session, world["admin_a"]))
        client.patch(url, json={"completed": False}, headers=h)
        db_session.expire_all()
        again = db_session.query(ImplementationTraining).filter(
            ImplementationTraining.id == row.id).first()
        assert again.customer_acknowledged_at is None


# ── blockers ────────────────────────────────────────────────────────────────

class TestBlockers:
    def test_an_internal_blocker_never_reaches_the_customer(
            self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        client.post("/god/launch/%s/blockers" % world["org_a"].id,
                    json={"title": "Colleague on leave",
                          "detail": "Internal staffing note",
                          "party": "provider"},
                    headers=_h(db_session, world["god"]))
        r = client.get("/launch/me/delivery", headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 200
        assert r.json()["blockers"] == []
        assert "staffing" not in r.text.lower()

    def test_a_customer_visible_blocker_reaches_them_without_its_internal_detail(
            self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        client.post("/god/launch/%s/blockers" % world["org_a"].id,
                    json={"title": "Domain access outstanding",
                          "detail": "INTERNAL: chase their IT person again",
                          "party": "customer", "customer_visible": True,
                          "customer_action": "Send us your registrar login"},
                    headers=_h(db_session, world["god"]))
        r = client.get("/launch/me/delivery", headers=_h(db_session, world["admin_a"]))
        body = r.json()
        assert len(body["blockers"]) == 1
        assert body["blockers"][0]["action"] == "Send us your registrar login"
        assert "INTERNAL" not in r.text
        assert any(a["kind"] == "blocker" for a in body["actions"])

    def test_resolving_closes_it_and_records_the_resolution(
            self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        made = client.post("/god/launch/%s/blockers" % world["org_a"].id,
                           json={"title": "Waiting on partner",
                                 "party": "partner"},
                           headers=_h(db_session, world["god"])).json()
        r = client.post("/god/launch/%s/blockers/%s/resolve"
                        % (world["org_a"].id, made["id"]),
                        json={"resolution": "They answered"},
                        headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        assert r.json()["status"] == "resolved"
        assert r.json()["resolution"] == "They answered"

    def test_a_blocker_needs_a_title_and_a_known_party(
            self, client, db_session, world):
        _delivery(client, db_session, world, world["org_a"])
        h = _h(db_session, world["god"])
        assert client.post("/god/launch/%s/blockers" % world["org_a"].id,
                           json={"title": "  "}, headers=h).status_code == 400
        assert client.post("/god/launch/%s/blockers" % world["org_a"].id,
                           json={"title": "x", "party": "martians"},
                           headers=h).status_code == 400
        assert db_session.query(ImplementationBlocker).count() == 0


# ── the go-live gate ────────────────────────────────────────────────────────

def _complete_intake(client, db, user, *, with_secrets=True):
    """Answer every required question and upload one document.

    Driven by the SERVER's schema rather than a hand-written list, so a new
    required field cannot make this helper quietly stop completing the intake.
    """
    h = _h(db, user)
    for step in launch_intake.STEP_SCHEMA:
        answers = {}
        for f in step["fields"]:
            if f["kind"] == launch_intake.KIND_SECRET:
                if with_secrets:
                    answers[f["key"]] = "s3cret-%s" % f["key"]
                continue
            if not f["required"]:
                continue
            if f["kind"] == launch_intake.KIND_CHECKBOX:
                answers[f["key"]] = True
            elif f["kind"] == launch_intake.KIND_SELECT:
                answers[f["key"]] = f["options"][0]["value"]
            elif f["kind"] == launch_intake.KIND_EMAIL:
                answers[f["key"]] = "person@example.test"
            elif f["kind"] == launch_intake.KIND_DATE:
                answers[f["key"]] = "2030-01-01"
            else:
                answers[f["key"]] = "Answer for %s" % f["key"]
        if step["key"] == "files":
            client.post("/launch/me/files",
                        files={"file": ("brand.png", io.BytesIO(b"png-bytes"),
                                        "image/png")},
                        data={"step_key": "files"}, headers=h)
        if answers:
            r = client.put("/launch/me/steps/%s" % step["key"],
                           json={"answers": answers}, headers=h)
            assert r.status_code == 200, r.text
    return client.post("/launch/me/submit", json={}, headers=h)


class TestGoLiveGate:
    def test_readiness_is_computed_and_starts_unmet(self, client, db_session, world):
        r = client.get("/god/launch/%s/readiness" % world["org_a"].id,
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is False
        keys = {i["key"] for i in body["items"]}
        assert keys == set(launch_template.GOLIVE_KEYS)
        # Every item explains itself. A gate that says only "no" is a gate
        # somebody works around.
        assert all(i["detail"] for i in body["items"])

    def test_a_customer_submitting_their_intake_does_not_make_them_live(
            self, client, db_session, world):
        assert _complete_intake(client, db_session, world["admin_a"]).status_code == 200
        db_session.expire_all()
        impl = db_session.query(Implementation).filter(
            Implementation.id == world["impl_a"].id).first()
        assert impl.status != IMPL_LIVE
        assert impl.launched_at is None

        r = client.get("/god/launch/%s/readiness" % world["org_a"].id,
                       headers=_h(db_session, world["god"]))
        body = r.json()
        assert body["ready"] is False
        by_key = {i["key"]: i for i in body["items"]}
        assert by_key["intake_submitted"]["ok"] is True
        assert by_key["files_received"]["ok"] is True
        assert by_key["access_received"]["ok"] is True
        assert by_key["provider_signoff"]["ok"] is False

    def test_a_customer_cannot_make_themselves_live(self, client, db_session, world):
        _complete_intake(client, db_session, world["admin_a"])
        h = _h(db_session, world["admin_a"])
        for method, path, body in [
                ("post", "/god/ops/implementations/%s/launch" % world["impl_a"].id,
                 {"acknowledge_warnings": True}),
                ("post", "/god/launch/%s/approvals/%s"
                 % (world["org_a"].id, APPROVAL_PROVIDER), {}),
                ("post", "/god/launch/%s/approvals/%s"
                 % (world["org_a"].id, APPROVAL_CUSTOMER), {})]:
            r = getattr(client, method)(path, json=body, headers=h)
            assert r.status_code in (401, 403, 404), "%s was open to a customer" % path
        db_session.expire_all()
        assert db_session.query(ImplementationApproval).count() == 0
        assert db_session.query(Implementation).filter(
            Implementation.id == world["impl_a"].id).first().status != IMPL_LIVE

    def test_launch_is_refused_until_the_gate_is_acknowledged(
            self, client, db_session, world):
        r = client.post("/god/ops/implementations/%s/launch" % world["impl_a"].id,
                        json={"acknowledge_warnings": False},
                        headers=_h(db_session, world["god"]))
        assert r.status_code == 409
        warnings = r.json()["detail"]["warnings"]
        assert any("Testing complete" in w for w in warnings)
        assert any("go live" in w.lower() for w in warnings)
        db_session.expire_all()
        assert db_session.query(Implementation).filter(
            Implementation.id == world["impl_a"].id).first().status != IMPL_LIVE

    def test_a_fully_satisfied_gate_reads_ready_and_launch_goes_through(
            self, client, db_session, world):
        gh = _h(db_session, world["god"])
        org_id = world["org_a"].id
        impl_id = world["impl_a"].id

        _complete_intake(client, db_session, world["admin_a"])
        _delivery(client, db_session, world, world["org_a"])

        for row in db_session.query(ImplementationIntegration).filter(
                ImplementationIntegration.implementation_id == impl_id).all():
            client.patch("/god/launch/%s/integrations/%s" % (org_id, row.id),
                         json={"status": INT_VERIFIED}, headers=gh)
        for row in db_session.query(ImplementationCheck).filter(
                ImplementationCheck.implementation_id == impl_id).all():
            client.patch("/god/launch/%s/checks/%s" % (org_id, row.id),
                         json={"status": CHECK_PASS}, headers=gh)
        for row in db_session.query(ImplementationTraining).filter(
                ImplementationTraining.implementation_id == impl_id).all():
            client.patch("/god/launch/%s/training/%s" % (org_id, row.id),
                         json={"completed": True}, headers=gh)
        for kind in (APPROVAL_CUSTOMER, APPROVAL_PROVIDER):
            client.post("/god/launch/%s/approvals/%s" % (org_id, kind),
                        json={"given_name": "A Named Person"}, headers=gh)

        body = client.get("/god/launch/%s/readiness" % org_id, headers=gh).json()
        assert body["ready"] is True, [i for i in body["outstanding"]]

        # The gate is satisfied; the PRE-EXISTING launch warnings are separate
        # and still apply. An implementation with nobody accountable for it is
        # one of them, and it should be — the gate did not replace that check,
        # it joined it.
        impl = db_session.query(Implementation).filter(
            Implementation.id == impl_id).first()
        impl.owner_user_id = world["god"].id
        db_session.commit()

        r = client.post("/god/ops/implementations/%s/launch" % impl_id,
                        json={"acknowledge_warnings": False}, headers=gh)
        assert r.status_code == 200, r.text
        db_session.expire_all()
        impl = db_session.query(Implementation).filter(
            Implementation.id == impl_id).first()
        assert impl.status == IMPL_LIVE
        assert impl.launched_at is not None
        assert impl.launched_by == world["god"].id

    def test_go_live_is_audited(self, client, db_session, world):
        from app.models.models import AuditLogEntry
        client.post("/god/ops/implementations/%s/launch" % world["impl_a"].id,
                    json={"acknowledge_warnings": True},
                    headers=_h(db_session, world["god"]))
        rows = (db_session.query(AuditLogEntry)
                .filter(AuditLogEntry.action == "customer_marked_live").all())
        assert len(rows) == 1
        assert rows[0].organization_id == world["org_a"].id

    def test_withdrawing_an_approval_reopens_the_gate(self, client, db_session, world):
        gh = _h(db_session, world["god"])
        org_id = world["org_a"].id
        client.post("/god/launch/%s/approvals/%s" % (org_id, APPROVAL_PROVIDER),
                    json={"given_name": "Someone"}, headers=gh)
        assert db_session.query(ImplementationApproval).count() == 1
        r = client.delete("/god/launch/%s/approvals/%s" % (org_id, APPROVAL_PROVIDER),
                          headers=gh)
        assert r.status_code == 200
        assert db_session.query(ImplementationApproval).count() == 0

    def test_an_unknown_approval_kind_is_refused(self, client, db_session, world):
        r = client.post("/god/launch/%s/approvals/mystery" % world["org_a"].id,
                        json={}, headers=_h(db_session, world["god"]))
        assert r.status_code == 400
        assert db_session.query(ImplementationApproval).count() == 0

    def test_a_brand_can_turn_a_requirement_off_without_hiding_it(
            self, client, db_session, world):
        client.put("/god/launch-templates/%s" % world["plat"].id,
                   json={"config": {"golive": {"customer_signoff": False}}},
                   headers=_h(db_session, world["god"]))
        body = client.get("/god/launch/%s/readiness" % world["org_a"].id,
                          headers=_h(db_session, world["god"])).json()
        item = next(i for i in body["items"] if i["key"] == "customer_signoff")
        assert item["required"] is False
        assert item["ok"] is False, "turning a requirement off must not fake it"
        assert item["key"] not in {i["key"] for i in body["outstanding"]}


# ── milestones still count ──────────────────────────────────────────────────

class TestBuildItemsStillGate:
    def test_an_open_required_milestone_keeps_the_gate_shut(
            self, client, db_session, world):
        db_session.add(ImplementationMilestone(
            implementation_id=world["impl_a"].id, key="kickoff",
            label="Kickoff call", is_required=True, status=MILESTONE_PENDING))
        db_session.commit()
        body = client.get("/god/launch/%s/readiness" % world["org_a"].id,
                          headers=_h(db_session, world["god"])).json()
        item = next(i for i in body["items"] if i["key"] == "milestones_complete")
        assert item["ok"] is False
        assert "Kickoff call" in item["detail"]

    def test_settling_it_satisfies_that_item(self, client, db_session, world):
        m = ImplementationMilestone(
            implementation_id=world["impl_a"].id, key="kickoff",
            label="Kickoff call", is_required=True, status=MILESTONE_DONE)
        db_session.add(m)
        db_session.commit()
        body = client.get("/god/launch/%s/readiness" % world["org_a"].id,
                          headers=_h(db_session, world["god"])).json()
        item = next(i for i in body["items"] if i["key"] == "milestones_complete")
        assert item["ok"] is True


# ── legacy and honesty ──────────────────────────────────────────────────────

class TestLegacyAndHonesty:
    def test_a_launch_with_no_delivery_rows_still_loads(
            self, client, db_session, world):
        """The state every implementation created before this feature is in."""
        assert db_session.query(ImplementationIntegration).count() == 0
        r = client.get("/god/launch/%s" % world["org_b"].id,
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        r = client.get("/launch/me/delivery", headers=_h(db_session, world["admin_b"]))
        assert r.status_code == 200
        assert r.json()["counts"]["checks_total"] == 0

    def test_a_customer_with_no_launch_at_all_gets_the_same_honest_404(
            self, client, db_session, world):
        lonely = _org(db_session, world["plat"], "No Impl Co")
        u = _user(db_session, lonely, "org_admin", "lonely")
        assert client.get("/launch/me/delivery",
                          headers=_h(db_session, u)).status_code == 404

    def test_nothing_claims_to_be_connected_that_has_not_been_verified(
            self, client, db_session, world):
        body = _delivery(client, db_session, world, world["org_a"]).json()
        assert all(i["status"] == "required" for i in body["integrations"])
        assert all(i["settled"] is False for i in body["integrations"])
        assert body["progress"]["integrations"]["required_done"] == 0

    def test_the_delivery_modules_name_no_brand_and_no_customer(self):
        """The same guard test_launch_engine.py applies, extended to these files.

        The first customer configured must be a row, not a branch. This is
        cheap to keep true and expensive to restore once it is not.
        """
        root = pathlib.Path(__file__).resolve().parents[1]
        for rel in ("app/services/launch_delivery.py",
                    "app/services/launch_template.py",
                    "app/models/launch_delivery_models.py"):
            text = (root / rel).read_text(encoding="utf-8").lower()
            assert "atlantis" not in text, "%s names a customer" % rel
            assert "comparepower" not in text, "%s names a customer's partner" % rel
            for brand in ("evosys", "bookaboost"):
                assert ('== "%s' % brand) not in text
                assert ("== '%s" % brand) not in text


class TestCustomerAccessIsVisible:
    """A launch nobody has been invited to is not a launch that is 0% done.

    It has not started, and that fact was invisible on a screen otherwise full
    of green ticks about our own work.
    """

    def test_the_staff_surface_says_whether_the_customer_can_get_in(
            self, client, db_session, world):
        body = _delivery(client, db_session, world, world["org_a"]).json()
        assert "access" in body
        # org_a has an active admin but no invitation on record.
        assert body["access"]["state"] == "users_exist"
        assert body["access"]["active_users"] == 1
        assert body["access"]["invitations"] == []

    def test_a_customer_with_no_users_at_all_reads_as_not_invited(
            self, client, db_session, world):
        plat = world["plat"]
        empty = _org(db_session, plat, "Nobody Home")
        _impl(db_session, empty, plat)
        body = _delivery(client, db_session, world, empty).json()
        assert body["access"]["state"] == "not_invited"
        assert body["access"]["active_users"] == 0

    def test_no_activation_token_is_ever_returned(self, client, db_session, world):
        from datetime import datetime, timedelta
        from app.models.implementation_models import CustomerActivation
        db_session.add(CustomerActivation(
            user_id=world["admin_a"].id, organization_id=world["org_a"].id,
            implementation_id=world["impl_a"].id,
            token_prefix="pfx-visible", token_hash="hash-of-a-secret",
            status="pending",
            expires_at=datetime.utcnow() + timedelta(days=7),
            send_count=1, last_sent_at=datetime.utcnow()))
        db_session.commit()

        r = _delivery(client, db_session, world, world["org_a"])
        assert r.json()["access"]["state"] == "invited"
        # Neither half of the token appears, and neither does the field name.
        assert "hash-of-a-secret" not in r.text
        assert "pfx-visible" not in r.text
        assert "token" not in r.text.lower()


class TestTheListShowsWhatIsStuck:
    """A launch list nobody has to open to see what is blocked."""

    def test_an_open_blocker_appears_on_the_list_with_whose_move_it_is(
            self, client, db_session, world):
        gh = _h(db_session, world["god"])
        _delivery(client, db_session, world, world["org_a"])
        client.post("/god/launch/%s/blockers" % world["org_a"].id,
                    json={"title": "Registrar login outstanding",
                          "party": "customer"}, headers=gh)

        rows = client.get("/god/launch", headers=gh).json()["launches"]
        mine = next(r for r in rows
                    if r["organization_id"] == world["org_a"].id)
        assert any("Registrar login outstanding" in b for b in mine["blockers"])
        assert any("Waiting on customer" in b for b in mine["blockers"])

        # And it belongs to that customer only.
        other = next(r for r in rows
                     if r["organization_id"] == world["org_b"].id)
        assert not any("Registrar" in b for b in other["blockers"])

    def test_a_resolved_blocker_leaves_the_list(self, client, db_session, world):
        gh = _h(db_session, world["god"])
        _delivery(client, db_session, world, world["org_a"])
        made = client.post("/god/launch/%s/blockers" % world["org_a"].id,
                           json={"title": "Partner has not replied",
                                 "party": "partner"}, headers=gh).json()
        client.post("/god/launch/%s/blockers/%s/resolve"
                    % (world["org_a"].id, made["id"]),
                    json={"resolution": "They replied"}, headers=gh)
        rows = client.get("/god/launch", headers=gh).json()["launches"]
        mine = next(r for r in rows
                    if r["organization_id"] == world["org_a"].id)
        assert not any("Partner has not replied" in b for b in mine["blockers"])
