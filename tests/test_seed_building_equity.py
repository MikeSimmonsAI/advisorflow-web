"""scripts/seed_building_equity.py - against the in-memory TEST database only.

Idempotent, applies the Wholesale blueprint, automation OFF, and creates NO
user (Derrick Davis's credentials are never invented)."""
import importlib.util
import json
import os

import pytest

from app.models.models import Organization, Platform, User
from app.services.auth_service import hash_password

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "seed_building_equity.py")


def _module():
    spec = importlib.util.spec_from_file_location("seed_building_equity", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def platform_and_owner(db_session):
    plat = Platform(name="EvoSys Pro", slug="evosyspro")
    db_session.add(plat)
    db_session.commit()
    god = User(organization_id=None, email="owner@platform.test", password_hash=hash_password("Pass12345!"),
               full_name="Platform Owner", role="god_admin", must_change_password=False)
    db_session.add(god)
    db_session.commit()
    return plat, god


def _run(db, apply=True):
    lines = []
    report = _module().seed(db, apply=apply, out=lines.append)
    return report, "\n".join(lines)


def test_seed_creates_the_pilot_org_with_automation_off_and_no_user(db_session, platform_and_owner):
    from app.models.evosense_models import EvoSenseControl
    from app.models.wholesale_models import WholesaleSettings
    from app.models.wholesale_ops_models import WholesalePilotControl
    plat, _ = platform_and_owner
    report, text = _run(db_session)
    org = db_session.query(Organization).filter(Organization.slug == "building-equity-investments").one()
    assert org.name == "Building Equity Investments LLC" and org.platform_id == plat.id
    feats = json.loads(org.enabled_features)
    assert "wholesale_real_estate" in feats and "leads" in feats
    from app.services import org_blueprints
    assert org_blueprints.select_blueprint(org)[0] == "wholesale_real_estate"
    s = db_session.query(WholesaleSettings).filter(WholesaleSettings.organization_id == org.id).one()
    assert not any([s.auto_enrich_on_import, s.auto_stage_on_enrichment, s.auto_qualify_on_reply,
                    s.auto_analysis_on_qualified, s.auto_match_on_contract, s.enrichment_auto,
                    s.sms_program_enabled, s.inquiry_email_enabled])
    ctl = db_session.query(EvoSenseControl).filter(EvoSenseControl.organization_id == org.id).one()
    assert ctl.paused_sms and ctl.paused_email and ctl.paused_voice
    pilot = db_session.query(WholesalePilotControl).filter(
        WholesalePilotControl.organization_id == org.id).one()
    assert pilot.max_records == 250 and pilot.skip_trace_budget_cents == 0 and pilot.status == "draft"
    # No account for anyone - Derrick is invited by the owner through the normal flow.
    assert db_session.query(User).filter(User.organization_id == org.id).count() == 0
    assert report["user_created"] is False and "NOT created" in text
    assert "derrick" not in json.dumps(report).lower()


def test_seed_is_idempotent_and_keeps_admin_changes(db_session, platform_and_owner):
    from app.models.wholesale_models import WholesaleSettings
    _run(db_session)
    org = db_session.query(Organization).filter(Organization.slug == "building-equity-investments").one()
    s = db_session.query(WholesaleSettings).filter(WholesaleSettings.organization_id == org.id).one()
    s.auto_match_on_contract = True           # an admin's later choice
    db_session.commit()
    report, _ = _run(db_session)
    assert report["created"] == []
    assert db_session.query(Organization).filter(
        Organization.name == "Building Equity Investments LLC").count() == 1
    db_session.refresh(s)
    assert s.auto_match_on_contract is True


def test_dry_run_writes_nothing(db_session, platform_and_owner):
    _, text = _run(db_session, apply=False)
    assert "DRY RUN" in text
    assert db_session.query(Organization).filter(
        Organization.slug == "building-equity-investments").count() == 0


def test_refuses_without_the_brand(db_session):
    with pytest.raises(SystemExit):
        _run(db_session)
