# -*- coding: utf-8 -*-
"""VOLT consumer leads (DEV-4): intake, dedup, consent, isolation, assignment, history."""
import logging

import pytest
from sqlalchemy import inspect

from app.models.volt_leads_models import VoltLead, VoltLeadEvent
from app.services.volt import leads as L
from tests.test_volt_recruiting import H, _user, volt  # noqa: F401  (fixture)

SLUG = "volt-marketing"


def lead(**kw):
    b = {"first_name": "Lee", "last_name": "Sample", "zip": "19103", "phone": "(555) 234-5678",
         "email": "lee@example.test", "age_range": "26-44", "coverage_status": "none", "household_size": 2,
         "consent": True, "consent_version": L.CONSENT_VERSION, "source": "facebook", "campaign": "spring<script>"}
    b.update(kw)
    return b


def enable(v):
    r = v["c"].put("/volt/ops/%s/leads/settings" % v["org"].id, headers=H(v["db"], v["god"]),
                   json={"intake_enabled": True, "reviewed": True, "review_note": "Compliance approved 2026-11-14"})
    assert r.status_code == 200, r.text


def post(v, **kw):
    return v["c"].post("/volt/public/%s/leads" % SLUG, json=lead(**kw))


def ops(v, who, path="", method="get", **kw):
    return getattr(v["c"], method)("/volt/ops/%s/leads%s" % (v["org"].id, path), headers=H(v["db"], v[who]), **kw)


def test_tables_created_and_separate(volt):
    names = inspect(volt["db"].get_bind()).get_table_names()
    for t in ("volt_leads", "volt_lead_events", "volt_lead_grants", "volt_lead_settings", "volt_reps"):
        assert t in names


def test_intake_disabled_by_default_and_config_public(volt):
    assert volt["c"].get("/volt/public/%s/leads/config" % SLUG).json()["enabled"] is False
    assert post(volt).status_code == 503
    assert volt["db"].query(VoltLead).count() == 0


def test_enabling_needs_review_and_owner(volt):
    path = "/settings"
    assert ops(volt, "staff", path, "put", json={"intake_enabled": True, "reviewed": True,
                                                  "review_note": "long enough note"}).status_code == 403
    assert ops(volt, "god", path, "put", json={"intake_enabled": True}).status_code == 422


def test_submit_validation_consent_and_public_response(volt, caplog):
    enable(volt)
    caplog.set_level(logging.DEBUG)
    assert post(volt, consent=False).status_code == 422
    assert post(volt, consent_version="old").status_code == 422
    assert post(volt, zip="1234").status_code == 422
    assert post(volt, phone="123", email="").status_code == 422
    assert post(volt, phone="", email="").status_code == 422
    assert post(volt, age_range="x").status_code == 422
    assert volt["db"].query(VoltLead).count() == 0
    r = post(volt)
    assert r.status_code == 200 and set(r.json()) == {"ok", "message"}
    row = volt["db"].query(VoltLead).one()
    assert row.consent_version == L.CONSENT_VERSION and row.consent_at and row.phone == "5552345678"
    assert row.campaign == "springscript"   # angle brackets stripped
    assert "5552345678" not in caplog.text and "lee@example.test" not in caplog.text


def test_dedup_and_retry_are_identical_and_leak_nothing(volt):
    enable(volt)
    a, b = post(volt), post(volt, first_name="Other", source="google")
    assert a.json() == b.json()
    row = volt["db"].query(VoltLead).one()
    volt["db"].refresh(row)
    assert row.submissions == 2 and row.source == "facebook" and row.first_name == "Lee"
    actions = [e.action for e in volt["db"].query(VoltLeadEvent).all()]
    assert sorted(actions) == ["consent_recorded", "created", "repeat_submission"]
    assert all("5552345678" not in str(e.note or e.new_value) for e in volt["db"].query(VoltLeadEvent))


def test_public_rate_limit(volt):
    enable(volt)
    codes = [post(volt, phone="(555) 234-%04d" % i).status_code for i in range(14)]
    assert 429 in codes


def test_isolation_assignment_and_history(volt):
    enable(volt)
    post(volt)
    lid = ops(volt, "god").json()["leads"][0]["id"]
    # No grant: org admin sees nothing, cannot open, update or assign.
    assert ops(volt, "staff").json()["leads"] == []
    assert ops(volt, "staff", "/" + lid).status_code == 404
    assert ops(volt, "staff", "/" + lid, "patch", json={"status": "contacted"}).status_code == 404
    assert ops(volt, "staff", "/%s/assign" % lid, "post", json={"user_id": volt["staff"].id}).status_code == 403
    # Viewer, outsider and the other tenant's admin are refused outright.
    for who in ("viewer", "outsider", "other_staff"):
        assert ops(volt, who).status_code == 403
        assert ops(volt, who, "/report").status_code == 403
    # Owner assigns; the agent now sees exactly that lead.
    r = ops(volt, "god", "/%s/assign" % lid, "post", json={"user_id": volt["staff"].id})
    assert r.status_code == 200 and r.json()["assigned_user_id"] == volt["staff"].id
    assert len(ops(volt, "staff").json()["leads"]) == 1
    assert ops(volt, "staff", "?q=555").json()["leads"][0]["id"] == lid
    assert ops(volt, "staff", "/report").json()["total"] == 1
    r = ops(volt, "staff", "/" + lid, "patch", json={"status": "contacted", "note": "Left voicemail"})
    assert r.status_code == 200 and r.json()["status"] == "contacted"
    assert ops(volt, "staff", "/" + lid, "patch", json={"status": "bogus"}).status_code == 422
    hist = [h["action"] for h in ops(volt, "staff", "/" + lid).json()["history"]]
    assert hist == ["created", "consent_recorded", "assigned", "status", "note"]
    # Assigning a non-staff person is refused.
    assert ops(volt, "god", "/%s/assign" % lid, "post", json={"user_id": volt["viewer"].id}).status_code == 422
    # Other tenant's path never exposes this tenant's lead.
    r = volt["c"].get("/volt/ops/%s/leads/%s" % (volt["other"].id, lid), headers=H(volt["db"], volt["other_staff"]))
    assert r.status_code == 404


def test_owner_grant_widens_access_and_can_be_revoked(volt):
    enable(volt)
    post(volt)
    assert ops(volt, "staff").json()["leads"] == []
    assert ops(volt, "staff", "/grants", "post", json={"user_id": volt["staff"].id}).status_code == 403
    r = ops(volt, "god", "/grants", "post", json={"user_id": volt["staff"].id})
    assert r.status_code == 200 and len(r.json()["grants"]) == 1
    out = ops(volt, "staff").json()
    assert out["access"] == "all" and len(out["leads"]) == 1
    ops(volt, "god", "/grants", "post", json={"user_id": volt["staff"].id, "active": False})
    assert ops(volt, "staff").json()["leads"] == []


def test_recruiting_data_untouched(volt):
    enable(volt)
    b, tok, _ = volt["register"]()
    post(volt)
    assert volt["c"].get("/volt/rep/%s/me" % SLUG, headers=tok).status_code == 200
    assert volt["db"].query(VoltLead).count() == 1



# ── 2026-11-14 completion: fields, attribution, activity, reporting, card, access ──

def test_new_fields_validation_and_attribution(volt):
    enable(volt)
    base = dict(state="tx", income_range="35k_50k", contact_preference="text", best_time="evening",
                utm_source="facebook", utm_medium="paid_social", utm_campaign="nov-aca", utm_content="ad-3",
                referrer_host="m.facebook.com", landing_path="/volt/check-coverage")
    assert post(volt, **dict(base, state="ZZ")).json()["detail"]["field"] == "state"
    assert post(volt, **dict(base, income_range="lots")).json()["detail"]["field"] == "income_range"
    assert post(volt, **dict(base, contact_preference="pigeon")).json()["detail"]["field"] == "contact_preference"
    assert post(volt, **dict(base, contact_preference="email", email="")).json()["detail"]["field"] == "email"
    assert post(volt, **dict(base, contact_preference="text", phone="", email="x@example.test")).json()["detail"]["field"] == "phone"
    assert post(volt, **base).status_code == 200
    row = volt["db"].query(VoltLead).one()
    assert (row.state, row.income_range, row.contact_preference, row.best_time) == ("TX", "35k_50k", "text", "evening")
    assert (row.channel, row.utm_campaign, row.utm_medium, row.referrer_host) == ("meta", "nov-aca", "paid_social", "m.facebook.com")
    assert L.channel_of("google", "cpc", "") == "google" and L.channel_of("", "", "www.google.com") == "organic"
    assert L.channel_of("", "", "") == "direct" and L.channel_of("", "", "news.example.com") == "referral"
    assert L.channel_of("referral", "", "") == "referral" and L.channel_of("tiktok", "", "") == "other"
    cfg = volt["c"].get("/volt/public/%s/leads/config" % SLUG).json()
    assert cfg["page"]["headline"] == "You May Qualify for $0 Monthly Health Insurance."
    text = " ".join(cfg["page"]["disclaimers"])
    for must in ("Not everyone qualifies", "not guaranteed", "does not mean all care is free", "not a government website", "HealthCare.gov"):
        assert must in text
    assert "Medicare" in cfg["page"]["medicare_note"]


def test_activity_events_timestamps_and_report(volt):
    enable(volt)
    assert post(volt, phone="(555) 234-0001", email="a1@example.test", utm_source="google", utm_medium="cpc").status_code == 200
    lid = volt["db"].query(VoltLead).one().id
    ops(volt, "god", "/%s/assign" % lid, "post", json={"user_id": volt["staff"].id})
    assert ops(volt, "staff", "/" + lid, "patch", json={"event": "call_attempted", "note": "no answer"}).status_code == 200
    r = ops(volt, "staff", "/" + lid, "patch", json={"event": "contact_completed"}).json()
    assert r["status"] == "contacted" and r["last_contact_at"].endswith("+00:00")
    r = ops(volt, "staff", "/" + lid, "patch", json={"event": "appointment_set", "appointment_at": "2026-11-20T21:30:00Z"}).json()
    assert r["status"] == "appointment_set" and r["appointment_at"].startswith("2026-11-20T21:30:00")
    r = ops(volt, "staff", "/" + lid, "patch", json={"event": "appointment_set", "appointment_at": "2026-11-21T15:00:00-06:00"}).json()
    assert r["appointment_at"].startswith("2026-11-21T21:00:00")                      # stored in UTC
    r = ops(volt, "staff", "/" + lid, "patch", json={"event": "enrollment_recorded", "note": "Silver plan"}).json()
    assert r["status"] == "enrolled" and r["enrolled_at"]
    assert ops(volt, "staff", "/" + lid, "patch", json={"event": "teleport"}).status_code == 422
    assert ops(volt, "staff", "/" + lid, "patch", json={"event": "appointment_set", "appointment_at": "soon"}).status_code == 422
    hist = r["history"]
    acts = [h["action"] for h in hist]
    for a in ("created", "consent_recorded", "assigned", "call_attempted", "contact_completed", "appointment_set",
              "appointment_changed", "enrollment_recorded"):
        assert a in acts
    assert all(h["at"].endswith("+00:00") for h in hist)                              # timezone-aware UTC
    assigned = next(h for h in hist if h["action"] == "assigned")
    assert assigned["new"] == (volt["staff"].full_name or volt["staff"].email)      # a name, not an id
    status_moves = [(h["old"], h["new"]) for h in hist if h["action"] == "status"]
    assert ("New", "Contacted") in status_moves and ("Appointment Set", "Enrolled") in status_moves
    rep = ops(volt, "god", "/report").json()
    assert rep["total"] == 1 and rep["enrollments"] == 1 and rep["appointments"] == 1 and rep["last_activity_at"]
    g = next(c for c in rep["by_channel"] if c["channel"] == "google")
    assert g == {"channel": "google", "label": "Google", "leads": 1, "qualified": 1, "appointments": 1, "enrolled": 1}
    assert rep["agents"][0]["conversion_pct"] == 100.0 and rep["cost"] is None and "No ad spend" in rep["cost_note"]


def test_campaign_card_counts_only_and_scoped(volt):
    enable(volt)
    post(volt, phone="(555) 234-0002", email="a2@example.test")
    s = ops(volt, "god", "/summary").json()
    assert s["title"] == "Consumer Leads" and s["subtitle"] == "Health Insurance Lead Acquisition & Enrollment"
    assert s["total"] == 1 and s["new"] == 1 and s["intake_enabled"] is True and s["can_settings"] is True
    assert not any(k in s for k in ("leads", "phone", "email", "name"))               # no lead details on the card
    staff = ops(volt, "staff", "/summary").json()
    assert staff["access"] == "assigned" and staff["total"] == 0 and staff["can_settings"] is False
    for who in ("viewer", "outsider"):
        assert ops(volt, who, "/summary").status_code == 403


def test_named_operator_manages_all_leads_without_a_grant(volt, monkeypatch):
    enable(volt)
    post(volt, phone="(555) 234-0003", email="a3@example.test")
    assert ops(volt, "staff").json()["access"] == "assigned"
    monkeypatch.setenv("VOLT_DEV_PRINCIPALS", volt["staff"].email)
    r = ops(volt, "staff").json()
    assert r["access"] == "all" and len(r["leads"]) == 1
    from app.services.volt import access
    _user(volt["db"], "second.admin", volt["org"], "org_admin")                      # keep one admin active
    volt["db"].commit()
    access.change(volt["db"], volt["org"].id, volt["god"], volt["staff"], "suspend", "test")
    volt["db"].commit()
    assert ops(volt, "staff").status_code == 403                                       # suspension ends it
