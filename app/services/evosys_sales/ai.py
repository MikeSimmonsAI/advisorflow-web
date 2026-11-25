"""EvoSys BookIt AI: suggestions a person reviews. Never decisions.

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
              "text_reply": "draft_reply", "translate": "draft_reply"}
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
    "HOW MIKE PUTS IT: we're testing a new appointment-setting service for cleaners and inviting companies to try "
    "it free for 30 days. We find potential business customers and work to schedule appointments for them to visit, "
    "give an estimate and win the cleaning business. They set their own prices, handle the walkthrough and keep the "
    "cleaning revenue. The pilot goal is 3 appointments, with no fee or obligation to continue. It's free so we can "
    "gather feedback and see how well it works for cleaning companies. Good questions to end on: which parts of "
    "their area they serve, and what types of properties they want more of (offices, medical facilities, etc.). "
    "WHERE / WHAT KIND: the businesses we line up are in the cleaner's OWN service area, and they need the kind of "
    "cleaning the cleaner already does (offices, medical, retail, homes - whatever they offer). On the call Mike asks "
    "which cities they cover and what services they want, so the appointments fit them. "
    "NEXT STEP: a short call with Mike so he learns where they like to work, what services they offer and what "
    "kind of customers they want. It is NOT free cleaning, and we do NOT hand out job addresses yet - first the call, "
    "then we book the walkthroughs. "
    "OUR WEBSITE is https://evosyspro.live (EvoSys Pro). Never say we have no website. "
    "NEVER SAY (not approved): that the businesses are verified, pre-qualified, already requested cleaning, are "
    "'actively looking', or are leads/contracts waiting for them; how many businesses we have; anything about our "
    "address, references or how we make money. Say instead that we reach out to businesses in their area and work to "
    "schedule a walkthrough for them.")

TEXT_REPLY_SYSTEM = (
    "A cleaning business answered EvoSys Pro's text. " + OFFER_FACTS + " "
    "facts.examples are real answers Mike sent or approved to earlier texts: match their tone, length and wording "
    "style (never copy their links or names). Greet facts.their_name if given, otherwise just 'Hi!'. "
    "Using ONLY those facts and the JSON given, return JSON {\"category\": one of " + ", ".join(REPLY_CATEGORIES) + ", "
    "\"summary\": \"what they said, one sentence IN ENGLISH\", \"draft\": \"the text to send back\", "
    "\"draft_english\": \"the same draft in English (only when the draft is in Spanish)\", \"simple\": true|false}. "
    "READ facts.conversation FIRST (oldest to newest; from 'us' or 'them'). Never ask for anything they already "
    "told us (cities, areas, services, property types, language) - if they told us, name it back briefly instead. "
    "Never repeat a pitch or sentence we already sent them; if they already heard the offer, don't explain it "
    "again - only answer what is new. "
    "The draft: plain and friendly, from Mike, written ONLY in facts.reply_language, answers each thing they asked "
    "directly. ONE TEXT TO A BOOKING (Mike's rule, 2026-11-23): when they say yes / ok / interested and facts.booked "
    "is empty, do NOT pitch again and do NOT ask them questions - reply in one or two short sentences telling them "
    "to grab a time on Mike's calendar to lock in their free appointments, with facts.booking_link right there. "
    "Where they serve and what they want gets covered on the call. For any other question with facts.booked empty: "
    "answer it, then always point them to facts.booking_link to book the call. If "
    "facts.booked is set, they already have a call at that time - never offer another one. facts.style_examples are answers Mike wrote himself - follow their tone, structure "
    "and length closely. Keep the words (not counting the link) under 600 characters. Never invent prices, numbers of contracts, results, guarantees, dates or anything not in "
    "the facts; say 'our goal is' rather than promising. simple=true ONLY when they said yes / are interested, or "
    "asked whether it is free / what the catch is / how it works / what this is / whether it is real - things the "
    "facts fully answer. simple=false for anything else: angry, not interested, wrong number, asks for a price after "
    "the 30 days, asks something the facts don't answer, asks how we find or verify the businesses, asks for a "
    "website, written details, an email, our address or legal name, references or how we make money, or you are unsure.")

_PREF_RE = re.compile(r"\b(prefer|preferred|prefiero|speak|hablo|habla|language|idioma|my english|mi ingl)", re.I)
# Things only Mike can answer: never auto-sent, always waits for him.
PERSON_ONLY_RE = re.compile(
    r"\b(website|web site|sitio web|p[aá]gina web|in writing|written|email|correo|address|direcci[oó]n|legal name|"
    r"references?|referencias|verif|how do you (find|get|generate|choose)|where do you (find|get)|"
    r"how do you make money|what's in it for you|catch|contract|contrato|insurance|seguro)\b", re.I)
# Claims we may not make.
CLAIM_RE = re.compile(r"\b(verified|pre-?qualified|actively looking|already (requested|asked for)|guarantee[ds]?|"
                      r"verificad[oa]s|garantiza\w*)\b"
                      r"|\b(no|don'?t|do not)\s+(tenemos|have)\s+(?:(?:un|una|an?|any)\s+)?(?:official\s+)?"
                      r"(sitio web|website|web site|p[aá]gina web)", re.I)


def preferred_language(conversation: list, latest_text: str) -> str:
    """The language to answer in: one they asked for in this conversation,
    otherwise the language of their newest text."""
    for m in reversed(conversation or []):
        if m.get("from") != "them":
            continue
        t = (m.get("text") or "").lower()
        if _PREF_RE.search(t):
            if re.search(r"spanish|español|espanol", t):
                return "Spanish"
            if re.search(r"english|inglés|ingles", t) and not re.search(r"english is not|my english|mi ingl", t):
                return "English"
    return "Spanish" if looks_spanish(latest_text) else "English"


def _too_close(draft: str, conversation: list) -> bool:
    """True when the draft is nearly a text we already sent them."""
    import difflib
    d = re.sub(r"https?://\S+", "", draft or "").lower()
    for m in conversation or []:
        if m.get("from") == "us":
            o = re.sub(r"https?://\S+", "", m.get("text") or "").lower()
            if o and difflib.SequenceMatcher(None, d, o).ratio() >= 0.8:
                return True
    return False

AUTO_CATEGORIES = ("interested", "question")

# A plain yes is a yes (owner, 2026-11-23): "Yes", "Of course", "Sure", "Sí"...
# never waits for a person - it gets the booking link right away.
_YES_WORDS = {"yes", "yes please", "yess", "yesss", "yeah", "yea", "yep", "yup", "sure", "sure thing", "ok", "okay",
              "of course", "absolutely", "definitely", "interested", "im interested", "i am interested",
              "very interested", "sounds good", "lets do it", "let s do it", "i want it", "i want them",
              "i want in", "im in", "yes im interested", "perfect", "please", "yes i am", "yes i would", "yes i do", "yes sir", "yes maam",
              "count me in", "go ahead", "si", "sí", "claro", "claro que si", "claro que sí", "por supuesto",
              "me interesa", "si me interesa", "sí me interesa", "dale", "si por favor", "sí por favor", "ok si", "ok sí"}


def plain_yes(text) -> bool:
    """True when the whole reply is just a yes (emoji and punctuation ignored)."""
    t = re.sub(r"[^\w\sáéíóúñü']", " ", str(text or "").lower()).replace("'", "")
    t = re.sub(r"\s+", " ", t).strip()
    return bool(t) and t in _YES_WORDS


def yes_text(lang: str, link: str) -> str:
    if lang == "Spanish":
        return ("¡Perfecto! Elija un horario en mi calendario para asegurar sus 3 citas gratis en los próximos "
                "30 días: %s" % link)
    return "Great! Grab a time on my calendar to lock in your 3 free appointments within the next 30 days: %s" % link
_ES_WORDS = {"hola", "gracias", "que", "qué", "por", "para", "como", "cómo", "cuánto", "cuanto", "sí", "si", "pues",
             "mándeme", "manda", "trabajos", "limpieza", "usted", "gente", "quiero", "puedo", "nomás", "sea",
             "solo", "ahí", "estaré", "intentémoslo", "también", "dónde", "donde", "negocios", "citas", "gratis",
             "le", "lo", "los", "las", "una", "el", "es", "está", "de", "y", "con", "mi", "su", "sus", "me",
             "interesa", "trabajo", "manera", "esa", "ese", "esta", "este", "bien", "bueno", "quien", "cuando",
             "necesito", "tengo", "tiene", "ahora", "hoy", "mañana", "favor", "claro", "mucho", "nada", "nos",
             "nosotros", "ustedes", "hacer", "hago", "cuesta", "precio", "llamar", "llamada", "ok", "vale"}
_EN_WORDS = {"the", "and", "you", "what", "how", "is", "are", "thanks", "thank", "yes", "for", "your", "it", "this",
             "of", "to", "do", "they", "free", "would", "can", "we", "i", "my", "ok", "not", "interested", "call"}


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


TRANSLATE_SYSTEM = ("Translate each string in facts.texts from Spanish into plain, natural American English, keeping "
                    "the meaning and tone exactly (do not add or soften anything; keep links as they are). Return JSON "
                    "{\"translations\": [one English string per input, same order]}.")


def translate(texts: list, bso_id: str, actor: str, background: bool = False) -> list:
    """English for each Spanish text (None where it failed). Never raises."""
    if not texts:
        return []
    try:
        raw = _ask("translate", actor, bso_id, TRANSLATE_SYSTEM, {"texts": [t[:1500] for t in texts]},
                   background=background)
        out = raw.get("translations") if isinstance(raw, dict) else None
        if isinstance(out, list) and len(out) == len(texts):
            return [str(x)[:2000] if x else None for x in out]
    except Exception as exc:                                  # noqa: BLE001
        log.warning("evosys_sales translate failed: %s", type(exc).__name__)
    return [None] * len(texts)


def fill_translations(db: Session, msgs: list, bso_id: str, actor: str, background: bool = False) -> None:
    """Translate the Spanish messages in `msgs` that have no English yet."""
    todo = [m for m in msgs if m.channel == "text" and not m.translation and looks_spanish(m.body)]
    if not todo:
        return
    for m, t in zip(todo, translate([m.body or "" for m in todo], bso_id, actor, background)):
        if t:
            m.translation = t
    db.flush()


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
    lang = preferred_language(conversation, reply_msg.body)
    booked = None
    try:
        from app.services.evosys_sales import booking_page as BP
        a = BP.current(db, opp)
        booked = BP._ct(a.starts_at) if a is not None else None
    except Exception:                                                # noqa: BLE001
        booked = None
    facts = {"their_text": (reply_msg.body or "")[:1500], "reply_language": lang, "booked": booked or "",
             "their_company": text_name(opp.company_name),
             "their_name": first_names(opp.contact_name), "conversation": conversation[-12:],
             "examples": approved_examples(db, opp.brand_sales_org_id, lang),
             "style_examples": [str(x)[:1200] for x in (settings.reply_examples or []) if str(x).strip()][:6],
             "booking_link": _lead_booking_link(settings, campaign, opp),
             "sender_name": settings.sender_name or "Mike"}

    def check(raw):
        cat = raw.get("category") if raw.get("category") in REPLY_CATEGORIES else "unclear"
        draft = re.sub(r"\s+", " ", str(raw.get("draft") or "")).strip()[:600]
        needs = []
        link0 = facts.get("booking_link") or ""
        if plain_yes(reply_msg.body) and not booked and link0:
            # A plain yes is a yes - never "unclear" (owner, 2026-11-23).
            cat, raw = "interested", dict(raw, simple=True)
            if not draft or _too_close(draft, conversation) or MONEY_RE.search(draft) \
                    or CLAIM_RE.search(draft) or (looks_spanish(draft) != (lang == "Spanish")):
                draft = yes_text(lang, link0)
        if MONEY_RE.search(draft):
            needs.append("The draft names a price - Mike answers pricing.")
            draft = ""
        if CLAIM_RE.search(draft):
            needs.append("The draft makes a claim we haven't approved (verified / actively looking / guarantee) - check it.")
        if PERSON_ONLY_RE.search(reply_msg.body or ""):
            needs.append("They asked something only you can answer (website, details in writing, how we find businesses...).")
        if draft and _too_close(draft, conversation):
            needs.append("The draft repeats a text they already got - write something new.")
        if booked and cat not in ("unsubscribe", "not_interested"):
            needs.append("They're already booked (%s) - answer this one yourself." % booked)
        if cat in ("unsubscribe", "not_interested"):
            needs.append("They don't want this - no answer drafted.")
            draft = ""
        if cat in ("wrong_person", "unclear", "pricing"):
            needs.append("Read this one yourself.")
        words_only = re.sub(r"https?://\S+", "", draft)
        if len(words_only) > 640:
            needs.append("The draft is long for a text - trim it.")
        if draft and (looks_spanish(draft) != (lang == "Spanish")):
            needs.append("The draft is not in %s - check it." % lang)
        link = facts.get("booking_link") or ""
        if draft and link and not booked and cat in AUTO_CATEGORIES and link not in draft:
            # Never let a yes go out without the booking link (2026-11-23).
            draft = draft.rstrip() + " " + link
        auto_ok = (bool(raw.get("simple")) and cat in AUTO_CATEGORIES and not needs and bool(draft))
        out = {"category": cat, "summary": str(raw.get("summary") or "")[:400], "draft": draft,
               "needs_person": needs, "auto_ok": auto_ok, "used_offer": True,
               "draft_english": (str(raw.get("draft_english") or "")[:600] or None) if lang == "Spanish" and draft else None}
        if lang == "Spanish" and draft and not out["draft_english"]:
            # Mike doesn't read Spanish: every Spanish draft comes with English
            out["draft_english"] = (translate([draft], opp.brand_sales_org_id, user_id, background) or [None])[0]
        return {"output": out, "status": "needs_person" if needs else "done"}
    return run(db, kind="text_reply", target_id=reply_msg.id, bso_id=opp.brand_sales_org_id, user_id=user_id,
               facts=facts, system=TEXT_REPLY_SYSTEM, check=check, force=force, background=background)


def resolve(db: Session, t: EvoSysAITask, outcome: str) -> None:
    t.outcome, t.resolved_at = outcome, datetime.utcnow()
    db.flush()
