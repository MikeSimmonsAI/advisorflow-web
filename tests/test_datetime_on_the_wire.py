"""A bare (naive, UTC) datetime in a response says it is UTC.

Pins the behaviour of time_fmt.install_utc_json (installed by app.main):
naive datetimes go out with a trailing Z, aware ones keep their offset,
dates stay dates - checked on a real route (auto-send queue).
"""
import datetime

from fastapi.encoders import jsonable_encoder


def test_naive_gets_z_aware_and_date_unchanged():
    import app.main  # noqa: F401  - installs the encoder
    out = jsonable_encoder({"n": datetime.datetime(2026, 10, 2, 20, 0, 0),
                            "a": datetime.datetime(2026, 10, 2, 20, tzinfo=datetime.timezone.utc),
                            "d": datetime.date(2026, 10, 2)})
    assert out == {"n": "2026-10-02T20:00:00Z", "a": "2026-10-02T20:00:00+00:00", "d": "2026-10-02"}


def test_auto_send_queue_times_are_utc(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import Lead
    from app.routers.auto_send_router import AutoSendItem
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Z",
                last_name="Wire", phone="12145559911", status="new")
    db_session.add(lead); db_session.commit()
    db_session.add(AutoSendItem(organization_id=sample_org.id, lead_id=lead.id, advisor_id=sample_advisor.id,
                                message="hi", status="pending", created_at=datetime.datetime(2026, 10, 2, 20)))
    db_session.commit()
    rows = client.get("/auto-send/queue", headers=auth_headers).json()
    rows = rows if isinstance(rows, list) else rows.get("items", rows.get("queue", []))
    assert any(r.get("created_at") == "2026-10-02T20:00:00Z" for r in rows)

