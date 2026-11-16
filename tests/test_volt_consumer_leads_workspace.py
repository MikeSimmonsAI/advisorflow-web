# -*- coding: utf-8 -*-
"""Consumer Leads as its own workspace (2026-11-14, #571): own access rules,
website preview into a test partition, lead sources (signed webhook, Google
Ads lead form, CSV), idempotent deliveries with retry, follow-up monitoring."""
import hashlib
import hmac
import json
import time

from app.models.volt_leads_models import VoltLead, VoltLeadDelivery, VoltLeadEvent
from app.services.volt import lead_sources as S
from app.services.volt import leads as L
from tests.test_volt_recruiting import H, _user, volt  # noqa: F401  (fixture)
from tests.test_volt_leads import SLUG, enable, lead, licensed, ops, post, team


def _ops(v, who, path, method="get", **kw):
    return getattr(v["c"], method)("/volt/ops/%s/leads%s" % (v["org"].id, path), headers=H(v["db"], v[who]), **kw)


def _agent(v, name="leads.only"):
    """A leads-only person: a member of the VOLT org with NO recruiting staff role."""
    u = _user(v["db"], "advisor", v["org"], "member")
    v["db"].commit()
    v[name] = u
    return u


def _sign(secret, body, ts=None):
    ts = str(int(ts if ts is not None else time.time()))
    sig = hmac.new(secret.encode(), ("%s.%s" % (ts, body)).encode(), hashlib.sha256).hexdigest()
    return {"X-Volt-Timestamp": ts, "X-Volt-Signature": "sha256=" + sig, "Content-Type": "application/json"}


def _hook(v, sid, body, headers):
    return v["c"].post("/volt/public/%s/leads/hooks/%s" % (SLUG, sid), content=body, headers=headers)


# ── access and workspace card ───────────────────────────────────────────────

def test_page_has_v3_steps_and_questions():
    assert len(L.PAGE["steps"]) == 3 and len(L.PAGE["faq"]) == 5
    assert any("government" in q for q, _ in L.PAGE["faq"])


def test_leads_only_agent_and_team_rules(volt):
    enable(volt)
    post(volt)
    lid = volt["db"].query(VoltLead).one().id
    a = _agent(volt)
    assert _ops(volt, "leads.only", "").status_code == 403                        # member, no role yet
    team(volt, "leads.only")                                                      # owner adds as agent
    licensed(volt, "leads.only")                                                  # owner verifies the license
    assert _ops(volt, "leads.only", "").json() == {"access": "assigned", "test": False, "leads": []}
    assert _ops(volt, "god", "/%s/assign" % lid, "post", json={"user_id": a.id}).status_code == 200
    assert len(_ops(volt, "leads.only", "").json()["leads"]) == 1
    assert _ops(volt, "leads.only", "/" + lid, "patch", json={"status": "contacted"}).status_code == 200
    # Agents cannot manage the team, see settings, or preview.
    assert _ops(volt, "leads.only", "/grants", "post", json={"user_id": volt["staff"].id}).status_code == 403
    assert _ops(volt, "leads.only", "/settings").status_code == 403
    assert _ops(volt, "leads.only", "/preview", "post", json=lead()).status_code == 403
    # Someone outside the VOLT org cannot be added.
    r = _ops(volt, "god", "/grants", "post", json={"user_id": volt["outsider"].id})
    assert r.status_code == 422
    # Recruiting-only staff (VOLT org admin) still has no Consumer Leads access.
    assert _ops(volt, "staff", "").status_code == 403


def test_named_operator_adds_agents_but_not_managers(volt, monkeypatch):
    monkeypatch.setenv("VOLT_DEV_PRINCIPALS", volt["staff"].email)
    a = _agent(volt)
    assert _ops(volt, "staff", "/settings").status_code == 200
    assert _ops(volt, "staff", "/grants", "post", json={"user_id": a.id, "role": "manager"}).status_code == 403
    r = _ops(volt, "staff", "/grants", "post", json={"user_id": a.id, "role": "agent"})
    assert r.status_code == 200 and r.json()["grants"][0]["role"] == "agent"
    assert any(x["id"] == a.id for x in _ops(volt, "staff", "/agents").json()["agents"])
    assert _ops(volt, "staff", "/settings", "put", json={"intake_enabled": True, "reviewed": True,
                                                        "review_note": "trying to switch on"}).status_code == 403


def test_workspace_card_only_for_authorized_people(volt, monkeypatch):
    c, db = volt["c"], volt["db"]

    def cards(who):
        r = c.get("/auth/my-contexts", headers=H(db, volt[who]))
        assert r.status_code == 200
        return r.json()

    god = cards("god")["consumer_leads_contexts"]
    assert [x["organization_id"] for x in god] == [volt["org"].id] and god[0]["path"].startswith("/consumer-leads/")
    staff = cards("staff")
    assert staff["consumer_leads_contexts"] == []                                # recruiting role alone: no card
    assert [x["organization_id"] for x in staff["volt_contexts"]] == [volt["org"].id]   # recruiting card intact
    assert cards("other_staff")["consumer_leads_contexts"] == []
    assert cards("outsider")["consumer_leads_contexts"] == []
    monkeypatch.setenv("VOLT_DEV_PRINCIPALS", volt["staff"].email)
    assert cards("staff")["consumer_leads_contexts"][0]["access"] == "all"       # D'Angelo's rule
    monkeypatch.delenv("VOLT_DEV_PRINCIPALS")
    a = _agent(volt)
    team(volt, "leads.only")
    assert cards("leads.only")["consumer_leads_contexts"][0]["access"] == "assigned"


# ── preview into the test partition ─────────────────────────────────────────

def test_preview_writes_test_lead_only_with_intake_off(volt, monkeypatch):
    monkeypatch.setenv("VOLT_DEV_PRINCIPALS", volt["staff"].email)
    assert post(volt).status_code == 503                                          # public intake still off
    for who in ("god", "staff"):                                                  # owner and D'Angelo
        r = _ops(volt, who, "/preview", "post", json=lead(first_name="Synthetic", phone="(555) 234-77%02d" % len(who)))
        assert r.status_code == 200 and r.json()["test"] is True
    for who in ("viewer", "outsider", "other_staff"):
        assert _ops(volt, who, "/preview", "post", json=lead()).status_code == 403
    db = volt["db"]
    assert db.query(VoltLead).filter(VoltLead.is_test.is_(False)).count() == 0
    assert db.query(VoltLead).filter(VoltLead.is_test.is_(True)).count() == 2
    # Real views never show test rows; test views show them.
    assert _ops(volt, "god", "").json()["leads"] == []
    assert _ops(volt, "god", "/report").json()["total"] == 0
    assert _ops(volt, "god", "/summary").json()["total"] == 0
    t = _ops(volt, "god", "?test=1").json()
    assert t["test"] is True and len(t["leads"]) == 2 and all(x["test"] for x in t["leads"])
    lid = t["leads"][0]["id"]
    assert _ops(volt, "god", "/" + lid).status_code == 404                        # not reachable as a real lead
    a = _agent(volt)
    team(volt, "leads.only")
    r = _ops(volt, "god", "/%s/assign" % lid, "post", json={"user_id": a.id, "test": True})
    assert r.status_code == 200 and r.json()["test"] is True
    r = _ops(volt, "leads.only", "/%s?test=1" % lid, "patch", json={"status": "qualified"})
    assert r.status_code == 200 and r.json()["status"] == "qualified"
    hist = [h["action"] for h in r.json()["history"]]
    assert hist[:2] == ["created", "consent_recorded"] and "assigned" in hist and all(
        h["at"].endswith("+00:00") for h in r.json()["history"])
    assert _ops(volt, "god", "/report?test=1").json()["total"] == 2
    assert _ops(volt, "leads.only", "/test-data/clear", "post").status_code == 403
    assert _ops(volt, "god", "/test-data/clear", "post").json()["removed"] == 2
    assert db.query(VoltLead).count() == 0


def test_preview_does_not_merge_with_real_lead(volt):
    enable(volt)
    post(volt)
    assert _ops(volt, "god", "/preview", "post", json=lead()).json()["created"] is True
    assert volt["db"].query(VoltLead).count() == 2
    assert volt["db"].query(VoltLead).filter(VoltLead.is_test.is_(False)).one().submissions == 1


# ── lead sources ────────────────────────────────────────────────────────────

def _source(v, provider="custom_webhook", test=True, **kw):
    r = _ops(v, "god", "/sources", "post", json=dict(provider=provider, name="Src", test=test, **kw))
    assert r.status_code == 200, r.text
    return r.json()


def test_signed_webhook_accept_reject_duplicate_and_attribution(volt):
    src = _source(volt, campaign="Fall 2026")
    sid, secret = src["id"], src["secret"]
    assert secret and src["status"] == "paused"
    assert "secret" not in _ops(volt, "god", "/sources").json()["sources"][0]          # shown once only
    body = json.dumps({"id": "ext-1", "first_name": "Webhook", "last_name": "Test", "phone": "555-234-8899",
                       "zip": "75201", "state": "TX", "consent": True, "utm_source": "facebook"})
    assert _hook(volt, sid, body, _sign(secret, body)).status_code == 503              # paused
    assert _ops(volt, "god", "/sources/" + sid, "patch", json={"status": "active"}).status_code == 200
    r = _hook(volt, sid, body, _sign(secret, body))
    assert r.status_code == 200 and r.json() == {"received": True, "duplicate": False, "status": "processed"}
    lead_row = volt["db"].query(VoltLead).one()
    assert lead_row.is_test and lead_row.channel == "meta" and lead_row.utm_campaign == "Fall 2026"
    assert lead_row.source_id == sid
    r = _hook(volt, sid, body, _sign(secret, body))                                    # same id again
    assert r.json()["duplicate"] is True and volt["db"].query(VoltLead).count() == 1
    bad = _sign("wrong-secret", body)
    assert _hook(volt, sid, body, bad).status_code == 401
    old = _sign(secret, body, ts=time.time() - 3600)
    assert _hook(volt, sid, body, old).status_code == 401                              # stale timestamp
    s = _ops(volt, "god", "/sources").json()["sources"][0]
    assert (s["received"], s["duplicates"], s["rejected"]) == (1, 1, 2)
    d = volt["db"].query(VoltLeadDelivery).one()
    assert d.payload is None and d.status == "processed"                               # payload cleared
    # Another business cannot see or use this source.
    assert volt["c"].get("/volt/ops/%s/leads/sources" % volt["other"].id,
                         headers=H(volt["db"], volt["other_staff"])).status_code == 403
    r = volt["c"].post("/volt/public/other-recruiting/leads/hooks/%s" % sid, content=body, headers=_sign(secret, body))
    assert r.status_code == 404


def test_real_sources_wait_for_intake_and_pending_providers_cannot_start(volt):
    real = _source(volt, test=False)
    r = _ops(volt, "god", "/sources/" + real["id"], "patch", json={"status": "active"})
    assert r.status_code == 409 and "intake" in r.text
    for p in ("facebook", "instagram", "tiktok"):
        s = _source(volt, provider=p)
        assert s["status"] == "pending_connection" and s["needs"] and s["webhook_url"] is None
        assert _ops(volt, "god", "/sources/" + s["id"], "patch", json={"status": "active"}).status_code == 409
    enable(volt)
    assert _ops(volt, "god", "/sources/" + real["id"], "patch", json={"status": "active"}).status_code == 200
    body = json.dumps({"id": "r-1", "first_name": "Real", "phone": "555-234-1212", "zip": "75201", "consent": True})
    assert _hook(volt, real["id"], body, _sign(real["secret"], body)).json()["status"] == "processed"
    assert volt["db"].query(VoltLead).filter(VoltLead.is_test.is_(False)).count() == 1
    # Owner switches intake off again: the real source stops accepting.
    _ops(volt, "god", "/settings", "put", json={"intake_enabled": False})
    body2 = json.dumps({"id": "r-2", "first_name": "Late", "phone": "555-234-1313", "zip": "75201", "consent": True})
    assert _hook(volt, real["id"], body2, _sign(real["secret"], body2)).status_code == 503


def test_failed_delivery_mapping_fix_and_retry(volt, monkeypatch):
    src = _source(volt)
    sid, secret = src["id"], src["secret"]
    _ops(volt, "god", "/sources/" + sid, "patch", json={"status": "active"})
    body = json.dumps({"id": "m-1", "fname": "Mapped", "mobile": "555-234-4545", "zip": "75201", "consent": "yes"})
    assert _hook(volt, sid, body, _sign(secret, body)).json()["status"] == "failed"
    dl = _ops(volt, "god", "/sources/%s/deliveries" % sid).json()["deliveries"][0]
    assert dl["status"] == "failed" and dl["can_retry"] and "first_name" in dl["error"]
    _ops(volt, "god", "/sources/" + sid, "patch",
         json={"field_map": {"first_name": "fname", "phone": "mobile", "zip": "zip", "consent": "consent"}})
    r = _ops(volt, "god", "/deliveries/%s/retry" % dl["id"], "post")
    assert r.status_code == 200 and r.json()["status"] == "processed"
    assert volt["db"].query(VoltLead).one().first_name == "Mapped"
    # A temporary failure is retried automatically by the watch loop.
    real_create = L.create_or_merge
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database hiccup")
        return real_create(*a, **kw)
    monkeypatch.setattr(L, "create_or_merge", flaky)
    body = json.dumps({"id": "m-2", "fname": "Retry", "mobile": "555-234-4646", "zip": "75201", "consent": True})
    assert _hook(volt, sid, body, _sign(secret, body)).json()["status"] == "failed"
    d = volt["db"].query(VoltLeadDelivery).filter(VoltLeadDelivery.external_id == "m-2").one()
    assert d.next_retry_at is not None and d.payload is not None
    from datetime import datetime, timedelta
    d.next_retry_at = datetime.utcnow() - timedelta(seconds=1)
    volt["db"].commit()
    assert S.retry_pass(volt["db"]) == 1
    volt["db"].commit()
    volt["db"].refresh(d)
    assert d.status == "processed" and d.attempts == 2 and d.payload is None


def test_google_ads_lead_form_key(volt):
    src = _source(volt, provider="google_ads")
    sid, key = src["id"], src["secret"]
    _ops(volt, "god", "/sources/" + sid, "patch", json={"status": "active"})
    payload = {"lead_id": "g-1", "form_id": 77, "campaign_id": 55, "google_key": key, "api_version": "1.0",
               "user_column_data": [{"column_id": "FULL_NAME", "string_value": "Gina Google"},
                                    {"column_id": "PHONE_NUMBER", "string_value": "+1 555-234-5656"},
                                    {"column_id": "POSTAL_CODE", "string_value": "75201"}]}
    r = _hook(volt, sid, json.dumps(payload), {"Content-Type": "application/json"})
    assert r.json()["status"] == "processed"
    row = volt["db"].query(VoltLead).one()
    assert (row.first_name, row.last_name, row.channel) == ("Gina", "Google", "google")
    ev = volt["db"].query(VoltLeadEvent).filter(VoltLeadEvent.action == "consent_recorded").one()
    assert "Google Ads lead form 77" in ev.note
    payload["google_key"] = "nope"
    assert _hook(volt, sid, json.dumps(payload), {"Content-Type": "application/json"}).status_code == 401


def test_csv_import(volt):
    src = _source(volt, provider="csv")
    sid = src["id"]
    _ops(volt, "god", "/sources/" + sid, "patch", json={"status": "active"})
    rows = [{"first_name": "Cee", "phone": "555-234-3131", "zip": "75201", "consent": "yes"},
            {"first_name": "Ess", "email": "ess@example.test", "zip": "75202", "consent": "yes"},
            {"first_name": "", "phone": "555-234-3232", "zip": "75201", "consent": "yes"}]
    assert _ops(volt, "god", "/sources/%s/import" % sid, "post", json={"rows": rows, "attestation": "short"}).status_code == 422
    att = "Signed paper forms at the Nov 12 health fair, kept in the office"
    r = _ops(volt, "god", "/sources/%s/import" % sid, "post", json={"rows": rows, "attestation": att})
    assert r.json() == {"processed": 2, "failed": 1, "duplicate": 0}
    r = _ops(volt, "god", "/sources/%s/import" % sid, "post", json={"rows": rows[:1], "attestation": att})
    assert r.json()["duplicate"] == 1
    assert volt["db"].query(VoltLead).filter(VoltLead.is_test.is_(True)).count() == 2


# ── responses & follow-up ───────────────────────────────────────────────────

def test_followup_monitoring_and_no_outreach(volt):
    enable(volt)
    post(volt)
    post(volt, phone="(555) 234-9090", email="b@example.test")
    ids = [r.id for r in volt["db"].query(VoltLead).all()]
    _ops(volt, "god", "/" + ids[0], "patch", json={"event": "call_attempted"})
    r = _ops(volt, "god", "/" + ids[1], "patch", json={"event": "follow_up_scheduled", "follow_up_at": "2026-01-01T15:00:00Z"})
    assert r.status_code == 200 and r.json()["next_follow_up_at"].startswith("2026-01-01T15:00:00")
    f = _ops(volt, "god", "/followup").json()
    assert f["response"]["contacted"] == 1 and f["response"]["never_contacted"] == 1
    assert [x["id"] for x in f["follow_ups_due"]] == [ids[1]] and f["follow_ups_due"][0]["overdue"] is True
    states = {c["key"]: c["state"] for c in f["channels"]}
    assert states["customer_text"] == "not_enabled" and states["customer_email"] == "not_enabled"
    assert _ops(volt, "staff", "/followup").status_code == 403
