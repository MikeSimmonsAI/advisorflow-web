"""The funding deal packet: what a funding partner needs to look at a deal,
built ONLY from what this workspace actually holds, each figure with its basis.

RULES
-----
* Nothing is invented. A value the system does not hold is shown as
  NOT ON FILE - never estimated, never defaulted, never zero-filled. ARV comes
  only from the deal's own ARV with its source and confidence; MAO appears only
  when the MAO gate says CALCULATED, otherwise its reasons are printed; repairs
  carry their status and source; comps are the included comps with their
  origin and verification state; title status is the deal's own field.
* No seller personal data (name, phone, email, motivation, conversation) and no
  disposition economics (assignment fee, desired fee, buyer offers). A lender
  needs the property and the purchase, not the seller's story or our margin.
* Photos and documents are LISTED from the file records. The packet never
  embeds them and never fails for want of them - with no file storage it says
  so and is still complete.
* Generating a packet is not sending it. Nothing here emails, texts or shares;
  the packet is a download for a person to review and send themselves. Every
  generation is a wholesale event.
* EvoSys is not the lender, and the packet says so.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.wholesale_models import (ACTOR_USER, WholesaleComp, WholesaleFile,
                                         WholesaleFundingPartner, WholesaleFundingSubmission,
                                         WholesaleProperty, WholesaleSellerProfile)
from app.services import wholesale_service as svc
from app.services.wholesale_funding import DISCLAIMER, PRODUCT_LABELS

NOT_ON_FILE = "Not on file"


def _n(v) -> Optional[float]:
    return float(v) if v is not None else None


def _val(value, basis: Optional[str] = None, *, money: bool = False) -> Dict[str, Any]:
    if value is None or value == "":
        return {"value": None, "display": NOT_ON_FILE, "basis": None}
    disp = ("$" + format(round(float(value)), ",")) if money else str(value)
    return {"value": _n(value) if money else value, "display": disp, "basis": basis}


def build(db: Session, org_id: str, deal_id: str, *, partner_id: Optional[str] = None,
          submission_id: Optional[str] = None) -> Dict[str, Any]:
    from app.services import mao_gate, wholesale_publication as PUB, wholesale_repairs as REP
    deal = svc.get_deal(db, org_id, deal_id)                     # 404 outside this tenant
    prop = (db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id,
                                               WholesaleProperty.organization_id == org_id).first())
    partner = None
    if partner_id:
        from app.services.wholesale_funding import get_partner
        partner = get_partner(db, org_id, partner_id)
    sub = None
    if submission_id:
        sub = (db.query(WholesaleFundingSubmission)
               .filter(WholesaleFundingSubmission.id == submission_id,
                       WholesaleFundingSubmission.organization_id == org_id,
                       WholesaleFundingSubmission.deal_id == deal.id).first())
        if sub is not None and partner is None:
            partner = (db.query(WholesaleFundingPartner)
                       .filter(WholesaleFundingPartner.id == sub.partner_id,
                               WholesaleFundingPartner.organization_id == org_id).first())
    settings = svc.resolve_settings(db, org_id, commit=False)
    profile = (db.query(WholesaleSellerProfile)
               .filter(WholesaleSellerProfile.id == deal.seller_profile_id,
                       WholesaleSellerProfile.organization_id == org_id).first()
               if deal.seller_profile_id else None)
    brand = PUB.branding(db, deal)

    p = prop
    property_block = {
        "address": svc.address_line(p) if p else NOT_ON_FILE,
        "facts": [
            ("County", _val(getattr(p, "county", None))),
            ("Parcel / APN", _val(getattr(p, "parcel_apn", None))),
            ("Property type", _val((getattr(p, "property_type", None) or "").replace("_", " ") or None)),
            ("Bedrooms", _val(_n(getattr(p, "bedrooms", None)))),
            ("Bathrooms", _val(_n(getattr(p, "bathrooms", None)))),
            ("Living area (sq ft)", _val(getattr(p, "square_feet", None))),
            ("Lot size (sq ft)", _val(getattr(p, "lot_size_sqft", None))),
            ("Year built", _val(getattr(p, "year_built", None))),
            ("Occupancy", _val((getattr(p, "occupancy_status", None) or "").replace("_", " ") or None)),
        ],
    }
    condition = _val(getattr(profile, "property_condition", None), "stated by the seller")
    repairs = REP.view(db, org_id, deal)
    repairs_block = {"status": repairs["label"],
                     "amount": _val(repairs["amount"], repairs["label"], money=True),
                     "range": ("%s - %s" % (_val(repairs["low"], money=True)["display"],
                                            _val(repairs["high"], money=True)["display"])
                               if repairs["low"] is not None and repairs["high"] is not None else None)}
    gate = mao_gate.evaluate(deal, settings)
    arv_basis = None
    if deal.arv is not None:
        arv_basis = "%s%s" % (deal.arv_source or "source not recorded",
                              (", %s confidence" % deal.arv_confidence) if getattr(deal, "arv_confidence", None) else "")
    valuation = {
        "arv": _val(_n(deal.arv), arv_basis, money=True) if deal.arv is not None else
               {"value": None, "display": "Not established", "basis": "no evidence-backed ARV on file"},
        "estimated_value": (_val(_n(p.estimated_value), p.estimated_value_source or "source not recorded", money=True)
                            if p is not None and p.estimated_value is not None else _val(None)),
        "mao": ({"value": _n(deal.max_allowable_offer), "basis": "calculated by the MAO gate",
                 "display": _val(_n(deal.max_allowable_offer), money=True)["display"]}
                if gate.get("status") == mao_gate.CALCULATED and deal.max_allowable_offer is not None
                else {"value": None, "display": "NOT CALCULATED",
                      "basis": "; ".join(gate.get("reasons") or []) or "not calculated"}),
    }
    comps = (db.query(WholesaleComp).filter(WholesaleComp.organization_id == org_id,
                                            WholesaleComp.deal_id == deal.id,
                                            WholesaleComp.included.is_(True))
             .order_by(WholesaleComp.sale_date.desc()).all())
    comps_block = [{"address": ", ".join(x for x in (c.street_address, c.city) if x),
                    "sale_price": _val(_n(c.sale_price), money=True)["display"],
                    "sale_date": c.sale_date.isoformat() if c.sale_date else NOT_ON_FILE,
                    "square_feet": c.square_feet or NOT_ON_FILE,
                    "distance_miles": _n(c.distance_miles),
                    "origin": "provider" if getattr(c, "provider_key", None) else "entered by a person",
                    "verification": getattr(c, "verification_state", None) or "manual"} for c in comps]
    terms = [
        ("Contract price", _val(_n(deal.contract_price), "purchase agreement", money=True)),
        ("Contract status", _val(deal.contract_status)),
        ("Contract date", _val(deal.contract_date.isoformat() if deal.contract_date else None)),
        ("Earnest money", _val(_n(deal.earnest_money), money=True)),
        ("Closing deadline", _val(deal.closing_deadline.isoformat() if deal.closing_deadline else None)),
        ("Target close", _val(deal.close_of_escrow_target.isoformat() if deal.close_of_escrow_target else None)),
    ]
    title = [
        ("Title status", _val((deal.title_status or "").replace("_", " ") or None)),
        ("Title company", _val(deal.title_company)),
        ("Title commitment received", _val(deal.title_commitment_received_at.isoformat()
                                           if deal.title_commitment_received_at else None)),
        ("Title issues noted", _val(deal.title_issues)),
    ]
    files = (db.query(WholesaleFile).filter(WholesaleFile.organization_id == org_id,
                                            (WholesaleFile.deal_id == deal.id)
                                            | (WholesaleFile.property_id == deal.property_id)).all())
    try:
        from app.services import mobile_storage
        storage = mobile_storage.capability()
    except Exception:  # noqa: BLE001
        storage = {"uploads_enabled": False}
    attachments = {
        "photos": [f.caption or f.original_filename or "photo" for f in files if f.kind == "property_photo"],
        "documents": [f.original_filename or f.category or "document" for f in files if f.kind == "document"],
        "note": (None if files else
                 "No photos or documents are on file for this deal." +
                 ("" if storage.get("uploads_enabled") else " File storage is not configured for this workspace.")),
    }
    request = None
    if sub is not None:
        request = {"product": PRODUCT_LABELS.get(sub.product or "", sub.product) or NOT_ON_FILE,
                   "amount_requested": _val(_n(sub.amount_requested), money=True)["display"],
                   "status": sub.status}
    return {
        "title": "Funding deal packet",
        "prepared_by": brand.get("name") or "the workspace",
        "prepared_for": getattr(partner, "name", None),
        "prepared_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "deal_id": deal.id, "is_test": bool(deal.is_test),
        "property": property_block, "condition": condition, "repairs": repairs_block,
        "valuation": valuation, "comps": comps_block, "terms": terms, "title": title,
        "attachments": attachments, "request": request,
        "disclaimer": DISCLAIMER,
        "data_note": ("Every figure is what this workspace holds, with its basis. "
                      "'%s' means the system has no value; nothing was estimated to fill it." % NOT_ON_FILE),
    }


def log_generated(db: Session, org_id: str, packet: Dict[str, Any], user) -> None:
    svc.log_event(db, org_id, "funding.packet_generated", actor_type=ACTOR_USER,
                  actor_user_id=getattr(user, "id", None), deal_id=packet["deal_id"],
                  summary="Funding packet generated%s (not sent)" % (
                      (" for %s" % packet["prepared_for"]) if packet.get("prepared_for") else ""),
                  details={"prepared_for": packet.get("prepared_for")})


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _rows(pairs) -> str:
    out = []
    for label, v in pairs:
        basis = (' <span class="b">(%s)</span>' % _e(v["basis"])) if v.get("basis") else ""
        cls = ' class="nf"' if v["value"] is None else ""
        out.append("<tr><th>%s</th><td%s>%s%s</td></tr>" % (_e(label), cls, _e(v["display"]), basis))
    return "".join(out)


def render_html(pk: Dict[str, Any]) -> str:
    v = pk["valuation"]
    comps = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            _e(c["address"]), _e(c["sale_price"]), _e(c["sale_date"]), _e(c["square_feet"]),
            _e("%.2f mi" % c["distance_miles"] if c["distance_miles"] is not None else "-"),
            _e("%s, %s" % (c["origin"], c["verification"]))) for c in pk["comps"])
    att = pk["attachments"]
    req = pk.get("request")
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Funding deal packet - %(addr)s</title>
<style>
body{font:14px/1.5 -apple-system,"Segoe UI",Roboto,Arial,sans-serif;color:#16233b;margin:0;background:#fff}
.w{max-width:820px;margin:0 auto;padding:32px 28px}
h1{font:700 26px/1.2 Georgia,serif;margin:0 0 4px;color:#0b1f3a}
h2{font:700 13px/1 Arial,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:#7a5a12;
   margin:28px 0 10px;padding-bottom:6px;border-bottom:2px solid #d4ae5a}
.meta{color:#56637a;font-size:13px}
table{width:100%%;border-collapse:collapse}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #e3ded3;vertical-align:top}
th{width:38%%;color:#3d4b63;font-weight:600}
.comps th{width:auto;background:#f5f3ee}
.b{color:#56637a;font-size:12px}
.nf{color:#8a93a3;font-style:italic}
.big{font:700 20px Georgia,serif;color:#0b1f3a}
.note{background:#f5f3ee;border-left:4px solid #d4ae5a;padding:10px 12px;margin:16px 0;font-size:13px}
.test{background:#fdecef;border:1px solid #f1b3bf;color:#6f1024;padding:8px 12px;font-weight:700}
@media print{.w{padding:0}h2{break-after:avoid}table{break-inside:avoid}}
</style></head><body><div class="w">
%(test)s
<h1>%(addr)s</h1>
<div class="meta">Funding deal packet &middot; prepared by %(by)s%(for)s &middot; %(at)s</div>
<div class="note">%(disc)s %(dnote)s</div>
%(req)s
<h2>Property</h2><table>%(facts)s</table>
<h2>Valuation</h2><table>
<tr><th>After-repair value (ARV)</th><td%(arvc)s><span class="big">%(arv)s</span> <span class="b">%(arvb)s</span></td></tr>
<tr><th>Other value on file</th><td%(evc)s>%(ev)s <span class="b">%(evb)s</span></td></tr>
<tr><th>Maximum allowable offer</th><td>%(mao)s <span class="b">(%(maob)s)</span></td></tr>
</table>
<h2>Condition &amp; repairs</h2><table>
<tr><th>Condition</th><td%(condc)s>%(cond)s <span class="b">%(condb)s</span></td></tr>
<tr><th>Repair estimate</th><td>%(rep)s <span class="b">(%(reps)s)</span>%(repr)s</td></tr>
</table>
<h2>Comparable sales used</h2>
%(comps)s
<h2>Purchase terms</h2><table>%(terms)s</table>
<h2>Title</h2><table>%(title)s</table>
<h2>Photos &amp; documents</h2>
<p>%(att)s</p>
</div></body></html>""" % {
        "addr": _e(pk["property"]["address"]),
        "test": '<div class="test">TEST DATA - not a real deal</div>' if pk["is_test"] else "",
        "by": _e(pk["prepared_by"]),
        "for": (" for %s" % _e(pk["prepared_for"])) if pk.get("prepared_for") else "",
        "at": _e(pk["prepared_at"]), "disc": _e(pk["disclaimer"]), "dnote": _e(pk["data_note"]),
        "req": ("<h2>Funding request</h2><table><tr><th>Product</th><td>%s</td></tr>"
                "<tr><th>Amount requested</th><td>%s</td></tr></table>"
                % (_e(req["product"]), _e(req["amount_requested"]))) if req else "",
        "facts": _rows(pk["property"]["facts"]),
        "arv": _e(v["arv"]["display"]), "arvb": _e("(%s)" % v["arv"]["basis"]) if v["arv"]["basis"] else "",
        "arvc": ' class="nf"' if v["arv"]["value"] is None else "",
        "ev": _e(v["estimated_value"]["display"]),
        "evb": _e("(%s)" % v["estimated_value"]["basis"]) if v["estimated_value"]["basis"] else "",
        "evc": ' class="nf"' if v["estimated_value"]["value"] is None else "",
        "mao": _e(v["mao"]["display"]), "maob": _e(v["mao"]["basis"]),
        "cond": _e(pk["condition"]["display"]),
        "condb": _e("(%s)" % pk["condition"]["basis"]) if pk["condition"]["basis"] else "",
        "condc": ' class="nf"' if pk["condition"]["value"] is None else "",
        "rep": _e(pk["repairs"]["amount"]["display"]), "reps": _e(pk["repairs"]["status"]),
        "repr": (" &middot; range %s" % _e(pk["repairs"]["range"])) if pk["repairs"]["range"] else "",
        "comps": ('<table class="comps"><tr><th>Address</th><th>Sale price</th><th>Sale date</th>'
                  '<th>Sq ft</th><th>Distance</th><th>Origin</th></tr>%s</table>' % comps)
                 if pk["comps"] else '<p class="nf">No comparable sales are on file for this deal.</p>',
        "terms": _rows(pk["terms"]), "title": _rows(pk["title"]),
        "att": _e("; ".join(filter(None, [
            ("Photos on file: %s" % ", ".join(att["photos"])) if att["photos"] else None,
            ("Documents on file: %s" % ", ".join(att["documents"])) if att["documents"] else None,
            att["note"]])) or "None on file."),
    }
