"""Owner can correct a team member's availability (2026-11-18: a Saturday
block saved as 8 AM-11 PM hid every Saturday time); nobody else can."""
import pytest
from fastapi import HTTPException

from app.models.scheduling_models import AvailabilityBlock
from app.routers import sales_scheduling_router as R
from app.services import availability as av

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, manager, _make_sales_user, _SEQ,
)


def test_owner_fixes_a_members_blocks_only_for_that_member(db_session, manager):
    other = _make_sales_user(db_session, "other%d@evosys.live" % next(_SEQ))
    god = _make_sales_user(db_session, "god%d@evosys.live" % next(_SEQ))
    R.put_my_availability(R.AvailabilityIn(recurring_blocks=[
        {"day_of_week": 5, "start_minute": 480, "end_minute": 1380, "label": "Unavailable"},
        {"day_of_week": 6, "start_minute": 480, "end_minute": 720, "label": "Unavailable"}]),
        user=manager, db=db_session)
    R.put_my_availability(R.AvailabilityIn(recurring_blocks=[
        {"day_of_week": 5, "start_minute": 480, "end_minute": 1380, "label": "Mine"}]), user=other, db=db_session)
    out = R.owner_put_availability(manager.id, R.AvailabilityIn(recurring_blocks=[
        {"day_of_week": 5, "start_minute": 480, "end_minute": 660, "label": "Unavailable"},
        {"day_of_week": 6, "start_minute": 480, "end_minute": 720, "label": "Unavailable"}]),
        god=god, db=db_session)
    assert sorted((b["day_of_week"], b["end_minute"]) for b in out["recurring_blocks"]) == [(5, 660), (6, 720)]
    prof_other = av.get_or_create_profile(db_session, other)
    assert [b.end_minute for b in db_session.query(AvailabilityBlock).filter_by(profile_id=prof_other.id)] == [1380]
    assert R.owner_get_availability(manager.id, god=god, db=db_session)["recurring_blocks"]
    with pytest.raises(HTTPException) as e:
        R.owner_get_availability("no-such-user", god=god, db=db_session)
    assert e.value.status_code == 404


def test_owner_routes_are_god_only():
    from app.deps import require_god
    for r in R.router.routes:
        if getattr(r, "path", "").startswith("/sales/availability/users/"):
            assert any(d.call is require_god for d in r.dependant.dependencies), r.path
