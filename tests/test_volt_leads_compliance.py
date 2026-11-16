# -*- coding: utf-8 -*-
"""Consumer-leads launch safeguards (#619): collecting company, privacy notice,
two-part consent, opt-out, license-gated assignment, no automatic outreach."""
from app.models.volt_leads_models import VoltLead, VoltLeadEvent
from app.services.volt import leads as L
from app.services.volt import leads_compliance as LC
from tests.test_volt_leads import COLLECTOR, SLUG, enable, licensed, ops, post, ready, team
from tests.test_volt_recruiting import H, _user, volt  # noqa: F401  (fixture)

ON = {"intake_enabled": True, "reviewed": True, "review_note": "Owner turned intake on for launch"}


def cfg(v):
    return v["c"].get("/volt/public/%s/leads/config" % SLUG).json()


def test_intake_refuses_until_company_and_licensed_agent_exist(volt):
    r = ops(volt, "god", "/settings", "put", json=ON)
    assert r.status_code == 422
    assert "company" in r.text.lower() or "collect" in r.text.lower()
    # Company alone is not enough: no verified licensed agent yet.
    assert ops(volt, "god", "/settings/collector", "put", json=COLLECTOR).status_code == 200
    r = ops(volt, "god", "/settings", "put", json=ON)
    assert r.status_code == 422 and "licens" in r.text.lower()
    s = ops(volt, "god", "/settings").json()
    assert {x["key"]: x["ok"] for x in s["readiness"]}["collector"] is True
    assert not all(x["ok"] for x in s["readiness"])
    # Only the owner may set the company.
    assert ops(volt, "staff", "/settings/collector", "put", json=COLLECTOR).status_code == 403
    assert cfg(volt)["enabled"] is False and post(volt).status_code == 503


def test_collector_validation_and_name_change_turns_intake_off(volt):
    bad = dict(COLLECTOR, privacy_email="not-an-email")
    assert ops(volt, "god", "/settings/collector", "put", json=bad).status_code == 422
    assert ops(volt, "god", "/settings/collector", "put", json=dict(COLLECTOR, collector_address="short")).status_code == 422
    enable(volt)
    assert cfg(volt)["enabled"] is True
    assert ops(volt, "god", "/settings/collector", "put",
               json=dict(COLLECTOR, collector_name="Another Example Test LLC")).status_code == 200
    assert cfg(volt)["enabled"] is False


def test_privacy_notice_names_company_and_says_no_selling(volt):
    p = cfg(volt)["privacy"]
    assert p["available"] is False          # nothing invented before the owner enters a company
    ready(volt)
    c = cfg(volt)
    assert c["privacy_path"] == LC.PRIVACY_PATH and c["collector"]["name"] == COLLECTOR["collector_name"]
    text = " ".join(" ".join(paras) for _, paras in c["privacy"]["sections"])
    assert COLLECTOR["collector_name"] in text and COLLECTOR["privacy_email"] in text
    assert "do not sell" in text.lower()
    for claim in ("government", "guarantee"):
        assert claim not in c["consent_text"].lower()
    assert COLLECTOR["collector_name"] in c["consent_text"] and COLLECTOR["collector_name"] in c["automated_consent_text"]


def test_two_part_consent_recorded_separately(volt):
    enable(volt)
    assert post(volt, automated_consent="yes").status_code == 422
    assert post(volt).status_code == 200            # automated box left unticked
    row = volt["db"].query(VoltLead).one()
    assert row.automated_contact_consent is False and row.consent_version == L.CONSENT_VERSION
    contact, automated = LC.consent_texts(LC.collector(L.settings_row(volt["db"], volt["org"].id)))
    assert row.consent_text_hash == LC.consent_hash(contact, automated, False)
    # Second submission ticks the optional box: recorded, never removed afterwards.
    post(volt, automated_consent=True)
    volt["db"].refresh(row)
    assert row.automated_contact_consent is True
    post(volt, automated_consent=False)
    volt["db"].refresh(row)
    assert row.automated_contact_consent is True
    acts = [e.action for e in volt["db"].query(VoltLeadEvent).filter_by(lead_id=row.id)]
    assert "automated_consent_recorded" in acts


def test_strict_website_questions_required(volt):
    enable(volt)
    for k in ("household_size", "state", "income_range", "contact_preference", "best_time"):
        assert post(volt, **{k: None}).status_code == 422, k
    assert volt["db"].query(VoltLead).count() == 0


def test_opt_out_is_permanent_and_survives_resubmission(volt):
    enable(volt)
    post(volt)
    lid = ops(volt, "god").json()["leads"][0]["id"]
    r = ops(volt, "god", "/%s/opt-out" % lid, "post", json={"how": "said so on the phone"})
    assert r.status_code == 200 and r.json()["contact_opt_out_at"]
    post(volt)                                       # they fill the form again
    d = ops(volt, "god", "/%s" % lid).json()
    assert d["contact_opt_out_at"] and d["do_not_contact"] is True
    hist = " ".join((h.get("note") or "") for h in d["history"])
    assert "stays stopped" in hist


def test_real_leads_need_a_verified_license_for_their_state(volt):
    enable(volt)
    post(volt)                                       # PA lead
    lid = ops(volt, "god").json()["leads"][0]["id"]
    assert ops(volt, "god").json()["leads"][0]["awaiting_agent"] is True
    team(volt, "staff")
    assert ops(volt, "god", "/%s/assign" % lid, "post", json={"user_id": volt["staff"].id}).status_code == 422
    licensed(volt, "staff", states=["TX"])
    assert ops(volt, "god", "/%s/assign" % lid, "post", json={"user_id": volt["staff"].id}).status_code == 422
    licensed(volt, "staff", states=["TX", "PA"])
    assert ops(volt, "god", "/%s/assign" % lid, "post", json={"user_id": volt["staff"].id}).status_code == 200
    # License entry is owner-only and needs the "verified" tick and a real-looking NPN.
    base = "/grants/%s/license" % volt["staff"].id
    assert ops(volt, "staff", base, "put", json={"npn": "1234567", "states": ["PA"], "verified": True}).status_code == 403
    assert ops(volt, "god", base, "put", json={"npn": "1234567", "states": ["PA"], "verified": False}).status_code == 422
    assert ops(volt, "god", base, "put", json={"npn": "12", "states": ["PA"], "verified": True}).status_code == 422
    outsider = _user(volt["db"], "advisor", volt["org"], "spectator")
    volt["outsider"] = outsider
    assert ops(volt, "god", "/grants/%s/license" % outsider.id, "put",
               json={"npn": "1234567", "states": ["PA"], "verified": True}).status_code in (404, 422)


def test_submitting_sends_nothing_to_the_customer(volt, monkeypatch):
    sent = []
    import app.services.volt.leads as mod
    for name in dir(mod):
        if any(w in name.lower() for w in ("send_sms", "send_email", "twilio", "dial")):
            monkeypatch.setattr(mod, name, lambda *a, **k: sent.append(name))
    enable(volt)
    assert post(volt, automated_consent=True).status_code == 200
    assert sent == []
    acts = {e.action for e in volt["db"].query(VoltLeadEvent)}
    assert acts <= {"created", "consent_recorded", "automated_consent_recorded"}
