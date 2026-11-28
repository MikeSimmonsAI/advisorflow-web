"""Owner, Nov 28: campaigns take a day off and come back on their own."""
from datetime import datetime, timezone

from app.services import outbound_brake as B


def test_pause_holds_until_the_time_then_ends_by_itself(monkeypatch):
    monkeypatch.setenv(B.PAUSE_ENV, "2026-11-30T08:00:00-06:00")
    assert B.campaigns_paused(datetime(2026, 11, 30, 13, 59, tzinfo=timezone.utc))
    assert not B.campaigns_paused(datetime(2026, 11, 30, 14, 0, tzinfo=timezone.utc))
    monkeypatch.setenv(B.PAUSE_ENV, "2026-11-30T08:00:00")          # no offset = Central
    assert B.campaign_pause_until() == datetime(2026, 11, 30, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setenv(B.PAUSE_ENV, "nonsense")
    assert not B.campaigns_paused()
    monkeypatch.delenv(B.PAUSE_ENV)
    assert not B.campaigns_paused()


def test_paused_campaigns_stop_texts_emails_and_auto_replies(monkeypatch):
    from app.services import cadence_service
    from app.services.programs import ai_setter, email_touches
    monkeypatch.setenv(B.PAUSE_ENV, "2999-01-01T00:00:00-06:00")
    monkeypatch.setenv(email_touches.SENDING_ENV, "1")
    monkeypatch.delenv("AI_SCI_SETTER_ENABLED", raising=False)
    assert email_touches.sending_enabled() is False
    assert cadence_service.org_sending_enabled(None, "any-org") is False

    class P:
        ai_setter_mode = "auto"
    assert ai_setter.mode_of(P()) == ai_setter.DRAFT
    monkeypatch.setenv(B.PAUSE_ENV, "2000-01-01T00:00:00-06:00")    # over
    assert email_touches.sending_enabled() is True
    assert ai_setter.mode_of(P()) == ai_setter.AUTO
