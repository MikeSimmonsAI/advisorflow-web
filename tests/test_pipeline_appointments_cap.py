"""UPCOMING APPOINTMENTS ARE NEVER THE ONES CUT.

GET /pipeline/appointments returned one ascending list capped at 300, from
`days` ago forward. A busy workspace filled the cap with past appointments and
the upcoming ones - the ones a rep acts on - were dropped. Upcoming and past
are now capped separately, and the real totals come back with the page.
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from app.models.models import BookingLink, Lead, Organization, User
from app.services.auth_service import create_access_token, hash_password


def test_upcoming_survive_a_full_past_and_totals_are_real(client, db_session):
    db = db_session
    org = Organization(name="Appt Cap", slug="ac-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                       industry="energy", is_active=True)
    db.add(org)
    db.commit()
    admin = User(organization_id=org.id, email="ac-%s@ac.test" % uuid.uuid4().hex[:6],
                 password_hash=hash_password("Pass12345!"), full_name="Admin", role="org_admin",
                 is_active=True, must_change_password=False)
    db.add(admin)
    db.commit()
    now = datetime.utcnow()
    for i, hours in enumerate([-50, -40, -30, -20, -10, 5, 30]):
        lead = Lead(organization_id=org.id, assigned_to_id=admin.id, first_name="A%d" % i,
                    last_name="X", status="sent")
        db.add(lead)
        db.flush()
        db.add(BookingLink(lead_id=lead.id, user_id=admin.id, status="booked",
                           booked_time=now + timedelta(hours=hours)))
    db.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(admin, db)}
    with patch("app.routers.pipeline_router.APPOINTMENTS_CAP", 3):
        d = client.get("/pipeline/appointments?days=7", headers=h).json()
    names = [i["lead_name"] for i in d["items"]]
    assert [i["lead_name"] for i in d["items"] if i["upcoming"]] == ["A5 X", "A6 X"]
    assert names == ["A2 X", "A3 X", "A4 X", "A5 X", "A6 X"]   # most recent past kept, in time order
    assert d["totals"] == {"upcoming": 2, "past": 5}
    assert d["truncated"] is True


def test_pipeline_page_shows_the_server_totals():
    src = open("frontend/src/pages/Pipeline.jsx", encoding="utf-8").read()
    assert "totals?.upcoming" in src and "totals?.past" in src
