"""
Launch Engine — the productization guarantees.

test_launch_engine.py already proves the ENGINE: access, isolation, encryption,
save/resume, the submission lock. This file proves the PRODUCT properties the
screens depend on, and each one exists because a real screen got it wrong:

  * Customer intake progress and implementation progress are two different
    numbers with two different names, reported separately by every endpoint
    that reports either. A customer at 8/8 sections and 0/8 milestones is
    normal, and no payload may let a caller mistake one for the other.

  * Every field a human will ever read has a human label. "sigAffirm: true" was
    on a staff screen; the label "I confirm this information is accurate" was
    in the schema the whole time and simply never asked for.

  * No response, anywhere, on any path, contains a credential VALUE.

  * The vocabulary is white-label. No step label, title or blurb names a
    vertical-specific third party, because the same engine onboards a fibre
    reseller and a funeral home.
"""

import itertools

import pytest

from app.models.implementation_models import Implementation
from app.models.models import Organization, Platform, User
from app.services import launch_intake
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(90000)


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


@pytest.fixture
def world(db_session):
    plat = Platform(name="Fiber Cartel", slug="fc-%d" % next(_SEQ),
                    short_name="FC", support_email="launch@example.test")
    db_session.add(plat)
    db_session.commit()
    org_a = _org(db_session, plat, "Alpha Fibre")
    org_b = _org(db_session, plat, "Beta Fibre")
    im_a = Implementation(organization_id=org_a.id, platform_id=plat.id,
                          opportunity_id="opp-%d" % next(_SEQ),
                          status="not_started")
    im_b = Implementation(organization_id=org_b.id, platform_id=plat.id,
                          opportunity_id="opp-%d" % next(_SEQ),
                          status="not_started")
    db_session.add_all([im_a, im_b])
    db_session.commit()
    return {"plat": plat, "org_a": org_a, "org_b": org_b,
            "impl_a": im_a, "impl_b": im_b,
            "admin_a": _user(db_session, org_a, "org_admin", "alpha-admin"),
            "admin_b": _user(db_session, org_b, "org_admin", "beta-admin"),
            "god": _user(db_session, None, "god_admin", "owner")}


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


# ── the two progress concepts ───────────────────────────────────────────────

class TestProgressIsTwoDifferentThings:
    def test_staff_list_reports_both_separately(self, client, db_session, world):
        r = client.get("/god/launch", headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        rows = r.json()["launches"]
        assert rows, "the fixture created implementations"
        row = rows[0]
        # Both present, both named, and NEITHER is called just "progress".
        for key in ("intake_pct", "intake_complete_steps", "intake_total_steps",
                    "implementation_pct", "implementation_settled",
                    "implementation_total"):
            assert key in row, "%s missing from the launch list" % key

    def test_implementation_detail_reports_intake_too(self, client, db_session, world):
        r = client.get("/god/ops/implementations/" + world["impl_a"].id,
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        body = r.json()
        assert "completion" in body, "milestone completion"
        assert "intake" in body, (
            "the implementation screen showed 0% with no sign the customer had "
            "finished their intake")
        assert body["intake"]["total_steps"] == len(launch_intake.STEP_SCHEMA)
        # The two live under different keys so nothing can average them.
        assert body["intake"] is not body["completion"]

    def test_a_finished_intake_does_not_move_milestones(self, client, db_session, world):
        """The exact case that looked like a contradiction."""
        impl, org = world["impl_a"], world["org_a"]
        for step in launch_intake.STEP_SCHEMA:
            answers = {}
            for f in step["fields"]:
                if not f["required"]:
                    continue
                if f["kind"] == "checkbox":
                    answers[f["key"]] = True
                elif f["kind"] == "select":
                    answers[f["key"]] = f["options"][0]["value"]
                elif f["kind"] == "secret":
                    answers[f["key"]] = "not-a-real-secret"
                else:
                    answers[f["key"]] = "x"
            if answers:
                launch_intake.save_step(db_session, impl.id, org.id,
                                        step["key"], answers, None)

        r = client.get("/god/ops/implementations/" + impl.id,
                       headers=_h(db_session, world["god"]))
        body = r.json()
        # Intake has moved. Milestones have not. Both are correct.
        assert body["intake"]["percent"] > 0
        assert body["completion"]["settled"] == 0
        assert body["intake"]["percent"] != body["completion"]["percent"] \
            or body["intake"]["percent"] == 0


# ── human language ──────────────────────────────────────────────────────────

class TestEveryFieldHasAHumanLabel:
    def test_no_field_label_is_its_own_key(self):
        for step in launch_intake.STEP_SCHEMA:
            assert step["label"] and step["label"] != step["key"]
            for f in step["fields"]:
                assert f["label"], "%s has no label" % f["key"]
                assert f["label"] != f["key"], (
                    "%s would render as its own column name" % f["key"])
                # A label starting lower-case and containing no space is a key
                # in disguise.
                assert not (f["label"][0].islower() and " " not in f["label"])

    def test_the_signature_affirmation_reads_as_a_sentence(self):
        review = launch_intake.STEP_BY_KEY["review"]
        sig = [f for f in review["fields"] if f["key"] == "sigAffirm"][0]
        assert sig["kind"] == "checkbox"
        assert "confirm" in sig["label"].lower()

    def test_every_select_option_has_a_label(self):
        for step in launch_intake.STEP_SCHEMA:
            for f in step["fields"]:
                for o in f.get("options") or []:
                    assert o.get("label"), "%s=%s has no label" % (f["key"], o)
                    assert o["label"] != o["value"], (
                        "%s would render the enum code" % f["key"])

    def test_config_endpoint_ships_the_labels(self, client, db_session, world):
        r = client.get("/launch/config", headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 200
        steps = r.json()["steps"]
        flat = [f for s in steps for f in s["fields"]]
        assert flat, "the schema reached the client"
        assert all(f.get("label") for f in flat)


class TestVocabularyIsWhiteLabel:
    """The same engine onboards a fibre reseller and a funeral home."""

    FORBIDDEN = ["comparepower", "advisorflow", "evosys", "bookaboost",
                 "restland", "fiber cartel"]

    def test_no_step_names_a_vertical_specific_third_party(self):
        for step in launch_intake.STEP_SCHEMA:
            blob = " ".join([step["label"], step["title"], step.get("blurb") or ""])
            for word in self.FORBIDDEN:
                assert word not in blob.lower(), (
                    "step %s says %r — a customer of another brand or vertical "
                    "reads that too" % (step["key"], word))

    def test_no_field_label_names_one_either(self):
        for step in launch_intake.STEP_SCHEMA:
            for f in step["fields"]:
                blob = (f["label"] + " " + (f.get("help") or "")).lower()
                for word in self.FORBIDDEN:
                    assert word not in blob, (
                        "field %s says %r" % (f["key"], word))

    def test_brand_comes_from_the_platform_row(self, client, db_session, world):
        r = client.get("/launch/me", headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 200
        assert r.json()["brand"]["name"] == world["plat"].name


# ── secrets ─────────────────────────────────────────────────────────────────

class TestNoCredentialEverLeaves:
    SECRET = "sup3r-secret-value-9f2a"

    def _store(self, db, world):
        launch_intake.save_step(
            db, world["impl_a"].id, world["org_a"].id, "website",
            {"hostProvider": "SiteGround", "hostPass": self.SECRET,
             "registrar": "GoDaddy", "registrarPass": self.SECRET}, None)

    def test_not_in_the_customers_own_responses(self, client, db_session, world):
        self._store(db_session, world)
        h = _h(db_session, world["admin_a"])
        for path in ("/launch/me", "/launch/me/steps/website",
                     "/launch/me/summary"):
            r = client.get(path, headers=h)
            assert r.status_code == 200, path
            assert self.SECRET not in r.text, "credential leaked from %s" % path

    def test_not_in_the_staff_review(self, client, db_session, world):
        self._store(db_session, world)
        r = client.get("/god/launch/" + world["org_a"].id,
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        assert self.SECRET not in r.text

    def test_presence_is_reported_without_the_value(self, client, db_session, world):
        self._store(db_session, world)
        r = client.get("/launch/me/steps/website",
                       headers=_h(db_session, world["admin_a"]))
        body = r.json()
        assert "hostPass" in body["secrets_set"]
        assert "hostPass" not in body["answers"]


# ── the summary endpoint ────────────────────────────────────────────────────

class TestCustomerSummary:
    def test_returns_every_step(self, client, db_session, world):
        r = client.get("/launch/me/summary",
                       headers=_h(db_session, world["admin_a"]))
        assert r.status_code == 200
        answers = r.json()["answers"]
        assert set(answers.keys()) == set(launch_intake.STEP_KEYS)

    def test_takes_no_org_id_and_cannot_reach_another_tenant(
            self, client, db_session, world):
        """Beta's admin gets Beta's summary. There is no parameter to abuse."""
        launch_intake.save_step(db_session, world["impl_a"].id, world["org_a"].id,
                                "company", {"legalName": "ALPHA ONLY"}, None)
        r = client.get("/launch/me/summary",
                       headers=_h(db_session, world["admin_b"]))
        assert r.status_code == 200
        assert "ALPHA ONLY" not in r.text

    def test_wrong_org_cannot_be_read_by_staff_path_either(
            self, client, db_session, world):
        r = client.get("/god/launch/" + world["org_b"].id,
                       headers=_h(db_session, world["admin_a"]))
        assert r.status_code in (401, 403, 404)


# ── lock and reopen ─────────────────────────────────────────────────────────

def _complete(db, impl, org):
    for step in launch_intake.STEP_SCHEMA:
        answers = {}
        for f in step["fields"]:
            if not f["required"]:
                continue
            if f["kind"] == "checkbox":
                answers[f["key"]] = True
            elif f["kind"] == "select":
                answers[f["key"]] = f["options"][0]["value"]
            elif f["kind"] == "secret":
                answers[f["key"]] = "x-secret"
            else:
                answers[f["key"]] = "x"
        if answers:
            launch_intake.save_step(db, impl.id, org.id, step["key"], answers, None)


class TestLockAndReopen:
    def test_submitted_intake_refuses_edits(self, client, db_session, world):
        impl, org = world["impl_a"], world["org_a"]
        _complete(db_session, impl, org)
        # The files step needs an upload before submit is allowed.
        h = _h(db_session, world["admin_a"])
        client.post("/launch/me/files", headers=h,
                    files={"file": ("a.txt", b"hello", "text/plain")},
                    data={"step_key": "files"})
        r = client.post("/launch/me/submit", headers=h)
        assert r.status_code == 200, r.text

        blocked = client.put("/launch/me/steps/company", headers=h,
                             json={"answers": {"legalName": "CHANGED AFTER SUBMIT"}})
        assert blocked.status_code == 409
        # AND NOTHING CHANGED.
        after = client.get("/launch/me/steps/company", headers=h).json()
        assert after["answers"].get("legalName") != "CHANGED AFTER SUBMIT"

    def test_review_reopens_for_edits(self, client, db_session, world):
        impl, org = world["impl_a"], world["org_a"]
        _complete(db_session, impl, org)
        h = _h(db_session, world["admin_a"])
        client.post("/launch/me/files", headers=h,
                    files={"file": ("a.txt", b"hello", "text/plain")},
                    data={"step_key": "files"})
        client.post("/launch/me/submit", headers=h)

        rev = client.post("/god/launch/" + org.id + "/review",
                          headers=_h(db_session, world["god"]), json={})
        assert rev.status_code == 200

        ok = client.put("/launch/me/steps/company", headers=h,
                        json={"answers": {"legalName": "EDITED AFTER REOPEN"}})
        assert ok.status_code == 200
        assert ok.json()["answers"]["legalName"] == "EDITED AFTER REOPEN"


# ── test records do not break the list ──────────────────────────────────────

class TestListSurvivesOddData:
    def test_inactive_and_unnamed_customers_do_not_break_it(
            self, client, db_session, world):
        plat = world["plat"]
        odd = _org(db_session, plat, "ZZ Launch Verify A (test)")
        odd.is_active = False
        db_session.add(Implementation(organization_id=odd.id, platform_id=plat.id,
                                      opportunity_id="opp-%d" % next(_SEQ),
                                      status="not_started"))
        db_session.commit()

        r = client.get("/god/launch", headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        names = [x["organization_name"] for x in r.json()["launches"]]
        # Present, not omitted — the staff list is where somebody notices them.
        assert "ZZ Launch Verify A (test)" in names
        for row in r.json()["launches"]:
            assert row["intake_total_steps"] == len(launch_intake.STEP_SCHEMA)


# ── the wizard the customer actually reads ──────────────────────────────────
#
# The schema guard above protects the SERVER vocabulary. It cannot see the
# React step components, and that is exactly where the leak was: the server
# said "Main lead source or partner" while the screen still said "Your
# existing contact at ComparePower". A customer of a fibre reseller was being
# asked, in rendered text, about an electricity marketplace.

import re as _re
from pathlib import Path as _Path

_WIZARD_DIR = _Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "launch"

_BLOCK_COMMENT = _re.compile(r"/\*.*?\*/", _re.S)
_LINE_COMMENT = _re.compile(r"^\s*//.*$", _re.M)


def _rendered_source(path: _Path) -> str:
    """The file with comments removed — what a customer could actually see.

    Comments are stripped because the explanation of WHY a name was removed
    necessarily contains that name, and a guard that fails on its own
    rationale teaches people to delete the rationale.
    """
    text = path.read_text(encoding="utf-8")
    text = _BLOCK_COMMENT.sub(" ", text)
    text = _LINE_COMMENT.sub(" ", text)
    return text


class TestTheWizardScreensAreWhiteLabelToo:
    """Same rule as the schema, enforced on the JSX the customer reads."""

    # Brands and one vertical-specific third party. Kept separate from the
    # schema list only so a failure names which layer leaked.
    FORBIDDEN = ["comparepower", "advisorflow", "evosys", "bookaboost",
                 "restland", "fiber cartel", "atlantis"]

    # Vertical vocabulary. The engine onboards a fibre reseller, a funeral
    # home and a benefits agency through these same eight screens.
    VERTICAL = ["electricity", "kilowatt", "kwh", "megawatt", " tdu", "puc"]

    def _files(self):
        files = sorted(_WIZARD_DIR.rglob("*.jsx")) + sorted(_WIZARD_DIR.rglob("*.js"))
        assert files, "the launch wizard source was not found at %s" % _WIZARD_DIR
        return files

    def test_no_wizard_screen_names_a_brand_or_named_partner(self):
        offences = []
        for path in self._files():
            body = _rendered_source(path).lower()
            for word in self.FORBIDDEN:
                if word in body:
                    offences.append("%s says %r" % (path.name, word))
        assert not offences, (
            "customer-facing wizard copy names something specific to one "
            "brand or one customer: " + "; ".join(offences))

    def test_no_wizard_screen_assumes_the_customers_industry(self):
        offences = []
        for path in self._files():
            body = _rendered_source(path).lower()
            for word in self.VERTICAL:
                if word in body:
                    offences.append("%s says %r" % (path.name, word))
        assert not offences, (
            "customer-facing wizard copy assumes an industry: "
            + "; ".join(offences))

    def test_the_wizard_renders_the_brand_from_props_not_a_literal(self):
        """Whatever names the brand on screen comes from `brand`, not source."""
        sidebar = _rendered_source(_WIZARD_DIR / "LaunchSidebar.jsx")
        assert "brand." in sidebar, "the rail footer must read the brand object"
        hero = _rendered_source(_WIZARD_DIR / "LaunchHero.jsx")
        assert "brand." in hero, "the hero must read the brand object"


# ── history ─────────────────────────────────────────────────────────────────
#
# The implementation history is read from the audit log, and the intake wrote
# nothing to it. A staff member looking at "what happened with this customer"
# could see a milestone ticked but not the customer submitting the intake or a
# reviewer reopening it — the two events people actually ask about.


class TestHistoryRecordsTheIntakeLifecycle:

    def _submit(self, client, db_session, world):
        impl, org = world["impl_a"], world["org_a"]
        _complete(db_session, impl, org)
        h = _h(db_session, world["admin_a"])
        client.post("/launch/me/files", headers=h,
                    files={"file": ("a.txt", b"hello", "text/plain")},
                    data={"step_key": "files"})
        r = client.post("/launch/me/submit", headers=h)
        assert r.status_code == 200, r.text
        return h

    def _actions(self, client, db_session, world):
        r = client.get("/god/ops/implementations/" + world["impl_a"].id,
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 200, r.text
        return [e["action"] for e in r.json().get("timeline") or []]

    def test_submitting_appears_in_the_history(self, client, db_session, world):
        self._submit(client, db_session, world)
        assert "launch_intake_submitted" in self._actions(client, db_session, world)

    def test_review_records_both_the_review_and_the_reopen(
            self, client, db_session, world):
        self._submit(client, db_session, world)
        rev = client.post("/god/launch/" + world["org_a"].id + "/review",
                          headers=_h(db_session, world["god"]), json={})
        assert rev.status_code == 200, rev.text
        actions = self._actions(client, db_session, world)
        # Reviewing also reopens. A reader should not have to know that.
        assert "launch_intake_reviewed" in actions
        assert "launch_intake_reopened" in actions

    def test_history_entries_carry_an_actor_and_a_time(
            self, client, db_session, world):
        self._submit(client, db_session, world)
        r = client.get("/god/ops/implementations/" + world["impl_a"].id,
                       headers=_h(db_session, world["god"]))
        rows = [e for e in r.json()["timeline"]
                if e["action"] == "launch_intake_submitted"]
        assert rows, "the submission is in the history"
        assert rows[0]["at"], "with a timestamp"
        assert rows[0]["actor"], "and a named actor, not an anonymous blob"

    def test_no_credential_reaches_the_history(self, client, db_session, world):
        """_complete() stores "x-secret" in every secret field."""
        self._submit(client, db_session, world)
        r = client.get("/god/ops/implementations/" + world["impl_a"].id,
                       headers=_h(db_session, world["god"]))
        assert "x-secret" not in r.text


class TestEveryHistoryCodeHasAPhrase:
    """The screen renders `action` through present.js, not with a regex."""

    # Codes this surface can emit, from the routers and services that write
    # them with target_type "implementation" / "implementation_milestone" /
    # "customer_activation" — the three the timeline query filters on.
    EMITTED = [
        "launch_intake_submitted", "launch_intake_reviewed",
        "launch_intake_reopened",
        "implementation_milestone_added", "implementation_milestone_changed",
        "implementation_owner_assigned", "customer_marked_live",
        "billing_configuration_changed",
        "customer_admin_invite_revoked", "customer_admin_activated",
    ]

    def test_present_js_has_a_human_phrase_for_each(self):
        src = (_WIZARD_DIR / "present.js").read_text(encoding="utf-8")
        block = src.split("const EVENT_LABELS", 1)
        assert len(block) == 2, "present.js still declares EVENT_LABELS"
        body = block[1].split("}", 1)[0]
        missing = [c for c in self.EMITTED if (c + ":") not in body]
        assert not missing, (
            "these audit codes would render as raw text in History: %s"
            % ", ".join(missing))

    def test_every_phrase_reads_as_a_sentence(self):
        """Not the raw code, and not an all-lower-case fragment.

        `customer_provisioned` -> "Customer provisioned" is a legitimate
        phrase that happens to share the code's words, so the rule is about
        SHAPE: it must be written for a reader (capitalised, no underscores),
        never the identifier passed through str.replace.
        """
        src = (_WIZARD_DIR / "present.js").read_text(encoding="utf-8")
        body = src.split("const EVENT_LABELS", 1)[1].split("}", 1)[0]
        seen = 0
        for line in body.splitlines():
            if ":" not in line or line.strip().startswith("//"):
                continue
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip().rstrip(",").strip("'\"")
            if not key or not val:
                continue
            seen += 1
            assert "_" not in val, "%s still shows an identifier" % key
            assert val[0].isupper(), "%s is not written for a reader" % key
            assert val != key, "%s is its own label" % key
        assert seen >= 10, "the label table was parsed, not skipped"
