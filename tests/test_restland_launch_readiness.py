"""RESTLAND launch readiness (Issue #507): one calendar target for every
write, the read-only provider/calendar evidence probe, and the volume
forecast. Synthetic data; no provider or calendar is contacted."""
from datetime import datetime
from types import SimpleNamespace

from app.models.calendar_models import CalendarConnection
from app.services.calendar_service import booking_calendar_id
from app.services.programs import restland_ffr as F
from app.services.programs import workspace_configure as WC
from tests.test_restland_booking_cadence import rl  # noqa: F401 (fixture)


def test_one_calendar_target_with_the_same_precedence_as_availability(rl):
    db, mike = rl["db"], rl["mike"]
    mike.google_calendar_id = None
    db.commit()
    assert booking_calendar_id(mike) == "primary"
    mike.google_calendar_id = "stored-cal"
    db.commit()
    assert booking_calendar_id(mike) == "stored-cal"
    db.add(CalendarConnection(user_id=mike.id, provider="google", is_connected=True, calendar_id="conn-cal"))
    db.commit()
    assert booking_calendar_id(mike) == "conn-cal"          # the row availability reads wins for writes too
    assert booking_calendar_id(None) == "primary"


def test_every_calendar_write_path_uses_the_one_target():
    import inspect
    from app.services import calendar_service
    from app.services.programs import appointment_ops
    src = inspect.getsource(calendar_service) + inspect.getsource(appointment_ops)
    assert "calendarId=advisor.google_calendar_id" not in src
    assert 'getattr(owner, "google_calendar_id", None) or "primary"' not in src
    assert src.count("booking_calendar_id(") >= 5


def _verification(**kw):
    base = dict(sid="HHx", tollfree_phone_number_sid="PN1", status="TWILIO_APPROVED", date_created=None,
                date_updated=datetime(2026, 11, 6, 18, 20), business_name="EVO Integrated Solutions LLC",
                business_website="https://example.test", business_city="Dallas", business_state_province_region="TX",
                use_case_categories=["ACCOUNT_NOTIFICATIONS"], use_case_summary="summary",
                production_message_sample="sample", opt_in_type="WEB_FORM", opt_in_image_urls=["https://x.test/a"],
                message_volume="10,000", additional_information=None, edit_allowed=False, edit_expiration=None,
                rejection_reason=None, rejection_reasons=None, error_code=None, external_reference_id=None,
                customer_profile_sid="BU1", trust_product_sid=None,
                business_contact_email="private@example.test", business_contact_phone="+15550100",
                business_contact_first_name="Pat", business_contact_last_name="Doe",
                notification_email="notify@example.test", business_street_address="1 Main St")
    base.update(kw)
    return SimpleNamespace(**base)


def test_verification_record_keeps_the_claims_and_drops_private_contact_details():
    rec = WC.verification_record(_verification(), "PN1")
    assert rec["matches_number_sid"] and rec["use_case_categories"] == ["ACCOUNT_NOTIFICATIONS"]
    assert rec["use_case_summary"] == "summary" and rec["production_message_sample"] == "sample"
    flat = repr(rec)
    for private in ("private@example.test", "+15550100", "Pat", "Doe", "notify@example.test", "1 Main St"):
        assert private not in flat
    assert WC.verification_record(_verification(), "PN2")["matches_number_sid"] is False


class _Events:
    def __init__(self, by_id):
        self.by_id = by_id

    def get(self, calendarId, eventId):
        ev = self.by_id[eventId]
        return SimpleNamespace(execute=lambda: ev)


def test_calendar_identity_reads_existing_events_and_masks_the_address(rl, monkeypatch):
    mike = rl["mike"]
    evs = {"e1": {"organizer": {"email": "someone@gmail.com", "self": True, "displayName": "Personal Calendar"},
                  "creator": {"email": "someone@gmail.com"}},
           "e2": {"organizer": {"email": "someone@gmail.com", "self": True}, "creator": {"email": "someone@gmail.com"}}}
    svc = SimpleNamespace(events=lambda: _Events(evs))
    import app.services.calendar_service as CS
    monkeypatch.setattr(CS, "_get_calendar_service", lambda u: svc)
    out = WC.calendar_identity(mike, ["e1", "e2"])
    assert out["events_checked"] == 2 and out["same_calendar"] is True and out["organizer_is_self"] is True
    assert out["organizers"] == ["so…@gmail.com"] and "someone@" not in repr(out)
    assert out["matches_login"] is False                     # a different Google account than the login


def test_forecast_counts_totals_per_family_and_a_real_segment_range():
    fc = F.forecast("https://book.example.test/b/" + "X" * 22, first_names=["Al", "Bartholomew-Alexander", "Zoë"])
    row15 = [r for r in fc["by_volume"] if r["new_families_per_day"] == 15][0]
    assert (row15["texts_per_day"], row15["emails_per_day_max"]) == (75, 60)
    rng = fc["segments_per_family_range"]
    assert rng["families_measured"] == 3 and rng["min"] <= rng["max"]
    assert row15["text_segments_per_day_range"] == [rng["min"] * 15, rng["max"] * 15]
    assert "in total over 14 days" in fc["assumptions"]
    assert "segments x" in fc["billing"]["charge_formula"] and set(fc["controls"]) == {
        "daily_intake", "outbound_workload", "provider_throughput"}


def test_forecast_measures_the_saved_copy_when_given():
    saved = {1: "Hi {first_name}, short.", 2: "x" * 300}
    fc = F.forecast("", texts=saved)
    assert fc["copy_source"] == "saved campaign copy"
    assert fc["segments_per_family_first_round"] == 1 + 2


def test_probe_is_read_only_and_reports_every_part(rl):
    from tests.test_restland_booking_cadence import configured
    from app.models.models import BookingLink
    db = rl["db"]
    configured(rl)
    before = db.query(BookingLink).count()
    out = WC.probe_only(db, rl["org"], rl["mike"].id)
    assert set(out) >= {"calendar", "calendar_identity", "sender", "copy_context"}
    assert "error" not in out["copy_context"], out["copy_context"]
    assert out["copy_context"]["location_name"]
    assert db.query(BookingLink).count() == before          # no link minted
