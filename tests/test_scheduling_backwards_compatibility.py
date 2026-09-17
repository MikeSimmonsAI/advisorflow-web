"""EXISTING SCHEDULING BEHAVIOUR DID NOT CHANGE. Asserted, not assumed.

The leadership quorum is a genuine change to how attendees are chosen, and it
would be easy for it to leak into the internal path - where a salesperson reads
a candidate list and picks, and where required_slots has meant "all of these
people" for the life of the feature.

It must not. A rep who books a Closing Call from inside the product in the
morning must get exactly what they got yesterday.

The guarantee has two halves and this file holds both:

  1. NULL policy is inert. Every meeting type that exists in every brand today
     has leadership_policy NULL, and a NULL policy makes the quorum engine do
     nothing - it does not walk a chain, does not require a leader, and does not
     alter participants.

  2. The internal booking path never consults the policy at all. It takes an
     explicit participant list from an authenticated salesperson, and that is
     still the only thing that decides who attends.
"""

import itertools

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER,
    ROLE_SALES_REP,
)
from app.models.scheduling_models import MeetingType, LEADERSHIP_REPORTING_CHAIN
from app.services import leadership_quorum as lq
from app.services import meeting_roles
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)


@pytest.fixture()
def brand(db_session):
    p = Platform(name="Compat Brand", slug="compat-plat-%d" % next(_SEQ))
    db_session.add(p)
    db_session.commit()
    b = BrandSalesOrg(platform_id=p.id, name="Compat Sales",
                      slug="compat-bso-%d" % next(_SEQ))
    db_session.add(b)
    db_session.commit()
    return b


def test_every_seeded_type_except_discovery_demo_stays_exactly_as_it_was(
        db_session, brand):
    """Discovery, Demo, Proposal, Closing and Internal keep NULL policy and
    stay closed to the public."""
    meeting_roles.ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    rows = {m.key: m for m in db_session.query(MeetingType).filter(
        MeetingType.brand_sales_org_id == brand.id).all()}

    for key in ("discovery", "discovery_60", "demo", "proposal", "closing",
                "internal"):
        mt = rows[key]
        assert mt.leadership_policy is None, (
            "%s acquired a leadership policy it was never configured with" % key)
        assert mt.public_bookable is False, (
            "%s became bookable from the internet" % key)
        assert lq.QuorumPolicy.from_meeting_type(mt).active is False


def test_discovery_demo_is_the_only_one_opened(db_session, brand):
    meeting_roles.ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    mt = db_session.query(MeetingType).filter(
        MeetingType.brand_sales_org_id == brand.id,
        MeetingType.key == "discovery_demo").one()
    assert mt.leadership_policy == LEADERSHIP_REPORTING_CHAIN
    assert mt.leadership_minimum == 1
    assert mt.leadership_depth == 2
    assert mt.include_additional_leaders is True
    assert mt.public_bookable is True


def test_required_slots_are_untouched_by_the_quorum(db_session, brand):
    """The internal Find Team Time screen reads these and must keep behaving
    exactly as it does today."""
    meeting_roles.ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    mt = db_session.query(MeetingType).filter(
        MeetingType.brand_sales_org_id == brand.id,
        MeetingType.key == "discovery_demo").one()
    assert mt.required_slot_list() == ["opportunity_owner", "sales_manager",
                                       "product_specialist"]


def test_a_brand_that_configured_its_own_policy_keeps_it(db_session, brand):
    meeting_roles.ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    mt = db_session.query(MeetingType).filter(
        MeetingType.key == "discovery_demo",
        MeetingType.brand_sales_org_id == brand.id).one()
    mt.leadership_minimum = 2
    mt.include_additional_leaders = False
    mt.public_bookable = False
    db_session.commit()

    meeting_roles.ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    db_session.refresh(mt)
    assert mt.leadership_minimum == 2
    assert mt.include_additional_leaders is False
    assert mt.public_bookable is False, (
        "a brand's deliberate decision not to take website bookings on this "
        "type was undone by a startup backfill")


def test_a_hand_edited_row_is_never_backfilled(db_session, brand):
    """The untouched-guard: updated_at != created_at means a human has been
    here, and their choice is permanent."""
    from datetime import datetime, timedelta
    mt = MeetingType(brand_sales_org_id=brand.id, key="discovery_demo",
                     name="Our Own Discovery + Demo", duration_minutes=45,
                     required_slots="opportunity_owner")
    db_session.add(mt)
    db_session.commit()
    mt.updated_at = (mt.created_at or datetime.utcnow()) + timedelta(hours=1)
    db_session.commit()

    meeting_roles.ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    db_session.refresh(mt)
    assert mt.leadership_policy is None
    assert mt.public_bookable is False
    assert mt.duration_minutes == 45


def test_seeding_twice_changes_nothing(db_session, brand):
    meeting_roles.ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    before = {(m.key, m.leadership_policy, m.public_bookable, m.duration_minutes)
              for m in db_session.query(MeetingType).filter(
                  MeetingType.brand_sales_org_id == brand.id).all()}
    meeting_roles.ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    after = {(m.key, m.leadership_policy, m.public_bookable, m.duration_minutes)
             for m in db_session.query(MeetingType).filter(
                 MeetingType.brand_sales_org_id == brand.id).all()}
    assert before == after


def test_the_internal_booking_route_never_reads_the_policy(db_session):
    """Asserted against the source, because a behavioural test would pass
    whether or not the coupling exists until somebody configures a policy.

    The internal path takes an explicit participant list from an authenticated
    salesperson. That is still the only thing deciding who attends.
    """
    import inspect
    from app.routers import sales_scheduling_router as r
    src = inspect.getsource(r.create_appointment)
    for name in ("leadership_policy", "leadership_minimum", "leadership_depth",
                 "include_additional_leaders", "leadership_quorum",
                 "QuorumPolicy"):
        assert name not in src, (
            "the internal booking route now consults %r - the quorum was "
            "supposed to apply to the public path only" % name)


def test_the_internal_route_still_requires_an_explicit_participant_list(db_session):
    import inspect
    from app.routers import sales_scheduling_router as r
    src = inspect.getsource(r.create_appointment)
    assert "required_user_ids" in src
    assert "At least one required participant is needed." in src


def test_brand_wide_manager_resolution_still_exists_for_the_internal_screen(
        db_session, brand):
    """meeting_roles.resolve_slot is NOT changed. It returns candidates for a
    human to choose from, which is correct for the screen it serves - the defect
    was only ever using it for an unattended public booking."""
    u = User(organization_id=None, email="compat%d@t.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name="A Manager",
             role="advisor", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    db_session.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=brand.id, role=ROLE_SALES_MANAGER,
                              is_active=True))
    db_session.commit()
    users, note = meeting_roles.resolve_slot(
        db_session, "sales_manager", brand.id)
    assert [x.id for x in users] == [u.id]
