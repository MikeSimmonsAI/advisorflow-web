"""BOOKED TIMES ARE LOCAL WALL TIME - SENT WITH THEIR OFFSET.

BookingLink.booked_time is stored as the advisor's local wall time (the
booking flows write it so and the reminder texts print it as-is). The JSON
layer marks naive datetimes as UTC, so a 3:00 PM Central appointment went out
as "15:00Z" and Dallas browsers showed 10:00 AM. Readers now send it with the
advisor's offset, and decide "upcoming" in that zone, not on the server clock.
"""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.models import BookingLink, Lead, Organization, User
from app.services import booking_time as bkt
from app.services.auth_service import create_access_token, hash_password


def test_to_wire_carries_the_advisors_offset():
    assert bkt.to_wire(datetime(2026, 10, 3, 15, 0), "America/Chicago") == "2026-10-03T15:00:00-05:00"
    assert bkt.to_wire(datetime(2026, 12, 3, 15, 0), "America/Chicago") == "2026-12-03T15:00:00-06:00"
    assert bkt.to_wire(None, "America/Chicago") is None
    assert bkt.to_utc(datetime(2026, 10, 3, 15, 0), "America/Chicago") == datetime(2026, 10, 3, 20, 0)
    assert bkt.zone_for(type("U", (), {"booking_timezone": "Not/AZone"})()) == bkt.DEFAULT_ZONE


def _world(db, tz="America/Chicago"):
    org = Organization(name="Wire", slug="bw-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                       industry="insurance", is_active=True)
    db.add(org)
    db.commit()
    admin = User(organization_id=org.id, email="bw-%s@bw.test" % uuid.uuid4().hex[:6],
                 password_hash=hash_password("Pass12345!"), full_name="Admin", role="org_admin",
                 is_active=True, must_change_password=False, booking_timezone=tz)
    db.add(admin)
    db.commit()
    return org, admin


def _book(db, org, adv, local_dt, name):
    lead = Lead(organization_id=org.id, assigned_to_id=adv.id, first_name=name, last_name="X",
                status="booked")
    db.add(lead)
    db.flush()
    b = BookingLink(lead_id=lead.id, user_id=adv.id, status="booked", booked_time=local_dt)
    db.add(b)
    db.commit()
    return b


def test_an_appointment_two_hours_out_is_upcoming_and_shows_its_wall_time(client, db_session):
    db = db_session
    org, adv = _world(db)
    local_now = datetime.now(ZoneInfo("America/Chicago")).replace(tzinfo=None, microsecond=0)
    soon = _book(db, org, adv, local_now + timedelta(hours=2), "Soon")
    gone = _book(db, org, adv, local_now - timedelta(hours=2), "Gone")
    h = {"Authorization": "Bearer %s" % create_access_token(adv, db)}

    appts = client.get("/pipeline/appointments?days=2", headers=h).json()
    by = {i["lead_name"]: i for i in appts["items"]}
    assert by["Soon X"]["upcoming"] is True and by["Gone X"]["upcoming"] is False
    assert appts["totals"] == {"upcoming": 1, "past": 1}
    wire = by["Soon X"]["booked_time"]
    assert wire.startswith(soon.booked_time.isoformat()) and wire[-6:] in ("-05:00", "-06:00")

    up = client.get("/availability/upcoming?with_total=true", headers=h).json()
    assert [i["id"] for i in up["items"]] == [soon.id] and up["total"] == 1
    assert up["items"][0]["booked_time"] == wire
    assert gone.id not in [i["id"] for i in up["items"]]
