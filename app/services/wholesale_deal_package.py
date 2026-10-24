"""INVESTOR DEAL PACKAGE (R3): create, review, approve, publish, withdraw - and a PDF.

Built ON the existing publication boundary, not beside it:
  * the content is `wholesale_publication.buyer_room_payload` - the same field-by-field
    whitelist the investor page uses, so a package can never carry a seller detail,
    the MAO, the contract price or the fee;
  * approval is an ordinary Approval Center request (kind "package") that snapshots
    the exact content and its hash;
  * publishing turns on the SAME buyer room the existing links already serve, and is
    refused unless the approved snapshot is still the current content - an edit made
    after approval sends the package back to review;
  * the investor offer form, view tracking and buyer board are unchanged.

Status is DERIVED from records that already exist (events, the approval, the deal),
so there is no second state to drift:
  Draft -> Review -> Approved -> Published -> Under offer -> Closed, or Withdrawn.
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

STATUSES = ("none", "draft", "review", "approved", "published", "changed", "under_offer", "closed", "withdrawn")
LABELS = {"none": "No package yet", "draft": "Draft", "review": "In review", "approved": "Approved",
          "published": "Published", "changed": "Changed after approval - hidden from investors",
          "under_offer": "Under offer", "closed": "Closed", "withdrawn": "Withdrawn"}
# Per-visitor fields: never part of what is approved.
VOLATILE = ("published", "you", "recipient_name")
# Shown to investors but NOT marketing content: changing them must not take a live
# page down mid-sale (closing dates, the operator's own branding, the legal notice).
NOT_MATERIAL = ("closing", "brand", "disclosure", "deal_id")
KIND = "package"


def content(db, deal) -> Dict[str, Any]:
    """Exactly what the investor page shows (the publication whitelist), photos and
    documents included, so swapping a photo is a change like any other."""
    from app.services import wholesale_publication as pub
    p = pub.buyer_room_payload(db, deal)
    out = {k: v for k, v in p.items() if k not in VOLATILE}
    out["photo_count"] = len(p.get("photos") or [])
    out["document_count"] = len(p.get("documents") or [])
    return out


def material(c: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in c.items() if k not in NOT_MATERIAL}


def content_hash(c: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(material(c), sort_keys=True, default=str).encode()).hexdigest()[:16]


def _latest_approved(apps) -> Optional[Any]:
    for a in reversed(apps):
        if a.status == "approved":
            return a
    return None


def serving(db, org_id: str, deal) -> bool:
    """THE ONE RULE for every investor-facing route: the page is live only while it is
    published AND what it would show is exactly what was last approved. Any edit,
    photo or document change, or a publish through any route, takes it dark until
    approved again. Withdrawn / unpublished is never served."""
    if not deal.buyer_room_published:
        return False
    a = _latest_approved(_approvals(db, org_id, deal.id))
    return a is not None and _inputs(a).get("hash") == content_hash(content(db, deal))


def can_publish(db, user) -> bool:
    """Approving and publishing investor content: an admin or a Wholesale team manager."""
    from app.services import wholesale_team as team
    from app.services.lead_scope import effective_role
    if getattr(user, "role", None) in ("god_admin", "super_admin"):
        return True
    if (effective_role(user, db) or "").lower() in ("org_admin", "super_admin", "god_admin"):
        return True
    return team.role_of(db, user) == team.MANAGER


PUBLISH_ROLE_REFUSAL = "Only an admin or a team manager can approve or publish investor content."


def _events(db, org_id, deal_id) -> List[Any]:
    from app.models.wholesale_models import WholesaleEvent
    return (db.query(WholesaleEvent)
            .filter(WholesaleEvent.organization_id == org_id, WholesaleEvent.deal_id == deal_id,
                    WholesaleEvent.action.in_(("package.created", "package.published", "package.withdrawn")))
            .order_by(WholesaleEvent.created_at.asc()).all())


def _approvals(db, org_id, deal_id) -> List[Any]:
    from app.models.wholesale_models import WholesaleApproval
    return (db.query(WholesaleApproval)
            .filter(WholesaleApproval.organization_id == org_id, WholesaleApproval.deal_id == deal_id,
                    WholesaleApproval.kind == KIND)
            .order_by(WholesaleApproval.created_at.asc()).all())


def _inputs(a) -> Dict[str, Any]:
    try:
        return json.loads(a.inputs or "{}")
    except (TypeError, ValueError):
        return {}


def status(db, org_id: str, deal) -> Dict[str, Any]:
    from app.models.wholesale_models import WholesaleBuyerOutreach
    from app.utils.time_fmt import iso_utc
    evs = _events(db, org_id, deal.id)
    apps = _approvals(db, org_id, deal.id)
    cur = content(db, deal)
    h = content_hash(cur)
    created = [e for e in evs if e.action == "package.created"]
    last_ev = evs[-1] if evs else None
    last_app = apps[-1] if apps else None
    latest_ok = _latest_approved(apps)
    approved_hash = _inputs(latest_ok).get("hash") if latest_ok is not None else None
    changed_since_approval = bool(approved_hash and approved_hash != h)
    live = bool(deal.buyer_room_published) and approved_hash is not None and not changed_since_approval
    notes: List[str] = []

    if deal.closed_at is not None and deal.buyer_room_published:
        key = "closed"
    elif not created:
        key = "none"
    elif last_ev is not None and last_ev.action == "package.withdrawn" and not deal.buyer_room_published:
        key = "withdrawn"
    elif deal.buyer_room_published and not live:
        key = "changed"
        notes.append("Investors cannot see this page: %s Send it for approval again; the existing links "
                     "come back once it is approved." % (
                         "it was never approved." if approved_hash is None
                         else "its content changed after it was approved."))
    elif deal.buyer_room_published:
        offers = (db.query(WholesaleBuyerOutreach)
                  .filter(WholesaleBuyerOutreach.organization_id == org_id,
                          WholesaleBuyerOutreach.deal_id == deal.id,
                          WholesaleBuyerOutreach.offer_amount.isnot(None)).count())
        key = "under_offer" if (offers or deal.assigned_buyer_id) else "published"
    elif last_app is not None and last_app.status == "pending" and last_app.created_at >= created[-1].created_at:
        key = "review"
    elif (approved_hash and not changed_since_approval and last_app is latest_ok
          and last_app.created_at >= created[-1].created_at):
        key = "approved"
    else:
        key = "draft"
        if last_app is not None and last_app.status == "rejected":
            notes.append("The last review was rejected%s." % (": %s" % last_app.comments if last_app.comments else ""))
        if changed_since_approval:
            notes.append("Content changed after approval - it needs review again.")

    missing = []                      # blocks review
    if not (cur.get("property") or {}).get("line1"):
        missing.append("Property address")
    recommended = []                  # shown to the approver; does not block
    if not cur.get("asking_price"):
        recommended.append("Asking price for investors")
    if not cur.get("summary"):
        recommended.append("Summary for investors")
    if not cur.get("photo_count"):
        recommended.append("At least one photo marked for investors")
    return {
        "status": key, "label": LABELS[key], "version": len(created), "content_hash": h,
        "approved_hash": approved_hash, "changed_since_approval": changed_since_approval,
        "approval": ({"id": last_app.id, "status": last_app.status, "requested_at": iso_utc(last_app.created_at),
                      "decided_at": iso_utc(last_app.decided_at), "comments": last_app.comments}
                     if last_app is not None else None),
        "notes": notes, "missing": missing, "recommended": recommended, "content": cur, "live": live,
        "can": {"create": True,
                "submit": key in ("draft", "approved", "changed") and bool(created) and not missing,
                "publish": key == "approved",
                "withdraw": key in ("published", "under_offer", "changed")},
        "history": [{"at": iso_utc(e.created_at), "summary": e.summary} for e in evs]
                   + [{"at": iso_utc(a.created_at), "summary": "Review requested (%s)" % a.status} for a in apps],
    }


def create(db, org_id, deal, user) -> Dict[str, Any]:
    from app.services import wholesale_service as svc
    c = content(db, deal)
    n = len([e for e in _events(db, org_id, deal.id) if e.action == "package.created"]) + 1
    svc.log_event(db, org_id, "package.created", actor_type=svc.ACTOR_USER, actor_user_id=user.id,
                  deal_id=deal.id, summary="Deal package v%d created" % n,
                  after={"hash": content_hash(c), "version": n})
    db.flush()
    return status(db, org_id, deal)


def submit(db, org_id, deal, user) -> Dict[str, Any]:
    from app.services import wholesale_service as svc
    st = status(db, org_id, deal)
    if not st["can"]["submit"]:
        raise HTTPException(status_code=409, detail="This package cannot go to review yet%s." % (
            ": missing " + ", ".join(m.lower() for m in st["missing"]) if st["missing"] else
            " (status: %s)" % st["label"].lower()))
    c = st["content"]
    svc.request_approval(db, org_id, deal, KIND, amount=c.get("asking_price"),
                         recommendation="Publish deal package v%d to investors" % st["version"],
                         reasoning="Content shown to investors is attached; the address, asking price and "
                                   "anything switched on in Sharing.",
                         user=user, inputs={"hash": st["content_hash"], "version": st["version"], "content": c})
    db.flush()
    return status(db, org_id, deal)


def publish(db, org_id, deal, user) -> Dict[str, Any]:
    from app.services import wholesale_service as svc
    if not can_publish(db, user):
        svc.log_event(db, org_id, "publication.refused", actor_type=svc.ACTOR_USER, actor_user_id=user.id,
                      deal_id=deal.id, summary="Publish refused: role")
        db.commit()
        raise HTTPException(status_code=403, detail=PUBLISH_ROLE_REFUSAL)
    st = status(db, org_id, deal)
    if st["status"] != "approved":
        svc.log_event(db, org_id, "publication.refused", actor_type=svc.ACTOR_USER, actor_user_id=user.id,
                      deal_id=deal.id, summary="Publish refused: package is %s" % st["label"].lower())
        db.commit()
        raise HTTPException(status_code=409, detail=(
            "Only an approved package can be published. Status now: %s.%s" % (
                st["label"], " " + " ".join(st["notes"]) if st["notes"] else "")))
    now = datetime.utcnow()
    deal.buyer_room_published = True
    deal.buyer_room_published_at = now
    deal.buyer_room_published_by_id = user.id
    svc.log_event(db, org_id, "publication.published", actor_type=svc.ACTOR_USER, actor_user_id=user.id,
                  deal_id=deal.id, summary="Buyer room published")
    svc.log_event(db, org_id, "package.published", actor_type=svc.ACTOR_USER, actor_user_id=user.id,
                  deal_id=deal.id, summary="Deal package v%d published (approved content)" % st["version"],
                  after={"hash": st["content_hash"]})
    db.flush()
    return status(db, org_id, deal)


def withdraw(db, org_id, deal, user, reason: Optional[str]) -> Dict[str, Any]:
    from app.services import wholesale_service as svc
    deal.buyer_room_published = False
    svc.log_event(db, org_id, "publication.unpublished", actor_type=svc.ACTOR_USER, actor_user_id=user.id,
                  deal_id=deal.id, summary="Buyer room unpublished")
    svc.log_event(db, org_id, "package.withdrawn", actor_type=svc.ACTOR_USER, actor_user_id=user.id,
                  deal_id=deal.id, summary="Deal package withdrawn%s" % (": %s" % reason if reason else ""))
    db.flush()
    return status(db, org_id, deal)


# ── PDF ─────────────────────────────────────────────────────────────────────

def _m(v) -> str:
    return "$%s" % format(int(round(float(v))), ",") if v is not None else "-"


def pdf_bytes(c: Dict[str, Any], *, watermark: Optional[str] = None) -> bytes:
    """A one-to-two page investor sheet from the whitelisted content only."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10, leading=13)
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=18, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
    e = lambda x: escape(str(x)) if x is not None else ""  # noqa: E731

    p = c.get("property") or {}
    brand = c.get("brand") or {}
    story = []
    if brand.get("name"):
        story.append(Paragraph(e(brand["name"]), small))
    story.append(Paragraph(e(p.get("line1") or "Investment property"), h1))
    story.append(Paragraph(e(", ".join(x for x in (p.get("city"), p.get("state"), p.get("zip_code")) if x)), body))
    story.append(Spacer(1, 8))
    facts = [["Asking price", _m(c.get("asking_price"))]]
    if "arv" in c:
        facts.append(["After-repair value (our estimate)", _m(c.get("arv"))])
    if "estimated_repairs" in c:
        facts.append(["Estimated repairs (our estimate)", _m(c.get("estimated_repairs"))])
    for label, key in (("Type", "property_type"), ("Beds", "bedrooms"), ("Baths", "bathrooms"),
                       ("Living area (sq ft)", "square_feet"), ("Lot (sq ft)", "lot_size_sqft"),
                       ("Year built", "year_built"), ("Occupancy", "occupancy_status"), ("County", "county")):
        if p.get(key) not in (None, ""):
            facts.append([label, str(p[key]).replace("_", " ")])
    cl = c.get("closing") or {}
    if cl.get("target_close") or cl.get("closing_date"):
        facts.append(["Target closing", cl.get("closing_date") or cl.get("target_close")])
    if cl.get("title_company"):
        facts.append(["Title company", cl["title_company"]])
    t = Table(facts, colWidths=[2.4 * inch, 4.4 * inch])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 10), ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(t)
    if c.get("summary"):
        story += [Paragraph("Summary", h2), Paragraph(e(c["summary"]), body)]
    if c.get("condition_summary"):
        story += [Paragraph("Condition", h2), Paragraph(e(c["condition_summary"]), body)]
    comps = c.get("comparable_sales") or []
    if comps:
        story.append(Paragraph("Comparable sales", h2))
        rows = [["Address", "Sold", "Date", "Sq ft"]]
        for x in comps[:10]:
            rows.append([e(x.get("address") or x.get("street_address") or ""), _m(x.get("sale_price")),
                         e(x.get("sale_date") or ""), e(x.get("square_feet") or "")])
        ct = Table(rows, colWidths=[3.2 * inch, 1.2 * inch, 1.2 * inch, 1.0 * inch])
        ct.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
                                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd"))]))
        story.append(ct)
    d = c.get("disclosure") or {}
    if d:
        story += [Paragraph(e(d.get("title") or "Notice"), h2), Paragraph(e(d.get("text") or d.get("body") or ""), small)]
    story += [Spacer(1, 8), Paragraph("Figures marked as our estimate are not appraisals. Verify everything "
                                      "independently before buying.", small)]

    def mark(canvas, _doc):
        if watermark:
            canvas.saveState()
            canvas.setFont("Helvetica-Bold", 40)
            canvas.setFillColor(colors.Color(0.85, 0.2, 0.2, alpha=0.18))
            canvas.translate(4.25 * inch, 5.5 * inch)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, watermark)
            canvas.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                      topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                      title="Deal package - %s" % (p.get("line1") or "property")).build(story, onFirstPage=mark, onLaterPages=mark)
    return buf.getvalue()
