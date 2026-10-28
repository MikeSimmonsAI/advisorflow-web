"""PHOTO-ASSISTED REPAIR ESTIMATES - a DRAFT itemized estimate from a deal's photos.

What it does, in order:
  1. QUALITY CHECK every photo locally (no AI): size, darkness, blur, duplicates.
     Unusable photos are named and skipped.
  2. LOOK at the usable photos through an authorized vision provider - today the
     platform's own OpenAI gateway (ai_gateway, capability "wholesale_photo_repairs",
     an approved model, a MANUAL action by a named user, under a daily cap per
     workspace). The model reports only what is VISIBLE: the room or area, the
     observations, and repair categories with a rough quantity and its confidence.
     It never prices anything.
  3. PRICE each suggested line from the DFW cost table below - starting assumptions
     with their source and date, low / base / high - every quantity and unit cost
     editable before saving.
  4. ALWAYS ADD what photos cannot show: foundation, plumbing, electrical, HVAC
     internals, roof decking, sewer line. They are listed as unknowns with a
     contingency suggestion, never as findings.

Nothing is saved to the deal by an analysis. A person reviews the draft and saves
it through POST /wholesale/deals/{id}/repairs/itemized, which records it as a
MANUAL estimate (or an INSPECTION estimate when a contractor's numbers are used),
keeps the history, and recalculates the MAO. An inspection / verified estimate is
never replaced without the person confirming it.

When no vision provider is available the analysis says so and the manual
itemized estimate keeps working exactly as before.
"""
from __future__ import annotations

import io
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

CAPABILITY = "wholesale_photo_repairs"
MAX_PHOTOS = 8
DAILY_CAP_ENV = "WHOLESALE_PHOTO_AI_DAILY_CAP"
DEFAULT_DAILY_CAP = 20

COST_SOURCE = ("EvoSys DFW starting assumptions, Oct 2026 - typical investor-grade unit costs for Dallas-Fort Worth. "
               "Not a contractor bid: replace with real quotes.")
COST_AS_OF = "2026-10-24"


def cost_source(brand: str = "EvoSys") -> str:
    """COST_SOURCE in the workspace's own brand (brand_config.brand_word).
    "EvoSys" gives back COST_SOURCE exactly."""
    return COST_SOURCE.replace("EvoSys DFW", "%s DFW" % (brand or "EvoSys"), 1)


def cost_source_for(db, org_id) -> str:
    from app.services.brand_config import brand_word
    return cost_source(brand_word(db, org_id))
# item key -> (category, label, unit, low, base, high)
DFW_COSTS: Dict[str, Tuple[str, str, str, float, float, float]] = {
    "roof_replace": ("Roof", "Roof replacement (asphalt shingle)", "square (100 sq ft)", 380, 450, 560),
    "roof_repair": ("Roof", "Roof repair / patch", "each", 400, 900, 1800),
    "gutters": ("Roof", "Gutters", "linear ft", 7, 9, 12),
    "exterior_paint": ("Exterior", "Exterior paint", "sq ft of living area", 1.5, 2.2, 3.2),
    "siding_repair": ("Exterior", "Siding / trim repair", "sq ft", 6, 9, 14),
    "windows": ("Windows & doors", "Window replacement", "each", 350, 500, 800),
    "exterior_door": ("Windows & doors", "Exterior door", "each", 450, 750, 1300),
    "garage_door": ("Windows & doors", "Garage door", "each", 900, 1400, 2200),
    "fence": ("Yard", "Fence repair / replace", "linear ft", 22, 32, 45),
    "landscaping_cleanup": ("Yard", "Yard clean-up / landscaping", "each", 400, 900, 2000),
    "trash_out": ("Yard", "Trash-out / debris removal", "each", 500, 1200, 3000),
    "kitchen_cabinets": ("Kitchen", "Kitchen cabinets", "linear ft", 150, 225, 350),
    "countertops": ("Kitchen", "Countertops", "sq ft", 35, 55, 85),
    "kitchen_appliances": ("Kitchen", "Appliance package", "each", 1800, 2600, 4000),
    "kitchen_full": ("Kitchen", "Kitchen remodel (rental grade)", "each", 9000, 14000, 22000),
    "bath_full": ("Bathrooms", "Bathroom remodel (rental grade)", "each", 5500, 8500, 14000),
    "bath_fixtures": ("Bathrooms", "Bath fixtures / vanity / toilet", "each", 900, 1600, 2800),
    "tub_shower": ("Bathrooms", "Tub / shower surround", "each", 1200, 2200, 4000),
    "flooring_lvp": ("Flooring", "LVP flooring", "sq ft", 3.5, 4.5, 6.5),
    "carpet": ("Flooring", "Carpet", "sq ft", 2.5, 3.5, 5),
    "tile_floor": ("Flooring", "Tile floor", "sq ft", 8, 11, 16),
    "interior_paint": ("Paint (inside & out)", "Interior paint", "sq ft of living area", 2, 2.8, 4),
    "drywall": ("Interior", "Drywall repair", "sq ft", 3, 5, 9),
    "ceiling_water_damage": ("Interior", "Ceiling water-damage repair (visible stain)", "each", 400, 900, 2500),
    "hvac_replace": ("HVAC", "HVAC system replacement", "each", 6500, 8500, 12000),
    "water_heater": ("Plumbing", "Water heater", "each", 1200, 1700, 2600),
    "electrical_panel": ("Electrical", "Electrical panel replacement", "each", 1800, 2600, 4000),
    "light_fixtures": ("Electrical", "Light fixtures / outlets", "each", 60, 110, 200),
    "foundation_repair": ("Foundation", "Foundation repair (piers)", "pier", 300, 400, 550),
    "other": ("Other", "Other (describe)", "each", 0, 0, 0),
}
NOT_VISIBLE = [
    ("Foundation", "Foundation movement under the slab or piers"),
    ("Plumbing", "Supply and drain lines inside walls and under the slab, sewer line"),
    ("Electrical", "Wiring inside walls, panel capacity and grounding"),
    ("HVAC", "System age, refrigerant, ductwork condition"),
    ("Roof", "Roof decking and underlayment, attic leaks"),
    ("Other", "Termites / pests, mold behind surfaces, permits"),
]
AREAS = ("exterior_front", "exterior_rear", "exterior_side", "roof", "kitchen", "living_room", "bedroom",
         "bathroom", "garage", "hvac", "electrical", "plumbing", "foundation", "yard", "other")


# ── 1. quality (local) ──────────────────────────────────────────────────────

def quality(data: bytes) -> Dict[str, Any]:
    """{ok, width, height, problems[]} - Pillow only, no AI."""
    from PIL import Image, ImageFilter, ImageStat
    problems: List[str] = []
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa: BLE001
        return {"ok": False, "problems": ["Not a readable image."], "width": None, "height": None}
    w, h = im.size
    if w < 480 or h < 360:
        problems.append("Too small (%dx%d) - details cannot be seen." % (w, h))
    g = im.convert("L")
    g.thumbnail((640, 640))
    mean = ImageStat.Stat(g).mean[0]
    if mean < 35:
        problems.append("Too dark to judge condition.")
    if mean > 235:
        problems.append("Washed out / overexposed.")
    fe = g.filter(ImageFilter.FIND_EDGES)
    fe = fe.crop((3, 3, max(4, fe.width - 3), max(4, fe.height - 3)))   # the filter marks the border itself
    edges = ImageStat.Stat(fe).var[0]
    if edges < 60:
        problems.append("Blurry - edges are not sharp.")
    return {"ok": not problems, "problems": problems, "width": w, "height": h}


# ── 2. the vision provider (adapter) ────────────────────────────────────────

PROMPT = (
    "You are helping a real-estate investor in Dallas-Fort Worth look at property photos. "
    "Report ONLY what is clearly visible. Do not guess about anything hidden (foundation, plumbing, "
    "electrical, HVAC internals, roof decking). Do not give prices.\n"
    "For each photo, in order, return JSON with: index, area (one of %s), "
    "observations (list of short plain sentences about visible condition), "
    "repairs (list of {item, quantity, unit_hint, confidence, evidence}) where item is one of %s, "
    "quantity is your rough visible quantity or 1 when unknown, confidence is low/medium/high, "
    "evidence quotes what in the photo shows it. Use an empty repairs list when nothing visibly needs work.\n"
    "Return ONLY: {\"photos\": [ ... ]}"
) % (", ".join(AREAS), ", ".join(DFW_COSTS))


class OpenAIVision:
    key = "openai_vision"
    label = "OpenAI vision (platform AI gateway)"

    def available(self) -> Tuple[bool, str]:
        from app.services import ai_gateway as gw
        if not os.environ.get("OPENAI_API_KEY"):
            return False, "AI is not configured on this server (OPENAI_API_KEY is not set)."
        if not gw.manual_enabled():
            return False, "Manual AI actions are switched off (AI_MANUAL_ACTIONS_ENABLED=false)."
        try:
            gw.resolve_model(CAPABILITY)
        except gw.AIRefused as exc:
            return False, str(exc)
        return True, ""

    def analyze(self, images: List[Tuple[str, bytes]], *, actor: str, org_id: str, client=None) -> Dict[str, Any]:
        import base64
        from app.services import ai_gateway as gw
        content: List[Dict[str, Any]] = [{"type": "text", "text": PROMPT}]
        for ctype, data in images:
            content.append({"type": "image_url", "image_url": {
                "url": "data:%s;base64,%s" % (ctype, base64.b64encode(_shrink(data)).decode()), "detail": "low"}})
        resp = gw.chat_completion(feature="wholesale.photo_repairs", capability=CAPABILITY, mode=gw.MANUAL,
                                  actor=actor, org_id=org_id, client=client,
                                  messages=[{"role": "user", "content": content}],
                                  response_format={"type": "json_object"}, temperature=0, max_tokens=1800)
        text = resp.choices[0].message.content or "{}"
        try:
            return json.loads(text)
        except ValueError:
            m = re.search(r"\{.*\}", text, re.S)
            return json.loads(m.group(0)) if m else {"photos": []}


def _shrink(data: bytes, edge: int = 1024) -> bytes:
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGB")
        im.thumbnail((edge, edge))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return data


PROVIDERS = [OpenAIVision()]


def provider_status() -> Dict[str, Any]:
    out = []
    for p in PROVIDERS:
        ok, why = p.available()
        out.append({"key": p.key, "label": p.label, "available": ok, "why": why or None})
    return {"providers": out, "available": any(x["available"] for x in out)}


# ── 3. price from the DFW table ─────────────────────────────────────────────

def price_lines(suggested: List[Dict[str, Any]], living_area: Optional[float],
                source: str = COST_SOURCE) -> List[Dict[str, Any]]:
    lines = []
    for s in suggested:
        key = s.get("item") if s.get("item") in DFW_COSTS else "other"
        cat, label, unit, lo, base, hi = DFW_COSTS[key]
        q = s.get("quantity")
        try:
            q = float(q)
        except (TypeError, ValueError):
            q = 1.0
        if "living area" in unit and living_area:
            q = float(living_area)
        q = max(0.0, min(q, 100000.0))
        lines.append({"item_key": key, "category": cat, "item": label, "quantity": round(q, 1), "unit": unit,
                      "unit_cost": base, "unit_cost_low": lo, "unit_cost_high": hi,
                      "line_low": round(q * lo, 2), "line_base": round(q * base, 2), "line_high": round(q * hi, 2),
                      "confidence": s.get("confidence") or "low", "evidence": (s.get("evidence") or "")[:300],
                      "photo": s.get("photo"), "area": s.get("area"),
                      "cost_source": source, "cost_as_of": COST_AS_OF})
    return lines


# ── 4. the request ──────────────────────────────────────────────────────────

def _photos(db, org_id: str, deal) -> List[Any]:
    from app.models.wholesale_models import WholesaleFile
    return (db.query(WholesaleFile)
            .filter(WholesaleFile.organization_id == org_id, WholesaleFile.property_id == deal.property_id,
                    WholesaleFile.kind == "property_photo")
            .order_by(WholesaleFile.sort_order.asc(), WholesaleFile.created_at.asc()).all())


def used_today(db, org_id: str) -> int:
    from app.models.wholesale_models import WholesaleEvent
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (db.query(WholesaleEvent).filter(WholesaleEvent.organization_id == org_id,
                                            WholesaleEvent.action == "repairs.photo_analyzed",
                                            WholesaleEvent.created_at >= start).count())


def daily_cap() -> int:
    try:
        return max(0, int(os.environ.get(DAILY_CAP_ENV) or DEFAULT_DAILY_CAP))
    except ValueError:
        return DEFAULT_DAILY_CAP


def analyze(db, org_id: str, deal, user, *, photo_ids: Optional[List[str]] = None, client=None,
            provider=None) -> Dict[str, Any]:
    from fastapi import HTTPException
    from app.services import wholesale_files as F
    from app.services import wholesale_service as svc
    rows = _photos(db, org_id, deal)
    if photo_ids:
        rows = [r for r in rows if r.id in set(photo_ids)]
    if not rows:
        raise HTTPException(status_code=409, detail="Upload property photos first (exterior, kitchen, baths, roof).")
    checked, usable, seen = [], [], set()
    for r in rows[:MAX_PHOTOS * 2]:
        try:
            data = F.fetch(r.storage_backend, r.storage_key, db=db)
        except Exception:  # noqa: BLE001
            checked.append({"photo_id": r.id, "ok": False, "problems": ["The stored file could not be read."]})
            continue
        q = quality(data)
        if r.checksum and r.checksum in seen:
            q["ok"], q["problems"] = False, q["problems"] + ["Same image as another photo."]
        seen.add(r.checksum)
        checked.append({"photo_id": r.id, "category": r.category, "caption": r.caption, **q})
        if q["ok"] and len(usable) < MAX_PHOTOS:
            usable.append((r, data))
    p = provider or PROVIDERS[0]
    ok, why = p.available()
    base = {"deal_id": deal.id, "photos": checked, "not_visible": [{"category": c, "what": w} for c, w in NOT_VISIBLE],
            "cost_source": cost_source_for(db, org_id), "cost_as_of": COST_AS_OF, "draft": True,
            "note": "A draft from photos. Nothing is saved until you review it and press Save. Photos cannot show "
                    "hidden damage - budget a contingency or get an inspection."}
    if not usable:
        return {**base, "ai": {"available": ok, "used": False, "why": "No photo passed the quality check."},
                "lines": [], "observations": []}
    if not ok:
        return {**base, "ai": {"available": False, "used": False, "why": why}, "lines": [], "observations": [],
                "manual": "Use the itemized estimate below - the categories and DFW unit costs are the same."}
    cap = daily_cap()
    if used_today(db, org_id) >= cap:
        return {**base, "ai": {"available": True, "used": False,
                               "why": "Today's photo-analysis limit for this workspace is used (%d). It resets at "
                                      "midnight UTC; %s raises it." % (cap, DAILY_CAP_ENV)},
                "lines": [], "observations": []}
    images = [(_jpeg(r.content_type), data) for r, data in usable]
    try:
        raw = p.analyze(images, actor=user.id, org_id=org_id, client=client)
    except Exception as exc:  # noqa: BLE001 - the provider failing is reported, never faked
        return {**base, "ai": {"available": True, "used": False,
                               "why": "The vision provider did not answer: %s" % type(exc).__name__},
                "lines": [], "observations": []}
    suggested, observations = [], []
    for i, ph in enumerate((raw or {}).get("photos") or []):
        try:
            idx = int(ph.get("index", i))
        except (TypeError, ValueError):
            idx = i
        row = usable[idx][0] if 0 <= idx < len(usable) else None
        area = ph.get("area") if ph.get("area") in AREAS else "other"
        for o in (ph.get("observations") or [])[:8]:
            observations.append({"photo_id": getattr(row, "id", None), "area": area, "text": str(o)[:300]})
        for rep in (ph.get("repairs") or [])[:10]:
            if isinstance(rep, dict):
                suggested.append(dict(rep, photo=getattr(row, "id", None), area=area))
    sqft = None
    try:
        from app.models.wholesale_models import WholesaleProperty
        wp = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id).first()
        sqft = float(wp.square_feet) if wp is not None and wp.square_feet else None
    except Exception:  # noqa: BLE001
        sqft = None
    lines = _merge(price_lines(suggested, sqft, cost_source_for(db, org_id)))
    svc.log_event(db, org_id, "repairs.photo_analyzed", actor_type=svc.ACTOR_USER, actor_user_id=user.id,
                  deal_id=deal.id, summary="Photos analyzed for repairs: %d photo(s), %d suggested line(s) (draft)" % (
                      len(usable), len(lines)),
                  after={"provider": p.key, "photos": [r.id for r, _ in usable], "lines": len(lines)})
    return {**base, "ai": {"available": True, "used": True, "provider": p.label},
            "observations": observations, "lines": lines,
            "totals": {"low": round(sum(x["line_low"] for x in lines), 2),
                       "base": round(sum(x["line_base"] for x in lines), 2),
                       "high": round(sum(x["line_high"] for x in lines), 2)}}


def _jpeg(ctype: Optional[str]) -> str:
    return "image/jpeg"           # _shrink re-encodes every image as JPEG


def _merge(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One line per item: the same roof seen in three photos is one roof."""
    out: Dict[str, Dict[str, Any]] = {}
    rank = {"low": 0, "medium": 1, "high": 2}
    for ln in lines:
        k = ln["item_key"] if ln["item_key"] != "other" else "other:%s" % ln["evidence"][:40]
        cur = out.get(k)
        if cur is None:
            out[k] = dict(ln, photos=[ln["photo"]] if ln.get("photo") else [])
            continue
        if ln["quantity"] > cur["quantity"] and "living area" not in ln["unit"]:
            for f in ("quantity", "line_low", "line_base", "line_high"):
                cur[f] = ln[f]
        if rank.get(ln["confidence"], 0) > rank.get(cur["confidence"], 0):
            cur["confidence"] = ln["confidence"]
        if ln.get("photo") and ln["photo"] not in cur["photos"]:
            cur["photos"].append(ln["photo"])
    return list(out.values())
