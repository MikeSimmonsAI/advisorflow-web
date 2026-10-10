"""Dependency-free proof of Wholesale buyer matching (SYNTHETIC LOGIC + STATIC SOURCE).

Runs the REAL app/services/wholesale_matching.py (pure; stdlib only) on synthetic
objects, and statically checks the org-isolation predicates of the caller.
No DB, no network. Run: python3 scripts/wholesale_buyer_matching_proof.py
"""
import os
import re
import sys
from types import SimpleNamespace as NS

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from app.services import wholesale_matching as M  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", name)


def deal(**kw):
    d = dict(buyer_price=150000, contract_price=None, proposed_offer=None,
             arv=250000, repair_estimate=20000)
    d.update(kw)
    return NS(**d)


def prop(**kw):
    p = dict(state="TX", county="Dallas", city="Dallas", zip_code="75201",
             property_type="single_family", bedrooms=3, bathrooms=2,
             square_feet=1500, year_built=1990)
    p.update(kw)
    return NS(**p)


def box(**kw):
    b = dict(id="box", is_active=True, zips=None, cities=None, counties=None,
             markets=None, states=None, property_types=None, strategies=None,
             min_price=None, max_price=None, min_beds=None, max_beds=None,
             min_baths=None, min_sqft=None, max_sqft=None, min_year_built=None,
             max_year_built=None, rehab_tolerance=None, min_spread=None)
    b.update(kw)
    return NS(**b)


def buyer(bid, boxes, name=None, **kw):
    d = dict(id=bid, company_name=name or bid, contact_name=None, is_active=True,
             do_not_contact=False, buy_boxes=boxes)
    d.update(kw)
    return NS(**d)


def dim(res, name):
    return [f for f in res["factors"] if f["dimension"] == name][0]


# ---- eligibility: inactive / opted-out never ranked -----------------------
r = M.match_deal_to_buyers(deal(), prop(), [
    buyer("a", [box(states=["tx"])], is_active=False),
    buyer("b", [box(states=["tx"])], do_not_contact=True),
    buyer("c", [box(states=["tx"])]),
    buyer("d", [box(states=["tx"], is_active=False)])])
check("inactive/opted-out buyers excluded", [x["buyer"].id for x in r] == ["c", "d"])
check("buyer whose only box is inactive is returned unranked with reason",
      r[1]["score"] == 0 and r[1]["buy_box"] is None and "no active buy box" in r[1]["factors"][0]["detail"])

# ---- insufficient data ----------------------------------------------------
empty = M.score_buy_box(deal(), prop(), box())
check("empty box: score 0, not disqualified", empty["score"] == 0 and not empty["disqualified"])
check("empty box says it is insufficient data",
      any(f["dimension"] == "buy_box" and "not enough information" in f["detail"]
          for f in empty["factors"]))
strat_only = M.score_buy_box(deal(), prop(), box(strategies=["flip"]))
check("strategy-only box no longer scores 100 for every deal", strat_only["score"] == 0)
check("strategy is reported but unscored", dim(strat_only, "strategy")["matched"] is None)
check("strategy-only box flagged insufficient",
      any(f["dimension"] == "buy_box" for f in strat_only["factors"]))
sparse = M.score_buy_box(deal(), prop(), box(states=["tx"]))
check("sparse box not punished (100)", sparse["score"] == 100)
check("strategy adds nothing to a real box",
      M.score_buy_box(deal(), prop(), box(states=["tx"], strategies=["flip"]))["score"] == 100)
miss = M.score_buy_box(deal(), prop(), box(min_beds=5))
check("constrained dimension with property missing data is a miss",
      M.score_buy_box(deal(), prop(bedrooms=None), box(min_beds=2))["score"] == 0)
check("no price on deal + price range = miss, not disqualified",
      not M.score_buy_box(deal(buyer_price=None, arv=None), prop(),
                          box(min_price=1, max_price=2))["disqualified"])

# ---- disqualification vs low score ---------------------------------------
over = M.score_buy_box(deal(buyer_price=900000), prop(), box(max_price=200000))
check("price above range disqualifies with a reason",
      over["disqualified"] and "price" in over["disqualified_reason"])
outside = M.score_buy_box(deal(), prop(), box(states=["ok"]))
check("outside stated geography disqualifies",
      outside["disqualified"] and "geography" in outside["disqualified_reason"])
check("beds miss lowers score but never disqualifies",
      not miss["disqualified"] and miss["score"] == 0)

# ---- determinism and explainability --------------------------------------
buyers = [buyer("b3", [box(states=["tx"])], name="Zed"),
          buyer("b1", [box(states=["tx"])], name="Alpha"),
          buyer("b2", [box(states=["tx"])], name="Alpha"),
          buyer("b0", [box(states=["ok"])], name="Aaa"),
          buyer("b9", [box(states=["tx"], min_price=100000, max_price=200000, min_beds=9)], name="Mid")]
orders = set()
import itertools  # noqa: E402
for perm in itertools.permutations(buyers):
    res = M.match_deal_to_buyers(deal(), prop(), list(perm))
    orders.add(tuple(x["buyer"].id for x in res))
check("ranking independent of input order (%d permutations)" % 120, len(orders) == 1)
order = list(orders)[0]
check("ties break by name then id", order[:3] == ("b1", "b2", "b3"))
check("qualified before disqualified, score descending",
      order == ("b1", "b2", "b3", "b9", "b0"))
res = M.match_deal_to_buyers(deal(), prop(), buyers)
check("every scored row has factors with a human detail",
      all(f["detail"] for x in res for f in x["factors"]))
check("detail text never prints a raw stored enum key",
      not any("single_family" in f["detail"] for x in res for f in x["factors"]))
check("include_disqualified=False drops them",
      "b0" not in [x["buyer"].id for x in M.match_deal_to_buyers(
          deal(), prop(), buyers, include_disqualified=False)])
best = M.match_deal_to_buyers(deal(), prop(), [buyer("m", [box(states=["ok"]), box(states=["tx"])])])
check("multi-box buyer keeps best box", best[0]["score"] == 100 and not best[0]["disqualified"])

# ---- standing: claimed past deals are never counted ----------------------
check("claimed deals never make a buyer proven",
      M.buyer_standing({}, NS(past_deals_count=50))["standing"] == "new")
check("closed deal is proven",
      M.buyer_standing({"deals_closed": 1, "sheets_sent": 1})["standing"] == "proven")
check("3 unanswered sheets = unresponsive",
      M.buyer_standing({"sheets_sent": 3})["standing"] == "unresponsive")
check("no activity = new", M.buyer_standing(None)["standing"] == "new")
check("offer without close = active (not proven)",
      M.buyer_standing({"offers_made": 1, "sheets_sent": 2})["standing"] == "active")
check("response rate None when nothing sent", M.buyer_standing({})["response_rate"] is None)

# ---- org isolation, statically -------------------------------------------
svc = open(os.path.join(ROOT, "app/services/wholesale_service.py"), encoding="utf-8").read()
body = svc[svc.index("def recompute_matches"):]
body = body[:body.index("\ndef ", 10)]
check("recompute_matches scopes buyers by org", "WholesaleBuyer.organization_id == org_id" in body)
check("recompute_matches scopes property by org", "WholesaleProperty.organization_id == org_id" in body)
check("recompute_matches scopes existing matches by org",
      "WholesaleBuyerMatch.organization_id == org_id" in body)
check("recompute_matches separates sandbox from real",
      "bool(b.is_test) == bool(deal.is_test)" in body)
check("recompute_matches only considers active buyers", "is_active.is_(True)" in body)

print("buyer matching proof: %d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
