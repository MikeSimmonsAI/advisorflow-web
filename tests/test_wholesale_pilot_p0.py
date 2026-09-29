"""Wholesale pilot P0 (Building Equity Investments LLC): safety gates, pilot
size, human workflow (HOT/WARM/COLD override, callbacks, notes, Pause AI /
Take over), cross-tenant isolation and no-send guarantees.

Nothing here reaches a provider: conftest refuses to construct a Twilio client,
and the voice campaign runner is exercised with a local fake client."""
from datetime import datetime, timedelta

import pytest

from app.models.models import Lead, LeadStatus, LeadTier, MessageTrack, Message, Organization, User
from app.services.auth_service import create_access_token, hash_password


def _ensure_router():
    from app.main import app
    from app.routers.wholesale_ops_router import router as ops_router
    if not any(getattr(r, "path", "").startswith("/wholesale/ops/") for r in app.routes):
        app.include_router(ops_router)


_ensure_router()


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:500])
    return r.json()


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def _user(db, org, role="org_admin", email=None):
    u = User(organization_id=org.id, email=email or "%s@%s.test" % (role, org.slug),
             password_hash=hash_password("Pass12345!"), full_name=role.title(),
             role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _lead(db, org, owner, phone, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=owner.id, first_name=kw.pop("first_name", "Sam"),
                last_name=kw.pop("last_name", "Seller"), phone=phone,
                tier=LeadTier.PRE_NEED, message_track=MessageTrack.PRE_NEED_LOCK_PRICE,
                status=kw.pop("status", LeadStatus.NEW), **kw)
    db.add(lead)
    db.commit()
    return lead


@pytest.fixture
def admin(db_session, sample_org):
    return _user(db_session, sample_org, "org_admin", "boss@pilot.test")


@pytest.fixture
def other_org(db_session):
    org = Organization(name="Other Wholesaler", slug="other-wholesaler", plan="standard",
                       industry="real_estate")
    db_session.add(org)
    db_session.commit()
    return org


@pytest.fixture
def intruder(db_session, other_org):
    return _user(db_session, other_org, "org_admin", "intruder@other.test")


@pytest.fixture
def deal(client, db_session, admin):
    h = _h(db_session, admin)
    prop = ok(client.post("/wholesale/properties", headers=h,
                          json={"street_address": "10 Pilot Way", "city": "Dallas", "state": "TX",
                                "zip_code": "75201"}))
    seller = ok(client.post("/wholesale/properties/%s/seller" % prop["id"], headers=h,
                            json={"first_name": "Pat", "last_name": "Owner", "phone": "2145550142"}))
    return {"deal_id": prop["deal"]["id"], "property_id": prop["id"], "lead_id": seller["lead_id"]}


# ── 1. Safety: bulk voice goes through the full gate ────────────────────────

class _FakeThread:
    started = []

    def __init__(self, target=None, args=(), daemon=None):
        self.args = args

    def start(self):
        _FakeThread.started.append(self.args)


def test_bulk_voice_campaign_refuses_dnc_suppressed_and_no_call_leads(
        client, db_session, sample_org, sample_advisor, auth_headers, monkeypatch):
    from app.routers import voice_router
    from app.services.compliance_service import add_suppression_entry
    monkeypatch.setattr(voice_router.threading, "Thread", _FakeThread)
    _FakeThread.started = []
    good = _lead(db_session, sample_org, sample_advisor, "12145550101")
    dnc = _lead(db_session, sample_org, sample_advisor, "12145550102", status=LeadStatus.DNC)
    suppressed = _lead(db_session, sample_org, sample_advisor, "12145550103")
    add_suppression_entry(db_session, sample_org.id, "12145550103", "test")
    db_session.commit()
    no_calls = _lead(db_session, sample_org, sample_advisor, "12145550104", allow_voice=False)
    dnc_twin = _lead(db_session, sample_org, sample_advisor, "2145550102")   # same number as a DNC record
    r = ok(client.post("/voice/campaigns", headers=auth_headers,
                       json={"name": "pilot", "lead_ids": [good.id, dnc.id, suppressed.id,
                                                           no_calls.id, dnc_twin.id]}))
    assert r["total_leads"] == 1 and r["skipped"] == 4
    from app.models.models import VoiceCallCampaign
    import json
    camp = db_session.query(VoiceCallCampaign).filter(VoiceCallCampaign.id == r["campaign_id"]).first()
    assert json.loads(camp.lead_ids) == [good.id]


def test_bulk_voice_refused_when_org_voice_is_paused(client, db_session, sample_org, sample_advisor,
                                                       auth_headers, monkeypatch):
    from app.routers import voice_router
    from app.services.evosense import common as C
    monkeypatch.setattr(voice_router.threading, "Thread", _FakeThread)
    _FakeThread.started = []
    good = _lead(db_session, sample_org, sample_advisor, "12145550111")
    C.controls(db_session, sample_org.id).paused_voice = True
    db_session.commit()
    r = client.post("/voice/campaigns", headers=auth_headers, json={"name": "x", "lead_ids": [good.id]})
    assert r.status_code == 409 and "paused" in r.text.lower()
    assert _FakeThread.started == []


def test_campaign_runner_rechecks_each_lead_before_dialling(db_session, sample_org, sample_advisor,
                                                            monkeypatch):
    """A lead that says STOP after the campaign was created is not dialled."""
    import json
    import time
    from app.models.models import VoiceCallCampaign
    from app.routers import voice_router
    ok_lead = _lead(db_session, sample_org, sample_advisor, "12145550121")
    late_stop = _lead(db_session, sample_org, sample_advisor, "12145550122")
    camp = VoiceCallCampaign(organization_id=sample_org.id, advisor_id=sample_advisor.id, name="c",
                             lead_ids=json.dumps([ok_lead.id, late_stop.id]), total_leads=2,
                             concurrent_calls=5, call_window_start="00:00", call_window_end="24:00",
                             status="running", created_at=datetime.utcnow())
    db_session.add(camp)
    late_stop.status = LeadStatus.DNC
    db_session.commit()

    dialled = []

    class _Call:
        sid = "CAfake"
        status = "completed"

    class _Calls:
        def create(self, **kw):
            dialled.append(kw["to"])
            return _Call()

    class _Client:
        def __init__(self, *a, **k):
            self.calls = _CallsAccessor()

    class _CallsAccessor(_Calls):
        def __call__(self, sid):
            class _F:
                def fetch(self_inner):
                    return _Call()
            return _F()

    import twilio.rest
    monkeypatch.setattr(twilio.rest, "Client", _Client)
    monkeypatch.setattr("app.utils.crypto.decrypt_value", lambda v: "tok")
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr("app.deps.SessionLocal", lambda: db_session)
    voice_router._run_campaign_background(camp.id, sample_advisor.id, sample_org.id)
    assert dialled == ["+12145550121"]


def test_single_ai_call_refused_while_a_person_has_taken_over(client, db_session, sample_org,
                                                              sample_lead, auth_headers):
    from app.services import wholesale_ops as OPS
    OPS.apply_control(db_session, sample_org.id, sample_lead, None, "takeover")
    db_session.commit()
    r = client.post("/voice/call/%s" % sample_lead.id, headers=auth_headers)
    assert r.status_code == 409 and "taken over" in r.text


# ── 1b. Re-import keeps DNC state ───────────────────────────────────────────

def test_reimported_owner_reuses_the_dnc_lead_instead_of_a_fresh_one(db_session, sample_org, admin):
    from app.services import wholesale_service as svc
    from app.models.wholesale_models import WholesaleProperty
    stopped = _lead(db_session, sample_org, admin, "12145550131", status=LeadStatus.DNC)
    prop = WholesaleProperty(organization_id=sample_org.id, street_address="1 Re St", city="Dallas",
                             state="TX", zip_code="75201")
    db_session.add(prop)
    db_session.commit()
    before = db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count()
    profile = svc.attach_seller(db_session, sample_org.id, admin, prop,
                                {"first_name": "Again", "phone": "(214) 555-0131"})
    db_session.commit()
    assert profile.lead_id == stopped.id
    assert db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count() == before
    assert getattr(db_session.get(Lead, stopped.id).status, "value", None) == "dnc" or \
        db_session.get(Lead, stopped.id).status == "dnc"


def test_reimport_matches_on_email_and_on_name_at_address(db_session, sample_org, admin):
    from app.services import wholesale_service as svc
    from app.models.wholesale_models import WholesaleProperty
    by_mail = _lead(db_session, sample_org, admin, None, email="Owner@Example.com", status=LeadStatus.DNC)
    at_addr = _lead(db_session, sample_org, admin, None, last_name="Quill", street_address="9 Elm St",
                    zip_code="75202", status=LeadStatus.DNC)
    p1 = WholesaleProperty(organization_id=sample_org.id, street_address="2 A St", zip_code="75201")
    p2 = WholesaleProperty(organization_id=sample_org.id, street_address="9 Elm St", zip_code="75202-1234")
    db_session.add_all([p1, p2])
    db_session.commit()
    assert svc.attach_seller(db_session, sample_org.id, admin, p1,
                             {"email": "owner@example.com"}).lead_id == by_mail.id
    assert svc.attach_seller(db_session, sample_org.id, admin, p2,
                             {"last_name": "QUILL", "first_name": "X"}).lead_id == at_addr.id


def test_reimport_never_matches_another_organizations_lead(db_session, sample_org, other_org, admin,
                                                           intruder):
    from app.services import wholesale_service as svc
    from app.models.wholesale_models import WholesaleProperty
    theirs = _lead(db_session, other_org, intruder, "12145550141", status=LeadStatus.DNC)
    prop = WholesaleProperty(organization_id=sample_org.id, street_address="3 B St", zip_code="75201")
    db_session.add(prop)
    db_session.commit()
    profile = svc.attach_seller(db_session, sample_org.id, admin, prop, {"phone": "2145550141"})
    assert profile.lead_id != theirs.id


def test_router_owner_add_of_a_dnc_number_stays_dnc(client, db_session, sample_org, admin):
    stopped = _lead(db_session, sample_org, admin, "12145550151", status=LeadStatus.DNC)
    h = _h(db_session, admin)
    prop = ok(client.post("/wholesale/properties", headers=h,
                          json={"street_address": "4 C St", "city": "Dallas", "state": "TX"}))
    seller = ok(client.post("/wholesale/properties/%s/seller" % prop["id"], headers=h,
                            json={"first_name": "Back", "phone": "214-555-0151"}))
    lead = db_session.get(Lead, seller["lead_id"])
    db_session.refresh(lead)
    # Universal Intake matches the existing person (or the direct path does):
    # either way the owner is the DNC record, never a fresh contactable one.
    assert seller["lead_id"] == stopped.id
    assert getattr(lead.status, "value", lead.status) == "dnc"


# ── 1c. DNC on every channel ────────────────────────────────────────────────

def test_dnc_blocks_sms_email_and_voice(db_session, sample_org, sample_advisor):
    from app.services import communication_eligibility as CE
    from app.services import sms_service, voice_bulk_gate
    from app.services.compliance_service import check_compliance_preflight
    lead = _lead(db_session, sample_org, sample_advisor, "12145550161", email="d@example.com",
                 status=LeadStatus.DNC, allow_email=True, allow_voice=True)
    with pytest.raises(ValueError):
        sms_service.send_sms(db_session, sample_advisor, lead, "hi", include_booking_link=False)
    with pytest.raises(ValueError):
        check_compliance_preflight(db_session, lead, channel="email")
    assert voice_bulk_gate.call_refusal(db_session, lead, sample_org.id)
    assert CE.email(db_session, sample_org.id, "d@example.com", lead=lead)["state"] == "BLOCKED"
    assert CE.voice(db_session, sample_org.id, lead.phone, lead=lead)["state"] == "BLOCKED"
    assert db_session.query(Message).count() == 0


# ── 2. Pilot size and distribution defaults ─────────────────────────────────

def test_pilot_hard_cap_is_500_and_a_cap():
    from app.services.evosense import strategy as ST
    assert ST.PILOT_HARD_CAP == 500
    _, problems = ST.validate({"name": "p", "pilot_max_properties": 500})
    assert not problems
    _, problems = ST.validate({"name": "p", "pilot_max_properties": 501})
    assert problems

    class S:
        pilot_max_properties = 10_000
    assert ST.pilot_cap(S()) == 500


def test_pilot_controls_validate_range_and_require_admin(client, db_session, sample_org, admin,
                                                         sample_advisor, auth_headers):
    h = _h(db_session, admin)
    got = ok(client.get("/wholesale/ops/pilot", headers=h))
    assert got["configured"] is False and got["limits"]["hard_cap"] == 500
    assert got["distribution"]["buyers"]["auto_distribution"] is False
    assert got["distribution"]["funding"]["auto_distribution"] is False
    assert client.put("/wholesale/ops/pilot", headers=h, json={"max_records": 501}).status_code == 422
    assert client.put("/wholesale/ops/pilot", headers=h, json={"max_records": 0}).status_code == 422
    put = ok(client.put("/wholesale/ops/pilot", headers=h,
                        json={"max_records": 300, "skip_trace_budget_cents": 500, "source": "county list"}))
    assert put["max_records"] == 300 and put["skip_trace_budget_cents"] == 500
    assert client.put("/wholesale/ops/pilot", headers=auth_headers,
                      json={"max_records": 250}).status_code == 403


def test_pilot_links_a_strategy_as_a_manual_pilot_without_paid_data(client, db_session, sample_org, admin):
    from app.models.evosense_models import EvoSenseStrategy
    s = EvoSenseStrategy(organization_id=sample_org.id, name="DFW", status="active",
                         states='["TX"]', property_types='["single_family"]')
    db_session.add(s)
    db_session.commit()
    h = _h(db_session, admin)
    got = ok(client.put("/wholesale/ops/pilot", headers=h,
                        json={"strategy_id": s.id, "max_records": 400, "skip_trace_budget_cents": 300}))
    assert got["strategy"]["pilot_mode"] is True and got["strategy"]["pilot_record_cap"] == 400
    assert got["strategy"]["pilot_allow_paid"] is False and got["strategy"]["auto_outreach"] is False
    got = ok(client.put("/wholesale/ops/pilot", headers=h, json={"status": "stopped"}))
    db_session.refresh(s)
    assert s.status == "paused" and got["kill_switches"]["paused_discovery"] is True


def test_auto_match_on_contract_defaults_off_for_new_rows_only(db_session, sample_org, other_org):
    from app.models.wholesale_models import WholesaleSettings
    from app.services import wholesale_service as svc
    assert svc.resolve_settings(db_session, sample_org.id).auto_match_on_contract is False
    row = WholesaleSettings(organization_id=other_org.id, auto_match_on_contract=True)
    db_session.add(row)
    db_session.commit()
    assert svc.resolve_settings(db_session, other_org.id).auto_match_on_contract is True


# ── 3/4. Temperature ────────────────────────────────────────────────────────

def test_human_temperature_override_wins_is_audited_and_survives_ai_recompute(
        client, db_session, admin, deal):
    from app.services.engagement_service import recompute_and_save
    h = _h(db_session, admin)
    url = "/wholesale/ops/leads/%s/temperature" % deal["lead_id"]
    t = ok(client.get(url, headers=h))
    assert t["effective_source"] == "ai" and t["ai"]["reason"]
    assert client.put(url, headers=h, json={"temperature": "HOT"}).status_code == 422   # reason required
    assert client.put(url, headers=h, json={"temperature": "BOILING", "reason": "x"}).status_code == 422
    t = ok(client.put(url, headers=h, json={"temperature": "hot", "reason": "Said he must sell by Friday",
                                            "deal_id": deal["deal_id"]}))
    assert t["effective"] == "HOT" and t["effective_source"] == "human"
    assert t["history"][0]["ai_temperature"] and t["history"][0]["actor_name"]
    lead = db_session.get(Lead, deal["lead_id"])
    recompute_and_save(db_session, lead)             # the AI runs again
    t = ok(client.get(url, headers=h))
    assert t["effective"] == "HOT"                   # not silently overwritten
    t = ok(client.delete(url, headers=h))
    assert t["effective_source"] == "ai" and [x["action"] for x in t["history"]] == ["clear", "set"]
    assert client.delete(url, headers=h).status_code == 409


# ── Callback Center ─────────────────────────────────────────────────────────

def test_callback_center_buckets_and_missed_stays_overdue(client, db_session, sample_org, admin, deal):
    h = _h(db_session, admin)
    now = datetime.utcnow()
    mk = lambda dt, n: ok(client.post("/wholesale/ops/callbacks", headers=h, json={
        "deal_id": deal["deal_id"], "due_at": dt.isoformat(), "notes": n}))
    due = mk(now + timedelta(minutes=5), "due")
    up = mk(now + timedelta(days=2), "later")
    missed = mk(now - timedelta(days=3), "missed")
    done = mk(now + timedelta(minutes=1), "done")
    ok(client.post("/wholesale/ops/callbacks/%s/complete" % done["id"], headers=h, json={"note": "talked"}))
    c = ok(client.get("/wholesale/ops/callbacks", headers=h))
    ids = {b: [i["id"] for i in c["buckets"][b]] for b in c["buckets"]}
    assert ids["due_now"] == [due["id"]] and ids["upcoming"] == [up["id"]]
    assert ids["overdue"] == [missed["id"]] and ids["completed"] == [done["id"]]
    assert c["counts"]["completed"] == 1
    assert client.post("/wholesale/ops/callbacks/%s/complete" % done["id"], headers=h).status_code == 409
    assert client.post("/wholesale/ops/callbacks", headers=h,
                       json={"deal_id": deal["deal_id"], "due_at": "not a date"}).status_code == 422


def test_seller_callback_exceptions_are_bridged_not_deleted(client, db_session, sample_org, admin, deal):
    from app.models.wholesale_models import WholesaleWorkException
    ex = WholesaleWorkException(organization_id=sample_org.id, kind="seller_callback_requested",
                                subject_type="lead", subject_id=deal["lead_id"], title="Call back Pat",
                                detail="asked to be called", due_at=datetime.utcnow() - timedelta(hours=5))
    db_session.add(ex)
    db_session.commit()
    h = _h(db_session, admin)
    c = ok(client.get("/wholesale/ops/callbacks", headers=h))
    item = c["buckets"]["overdue"][0]
    assert item["id"] == "exc:%s" % ex.id and item["lead_id"] == deal["lead_id"]
    ok(client.post("/wholesale/ops/callbacks/exc:%s/complete" % ex.id, headers=h, json={"note": "done"}))
    c = ok(client.get("/wholesale/ops/callbacks", headers=h))
    assert not c["buckets"]["overdue"] and c["counts"]["completed"] == 1
    db_session.expire_all()
    still = db_session.get(WholesaleWorkException, ex.id)
    assert still is not None and still.status == "open"


# ── Notes ───────────────────────────────────────────────────────────────────

def test_deal_notes_crud_author_and_admin_rules(client, db_session, sample_org, admin, deal):
    va = _user(db_session, sample_org, "advisor", "va@pilot.test")
    h, hv = _h(db_session, admin), _h(db_session, va)
    n = ok(client.post("/wholesale/ops/deals/%s/notes" % deal["deal_id"], headers=hv,
                       json={"body": "Seller wants 30 days"}))
    assert n["author_name"] and n["created_at"] and n["property_id"] == deal["property_id"]
    assert client.post("/wholesale/ops/deals/%s/notes" % deal["deal_id"], headers=hv,
                       json={"body": "  "}).status_code == 422
    other = _user(db_session, sample_org, "advisor", "other@pilot.test")
    assert client.patch("/wholesale/ops/notes/%s" % n["id"], headers=_h(db_session, other),
                        json={"body": "hijack"}).status_code == 403
    e = ok(client.patch("/wholesale/ops/notes/%s" % n["id"], headers=hv, json={"body": "Wants 45 days"}))
    assert e["edited_at"] and e["body"] == "Wants 45 days"
    ok(client.delete("/wholesale/ops/notes/%s" % n["id"], headers=h))
    assert ok(client.get("/wholesale/ops/deals/%s/notes" % deal["deal_id"], headers=h))["notes"] == []


# ── Pause AI / Take over / Resume AI — enforcement ──────────────────────────

def test_pause_and_takeover_block_every_automated_send_but_not_manual(client, db_session, sample_org,
                                                                        admin, deal, monkeypatch):
    from app.services import sms_service, wholesale_ops as OPS, wholesale_sms
    h = _h(db_session, admin)
    base = "/wholesale/ops/leads/%s/control" % deal["lead_id"]
    assert ok(client.get(base, headers=h))["ai_may_send"] is True
    c = ok(client.post(base + "/pause", headers=h, json={"reason": "tricky seller"}))
    assert c["paused_ai"] is True and c["ai_may_send"] is False
    lead = db_session.get(Lead, deal["lead_id"])
    for src in (None, "cadence", "ai_conversation", "auto_send", "pipeline_auto_reply", "ai_employee"):
        with pytest.raises(wholesale_sms.WholesaleSmsBlocked) as exc:
            sms_service.send_sms(db_session, admin, lead, "auto hello", include_booking_link=False,
                                 send_source=src)
        assert exc.value.reasons == ["AI_PAUSED"]
    assert OPS.ai_send_refusal(db_session, lead, "manual") is None
    c = ok(client.post(base + "/takeover", headers=h))
    assert c["mode"] == "human" and c["taken_over_by_name"]
    assert OPS.ai_send_refusal(db_session, lead, "cadence") == "HUMAN_TAKEOVER"
    from app.services import voice_bulk_gate
    assert "taken over" in voice_bulk_gate.call_refusal(db_session, lead, sample_org.id)
    c = ok(client.post(base + "/resume", headers=h))
    assert c["ai_may_send"] is True and [x["action"] for x in c["history"]] == ["resume", "takeover", "pause"]
    assert OPS.ai_send_refusal(db_session, lead, "cadence") is None
    assert client.post(base + "/explode", headers=h).status_code in (404, 405)
    assert db_session.query(Message).count() == 0


# ── Skip trace, calls, deal ops payload ─────────────────────────────────────

def test_skip_trace_summary_reads_the_real_ledger_only(client, db_session, sample_org, other_org, admin):
    from app.models.evosense_models import EvoSenseCostEntry
    rows = [("charged", True, 10), ("charged", False, 0), ("failed_refunded", None, 0)]
    for st, success, cents in rows:
        db_session.add(EvoSenseCostEntry(organization_id=sample_org.id, provider_key="tracerfy",
                                         capability="CONTACT_ENRICHMENT", operation="trace",
                                         total_cents=cents, status=st, success=success,
                                         period_day="d", period_month="m"))
    db_session.add(EvoSenseCostEntry(organization_id=other_org.id, provider_key="tracerfy",
                                     capability="CONTACT_ENRICHMENT", operation="trace",
                                     total_cents=999, status="charged", success=True,
                                     period_day="d", period_month="m"))
    db_session.commit()
    s = ok(client.get("/wholesale/ops/skip-trace", headers=_h(db_session, admin)))
    t = s["ledger"]["providers"]["tracerfy"]
    assert (t["attempts"], t["charged_cents"], t["hits"], t["no_result"], t["errors"]) == (3, 10, 1, 1, 1)
    assert s["ledger"]["total_charged_cents"] == 10
    assert s["target_cents_per_record"] == 1 and isinstance(s["target_viable"], bool)


def test_deal_ops_payload_is_honest_about_voicemail_and_dialer(client, db_session, admin, deal):
    d = ok(client.get("/wholesale/ops/deals/%s" % deal["deal_id"], headers=_h(db_session, admin)))
    assert d["calls"]["voicemail"]["inbound_voicemail_capture"] == "not_available"
    # A cold owner who never contacted us has no call-permission basis: no link.
    assert d["calls"]["dialer"]["kind"] == "tel_link" and d["calls"]["dialer"]["tel"] is None
    assert d["distribution"]["buyers"]["auto_distribution"] is False
    assert d["temperature"]["effective"] and d["control"]["ai_may_send"] is True


# ── Isolation and gating ────────────────────────────────────────────────────

def test_another_organization_gets_404_on_every_ops_route(client, db_session, sample_org, admin,
                                                         intruder, deal):
    h = _h(db_session, admin)
    cb = ok(client.post("/wholesale/ops/callbacks", headers=h,
                        json={"deal_id": deal["deal_id"], "due_at": datetime.utcnow().isoformat()}))
    note = ok(client.post("/wholesale/ops/deals/%s/notes" % deal["deal_id"], headers=h, json={"body": "x"}))
    from app.models.wholesale_models import WholesaleWorkException
    ex = WholesaleWorkException(organization_id=sample_org.id, kind="seller_callback_requested",
                                subject_type="lead", subject_id=deal["lead_id"], title="t")
    db_session.add(ex)
    db_session.commit()
    hi = _h(db_session, intruder)
    L, D = deal["lead_id"], deal["deal_id"]
    attempts = [
        ("get", "/wholesale/ops/leads/%s/temperature" % L, None),
        ("put", "/wholesale/ops/leads/%s/temperature" % L, {"temperature": "COLD", "reason": "x"}),
        ("delete", "/wholesale/ops/leads/%s/temperature" % L, None),
        ("get", "/wholesale/ops/leads/%s/control" % L, None),
        ("post", "/wholesale/ops/leads/%s/control/pause" % L, {}),
        ("get", "/wholesale/ops/leads/%s/calls" % L, None),
        ("post", "/wholesale/ops/callbacks", {"lead_id": L, "due_at": "2026-10-01T10:00:00"}),
        ("post", "/wholesale/ops/callbacks", {"deal_id": D, "due_at": "2026-10-01T10:00:00"}),
        ("post", "/wholesale/ops/callbacks/%s/complete" % cb["id"], {}),
        ("post", "/wholesale/ops/callbacks/%s/cancel" % cb["id"], {}),
        ("post", "/wholesale/ops/callbacks/exc:%s/complete" % ex.id, {}),
        ("post", "/wholesale/ops/callbacks/from-exception/%s" % ex.id, {}),
        ("get", "/wholesale/ops/deals/%s" % D, None),
        ("get", "/wholesale/ops/deals/%s/notes" % D, None),
        ("post", "/wholesale/ops/deals/%s/notes" % D, {"body": "planted"}),
        ("patch", "/wholesale/ops/notes/%s" % note["id"], {"body": "stolen"}),
        ("delete", "/wholesale/ops/notes/%s" % note["id"], None),
    ]
    for method, path, body in attempts:
        kw = {"headers": hi}
        if body is not None:
            kw["json"] = body
        r = getattr(client, method)(path, **kw)
        assert r.status_code == 404, "%s %s -> %s" % (method, path, r.status_code)
    listing = ok(client.get("/wholesale/ops/callbacks", headers=hi))
    assert all(not v for v in listing["buckets"].values())
    # ...and nothing of A's changed.
    assert ok(client.get("/wholesale/ops/deals/%s/notes" % D, headers=h))["notes"][0]["body"] == "x"
    assert ok(client.get("/wholesale/ops/leads/%s/control" % L, headers=h))["paused_ai"] is False


def test_ops_routes_require_the_wholesale_feature(client, db_session, other_org, intruder):
    import json
    other_org.enabled_features = json.dumps(["leads"])
    db_session.commit()
    for path in ("/wholesale/ops/callbacks", "/wholesale/ops/pilot", "/wholesale/ops/skip-trace"):
        assert client.get(path, headers=_h(db_session, intruder)).status_code in (402, 403)


# ── Review follow-ups ───────────────────────────────────────────────────────

def test_reuse_never_crosses_the_sandbox_line(db_session, sample_org, admin):
    from app.services import wholesale_service as svc
    from app.models.wholesale_models import WholesaleProperty
    real = _lead(db_session, sample_org, admin, "12145550171", email="real@example.com")
    sandbox = WholesaleProperty(organization_id=sample_org.id, street_address="5 Test St",
                                zip_code="75201", is_test=True)
    db_session.add(sandbox)
    db_session.commit()
    p = svc.attach_seller(db_session, sample_org.id, admin, sandbox,
                          {"first_name": "Rehearsal", "phone": "2145550171", "email": "real@example.com"})
    assert p.lead_id != real.id and db_session.get(Lead, p.lead_id).is_test is True
    # ...and a real property never adopts the sandbox lead just created.
    real_prop = WholesaleProperty(organization_id=sample_org.id, street_address="6 Real St", zip_code="75201")
    db_session.add(real_prop)
    db_session.commit()
    test_lead_id = p.lead_id
    p2 = svc.attach_seller(db_session, sample_org.id, admin, real_prop, {"phone": "2145550171"})
    assert p2.lead_id == real.id and p2.lead_id != test_lead_id


def test_pause_and_takeover_also_block_automated_email(db_session, sample_org, admin, deal):
    from app.models.models import EmailMessage
    from app.services import wholesale_ops as OPS
    from app.services.email_service import send_email_to_lead
    lead = db_session.get(Lead, deal["lead_id"])
    lead.email = "pat@example.com"
    db_session.commit()
    OPS.apply_control(db_session, sample_org.id, lead, admin, "pause")
    db_session.commit()
    for src in (None, "cadence", "ai_conversation", "auto_send", "pipeline_auto_reply"):
        with pytest.raises(ValueError, match="AI_PAUSED"):
            send_email_to_lead(db_session, admin, lead, subject="s", body_html="<p>x</p>", send_source=src)
    OPS.apply_control(db_session, sample_org.id, lead, admin, "takeover")
    db_session.commit()
    with pytest.raises(ValueError, match="HUMAN_TAKEOVER"):
        send_email_to_lead(db_session, admin, lead, subject="s", body_html="<p>x</p>", send_source="cadence")
    assert OPS.ai_send_refusal(db_session, lead, "manual") is None
    assert db_session.query(EmailMessage).count() == 0
