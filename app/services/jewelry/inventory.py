"""Inventory & Offers - an internal marketing list, not a store catalog or POS.

Items, their status and photos are real records. "Interested customers" for
an item are customers whose recorded interests or segments match the item's
category / interest tags, plus inquiries staff logged against the item.
There is no POS or inventory feed; the screen says so.
"""
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.jewelry_models import JewelryItem, JewelryItemInterest, JewelryItemPhoto
from app.models.models import Lead
from app.services.jewelry import common as C

MAX_PHOTOS = 12
CATEGORY_SEGMENTS = {"coins": {"coin_collector"}, "gold": {"gold_seller", "jewelry_buyer"},
                     "jewelry": {"jewelry_buyer"}, "silver": {"coin_collector", "jewelry_buyer"},
                     "collectibles": {"coin_collector"}, "other_precious_metals": {"coin_collector"}}


def _load(ctx: C.Ctx, item_id: str) -> JewelryItem:
    it = ctx.db.query(JewelryItem).filter(JewelryItem.id == item_id, JewelryItem.organization_id == ctx.org_id).first()
    if it is None:
        raise HTTPException(404, "Item not found")
    return it


def apply(it: JewelryItem, data: Dict[str, Any]) -> None:
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(400, "Give the item a name")
        it.name = name[:200]
    if "category" in data and data["category"]:
        if data["category"] not in C.ITEM_CATEGORIES:
            raise HTTPException(400, "Unknown category")
        it.category = data["category"]
    if "status" in data and data["status"]:
        if data["status"] not in C.ITEM_STATUSES:
            raise HTTPException(400, "Status must be available, sold or unavailable")
        it.status = data["status"]
    for k, lim in (("description", 4000), ("sku", 80), ("price_note", 120)):
        if k in data:
            setattr(it, k, (data[k] or "").strip()[:lim] or None)
    if "featured" in data and data["featured"] is not None:
        it.featured = bool(data["featured"])
    if "interest_tags" in data:
        it.interest_tags = C.jdump(C.clean_list(data["interest_tags"], limit=20, size=40))


def matching_customers(ctx: C.Ctx, it: JewelryItem, leads: Optional[List[Lead]] = None) -> List[Lead]:
    from app.services.jewelry import customers as CU
    tags = {t.lower() for t in C.jload(it.interest_tags, [])}
    segs = CATEGORY_SEGMENTS.get(it.category, set())
    leads = leads if leads is not None else CU.scoped_query(ctx).all()
    out = []
    for l in leads:
        cf = C.custom(l)
        ints = {str(i).lower() for i in cf.get(C.CF_INTERESTS) or []}
        hit_tag = any(t in i or i in t for t in tags for i in ints) if tags else False
        hit_seg = bool(segs & set(cf.get(C.CF_SEGMENTS) or []))
        if hit_tag or hit_seg:
            out.append(l)
    return out


def item_json(ctx: C.Ctx, it: JewelryItem, photos: List[JewelryItemPhoto], inquiries: int,
              matches: Optional[int]) -> Dict[str, Any]:
    return {"id": it.id, "name": it.name, "category": it.category,
            "category_label": C.ITEM_CATEGORY_LABEL.get(it.category, it.category), "status": it.status,
            "description": it.description, "sku": it.sku, "price_note": it.price_note, "featured": bool(it.featured),
            "interest_tags": C.jload(it.interest_tags, []),
            "photos": [{"id": p.id, "url": "/jewelry-ops/items/%s/photos/%s" % (it.id, p.id)} for p in photos],
            "inquiries": inquiries, "matching_customers": matches,
            "created_at": C.iso(it.created_at), "updated_at": C.iso(it.updated_at)}


def list_items(ctx: C.Ctx, category: Optional[str], status: Optional[str]) -> List[Dict[str, Any]]:
    from app.services.jewelry import customers as CU
    q = ctx.db.query(JewelryItem).filter(JewelryItem.organization_id == ctx.org_id)
    if category and category != "all":
        q = q.filter(JewelryItem.category == category)
    if status and status != "all":
        q = q.filter(JewelryItem.status == status)
    items = q.order_by(JewelryItem.featured.desc(), JewelryItem.updated_at.desc()).all()
    ids = [i.id for i in items] or ["-"]
    photos: Dict[str, List[JewelryItemPhoto]] = {}
    for p in ctx.db.query(JewelryItemPhoto).filter(JewelryItemPhoto.organization_id == ctx.org_id,
                                                   JewelryItemPhoto.item_id.in_(ids)).order_by(JewelryItemPhoto.created_at).all():
        photos.setdefault(p.item_id, []).append(p)
    inq: Dict[str, int] = {}
    for r in ctx.db.query(JewelryItemInterest).filter(JewelryItemInterest.organization_id == ctx.org_id,
                                                      JewelryItemInterest.item_id.in_(ids)).all():
        inq[r.item_id] = inq.get(r.item_id, 0) + 1
    leads = CU.scoped_query(ctx).all()
    return [item_json(ctx, it, photos.get(it.id, []), inq.get(it.id, 0), len(matching_customers(ctx, it, leads)))
            for it in items]


def log_interest(ctx: C.Ctx, it: JewelryItem, lead: Lead, note: Optional[str]) -> JewelryItemInterest:
    r = JewelryItemInterest(organization_id=ctx.org_id, item_id=it.id, lead_id=lead.id,
                            note=(note or "").strip()[:300] or None, recorded_by=ctx.user.id)
    ctx.db.add(r)
    ints = C.custom(lead).get(C.CF_INTERESTS) or []
    label = C.ITEM_CATEGORY_LABEL.get(it.category, it.category)
    if label not in ints:
        C.set_custom(lead, **{C.CF_INTERESTS: C.clean_list(ints + [label])})
    ctx.db.flush()
    return r


def inquiries(ctx: C.Ctx, it: JewelryItem) -> List[Dict[str, Any]]:
    rows = (ctx.db.query(JewelryItemInterest, Lead).join(Lead, Lead.id == JewelryItemInterest.lead_id)
            .filter(JewelryItemInterest.organization_id == ctx.org_id, JewelryItemInterest.item_id == it.id)
            .order_by(JewelryItemInterest.created_at.desc()).limit(200).all())
    return [{"id": r.id, "lead_id": l.id, "customer_name": C.lead_name(l), "note": r.note,
             "at": C.iso(r.created_at)} for r, l in rows]
