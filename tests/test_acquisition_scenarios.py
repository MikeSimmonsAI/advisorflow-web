"""The acquisition scenarios end to end - with a guard that fails the test if
ANY SMS or email would be handed to a provider.

Scenarios here (the rest live beside the code they test):
  strong property / questionable contact             -> contact data, not contactable
  strong property / verified contact, no SMS consent -> confidence up, still no SMS
  identical person + property in two workspaces      -> nothing crosses
  a full seller journey (inquiry -> offer request -> not now -> back -> stop)
    with no prohibited communication at any step
Also covered elsewhere: no contact data, /sell with/without consent, not now,
stop, wants offer, asking price (test_contactability / test_seller_intel);
multiple properties / multiple owners / re-engagement
(test_wholesale_intake_reconciliation); budget exhausted and provider failure
(test_evosense_journeys, test_enrichment_policy); ownership ambiguity
(test_contactability, test_seller_intel).
"""
import pytest

from app.models.evosense_models import EvoSenseContactPoint, EvoSenseProperty
from app.models.models import Lead, SuppressionEntry
from app.models.wholesale_models import (WholesaleDeal, WholesaleSellerFact,
                                         WholesaleSellerProfile, WholesaleSettings)
from app.services import contactability as CB
from app.services.evosense import contacts as CT
from app.services.evosense import eligibility as EL
from app.services.evosense import evaluate as EV
from app.services.evosense import hunt as HU
from app.services.evosense import sandbox_seed as SS

from test_wholesale_seller_sms_program import (KEY_B, _headers, _user, form, post,  # noqa: F401
                                               world)


@pytest.fixture(autouse=True)
def no_outbound(monkeypatch):
    """Every provider hand-off is recorded; the test asserts there were none."""
    sent = []

    class _Client:
        def __init__(self, *a, **k):
            sent.append(("twilio_client", a))

        def __getattr__(self, name):
            raise AssertionError("an SMS provider was reached")
    from app.services import email_service, sms_service
    monkeypatch.setattr(sms_service, "Client", _Client, raising=False)
    monkeypatch.setattr(email_service, "send_email_via_provider",
                        lambda *a, **k: sent.append(("email", a)) or {"ok": False})
    yield sent
    assert sent == [], "a message was handed to a provider: %r" % sent


# ── EvoSense owners (sandbox) ───────────────────────────────────────────────

@pytest.fixture()
def hunted(db_session, sample_org, sample_advisor):
    db, org = db_session, sample_org.id
    SS.enable_sandbox(db, org)
    s = SS.create_strategy(db, org, sample_advisor, dict(SS.DFW, name="Scenarios (TEST)",
                                                          daily_budget_cents=100000,
                                                          outreach_policy={"auto_outreach": False}))
    db.commit()
    HU.run_strategy(db, org, s, trigger="test", enrich=True)
    db.commit()
    return db, org, s, sample_advisor


def _with_contact(db, org):
    for p in (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org)
              .order_by(EvoSenseProperty.opportunity_score.desc()).all()):
        cp, sc = CT.best_contact(db, p)
        if cp is not None and cp.kind == "phone" and p.identity_status != "review":
            return p, cp
    raise AssertionError("sandbox has no property with a phone")


def test_questionable_contact_is_contact_data_not_contactable(hunted):
    db, org, s, _ = hunted
    prop, cp = _with_contact(db, org)
    cp.validation = "invalid"                     # a failed validation: -40
    EV.rescore(db, prop, s)
    res = CB.for_evosense_property(db, prop, s)
    assert res["state"] == CB.CONTACT_DATA_FOUND
    elig = EL.check(db, prop, cp, s, mark=False)
    assert not elig["eligible"] and "LOW_CONTACT_CONFIDENCE" in elig["blocks"]


def test_verified_contact_without_consent_is_still_not_sms_contactable(client, auth_headers, hunted):
    db, org, s, user = hunted
    prop, cp = _with_contact(db, org)
    before = prop.contact_confidence
    r = client.post("/wholesale/evosense/properties/%s/contacts/%s/verify" % (prop.id, cp.id),
                    headers=auth_headers, json={"note": "Spoke to the owner by phone"})
    assert r.status_code == 200, r.text
    db.expire_all()
    prop = db.query(EvoSenseProperty).filter_by(id=prop.id).one()
    assert prop.contact_confidence >= min(100, (before or 0) + 1)
    detail = r.json()["contactability"]
    assert detail["state"] == CB.CONTACT_DATA_FOUND
    assert detail["channels"]["sms"]["missing"] == ["NO_SMS_CONSENT"]


# ── two workspaces, the same person and the same house ──────────────────────

def test_identical_person_and_property_in_two_workspaces_share_nothing(client, world):
    db, o = world["db"], world["orgs"]
    for org in (o["evo"], o["other"]):
        st = db.query(WholesaleSettings).filter_by(organization_id=org.id).one()
        st.ai_qualification_enabled = False
    db.commit()
    post(client, form(submission_id="t-a", sms_consent=True))
    post(client, form(submission_id="t-b", sms_consent=True), key=KEY_B)
    a_deal = db.query(WholesaleDeal).filter_by(organization_id=o["evo"].id).one()
    ha = _headers(db, _user(db, o["evo"]))
    hb = _headers(db, _user(db, o["other"]))
    # A's seller says STOP; B's identical seller is untouched.
    client.post("/wholesale/deals/%s/seller-reply" % a_deal.id, headers=ha, json={"message": "STOP"})
    db.expire_all()
    a_lead = db.query(Lead).filter_by(organization_id=o["evo"].id).one()
    b_lead = db.query(Lead).filter_by(organization_id=o["other"].id).one()
    assert a_lead.status == "dnc" and b_lead.status != "dnc"
    assert db.query(SuppressionEntry).filter_by(organization_id=o["other"].id).count() == 0
    assert CB.for_seller(db, o["other"].id, b_lead)["state"] != CB.DO_NOT_CONTACT
    # facts, intelligence and command centers are per workspace
    b_prof = db.query(WholesaleSellerProfile).filter_by(organization_id=o["other"].id).one()
    assert client.get("/wholesale/sellers/%s/intelligence" % b_prof.id, headers=ha).status_code == 404
    a_facts = db.query(WholesaleSellerFact).filter_by(organization_id=o["evo"].id).all()
    assert a_facts and all(f.profile_id != b_prof.id for f in a_facts)
    assert client.get("/wholesale/command-center", headers=hb).json()["needs_you"]["count"] == 0


# ── one seller, start to finish, nothing sent ───────────────────────────────

def test_a_full_seller_journey_sends_nothing(client, world):
    db, o = world["db"], world["orgs"]
    st = db.query(WholesaleSettings).filter_by(organization_id=o["evo"].id).one()
    st.ai_qualification_enabled = False
    db.commit()
    h = _headers(db, _user(db, o["evo"]))
    post(client, form(submission_id="j1", sms_consent=True))
    deal = db.query(WholesaleDeal).one()
    prof = db.query(WholesaleSellerProfile).one()

    def reply(text):
        r = client.post("/wholesale/deals/%s/seller-reply" % deal.id, headers=h, json={"message": text})
        assert r.status_code == 200, r.text
        return r.json()

    assert reply("Can you make me an offer? 175k")["qualification_outcome"]["status"] in (
        "QUALIFIED", "NEEDS_MORE_INFORMATION")
    assert client.get("/wholesale/command-center", headers=h).json()["needs_you"]["count"] >= 1
    assert reply("Actually not right now, maybe next year")["qualification_outcome"]["status"] == "NURTURE"
    post(client, form(submission_id="j2", timeline="asap"))          # they come back
    db.expire_all()
    assert db.query(WholesaleSellerProfile).one().nurture_until is None
    assert reply("STOP")["qualification_outcome"]["status"] == "DISQUALIFIED"
    body = client.get("/wholesale/sellers/%s/intelligence" % prof.id, headers=h).json()
    assert body["contactability"]["state"] == CB.DO_NOT_CONTACT
    assert body["lifecycle"]["stage"] == "DO_NOT_CONTACT"
