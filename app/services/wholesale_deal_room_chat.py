"""Deal Room conversations: the AI investment concierge, investor <-> staff
threads, human takeover and return to AI (#415).

ONE CONVERSATION STORE. Threads are rows of the existing
`wholesale_buyer_conversations` (the #413 qualifier's table) with a `kind`
(concierge | relationship | deal). Every turn is kept in order:

    ai / buyer / staff   shown to the investor
    system               shown only when marked public
    internal             STAFF-ONLY notes - never serialized for an investor

WHAT THE CONCIERGE MAY SAY. It answers from the investor's OWN authorized
records only: the opportunities visible to them (through the same visibility
rule the opportunity API uses), the fields published for those properties, their
profile, their documents and signatures. A question it cannot answer from those
records is answered "not on record" and passed to the team - it never invents a
property fact, a timeline or a number. Learning the buy box reuses the #413
reader (rules, or the AI gateway when it is switched on); everything learned is
BUYER STATED until the investor confirms it in their profile.

Nothing here sends an SMS, email or call. In-app messages only.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.models import User
from app.models.wholesale_buyer_engine_models import CONV_HANDOFF, CONV_OPEN, WholesaleBuyerConversation
from app.services import wholesale_buyer_qualify as Q
from app.services import wholesale_deal_room as DR

PORTAL_KINDS = ("concierge", "relationship", "deal")
_HUMAN = re.compile(r"\b(human|real person|a person|someone|somebody|agent|representative|michael|schlueter|"
                    r"simmons|speak (to|with)|talk (to|with)|call me)\b", re.I)
_QUESTION = re.compile(r"\?\s*$|^\s*(what|which|when|how|do|does|did|can|could|is|are|where|why|who|will|would|"
                       r"should|any)\b", re.I)
_DEALS = re.compile(r"\b(deals?|opportunit\w*|propert\w*|listings?|available|for me|inventory)\b", re.I)
_TIME = re.compile(r"\b(when|deadline|closing|close|timeline|how long|expire\w*|countdown|time left|due)\b", re.I)
_DOCS = re.compile(r"\b(documents?|paperwork|send you|proof of funds|pof|upload|sign\w*|contract|disclosure)\b", re.I)
_PROFILE = re.compile(r"\b(my profile|buy ?box|criteria|what do you know|my preferences|what i buy)\b", re.I)
_NEXT = re.compile(r"\b(next steps?|what now|what should i|what do i do)\b", re.I)
_FACT = re.compile(r"\b(price|asking|bed\w*|bath\w*|sq\.? ?ft|square feet|units?|rent\w*|arv|repairs?|roof|"
                   r"year built|lot|acres?|occupan\w*|taxes|hoa|cap rate|noi|comps?|condition)\b", re.I)


def _now():
    return datetime.utcnow()


def _jl(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _turn(c, role: str, text: str, **extra) -> Dict[str, Any]:
    tr = _jl(c.transcript, [])
    t = {"role": role, "text": text, "at": _now().isoformat() + "Z", **extra}
    tr.append(t)
    c.transcript = json.dumps(tr)
    c.last_message_at = _now()
    return t


def buyer_turns(c) -> List[Dict[str, Any]]:
    out = []
    for t in _jl(c.transcript, []):
        role = t.get("role")
        if role in ("ai", "buyer", "staff") or (role == "system" and t.get("public")):
            out.append({"role": role, "text": t.get("text"), "at": t.get("at"), "by": t.get("by_name"),
                        "attachments": t.get("attachments") or [],
                        "engine": t.get("engine") if role == "ai" else None})
    return out


def thread_json(db, c, *, for_buyer: bool) -> Dict[str, Any]:
    base = {"id": c.id, "kind": c.kind, "deal_listing_id": None, "status": c.status,
            "ai_active": bool(c.ai_enabled) and c.status == CONV_OPEN,
            "unread": c.buyer_unread if for_buyer else c.staff_unread,
            "last_message_at": DR._iso(c.last_message_at)}
    if c.deal_id:
        lst = db.query(DR.DealListing).filter(DR.DealListing.deal_id == c.deal_id).first()
        base["deal_listing_id"] = getattr(lst, "id", None)
    if for_buyer:
        base["messages"] = buyer_turns(c)
        return base
    from app.services.wholesale_buyer_engine import conv_json
    return {**base, **conv_json(c), "unread": c.staff_unread, "buyer_id": c.buyer_id, "account_id": c.account_id,
            "assigned_user_id": c.assigned_user_id, "ai_enabled": bool(c.ai_enabled)}


# ── investor side ──────────────────────────────────────────────────────────

def threads(db, acct) -> List[WholesaleBuyerConversation]:
    return (db.query(WholesaleBuyerConversation)
            .filter(WholesaleBuyerConversation.organization_id == acct.organization_id,
                    WholesaleBuyerConversation.account_id == acct.id,
                    WholesaleBuyerConversation.kind.in_(PORTAL_KINDS))
            .order_by(WholesaleBuyerConversation.last_message_at.desc()).all())


def open_thread(db, acct, kind: str, listing_id: Optional[str] = None) -> WholesaleBuyerConversation:
    if kind not in PORTAL_KINDS:
        raise HTTPException(status_code=400, detail="kind must be concierge, relationship or deal")
    b = DR.buyer_of(db, acct)
    deal_id = None
    if kind == "deal":
        if not listing_id:
            raise HTTPException(status_code=400, detail="Which opportunity?")
        lst, deal, _m, _h = DR.get_visible(db, acct, listing_id)
        deal_id = deal.id
    q = db.query(WholesaleBuyerConversation).filter(
        WholesaleBuyerConversation.organization_id == acct.organization_id,
        WholesaleBuyerConversation.account_id == acct.id, WholesaleBuyerConversation.kind == kind)
    if deal_id:
        q = q.filter(WholesaleBuyerConversation.deal_id == deal_id)
    c = q.first()
    if c is not None:
        return c
    c = WholesaleBuyerConversation(organization_id=acct.organization_id, buyer_id=b.id, mode="portal",
                                   channel="deal_room", status=CONV_OPEN, kind=kind, deal_id=deal_id,
                                   account_id=acct.id, ai_enabled=(kind != "relationship"),
                                   assigned_user_id=acct.assigned_user_id, transcript="[]", answers="{}",
                                   objections="[]", commitments="[]", is_test=bool(b.is_test))
    db.add(c)
    db.flush()
    name = acct.preferred_name or (acct.legal_name or "").split(" ")[0] or "there"
    if kind == "concierge":
        nq = Q.next_question(b, {})
        _turn(c, "ai", "Welcome to your Deal Room, %s. I'm your investment concierge - I'll learn what you buy so "
                       "the right opportunities reach you, and I can answer questions about the deals shared with "
                       "you. You can ask for a person at any time.%s" % (
                           name, (" To start: %s" % nq[1]) if nq else ""), field=nq[0] if nq else None)
        c.buyer_unread = 1
    elif kind == "deal":
        _turn(c, "ai", "Questions about this property? Ask here - I'll answer from what's published for it, and "
                       "pass anything else to the team.")
    else:
        _turn(c, "system", "Your direct line to the team. A person answers here.", public=True)
    db.flush()
    return c


def get_thread(db, acct, conv_id: str) -> WholesaleBuyerConversation:
    c = db.query(WholesaleBuyerConversation).filter(
        WholesaleBuyerConversation.id == conv_id, WholesaleBuyerConversation.account_id == acct.id,
        WholesaleBuyerConversation.organization_id == acct.organization_id,
        WholesaleBuyerConversation.kind.in_(PORTAL_KINDS)).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if c.deal_id:                       # a deal thread follows the deal's visibility
        lst = db.query(DR.DealListing).filter(DR.DealListing.deal_id == c.deal_id).first()
        if lst is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        DR.get_visible(db, acct, lst.id)
    return c


def mark_read(db, acct, c) -> None:
    c.buyer_unread = 0
    db.flush()


def post(db, acct, conv_id: str, text: str, attachment_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    c = get_thread(db, acct, conv_id)
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Type a message.")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="Keep a message under 4,000 characters.")
    files = []
    for fid in attachment_ids or []:
        f = DR.investor_file(db, acct, fid)          # only their own files can be attached
        files.append({"id": f.id, "name": f.original_filename})
    b = DR.buyer_of(db, acct)
    _turn(c, "buyer", text, attachments=files, by_name=acct.preferred_name or acct.legal_name)
    c.staff_unread = (c.staff_unread or 0) + 1
    b.last_interaction_at = _now()
    reply = None
    if c.ai_enabled and c.status == CONV_OPEN and c.kind in ("concierge", "deal"):
        reply = concierge(db, acct, c, text)
    else:
        DR.notify_staff(db, acct.organization_id, "Deal Room message from %s" % (b.company_name or b.contact_name),
                        "/wholesale/deal-room?view=messages&thread=%s" % c.id, assignee_id=c.assigned_user_id,
                        event="message")
    c.buyer_unread = 0
    db.flush()
    return {"thread": thread_json(db, c, for_buyer=True), "reply": reply}


def _say(c, text: str, engine: str = "automatic", **extra) -> str:
    """An AI-side turn, labelled truthfully: engine "ai" when the model wrote or
    read it, "automatic" when the rules answered (AI off, unavailable or capped)."""
    _turn(c, "ai", text, engine=engine, **extra)
    return text


# ── the AI path for investor-initiated conversations ───────────────────────
#
# AUTHORIZATION. The investor is an authenticated platform user (role investor,
# fenced to /investor/*). Their in-app message is a MANUAL action they took, so
# the gateway's manual switch (AI_MANUAL_ACTIONS_ENABLED) governs it and the
# actor is named ("investor:<user id>") - the same rule every staff action
# follows. Nothing here grants staff permissions: the model only ever sees the
# CONTEXT built below, which is assembled through the investor's own visibility
# rules (the same functions their opportunity pages use).
#
# SPEND. On top of the gateway's own switches, each investor account and each
# workspace has a per-process ceiling (DEAL_ROOM_AI_PER_HOUR, default 30 per
# account per hour; DEAL_ROOM_AI_PER_ORG_DAY, default 400 per workspace per day).
# Past either ceiling the rules answer and say so.

_AI_LOCK = threading.Lock()
_AI_HITS: Dict[str, deque] = {}


def _get_client():
    """Test seam (returns None in production so the gateway's client is used)."""
    return None


def _ai_capacity(acct) -> Optional[str]:
    per_hour = int(os.environ.get("DEAL_ROOM_AI_PER_HOUR", "30") or 30)
    per_day = int(os.environ.get("DEAL_ROOM_AI_PER_ORG_DAY", "400") or 400)
    now = time.time()
    with _AI_LOCK:
        a = _AI_HITS.setdefault("acct:" + acct.id, deque())
        o = _AI_HITS.setdefault("org:" + acct.organization_id, deque())
        while a and a[0] < now - 3600:
            a.popleft()
        while o and o[0] < now - 86400:
            o.popleft()
        if len(a) >= per_hour:
            return "hourly limit for this investor"
        if len(o) >= per_day:
            return "daily limit for this Deal Room"
        a.append(now)
        o.append(now)
    return None


def _actor(acct) -> str:
    return "investor:%s" % acct.user_id


def authorized_context(db, acct, c) -> Dict[str, Any]:
    """Everything the concierge may draw on - and nothing else."""
    from app.services import wholesale_deal_room_setup as SET
    from app.services import wholesale_publication as P
    p = DR.profile(db, acct)
    boxes = []
    for bx in p["buy_boxes"]:
        boxes.append({k: {"value": v["value"], "label": v["label"]} for k, v in bx["fields"].items()
                      if v["value"] not in (None, [], "")})
    opps = []
    for lst, deal, m, hold in DR.visible_listings(db, acct):
        pay = P.buyer_room_payload(db, deal)
        hidden = set(DR._j(lst.hidden_document_ids, []))
        a = pay.get("property") or {}
        o = {"listing_id": lst.id, "address": ", ".join(x for x in (a.get("line1"), a.get("city"), a.get("state"))
                                                         if x),
             "county": a.get("county"), "property_type": a.get("property_type"),
             "bedrooms": a.get("bedrooms"), "bathrooms": a.get("bathrooms"), "square_feet": a.get("square_feet"),
             "year_built": a.get("year_built"), "lot_size_sqft": a.get("lot_size_sqft"),
             "occupancy": a.get("occupancy_status"), "asking_price": pay.get("asking_price"),
             "summary": pay.get("summary"), "condition": pay.get("condition_summary"),
             "closing": pay.get("closing"), "match_score": m.get("score"),
             "documents": [d.get("title") for d in pay.get("documents") or [] if d.get("id") not in hidden],
             "listing_closes": DR._iso(lst.expires_at),
             "your_exclusive_review_ends": DR._iso(hold.ends_at) if hold is not None else None}
        if "arv" in pay:
            o["estimated_after_repair_value"] = pay["arv"]
        if "estimated_repairs" in pay:
            o["estimated_repairs"] = pay["estimated_repairs"]
        opps.append({k: v for k, v in o.items() if v not in (None, "", [], {})})
    cur = None
    if c.deal_id:
        lst = db.query(DR.DealListing).filter(DR.DealListing.deal_id == c.deal_id).first()
        cur = getattr(lst, "id", None)
    sign = [d["title"] for d in DR.investor_documents(db, acct) if d["your_turn"]]
    room = SET.investor_view(db, acct.organization_id)
    return {"deal_room": {"name": room.get("name"), "support_email": room.get("support_email"),
                          "support_phone": room.get("support_phone")},
            "investor": {"name": p["account"].get("preferred_name") or p["account"].get("legal_name"),
                         "company": p["account"].get("company_legal_name"), "buy_boxes": boxes,
                         "funding": {"stated": p["funding"]["stated"], "documented": p["funding"]["documented"]}},
            "opportunities_visible_to_you": opps, "this_conversation_is_about_listing": cur,
            "documents_waiting_for_your_signature": sign,
            "exclusive_review_rule": DR.HOLD_NOTICE}


_SYSTEM_QA = (
    "You are the investment concierge inside a private real-estate Deal Room. Answer the investor's question "
    "using ONLY the JSON under CONTEXT. Never invent or estimate prices, values, rents, returns, cap rates, "
    "availability, dates, legal rights or financing terms - if CONTEXT does not contain the answer, set grounded "
    "to false and answer with an empty string. An exclusive review is a temporary visibility hold, never a "
    "contract, option or right to purchase. Do not give legal, tax or financing advice. Keep the answer under "
    "90 words, plain and friendly, and quote figures exactly as they appear in CONTEXT. Reply as JSON: "
    '{"answer": string, "grounded": boolean, "wants_person": boolean}')

_NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


def _numbers_supported(answer: str, ctx_text: str) -> bool:
    """Every figure in the answer must appear in the context (digits compared)."""
    digits_ctx = re.sub(r"[^0-9.]", " ", ctx_text)
    pool = {x.rstrip(".").split(".")[0] for x in digits_ctx.split() if x.strip(".")}
    for tok in _NUM.findall(answer or ""):
        d = re.sub(r"[^0-9.]", "", tok).rstrip(".").split(".")[0]
        if len(d) <= 1:
            continue                      # "1 property", "3 bedrooms" style small counts are allowed
        if d not in pool:
            return False
    return True


def ai_answer(db, acct, c, text: str) -> Optional[Dict[str, Any]]:
    """None when the AI could not be used (switched off, capped, unavailable)."""
    from app.services import ai_gateway
    from app.services import wholesale_deal_room_setup as SET
    if not SET.ai_enabled(db, acct.organization_id):
        return None
    if _ai_capacity(acct):
        return None
    ctx = authorized_context(db, acct, c)
    ctx_text = json.dumps(ctx, default=str)
    try:
        resp = ai_gateway.chat_completion(
            feature="deal_room_concierge", capability="wholesale_buyer_qualify", mode="manual",
            actor=_actor(acct), org_id=acct.organization_id, client=_get_client(), temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _SYSTEM_QA},
                      {"role": "system", "content": "CONTEXT: " + ctx_text[:24000]},
                      {"role": "user", "content": (text or "")[:2000]}])
        out = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001 - refusal / provider error: the rules answer
        import logging
        logging.getLogger("deal_room").info("concierge AI unavailable (%s); rules answered", type(exc).__name__)
        return None
    if not isinstance(out, dict):
        return None
    answer = str(out.get("answer") or "").strip()[:1200]
    grounded = bool(out.get("grounded")) and bool(answer) and _numbers_supported(answer, ctx_text)
    return {"answer": answer if grounded else "", "grounded": grounded, "wants_person": bool(out.get("wants_person"))}


def _handoff(db, acct, c, reason: str) -> str:
    from app.services import wholesale_exceptions as EX
    b = DR.buyer_of(db, acct)
    ex, _ = EX.raise_exception(db, acct.organization_id, kind="buyer_handoff", subject_type="buyer",
                               subject_id=b.id, title="Deal Room: %s asked for a person" % (b.company_name or b.contact_name),
                               detail=reason[:500], priority=20, source="deal_room", is_test=bool(b.is_test),
                               assigned_to_id=c.assigned_user_id)
    c.ai_enabled = False
    c.status, c.handoff_reason, c.handoff_exception_id = CONV_HANDOFF, reason[:250], ex.id
    _turn(c, "system", "Handed to the team: %s" % reason)
    DR.notify_staff(db, acct.organization_id, "Deal Room: %s asked for a person" % (b.company_name or b.contact_name),
                    "/wholesale/deal-room?view=messages&thread=%s" % c.id, assignee_id=c.assigned_user_id,
                    event="handoff")
    return _say(c, "I've asked the team to join this conversation - they'll reply right here. I'll stay quiet so "
                   "you don't get two answers.")


def _money(v) -> str:
    return "$%s" % format(int(round(float(v))), ",") if v not in (None, "") else "not published"


def _listings_answer(db, acct, c) -> str:
    rows = DR.visible_listings(db, acct)
    if not rows:
        return "There are no opportunities shared with you right now. When one matches your buy box and the team " \
               "publishes it, it appears on your dashboard."
    from app.services import wholesale_publication as P
    parts = []
    for lst, deal, m, hold in rows[:6]:
        pay = P.buyer_room_payload(db, deal)
        a = pay.get("property") or {}
        addr = ", ".join(x for x in (a.get("line1"), a.get("city")) if x) or "an opportunity"
        parts.append("%s - asking %s%s%s" % (addr, _money(pay.get("asking_price")),
                                            (", match %d" % m["score"]) if m.get("score") is not None else "",
                                            " (in your exclusive review)" if hold is not None else ""))
    return "You have %d opportunit%s: %s." % (len(rows), "y" if len(rows) == 1 else "ies", "; ".join(parts))


def _deal_payload(db, acct, c):
    if not c.deal_id:
        return None, None
    lst = db.query(DR.DealListing).filter(DR.DealListing.deal_id == c.deal_id).first()
    d = DR.opportunity_detail(db, acct, lst.id)
    return d, lst


def _time_answer(db, acct, c) -> str:
    d, _ = _deal_payload(db, acct, c)
    if d is None:
        lines = []
        for lst, deal, m, hold in DR.visible_listings(db, acct):
            if hold is not None:
                lines.append("your exclusive review ends %s UTC" % hold.ends_at.strftime("%b %d %H:%M"))
            if lst.expires_at:
                lines.append("a listing closes to new interest on %s" % lst.expires_at.strftime("%b %d"))
        return ("On record: " + "; ".join(lines) + ".") if lines else \
            "No deadlines are on record for your opportunities right now. Open a property to see its closing details."
    room = d["room"]
    cl = room.get("closing") or {}
    bits = []
    if cl.get("target_close") or cl.get("closing_date"):
        bits.append("target closing %s" % (cl.get("closing_date") or cl.get("target_close")))
    if d.get("hold") and d["hold"].get("ends_at"):
        bits.append("your exclusive review ends %s (UTC)" % d["hold"]["ends_at"][:16].replace("T", " "))
    if d.get("expires_at"):
        bits.append("the listing closes to new interest on %s" % d["expires_at"][:10])
    return ("For this property: " + "; ".join(bits) + ".") if bits else \
        "No closing date or deadline is on record for this property yet - I've asked the team."


def _docs_answer(db, acct) -> str:
    p = DR.profile(db, acct)
    docs = DR.investor_documents(db, acct)
    todo = [d for d in docs if d["your_turn"]]
    f = p["funding"]
    lines = []
    if not f["documented"]:
        lines.append("proof of funds - none current on file (upload it under Documents)")
    else:
        lines.append("proof of funds on file%s" % (" until %s" % f["pof_expires"][:10] if f.get("pof_expires") else ""))
    if not p["account"]["identity_complete"]:
        lines.append("your identity details (Profile)")
    if todo:
        lines.append("%d document%s waiting for your signature" % (len(todo), "" if len(todo) == 1 else "s"))
    return "Here's what's on record: " + "; ".join(lines) + "."


def _profile_answer(db, acct) -> str:
    p = DR.profile(db, acct)
    if not p["buy_boxes"]:
        return "You haven't told us what you buy yet. Tell me the property types, areas and price range you want, " \
               "or fill it in under Profile."
    parts = []
    for bx in p["buy_boxes"]:
        fl = bx["fields"]
        txt = ", ".join(x for x in (
            "/".join(fl["property_types"]["value"]) if fl["property_types"]["value"] else None,
            ", ".join((fl["counties"]["value"] or []) + (fl["cities"]["value"] or [])) or None,
            ("%s-%s" % (_money(fl["min_price"]["value"]) if fl["min_price"]["value"] else "?",
                        _money(fl["max_price"]["value"]) if fl["max_price"]["value"] else "?"))
            if (fl["min_price"]["value"] or fl["max_price"]["value"]) else None) if x)
        parts.append("%s (%s%s)" % (txt or "no criteria yet",
                                    "confirmed by you" if bx["confirmed_at"] else (bx["source"] or "stated"),
                                    "" if bx["source"] != "county_records" else " - inferred from public records"))
    fund = p["funding"]
    return ("Your buy box: %s. Funding: %s%s. You can review and correct everything under Profile."
            % ("; ".join(parts), fund["stated"] or "not stated",
               " (documented)" if fund["documented"] else " (stated, not yet documented)"))


def _next_answer(db, acct) -> str:
    p = DR.profile(db, acct)
    if not p["account"]["identity_complete"]:
        return "Next: complete your profile (individual or business, and the legal name you buy under)."
    if not any(b["confirmed_at"] for b in p["buy_boxes"]):
        return "Next: confirm your buy box under Profile so the team matches you to the right deals."
    if not p["funding"]["documented"]:
        return "Next: upload a current proof of funds under Documents - it lets the team move faster on a deal."
    return "Next: review the opportunities on your dashboard. Ask me anything about them."


def _fact_answer(db, acct, c, text: str) -> Optional[str]:
    d, _ = _deal_payload(db, acct, c)
    if d is None:
        return None
    room = d["room"]
    low = text.lower()
    facts = {"asking": ("asking price", _money(room.get("asking_price")) if room.get("asking_price") else None),
             "price": ("asking price", _money(room.get("asking_price")) if room.get("asking_price") else None),
             "arv": ("ARV", _money(room["arv"]) if room.get("arv") else None),
             "repair": ("estimated repairs", _money(room["estimated_repairs"]) if room.get("estimated_repairs") else None),
             "condition": ("condition", room.get("condition_summary"))}
    for k in ("bedrooms", "bathrooms", "square_feet", "year_built", "unit_count", "acreage", "building_sqft"):
        v = (d.get("facts") or {}).get(k)
        facts[k.split("_")[0][:4]] = (k.replace("_", " "), v)
    for key, (label, val) in facts.items():
        if key in low and val not in (None, ""):
            return "The %s on record for this property is %s." % (label, val)
    return None


def _ai_ok(db, acct) -> bool:
    from app.services import wholesale_deal_room_setup as SET
    return SET.ai_enabled(db, acct.organization_id) and _ai_capacity(acct) is None


def concierge(db, acct, c, text: str) -> str:
    b = DR.buyer_of(db, acct)
    r = Q.rules_read(text, context=Q._last_field(c) if hasattr(Q, "_last_field") else None)
    if r["stop"] == "opt_out":
        b.do_not_contact = True
        b.do_not_contact_reason = "Asked to stop (Deal Room chat, %s)" % _now().date()
        c.ai_enabled = False
        DR.notify_staff(db, acct.organization_id, "%s opted out of outreach in the Deal Room" %
                        (b.company_name or b.contact_name), "/wholesale/buyers?buyer=%s" % b.id)
        return _say(c, "Understood - you won't receive outreach messages from us. Your Deal Room stays available, "
                       "and you can message the team here whenever you like.")
    if _HUMAN.search(text):
        return _handoff(db, acct, c, "Asked for a person: \"%s\"" % text[:160])
    if _QUESTION.search(text):
        r = ai_answer(db, acct, c, text)
        if r is not None:
            if r["wants_person"] and not r["grounded"]:
                return _handoff(db, acct, c, "Asked for a person: \"%s\"" % text[:160])
            if r["grounded"]:
                return _say(c, r["answer"], engine="ai")
            DR.notify_staff(db, acct.organization_id, "Deal Room question: %s" % text[:120],
                            "/wholesale/deal-room?view=messages&thread=%s" % c.id, assignee_id=c.assigned_user_id,
                            event="message")
            return _say(c, "That isn't in the information I have for you, so I won't guess. I've passed your "
                           "question to the team - they'll answer here.", engine="ai")
        if c.kind == "deal" and _FACT.search(text):
            a = _fact_answer(db, acct, c, text)
            if a:
                return _say(c, a)
            DR.notify_staff(db, acct.organization_id, "Deal Room question: %s" % text[:120],
                            "/wholesale/deal-room?view=messages&thread=%s" % c.id, assignee_id=c.assigned_user_id)
            return _say(c, "That isn't in the information published for this property, so I won't guess. I've "
                           "passed your question to the team - they'll answer here.")
        if _TIME.search(text):
            return _say(c, _time_answer(db, acct, c))
        if _DOCS.search(text):
            return _say(c, _docs_answer(db, acct))
        if _PROFILE.search(text):
            return _say(c, _profile_answer(db, acct))
        if _NEXT.search(text):
            return _say(c, _next_answer(db, acct))
        if _DEALS.search(text):
            return _say(c, _listings_answer(db, acct, c))
        DR.notify_staff(db, acct.organization_id, "Deal Room question: %s" % text[:120],
                        "/wholesale/deal-room?view=messages&thread=%s" % c.id, assignee_id=c.assigned_user_id)
        return _say(c, "I don't have that on record, so I won't guess. I've passed your question to the team - "
                       "they'll answer here.")
    # a statement: learn what they buy (stated, never verified)
    if c.kind == "concierge":
        use_ai = _ai_ok(db, acct)
        res = Q.read(text, org_id=acct.organization_id, actor=_actor(acct) if use_ai else None,
                     context=Q._last_field(c))
        engine = "ai" if res.get("source") == "ai" else "automatic"
        if res["facts"]:
            Q._apply(db, acct.organization_id, b, c, res["facts"], res["source"], text, None)
        if res.get("objections"):
            c.objections = json.dumps(_jl(c.objections, []) + res["objections"])
        if res.get("commitments"):
            c.commitments = json.dumps(_jl(c.commitments, []) + res["commitments"])
        nq = Q.next_question(b, _jl(c.answers, {}))
        learned = ", ".join(k.replace("_", " ") for k in res["facts"]) if res["facts"] else None
        lead = ("Got it - I've noted your %s (as you stated it). " % learned) if learned else ""
        if nq:
            return _say(c, lead + nq[1], engine=engine, field=nq[0])
        acct.welcome_completed_at = acct.welcome_completed_at or _now()
        return _say(c, lead + "That's everything I need for now. Please review your buy box under My Criteria "
                              "and confirm it - then the team can match you precisely.", engine=engine)
    return _say(c, "Thanks - I've shared that with the team.")


# ── staff side ─────────────────────────────────────────────────────────────

def inbox(db, org_id: str, include_test: bool = False) -> List[Dict[str, Any]]:
    from app.models.wholesale_models import WholesaleBuyer
    rows = (db.query(WholesaleBuyerConversation)
            .filter(WholesaleBuyerConversation.organization_id == org_id,
                    WholesaleBuyerConversation.kind.in_(PORTAL_KINDS))
            .order_by(WholesaleBuyerConversation.last_message_at.desc()).limit(200).all())
    out = []
    for c in rows:
        if c.is_test and not include_test:
            continue
        b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == c.buyer_id).first()
        last = (_jl(c.transcript, []) or [{}])[-1]
        out.append({"id": c.id, "kind": c.kind, "buyer_id": c.buyer_id,
                    "buyer_name": getattr(b, "company_name", None) or getattr(b, "contact_name", None),
                    "status": c.status, "ai_enabled": bool(c.ai_enabled), "unread": c.staff_unread or 0,
                    "deal_id": c.deal_id, "last": (last.get("text") or "")[:140], "last_role": last.get("role"),
                    "last_message_at": DR._iso(c.last_message_at), "is_test": bool(c.is_test)})
    return out


def staff_thread(db, org_id: str, conv_id: str) -> WholesaleBuyerConversation:
    c = db.query(WholesaleBuyerConversation).filter(WholesaleBuyerConversation.id == conv_id,
                                                    WholesaleBuyerConversation.organization_id == org_id,
                                                    WholesaleBuyerConversation.kind.in_(PORTAL_KINDS)).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return c


def staff_reply(db, org_id: str, conv_id: str, text: str, user: User) -> WholesaleBuyerConversation:
    c = staff_thread(db, org_id, conv_id)
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Type a reply.")
    if c.ai_enabled:                 # a person answering IS a takeover: no conflicting AI replies
        c.ai_enabled = False
        _turn(c, "system", "%s joined the conversation." % (user.full_name or "A team member"), public=True)
    _turn(c, "staff", text[:4000], by=user.id, by_name=user.full_name or "Team")
    c.buyer_unread = (c.buyer_unread or 0) + 1
    c.staff_unread = 0
    c.assigned_user_id = c.assigned_user_id or user.id
    if c.status == CONV_HANDOFF:
        c.status = CONV_OPEN
    db.flush()
    return c


def internal_note(db, org_id: str, conv_id: str, text: str, user: User) -> WholesaleBuyerConversation:
    c = staff_thread(db, org_id, conv_id)
    _turn(c, "internal", (text or "").strip()[:4000], by=user.id, by_name=user.full_name or "Team")
    db.flush()
    return c


def takeover(db, org_id: str, conv_id: str, user: User) -> WholesaleBuyerConversation:
    c = staff_thread(db, org_id, conv_id)
    c.ai_enabled = False
    c.assigned_user_id = user.id
    if c.status == CONV_HANDOFF:
        c.status = CONV_OPEN
    _turn(c, "system", "%s took over from the AI concierge." % (user.full_name or "A team member"), public=True)
    db.flush()
    return c


def return_to_ai(db, org_id: str, conv_id: str, user: User) -> WholesaleBuyerConversation:
    from app.models.wholesale_models import WholesaleBuyer
    c = staff_thread(db, org_id, conv_id)
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == c.buyer_id).first()
    if c.kind == "relationship":
        raise HTTPException(status_code=409, detail="The direct line to the team has no AI.")
    if b is not None and b.do_not_contact:
        raise HTTPException(status_code=409, detail="This buyer opted out - the AI stays off.")
    c.ai_enabled, c.status = True, CONV_OPEN
    _turn(c, "system", "The AI concierge is back. Ask for a person any time.", public=True)
    db.flush()
    return c


def staff_read(db, org_id: str, conv_id: str) -> WholesaleBuyerConversation:
    c = staff_thread(db, org_id, conv_id)
    c.staff_unread = 0
    db.flush()
    return c
