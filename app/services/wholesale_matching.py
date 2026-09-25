"""Buyer matching — score a deal against a buy box, and show the working.

"Return a ranked match score. Show WHY the buyer matched. Do not make the score
a black box."

HOW THE SCORE IS BUILT
----------------------
Every dimension carries a weight. A dimension the buy box does not constrain is
NOT counted for or against — it is dropped from the denominator entirely, so a
buyer who told us only their counties and their price range is scored on
counties and price, not punished for the eight things they never said. The
alternative (treating silence as a miss) makes a sparse buy box score badly,
which would push people to invent constraints to make the number go up.

The score is therefore: points earned / points available, as a percentage, over
the dimensions the buy box actually constrains.

DISQUALIFIERS ARE NOT A LOW SCORE. Some things are a hard no — the buyer is
inactive, opted out, or the deal is outside a stated price range or geography.
Those set `disqualified` and the match is returned with its reason rather than
being silently dropped, because "why is this buyer not on the list" is a
question the screen has to be able to answer.

PURE FUNCTIONS. Model rows go in, dictionaries come out. No session, so the
scoring is testable on plain objects — which is the only way anybody is going to
trust a number that decides who gets called about a deal.
"""

import json
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from app.services import wholesale_geo as geo_module
from app.services.wholesale_analysis import money

# Weights. Geography and price dominate because they are the two things a cash
# buyer is actually strict about; the rest refine the ranking.
WEIGHTS = {
    "geography": 30,
    "price": 25,
    "property_type": 12,
    "strategy": 10,
    "beds": 6,
    "baths": 4,
    "sqft": 6,
    "year_built": 4,
    "rehab": 8,
    "spread": 10,
}

# How much work a buyer will take on, as an ordered scale. A buyer who tolerates
# a full gut also tolerates light work; the reverse is not true, which is why
# this is an ordering rather than an equality test.
REHAB_SCALE = {"light": 1, "moderate": 2, "heavy": 3, "full_gut": 4}

# Repair cost as a share of ARV, mapped to the rehab level it implies. Used only
# when both numbers exist; a deal with no repair estimate is not assigned a
# rehab level at all.
def _implied_rehab(arv: Optional[Decimal], repairs: Optional[Decimal]) -> Optional[str]:
    if not arv or arv <= 0 or repairs is None:
        return None
    share = repairs / arv
    if share < Decimal("0.05"):
        return "light"
    if share < Decimal("0.15"):
        return "moderate"
    if share < Decimal("0.30"):
        return "heavy"
    return "full_gut"


def _list(raw: Any) -> List[str]:
    """A JSON array column as a lowercased list. Empty means 'no restriction'."""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        items = raw
    else:
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(items, list):
        return []
    return [str(i).strip().lower() for i in items if str(i).strip()]


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _label(value: Any) -> str:
    """A stored enumeration, written the way a person reads it.

    Matching is done on the normalized value; only the SENTENCE explaining the
    match uses this. Nothing is looked up or mapped, so a value this code has
    never seen still comes out readable rather than as `single_family`.
    """
    text = str(value or "").replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else ""


def score_buy_box(deal: Any, prop: Any, box: Any,
                  asking_price: Any = None) -> Dict[str, Any]:
    """Score one deal against one buy box.

    `asking_price` is what we would ask THIS buyer to pay — the disposition
    price. It defaults to the deal's `buyer_price`, then to contract price, then
    to the proposed offer, because early in a deal the only number that exists
    is what we offered the seller and a match computed from nothing is worse
    than a match computed from an approximation that says so.

    Returns {"score", "factors", "disqualified", "disqualified_reason"}.
    """
    factors: List[Dict[str, Any]] = []
    earned = 0
    available = 0

    def add(dimension: str, matched: Optional[bool], detail: str,
            weight: Optional[int] = None, partial: float = 1.0):
        """Record a dimension. `matched=None` means the box does not constrain
        it, so it contributes to neither side of the fraction."""
        nonlocal earned, available
        w = WEIGHTS[dimension] if weight is None else weight
        if matched is None:
            factors.append({"dimension": dimension, "matched": None,
                            "weight": 0, "detail": detail})
            return
        available += w
        got = int(round(w * partial)) if matched else 0
        earned += got
        factors.append({"dimension": dimension, "matched": bool(matched),
                        "weight": w, "earned": got, "detail": detail})

    price = money(asking_price)
    if price is None:
        price = (money(getattr(deal, "buyer_price", None))
                 or money(getattr(deal, "contract_price", None))
                 or money(getattr(deal, "proposed_offer", None)))

    # ── Geography ───────────────────────────────────────────────────────────
    #
    # ONE DIMENSION, FIVE FIELDS, ANSWERED BY app/services/wholesale_geo.py.
    #
    # Scoring the five separately would let a buyer who named a city and nothing
    # else lose four fifths of the geography weight for being specific, so they
    # stay one dimension. What CHANGED in Phase 2 is everything underneath:
    #
    #   * spelling is normalized — "Dallas County, TX" matches a county column
    #     of "Dallas", "76107-1234" matches "76107", "Texas" matches "TX",
    #     "Ft. Worth" matches "Fort Worth". Phase 1 compared lowercased strings
    #     and therefore matched almost nothing a real person typed.
    #   * a match on ANY constrained field is a match. Phase 1 answered only on
    #     the most specific field, which silently discarded every other line the
    #     buyer had entered — a box listing twelve ZIPs AND "TX" lost the state.
    #   * DISQUALIFICATION IS STRICTER THAN A MISS. The property must be
    #     positively outside every constrained field with none unknown. One
    #     blank county column makes the answer "we cannot say", which scores as
    #     a miss and never excludes the buyer from the list.
    #
    # The module normalizes SPELLING and refuses to invent GEOGRAPHY: it holds
    # no table claiming the city Dallas sits in Dallas County, so it never says
    # so. See `wholesale_geo.resolve_containment` for where real data plugs in.
    geo = geo_module.geography_verdict({
        "zips": _list(getattr(box, "zips", None)),
        "cities": _list(getattr(box, "cities", None)),
        "counties": _list(getattr(box, "counties", None)),
        "markets": _list(getattr(box, "markets", None)),
        "states": _list(getattr(box, "states", None)),
    }, prop)
    if geo["verdict"] == "unconstrained":
        add("geography", None, geo["detail"])
    else:
        add("geography", geo["verdict"] == "match", geo["detail"])
    geo_disqualified = geo["verdict"] == "outside"

    # ── Price ───────────────────────────────────────────────────────────────
    min_price = money(getattr(box, "min_price", None))
    max_price = money(getattr(box, "max_price", None))
    price_disqualified = False
    if min_price is None and max_price is None:
        add("price", None, "This buy box names no price range.")
    elif price is None:
        add("price", False, "The buy box has a price range but this deal has no "
                            "price yet, so it cannot be checked.")
    else:
        below = min_price is not None and price < min_price
        above = max_price is not None and price > max_price
        if not below and not above:
            add("price", True, "%s is inside the buy box range." % float(price))
        else:
            add("price", False, "%s is %s the buy box range."
                % (float(price), "below" if below else "above"))
            price_disqualified = True

    # ── Property type ───────────────────────────────────────────────────────
    types = _list(getattr(box, "property_types", None))
    ptype = _norm(getattr(prop, "property_type", None))
    if not types:
        add("property_type", None, "This buy box names no property types.")
    elif not ptype:
        add("property_type", False, "The property has no type on file.")
    else:
        # The old sentence dropped the verb on a match ("Property type
        # single_family in the buy box") and printed the stored key.
        add("property_type", ptype in types,
            "Property type %s is%s in the buy box."
            % (_label(ptype), "" if ptype in types else " not"))

    # ── Strategy ────────────────────────────────────────────────────────────
    strategies = _list(getattr(box, "strategies", None))
    if not strategies:
        add("strategy", None, "This buy box names no strategy.")
    else:
        add("strategy", True, "Buyer strategy: %s. Recorded for context — a deal "
            "does not carry a strategy of its own."
            % ", ".join(_label(s) for s in strategies),
            weight=WEIGHTS["strategy"], partial=1.0)

    # ── Size and age ────────────────────────────────────────────────────────
    _range_factor(add, "beds", getattr(box, "min_beds", None), getattr(box, "max_beds", None),
                  getattr(prop, "bedrooms", None), "bedrooms")
    _range_factor(add, "baths", getattr(box, "min_baths", None), None,
                  getattr(prop, "bathrooms", None), "bathrooms")
    _range_factor(add, "sqft", getattr(box, "min_sqft", None), getattr(box, "max_sqft", None),
                  getattr(prop, "square_feet", None), "square feet")
    _range_factor(add, "year_built", getattr(box, "min_year_built", None),
                  getattr(box, "max_year_built", None),
                  getattr(prop, "year_built", None), "year built")

    # ── Rehab tolerance ─────────────────────────────────────────────────────
    tolerance = _norm(getattr(box, "rehab_tolerance", None))
    implied = _implied_rehab(money(getattr(deal, "arv", None)),
                             money(getattr(deal, "repair_estimate", None)))
    if not tolerance:
        add("rehab", None, "This buy box names no rehab tolerance.")
    elif implied is None:
        add("rehab", False, "The buy box tolerates %s work, but this deal has no "
            "ARV and repair estimate to imply a rehab level." % tolerance)
    else:
        ok = REHAB_SCALE.get(implied, 99) <= REHAB_SCALE.get(tolerance, 0)
        add("rehab", ok, "Repairs imply %s work; the buyer tolerates up to %s."
            % (implied, tolerance))

    # ── Spread the buyer would be left with ─────────────────────────────────
    min_spread = money(getattr(box, "min_spread", None))
    if min_spread is None:
        add("spread", None, "This buy box names no minimum spread.")
    else:
        arv = money(getattr(deal, "arv", None))
        repairs = money(getattr(deal, "repair_estimate", None)) or Decimal(0)
        if arv is None or price is None:
            add("spread", False, "The buy box requires a %s spread, but this deal "
                "has no ARV and price to measure one." % float(min_spread))
        else:
            spread = arv - price - repairs
            add("spread", spread >= min_spread,
                "Buyer spread at this price is %s against a %s minimum."
                % (float(spread), float(min_spread)))

    score = int(round(100 * earned / available)) if available else 0

    disqualified_reason = None
    if geo_disqualified:
        disqualified_reason = "The property is outside this buyer's stated geography."
    elif price_disqualified:
        disqualified_reason = "The price is outside this buyer's stated range."

    return {"score": score, "factors": factors,
            "disqualified": disqualified_reason is not None,
            "disqualified_reason": disqualified_reason,
            "points_earned": earned, "points_available": available}


def _range_factor(add, dimension: str, minimum: Any, maximum: Any,
                  actual: Any, label: str):
    """A numeric min/max dimension, scored the same way everywhere."""
    lo = money(minimum)
    hi = money(maximum)
    val = money(actual)
    if lo is None and hi is None:
        add(dimension, None, "This buy box names no %s range." % label)
        return
    if val is None:
        add(dimension, False, "The buy box constrains %s but the property has none "
            "on file." % label)
        return
    below = lo is not None and val < lo
    above = hi is not None and val > hi
    if not below and not above:
        add(dimension, True, "%s %s is inside the buy box range." % (label.title(), val))
    else:
        add(dimension, False, "%s %s is %s the buy box range."
            % (label.title(), val, "below" if below else "above"))


def match_deal_to_buyers(deal: Any, prop: Any, buyers: Sequence[Any],
                         asking_price: Any = None,
                         include_disqualified: bool = True) -> List[Dict[str, Any]]:
    """Score a deal against every buyer, best box per buyer, ranked.

    A buyer with several buy boxes is scored against each and keeps their BEST —
    an investor who buys rentals in Dallas and flips in Fort Worth should match
    a Fort Worth flip on the Fort Worth box, not be averaged into mediocrity.

    A buyer with NO buy box at all is returned with score 0 and a factor saying
    so, not silently dropped: "this buyer has never told us what they want" is
    useful information and the screen should show it.

    Inactive and opted-out buyers are excluded entirely — not scored, not
    returned. They are not a ranking question.
    """
    results: List[Dict[str, Any]] = []
    for buyer in buyers:
        if not getattr(buyer, "is_active", True):
            continue
        if getattr(buyer, "do_not_contact", False):
            continue
        boxes = [b for b in (getattr(buyer, "buy_boxes", None) or [])
                 if getattr(b, "is_active", True)]
        if not boxes:
            results.append({
                "buyer": buyer, "buy_box": None, "score": 0,
                "factors": [{"dimension": "buy_box", "matched": None, "weight": 0,
                             "detail": "This buyer has no active buy box on file, so "
                                       "there is nothing to match against. Add one to "
                                       "include them in ranked matching."}],
                "disqualified": False, "disqualified_reason": None,
            })
            continue
        best = None
        for box in boxes:
            scored = score_buy_box(deal, prop, box, asking_price)
            if best is None or scored["score"] > best[1]["score"]:
                best = (box, scored)
        box, scored = best
        if scored["disqualified"] and not include_disqualified:
            continue
        results.append({
            "buyer": buyer, "buy_box": box, "score": scored["score"],
            "factors": scored["factors"], "disqualified": scored["disqualified"],
            "disqualified_reason": scored["disqualified_reason"],
        })

    results.sort(key=lambda r: (not r["disqualified"], r["score"]), reverse=True)
    return results
