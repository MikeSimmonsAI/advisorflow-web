"""Campaign Studio - promotions are real records; SENDING IS LOCKED.

What works today: create / edit / save drafts, choose segments, write SMS and
email templates, preview them with a real customer's first name, preview the
eligible recipients (with the reason each excluded customer is excluded),
schedule, pause, resume, cancel, owner approval, and results that count only
real rows (appointments and recorded transactions tied to the campaign).

What does NOT happen: nothing is ever sent from this module. `send_readiness`
lists every gate and `send` refuses while any is closed - and the platform
send path for this program is not connected yet, so it always refuses. A
scheduled campaign is stored as scheduled and labelled "on hold"; no job
picks it up. Delivery, reply and opt-out counts therefore read zero, and the
screen says why rather than showing an estimate.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.jewelry_models import JewelryAppointmentDetail, JewelryCampaign, JewelryItem, JewelryTransaction
from app.models.models import Lead, SuppressionEntry
from app.services.jewelry import common as C

TEMPLATES = {
    "gold_buying": {
        "sms": "Hi {first_name}, {business_name} is holding a Gold Buying event. Bring gold jewelry, coins or broken pieces for a free in-store evaluation. Reply to set a time.",
        "subject": "You're invited: Gold Buying at {business_name}",
        "email": "Hi {first_name},\n\nThinking about selling gold jewelry, coins or broken pieces? Bring them to {business_name} for a free, no-obligation in-store evaluation.\n\nReply to this email to reserve a time."},
    "reactivation": {
        "sms": "Hi {first_name}, it's been a while! {business_name} would love to see you again. Reply to set up a visit.",
        "subject": "We've missed you at {business_name}",
        "email": "Hi {first_name},\n\nIt's been a while since your last visit. We'd love to see you again - stop in or reply to set a time."},
    "jewelry_specials": {
        "sms": "Hi {first_name}, new jewelry is in at {business_name}. Reply to book a time to see it in person.",
        "subject": "New jewelry at {business_name}",
        "email": "Hi {first_name},\n\nNew pieces just arrived. Reply to book a private consultation."},
    "coin_alerts": {
        "sms": "Collector alert from {business_name}: new coin arrivals are in. Reply to set a viewing time.",
        "subject": "Collector alert: new coin arrivals",
        "email": "Hi {first_name},\n\nNew coin arrivals just came in. As a collector you hear first - reply to set a viewing time."},
    "seasonal": {
        "sms": "Hi {first_name}, seasonal specials are here at {business_name}. Reply to learn more.",
        "subject": "Seasonal specials at {business_name}",
        "email": "Hi {first_name},\n\nOur seasonal specials are here. Reply or stop in to see them."},
    "vip": {
        "sms": "{first_name}, as one of our VIP customers you're invited to a private preview at {business_name}. Reply to reserve your spot.",
        "subject": "Your private VIP invitation",
        "email": "Hi {first_name},\n\nThank you for being one of our most valued customers. You're invited to a private preview - reply to reserve your spot."},
    "custom": {"sms": "", "subject": "", "email": ""},
}
STOP_FOOTER = " Reply STOP to opt out."


def camp_json(c: JewelryCampaign, results: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"id": c.id, "name": c.name, "kind": c.kind, "segments": C.jload(c.segments, []),
            "channel": c.channel, "sms_body": c.sms_body, "email_subject": c.email_subject,
            "email_body": c.email_body, "status": c.status, "scheduled_for": C.iso(c.scheduled_for),
            "item_id": c.item_id, "marketing_cost": c.marketing_cost,
            "approved": bool(c.approved_at), "approved_at": C.iso(c.approved_at),
            "created_at": C.iso(c.created_at), "updated_at": C.iso(c.updated_at),
            "sending_locked": True, "results": results}


def _load(ctx: C.Ctx, cid: str) -> JewelryCampaign:
    c = ctx.db.query(JewelryCampaign).filter(JewelryCampaign.id == cid,
                                             JewelryCampaign.organization_id == ctx.org_id).first()
    if c is None:
        raise HTTPException(404, "Campaign not found")
    return c


def apply(ctx: C.Ctx, c: JewelryCampaign, data: Dict[str, Any]) -> None:
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(400, "Give the campaign a name")
        c.name = name[:160]
    if "kind" in data and data["kind"]:
        if data["kind"] not in C.CAMPAIGN_KIND_KEYS:
            raise HTTPException(400, "Unknown campaign type")
        c.kind = data["kind"]
    if "segments" in data:
        c.segments = C.jdump([s for s in (data["segments"] or []) if s in C.SEGMENT_KEYS])
    if "channel" in data and data["channel"]:
        if data["channel"] not in ("sms", "email", "both"):
            raise HTTPException(400, "Channel must be sms, email or both")
        c.channel = data["channel"]
    for k, lim in (("sms_body", 640), ("email_subject", 200), ("email_body", 8000)):
        if k in data:
            setattr(c, k, (data[k] or "").strip()[:lim] or None)
    if "marketing_cost" in data:
        v = data["marketing_cost"]
        if v in (None, ""):
            c.marketing_cost = None
        else:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, "Marketing cost must be a number")
            if v < 0 or v > 1_000_000:
                raise HTTPException(400, "Marketing cost must be between 0 and 1,000,000")
            c.marketing_cost = round(v, 2)
    if "item_id" in data:
        if data["item_id"] and not ctx.db.query(JewelryItem.id).filter(
                JewelryItem.id == data["item_id"], JewelryItem.organization_id == ctx.org_id).first():
            raise HTTPException(404, "Item not found")
        c.item_id = data["item_id"] or None
    if c.status not in ("draft", "paused"):
        # Editing a scheduled campaign puts it back to draft and clears approval.
        c.status = "draft"
    c.approved_at, c.approved_by = None, None


def create(ctx: C.Ctx, data: Dict[str, Any], business_name: str) -> JewelryCampaign:
    kind = data.get("kind") or "custom"
    if kind not in C.CAMPAIGN_KIND_KEYS:
        raise HTTPException(400, "Unknown campaign type")
    tpl = TEMPLATES.get(kind, TEMPLATES["custom"])
    fill = lambda s: (s or "").replace("{business_name}", business_name)   # noqa: E731
    c = JewelryCampaign(organization_id=ctx.org_id, name=(data.get("name") or "").strip()[:160] or
                        next(k["label"] for k in C.CAMPAIGN_KINDS if k["key"] == kind),
                        kind=kind, channel="sms", status="draft", created_by=ctx.user.id,
                        segments=C.jdump([]), sms_body=fill(tpl["sms"]) or None,
                        email_subject=fill(tpl["subject"]) or None, email_body=fill(tpl["email"]) or None)
    ctx.db.add(c)
    apply(ctx, c, {k: v for k, v in data.items() if k != "kind"})
    ctx.db.flush()
    return c


# ── recipients ─────────────────────────────────────────────────────────────
def recipients(ctx: C.Ctx, c: JewelryCampaign, sample: int = 25) -> Dict[str, Any]:
    """Who WOULD be eligible on each channel, and why everyone else is not.

    Eligible for text: has a valid mobile number, allow_sms and sms_consent are
    both recorded, not opted out, not on the suppression list.
    Eligible for email: valid email, allow_email recorded, not opted out.
    Imported permission is only what the file said, and flagged for review.
    """
    from app.services.jewelry import customers as CU
    segs = C.jload(c.segments, [])
    leads = ctx.db.query(Lead).filter(Lead.organization_id == ctx.org_id).all()
    act = CU.activity_index(ctx, [l.id for l in leads])
    suppressed = {p for (p,) in ctx.db.query(SuppressionEntry.phone)
                  .filter(SuppressionEntry.organization_id == ctx.org_id).all()}
    want_sms = c.channel in ("sms", "both")
    want_email = c.channel in ("email", "both")
    matched, eligible = [], []
    excluded = {"opted_out": 0, "suppressed": 0, "no_sms_permission": 0, "no_email_permission": 0,
                "no_phone": 0, "no_email": 0, "test_record": 0}
    for l in leads:
        if segs and not set(segs) & set(CU.segments_of(l, act[l.id])):
            continue
        matched.append(l)
        if l.is_test:
            excluded["test_record"] += 1
            continue
        if CU.opted_out(l):
            excluded["opted_out"] += 1
            continue
        ok_sms = ok_email = False
        if want_sms:
            if not l.phone:
                excluded["no_phone"] += 1
            elif l.phone in suppressed:
                excluded["suppressed"] += 1
            elif not (l.allow_sms and l.sms_consent):
                excluded["no_sms_permission"] += 1
            else:
                ok_sms = True
        if want_email:
            if not l.email:
                excluded["no_email"] += 1
            elif not l.allow_email:
                excluded["no_email_permission"] += 1
            else:
                ok_email = True
        if ok_sms or ok_email:
            eligible.append((l, ok_sms, ok_email))
    return {"segments": segs, "channel": c.channel, "matched": len(matched), "eligible": len(eligible),
            "eligible_sms": sum(1 for _, s, _e in eligible if s), "eligible_email": sum(1 for _, _s, e in eligible if e),
            "excluded": excluded,
            "sample": [{"id": l.id, "name": C.lead_name(l), "sms": s, "email": e} for l, s, e in eligible[:sample]]}


def render(text: Optional[str], first_name: Optional[str], business_name: str, sms: bool = False) -> str:
    out = (text or "").replace("{first_name}", (first_name or "there").strip() or "there") \
                      .replace("{business_name}", business_name)
    if sms and out and "stop" not in out.lower():
        out += STOP_FOOTER
    return out


# ── lifecycle ──────────────────────────────────────────────────────────────
def schedule(ctx: C.Ctx, c: JewelryCampaign, when: str) -> None:
    try:
        dt = datetime.fromisoformat((when or "").replace("Z", ""))
    except ValueError:
        raise HTTPException(400, "Pick a date and time")
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    if dt <= C.local_now(ctx.org):
        raise HTTPException(400, "Pick a time in the future")
    if not C.jload(c.segments, []):
        raise HTTPException(400, "Choose at least one customer segment first")
    if c.channel in ("sms", "both") and not (c.sms_body or "").strip():
        raise HTTPException(400, "Write the text message first")
    if c.channel in ("email", "both") and not ((c.email_subject or "").strip() and (c.email_body or "").strip()):
        raise HTTPException(400, "Write the email subject and message first")
    c.scheduled_for, c.status = dt, "scheduled"


def set_status(c: JewelryCampaign, action: str) -> None:
    allowed = {"pause": (("scheduled",), "paused"), "resume": (("paused",), "scheduled"),
               "cancel": (("draft", "scheduled", "paused"), "cancelled")}
    if action not in allowed:
        raise HTTPException(400, "Unknown action")
    frm, to = allowed[action]
    if c.status not in frm:
        raise HTTPException(409, "A %s campaign can't be %s." % (c.status, {"pause": "paused", "resume": "resumed",
                                                                        "cancel": "cancelled"}[action]))
    if action == "resume" and not c.scheduled_for:
        raise HTTPException(409, "Schedule it again first")
    c.status = to


def send_readiness(ctx: C.Ctx, c: Optional[JewelryCampaign] = None) -> Dict[str, Any]:
    from app.services.cadence_service import org_sending_enabled
    checks = [
        {"key": "program_sending", "label": "Campaign sending is connected for this program", "ok": False,
         "detail": "Not connected yet. Campaigns are saved, scheduled and reported, but nothing is sent from this workspace."},
        {"key": "owner_activation", "label": "The business owner authorized outreach and data use", "ok": False,
         "detail": "Recorded during owner onboarding."},
        {"key": "texts_switch", "label": "Automated texting switched on for this workspace",
         "ok": bool(org_sending_enabled(ctx.db, ctx.org_id)), "detail": "Only the platform owner can switch this on."},
        {"key": "provider", "label": "Messaging sender verified for this business", "ok": False,
         "detail": "A verified texting number / email sender for this business is required."},
    ]
    if c is not None:
        checks.append({"key": "approved", "label": "Campaign approved by the owner", "ok": bool(c.approved_at),
                       "detail": "An owner or admin approves each campaign."})
    return {"ready": all(x["ok"] for x in checks), "checks": checks}


def results(ctx: C.Ctx, c: JewelryCampaign) -> Dict[str, Any]:
    appts = ctx.db.query(JewelryAppointmentDetail).filter(JewelryAppointmentDetail.organization_id == ctx.org_id,
                                                          JewelryAppointmentDetail.campaign_id == c.id).all()
    txns = ctx.db.query(JewelryTransaction).filter(JewelryTransaction.organization_id == ctx.org_id,
                                                   JewelryTransaction.campaign_id == c.id).all()
    revenue = round(sum(float(t.amount or 0) for t in txns if t.kind in ("sale", "trade")), 2)
    return {"sent": 0, "delivered": 0, "replies": 0, "opt_outs": 0, "delivery_connected": False,
            "appointments": len(appts), "completed_visits": sum(1 for a in appts if a.status == "completed"),
            "transactions": len(txns), "recorded_revenue": revenue,
            "marketing_cost": c.marketing_cost}


def from_item(ctx: C.Ctx, item: JewelryItem, business_name: str) -> JewelryCampaign:
    kind = "coin_alerts" if item.category == "coins" else ("gold_buying" if item.category == "gold" else "jewelry_specials")
    seg = {"coins": ["coin_collector"], "gold": ["gold_seller", "jewelry_buyer"]}.get(item.category, ["jewelry_buyer"])
    c = create(ctx, {"kind": kind, "name": "New arrival: %s" % item.name[:120], "segments": seg,
                     "item_id": item.id,
                     "sms_body": "Hi {first_name}, just in at {business_name}: %s. Reply to set a time to see it." % item.name[:120],
                     "email_subject": "Just arrived: %s" % item.name[:150],
                     "email_body": "Hi {first_name},\n\nYou told us you're interested in pieces like this, so you're hearing first: %s just arrived.\n\nReply to set a time to see it in person." % item.name[:200]},
               business_name)
    c.sms_body = (c.sms_body or "").replace("{business_name}", business_name)
    return c
