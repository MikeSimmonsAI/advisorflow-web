"""INDUSTRY DEFAULTS AND BRAND BOUNDARIES.

THE PRODUCTION DEFECT THIS FILE LOCKS SHUT
------------------------------------------
A brand-new customer in an energy business was created under one white-label
brand and opened its settings to find:

  * funeral-home lead tiers — Pre-Need, At-Need, Imminent, Contract Sold
  * funeral-home appointment types — "At-Need Arrangement Conference"
  * a branding preview naming a DIFFERENT brand's product

Three separate industry maps each fell back to funeral for an industry they
did not recognise, the Organization row itself defaulted to `industry =
"funeral"`, and the brand shown on the settings page was a string in a JSX
file rather than the organization's own platform.

Every test below fails if any one of those returns.

WHAT IT DOES NOT DO
-------------------
It does not assert that funeral defaults are gone. They are legitimate
configuration for a funeral home and a test asserts they still work. What it
asserts is that they are never anybody's FALLBACK.
"""

import itertools
import json

import pytest

from app.models.models import Organization, Platform, TierDefinition, User
from app.services import industry_migration, industry_templates as templates
from app.services import tier_config_service as tiers
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)

FUNERAL_WORDS = ("pre_need", "at_need", "imminent", "contract_sold",
                 "pre-need", "at-need")
FUNERAL_APPOINTMENTS = ("At-Need Arrangement Conference",
                        "Pre-Need Planning Consultation",
                        "Immediate Need Consultation")


def _platform(db, name, slug):
    p = Platform(name=name, slug="%s-%d" % (slug, next(_SEQ)), short_name=name[:2],
                 tagline="t", support_email="support@%s.test" % slug)
    db.add(p)
    db.commit()
    return p


def _org(db, plat, name, industry=None):
    kwargs = {}
    if industry is not None:
        kwargs["industry"] = industry
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


# ════════════════════════════════════════════════════════════════════════════
# THE REGISTRY
# ════════════════════════════════════════════════════════════════════════════

class TestIndustryResolution:
    def test_nothing_resolves_to_generic_never_to_a_vertical(self):
        for value in (None, "", "   ", "something nobody has heard of",
                      "widgets", "???"):
            assert templates.normalize(value) == templates.GENERIC_KEY, value
            assert templates.is_known(value) is False, value

    def test_generic_defaults_carry_no_vertical_vocabulary(self):
        tier_values = [t["value"] for t in templates.lead_tiers(None)]
        for word in FUNERAL_WORDS:
            assert word not in tier_values
        appointments = templates.appointment_types(None)
        for word in FUNERAL_APPOINTMENTS:
            assert word not in appointments

    def test_a_real_industry_resolves_however_it_is_written(self):
        for value in ("Energy / Energy Procurement", "energy",
                      "ENERGY_PROCUREMENT", "Energy  Procurement",
                      "utilities"):
            assert templates.normalize(value) == "energy", value
            assert templates.is_known(value) is True, value

    def test_energy_gets_energy_vocabulary(self):
        summary = templates.summary("energy")
        tier_values = [t["value"] for t in summary["lead_tiers"]]
        assert "rate_review" in tier_values
        assert "renewal_due" in tier_values
        assert "Energy Rate Review" in summary["appointment_types"]
        assert "Commercial Energy Consultation" in summary["appointment_types"]
        for word in FUNERAL_WORDS:
            assert word not in tier_values
        for word in FUNERAL_APPOINTMENTS:
            assert word not in summary["appointment_types"]
        assert summary["segments"] == ["Residential", "Commercial / B2B"]

    def test_funeral_is_still_a_first_class_industry(self):
        """The fix removed a fallback, not an industry."""
        summary = templates.summary("funeral")
        tier_values = [t["value"] for t in summary["lead_tiers"]]
        assert {"pre_need", "at_need", "imminent", "contract_sold"} <= set(tier_values)
        assert "At-Need Arrangement Conference" in summary["appointment_types"]

    def test_every_template_has_the_pieces_the_platform_reads(self):
        for key, tpl in templates.TEMPLATES.items():
            assert tpl["lead_tiers"], key
            assert tpl["appointment_types"], key
            assert tpl["crm_stages"], key
            assert tpl["vocabulary"], key
            # Every tier-definition key must resolve to real rows, or seeding
            # an org of that industry silently produces nothing.
            assert tiers.get_tier_set_for_industry(tpl["tier_definition_key"]), key

    def test_the_industry_list_is_one_list(self):
        """The settings router and the appointment router read the SAME map.

        They each had their own, which is how they disagreed."""
        from app.routers import org_settings_router, settings_router
        assert org_settings_router.DEFAULT_TIERS is templates.DEFAULT_TIERS
        assert settings_router.INDUSTRY_APPT_TYPES is templates.INDUSTRY_APPT_TYPES


# ════════════════════════════════════════════════════════════════════════════
# PROVISIONING
# ════════════════════════════════════════════════════════════════════════════

class TestProvisioningDefaults:
    def test_a_new_organization_does_not_claim_to_be_a_funeral_home(self, db_session):
        plat = _platform(db_session, "Brand One", "brand-one")
        org = _org(db_session, plat, "Some Company")
        assert org.industry == templates.GENERIC_KEY

    def test_creating_a_customer_without_an_industry_gets_neutral_defaults(
            self, db_session):
        from app.services import customer_provisioning as cp

        plat = _platform(db_session, "Brand One", "brand-one")
        actor = _user(db_session, None, "god_admin", "owner")
        org, _ = cp.create_customer(db_session, actor, name="Unstated Co",
                                    platform_id=plat.id)
        db_session.commit()
        assert org.industry == templates.GENERIC_KEY

        tier_values = [t["value"] for t in templates.lead_tiers(org.industry)]
        for word in FUNERAL_WORDS:
            assert word not in tier_values

    def test_creating_a_customer_with_an_industry_stores_the_canonical_key(
            self, db_session):
        from app.services import customer_provisioning as cp

        plat = _platform(db_session, "Brand One", "brand-one")
        actor = _user(db_session, None, "god_admin", "owner")
        org, _ = cp.create_customer(db_session, actor, name="Energy Co",
                                    platform_id=plat.id,
                                    industry="Energy / Energy Procurement")
        db_session.commit()
        assert org.industry == "energy"

    def test_seeding_tier_definitions_without_an_industry_is_not_funeral(
            self, db_session):
        plat = _platform(db_session, "Brand One", "brand-one")
        org = _org(db_session, plat, "Unstated Co")
        created = tiers.seed_default_tier_definitions(db_session, org.id)
        keys = {row.tier_key for row in created}
        assert keys
        assert not (keys & {"pre_need", "at_need", "imminent"})


# ════════════════════════════════════════════════════════════════════════════
# BRAND BOUNDARIES
# ════════════════════════════════════════════════════════════════════════════

class TestBrandBoundaries:
    def test_each_customer_sees_its_own_brand_and_no_other(
            self, client, db_session):
        """Three brands, three customers, three identities. No crossover, and
        no shared default underneath them."""
        brands = [("EvoSys Pro", "evosyspro"), ("BookaBoost", "bookaboost"),
                  ("Harmony Hustle", "harmonyhustle")]
        seen = []
        for name, slug in brands:
            plat = _platform(db_session, name, slug)
            org = _org(db_session, plat, "%s Customer" % name)
            admin = _user(db_session, org, "org_admin", "admin")
            body = client.get("/org-settings/platform-identity",
                              headers=_h(db_session, admin)).json()
            assert body["brand_name"] == name, name
            assert body["engine"] == "AdvisorFlow"
            hierarchy = [level["label"] for level in body["hierarchy"]]
            assert hierarchy == ["AdvisorFlow", name, org.name]
            seen.append((name, body["brand_name"]))

        # and nobody got anybody else's
        assert len({brand for _, brand in seen}) == 3
        for expected, actual in seen:
            assert expected == actual

    def test_an_unrecognised_host_is_not_silently_one_brand(self, db_session):
        """`config_for_host` returned a specific, real, operating brand for
        every host it could not place — which is how one brand's name reached
        another brand's customer."""
        from app.services import brand_config

        cfg = brand_config.config_for_host(db_session, "something-unrelated.test")
        assert (cfg.get("display_name") or "").lower() != "bookaboost"
        assert (cfg.get("slug") or "") != "bookaboost"

    def test_the_settings_payload_names_the_brand_from_the_platform_row(
            self, client, db_session):
        plat = _platform(db_session, "EvoSys Pro", "evosyspro")
        org = _org(db_session, plat, "Energy Customer", industry="energy")
        admin = _user(db_session, org, "org_admin", "admin")

        body = client.get("/org-settings/", headers=_h(db_session, admin)).json()
        assert body["platform"]["brand_name"] == "EvoSys Pro"
        assert body["industry"] == "energy"
        assert body["industry_matched"] is True
        tier_values = [t["value"] for t in body["tier_config"]]
        for word in FUNERAL_WORDS:
            assert word not in tier_values

    def test_a_customer_with_no_configuration_gets_neutral_settings(
            self, client, db_session):
        plat = _platform(db_session, "EvoSys Pro", "evosyspro")
        org = _org(db_session, plat, "Unstated Customer")
        admin = _user(db_session, org, "org_admin", "admin")

        body = client.get("/org-settings/", headers=_h(db_session, admin)).json()
        tier_values = [t["value"] for t in body["tier_config"]]
        for word in FUNERAL_WORDS:
            assert word not in tier_values

        appts = client.get("/settings/appointment-types",
                           headers=_h(db_session, admin)).json()
        for word in FUNERAL_APPOINTMENTS:
            assert word not in appts["appointment_types"]

    def test_a_funeral_customer_still_gets_funeral_settings(
            self, client, db_session):
        plat = _platform(db_session, "EvoSys Pro", "evosyspro")
        org = _org(db_session, plat, "Memorial Customer", industry="funeral")
        admin = _user(db_session, org, "org_admin", "admin")

        appts = client.get("/settings/appointment-types",
                           headers=_h(db_session, admin)).json()
        assert "At-Need Arrangement Conference" in appts["appointment_types"]

    def test_settings_sections_declare_who_owns_what(self, client, db_session):
        plat = _platform(db_session, "EvoSys Pro", "evosyspro")
        org = _org(db_session, plat, "Some Customer")
        admin = _user(db_session, org, "org_admin", "admin")

        body = client.get("/org-settings/sections",
                          headers=_h(db_session, admin)).json()
        sections = {s["key"]: s for s in body["sections"]}
        assert sections["platform_brand"]["owner"] == "platform"
        assert sections["platform_brand"]["can_edit"] is False
        assert sections["advanced"]["can_edit"] is False
        assert sections["organization_profile"]["can_edit"] is True
