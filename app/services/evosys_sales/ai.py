"""EvoSys Sales AI: suggestions a person reviews. Never decisions.

Through the platform's one AI door (app.services.ai_gateway): manual mode,
named after the person who pressed the button, existing capabilities and
approved models only. No new provider; no Grok or Harvey connection is used
or claimed here.

WHAT AI DOES HERE
  group_lead      suggest an industry + market for an unclear lead
  draft_campaign  draft a campaign's emails from its verified details
  reply_assist    summarise a reply that ALREADY stopped the follow-up (by
                  deterministic rules) and draft an answer

WHAT AI NEVER DOES: change an offer, approve or switch on a campaign, send,
override an opt-out, or mark a call booked. Every result is checked by rules
before it is shown (keys must be configured ones; quoted evidence must really
be in the record; a price may only come from the approved offer), and saved
with exactly the facts it was given. A failure is saved too, and the manual
workflow stays usable. The same request with the same inputs returns the saved
answer instead of asking again; prior saved work is never deleted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.models.evosys_sales_models import EvoSysAITask

log = logging.getLogger(__name__)
FEATURE = "evosys_sales"
CAPABILITY = {"group_lead": "lead_analysis", "draft_campaign": "campaign_copy", "reply_assist": "draft_reply",
              "text_reply": "draft_reply"}
TEXT_FEATURE = "evosys_sales_text_replies"
REPLY_CATEGORIES = ("interested", "pricing", "question", "not_interested", "wrong_person", "unsubscribe", "unclear")
MONEY_RE = re.compile(r"\$\s?\d[\d,]*(\.\d+)?|\b\d+\s?(dollars|usd)\b", re.I)


def _get_client():
    """Test seam: None in production (the gateway's own client)."""
    return None


def _hash(kind: str, facts: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps([kind, facts], sort_keys=True, default=str).encode()).hexdigest()


def latest(db: Session, kind: str, target_id: str) -> Optional[EvoSysAITask]:
    return (db.query(EvoSysAITask).filter(EvoSysAITask.kind == kind, EvoSysAITask.target_id == target_id)
            .order_by(EvoSysAITask.created_at.desc()).first())


def payload(t: Optional[EvoSysAITask]) -> Optional[Dict[str, Any]]:
    if t is None:
        return None
    return {"id": t.id, "kind": t.kind, "status": t.status, "output": t.output, "facts": t.facts,
            "error": t.error, "outcome": t.outcome, "at": t.created_at.isoformat() + "Z"}


def _ask(kind: str, user_id: str, org_id: str, system: str, facts: Dict[str, Any],
         background: bool = False) -> Dict[str, Any]:
    from app.services import ai_gateway
    kw = dict(capability=CAPABILITY[kind], actor=user_id, org_id=org_id,
              client=_get_client(), response_format={"type": "json_object"}, temperature=0.2,
              messages=[{"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(facts, default=str)}])
    if background:
        # A text came in with nobody at the keyboard: only this one feature,
        # only for this team, under every budget cap and breaker.
        with ai_gateway.scoped_grant(TEXT_FEATURE, org_id):
            resp = ai_gateway.chat_completion(feature=TEXT_FEATURE, mode=ai_gateway.BACKGROUND, **kw)
    else:
        resp = ai_gateway.chat_completion(feature=FEATURE, mode=ai_gateway.MANUAL, **kw)
    text = resp.choices[0].message.content or "{}"
    return json.loads(text)


def run(db: Session, *, kind: str, target_id: str, bso_id: str, user_id: str, facts: Dict[str, Any],
        system: str, check: Callable[[Dict[str, Any]], Dict[str, Any]], force: bool = False,
        background: bool = False) -> EvoSysAITask:
    """Ask once per (kind, target, facts). `check` turns the raw answer into
    {"output": ..., "status": "done"|"needs_person", "error": ...}."""
    h = _hash(kind, facts)
    prior = (db.query(EvoSysAITask).filter(EvoSysAITask.kind == kind, EvoSysAITask.target_id == target_id,
                                           EvoSysAITask.input_hash == h, EvoSysAITask.status != "failed")
             .order_by(EvoSysAITask.created_at.desc()).first())
    if prior is not None and not force:
        return prior
    t = EvoSysAITask(brand_sales_org_id=bso_id, kind=kind, target_id=target_id, input_hash=h, facts=facts,
                     created_by=user_id, created_at=datetime.utcnow(), status="failed")
    try:
        raw = _ask(kind, user_id, bso_id, system, facts, background=background)
        res = check(raw if isinstance(raw, dict) else {})
        t.output, t.status, t.error = res.get("output"), res.get("status", "done"), res.get("error")
    except Exception as exc:                                  # noqa: BLE001 - shown, manual path stays usable
        name = type(exc).__name__
        t.status = "failed"
        t.error = ("AI is turned off for manual actions." if name == "AIDisabled"
                   else "AI couldn't finish (%s). Nothing was changed; you can do this by hand or try again." % name)
        log.warning("evosys_sales ai %s for %s failed: %s", kind, target_id, name)
    db.add(t)
    db.flush()
    return t


# ── group a lead ──────────────────────────────────────────────────────────────

GROUP_SYSTEM = (
    "You sort service businesses into ONE industry and ONE market for a sales team. Use only the JSON facts given. "
    "Choose industry_key from facts.industries and market_key from facts.markets (or null if the facts do not say). "
    "Return JSON: {\"industry_key\":..,\"market_key\":..,\"reason\":\"one plain sentence\","
    "\"evidence\":[\"exact short quotes copied from facts.record\"]}. Never invent a location.")


def suggest_group(db: Session, settings, opp, lead, user_id: str, record_text: str, force: bool = False) -> EvoSysAITask:
    from app.services.evosys_sales import grouping as G
    inds = {i["key"]: i["label"] for i in G.industries(settings)}
    mks = {m["key"]: "%s (%s)" % (m["label"], m.get("state") or "") for m in G.markets(settings)}
    facts = {"record": record_text, "industries": inds, "markets": mks,
             "city": lead.city if lead else None, "state": lead.state if lead else None}

    def check(raw):
        ind, mk = raw.get("industry_key"), raw.get("market_key")
        ev = [e for e in (raw.get("evidence") or []) if isinstance(e, str)]
        problems = []
        if ind is not None and ind not in inds:
            problems.append("suggested an industry that is not configured")
            ind = None
        if mk is not None and mk not in mks:
            problems.append("suggested a market that is not configured")
            mk = None
        real = [e for e in ev if e.strip() and e.strip().lower() in record_text.lower()]
        if ev and len(real) < len(ev):
            problems.append("quoted text that is not in the record")
        out = {"industry_key": ind, "market_key": mk, "reason": str(raw.get("reason") or "")[:400],
               "evidence": real[:5], "problems": problems}
        return {"output": out, "status": "needs_person" if problems or not (ind and mk) else "done"}
    return run(db, kind="group_lead", target_id=opp.id, bso_id=opp.brand_sales_org_id, user_id=user_id,
               facts=facts, system=GROUP_SYSTEM, check=check, force=force)


# ── draft a campaign ──────────────────────────────────────────────────────────

DRAFT_SYSTEM = (
    "Write a short, plain cold-email follow-up (4 steps) from EvoSys Pro to owners of service businesses. "
    "Use ONLY facts given. Keep these placeholders exactly where they belong: {first_name}, {company}, {offer}, "
    "{booking_link}, {sender_name}. Never state a price, guarantee, result or number of appointments yourself - "
    "the offer goes only where {offer} is. Do not claim you saw their ad, post or website unless facts.source_seen "
    "is true. Return JSON {\"steps\":[{\"day\":0,\"subject\":..,\"body\":..},...],\"notes\":\"what you assumed\"}.")


def draft_campaign(db: Session, settings, campaign, user_id: str, source_seen: bool, force: bool = False) -> EvoSysAITask:
    from app.services.evosys_sales import grouping as G
    facts = {"industry": G.label_of(G.industries(settings), campaign.industry_key) or "service businesses",
             "market": G.label_of(G.markets(settings), campaign.market_key) or "their area",
             "what_evosys_does": ("finds businesses near the service company that need its services, reaches out, "
                                  "follows up and books appointments on its calendar"),
             "source_seen": bool(source_seen), "sender_name": settings.sender_name, "step_days": [0, 3, 7, 14]}

    def check(raw):
        steps, problems = [], []
        for i, st in enumerate((raw.get("steps") or [])[:6]):
            body = str((st or {}).get("body") or "")
            subj = str((st or {}).get("subject") or "")
            if MONEY_RE.search(body + " " + subj):
                problems.append("Step %d named a price; prices only come from the approved offer." % (i + 1))
            if not source_seen and re.search(r"\b(saw|noticed|came across)\b.{0,40}\b(ad|post|listing)\b", body, re.I):
                problems.append("Step %d claims we saw their ad, but these leads did not come from ads." % (i + 1))
            steps.append({"day": int((st or {}).get("day") or (0, 3, 7, 14, 21, 28)[min(i, 5)]),
                          "channel": "email", "subject": subj[:200], "body": body[:5000]})
        if not steps:
            problems.append("No steps came back.")
        return {"output": {"steps": steps, "notes": str(raw.get("notes") or "")[:500], "problems": problems},
                "status": "needs_person" if problems else "done"}
    return run(db, kind="draft_campaign", target_id=campaign.id, bso_id=campaign.brand_sales_org_id,
               user_id=user_id, facts=facts, system=DRAFT_SYSTEM, check=check, force=force)


# ── a reply ───────────────────────────────────────────────────────────────────

REPLY_SYSTEM = (
    "A business owner replied to EvoSys Pro's sales email. Using ONLY the facts, return JSON "
    "{\"category\": one of " + ", ".join(REPLY_CATEGORIES) + ", \"summary\":\"what they asked, one sentence\","
    "\"draft\":\"a short reply signed with facts.sender_name\"}. If they are interested, suggest a short intro call "
    "and include facts.booking_link. Quote a price ONLY by copying facts.offer exactly; if facts.offer is empty and "
    "they ask about price, say Mike will follow up with pricing and set category to pricing. Never promise results.")


def reply_assist(db: Session, settings, campaign, opp, reply_msg, original_text: str, user_id: str,
                 force: bool = False) -> EvoSysAITask:
    offer = ""
    if campaign is not None:
        from app.services.evosys_sales import campaigns as CA
        if CA.is_approved(campaign, settings) and not CA.OFFER_PLACEHOLDER_RE.match(campaign.offer or ""):
            offer = (campaign.offer or "").strip()
    facts = {"reply": (reply_msg.body or "")[:3000], "their_company": opp.company_name,
             "their_name": opp.contact_name, "our_email": (original_text or "")[:3000], "offer": offer,
             "booking_link": (campaign.booking_url if campaign else None) or "", "sender_name": settings.sender_name}

    def check(raw):
        cat = raw.get("category") if raw.get("category") in REPLY_CATEGORIES else "unclear"
        draft = str(raw.get("draft") or "")[:4000]
        needs = []
        prices = [m.group(0) for m in MONEY_RE.finditer(draft)]
        if prices and not all(p in offer for p in prices):
            needs.append("The draft names a price that isn't in the approved offer.")
            draft = ""
        if cat == "pricing" and not offer:
            needs.append("No approved offer on file - Mike answers pricing.")
        if cat in ("wrong_person", "unclear"):
            needs.append("Wrong person or unclear - read it yourself.")
        if cat in ("unsubscribe", "not_interested"):
            needs.append("They don't want more emails - consider Do Not Contact. No draft.")
            draft = ""
        out = {"category": cat, "summary": str(raw.get("summary") or "")[:400], "draft": draft,
               "needs_person": needs, "used_offer": bool(offer)}
        return {"output": out, "status": "needs_person" if needs else "done"}
    return run(db, kind="reply_assist", target_id=reply_msg.id, bso_id=opp.brand_sales_org_id, user_id=user_id,
               facts=facts, system=REPLY_SYSTEM, check=check, force=force)


# ── answer a TEXT reply (owner's own words, 2026-11-18) ───────────────────────

OFFER_FACTS = (
    "WHAT WE OFFER (the owner's own words - the only facts you may state): "
    "Mike with EvoSys Pro books cleaning companies at least 3 appointments within the next 30 days with businesses that need "
    "cleaning, so the cleaner can do a walkthrough and give a quote. The cleaner does the selling and closing; we "
    "just get them in front of customers. It is absolutely free: no obligation, no contract, no hidden fees. "
    "\"We're trying to earn your business before you get the business.\" It runs for 30 days - always say 'within "
    "30 days' / 'in the next 30 days', never 'this month' or 'by the end of the month'. If they like the "
    "service, Mike will then talk with them about what the system looks like; they also get a back office to see how "
    "the system works. It works anywhere - it's all done online, so their location doesn't matter. "
    "WHERE / WHAT KIND: the businesses we line up are in the cleaner's OWN service area, and they need the kind of "
    "cleaning the cleaner already does (offices, medical, retail, homes - whatever they offer). On the call Mike asks "
    "which cities they cover and what services they want, so the appointments fit them. "
    "NEXT STEP: a short call with Mike so he learns where they like to work, what services they offer and what "
    "kind of customers they want. It is NOT free cleaning, and we do NOT hand out job addresses yet - first the call, "
    "then we book the walkthroughs.")

TEXT_REPLY_SYSTEM = (
    "A cleaning business answered EvoSys Pro's text. " + OFFER_FACTS + " "
    "facts.examples are real answers Mike sent or approved to earlier texts: match their tone, length and wording "
    "style (never copy their links or names). Greet facts.their_name if given, otherwise just 'Hi!'. "
    "Using ONLY those facts and the JSON given, return JSON {\"category\": one of " + ", ".join(REPLY_CATEGORIES) + ", "
    "\"summary\": \"what they said, one sentence\", \"draft\": \"the text to send back\", \"simple\": true|false}. "
    "The draft: plain and friendly, from Mike, written ONLY in facts.reply_language, answers each thing they asked "
    "directly (one short sentence each), and ends by asking for the short call with facts.booking_link. Keep the "
    "words (not counting the link) under 230 characters. Never invent prices, numbers of contracts, results, guarantees, dates or anything not in "
    "the facts; say 'our goal is' rather than promising. simple=true ONLY when they said yes / are interested, or "
    "asked whether it is free / what the catch is / how it works / what this is / whether it is real - things the "
    "facts fully answer. simple=false for anything else: angry, not interested, wrong number, asks for a price after "
    "the 30 days, asks something the facts don't answer, or you are unsure.")

AUTO_CATEGORIES = ("interested", "question")
_ES_WORDS = {"hola", "gracias", "que", "qué", "por", "para", "como", "cómo", "cuánto", "cuanto", "sí", "si", "pues",
             "mándeme", "manda", "trabajos", "limpieza", "usted", "gente", "quiero", "puedo", "nomás", "sea",
             "solo", "ahí", "estaré", "intentémoslo", "también", "dónde", "donde", "negocios", "citas", "gratis",
             "le", "lo", "los", "las", "una", "el", "es", "está", "de", "y", "con", "mi", "su", "sus"}
_EN_WORDS = {"the", "and", "you", "what", "how", "is", "are", "thanks", "thank", "yes", "for", "your", "it", "this",
             "of", "to", "do", "they", "free", "would", "can", "we", "i", "me", "my"}


def looks_spanish(text) -> bool:
    words = re.findall(r"[a-záéíóúñü]+", (text or "").lower())
    es = sum(w in _ES_WORDS for w in words) + 2 * len(re.findall(r"[ñ¿¡áéíóú]", (text or "").lower()))
    en = sum(w in _EN_WORDS for w in words)
    return es > en


def first_names(raw) -> Optional[str]:
    """'Luz; Karla' -> 'Luz and Karla'; 'Imani McKinney' -> 'Imani'."""
    parts = [p.strip() for p in re.split(r"[;,/&]|\band\b", raw or "") if p.strip()]
    firsts = [p.split()[0] for p in parts if p.split()]
    firsts = [f for f in firsts if f.lower() not in ("mz", "ms", "mr", "mrs", "miss")] or \
        [" ".join(parts[0].split()[:2])] if parts else []
    if not firsts:
        return None
    return firsts[0] if len(firsts) == 1 else ", ".join(firsts[:-1]) + " and " + firsts[-1]


def approved_examples(db: Session, bso_id: str, lang: str, limit: int = 8) -> list:
    """The last answers a PERSON sent by text (written or approved), each with
    the text it answered - so AI drafts learn how Mike answers. AI's own
    automatic answers are never used as examples."""
    from app.models.evosys_sales_models import EvoSysOutreachMessage as M
    outs = (db.query(M).filter(M.brand_sales_org_id == bso_id, M.channel == "text", M.direction == "out",
                               M.status == "sent", M.actor_user_id.isnot(None))
            .order_by(M.created_at.desc()).limit(40).all())
    ex = []
    for o in outs:
        prev = (db.query(M).filter(M.opportunity_id == o.opportunity_id, M.direction == "in", M.channel == "text",
                                   M.created_at <= o.created_at).order_by(M.created_at.desc()).first())
        if prev is None or looks_spanish(o.body) != (lang == "Spanish"):
            continue
        ex.append({"they_said": (prev.body or "")[:300], "mike_answered": re.sub(r"https?://\S+", "[booking link]", o.body or "")[:400]})
        if len(ex) >= limit:
            break
    return ex


def text_name(name):
    from app.services.evosys_sales.outreach import text_company_name
    return text_company_name(name) or None


def _lead_booking_link(settings, campaign, opp) -> str:
    """This lead's own booking link (short), never the bare page address."""
    from app.services.evosys_sales import facts as F
    src = campaign if (campaign is not None and (campaign.booking_url or "").strip()) else settings
    return F._booking_link(src, opp) or ""


def text_reply(db: Session, settings, campaign, opp, reply_msg, conversation: list, user_id: str,
               force: bool = False, background: bool = False) -> EvoSysAITask:
    """Summarise a text reply and draft the answer. `output.auto_ok` says the
    rules allow it to go out by itself; the caller decides."""
    lang = "Spanish" if looks_spanish(reply_msg.body) else "English"
    facts = {"their_text": (reply_msg.body or "")[:1500], "reply_language": lang,
             "their_company": text_name(opp.company_name),
             "their_name": first_names(opp.contact_name), "conversation": conversation[-8:],
             "examples": approved_examples(db, opp.brand_sales_org_id, lang),
             "booking_link": _lead_booking_link(settings, campaign, opp),
             "sender_name": settings.sender_name or "Mike"}

    def check(raw):
        cat = raw.get("category") if raw.get("category") in REPLY_CATEGORIES else "unclear"
        draft = re.sub(r"\s+", " ", str(raw.get("draft") or "")).strip()[:600]
        needs = []
        if MONEY_RE.search(draft):
            needs.append("The draft names a price - Mike answers pricing.")
            draft = ""
        if re.search(r"\bguarantee", draft, re.I):
            needs.append("The draft promises a guarantee - check it.")
        if cat in ("unsubscribe", "not_interested"):
            needs.append("They don't want this - no answer drafted.")
            draft = ""
        if cat in ("wrong_person", "unclear", "pricing"):
            needs.append("Read this one yourself.")
        words_only = re.sub(r"https?://\S+", "", draft)
        if len(words_only) > 280:
            needs.append("The draft is long for a text - trim it.")
        if draft and (looks_spanish(draft) != (lang == "Spanish")):
            needs.append("The draft is not in %s - check it." % lang)
        auto_ok = (bool(raw.get("simple")) and cat in AUTO_CATEGORIES and not needs and bool(draft))
        out = {"category": cat, "summary": str(raw.get("summary") or "")[:400], "draft": draft,
               "needs_person": needs, "auto_ok": auto_ok, "used_offer": True}
        return {"output": out, "status": "needs_person" if needs else "done"}
    return run(db, kind="text_reply", target_id=reply_msg.id, bso_id=opp.brand_sales_org_id, user_id=user_id,
               facts=facts, system=TEXT_REPLY_SYSTEM, check=check, force=force, background=background)


def resolve(db: Session, t: EvoSysAITask, outcome: str) -> None:
    t.outcome, t.resolved_at = outcome, datetime.utcnow()
    db.flush()
