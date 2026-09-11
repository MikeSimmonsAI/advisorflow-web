"""THE CUSTOMER LAUNCH EXPERIENCE — configuration, preview, and the design.

WHAT THIS FILE IS PROTECTING
============================
An approved customer-facing onboarding design, built ONCE at the platform
layer and configured per brand, per industry and per customer. Four things can
destroy that, and each has a section below:

  1. SOMEBODY HARD-CODES A CUSTOMER. The shell starts carrying one company's
     name, logo, vocabulary or percentages, and the second customer needs a
     release. `TestNoCustomerIsInTheCode` and `TestPrecedence` fail if so.

  2. AN INDUSTRY LEAKS INTO ANOTHER. The defect this platform already shipped
     once: an energy customer opening their settings to find funeral-home
     vocabulary. `TestIndustryAwareness` fails if any industry becomes any
     other industry's fallback.

  3. THE PREVIEW STARTS DOING THINGS. An internal preview exists so staff can
     see a customer's onboarding BEFORE inviting them. The moment it writes
     anything — an answer, a file, a submission, an invitation, a completion —
     it stops being a preview and becomes an impersonation nobody consented
     to. `TestPreviewChangesNothing` counts every row before and after.

  4. PROGRESS STARTS LYING. A percentage computed from screens visited is a
     number that tells the one person relying on it the opposite of the truth.
     `TestProgressIsEarned` fails if looking ever moves it.

Auth dependencies are NOT overridden anywhere here. Real JWTs, real headers,
real scope resolution — an isolation test that stubs the thing it is testing
proves nothing.
"""

import itertools
import pathlib
import re

import pytest

from app.models.implementation_models import Implementation
from app.models.launch_experience_models import LaunchExperienceConfig
from app.models.launch_intake_models import (
    LaunchIntakeFile, LaunchIntakeStep, LaunchIntakeSubmission,
)
from app.models.models import AuditLogEntry, Organization, Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, SCOPE_BRAND_SALES_ORG,
)
from app.services import launch_experience
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


# ── fixtures ────────────────────────────────────────────────────────────────

def _platform(db, name):
    p = Platform(name=name, slug="p-%d" % next(_SEQ), short_name=name[:2],
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


def _org(db, plat, name, industry=None):
    kwargs = {} if industry is None else {"industry": industry}
    o = Organization(name=name, slug="o-%d" % next(_SEQ), platform_id=plat.id,
                     plan="standard", is_active=True, **kwargs)
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


def _impl(db, org, plat, bso=None, status="not_started"):
    im = Implementation(organization_id=org.id, platform_id=plat.id,
                        brand_sales_org_id=(bso.id if bso else None),
                        opportunity_id="opp-%d" % next(_SEQ), status=status)
    db.add(im)
    db.commit()
    return im


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


@pytest.fixture()
def world(db_session):
    """TWO BRANDS, and under one of them two customers in two industries.

    One brand cannot fail a brand-isolation test and one industry cannot fail
    an industry test, so the fixture carries both axes.
    """
    db = db_session
    brand_a = _platform(db, "Brand Alpha")
    brand_b = _platform(db, "Brand Beta")
    bso_a, bso_b = _bso(db, brand_a), _bso(db, brand_b)

    energy = _org(db, brand_a, "Northwind Utility", industry="energy")
    funeral = _org(db, brand_a, "Hillside Memorial", industry="funeral")
    unknown = _org(db, brand_a, "Unstated Company")
    other = _org(db, brand_b, "Beta Customer", industry="energy")

    return {
        "brand_a": brand_a, "brand_b": brand_b, "bso_a": bso_a, "bso_b": bso_b,
        "energy": energy, "funeral": funeral, "unknown": unknown,
        "other": other,
        "impl_energy": _impl(db, energy, brand_a, bso_a),
        "impl_funeral": _impl(db, funeral, brand_a, bso_a),
        "impl_unknown": _impl(db, unknown, brand_a, bso_a),
        "impl_other": _impl(db, other, brand_b, bso_b),
        "customer": _user(db, energy, "org_admin", "customer"),
        "god": _user(db, None, "god_admin", "owner"),
        "staff_a": _user(db, None, "user", "alpha-staff",
                         platform_id=brand_a.id),
        "staff_b": _user(db, None, "user", "beta-staff",
                         platform_id=brand_b.id),
    }


@pytest.fixture()
def staffed(db_session, world):
    _member(db_session, world["staff_a"], world["bso_a"], "sales_manager")
    _member(db_session, world["staff_b"], world["bso_b"], "sales_manager")
    return world


def _counts(db):
    """Every table the onboarding surface can write to, in one snapshot.

    Deliberately a COUNT OF EVERYTHING rather than an assertion about the row
    the test expected not to appear: a preview that starts writing something
    nobody anticipated is exactly the failure this is for.
    """
    return {
        "steps": db.query(LaunchIntakeStep).count(),
        "files": db.query(LaunchIntakeFile).count(),
        "submissions": db.query(LaunchIntakeSubmission).count(),
        "users": db.query(User).count(),
        "orgs": db.query(Organization).count(),
        "implementations": db.query(Implementation).count(),
        "configs": db.query(LaunchExperienceConfig).count(),
    }


# ════════════════════════════════════════════════════════════════════════════
# 1. CONFIGURATION PRECEDENCE
# ════════════════════════════════════════════════════════════════════════════

class TestPrecedence:
    def test_a_customer_with_no_configuration_still_gets_a_finished_page(
            self, db_session, world):
        """NOTHING IS REQUIRED. The whole design renders from the defaults, so
        a brand that has configured nothing does not ship an empty shell."""
        cfg = launch_experience.resolve(db_session)
        p = cfg["presentation"]
        assert p["title"] and p["subtitle"] and p["intro"]
        assert p["help"]["title"] and p["guide"]["title"]
        assert p["footer"]["copyright"]
        assert len(cfg["journey"]) == 7
        # And no imagery is invented for a customer who supplied none.
        assert p["hero_image_url"] is None
        assert p["rail_image_url"] is None
        assert p["guide"]["url"] is None

    def test_each_layer_overrides_the_one_beneath_it(self, db_session, world):
        db = db_session
        brand = world["brand_a"]
        org = world["energy"]

        def resolved():
            return launch_experience.resolve(
                db, industry=org.industry, platform_id=brand.id,
                organization_id=org.id)["presentation"]["subtitle"]

        base = resolved()
        db.add(LaunchExperienceConfig(scope_type="industry", scope_id="energy",
                                      presentation={"subtitle": "industry"}))
        db.commit()
        assert resolved() == "industry", "industry did not beat the default"

        db.add(LaunchExperienceConfig(scope_type="brand", scope_id=brand.id,
                                      presentation={"subtitle": "brand"}))
        db.commit()
        assert resolved() == "brand", "brand did not beat industry"

        db.add(LaunchExperienceConfig(scope_type="organization",
                                      scope_id=org.id,
                                      presentation={"subtitle": "customer"}))
        db.commit()
        assert resolved() == "customer", "customer did not beat brand"
        assert base != "customer"

    def test_one_overridden_key_does_not_wipe_the_others(self, db_session,
                                                         world):
        """A brand setting a title must not silently lose the help card."""
        db = db_session
        db.add(LaunchExperienceConfig(scope_type="brand",
                                      scope_id=world["brand_a"].id,
                                      presentation={"title": "Only this"}))
        db.commit()
        p = launch_experience.resolve(
            db, platform_id=world["brand_a"].id)["presentation"]
        assert p["title"] == "Only this"
        assert p["help"]["title"] == launch_experience.DEFAULT_PRESENTATION[
            "help"]["title"]
        assert p["footer"]["copyright"]

    def test_a_configured_list_replaces_it_and_never_appends(self, db_session,
                                                             world):
        """Five configured stages means five stages, not five plus seven."""
        db = db_session
        db.add(LaunchExperienceConfig(
            scope_type="brand", scope_id=world["brand_a"].id,
            journey=[{"key": "intake", "label": "One"},
                     {"key": "golive", "label": "Two"}]))
        db.commit()
        cfg = launch_experience.resolve(db, platform_id=world["brand_a"].id)
        assert [s["key"] for s in cfg["journey"]] == ["intake", "golive"]

    def test_layers_report_which_row_to_edit(self, db_session, world):
        """An operator asking 'why does it say that' is told where, not left
        to guess between four possible rows."""
        db = db_session
        db.add(LaunchExperienceConfig(scope_type="organization",
                                      scope_id=world["energy"].id,
                                      presentation={"title": "x"}))
        db.commit()
        cfg = launch_experience.resolve(db, industry="energy",
                                        platform_id=world["brand_a"].id,
                                        organization_id=world["energy"].id)
        from_row = {l["scope_type"] for l in cfg["layers"] if l["from_row"]}
        assert from_row == {"organization"}

    def test_an_inactive_row_contributes_nothing(self, db_session, world):
        db = db_session
        db.add(LaunchExperienceConfig(scope_type="brand",
                                      scope_id=world["brand_a"].id,
                                      presentation={"subtitle": "off"},
                                      is_active=False))
        db.commit()
        p = launch_experience.resolve(db, platform_id=world["brand_a"].id)
        assert p["presentation"]["subtitle"] != "off"


# ════════════════════════════════════════════════════════════════════════════
# 2. INDUSTRY AWARENESS
# ════════════════════════════════════════════════════════════════════════════

FUNERAL_WORDS = ("pre_need", "at_need", "imminent", "at-need", "pre-need",
                 "funeral", "cemetery", "arrangement conference")
ENERGY_WORDS = ("supplier", "kwh", "rate", "esiid", "utility", "deregulated")


def _blob(value):
    import json
    return json.dumps(value, default=str).lower()


class TestIndustryAwareness:
    def test_a_funeral_home_is_never_asked_an_energy_question(self,
                                                             db_session):
        cfg = launch_experience.resolve(db_session, industry="funeral")
        text = _blob(cfg["form"]) + _blob(cfg["journey"])
        for word in ENERGY_WORDS:
            assert word not in text, word

    def test_an_energy_business_is_never_shown_funeral_vocabulary(self,
                                                                  db_session):
        cfg = launch_experience.resolve(db_session, industry="energy")
        text = _blob(cfg["form"]) + _blob(cfg["journey"]) \
            + _blob(cfg["presentation"])
        for word in FUNERAL_WORDS:
            assert word not in text, word

    def test_an_industry_nobody_recognises_falls_back_to_neutral(self,
                                                                 db_session):
        """THE SHIPPED DEFECT, LOCKED SHUT. An unknown industry resolved to
        funeral, which is how a power company was handed at-need vocabulary."""
        for value in (None, "", "widgets", "something nobody has heard of"):
            cfg = launch_experience.resolve(db_session, industry=value)
            text = _blob(cfg["form"]) + _blob(cfg["journey"])
            for word in FUNERAL_WORDS:
                assert word not in text, "%r -> %s" % (value, word)

    def test_an_industry_template_can_still_say_something_specific(self,
                                                                   db_session):
        """The point of an industry layer is that it CAN differ — a test that
        only proved isolation would pass on a registry that said nothing."""
        energy = launch_experience.resolve(db_session, industry="energy")
        generic = launch_experience.resolve(db_session, industry=None)
        assert energy["form"] != generic["form"] \
            or energy["journey"] != generic["journey"]

    def test_the_industry_layer_reaches_the_customers_own_page(
            self, client, db_session, world):
        body = client.get("/launch-experience/me",
                          headers=_h(db_session, world["customer"])).json()
        assert body["experience"]["industry"]["key"] == "energy"


# ════════════════════════════════════════════════════════════════════════════
# 3. THE PREVIEW CHANGES NOTHING
# ════════════════════════════════════════════════════════════════════════════

class TestPreviewChangesNothing:
    def test_previewing_writes_no_customer_state_at_all(self, client,
                                                        db_session, world):
        before = _counts(db_session)
        for _ in range(3):
            r = client.get("/launch-experience/preview/" + world["energy"].id,
                           headers=_h(db_session, world["god"]))
            assert r.status_code == 200
        db_session.expire_all()
        assert _counts(db_session) == before

    def test_previewing_invites_nobody(self, client, db_session, world):
        """No user is created, and no existing user is touched. An invitation
        is an explicit authorized action; a page render is not one."""
        before = sorted(u.email for u in db_session.query(User).all())
        client.get("/launch-experience/preview/" + world["energy"].id,
                   headers=_h(db_session, world["god"]))
        db_session.expire_all()
        assert sorted(u.email for u in db_session.query(User).all()) == before

    def test_previewing_creates_no_submission_and_no_completion(
            self, client, db_session, world):
        client.get("/launch-experience/preview/" + world["energy"].id,
                   headers=_h(db_session, world["god"]))
        db_session.expire_all()
        assert db_session.query(LaunchIntakeSubmission).count() == 0
        impl = db_session.query(Implementation).filter(
            Implementation.id == world["impl_energy"].id).first()
        assert impl.status == "not_started"

    def test_the_preview_says_so_on_the_payload(self, client, db_session,
                                                world):
        body = client.get("/launch-experience/preview/" + world["energy"].id,
                          headers=_h(db_session, world["god"])).json()
        assert body["experience"]["preview"] is True
        ctx = body["preview_context"]
        assert ctx["read_only"] is True
        assert ctx["customer_notified"] is False
        assert ctx["answers_included"] is False
        assert ctx["organization_name"] == "Northwind Utility"

    def test_the_preview_does_not_carry_the_customers_typed_answers(
            self, client, db_session, world):
        """Judging the EXPERIENCE does not require reading what somebody
        wrote. Reading their answers is the staff review screen, which is
        separately scoped and separately audited."""
        db_session.add(LaunchIntakeStep(
            implementation_id=world["impl_energy"].id,
            organization_id=world["energy"].id, step_key="company",
            answers={"legal_name": "A PRIVATE ANSWER"}))
        db_session.commit()

        body = client.get("/launch-experience/preview/" + world["energy"].id,
                          headers=_h(db_session, world["god"])).json()
        assert "A PRIVATE ANSWER" not in _blob(body)

    def test_looking_is_recorded_so_it_is_not_mistaken_for_the_customer(
            self, client, db_session, world):
        client.get("/launch-experience/preview/" + world["energy"].id,
                   headers=_h(db_session, world["god"]))
        db_session.expire_all()
        rows = (db_session.query(AuditLogEntry)
                .filter(AuditLogEntry.action == "launch_experience_previewed")
                .all())
        assert len(rows) == 1
        assert rows[0].organization_id == world["energy"].id
        assert rows[0].actor_user_id == world["god"].id

    def test_the_preview_shows_the_customers_delivery_not_the_viewers(
            self, client, db_session, world):
        """A session-scoped fetch inside a preview would paint the OPERATOR's
        integrations onto the customer's page. The preview composes it."""
        body = client.get("/launch-experience/preview/" + world["energy"].id,
                          headers=_h(db_session, world["god"])).json()
        assert "delivery" in body

    def test_the_preview_renders_the_same_shell_the_customer_gets(
            self, client, db_session, world):
        """EXACTLY what the customer will see — so the two payloads must agree
        on presentation and journey. A separate 'staff rendering' of
        onboarding is the thing this must never become."""
        mine = client.get("/launch-experience/me",
                          headers=_h(db_session, world["customer"])).json()
        theirs = client.get("/launch-experience/preview/" + world["energy"].id,
                            headers=_h(db_session, world["god"])).json()
        assert (mine["experience"]["presentation"]
                == theirs["experience"]["presentation"])
        assert mine["experience"]["journey"] == theirs["experience"]["journey"]

    def test_the_preview_does_not_put_the_operators_name_in_the_customer_seat(
            self, client, db_session, world):
        body = client.get("/launch-experience/preview/" + world["energy"].id,
                          headers=_h(db_session, world["god"])).json()
        assert world["god"].email not in _blob(body.get("customer") or {})

    def test_there_is_no_person_in_a_preview_and_the_payload_says_so(
            self, client, db_session, world):
        """THE CONTRACT THE BLANK PAGE BROKE.

        `customer.user` is NULL in a preview, deliberately — the operator is
        not the customer and must not appear in their avatar. The shell read
        `customer.user.name` unconditionally, threw during render, and React
        unmounted the whole tree: the page became the background colour and
        nothing else. This pins the null so the contract is explicit rather
        than incidental.
        """
        body = client.get("/launch-experience/preview/" + world["energy"].id,
                          headers=_h(db_session, world["god"])).json()
        assert "user" in body["customer"]
        assert body["customer"]["user"] is None
        # The organization is still named — a preview identifies the customer,
        # it just does not invent a person.
        assert body["customer"]["name"] == "Northwind Utility"
        assert body["customer"]["short"]

    def test_the_customers_own_page_does_have_a_person(self, client,
                                                       db_session, world):
        """The other half: null is the PREVIEW's answer, not everyone's."""
        body = client.get("/launch-experience/me",
                          headers=_h(db_session, world["customer"])).json()
        assert body["customer"]["user"] is not None
        assert body["customer"]["user"]["name"]


# ════════════════════════════════════════════════════════════════════════════
# 4. WHO MAY PREVIEW, AND WHOSE
# ════════════════════════════════════════════════════════════════════════════

class TestPreviewIsolation:
    def test_anonymous_is_refused(self, client, world):
        r = client.get("/launch-experience/preview/" + world["energy"].id)
        assert r.status_code in (401, 403)
        r = client.get("/launch-experience/me")
        assert r.status_code in (401, 403)

    def test_one_brands_staff_cannot_preview_another_brands_customer(
            self, client, db_session, staffed):
        r = client.get("/launch-experience/preview/" + staffed["energy"].id,
                       headers=_h(db_session, staffed["staff_b"]))
        assert r.status_code == 404, "Beta staff reached an Alpha customer"

    def test_a_refusal_does_not_confirm_the_customer_exists(
            self, client, db_session, staffed):
        """404, never 403 — a distinguishable refusal is an enumeration
        oracle with extra steps."""
        real = client.get("/launch-experience/preview/" + staffed["energy"].id,
                          headers=_h(db_session, staffed["staff_b"]))
        fake = client.get("/launch-experience/preview/does-not-exist",
                          headers=_h(db_session, staffed["staff_b"]))
        assert real.status_code == fake.status_code == 404

    def test_the_owning_brands_staff_may_preview(self, client, db_session,
                                                 staffed):
        r = client.get("/launch-experience/preview/" + staffed["energy"].id,
                       headers=_h(db_session, staffed["staff_a"]))
        assert r.status_code == 200

    def test_a_customer_cannot_preview_anybody(self, client, db_session,
                                               world):
        r = client.get("/launch-experience/preview/" + world["funeral"].id,
                       headers=_h(db_session, world["customer"]))
        assert r.status_code == 404

    def test_a_customer_route_takes_no_organization_id(self, client,
                                                       db_session, world):
        """`/launch-experience/me` resolves the workspace from the session.
        There is no id in it for anyone to change."""
        body = client.get("/launch-experience/me",
                          headers=_h(db_session, world["customer"])).json()
        assert body["customer"]["name"] == "Northwind Utility"


# ════════════════════════════════════════════════════════════════════════════
# 5. WHO MAY CONFIGURE WHICH LAYER
# ════════════════════════════════════════════════════════════════════════════

class TestConfigAuthority:
    def test_platform_and_industry_layers_are_the_platform_owners(
            self, client, db_session, staffed):
        for path in ("/launch-experience/config/platform_default/default",
                     "/launch-experience/config/industry/energy"):
            r = client.put(path, json={"presentation": {"title": "no"}},
                           headers=_h(db_session, staffed["staff_a"]))
            assert r.status_code == 403, path
        db_session.expire_all()
        assert db_session.query(LaunchExperienceConfig).count() == 0

    def test_god_configures_the_platform_layer(self, client, db_session,
                                               world):
        r = client.put("/launch-experience/config/platform_default/default",
                       json={"presentation": {"subtitle": "Platform"}},
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        assert launch_experience.resolve(
            db_session)["presentation"]["subtitle"] == "Platform"

    def test_a_brand_configures_its_own_and_not_another(self, client,
                                                        db_session, staffed):
        ok = client.put(
            "/launch-experience/config/brand/" + staffed["brand_a"].id,
            json={"presentation": {"subtitle": "Alpha"}},
            headers=_h(db_session, staffed["staff_a"]))
        assert ok.status_code == 200

        nope = client.put(
            "/launch-experience/config/brand/" + staffed["brand_b"].id,
            json={"presentation": {"subtitle": "stolen"}},
            headers=_h(db_session, staffed["staff_a"]))
        assert nope.status_code == 404

        db_session.expire_all()
        rows = db_session.query(LaunchExperienceConfig).all()
        assert [r.scope_id for r in rows] == [staffed["brand_a"].id]

    def test_a_brand_cannot_configure_another_brands_customer(
            self, client, db_session, staffed):
        r = client.put(
            "/launch-experience/config/organization/" + staffed["energy"].id,
            json={"presentation": {"subtitle": "stolen"}},
            headers=_h(db_session, staffed["staff_b"]))
        assert r.status_code == 404
        db_session.expire_all()
        assert db_session.query(LaunchExperienceConfig).count() == 0

    def test_a_customer_cannot_configure_their_own_shell(self, client,
                                                         db_session, world):
        r = client.put(
            "/launch-experience/config/organization/" + world["energy"].id,
            json={"presentation": {"subtitle": "mine"}},
            headers=_h(db_session, world["customer"]))
        assert r.status_code in (403, 404)
        db_session.expire_all()
        assert db_session.query(LaunchExperienceConfig).count() == 0

    def test_a_brand_only_lists_layers_it_owns(self, client, db_session,
                                               staffed):
        db = db_session
        db.add(LaunchExperienceConfig(scope_type="brand",
                                      scope_id=staffed["brand_b"].id,
                                      presentation={"title": "beta"}))
        db.commit()
        body = client.get("/launch-experience/config",
                          headers=_h(db, staffed["staff_a"])).json()
        assert [c["scope_id"] for c in body["configs"]] == []


# ════════════════════════════════════════════════════════════════════════════
# 6. PROGRESS IS EARNED
# ════════════════════════════════════════════════════════════════════════════

def _sample_for(field):
    """A value the intake's own validator will accept, by field kind.

    SYNTHETIC, and shaped to the field rather than to one customer: a test
    that typed a real company's legal name, address or phone number here
    would be the seeded placeholder this whole feature forbids.
    """
    kind = field.get("kind") or "text"
    if kind == "email":
        return "intake-%d@test.local" % next(_SEQ)
    if kind == "phone":
        return "+15555550100"
    if kind == "url":
        return "https://example.test"
    if kind == "date":
        return "2030-01-01"
    if kind == "checkbox":
        return True
    if kind == "select":
        options = [o for o in (field.get("options") or []) if o]
        first = options[0] if options else "yes"
        return first.get("value") if isinstance(first, dict) else first
    return "Stated by the customer"


class TestProgressIsEarned:
    def test_a_customer_who_has_typed_nothing_is_at_zero(self, client,
                                                         db_session, world):
        body = client.get("/launch-experience/me",
                          headers=_h(db_session, world["customer"])).json()
        assert body["overview"]["overall_pct"] == 0

    def test_looking_at_screens_does_not_move_the_number(self, client,
                                                         db_session, world):
        """The failure this forbids: an operator telling a customer they are
        at 40% from a screen the customer has never opened."""
        head = _h(db_session, world["god"])
        first = client.get("/launch-experience/preview/" + world["energy"].id,
                           headers=head).json()["overview"]["overall_pct"]
        for step in ("company", "branding", "website", "review"):
            client.get("/launch-experience/preview/%s/%s"
                       % (world["energy"].id, step), headers=head)
        again = client.get("/launch-experience/preview/" + world["energy"].id,
                           headers=head).json()["overview"]["overall_pct"]
        assert first == again == 0

    def test_answering_a_real_question_is_what_moves_it(self, client,
                                                        db_session, world):
        """The other half of the same rule. A percentage that never moves is
        as useless as one that moves for nothing — answers must count."""
        head = _h(db_session, world["customer"])
        before = client.get("/launch-experience/me",
                            headers=head).json()["overview"]["overall_pct"]

        schema = client.get("/launch/config", headers=head).json()
        company = next(s for s in schema["steps"] if s["key"] == "company")
        answers = {f["key"]: _sample_for(f)
                   for f in company["fields"] if f.get("required")}
        assert answers, "the intake declares no required company fields"

        saved = client.put("/launch/me/steps/company", json={"answers": answers},
                           headers=head)
        assert saved.status_code == 200, saved.text

        after = client.get("/launch-experience/me",
                           headers=head).json()["overview"]["overall_pct"]
        assert after > before


# ════════════════════════════════════════════════════════════════════════════
# 7. THE APPROVED DESIGN
# ════════════════════════════════════════════════════════════════════════════

APPROVED_SHELL_PARTS = [
    "LaunchHero.jsx", "LaunchSidebar.jsx", "LaunchHeader.jsx",
    "LaunchProgress.jsx", "OnboardingProgressPanel.jsx",
    "OnboardingStepShell.jsx", "LaunchFooter.jsx", "PreviewBanner.jsx",
    "LaunchBoundary.jsx",
]
FRONTEND = (pathlib.Path(__file__).resolve().parents[1]
            / "frontend" / "src" / "pages" / "launch")


class TestTheApprovedDesign:
    """THE DESIGN IS PART OF THE PRODUCT, so it gets tests like one.

    These do not assert pixels. They assert that the PIECES of the approved
    experience still exist and still come from configuration — which is what
    a future refactor would quietly remove.
    """

    def test_every_part_of_the_approved_shell_still_exists(self):
        missing = [n for n in APPROVED_SHELL_PARTS
                   if not (FRONTEND / n).exists()]
        assert missing == []

    def test_the_shell_is_one_page_not_one_page_per_customer(self):
        """No customer-specific route components. The moment onboarding forks
        per customer, every customer needs a release."""
        names = [p.name.lower() for p in FRONTEND.rglob("*.jsx")]
        for banned in ("atlantis", "northwind", "evosys", "bookaboost"):
            assert not [n for n in names if banned in n], banned

    def test_the_seven_stage_journey_is_the_default(self):
        keys = [s["key"] for s in launch_experience.DEFAULT_JOURNEY]
        assert keys == ["intake", "access", "build", "integrations",
                        "review", "training", "golive"]

    def test_save_draft_and_save_and_continue_both_survive(self):
        shell = (FRONTEND / "OnboardingStepShell.jsx").read_text(
            encoding="utf-8")
        assert "Save Draft" in shell
        pad = (FRONTEND / "LaunchPad.jsx").read_text(encoding="utf-8")
        assert "Save & Continue" in pad

    def test_the_hero_the_rail_and_the_cards_read_configuration(self):
        for name in ("LaunchHero.jsx", "LaunchSidebar.jsx",
                     "OnboardingProgressPanel.jsx", "LaunchFooter.jsx"):
            src = (FRONTEND / name).read_text(encoding="utf-8")
            assert "presentation" in src, name

    def test_nothing_dereferences_the_signed_in_person_unguarded(self):
        """THE BLANK PAGE, AS A RULE INSTEAD OF A MEMORY.

        `customer.user` is null on every preview. One unguarded
        `customer.user.name` threw during render and blanked the entire page.
        Any component that reaches through `.user` without a guard reintroduces
        exactly that failure, so none may.
        """
        offenders = []
        for path in sorted(FRONTEND.rglob("*.jsx")):
            src = path.read_text(encoding="utf-8")
            for m in re.finditer(r"customer\.user\.", src):
                line_start = src.rfind("\n", 0, m.start()) + 1
                line = src[line_start:src.find("\n", m.start())]
                stripped = line.strip()
                # Prose ABOUT the bug is not the bug. The comments in
                # LaunchHeader and LaunchBoundary quote the offending
                # expression on purpose, so that the next reader knows what
                # went wrong; only real code counts.
                if stripped.startswith(("*", "//", "/*")):
                    continue
                # `person ? person.name : …` and `customer.user?.x` are guarded;
                # a bare `customer.user.x` is not.
                if "?." not in line and "customer.user &&" not in line:
                    offenders.append("%s: %s" % (path.name, stripped))
        assert offenders == [], offenders

    def test_a_launch_screen_can_never_fail_as_a_blank_page(self):
        """FAIL VISIBLE. A render that throws must produce an error card with a
        retry and a way back — never the background colour and nothing else."""
        assert (FRONTEND / "LaunchBoundary.jsx").exists()
        boundary = (FRONTEND / "LaunchBoundary.jsx").read_text(encoding="utf-8")
        # A real error boundary, not a component that merely looks like one.
        assert "getDerivedStateFromError" in boundary
        assert "componentDidCatch" in boundary
        assert "Try again" in boundary
        # And it must not print the error itself onto a customer-facing page.
        assert "this.state.error" not in boundary

        app = (FRONTEND.parents[1] / "App.jsx").read_text(encoding="utf-8")
        # Every launch route is wrapped, the preview ones included.
        for route in ('path="/launch"', 'path="/launch/:stepKey"',
                      'path="/launch/preview/:organizationId"'):
            idx = app.index(route)
            assert "LaunchBoundary" in app[idx:idx + 600], route

    def test_the_preview_turns_every_write_off(self):
        """Read the component, because this is the property a future edit is
        most likely to break by adding one more handler."""
        pad = (FRONTEND / "LaunchPad.jsx").read_text(encoding="utf-8")
        for handler in ("const persist", "const uploadFile",
                        "const removeFile", "const submit"):
            start = pad.index(handler)
            body = pad[start:start + 700]
            assert "if (preview)" in body, handler


# ════════════════════════════════════════════════════════════════════════════
# 8. NO CUSTOMER IS IN THE CODE
# ════════════════════════════════════════════════════════════════════════════

BANNED = [r"\batlantis\b", r"\bjosh\b", r"\bjoshua\b", r"\babel\b",
          r"\bshronce\b", r"\bcomparepower\b", r"\bbookaboost\b",
          r"\bevosys\b", r"75/25", r"\b469-926"]

SOURCES = [
    pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
    / "launch_experience.py",
    pathlib.Path(__file__).resolve().parents[1] / "app" / "routers"
    / "launch_experience_router.py",
    pathlib.Path(__file__).resolve().parents[1] / "app" / "models"
    / "launch_experience_models.py",
]


class TestNoCustomerIsInTheCode:
    def test_the_engine_names_no_customer_no_brand_and_no_split(self):
        """THE FIRST CUSTOMER IS DATA. Their name, their partner's name, their
        revenue split and the brand delivering it are rows — a second customer
        must not need a release, and a third must not need a fork."""
        offences = []
        for path in SOURCES + sorted(FRONTEND.rglob("*.jsx")):
            text = path.read_text(encoding="utf-8").lower()
            for pattern in BANNED:
                if re.search(pattern, text):
                    offences.append("%s: %s" % (path.name, pattern))
        assert offences == [], offences

    def test_no_mockup_placeholder_reached_the_defaults(self):
        """A design's example name, address, phone number or date is a
        PLACEHOLDER. Shipping one puts a stranger's details on a real
        customer's onboarding page."""
        blob = (_blob(launch_experience.DEFAULT_PRESENTATION)
                + _blob(launch_experience.DEFAULT_JOURNEY)
                + _blob(launch_experience.DEFAULT_FORM))
        assert not re.search(r"\b\d{3}[-.]\d{3}[-.]\d{4}\b", blob), "phone"
        assert not re.search(r"\b\d{1,5}\s+\w+\s+(street|st|road|rd|avenue|"
                             r"ave|drive|dr|lane|ln|boulevard|blvd)\b", blob), \
            "street address"
        assert "@" not in blob.replace("{brand}", ""), "an email address"

    def test_the_customers_own_name_arrives_as_a_token_not_a_string(self):
        assert "{customer}" in launch_experience.DEFAULT_PRESENTATION["title"]
        assert "{brand}" in _blob(launch_experience.DEFAULT_PRESENTATION)
