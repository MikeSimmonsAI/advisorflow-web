"""Cash buyer intelligence: tiers come from evidence, history adjusts within a cap,
and a typed 'past deals' number never counts."""
from datetime import date, timedelta
from types import SimpleNamespace as NS

from app.services import wholesale_buyer_intel as BI


def buyer(**kw):
    base = dict(source=None, source_detail=None, proof_of_funds_on_file=False, proof_of_funds_expires=None,
                cash_verified=False, past_deals_count=0, phone=None, email=None)
    base.update(kw)
    return NS(**base)


def test_claimed_deals_are_not_evidence():
    t = BI.tier(buyer(past_deals_count=40, email="x@example.test"), {})
    assert t["tier"] == BI.UNVERIFIED and "not counted" in t["claimed_note"]


def test_county_deeds_alone_are_potential_not_verified():
    b = buyer(source="county_roll:dallas",
              source_detail="Dallas County records: owns 7 houses; 3 deed(s) to them in the last 24 months")
    t = BI.tier(b, {})
    assert t["tier"] == BI.POTENTIAL and "3 deeds" in t["buys"][0]
    assert any("No phone or email" in m for m in t["missing"])


def test_offer_plus_reply_is_verified_active():
    t = BI.tier(buyer(email="b@example.test"), {"offers_made": 1, "responded": 1})
    assert t["tier"] == BI.VERIFIED and t["buys"] and t["contact"]


def test_expired_proof_of_funds_does_not_verify_contact():
    old = buyer(proof_of_funds_on_file=True, proof_of_funds_expires=date.today() - timedelta(days=1))
    assert BI.tier(old, {"deals_closed": 1})["tier"] == BI.POTENTIAL
    cur = buyer(proof_of_funds_on_file=True, proof_of_funds_expires=date.today() + timedelta(days=30))
    assert BI.tier(cur, {"deals_closed": 1})["tier"] == BI.VERIFIED


def test_history_learns_from_offers_and_passes_and_is_capped():
    rows = [{"zip": "75216", "property_type": "single_family", "price": 120000, "action": "offer"},
            {"zip": "75216", "property_type": "single_family", "price": 135000, "action": "closed"},
            {"zip": "75228", "property_type": "single_family", "price": 150000, "action": "passed"}]
    rev = BI.revealed(rows)
    assert rev["zips"] == {"75216": 2} and rev["passed_zips"] == {"75228": 1}
    good = BI.history_fit(rev, "75216", "single_family", 128000)
    assert good["points"] == BI.HISTORY_CAP            # 8 + 4 + 5 = 17, capped at 15
    bad = BI.history_fit(rev, "75228", "single_family", 400000)
    assert bad["points"] < 0 and any("Passed" in x["why"] for x in bad["lines"])
    none = BI.history_fit(BI.revealed([]), "75216", "single_family", 1)
    assert none["points"] == 0 and "nothing learned" in none["lines"][0]["why"]


def test_priority_explains_every_point_and_disqualified_is_zero():
    t = BI.tier(buyer(email="b@example.test"), {"offers_made": 1, "responded": 1})
    h = {"points": 5, "lines": [{"points": 5, "why": "x"}]}
    p = BI.priority(70, False, t, h)
    assert p["score"] == 85 and sum(x["points"] for x in p["explain"]) == 85
    assert BI.priority(70, True, t, h)["score"] == 0
