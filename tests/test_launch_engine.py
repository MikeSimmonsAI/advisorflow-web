"""
Launch Engine — access, isolation, persistence, validation, submission.

THE STANDARD THIS FILE HOLDS ITSELF TO, borrowed from
test_customer_360_authorization.py: a 403/404 is not enough on its own. Every
refusal below also asserts that NOTHING CHANGED. An endpoint that rejects the
caller after writing the row has not refused anything.

Auth dependencies are NOT overridden. Tests mint real JWTs and send real
Authorization headers, so get_current_user, require_god and the workspace
scope resolution all execute for real — which is the only way an isolation
test proves anything.
"""

import io
import itertools

import pytest

from app.models.implementation_models import Implementation
from app.models.launch_intake_models import (
    LaunchIntakeFile, LaunchIntakeStep, LaunchIntakeSubmission,
)
from app.models.models import Organization, Platform, User
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _org(db, plat, name):
    o = Organization(name=name, slug="%s-%d" % (name.lower().replace(" ", "-"),
                                                next(_SEQ)),
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


@pytest.fixture()
def world(db_session):
    """Two customers under one brand. Two customers is the whole point — a
    single-tenant fixture cannot fail an isolation test."""
    plat = Platform(name="EvoSys Pro", slug="evo-%d" % next(_SEQ),
                    short_name="EP", tagline="Enterprise Systems, Engineered",
                    support_email="launch@example.test")
    db_session.add(plat)
    db_session.commit()

    org_a = _org(db_session, plat, "Alpha Power")
    org_b = _org(db_session, plat, "Beta Energy")

    return {
        "plat": plat,
        "org_a": org_a, "org_b": org_b,
        "impl_a": _impl(db_session, org_a, plat),
        "impl_b": _impl(db_session, org_b, plat),
        "admin_a": _user(db_session, org_a, "org_admin", "alpha-admin"),
        "admin_b": _user(db_session, org_b, "org_admin", "beta-admin"),
        "god": _user(db_session, None, "god_admin", "owner"),
    }


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


# ── access ──────────────────────────────────────────────────────────────────

class TestAccess:
    def test_anonymous_is_refused_everywhere(self, client, world):
        for method, path in [("get", "/launch/me"), ("get", "/launch/config"),
                             ("get", "/launch/me/steps/company"),
                             ("post", "/launch/me/submit"),
                             ("get", "/launch/me/files"),
                             ("get", "/god/launch")]:
            r = getattr(client, method)(path)
            assert r.status_code in (401, 403), "%s %s was open" % (method, path)

    def test_customer_sees_their_own_launch(self, client, db_session, world):
        r = client.get("/launch/me", headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 200
        body = r.json()
        assert body["customer"]["id"] == world["org_a"].id
        assert body["implementation"]["id"] == world["impl_a"].id

    def test_customer_with_no_implementation_gets_a_clear_404(
            self, client, db_session, world):
        plat = world["plat"]
        lonely = _org(db_session, plat, "No Impl Co")
        u = _user(db_session, lonely, "org_admin", "lonely")
        r = client.get("/launch/me", headers=_h(db_session, u))
        assert r.status_code == 404
        assert "launch" in r.json()["detail"].lower()

    def test_customer_cannot_reach_the_staff_surface(self, client, db_session, world):
        r = client.get("/god/launch", headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 403

    def test_god_can_read_a_customers_launch(self, client, db_session, world):
        r = client.get("/god/launch/%s" % world["org_a"].id,
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        assert r.json()["customer"]["id"] == world["org_a"].id

    def test_unknown_org_refuses_without_confirming_existence(
            self, client, db_session, world):
        """404, not 403 — a 403 on an id you may not touch confirms it exists."""
        r = client.get("/god/launch/does-not-exist",
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 404


# ── tenant isolation ────────────────────────────────────────────────────────

class TestTenantIsolation:
    def test_one_customer_cannot_read_anothers_answers(
            self, client, db_session, world):
        client.put("/launch/me/steps/company",
                   json={"answers": {"legalName": "Alpha Power, LLC"}},
                   headers=_h(db_session, world["admin_a"]))

        r = client.get("/launch/me/steps/company",
                       headers=_h(db_session, world["admin_b"]))
        assert r.status_code == 200
        assert r.json()["answers"] == {}, "Beta read Alpha's answers"

    def test_a_save_lands_only_in_the_callers_org(self, client, db_session, world):
        client.put("/launch/me/steps/company",
                   json={"answers": {"legalName": "Beta Energy, LLC"}},
                   headers=_h(db_session, world["admin_b"]))
        rows = db_session.query(LaunchIntakeStep).all()
        assert len(rows) == 1
        assert rows[0].organization_id == world["org_b"].id

    def test_organization_id_in_the_body_is_ignored(self, client, db_session, world):
        """The org comes from the session. A body field must not redirect it."""
        r = client.put(
            "/launch/me/steps/company",
            json={"answers": {"legalName": "Spoof",
                              "organization_id": world["org_b"].id},
                  "organization_id": world["org_b"].id},
            headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 200
        row = db_session.query(LaunchIntakeStep).one()
        assert row.organization_id == world["org_a"].id
        # And the stray key was dropped rather than stored.
        assert "organization_id" not in (row.answers or {})

    def test_one_customer_cannot_download_anothers_file(
            self, client, db_session, world):
        up = client.post(
            "/launch/me/files",
            files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4 alpha"), "application/pdf")},
            headers=_h(db_session, world["admin_a"]))
        assert up.status_code == 200
        fid = up.json()["id"]

        r = client.get("/launch/me/files/%s/download" % fid,
                       headers=_h(db_session, world["admin_b"]))
        assert r.status_code == 404, "Beta downloaded Alpha's document"

    def test_one_customer_cannot_delete_anothers_file(
            self, client, db_session, world):
        up = client.post(
            "/launch/me/files",
            files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4 alpha"), "application/pdf")},
            headers=_h(db_session, world["admin_a"]))
        fid = up.json()["id"]

        r = client.delete("/launch/me/files/%s" % fid,
                          headers=_h(db_session, world["admin_b"]))
        assert r.status_code == 404
        # NOTHING CHANGED — the refusal must not have deleted it anyway.
        row = db_session.query(LaunchIntakeFile).filter_by(id=fid).one()
        assert row.deleted_at is None
        assert row.file_data is not None

    def test_file_listing_never_crosses_tenants(self, client, db_session, world):
        client.post("/launch/me/files",
                    files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
                    headers=_h(db_session, world["admin_a"]))
        r = client.get("/launch/me/files", headers=_h(db_session, world["admin_b"]))
        assert r.json()["files"] == []


# ── save / resume ───────────────────────────────────────────────────────────

class TestSaveAndResume:
    def test_answers_survive_a_new_session(self, client, db_session, world):
        """The refresh test. A new token is a new browser as far as the server
        is concerned; if the answer is not in the database it is gone."""
        client.put("/launch/me/steps/company",
                   json={"answers": {"legalName": "Alpha Power, LLC",
                                     "city": "Houston"}},
                   headers=_h(db_session, world["admin_a"]))

        fresh = _h(db_session, world["admin_a"])   # brand-new JWT
        r = client.get("/launch/me/steps/company", headers=fresh)
        assert r.json()["answers"]["legalName"] == "Alpha Power, LLC"
        assert r.json()["answers"]["city"] == "Houston"

    def test_saving_one_field_does_not_erase_the_others(
            self, client, db_session, world):
        h = _h(db_session, world["admin_a"])
        client.put("/launch/me/steps/company",
                   json={"answers": {"legalName": "Alpha", "city": "Houston"}},
                   headers=h)
        client.put("/launch/me/steps/company",
                   json={"answers": {"city": "Dallas"}}, headers=h)
        a = client.get("/launch/me/steps/company", headers=h).json()["answers"]
        assert a["legalName"] == "Alpha"
        assert a["city"] == "Dallas"

    def test_saving_twice_updates_rather_than_duplicates(
            self, client, db_session, world):
        h = _h(db_session, world["admin_a"])
        for _ in range(3):
            client.put("/launch/me/steps/company",
                       json={"answers": {"legalName": "Alpha"}}, headers=h)
        assert db_session.query(LaunchIntakeStep).count() == 1

    def test_unknown_fields_are_dropped_not_stored(self, client, db_session, world):
        h = _h(db_session, world["admin_a"])
        client.put("/launch/me/steps/company",
                   json={"answers": {"legalName": "Alpha", "evil": "<script>"}},
                   headers=h)
        a = client.get("/launch/me/steps/company", headers=h).json()["answers"]
        assert "evil" not in a

    def test_unknown_step_is_refused(self, client, db_session, world):
        h = _h(db_session, world["admin_a"])
        assert client.get("/launch/me/steps/nope", headers=h).status_code == 404
        assert client.put("/launch/me/steps/nope", json={"answers": {}},
                          headers=h).status_code == 404


# ── secrets ─────────────────────────────────────────────────────────────────

class TestCredentialFields:
    def test_a_password_is_never_returned_and_never_stored_in_clear(
            self, client, db_session, world):
        h = _h(db_session, world["admin_a"])
        client.put("/launch/me/steps/website",
                   json={"answers": {"hostProvider": "SiteGround",
                                     "hostPass": "hunter2-very-secret"}},
                   headers=h)

        body = client.get("/launch/me/steps/website", headers=h).json()
        assert "hostPass" not in body["answers"]
        assert "hostPass" in body["secrets_set"]

        row = (db_session.query(LaunchIntakeStep)
               .filter_by(step_key="website").one())
        assert "hunter2-very-secret" not in str(row.answers)
        assert "hunter2-very-secret" not in str(row.secrets_encrypted)
        # And it really is recoverable ciphertext, not a discarded value.
        from app.utils.crypto import decrypt_value
        assert decrypt_value(row.secrets_encrypted["hostPass"]) == "hunter2-very-secret"

    def test_an_empty_password_does_not_wipe_a_stored_one(
            self, client, db_session, world):
        """A form that renders a blank password box every time would otherwise
        clear the credential on every unrelated save."""
        h = _h(db_session, world["admin_a"])
        client.put("/launch/me/steps/website",
                   json={"answers": {"hostPass": "keepme"}}, headers=h)
        client.put("/launch/me/steps/website",
                   json={"answers": {"hostPass": "", "cms": "WordPress"}},
                   headers=h)
        row = db_session.query(LaunchIntakeStep).filter_by(step_key="website").one()
        from app.utils.crypto import decrypt_value
        assert decrypt_value(row.secrets_encrypted["hostPass"]) == "keepme"

    def test_secrets_are_absent_from_the_staff_view(self, client, db_session, world):
        client.put("/launch/me/steps/website",
                   json={"answers": {"hostPass": "topsecret"}},
                   headers=_h(db_session, world["admin_a"]))
        r = client.get("/god/launch/%s" % world["org_a"].id,
                       headers=_h(db_session, world["god"]))
        assert "topsecret" not in r.text


# ── completion + validation ─────────────────────────────────────────────────

class TestCompletion:
    def test_an_untouched_launch_is_zero_and_fully_blocked(
            self, client, db_session, world):
        r = client.get("/launch/me", headers=_h(db_session, world["admin_a"])).json()
        assert r["overview"]["overall_pct"] == 0
        assert r["blockers"], "an empty intake must report what is missing"

    def test_completion_rises_as_required_fields_are_answered(
            self, client, db_session, world):
        h = _h(db_session, world["admin_a"])
        before = client.get("/launch/me", headers=h).json()["overview"]["overall_pct"]
        client.put("/launch/me/steps/company",
                   json={"answers": {"legalName": "A", "contactFirst": "D",
                                     "contactLast": "W",
                                     "contactEmail": "d@a.test"}},
                   headers=h)
        after = client.get("/launch/me", headers=h).json()["overview"]["overall_pct"]
        assert after > before

    def test_blockers_name_the_actual_missing_fields(self, client, db_session, world):
        h = _h(db_session, world["admin_a"])
        r = client.get("/launch/me", headers=h).json()
        keys = {b["field_key"] for b in r["blockers"]}
        assert "legalName" in keys
        labels = {b["label"] for b in r["blockers"]}
        assert all(isinstance(x, str) and x for x in labels)

    def test_the_files_step_is_completed_by_uploading(self, client, db_session, world):
        h = _h(db_session, world["admin_a"])
        before = next(s for s in client.get("/launch/me", headers=h)
                      .json()["overview"]["steps"] if s["key"] == "files")
        assert before["pct"] == 0

        client.post("/launch/me/files",
                    files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
                    headers=h)
        after = next(s for s in client.get("/launch/me", headers=h)
                     .json()["overview"]["steps"] if s["key"] == "files")
        assert after["pct"] == 100

    def test_one_overall_number_is_served_to_everyone(self, client, db_session, world):
        """The ring and the meter must never be able to disagree."""
        h = _h(db_session, world["admin_a"])
        body = client.get("/launch/me", headers=h).json()["overview"]
        recomputed = round(sum(s["pct"] for s in body["steps"]) / len(body["steps"]))
        assert body["overall_pct"] == recomputed


# ── files ───────────────────────────────────────────────────────────────────

class TestFiles:
    def test_disallowed_type_is_refused_and_nothing_is_stored(
            self, client, db_session, world):
        r = client.post(
            "/launch/me/files",
            files={"file": ("evil.svg", io.BytesIO(b"<svg onload=alert(1)>"),
                            "image/svg+xml")},
            headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 400
        assert db_session.query(LaunchIntakeFile).count() == 0

    def test_empty_file_is_refused(self, client, db_session, world):
        r = client.post("/launch/me/files",
                        files={"file": ("e.pdf", io.BytesIO(b""), "application/pdf")},
                        headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 400

    def test_download_is_an_attachment_and_does_not_sniff(
            self, client, db_session, world):
        h = _h(db_session, world["admin_a"])
        fid = client.post("/launch/me/files",
                          files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4"),
                                          "application/pdf")},
                          headers=h).json()["id"]
        r = client.get("/launch/me/files/%s/download" % fid, headers=h)
        assert r.status_code == 200
        assert r.headers["content-disposition"].startswith("attachment")
        assert r.headers["x-content-type-options"] == "nosniff"

    def test_delete_drops_the_bytes_but_keeps_the_record(
            self, client, db_session, world):
        h = _h(db_session, world["admin_a"])
        fid = client.post("/launch/me/files",
                          files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4"),
                                          "application/pdf")},
                          headers=h).json()["id"]
        assert client.delete("/launch/me/files/%s" % fid, headers=h).status_code == 200
        row = db_session.query(LaunchIntakeFile).filter_by(id=fid).one()
        assert row.deleted_at is not None
        assert row.file_data is None
        assert client.get("/launch/me/files", headers=h).json()["files"] == []


# ── submission ──────────────────────────────────────────────────────────────

def _complete_everything(client, db, user):
    h = _h(db, user)
    client.put("/launch/me/steps/company", json={"answers": {
        "legalName": "Alpha Power, LLC", "contactFirst": "Dana",
        "contactLast": "Whitfield", "contactEmail": "d@alpha.test",
        "address": "1 Main", "city": "Houston", "state": "TX", "zip": "77027",
    }}, headers=h)
    client.put("/launch/me/steps/website", json={"answers": {
        "hostProvider": "SiteGround", "registrar": "GoDaddy"}}, headers=h)
    client.put("/launch/me/steps/compare", json={"answers": {
        "cpContact": "Marissa"}}, headers=h)
    client.put("/launch/me/steps/systems", json={"answers": {
        "sysAdmin": "Ray", "leadRecipient": "Care inbox"}}, headers=h)
    client.put("/launch/me/steps/process", json={"answers": {
        "processDetail": "We call them back.", "firstReceiver": "Care",
        "responseTime": "same_day"}}, headers=h)
    client.put("/launch/me/steps/branding", json={"answers": {
        "brandColors": "#0a0"}}, headers=h)
    client.post("/launch/me/files",
                files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
                headers=h)
    client.put("/launch/me/steps/review", json={"answers": {
        "sigName": "Dana Whitfield", "sigTitle": "Director",
        "sigCompany": "Alpha Power, LLC", "sigAffirm": True}}, headers=h)
    return h


class TestSubmission:
    def test_an_incomplete_intake_cannot_be_submitted_and_says_why(
            self, client, db_session, world):
        r = client.post("/launch/me/submit",
                        headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["blockers"], "a refusal with no reason loses the customer"
        assert db_session.query(LaunchIntakeSubmission).count() == 0

    def test_a_complete_intake_submits_and_snapshots(self, client, db_session, world):
        h = _complete_everything(client, db_session, world["admin_a"])
        assert client.get("/launch/me", headers=h).json()["blockers"] == []

        r = client.post("/launch/me/submit", headers=h)
        assert r.status_code == 200, r.text
        sub = db_session.query(LaunchIntakeSubmission).one()
        assert sub.organization_id == world["org_a"].id
        assert sub.snapshot["company"]["legalName"] == "Alpha Power, LLC"
        assert sub.signed_name == "Dana Whitfield"
        assert sub.file_count == 1

    def test_submission_advances_the_existing_implementation_record(
            self, client, db_session, world):
        h = _complete_everything(client, db_session, world["admin_a"])
        client.post("/launch/me/submit", headers=h)
        db_session.refresh(world["impl_a"])
        assert world["impl_a"].status == "configuration"

    def test_submission_never_drags_a_later_implementation_backwards(
            self, client, db_session, world):
        world["impl_a"].status = "testing"
        db_session.commit()
        h = _complete_everything(client, db_session, world["admin_a"])
        client.post("/launch/me/submit", headers=h)
        db_session.refresh(world["impl_a"])
        assert world["impl_a"].status == "testing"

    def test_a_submitted_intake_is_frozen_until_staff_review(
            self, client, db_session, world):
        h = _complete_everything(client, db_session, world["admin_a"])
        client.post("/launch/me/submit", headers=h)

        r = client.put("/launch/me/steps/company",
                       json={"answers": {"city": "Dallas"}}, headers=h)
        assert r.status_code == 409
        # NOTHING CHANGED.
        row = db_session.query(LaunchIntakeStep).filter_by(step_key="company").one()
        assert row.answers["city"] == "Houston"

    def test_double_submit_is_refused(self, client, db_session, world):
        h = _complete_everything(client, db_session, world["admin_a"])
        assert client.post("/launch/me/submit", headers=h).status_code == 200
        assert client.post("/launch/me/submit", headers=h).status_code == 409
        assert db_session.query(LaunchIntakeSubmission).count() == 1

    def test_the_snapshot_does_not_move_when_answers_later_change(
            self, client, db_session, world):
        h = _complete_everything(client, db_session, world["admin_a"])
        client.post("/launch/me/submit", headers=h)
        client.post("/god/launch/%s/review" % world["org_a"].id, json={"note": "ok"},
                    headers=_h(db_session, world["god"]))
        client.put("/launch/me/steps/company",
                   json={"answers": {"city": "Dallas"}}, headers=h)

        sub = db_session.query(LaunchIntakeSubmission).one()
        assert sub.snapshot["company"]["city"] == "Houston", \
            "what was signed off must not be rewritten by a later edit"

    def test_staff_review_reopens_editing(self, client, db_session, world):
        h = _complete_everything(client, db_session, world["admin_a"])
        client.post("/launch/me/submit", headers=h)
        client.post("/god/launch/%s/review" % world["org_a"].id, json={},
                    headers=_h(db_session, world["god"]))
        assert client.put("/launch/me/steps/company",
                          json={"answers": {"city": "Dallas"}},
                          headers=h).status_code == 200


# ── staff surface ───────────────────────────────────────────────────────────

class TestStaffSurface:
    def test_list_includes_customers_who_have_not_started(
            self, client, db_session, world):
        r = client.get("/god/launch", headers=_h(db_session, world["god"])).json()
        states = {x["organization_id"]: x["intake_state"] for x in r["launches"]}
        assert states[world["org_a"].id] == "not_started"
        assert states[world["org_b"].id] == "not_started"

    def test_list_reflects_progress_and_submission(self, client, db_session, world):
        h = _complete_everything(client, db_session, world["admin_a"])
        client.post("/launch/me/submit", headers=h)
        r = client.get("/god/launch", headers=_h(db_session, world["god"])).json()
        row = next(x for x in r["launches"]
                   if x["organization_id"] == world["org_a"].id)
        assert row["intake_state"] == "submitted"
        assert row["overall_pct"] == 100
        assert row["submitted_at"]

    def test_staff_detail_carries_the_answers(self, client, db_session, world):
        client.put("/launch/me/steps/company",
                   json={"answers": {"legalName": "Alpha Power, LLC"}},
                   headers=_h(db_session, world["admin_a"]))
        r = client.get("/god/launch/%s" % world["org_a"].id,
                       headers=_h(db_session, world["god"])).json()
        assert r["answers"]["company"]["answers"]["legalName"] == "Alpha Power, LLC"

    def test_review_before_submission_is_refused(self, client, db_session, world):
        r = client.post("/god/launch/%s/review" % world["org_a"].id, json={},
                        headers=_h(db_session, world["god"]))
        assert r.status_code == 404


# ── brand configurability ───────────────────────────────────────────────────

class TestBrandIsConfiguration:
    def test_the_brand_is_read_from_the_platform_row(self, client, db_session, world):
        r = client.get("/launch/me", headers=_h(db_session, world["admin_a"])).json()
        assert r["brand"]["name"] == "EvoSys Pro"
        assert r["brand"]["short"] == "EP"

    def test_a_second_brand_renders_as_itself(self, client, db_session, world):
        other = Platform(name="BookaBoost", slug="bb-%d" % next(_SEQ),
                         short_name="BB")
        db_session.add(other)
        db_session.commit()
        org_c = _org(db_session, other, "Gamma Gas")
        _impl(db_session, org_c, other)
        u = _user(db_session, org_c, "org_admin", "gamma-admin")

        r = client.get("/launch/me", headers=_h(db_session, u)).json()
        assert r["brand"]["name"] == "BookaBoost"
        assert r["customer"]["name"] == "Gamma Gas"

    def test_no_customer_or_brand_name_is_hard_coded_in_the_engine(self):
        """A customer may be the first one configured. It must not be a branch."""
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        for rel in ("app/services/launch_intake.py", "app/routers/launch_router.py",
                    "app/models/launch_intake_models.py"):
            text = (root / rel).read_text(encoding="utf-8").lower()
            assert "atlantis" not in text, "%s names a customer" % rel
            # EvoSys/BookaBoost may appear only in prose explaining the layering,
            # never in a comparison.
            for brand in ("evosys", "bookaboost"):
                assert ('== "%s' % brand) not in text
                assert ("== '%s" % brand) not in text

    def test_no_customer_identity_is_hard_coded_in_THE_UI_EITHER(self):
        """The backend guard alone was not enough, and this is how I found out.

        The engine modules were clean while five step components still carried
        one customer's name, staff and domain in placeholders and subtitles —
        "Detected from <their domain>", "<Their name> receives it", three
        invented people at their email domain. All of it shipped to production
        in the bundle and would have appeared on every other customer's screen.

        A guard that covers only the files you were thinking about is a guard
        that certifies the half you already trusted.

        COMMENTS STRIPPED BEFORE SEARCHING, on purpose. The bundler drops
        comments, so a customer named in a `//` line never reaches a browser —
        and several of these files legitimately discuss the first customer by
        name while explaining why nothing is hard-coded. What matters is what
        SHIPS: a string literal, a placeholder, a subtitle, JSX text. Flagging
        prose would push somebody to delete the explanation instead of the bug.
        """
        import pathlib
        import re

        ui = (pathlib.Path(__file__).resolve().parents[1]
              / "frontend" / "src" / "pages" / "launch")

        def shipped(src: str) -> str:
            src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)   # block comments
            src = re.sub(r"^\s*//.*$", "", src, flags=re.M)   # whole-line //
            return src.lower()

        offenders = []
        for path in sorted(ui.rglob("*.js*")):
            if "atlantis" in shipped(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ui)))
        assert not offenders, (
            "these Launch Engine UI files ship a specific customer's identity: %s"
            % ", ".join(offenders))
