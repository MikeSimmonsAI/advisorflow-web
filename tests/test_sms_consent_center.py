"""EvoSys Pro Universal SMS Consent Center.

One opt-in page, three programs (general / wholesale / sci), one consent ledger
tracked separately by program and sender; per-campaign link/phone approval; the
SCI send gate. Nothing here reaches a real provider: Twilio is mocked wherever a
send path is exercised, and every refusal is asserted to happen BEFORE the
provider call.
"""
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import Organization
from app.models.sms_consent_models import SmsConsentRecord
from app.services import sms_campaigns, sms_programs
from tests.test_site_intake import OPTIN, _configured, _post

ROOT = Path(__file__).resolve().parents[1]
PHP_PROGRAMS = ROOT / "public-site" / "private" / "sms-programs.php"

WHOLESALE_TEXT = sms_programs.DISCLOSURES[("wholesale", "evo-wholesale-sell-2026-09-25")]
SCI_VERSION = "sci-poc-2026-10-08"
SCI_TEXT = sms_programs.DISCLOSURES[("sci", SCI_VERSION)]


def _php_programs():
    """{key: {status, version, disclosure}} parsed from the PHP mirror."""
    src = PHP_PROGRAMS.read_text(encoding="utf-8")
    out = {}
    for key in ("general", "wholesale", "sci"):
        start = src.index("'%s' => [" % key)
        block = src[start:src.index("\n        ],", start)]
        # Anchored to the start of a line, so a comment that quotes a key is ignored.
        get = lambda f: re.search(r"^[ ]+'%s' => '((?:[^'\\]|\\.)*)'," % f, block, re.M).group(1)  # noqa: E731
        out[key] = {"status": get("status"), "version": get("version"),
                    "disclosure": get("disclosure").replace("\\'", "'"),
                    "sender": get("sender")}
    return out


def _org(db, name):
    import uuid
    o = Organization(name=name, slug="o-" + uuid.uuid4().hex[:8], plan="standard",
                     industry="funeral", is_active=True)
    db.add(o)
    db.commit()
    return o


def _sci_final(monkeypatch):
    """Make the SCI program's (already registered) wording final, for the test only."""
    p = sms_programs.PROGRAMS["sci"]
    monkeypatch.setitem(sms_programs.PROGRAMS, "sci",
                        sms_programs.Program(**{**p.__dict__, "copy_status": "final"}))


SCI_CAMPAIGN = {"key": "SCI-NEW", "campaign_id": "CMtest", "messaging_service_sid": "MG" + "1" * 32,
                "numbers": ["+12058823908"], "status": "VERIFIED", "has_embedded_links": True,
                "has_embedded_phone": True, "programs": ["sci"]}


# ── 1. the page and the platform say the same words ─────────────────────────

def test_php_and_platform_wording_are_identical_per_program():
    php = _php_programs()
    for key, p in sms_programs.PROGRAMS.items():
        assert php[key]["version"] == (p.current_version or "")
        assert php[key]["disclosure"] == sms_programs.DISCLOSURES[(key, p.current_version)]
        assert php[key]["status"] == p.copy_status
        assert php[key]["sender"] == p.sender


def test_general_wording_is_unchanged_from_the_approved_page():
    assert sms_programs.DISCLOSURES[("general", "2026-09")] == OPTIN["consent_text"]


def test_sci_keeps_its_own_wording_and_is_pending_not_generic():
    p = sms_programs.PROGRAMS["sci"]
    assert p.copy_status == "pending" and not sms_programs.is_open(p)
    assert "pre-planning information I requested" in SCI_TEXT
    assert "funeral home and cemetery locations" in SCI_TEXT
    assert SCI_TEXT != sms_programs.DISCLOSURES[("general", "2026-09")]


# ── 2. opt-ins are filed per program, per sender, per organization ──────────

def test_general_optin_files_a_program_record_with_server_time(client, db_session):
    _platform, org = _configured(db_session)
    with patch("twilio.rest.Client") as tw:
        r = _post(client, "sms-optin", OPTIN)
        tw.assert_not_called()                      # no confirmation text, ever
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["program"] == "general" and body["wording_registered"] is True
    rec = db_session.query(SmsConsentRecord).one()
    assert rec.organization_id == org.id and rec.program == "evosys_general_sms"
    assert rec.disclosure_text == OPTIN["consent_text"] and rec.disclosure_version == "2026-09"
    assert rec.form_id == "evosys_sms_optin"
    assert rec.campaign_sid == "CO3YNIF"            # its own campaign, from the registry
    assert rec.source_url == "https://evosyspro.live/sms-optin/"
    assert rec.ip_address == "203.0.113.44"
    assert rec.consented_at is not None and rec.confirmation_status is None
    view = sms_programs.record_view(rec)
    assert view["sender"].startswith("EvoSys Pro") and view["stop_status"] == "active"


def test_wholesale_optin_lands_in_the_wholesale_org_only(client, db_session, monkeypatch):
    _platform, general_org = _configured(db_session)
    wh = _org(db_session, "EVO Wholesale Test Org")
    monkeypatch.setenv("SMS_PROGRAM_ORG_WHOLESALE", wh.id)
    r = _post(client, "sms-optin", {**OPTIN, "program": "wholesale", "consent_text": WHOLESALE_TEXT,
                                    "consent_version": "evo-wholesale-sell-2026-09-25",
                                    "form_version": "optin-wholesale-v1",
                                    "source_url": "https://evosyspro.live/sms-optin/?program=wholesale"})
    assert r.status_code == 201, r.text
    rec = db_session.query(SmsConsentRecord).one()
    assert rec.organization_id == wh.id and rec.program == "wholesale_seller_sms"
    assert rec.form_id == "evosys_sms_optin_wholesale" and rec.form_version == "optin-wholesale-v1"
    assert r.json()["lead_id"] is None              # a consent, not a sales lead
    # the wholesale send gate sees it exactly like a /sell consent
    from app.services import wholesale_sms
    assert wholesale_sms.latest_consent(db_session, wh.id, "+12145550142").id == rec.id
    assert wholesale_sms.latest_consent(db_session, general_org.id, "+12145550142") is None


def test_unconfigured_program_org_refuses_and_writes_nothing(client, db_session, monkeypatch):
    _configured(db_session)
    monkeypatch.delenv("SMS_PROGRAM_ORG_WHOLESALE", raising=False)
    r = _post(client, "sms-optin", {**OPTIN, "program": "wholesale", "consent_text": WHOLESALE_TEXT,
                                    "consent_version": "evo-wholesale-sell-2026-09-25"})
    assert r.status_code == 503
    assert db_session.query(SmsConsentRecord).count() == 0


def test_sci_pending_takes_no_optin(client, db_session, monkeypatch):
    _configured(db_session)
    sci = _org(db_session, sms_programs.SCI_ORG_NAME)
    monkeypatch.setenv("SMS_PROGRAM_ORG_SCI", sci.id)
    r = _post(client, "sms-optin", {**OPTIN, "program": "sci", "consent_text": SCI_TEXT,
                                    "consent_version": SCI_VERSION})
    assert r.status_code == 409
    assert db_session.query(SmsConsentRecord).count() == 0


def test_sci_once_final_files_into_the_sci_org(client, db_session, monkeypatch):
    _configured(db_session)
    sci = _org(db_session, sms_programs.SCI_ORG_NAME)
    monkeypatch.setenv("SMS_PROGRAM_ORG_SCI", sci.id)
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([SCI_CAMPAIGN]))
    _sci_final(monkeypatch)
    r = _post(client, "sms-optin", {**OPTIN, "program": "sci", "consent_text": SCI_TEXT,
                                    "consent_version": SCI_VERSION})
    assert r.status_code == 201, r.text
    rec = db_session.query(SmsConsentRecord).one()
    assert rec.organization_id == sci.id and rec.program == "sci_poc_sms"
    assert rec.campaign_sid == "CMtest"


def test_sci_env_pointing_at_another_customer_is_refused(client, db_session, monkeypatch):
    _configured(db_session)
    other = _org(db_session, "Some Other Customer")
    monkeypatch.setenv("SMS_PROGRAM_ORG_SCI", other.id)
    _sci_final(monkeypatch)
    r = _post(client, "sms-optin", {**OPTIN, "program": "sci", "consent_text": SCI_TEXT,
                                    "consent_version": SCI_VERSION})
    assert r.status_code == 503
    assert db_session.query(SmsConsentRecord).count() == 0


def test_unknown_program_is_refused(client, db_session):
    _configured(db_session)
    assert _post(client, "sms-optin", {**OPTIN, "program": "atlantis"}).status_code == 422
    assert db_session.query(SmsConsentRecord).count() == 0


def test_mismatched_wording_is_filed_but_marked_unregistered(client, db_session):
    _configured(db_session)
    r = _post(client, "sms-optin", {**OPTIN, "consent_text": "Something else entirely."})
    assert r.status_code == 201 and r.json()["wording_registered"] is False
    rec = db_session.query(SmsConsentRecord).one()
    assert rec.disclosure_text == "Something else entirely."      # evidence kept verbatim


def test_stop_reply_withdraws_the_program_consent(db_session, sample_org, twilio_webhook):
    from tests.conftest import TEST_ORG_TWILIO_NUMBER
    rec = sms_programs.record(db_session, sms_programs.PROGRAMS["general"], sample_org.id,
                              phone_raw="214-555-0177", disclosure_text=OPTIN["consent_text"],
                              disclosure_version="2026-09", form_version="optin-general-v1",
                              source_url=OPTIN["source_url"], ip=None, user_agent=None)
    db_session.commit()
    r = twilio_webhook("/sms/webhook/inbound", data={"From": "+12145550177", "To": TEST_ORG_TWILIO_NUMBER,
                                                     "Body": "STOP", "MessageSid": "SMstop1"})
    assert r.status_code == 200
    db_session.refresh(rec)
    assert rec.status == "opted_out" and rec.opt_out_keyword == "STOP"
    assert sms_programs.record_view(rec)["stop_status"] == "opted_out"


# ── 3. what each campaign may carry ─────────────────────────────────────────

LINK = "Here's the planning guide you requested: https://evosyspro.live/planning-guide"
PHONE = "Questions? Call 205-882-3908."


def test_co3ynif_may_carry_neither_links_nor_phone_numbers():
    e = sms_campaigns.for_sender(from_number="+14692241155")
    assert e["key"] == "CO3YNIF"
    assert sms_campaigns.content_refusal(LINK, e) == [sms_campaigns.LINKS_NOT_APPROVED]
    assert sms_campaigns.content_refusal(PHONE, e) == [sms_campaigns.PHONE_NOT_APPROVED]
    assert sms_campaigns.content_refusal("Hi Jane. Reply STOP to opt out.", e) == []


def test_unregistered_sender_is_the_most_restrictive():
    assert sms_campaigns.for_sender(from_number="+13125550100") is None
    assert len(sms_campaigns.content_refusal(LINK + " " + PHONE, None)) == 2


def test_new_campaign_unlocks_links_and_phone_only_once_verified(monkeypatch):
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([dict(SCI_CAMPAIGN, status="PENDING")]))
    e = sms_campaigns.for_sender(from_number="+12058823908")
    assert sms_campaigns.content_refusal(LINK + " " + PHONE, e) == [
        sms_campaigns.LINKS_NOT_APPROVED, sms_campaigns.PHONE_NOT_APPROVED]
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([SCI_CAMPAIGN]))
    e = sms_campaigns.for_sender(messaging_service_sid=SCI_CAMPAIGN["messaging_service_sid"])
    assert sms_campaigns.content_refusal(LINK + " " + PHONE, e) == []
    # ...and CO3YNIF is still locked while the new one is open
    assert sms_campaigns.content_refusal(LINK, sms_campaigns.for_sender(from_number="+14692241155"))


def test_env_cannot_unlock_co3ynif_by_accident_with_bad_json(monkeypatch):
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, "{not json")
    e = sms_campaigns.for_sender(from_number="+14692241155")
    assert sms_campaigns.features(e) == {"links": False, "phone": False}


def test_assert_content_allowed_refuses_rather_than_sends():
    with pytest.raises(sms_campaigns.CampaignContentBlocked) as exc:
        sms_campaigns.assert_content_allowed(LINK, from_number="+14692241155")
    assert exc.value.reasons == [sms_campaigns.LINKS_NOT_APPROVED]
    assert isinstance(exc.value, ValueError)        # existing callers treat it as "blocked"


# ── 4. the SCI send gate, on the real send path ─────────────────────────────

def _sci_send(db, advisor, lead, from_number="+12058823908"):
    from app.services import sms_service
    client = MagicMock()
    client.messages.create.return_value = MagicMock(sid="SMx", status="queued", error_code=None,
                                                    error_message=None)
    with patch.object(sms_service, "_resolve_twilio_creds", return_value=(client, from_number, None)):
        try:
            sms_service.send_sms(db, advisor, lead, "Hi {first_name}, following up. Reply STOP to opt out.")
            return client, None
        except ValueError as exc:
            return client, exc


def test_sci_text_is_refused_before_the_provider_call(db_session, sample_org, sample_advisor,
                                                      sample_lead, monkeypatch):
    sample_org.name = sms_programs.SCI_ORG_NAME
    db_session.commit()
    monkeypatch.delenv(sms_programs.SCI_SEND_ENV, raising=False)
    client, exc = _sci_send(db_session, sample_advisor, sample_lead)
    client.messages.create.assert_not_called()
    assert exc is not None and set(exc.reasons) >= {
        "SCI_SMS_DISABLED", "SCI_COPY_NOT_FINAL", "NO_SMS_CONSENT", "CAMPAIGN_NOT_APPROVED"}


def test_sci_text_needs_every_condition(db_session, sample_org, sample_advisor, sample_lead, monkeypatch):
    sample_org.name = sms_programs.SCI_ORG_NAME
    db_session.commit()
    monkeypatch.setenv(sms_programs.SCI_SEND_ENV, "on")
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([SCI_CAMPAIGN]))
    _sci_final(monkeypatch)
    # consent under the SCI wording, for this lead's number, in the SCI org
    sms_programs.record(db_session, sms_programs.PROGRAMS["sci"], sample_org.id,
                        phone_raw=sample_lead.phone, disclosure_text=SCI_TEXT,
                        disclosure_version=SCI_VERSION, form_version="optin-sci-v1",
                        source_url="https://evosyspro.live/sms-optin/?program=sci",
                        ip=None, user_agent=None)
    db_session.commit()
    # from CO3YNIF's number: refused - that campaign does not list SCI
    client, exc = _sci_send(db_session, sample_advisor, sample_lead, from_number="+14692241155")
    client.messages.create.assert_not_called()
    assert exc.reasons == ["CAMPAIGN_NOT_APPROVED"]
    # from the new campaign's number with everything in place: sent (mocked)
    client, exc = _sci_send(db_session, sample_advisor, sample_lead)
    assert exc is None and client.messages.create.call_count == 1


def test_sci_consent_under_unregistered_wording_does_not_count(db_session, sample_org, sample_lead,
                                                                monkeypatch):
    sample_org.name = sms_programs.SCI_ORG_NAME
    db_session.commit()
    monkeypatch.setenv(sms_programs.SCI_SEND_ENV, "on")
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([SCI_CAMPAIGN]))
    _sci_final(monkeypatch)
    sms_programs.record(db_session, sms_programs.PROGRAMS["sci"], sample_org.id,
                        phone_raw=sample_lead.phone, disclosure_text="generic wording",
                        disclosure_version=SCI_VERSION, form_version=None, source_url=None,
                        ip=None, user_agent=None)
    db_session.commit()
    out = sms_programs.sci_check(db_session, sample_org.id, sample_lead.phone,
                                 from_number="+12058823908")
    assert out["reasons"] == ["CONSENT_WORDING_UNREGISTERED"]


def test_non_sci_orgs_are_untouched_by_the_sci_gate(db_session, sample_org, sample_lead):
    assert sms_programs.sci_send_refusal(db_session, sample_lead) is None


# ── 5. operator view ────────────────────────────────────────────────────────

def test_god_overview_and_ledger(client, db_session, monkeypatch):
    from tests.test_outreach_program import _god, _h
    _configured(db_session)
    _post(client, "sms-optin", OPTIN)
    h = _h(db_session, _god(db_session))
    ov = client.get("/god/sms-consent/overview", headers=h).json()
    assert {p["program"]: p["open"] for p in ov["programs"]} == {
        "general": True, "wholesale": True, "sci": False}
    co = next(c for c in ov["campaigns"] if c["key"] == "CO3YNIF")
    assert co["links_allowed_now"] is False and co["phone_allowed_now"] is False
    recs = client.get("/god/sms-consent/records?program=general", headers=h).json()
    assert recs["count"] == 1
    r0 = recs["records"][0]
    assert r0["program"] == "evosys_general_sms" and r0["sender"].startswith("EvoSys Pro")
    assert r0["disclosure_version"] == "2026-09" and r0["stop_status"] == "active"
    assert client.get("/god/sms-consent/records?program=sci", headers=h).json()["count"] == 0
    assert client.get("/god/sms-consent/overview").status_code in (401, 403)
