"""The first seller conversation, through the REAL reply reader and hand-off
rules, on sandbox records (synthetic owners, 555-01xx numbers). For every
scenario: nothing is sent, the right thing stops, a person is pulled in when
judgment is needed, and only an opt-out puts a number on do-not-contact."""
import pytest

from app.models.evosense_models import (EvoSenseEngagement, EvoSenseFact, EvoSenseHandoff, EvoSenseMessage,
                                        EvoSenseProperty)
from app.models.models import SuppressionEntry
from app.services.evosense import common as C
from app.services.evosense import conversation as CV
from app.services.evosense import first_contact as FC
from app.services.evosense import reply_flags as RF
from app.services.evosense import sandbox_seed as SS

FLAGSHIP = "1418 Cedar Springs Rd"

# (scenario, reply, expected outcome(s), expected hand-off codes, engagement status, opt-out?)
SCENARIOS = [
    ("interested seller", "Yes, I'd consider selling. What would you offer?",
     {C.O_WANTS_OFFER, C.O_INTERESTED}, {"WANTS_OFFER"}, "handed_off", False),
    ("not interested", "No thanks, not selling.", {C.O_HARD_NO}, set(), "stopped", False),
    ("wrong number", "Wrong number.", {C.O_WRONG_PERSON}, set(), "stopped", False),
    ("deceased owner", "The owner is deceased. I'm her son and I handle her things.",
     None, {"ESTATE"}, "handed_off", False),
    ("already sold", "We already sold it last month.", {C.O_ALREADY_SOLD}, set(), "stopped", False),
    ("auction postponed", "The foreclosure sale was postponed, we're working with the bank.",
     None, {"AUCTION_UPDATE"}, "handed_off", False),
    ("wants callback", "Can you call me tomorrow afternoon?", {C.O_CALL_LATER}, {"CALLBACK"}, "handed_off", False),
    ("requests a human", "Is this a bot? I want to talk to a real person.", None, {"HUMAN_REQUESTED"},
     "handed_off", False),
    ("requests no contact", "Stop. Do not contact me again.", {C.O_DNC}, set(), "stopped", True),
    ("angry", "How did you get my number? This is harassment.", None, {"ANGRY"}, "stopped", False),
    ("multiple properties", "Maybe. I have three rental houses I might sell together.",
     None, {"MULTIPLE_PROPERTIES"}, "handed_off", False),
    ("uncertain ownership", "It's my mother's house, I'm not sure if I'm the one who can sell it.",
     None, None, "handed_off", False),
    ("estate representative", "I'm the executor of my late father's estate. We might sell the house.",
     None, {"ESTATE"}, "handed_off", False),
    ("wants appointment", "Sure, you can come look at the house Thursday afternoon.",
     None, {"APPOINTMENT"}, "handed_off", False),
]


@pytest.fixture()
def world(db_session, sample_org, sample_advisor):
    out = SS.seed_review(db_session, sample_org.id, sample_advisor, replies=False)
    out["org_id"] = sample_org.id
    return out


@pytest.mark.parametrize("name,text,outcomes,handoff,status,opt_out", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_scenario(db_session, world, name, text, outcomes, handoff, status, opt_out):
    db, org = db_session, world["org_id"]
    prop = SS.prop_at(db, org, FLAGSHIP)
    eng = SS.engagement_for(db, prop)
    out_before = db.query(EvoSenseMessage).filter(EvoSenseMessage.property_id == prop.id,
                                                  EvoSenseMessage.direction == "outbound").count()
    sup_before = db.query(SuppressionEntry).filter(SuppressionEntry.organization_id == org).count()
    res = CV.receive(db, org, eng, text, delivery="sandbox_simulated")
    db.commit()
    db.refresh(eng)
    if outcomes is not None:
        assert res["outcome"] in outcomes, (name, res)
    assert eng.status == status, (name, eng.status, res)
    h = db.query(EvoSenseHandoff).filter(EvoSenseHandoff.property_id == prop.id,
                                         EvoSenseHandoff.status.in_(("open", "acknowledged"))).first()
    if handoff:
        assert h is not None, name
        assert handoff <= {r["code"] for r in C.jload(h.reasons)}, (name, h.reasons)
    elif handoff is None:
        assert h is not None, name                      # a person reads it, whatever the reason
    else:
        assert h is None, (name, h and h.reasons)
    # nothing is ever sent by reading a reply
    assert db.query(EvoSenseMessage).filter(EvoSenseMessage.property_id == prop.id,
                                            EvoSenseMessage.direction == "outbound").count() == out_before
    sup_after = db.query(SuppressionEntry).filter(SuppressionEntry.organization_id == org).count()
    assert (sup_after > sup_before) == opt_out, name     # do-not-contact only on the owner's own words


def test_a_stop_inside_an_angry_or_flagged_reply_still_wins(db_session, world):
    db, org = db_session, world["org_id"]
    prop = SS.prop_at(db, org, FLAGSHIP)
    eng = SS.engagement_for(db, prop)
    res = CV.receive(db, org, eng, "This is harassment. STOP texting me. I want a real person to explain.",
                     delivery="sandbox_simulated")
    db.commit()
    assert res["outcome"] == C.O_DNC and eng.status == "stopped" and eng.blocked_reason == "opted_out"


def test_seller_statements_never_change_the_clerk_record(db_session, world):
    from app.models.evosense_models import EvoSenseSignal
    db, org = db_session, world["org_id"]
    prop = SS.prop_at(db, org, FLAGSHIP)
    before = sorted((s.signal_type, s.active) for s in db.query(EvoSenseSignal).filter(
        EvoSenseSignal.property_id == prop.id))
    CV.receive(db, org, SS.engagement_for(db, prop), "The auction was cancelled, the loan was reinstated.",
               delivery="sandbox_simulated")
    db.commit()
    after = sorted((s.signal_type, s.active) for s in db.query(EvoSenseSignal).filter(
        EvoSenseSignal.property_id == prop.id))
    assert before == after
    f = db.query(EvoSenseFact).filter(EvoSenseFact.property_id == prop.id,
                                      EvoSenseFact.fact_type == "auction_status").one()
    assert f.truth_state == C.T_SELLER_STATED and "cancelled" in f.quote


def test_reply_flags_are_specific():
    assert [f["code"] for f in RF.read("He passed on it, not interested")] == []
    assert [f["code"] for f in RF.read("she passed away in May")] == ["DECEASED_OWNER"]
    assert [f["code"] for f in RF.read("are you a bot")] == ["HUMAN_REQUESTED"]
    assert RF.read("We have a few other properties") and RF.read("I own several houses")
    assert RF.read("Nice day") == []


# ── first-contact wording ───────────────────────────────────────────────────

def test_every_shipped_first_contact_text_passes_the_guard():
    w = FC.approved_wording({"business": "Example Home Buyers", "address": "1 Test St", "owner_first": "Pat",
                             "caller": "Sam"})
    texts = w["call_track"] + [w["sms_opener"], w["permission_link"], w["email"]["subject"], w["email"]["body"],
                               w["ai_disclosure"]]
    for t in texts:
        assert FC.guard(t)["ok"], (t, FC.guard(t))
    assert "Example Home Buyers" in w["call_track"][0] and "1 Test St" in w["call_track"][1]
    assert "automated assistant" in w["ai_disclosure"]


@pytest.mark.parametrize("bad,code", [
    ("Hi, I'm a real person, not a bot!", "pretends_human"),
    ("We guarantee a cash offer of $150,000 today", "promises_money"),
    ("We can stop the foreclosure for you", "stops_foreclosure"),
    ("You should file bankruptcy right away", "legal_advice"),
    ("Act now - last chance before the sale", "false_urgency"),
    ("If you wait, the bank will take your house and you'll regret it", "pressure"),
    ("Our records show you owe the bank and you are being foreclosed", "claims_records"),
])
def test_the_guard_refuses_what_a_first_message_must_never_say(bad, code):
    g = FC.guard(bad)
    assert not g["ok"] and code in {p["code"] for p in g["problems"]}


def test_first_contact_plan_and_readiness_endpoints(client, auth_headers, db_session, world):
    prop = SS.prop_at(db_session, world["org_id"], FLAGSHIP)
    r = client.get("/wholesale/evosense/properties/%s/first-contact" % prop.id, headers=auth_headers)
    if r.status_code == 404:
        pytest.skip("auth fixture belongs to a different organization")
    j = r.json()
    assert {c["channel"] for c in j["channels"]} == {"call", "sms"}
    assert "not permission to text" in j["notice"] and j["wording"]["call_rules"]
    chk = client.post("/wholesale/evosense/first-contact/check", headers=auth_headers,
                      json={"text": "We can stop your foreclosure"}).json()
    assert chk["ok"] is False
    rd = client.get("/wholesale/evosense/automation/readiness", headers=auth_headers).json()
    assert "blockers" in rd and rd["how"]["pause"] and "channels" in rd and rd["test_mode_available"]


def test_a_real_owner_without_consent_is_never_texted(db_session, sample_org, sample_advisor):
    from app.services.evosense import contacts as CT
    from app.services.evosense import eligibility as EL
    from app.services.evosense import hunt as HU
    from app.services.evosense import providers as PV
    from app.services import wholesale_enrichment as WE
    db, org = db_session, sample_org.id
    SS.enable_sandbox(db, org)
    s = SS.create_strategy(db, org, sample_advisor, dict(SS.DFW, name="Real consent check"))
    s.is_test = False
    r = HU.add_manual(db, org, {"street_address": "901 Consent St", "city": "Dallas", "state": "TX",
                                "zip_code": "75201", "county": "Dallas", "property_type": "single_family",
                                "owner_name": "Pat Q Consent", "signals": ["PRE_FORECLOSURE"]},
                      user=sample_advisor, strategy=s)
    prop = db.query(EvoSenseProperty).get(r["property_id"])
    CT.apply_result(db, CT.primary_owner(db, prop),
                    WE.EnrichmentResult(status="succeeded", provider="manual", owner_name="Pat Q Consent",
                                        phones=[WE.EnrichmentPhone(number="2145550188")]),
                    PV.PROVIDERS["manual"])
    cp, _ = CT.best_contact(db, prop)
    assert EL.check(db, prop, cp, s, mark=False)["eligible"] is False
    plan = FC.plan(db, org, prop)
    assert next(c for c in plan["channels"] if c["channel"] == "sms")["permitted"] is False


def test_enrichment_queue_orders_by_priority_and_buys_nothing(client, auth_headers, db_session, sample_org):
    from app.models.evosense_models import EvoSenseCostEntry
    from app.services.evosense import automation as AU
    from tests.test_evosense_opportunity import _prop
    db, org = db_session, sample_org.id
    lo = _prop(db, org, "20 Low Priority St")
    hi = _prop(db, org, "21 High Priority St")
    lo.next_best_action = hi.next_best_action = "find_contact"
    lo.priority_score, hi.priority_score = 30, 80
    db.commit()
    q = AU.enrichment_queue(db, org)
    assert q["needing_contact"] == 2 and [r["address"] for r in q["rows"]] == ["21 High Priority St",
                                                                              "20 Low Priority St"]
    assert any(p["key"] == "propstream" for p in q["providers"] if "key" in p)
    assert db.query(EvoSenseCostEntry).filter(EvoSenseCostEntry.organization_id == org).count() == 0
    r = client.get("/wholesale/evosense/enrichment/queue", headers=auth_headers)
    assert r.status_code == 200 and "Nothing is bought" in r.json()["notice"]


def test_qualification_comes_only_from_the_sellers_words(db_session, world):
    from app.services.evosense import qualification as Q
    db, org = db_session, world["org_id"]
    prop = SS.prop_at(db, org, FLAGSHIP)
    before = Q.for_property(db, prop)
    assert before["known"] == 0                                   # distress is never read as motivation
    CV.receive(db, org, SS.engagement_for(db, prop),
               "Yes I'd sell. The roof is leaking and nobody lives there. I already have an offer from "
               "another investor.", delivery="sandbox_simulated")
    db.commit()
    q = {r["key"]: r["answer"] for r in Q.for_property(db, prop)["questions"]}
    assert q["open_to_selling"]["who"] == "seller said"
    assert q["repairs"] and "roof" in q["repairs"]["quote"].lower()
    assert q["existing_offer"] and q["existing_offer"]["who"] == "seller said"
    assert q["timeline"] is None and q["authority"] is None       # not said, so not known
