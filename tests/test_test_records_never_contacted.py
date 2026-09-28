"""
Internal test records (Lead.is_test = True) never reach real outreach.

app/services/test_records.py states the rule; these tests assert that every
core send / enrolment / bulk-selection path actually enforces it, and that test
records do not consume plan seats. No provider is ever reached: the Twilio
credential resolver is patched, and each test asserts it was not called.
"""

import itertools
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.billing_models import BrandBillingPlan
from app.models.models import (CadenceState, CadenceTouchLog, Lead,
                               Organization, Platform, User)
from app.services import cadence_service as cs
from app.services import compliance_service, plan_limits, sms_service, test_records
from app.services.auth_service import hash_password

_SEQ = itertools.count(71000)

# 15:00 UTC is inside the default contact window (see test_cadence_engine).
_now = datetime.utcnow()
AT = _now.replace(hour=15, minute=0, second=0, microsecond=0)
if AT < _now:
    AT += timedelta(days=1)


def _lead(db, org, advisor=None, *, is_test=False, phone=None, **kw):
    n = next(_SEQ)
    phone = phone or "1214555%04d" % (n % 10000)
    kw.setdefault("status", "new")
    lead = Lead(organization_id=org.id,
                assigned_to_id=getattr(advisor, "id", None),
                first_name="T", last_name="Rec%d" % n,
                phone=phone, phone_raw=phone, is_test=is_test,
                test_note="QA fixture" if is_test else None, **kw)
    db.add(lead)
    db.commit()
    return lead


def _twilio():
    client = MagicMock()
    client.messages.create.return_value = MagicMock(
        sid="SM_should_not_exist", status="queued", error_code=None,
        error_message=None)
    return client


# ── SMS / MMS ───────────────────────────────────────────────────────────────

def test_send_sms_refuses_a_test_lead_without_calling_the_provider(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, is_test=True)
    client = _twilio()
    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(client, "+19998887777", None)) as creds:
        with pytest.raises(ValueError) as exc:
            sms_service.send_sms(db_session, sample_advisor, lead, "Hello")
    assert "test record" in str(exc.value)
    creds.assert_not_called()
    client.messages.create.assert_not_called()


def test_send_mms_refuses_a_test_lead(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, is_test=True)
    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        with pytest.raises(ValueError):
            sms_service.send_mms(db_session, sample_advisor, lead, "Hi",
                                 "https://example.com/flyer.png")
    creds.assert_not_called()


def test_send_batch_skips_test_leads(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, is_test=True)
    with patch("app.services.sms_service._resolve_twilio_creds") as creds, \
            patch("app.services.sms_service.send_sms") as single:
        out = sms_service.send_batch(db_session, sample_advisor, [lead], "Hi")
    assert out["sent_count"] == 0 and out["skipped_ids"] == [lead.id]
    single.assert_not_called()
    creds.assert_not_called()


# ── Compliance preflight ────────────────────────────────────────────────────

@pytest.mark.parametrize("channel", ["sms", "email"])
def test_compliance_preflight_refuses_a_test_lead(
        db_session, sample_org, sample_advisor, channel):
    lead = _lead(db_session, sample_org, sample_advisor, is_test=True,
                 email="qa@example.com", allow_email=True)
    with pytest.raises(ValueError) as exc:
        compliance_service.check_compliance_preflight(db_session, lead, channel)
    assert "test record" in str(exc.value)


def test_compliance_preflight_still_passes_a_real_lead(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    assert compliance_service.check_compliance_preflight(db_session, lead) is None


# ── Cadence ─────────────────────────────────────────────────────────────────

def test_start_cadence_returns_none_for_a_test_lead(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, is_test=True)
    assert cs.start_cadence(db_session, lead) is None
    assert db_session.query(CadenceState).filter(
        CadenceState.lead_id == lead.id).count() == 0


def test_an_enrolled_test_lead_is_stopped_with_a_recorded_reason(
        db_session, sample_org, sample_advisor, monkeypatch):
    monkeypatch.setenv("CADENCE_SMS_SENDING", "true")
    lead = _lead(db_session, sample_org, sample_advisor, is_test=True)
    now = datetime.utcnow()
    state = CadenceState(lead_id=lead.id, status="active", current_touch_number=0,
                         cadence_started_at=now - timedelta(days=1),
                         next_touch_due_at=now - timedelta(minutes=5))
    db_session.add(state)
    db_session.commit()

    client = _twilio()
    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(client, "+19998887777", None)) as creds:
        result = cs.run_due_cadences(db_session, now=AT)

    creds.assert_not_called()
    client.messages.create.assert_not_called()
    assert result["sent"] == 0
    db_session.refresh(state)
    assert state.status == "stopped_test_record"
    log = (db_session.query(CadenceTouchLog)
           .filter(CadenceTouchLog.lead_id == lead.id).one())
    assert log.outcome == cs.OUTCOME_STOPPED
    assert log.reason == "test record"


def test_start_all_eligible_skips_test_leads(db_session, sample_org, sample_advisor):
    from app.routers import cadence_router
    admin = User(organization_id=sample_org.id, email="tr-admin@restland.com",
                 password_hash=hash_password("AdminPass123!"),
                 full_name="Org Admin", role="org_admin", must_change_password=False)
    db_session.add(admin)
    db_session.commit()
    real = _lead(db_session, sample_org, sample_advisor)
    test = _lead(db_session, sample_org, sample_advisor, is_test=True)

    out = cadence_router.start_all_eligible(db=db_session, current_user=admin)

    assert out["started"] == 1 and out["skipped"] == 0
    enrolled = {s.lead_id for s in db_session.query(CadenceState).all()}
    assert real.id in enrolled and test.id not in enrolled


def test_start_batch_skips_and_reports_test_leads(
        db_session, sample_org, sample_advisor):
    from app.routers import cadence_router
    real = _lead(db_session, sample_org, sample_advisor)
    test = _lead(db_session, sample_org, sample_advisor, is_test=True)
    out = cadence_router.start_batch_cadence(
        [real.id, test.id], db=db_session, current_user=sample_advisor)
    assert out["started"] == 1
    assert out["skipped_test_records"] == [test.id]


# ── Campaigns ───────────────────────────────────────────────────────────────

def test_campaign_filters_exclude_test_records(db_session, sample_org, sample_advisor):
    from app.routers.campaign_router import _apply_filters, _compliance_check
    real = _lead(db_session, sample_org, sample_advisor)
    test = _lead(db_session, sample_org, sample_advisor, is_test=True)

    for include_dnc in (False, True):
        ids = {l.id for l in _apply_filters(db_session.query(Lead), sample_org.id,
                                            {}, include_dnc=include_dnc).all()}
        assert real.id in ids and test.id not in ids

    ok, reason = _compliance_check(test, "sms")
    assert ok is False and "test record" in reason
    assert _compliance_check(real, "sms") == (True, "")


# ── Auto-send / pipeline / voice / AI conversation ──────────────────────────

def test_ai_conversation_start_refuses_a_test_lead(
        db_session, sample_org, sample_advisor):
    from app.services import ai_conversation_service
    lead = _lead(db_session, sample_org, sample_advisor, is_test=True,
                 email="qa@example.com")
    result = ai_conversation_service.start_ai_conversation(
        db_session, lead, sample_advisor)
    assert result["success"] is False
    assert "test record" in result["error"]


def test_voice_eligibility_refuses_a_test_lead(db_session, sample_org, sample_advisor):
    from app.services import voice_orchestrator
    lead = _lead(db_session, sample_org, sample_advisor, is_test=True)
    elig = voice_orchestrator.check_call_eligibility(db_session, lead, sample_org.id)
    assert elig.ok is False and elig.code == "test_record"


def test_pipeline_launch_skips_a_test_lead(db_session, sample_org, sample_advisor):
    from app.services import pipeline_service
    lead = _lead(db_session, sample_org, sample_advisor, is_test=True)
    with patch("app.services.sms_service._resolve_twilio_creds") as creds, \
            patch("app.services.sms_service.send_sms") as send:
        out = pipeline_service.launch_pipeline(
            db_session, [lead], sample_advisor, lead_type="general",
            tone="warm", ai_direction="")
    send.assert_not_called()
    assert out.get("launched", 0) == 0
    creds.assert_not_called()


# ── Plan usage ──────────────────────────────────────────────────────────────

def test_plan_usage_excludes_test_leads(db_session):
    p = Platform(name="TR", slug="tr-%d" % next(_SEQ))
    db_session.add(p); db_session.commit()
    db_session.add(BrandBillingPlan(
        platform_id=p.id, key="tiny", name="Tiny", monthly_cents=100,
        currency="usd", is_purchasable=True, is_active=True, sort_order=1,
        max_leads=2, max_users=50))
    org = Organization(name="TR Org", slug="tr-org-%d" % next(_SEQ),
                       platform_id=p.id, is_active=True, plan="tiny",
                       billing_plan_key="tiny", billing_status="active")
    db_session.add(org); db_session.commit()

    _lead(db_session, org)
    _lead(db_session, org, is_test=True)
    _lead(db_session, org, is_test=True)
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_LEADS) == 1

    # A test lead arriving at a full plan is never held (it costs no seat).
    from app.services import lead_capacity
    _lead(db_session, org)
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_LEADS) == 2
    extra = Lead(organization_id=org.id, first_name="QA", last_name="Late",
                 status="new", is_test=True)
    assert lead_capacity.hold_if_over_capacity(db_session, extra, org) is False
    assert test_records.is_outreach_eligible(extra) is False


def test_a_deliberate_manual_send_to_a_test_record_is_still_possible(db_session):
    """test_records: 'testers still need to test' - only a one-to-one MANUAL
    send passes; every automated/bulk source is refused."""
    from types import SimpleNamespace
    from app.services import compliance_service, send_source as ss
    lead = SimpleNamespace(id="t1", is_test=True, test_note="x", status="new", phone="12145550100",
                           email="t@x.test", allow_email=None, manual_flag=None, organization_id=None,
                           sms_consent=None)
    import pytest as _p
    with _p.raises(ValueError):
        compliance_service.check_compliance_preflight(db_session, lead, channel="email")
    try:
        compliance_service.check_compliance_preflight(db_session, lead, channel="email", allow_test=True)
    except ValueError as exc:
        assert "test" not in str(exc).lower(), exc
    assert ss.MANUAL not in (ss.BULK, ss.BULK_AI, ss.CAMPAIGN, ss.CADENCE)
