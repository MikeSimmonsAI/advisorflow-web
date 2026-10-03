"""UPCOMING APPOINTMENTS: THE REAL COUNT, NOT THE CAP.

GET /availability/upcoming returned at most 20 rows for one advisor (100
org-wide) and the Availability page showed the list's length as "Upcoming
appointments". with_total=true now returns {items, total}; the bare list is
unchanged for older callers.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from app.models.models import BookingLink, Lead


def _book(db, org, advisor, n, *, hours=24):
    for i in range(n):
        lead = Lead(organization_id=org.id, assigned_to_id=advisor.id, first_name="U%d" % i,
                    last_name="T", status="sent")
        db.add(lead)
        db.flush()
        db.add(BookingLink(lead_id=lead.id, user_id=advisor.id, status="booked",
                           booked_time=datetime.now() + timedelta(hours=hours + i)))
    db.commit()


def test_with_total_reports_the_real_count(client, db_session, sample_org, sample_advisor,
                                           auth_headers):
    _book(db_session, sample_org, sample_advisor, 25)
    _book(db_session, sample_org, sample_advisor, 2, hours=-48)   # past - not upcoming
    bare = client.get("/availability/upcoming", headers=auth_headers).json()
    assert isinstance(bare, list) and len(bare) == 25           # was capped at 20
    with patch("app.routers.availability_router.UPCOMING_CAP", 10):
        d = client.get("/availability/upcoming?with_total=true", headers=auth_headers).json()
    assert len(d["items"]) == 10 and d["total"] == 25
    assert d["items"][0]["lead_name"] == "U0 T"                  # soonest first


def test_availability_page_asks_for_the_total():
    src = open("frontend/src/pages/Availability.jsx", encoding="utf-8").read()
    assert src.count("with_total=true") >= 2 and "upcomingTotal" in src
