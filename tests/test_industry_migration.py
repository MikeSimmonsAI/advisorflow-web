"""REPAIRING AN ORGANIZATION THAT WAS PROVISIONED WRONG.

The customer that surfaced the defect already exists in production, has been
configured for its commercial arrangement, and must not be recreated. So the
repair has to be a migration, and a migration that cannot tell inherited
defaults from somebody's work is a migration that destroys the work.

These tests hold the line that makes it safe:

  an UNTOUCHED default is replaced,
  a CUSTOMIZED value is preserved and reported,
  nothing at all happens without a reason, and
  the whole thing is on the audit log afterwards.
"""

import itertools
import json

import pytest

from app.models.models import (
    AuditLogEntry, Organization, Platform, TierDefinition, User,
)
from app.services import industry_migration as migrate
from app.services import industry_templates as templates
from app.services import tier_config_service as tiers
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _platform(db):
    p = Platform(name="EvoSys Pro", slug="evo-%d" % next(_SEQ), short_name="EP",
                 tagline="t", support_email="s@example.test")
    db.add(p)
    db.commit()
    return p


def _org(db, plat, **kwargs):
    o = Organization(name="Repair Target", slug="repair-%d" % next(_SEQ),
                     platform_id=plat.id, plan="standard", is_active=True,
                     **kwargs)
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
    plat = _platform(db_session)
    # An organization in exactly the state the defect produced: created with a
    # vertical it has nothing to do with, holding that vertical's seeded
    # defaults and never edited by anybody.
    org = _org(db_session, plat, industry="funeral",
               tier_config=json.dumps(templates.lead_tiers("funeral")),
               appointment_types=json.dumps(templates.appointment_types("funeral")))
    tiers.seed_default_tier_definitions(db_session, org.id, industry="funeral")
    db_session.commit()
    return {"plat": plat, "org": org,
            "god": _user(db_session, None, "god_admin", "owner"),
            "admin": _user(db_session, org, "org_admin", "admin")}


class TestClassification:
    def test_seeded_defaults_are_recognised_as_inherited(self, db_session, world):
        state = migrate.classify(db_session, world["org"])
        assert state[migrate.SURFACE_TIER_CONFIG]["state"] == migrate.STATE_UNTOUCHED
        assert state[migrate.SURFACE_TIER_CONFIG]["matches_template"] == "funeral"
        assert state[migrate.SURFACE_APPOINTMENT_TYPES]["state"] == migrate.STATE_UNTOUCHED
        assert state[migrate.SURFACE_TIER_DEFINITIONS]["state"] == migrate.STATE_UNTOUCHED

    def test_an_empty_surface_is_empty_not_customized(self, db_session, world):
        world["org"].appointment_types = None
        db_session.commit()
        state = migrate.classify(db_session, world["org"])
        assert state[migrate.SURFACE_APPOINTMENT_TYPES]["state"] == migrate.STATE_EMPTY

    def test_an_edited_surface_is_customized(self, db_session, world):
        world["org"].appointment_types = json.dumps(
            ["Site Visit", "Rate Review", "Something Their Own"])
        db_session.commit()
        state = migrate.classify(db_session, world["org"])
        assert state[migrate.SURFACE_APPOINTMENT_TYPES]["state"] == migrate.STATE_CUSTOMIZED

    def test_a_renamed_tier_makes_the_whole_surface_customized(
            self, db_session, world):
        rows = templates.lead_tiers("funeral")
        rows[0]["label"] = "Advance Planning"       # their own word for it
        world["org"].tier_config = json.dumps(rows)
        db_session.commit()
        state = migrate.classify(db_session, world["org"])
        assert state[migrate.SURFACE_TIER_CONFIG]["state"] == migrate.STATE_CUSTOMIZED


class TestPreview:
    def test_preview_writes_nothing(self, db_session, world):
        before = (world["org"].industry, world["org"].tier_config,
                  world["org"].appointment_types)
        migrate.preview(db_session, world["org"], "energy")
        assert (world["org"].industry, world["org"].tier_config,
                world["org"].appointment_types) == before

    def test_preview_names_every_surface_and_what_happens_to_it(
            self, db_session, world):
        plan = migrate.preview(db_session, world["org"], "energy")
        planned = {row["surface"] for row in plan["planned"]}
        assert planned == {migrate.SURFACE_TIER_CONFIG,
                           migrate.SURFACE_APPOINTMENT_TYPES,
                           migrate.SURFACE_TIER_DEFINITIONS}
        assert plan["preserved"] == []
        assert plan["target_industry"] == "energy"
        assert plan["safety"]["sends_nothing"] is True
        for row in plan["planned"]:
            assert row["reason"]

    def test_customized_surfaces_are_listed_as_preserved(self, db_session, world):
        world["org"].appointment_types = json.dumps(["Their Own Thing"])
        db_session.commit()
        plan = migrate.preview(db_session, world["org"], "energy")
        preserved = {row["surface"] for row in plan["preserved"]}
        assert migrate.SURFACE_APPOINTMENT_TYPES in preserved
        planned = {row["surface"] for row in plan["planned"]}
        assert migrate.SURFACE_APPOINTMENT_TYPES not in planned


class TestApply:
    def test_a_migration_without_a_reason_is_refused_and_changes_nothing(
            self, db_session, world):
        from fastapi import HTTPException
        before = world["org"].industry
        with pytest.raises(HTTPException) as exc:
            migrate.apply(db_session, world["org"], world["god"], "energy",
                          reason="   ")
        assert exc.value.status_code == 400
        assert world["org"].industry == before

    def test_inherited_defaults_are_replaced(self, db_session, world):
        result = migrate.apply(db_session, world["org"], world["god"], "energy",
                               reason="Provisioned with the wrong business type.")
        db_session.commit()

        org = world["org"]
        assert org.industry == "energy"

        tier_values = [t["value"] for t in json.loads(org.tier_config)]
        assert "rate_review" in tier_values
        assert "pre_need" not in tier_values

        appts = json.loads(org.appointment_types)
        assert "Energy Rate Review" in appts
        assert "At-Need Arrangement Conference" not in appts

        rows = {r.tier_key for r in db_session.query(TierDefinition)
                .filter(TierDefinition.organization_id == org.id).all()}
        assert "renewal_due" in rows
        assert "pre_need" not in rows
        assert set(result["applied"]) == {migrate.SURFACE_TIER_CONFIG,
                                          migrate.SURFACE_APPOINTMENT_TYPES,
                                          migrate.SURFACE_TIER_DEFINITIONS}

    def test_customized_configuration_survives_the_migration(
            self, db_session, world):
        theirs = ["Site Survey", "Rate Review", "Renewal Call"]
        world["org"].appointment_types = json.dumps(theirs)
        db_session.commit()

        result = migrate.apply(db_session, world["org"], world["god"], "energy",
                               reason="Repairing an incorrect industry.")
        db_session.commit()

        assert json.loads(world["org"].appointment_types) == theirs
        assert migrate.SURFACE_APPOINTMENT_TYPES in result["preserved"]
        assert migrate.SURFACE_APPOINTMENT_TYPES not in result["applied"]
        # and the surfaces that WERE inherited still got repaired
        assert migrate.SURFACE_TIER_CONFIG in result["applied"]

    def test_overwriting_customization_is_possible_but_must_be_asked_for(
            self, db_session, world):
        theirs = ["Site Survey"]
        world["org"].appointment_types = json.dumps(theirs)
        db_session.commit()

        result = migrate.apply(db_session, world["org"], world["god"], "energy",
                               reason="Customer asked for a clean reset.",
                               replace_customized=True)
        db_session.commit()
        assert json.loads(world["org"].appointment_types) != theirs
        assert migrate.SURFACE_APPOINTMENT_TYPES in result["applied"]

    def test_the_migration_is_audited_with_what_it_touched(
            self, db_session, world):
        migrate.apply(db_session, world["org"], world["god"], "energy",
                      reason="Provisioned with the wrong business type.")
        db_session.commit()

        entry = (db_session.query(AuditLogEntry)
                 .filter(AuditLogEntry.action == migrate.ACTION_APPLIED)
                 .first())
        assert entry is not None
        assert entry.target_id == world["org"].id
        assert entry.actor_user_id == world["god"].id
        assert "wrong business type" in (entry.note or "")
        assert "energy" in (entry.after_state or "")

    def test_a_migration_does_not_touch_the_commercial_agreement(
            self, db_session, world):
        """The repair path and the commercial work are independent, and the
        customer that needs both must not lose one to get the other."""
        from datetime import date

        from app.models.commercial_models import TYPE_REVENUE_SHARE
        from app.services.commercial import agreements as ag

        agreement = ag.create(db_session, world["god"],
                              platform_id=world["plat"].id,
                              organization_id=world["org"].id,
                              agreement_type=TYPE_REVENUE_SHARE,
                              effective_date=date.today())
        db_session.commit()
        before = (agreement.status, agreement.agreement_type, agreement.version)

        migrate.apply(db_session, world["org"], world["god"], "energy",
                      reason="Repair.")
        db_session.commit()

        db_session.refresh(agreement)
        assert (agreement.status, agreement.agreement_type,
                agreement.version) == before


class TestMigrationRoutes:
    def test_preview_over_http_writes_nothing(self, client, db_session, world):
        before = world["org"].industry
        r = client.post("/org-settings/industry/preview?org_id=%s" % world["org"].id,
                        json={"industry": "energy"},
                        headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        assert r.json()["target_industry"] == "energy"
        db_session.refresh(world["org"])
        assert world["org"].industry == before

    def test_apply_over_http_requires_a_reason(self, client, db_session, world):
        r = client.post("/org-settings/industry/apply?org_id=%s" % world["org"].id,
                        json={"industry": "energy"},
                        headers=_h(db_session, world["god"]))
        assert r.status_code == 400
        db_session.refresh(world["org"])
        assert world["org"].industry == "funeral"

    def test_only_the_platform_owner_may_overwrite_customization(
            self, client, db_session, world):
        world["org"].appointment_types = json.dumps(["Their Own Thing"])
        db_session.commit()

        r = client.post("/org-settings/industry/apply?org_id=%s" % world["org"].id,
                        json={"industry": "energy", "reason": "reset",
                              "replace_customized": True},
                        headers=_h(db_session, world["admin"]))
        assert r.status_code in (403, 404)
        db_session.refresh(world["org"])
        assert json.loads(world["org"].appointment_types) == ["Their Own Thing"]

    def test_the_ordinary_industry_patch_no_longer_destroys_customization(
            self, client, db_session, world):
        theirs = ["Their Own Thing"]
        world["org"].appointment_types = json.dumps(theirs)
        db_session.commit()

        r = client.patch("/org-settings/industry?org_id=%s" % world["org"].id,
                         json={"industry": "energy"},
                         headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        assert migrate.SURFACE_APPOINTMENT_TYPES in r.json()["preserved"]
        db_session.refresh(world["org"])
        assert json.loads(world["org"].appointment_types) == theirs
