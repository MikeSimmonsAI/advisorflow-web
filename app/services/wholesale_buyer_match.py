"""ONE universal property -> buyer matcher, and buyer demand -> hunt suggestions.

From ANY acquisition property - a foreclosure or other EvoSense discovery, a
Wholesale property, a deal; single-family, multifamily, land, commercial or a
category added later - the same engine (wholesale_matching v2) scores every
buyer's buy boxes. No category-specific matcher exists or is needed: the
property answers with its category/subtype/facts and the buy box with its
criteria.

Results show the THREE measurements side by side - match score, verification,
readiness - and never fold one into another. A buyer inside the ZIP and budget
is a MATCH, not a ready buyer.

Nothing here sends anything. Saving a match writes the existing
wholesale_buyer_matches row (for a deal). "Prepare for disposition" promotes a
discovery to a deal through the existing promotion path; sending still goes
through the existing disposition desk with its approvals.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.services import wholesale_buyer_engine as ENG
from app.services import wholesale_matching as M

SUBJECT_TYPES = ("evosense", "property", "deal")


def _facts(raw) -> Dict[str, Any]:
    try:
        d = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
    except (TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _fact_value(facts: Dict[str, Any], key: str):
    e = facts.get(key)
    return e.get("value") if isinstance(e, dict) else e


def subject(db, org_id: str, kind: str, sid: str) -> Dict[str, Any]:
    """(deal_like, prop_like, label, meta) for any acquisition record, org-scoped."""
    from app.models.evosense_models import EvoSenseProperty
    from app.models.wholesale_models import WholesaleDeal, WholesaleProperty
    if kind not in SUBJECT_TYPES:
        raise HTTPException(status_code=400, detail="subject_type must be one of %s" % ", ".join(SUBJECT_TYPES))
    deal = None
    if kind == "deal":
        deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == sid, WholesaleDeal.organization_id == org_id).first()
        if deal is None:
            raise HTTPException(status_code=404, detail="Deal not found")
        prop = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id,
                                                  WholesaleProperty.organization_id == org_id).first()
        evo = None
    elif kind == "property":
        prop = db.query(WholesaleProperty).filter(WholesaleProperty.id == sid,
                                                  WholesaleProperty.organization_id == org_id).first()
        if prop is None:
            raise HTTPException(status_code=404, detail="Property not found")
        deal = db.query(WholesaleDeal).filter(WholesaleDeal.property_id == prop.id,
                                              WholesaleDeal.organization_id == org_id).order_by(
            WholesaleDeal.created_at.desc()).first()
        evo = None
    else:
        evo = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == sid,
                                                EvoSenseProperty.organization_id == org_id).first()
        if evo is None:
            raise HTTPException(status_code=404, detail="Property not found")
        if evo.promoted_deal_id:
            deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == evo.promoted_deal_id,
                                                  WholesaleDeal.organization_id == org_id).first()
        f = _facts(evo.type_facts)
        prop = SimpleNamespace(
            street_address=evo.street_address, city=evo.city, state=evo.state, zip_code=evo.zip_code,
            county=evo.county, property_type=evo.property_type, property_category=evo.property_category,
            property_subtype=evo.property_subtype, bedrooms=evo.bedrooms, bathrooms=evo.bathrooms,
            square_feet=evo.square_feet, year_built=evo.year_built,
            unit_count=_fact_value(f, "unit_count"), acreage=_fact_value(f, "acreage") or _fact_value(f, "lot_acres"),
            building_sqft=_fact_value(f, "building_sqft"), type_facts=evo.type_facts, is_test=evo.is_test)
    label = ", ".join(x for x in (getattr(prop, "street_address", None), getattr(prop, "city", None),
                                  getattr(prop, "zip_code", None)) if x)
    return {"deal": deal, "prop": prop, "evo": evo, "label": label,
            "is_test": bool(getattr(deal, "is_test", False) or getattr(prop, "is_test", False)),
            "category": getattr(prop, "property_category", None) or getattr(prop, "property_type", None)}


def _deal_like(s: Dict[str, Any], asking_price: Optional[float]):
    d = s["deal"]
    if d is not None:
        return d
    return SimpleNamespace(buyer_price=asking_price, contract_price=None, proposed_offer=None, arv=None,
                           repair_estimate=None)


def match(db, org_id: str, kind: str, sid: str, *, asking_price: Optional[float] = None,
          include_disqualified: bool = True, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    s = subject(db, org_id, kind, sid)
    deal_like = _deal_like(s, asking_price)
    price_note = None
    if s["deal"] is None:
        price_note = ("Asking price to buyers: $%s (entered)" % format(int(asking_price), ",")) if asking_price \
            else "No asking price yet - price fit is listed as missing, not counted against anyone."
    buyers, boxes, acqs, ents, convs, matches, activity = ENG.load(db, org_id, include_test=s["is_test"])
    # a sandbox property matches sandbox buyers and a real one real buyers
    buyers = [b for b in buyers if bool(b.is_test) == s["is_test"]]
    ranked = M.match_deal_to_buyers(deal_like, s["prop"], buyers, asking_price=asking_price,
                                    include_disqualified=include_disqualified)
    saved = {}
    if s["deal"] is not None:
        from app.models.wholesale_models import WholesaleBuyerMatch
        saved = {m.buyer_id: m for m in db.query(WholesaleBuyerMatch).filter(
            WholesaleBuyerMatch.organization_id == org_id, WholesaleBuyerMatch.deal_id == s["deal"].id).all()}
    items = []
    for r in ranked:
        b = r["buyer"]
        a = ENG.assess(b, boxes.get(b.id, []), acqs.get(b.id, []), ents.get(b.id, []), activity.get(b.id), today)
        box = r["buy_box"]
        scored = M.score_buy_box(deal_like, s["prop"], box, asking_price) if box is not None else None
        items.append({
            "buyer_id": b.id, "name": ENG._name(b), "is_test": bool(b.is_test),
            "match_score": r["score"], "disqualified": r["disqualified"],
            "disqualified_reason": r["disqualified_reason"],
            "buy_box": {"id": getattr(box, "id", None), "label": getattr(box, "label", None),
                        "source": getattr(box, "source", None)} if box is not None else None,
            "factors": r["factors"], "missing": (scored or {}).get("missing", []),
            "checked": (scored or {}).get("checked", 0),
            "verification": {"badges": a["badges"], "headline": a["headline_label"],
                             "contact": a["contact"]["label"], "buy_box": a["buy_box"]["label"]},
            "readiness": {"level": a["readiness"]["level"], "label": a["readiness"]["label"],
                          "missing": a["readiness"]["missing"][:3]},
            "purchase_history": {"label": a["purchases"]["label"], "recent": a["purchases"]["recent"],
                                 "lines": a["purchases"]["lines"][:3]},
            "next_action": _next(b, r, a),
            "saved": bool(saved.get(b.id)), "has_phone": bool(b.phone), "has_email": bool(b.email),
        })
    items.sort(key=lambda x: (x["disqualified"], -x["match_score"],
                              ENG.READY_ORDER.get(x["readiness"]["level"], 9), x["name"].lower()))
    return {"subject": {"type": kind, "id": sid, "label": s["label"], "category": s["category"],
                        "deal_id": getattr(s["deal"], "id", None), "is_test": s["is_test"],
                        "can_save": s["deal"] is not None, "evosense_id": getattr(s["evo"], "id", None)},
            "price_note": price_note, "version": M.VERSION, "count": len(items),
            "eligible": sum(1 for i in items if not i["disqualified"]), "buyers": items,
            "rules": "Match score = how well this property fits what the buyer stated or showed they buy. It is "
                     "not verification and not readiness: those are shown separately. Unknown facts are listed "
                     "as missing, never counted as a miss."}


def _next(b, r, a) -> Dict[str, str]:
    if r["disqualified"]:
        return {"key": "none", "label": "Not a fit: %s" % r["disqualified_reason"]}
    if a["contact"]["status"] == "DO_NOT_CONTACT":
        return {"key": "none", "label": "Do not contact"}
    if a["contact"]["status"] in ("NONE", "BAD"):
        return {"key": "find_contact", "label": "Find a working phone or email first"}
    if a["readiness"]["level"] == "DEAL_READY":
        return {"key": "prepare", "label": "Prepare the deal package and approve outreach"}
    if a["buy_box"]["status"] != "CONFIRMED":
        return {"key": "qualify", "label": "Confirm their buy box before sending"}
    return {"key": "qualify", "label": "Confirm they are buying now and funding"}


def save(db, org_id: str, deal_id: str, buyer_id: str, user) -> Dict[str, Any]:
    """Store one buyer's match on a deal (the existing match table, with reasons)."""
    from app.models.wholesale_models import WholesaleBuyer, WholesaleBuyerMatch, WholesaleDeal, WholesaleProperty
    from app.services import wholesale_service as svc
    deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id, WholesaleDeal.organization_id == org_id).first()
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == buyer_id, WholesaleBuyer.organization_id == org_id).first()
    if deal is None or b is None:
        raise HTTPException(status_code=404, detail="Deal or buyer not found")
    if bool(deal.is_test) != bool(b.is_test):
        raise HTTPException(status_code=409, detail="A sandbox deal is matched only with sandbox buyers.")
    prop = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id).first()
    r = M.match_deal_to_buyers(deal, prop, [b])
    if not r:
        raise HTTPException(status_code=409, detail="This buyer cannot be matched (inactive, opted out or linked "
                                                    "to another investor's record).")
    r = r[0]
    row = db.query(WholesaleBuyerMatch).filter(WholesaleBuyerMatch.deal_id == deal.id,
                                               WholesaleBuyerMatch.buyer_id == b.id).first()
    if row is None:
        row = WholesaleBuyerMatch(organization_id=org_id, deal_id=deal.id, buyer_id=b.id)
        db.add(row)
    row.buy_box_id = getattr(r["buy_box"], "id", None)
    row.score = r["score"]
    row.factors = json.dumps(r["factors"])
    row.disqualified = r["disqualified"]
    row.disqualified_reason = r["disqualified_reason"]
    row.computed_at = datetime.utcnow()
    db.flush()
    svc.log_event(db, org_id, "buyers.match_saved", actor_type="user", actor_user_id=user.id, deal_id=deal.id,
                  summary="Saved buyer match: %s (%d, %s)" % (ENG._name(b), r["score"], M.VERSION),
                  details={"buyer_id": b.id, "score": r["score"], "version": M.VERSION})
    return {"deal_id": deal.id, "buyer_id": b.id, "score": row.score, "disqualified": row.disqualified}


def prepare(db, org_id: str, evo_id: str, user) -> Dict[str, Any]:
    """A discovery becomes a deal through the EXISTING promotion path, then the
    deal's matches are stored. Nothing is sent."""
    from app.models.evosense_models import EvoSenseProperty
    from app.services import wholesale_service as svc
    from app.services.evosense import promotion
    evo = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == evo_id,
                                            EvoSenseProperty.organization_id == org_id).first()
    if evo is None:
        raise HTTPException(status_code=404, detail="Property not found")
    res = promotion.promote(db, org_id, evo, user, note="Prepared for buyer matching / disposition review")
    deal = svc.get_deal(db, org_id, res["deal_id"])
    rows = svc.recompute_matches(db, org_id, deal, user)
    return {"deal_id": deal.id, "already": bool(res.get("already")), "matches_saved": len(rows)}


# ── buyer demand -> hunt suggestions (read only) ────────────────────────────

def demand(db, org_id: str, *, segment: Optional[str] = None, include_test: bool = False,
           only_verified: bool = True, today: Optional[date] = None) -> Dict[str, Any]:
    """What verified buyers say they want, grouped - and the hunting criteria
    that would serve them. Read-only: nothing changes a hunt, nothing is bought."""
    today = today or date.today()
    buyers, boxes, acqs, ents, convs, matches, activity = ENG.load(db, org_id, include_test=include_test)
    groups: Dict[str, Dict[str, Any]] = {}
    for b in buyers:
        if b.linked_to_buyer_id or b.do_not_contact or not b.is_active:
            continue
        a = ENG.assess(b, boxes.get(b.id, []), acqs.get(b.id, []), ents.get(b.id, []), activity.get(b.id), today)
        verified = a["readiness"]["level"] in ("ACTIVE", "DEAL_READY") or a["buy_box"]["status"] == "CONFIRMED"
        if only_verified and not verified:
            continue
        for x in boxes.get(b.id, []):
            if not x.is_active:
                continue
            segs = ENG.box_segments(x) or {"other": set()}
            for seg in segs:
                if segment and seg != segment:
                    continue
                g = groups.setdefault(seg, {"segment": seg, "buyers": set(), "counties": {}, "cities": {}, "zips": {},
                                            "price_min": [], "price_max": [], "units_min": [], "units_max": [],
                                            "acres_min": [], "acres_max": [], "strategies": {}})
                g["buyers"].add(b.id)
                for col, key in (("counties", "counties"), ("cities", "cities"), ("zips", "zips")):
                    for v in ENG._lst(getattr(x, col)):
                        g[key][v] = g[key].get(v, 0) + 1
                for v in ENG._lst(x.strategies):
                    g["strategies"][v] = g["strategies"].get(v, 0) + 1
                for src, dst in (("min_price", "price_min"), ("max_price", "price_max"), ("min_units", "units_min"),
                                 ("max_units", "units_max"), ("min_acres", "acres_min"), ("max_acres", "acres_max")):
                    v = ENG._num(getattr(x, src))
                    if v is not None:
                        g[dst].append(v)
    out = []
    for seg, g in groups.items():
        def top(d, n=8):
            return [k.title() if not k.isdigit() else k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:n]]
        n = len(g["buyers"])
        sugg = {"property_category": seg if seg not in ("single_family", "portfolio", "other") else
                ("residential" if seg == "single_family" else None),
                "counties": top(g["counties"]), "zips": top(g["zips"], 20),
                "price_min": min(g["price_min"]) if g["price_min"] else None,
                "price_max": max(g["price_max"]) if g["price_max"] else None,
                "units_min": min(g["units_min"]) if g["units_min"] else None,
                "units_max": max(g["units_max"]) if g["units_max"] else None,
                "acres_min": min(g["acres_min"]) if g["acres_min"] else None,
                "acres_max": max(g["acres_max"]) if g["acres_max"] else None}
        out.append({"segment": seg, "label": dict(ENG.SEGMENTS).get(seg, seg), "buyers": n,
                    "counties": top(g["counties"]), "cities": top(g["cities"]), "strategies": top(g["strategies"]),
                    "suggested_hunt": sugg})
    out.sort(key=lambda x: -x["buyers"])
    return {"groups": out, "only_verified": only_verified,
            "note": "Buyer DEMAND, summarized from buy boxes. It suggests hunting criteria; it changes no hunt, "
                    "starts nothing and buys no data. Starting a hunt needs the owner's approval on Strategies. "
                    "Property distress scores are never adjusted to fit demand."}
