"""P0 regression: four lead-ingestion paths were dead on every arrival.

═══════════════════════════════════════════════════════════════════════════
WHAT WAS BROKEN
═══════════════════════════════════════════════════════════════════════════

`social_webhooks_router`, `fiber_intake_router`, `fiber_leads_router` and
`lead_scraper_router` all construct `Lead(source=...)`. `Lead` had no `source`
column. SQLAlchemy's declarative constructor rejects unknown keyword
arguments, so every one of those calls raised

    TypeError: 'source' is an invalid keyword argument for Lead

at the moment of creating the lead. Every Facebook / Instagram / TikTok
lead-gen webhook, every public fiber intake submission, every door-knocker
field capture and every scraper import failed - on the line that mattered, in
the paths whose entire purpose is bringing leads in.

═══════════════════════════════════════════════════════════════════════════
WHY IT SURVIVED
═══════════════════════════════════════════════════════════════════════════

NOT ONE of those four routers had a test. The suite was green and the feature
was dead, which is the failure mode a green suite is least able to report.
That is what these tests are for: they are deliberately about the mechanism
(can a lead be created at all through this shape) rather than about business
behaviour, because the mechanism is what was missing.
"""

import itertools

import pytest

from app.models.models import Lead, Organization, Platform
from app.services import lead_capacity

_SEQ = itertools.count(31000)


def _org(db):
    n = next(_SEQ)
    p = Platform(name="Src", slug="src-%d" % n)
    db.add(p); db.commit()
    org = Organization(name="Src Org %d" % n, slug="src-org-%d" % n,
                       platform_id=p.id, is_active=True, plan="trial")
    db.add(org); db.commit()
    return org


def test_lead_accepts_a_source_keyword(db_session):
    """THE regression. This exact call raised TypeError in production."""
    org = _org(db_session)
    lead = Lead(organization_id=org.id, first_name="Social",
                last_name="Arrival", source="facebook", status="new")
    db_session.add(lead)
    db_session.commit()

    assert lead.id is not None
    assert lead.source == "facebook"


@pytest.mark.parametrize("source", [
    "facebook",       # social_webhooks_router
    "instagram",      # social_webhooks_router
    "tiktok",         # social_webhooks_router
    "fiber_intake",   # fiber_intake_router
    "fiber_field",    # fiber_leads_router
    "google_places_sms",    # lead_scraper_router
    "google_places_email",  # lead_scraper_router
    "google_places_voice",  # lead_scraper_router
])
def test_every_source_value_the_four_routers_write_is_storable(db_session, source):
    org = _org(db_session)
    lead = Lead(organization_id=org.id, first_name="A", last_name="B",
                source=source, status="new")
    db_session.add(lead)
    db_session.commit()
    assert lead.source == source


def test_source_is_separate_from_source_file(db_session):
    """`source_file` means "the spreadsheet this row came out of".

    Reusing it for "facebook" would corrupt the one column import
    traceability depends on, which is why a new column was added rather than
    the four routers being rewritten onto the existing one.
    """
    org = _org(db_session)
    lead = Lead(organization_id=org.id, first_name="A", last_name="B",
                source="facebook", source_file="2024_purchased_list.xlsx",
                source_category="organic", status="new")
    db_session.add(lead)
    db_session.commit()

    assert lead.source == "facebook"
    assert lead.source_file == "2024_purchased_list.xlsx"
    assert lead.source_category == "organic"


def test_source_defaults_to_null_for_leads_that_do_not_set_it(db_session):
    """Every pre-existing row. The column must be optional."""
    org = _org(db_session)
    lead = Lead(organization_id=org.id, first_name="A", last_name="B",
                status="new")
    db_session.add(lead)
    db_session.commit()
    assert lead.source is None


def test_the_source_column_is_in_the_live_migration_path(db_session):
    """Alembic is dead here; auto_migrate is what creates columns in prod.

    A model-only column would work in tests (create_all builds the table from
    the model) and fail in production (create_all never ADDS a column to an
    existing table). That asymmetry is precisely how a column can pass the
    whole suite and still not exist on Render.
    """
    from app.auto_migrate import COLUMNS_TO_ADD
    pairs = {(t, c) for t, c, _type in COLUMNS_TO_ADD}
    for col in ("source", "capacity_state", "capacity_held_at",
                "capacity_hold_reason", "capacity_released_at"):
        assert ("leads", col) in pairs, (
            "leads.%s is on the model but not in COLUMNS_TO_ADD - it will "
            "never be created on an existing production database." % col)
