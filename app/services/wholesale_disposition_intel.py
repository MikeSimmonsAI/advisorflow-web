"""DISPOSITION (R4): engagement, offer comparison, follow-ups, closing checklist, analytics.

Read-only over the existing CRM rows - buyer outreach, share-link views, approvals,
the deal itself. No second CRM, no second task system, nothing sent. Every count is
a count of rows that exist; an event this deployment cannot observe (an email open
with no tracking) stays 0 and is labelled as such, never inferred from "we sent it".

The offer comparison lays facts side by side and names which offer is highest, which
nets the most, which closes soonest and which has verified funds. It does not pick:
the existing buyer board and select-buyer step are where a person decides.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

NO_REPLY_DAYS = 2
DEADLINE_DAYS = 3


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _iso(d) -> Optional[str]:
    from app.utils.time_fmt import iso_utc
    return iso_utc(d) if d is not None else None


def _days(a, b) -> Optional[float]:
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() / 86400.0, 1)


def summary(db, org_id: str, deal, now: Optional[datetime] = None) -> Dict[str, Any]:
    from app.models.wholesale_models import (WholesaleApproval, WholesaleBuyer, WholesaleBuyerMatch,
                                             WholesaleBuyerOutreach)
    from app.models.wholesale_share_models import WholesaleShareLink, WholesaleShareView
    from app.routers.wholesale_buyers_router import _buyer_activity
    from app.services import wholesale_buyer_intel as BI
    now = now or datetime.utcnow()
    rows = (db.query(WholesaleBuyerOutreach)
            .filter(WholesaleBuyerOutreach.organization_id == org_id, WholesaleBuyerOutreach.deal_id == deal.id)
            .order_by(WholesaleBuyerOutreach.created_at.asc()).all())
    buyers = {b.id: b for b in db.query(WholesaleBuyer).filter(
        WholesaleBuyer.organization_id == org_id,
        WholesaleBuyer.id.in_([r.buyer_id for r in rows] or ["-"])).all()}
    activity = _buyer_activity(db, org_id, list(buyers))
    matched = (db.query(WholesaleBuyerMatch)
               .filter(WholesaleBuyerMatch.organization_id == org_id, WholesaleBuyerMatch.deal_id == deal.id,
                       WholesaleBuyerMatch.disqualified.isnot(True)).count())
    links = {l.id: l for l in db.query(WholesaleShareLink).filter(
        WholesaleShareLink.organization_id == org_id, WholesaleShareLink.deal_id == deal.id).all()}
    views = (db.query(WholesaleShareView)
             .filter(WholesaleShareView.organization_id == org_id, WholesaleShareView.deal_id == deal.id).all())
    viewed_buyers = {getattr(links.get(v.share_link_id), "buyer_id", None) for v in views if v.action == "view"} - {None}
    pdfs = sum(1 for v in views if v.action == "pdf_downloaded")

    sent = [r for r in rows if r.sent_at is not None]
    replied = [r for r in rows if r.replied_at is not None or r.status in (
        "replied", "interested", "needs_info", "offer_submitted", "selected", "passed", "rejected", "accepted",
        "requested_info")]
    offers = [r for r in rows if r.offer_amount is not None]
    funnel = [
        {"key": "matched", "label": "Matched buyers", "count": matched},
        {"key": "sent", "label": "Deal sheets sent", "count": len(sent)},
        {"key": "delivered", "label": "Delivered (provider confirmed)", "count": sum(1 for r in rows if r.delivered_at)},
        {"key": "opened", "label": "Opened (tracked opens only)", "count": sum(1 for r in rows if getattr(r, "opened_at", None)),
         "note": "Only counted when this deployment can observe an open; otherwise 0, not a guess."},
        {"key": "viewed", "label": "Viewed the investor page", "count": len(viewed_buyers)},
        {"key": "pdf", "label": "PDF downloads", "count": pdfs},
        {"key": "replied", "label": "Replied", "count": len(replied)},
        {"key": "offers", "label": "Offers", "count": len(offers)},
        {"key": "pof", "label": "Proof of funds verified", "count": sum(1 for r in offers if r.pof_status == "verified")},
    ]

    contract = _f(deal.contract_price)
    items = []
    for r in offers:
        b = buyers.get(r.buyer_id)
        amt = _f(r.offer_amount)
        tc = r.target_close_date
        items.append({
            "outreach_id": r.id, "buyer_id": r.buyer_id,
            "buyer": getattr(b, "company_name", None) or getattr(b, "contact_name", None) or "(unnamed buyer)",
            "offer": amt, "net": (amt - contract) if (amt is not None and contract is not None) else None,
            "financing": r.offer_financing, "pof_status": r.pof_status or "not_requested",
            "target_close_date": str(tc)[:10] if tc else None,
            "typical_close_days": getattr(b, "typical_close_days", None),
            "tier": BI.tier(b, activity.get(r.buyer_id))["label"] if b is not None else None,
            "selected": bool(r.is_selected), "status": r.status,
        })
    highlights = []
    if items:
        top = max(items, key=lambda x: x["offer"] or 0)
        highlights.append({"what": "Highest offer", "buyer": top["buyer"], "value": top["offer"]})
        if contract is not None:
            highlights.append({"what": "Most to you after the contract price", "buyer": top["buyer"], "value": top["net"]})
        dated = [x for x in items if x["target_close_date"]]
        if dated:
            soon = min(dated, key=lambda x: x["target_close_date"])
            highlights.append({"what": "Soonest proposed closing", "buyer": soon["buyer"], "value": soon["target_close_date"]})
        verified = [x for x in items if x["pof_status"] == "verified"]
        highlights.append({"what": "Offers with verified proof of funds", "buyer": ", ".join(x["buyer"] for x in verified) or "none yet",
                           "value": len(verified)})

    follow = []
    for r in sent:
        b = buyers.get(r.buyer_id)
        name = getattr(b, "company_name", None) or getattr(b, "contact_name", None) or "buyer"
        quiet = r not in replied and r.status not in ("passed", "rejected")
        age = _days(r.sent_at, now)
        if quiet and r.buyer_id in viewed_buyers:
            follow.append({"buyer": name, "outreach_id": r.id, "why": "Opened the investor page but has not answered",
                           "since": _iso(r.sent_at), "priority": 1})
        elif quiet and age is not None and age >= NO_REPLY_DAYS:
            follow.append({"buyer": name, "outreach_id": r.id, "why": "No reply %d days after the deal sheet" % int(age),
                           "since": _iso(r.sent_at), "priority": 2})
    for x in items:
        if x["pof_status"] != "verified" and not x["status"] in ("passed", "rejected"):
            follow.append({"buyer": x["buyer"], "outreach_id": x["outreach_id"],
                           "why": "Offer of $%s - proof of funds %s" % (format(int(x["offer"] or 0), ","),
                                                                      x["pof_status"].replace("_", " ")),
                           "since": None, "priority": 1})
    for field, label in (("inspection_deadline", "Inspection deadline"), ("closing_deadline", "Closing deadline"),
                         ("close_of_escrow_target", "Target closing")):
        d = getattr(deal, field, None)
        if d is not None and deal.closed_at is None:
            dd = d if isinstance(d, datetime) else datetime.combine(d, datetime.min.time())
            left = _days(now, dd)
            if left is not None and left <= DEADLINE_DAYS:
                follow.append({"buyer": None, "outreach_id": None, "priority": 0,
                               "why": "%s %s" % (label, "passed %d day(s) ago" % int(-left) if left < 0 else "in %d day(s)" % max(0, int(left))),
                               "since": _iso(dd)})
    follow.sort(key=lambda x: x["priority"])

    apps = {}
    for a in db.query(WholesaleApproval).filter(WholesaleApproval.organization_id == org_id,
                                                WholesaleApproval.deal_id == deal.id).all():
        if a.status == "approved":
            apps[a.kind] = a
    checklist = [
        ("Contract signed with the seller", deal.contract_status == "signed"),
        ("Contract approved", "contract" in apps),
        ("Deal package approved", "package" in apps),
        ("Buyer selected", deal.assigned_buyer_id is not None),
        ("Buyer price and assignment fee set", deal.buyer_price is not None and deal.assignment_fee is not None),
        ("Assignment approved", "assignment" in apps),
        ("Title company opened", bool(deal.title_company)),
        ("Title clear", (deal.title_status or "") in ("clear", "clear_to_close", "closing_scheduled", "closed")),
        ("Buyer's earnest money received", getattr(deal, "earnest_money_received_at", None) is not None),
        ("Closing date set", getattr(deal, "closing_date", None) is not None),
        ("Closed", deal.closed_at is not None),
        ("Fee collected", deal.wholesale_fee_collected is not None),
    ]
    steps = [{"label": l, "done": bool(d)} for l, d in checklist]
    nxt = next((s["label"] for s in steps if not s["done"]), None)
    return {"deal_id": deal.id, "contract_price": contract, "funnel": funnel, "offers": items,
            "highlights": highlights, "follow_ups": follow,
            "closing_checklist": {"steps": steps, "done": sum(1 for s in steps if s["done"]), "total": len(steps),
                                  "next": nxt},
            "rules": {"no_reply_days": NO_REPLY_DAYS, "deadline_days": DEADLINE_DAYS}}


def analytics(db, org_id: str, include_test: bool = False, days: int = 180,
              now: Optional[datetime] = None) -> Dict[str, Any]:
    from app.models.wholesale_models import WholesaleBuyerOutreach, WholesaleDeal
    now = now or datetime.utcnow()
    since = now - timedelta(days=days)
    q = db.query(WholesaleBuyerOutreach).filter(WholesaleBuyerOutreach.organization_id == org_id,
                                                WholesaleBuyerOutreach.created_at >= since)
    rows = q.all()
    deal_ids = {r.deal_id for r in rows}
    deals = {d.id: d for d in db.query(WholesaleDeal).filter(WholesaleDeal.id.in_(deal_ids or {"-"})).all()}
    if not include_test:
        deals = {k: v for k, v in deals.items() if not v.is_test}
        rows = [r for r in rows if r.deal_id in deals]
    sent = [r for r in rows if r.sent_at is not None]
    replied = [r for r in sent if r.replied_at is not None]
    offers = [r for r in rows if r.offer_amount is not None]
    first_offer: Dict[str, Any] = {}
    first_sent: Dict[str, Any] = {}
    for r in sent:
        if r.deal_id not in first_sent or r.sent_at < first_sent[r.deal_id]:
            first_sent[r.deal_id] = r.sent_at
    for r in offers:
        t = r.replied_at
        if t is not None and (r.deal_id not in first_offer or t < first_offer[r.deal_id]):
            first_offer[r.deal_id] = t

    def avg(vals):
        vals = [v for v in vals if v is not None and v >= 0]
        return {"value": round(sum(vals) / len(vals), 1) if vals else None, "n": len(vals)}

    closed = [d for d in deals.values() if d.closed_at is not None]
    channels: Dict[str, Dict[str, int]] = {}
    for r in sent:
        c = channels.setdefault(r.channel or "unknown", {"sent": 0, "replied": 0, "offers": 0})
        c["sent"] += 1
        c["replied"] += 1 if r.replied_at is not None else 0
        c["offers"] += 1 if r.offer_amount is not None else 0
    return {
        "window_days": days, "include_test": include_test,
        "deals_marketed": len({r.deal_id for r in sent}),
        "deal_sheets_sent": len(sent),
        "reply_rate": {"value": round(len(replied) / len(sent), 3) if sent else None, "n": len(sent)},
        "offers": len(offers),
        "offers_per_marketed_deal": {"value": round(len(offers) / len({r.deal_id for r in sent}), 2) if sent else None,
                                     "n": len({r.deal_id for r in sent})},
        "days_sent_to_first_offer": avg([_days(first_sent.get(k), v) for k, v in first_offer.items()]),
        "days_contract_to_buyer": avg([_days(d.contract_signed_at, d.buyer_selected_at) for d in deals.values()]),
        "days_buyer_to_close": avg([_days(d.buyer_selected_at, d.closed_at) for d in closed]),
        "deals_closed": len(closed),
        "fees_collected": round(sum(_f(d.wholesale_fee_collected) or 0 for d in closed), 2),
        "average_assignment_fee": avg([_f(d.assignment_fee) for d in closed]),
        "by_channel": channels,
        "note": "Counts of rows that exist in this window. An average shows how many deals it is based on (n).",
    }
