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
    assert sorted(actions) == ["created", "repeat_submission"]
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
    assert hist == ["created", "assigned", "status", "note"]
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
