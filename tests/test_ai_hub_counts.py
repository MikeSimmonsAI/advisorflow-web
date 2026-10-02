"""AI HUB COUNTS ARE THE SERVER'S, ON THE WORKSPACE'S DAY.

"Calls made today" was counted in the browser from the newest 100 rows of
/voice/calls using the browser's own calendar day: it could never pass 100 and
a manager in another zone saw a different "today" from the workspace's.
GET /voice/calls-summary counts on the server, scoped like /voice/calls (the
caller's own calls), using the workspace's timezone.
"""
from datetime import timedelta

from app.models.models import Lead, VoiceCall
from app.services import workspace_time


def _calls(db, org, advisor, lead, when, n):
    for i in range(n):
        db.add(VoiceCall(lead_id=lead.id, advisor_id=advisor.id, organization_id=org.id,
                         to_phone="12145557400", status="completed", created_at=when))
    db.commit()


def test_calls_today_counts_past_100_on_the_workspace_day(client, db_session, sample_org,
                                                          sample_advisor, auth_headers):
    sample_org.timezone = "America/Los_Angeles"
    db_session.commit()
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Hub", last_name="Count", phone="12145557400", status="new")
    db_session.add(lead)
    db_session.commit()
    start, end, tz = workspace_time.day_bounds(db_session, sample_org.id)
    assert tz == "America/Los_Angeles"
    _calls(db_session, sample_org, sample_advisor, lead, start + timedelta(minutes=1), 130)
    _calls(db_session, sample_org, sample_advisor, lead, start - timedelta(minutes=1), 7)  # yesterday, local
    r = client.get("/voice/calls-summary", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"today": 130, "timezone": "America/Los_Angeles"}


def test_ai_hub_uses_the_server_counts():
    src = open("frontend/src/pages/AIHub.jsx", encoding="utf-8").read()
    assert "/voice/calls-summary" in src
    assert "toDateString()" not in src          # no browser-day counting
    assert "stats.flagged_count" in src         # past the list cap, the server count
