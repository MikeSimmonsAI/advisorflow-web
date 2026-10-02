"""Conversation memory: build it from the record, keep it current, answer from it.

    sync(db, lead)            read new messages since the cursor into memory
    build_context(db, lead)   the precise package an AI or a person needs
    decide_ai(ctx, channel)   may automation speak now? if not, why
    set_mode(...)             human takeover / resume / pause

Everything is read from the lead's own organization. Nothing here sends.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.conversation_models import (
    ACTIVE, CAT_COMMITMENT, CAT_FACT, CAT_FOLLOW_UP, CAT_INFERENCE, CAT_INTENT, CAT_OBJECTION,
    CAT_PREFERENCE, CAT_QUESTION, FACT, INFERENCE, MODE_AI_ACTIVE, MODE_AI_PAUSED,
    MODE_HUMAN_ACTIVE, MODE_WAITING_CUSTOMER, MODE_WAITING_HUMAN, RESOLVED, SUPERSEDED,
    ConversationMemoryItem, ConversationState)
from app.models.models import Lead, User
from app.services.conversation_intel import extract as X
from app.services.conversation_intel.thread import Event, load_events

log = logging.getLogger(__name__)

CAT_OUR_QUESTION = "our_question"     # what WE asked - so we never ask it twice
HUMAN_ACTIVE_HOURS = 24               # a human reply keeps AI quiet this long
COALESCE_SECONDS = 120                # rapid inbound messages are one turn
STALE_DAYS = 14
AT_RISK_HOURS = 24
NOT_NOW_DAYS = 21                     # a follow-up further out than this is "not now", not "follow up"

SINGLE_VALUED = ("household.", "person.", "location.", "coverage.", "property.", "energy.", "pref.",
                 "prior_contact.", "follow_up.", "intent.", "inferred.", "flag.", "objection.")

FACT_LABELS = {
    "household.spouse": "Spouse/partner", "household.children": "Children",
    "person.age": "Age", "person.name_stated": "Name they gave", "location.city": "City",
    "coverage.existing": "Existing coverage", "prior_contact.with": "Already spoke with",
    "property.inherited": "Inherited property", "property.occupancy": "Occupancy",
    "property.condition": "Condition", "property.owner_stated": "Owner on title",
    "property.subject_correction": "Which property", "energy.moving": "Moving",
    "energy.contract_end": "Current contract ends", "consent.stop_requested": "Asked us to stop",
}

# what each vertical needs next, in order; (slot, question, satisfied-by keys / lead fields)
SLOTS = {
    X.INSURANCE: [
        ("need", "What prompted you to look into coverage - protecting family, retirement income, or something else?",
         ("intent.vertical",)),
        ("household", "Who would the coverage be protecting?", ("household.spouse", "household.children")),
        ("existing", "Do you have any coverage today, for example through work?", ("coverage.existing",)),
        ("age", "To give you real numbers, may I ask your age?", ("person.age",)),
        ("time", "What's a good day and time to go over options together?", ("follow_up.when", "pref.time_of_day")),
    ],
    X.ENERGY: [
        ("address", "What's the service address or ZIP code for the account?", ("lead.zip_code", "lead.street_address", "location.city")),
        ("contract", "When does your current electricity contract end?", ("energy.contract_end",)),
        ("moving", "Are you staying at this address, or is a move coming up?", ("energy.moving",)),
        ("time", "What's the best time to go over the rate options?", ("follow_up.when", "pref.time_of_day")),
    ],
    X.WHOLESALE: [
        ("address", "Which property address are we talking about?", ("lead.street_address",)),
        ("condition", "How would you describe the condition - any big repairs needed?", ("property.condition",)),
        ("occupancy", "Is anyone living there right now - you, family, or tenants?", ("property.occupancy",)),
        ("timeline", "What timeline are you hoping for?", ("follow_up.when", "intent.timeline")),
        ("time", "What's the best time for a quick call?", ("pref.time_of_day", "follow_up.when")),
    ],
    X.GENERAL: [
        ("need", "What would be most helpful for you right now?", ("intent.vertical",)),
        ("time", "What's the best time to connect?", ("follow_up.when", "pref.time_of_day")),
    ],
}

VOICE = {
    X.INSURANCE: "educational, confident, thoughtful and relationship-first; never pushy, never quote a premium or "
                 "promise approval",
    X.ENERGY: "clear, practical, quick and service-oriented; plain numbers only when they come from the record",
    X.WHOLESALE: "direct, conversational, investor-friendly and respectful; never name a price, offer or commitment",
    X.GENERAL: "warm, clear and professional",
}


# ── workspace day ───────────────────────────────────────────────────────────

def workspace_today(db: Session, org_id: str, now: Optional[datetime] = None) -> date:
    try:
        from app.services.workspace_time import local_today
        return local_today(db, org_id, now)
    except Exception:                                            # noqa: BLE001
        from app.services.activity_reporting import org_local_date
        return org_local_date(db, org_id, on=now)


def lead_vertical(db: Session, lead: Lead) -> str:
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == lead.organization_id).first()
    features = getattr(org, "enabled_features", None) if org else None
    return X.vertical_for_industry(getattr(org, "industry", None) if org else None,
                                   features if isinstance(features, str) else json.dumps(features or ""))


# ── memory store ────────────────────────────────────────────────────────────

def _active(db, lead, key):
    db.flush()
    return (db.query(ConversationMemoryItem)
            .filter(ConversationMemoryItem.organization_id == lead.organization_id,
                    ConversationMemoryItem.lead_id == lead.id,
                    ConversationMemoryItem.key == key,
                    ConversationMemoryItem.status == ACTIVE)
            .order_by(ConversationMemoryItem.observed_at.desc()).first())


def _exists(db, lead, ev: Event, key) -> bool:
    return db.query(ConversationMemoryItem.id).filter(
        ConversationMemoryItem.lead_id == lead.id,
        ConversationMemoryItem.source_type == ev.source_type,
        ConversationMemoryItem.source_id == ev.source_id,
        ConversationMemoryItem.key == key).first() is not None


def remember(db: Session, lead: Lead, ev: Event, *, category: str, key: str, value: Optional[str],
             quote: Optional[str] = None, certainty: str = FACT, value_date: Optional[date] = None,
             force_supersede: bool = False) -> Optional[ConversationMemoryItem]:
    """Write one memory item. Single-valued keys SUPERSEDE, never overwrite.

    A later observation replaces the current one; an EARLIER observation that
    arrives late (out-of-order delivery) is stored as history and does not
    displace the newer truth.
    """
    if _exists(db, lead, ev, key):
        return None
    item = ConversationMemoryItem(
        organization_id=lead.organization_id, lead_id=lead.id, category=category, key=key,
        value=(value or "")[:2000] or None,
        value_date=datetime.combine(value_date, datetime.min.time()) if value_date else None,
        certainty=certainty, status=ACTIVE, source_type=ev.source_type, source_id=ev.source_id,
        source_channel=ev.channel, source_quote=(quote or "")[:400] or None, observed_at=ev.at)
    if key.startswith(SINGLE_VALUED):
        cur = _active(db, lead, key)
        if cur is not None:
            same = (cur.value or "") == (item.value or "") and cur.value_date == item.value_date
            if same and not force_supersede:
                return None
            if cur.observed_at and ev.at and ev.at < cur.observed_at:
                item.status = SUPERSEDED            # late-arriving older statement: history only
            else:
                if not _insert(db, item):
                    return None
                cur.status = SUPERSEDED
                cur.superseded_by_id = item.id
                return item
    return item if _insert(db, item) else None


def _insert(db, item) -> bool:
    """Insert under a savepoint. Two workers syncing the same lead at once both
    try to write the same (lead, source, key) row; the unique index lets one
    win and the other simply skips - neither request fails."""
    nested = db.begin_nested()
    try:
        db.add(item)
        db.flush()
        nested.commit()
        return True
    except IntegrityError:
        nested.rollback()
        return False


def resolve(db: Session, item: ConversationMemoryItem, ev: Optional[Event] = None) -> None:
    item.status = RESOLVED
    item.resolved_at = ev.at if ev else datetime.utcnow()
    item.resolved_by_source_id = ev.source_id if ev else None


def _items(db, lead, *, category=None, status=None, prefix=None):
    db.flush()
    q = db.query(ConversationMemoryItem).filter(
        ConversationMemoryItem.organization_id == lead.organization_id,
        ConversationMemoryItem.lead_id == lead.id)
    if category:
        q = q.filter(ConversationMemoryItem.category == category)
    if status:
        q = q.filter(ConversationMemoryItem.status == status)
    if prefix:
        q = q.filter(ConversationMemoryItem.key.like(prefix + "%"))
    return q.order_by(ConversationMemoryItem.observed_at.asc()).all()


# ── applying one event ──────────────────────────────────────────────────────

def _answers(question_words: List[str], outbound_text: str) -> bool:
    if not question_words:
        return True
    out = set(X.content_words(outbound_text))
    qw = set(question_words) - {"something", "thing", "anything", "would", "like"}
    if not qw:
        return len(out) >= 4
    if qw & out:
        return True
    price = {"cost", "price", "pricing", "premium", "rate", "rates", "fee", "fees", "afford", "quote", "much"}
    if qw & price and (out & price or "$" in outbound_text):
        return True
    return False


def apply_event(db: Session, lead: Lead, ev: Event, vertical: str, today: date) -> X.Analysis:
    if ev.direction == "note":
        return X.Analysis(direction="note")
    a = X.analyze(ev.text, direction=ev.direction, vertical=vertical, today=today)
    if ev.direction == "outbound":
        for f in a.commitments:
            remember(db, lead, ev, category=CAT_COMMITMENT, key=f.key, value=f.value, quote=f.value)
        for q, words in a.questions:
            remember(db, lead, ev, category=CAT_OUR_QUESTION, key="asked." + X.short_hash(q), value=q, quote=q)
        for item in _items(db, lead, category=CAT_QUESTION, status=ACTIVE):
            if item.observed_at and ev.at and item.observed_at > ev.at:
                continue
            words = X.content_words(item.value or "")
            if _answers(words, ev.text):
                resolve(db, item, ev)
        if ev.actor == "human":
            for key in ("flag.human_request", "flag.complaint", "flag.identity", "flag.disputes_record"):
                cur = _active(db, lead, key)
                if cur is not None:
                    resolve(db, cur, ev)
        return a

    # inbound
    for f in a.facts:
        remember(db, lead, ev, category=CAT_FACT, key=f.key, value=f.value, quote=f.quote,
                 force_supersede=a.is_correction)
    for f in a.inferences:
        remember(db, lead, ev, category=CAT_INFERENCE, key=f.key, value=f.value, quote=f.quote, certainty=INFERENCE)
    for f in a.preferences:
        remember(db, lead, ev, category=CAT_PREFERENCE, key=f.key, value=f.value, quote=f.quote)
    for o in a.objections:
        remember(db, lead, ev, category=CAT_OBJECTION, key=o.key, value=o.value, quote=o.quote)
    for q, words in a.questions:
        remember(db, lead, ev, category=CAT_QUESTION, key="question." + X.short_hash(q), value=q, quote=q)
    if a.timing is not None:
        remember(db, lead, ev, category=CAT_FOLLOW_UP, key="follow_up.when", value=a.timing.label,
                 quote=a.timing.quote, value_date=a.timing.when, force_supersede=True)
    if a.vertical_intents:
        # Topics ACCUMULATE: asking about existing coverage does not erase that
        # they came for family protection. Order: first seen first.
        cur = _active(db, lead, "intent.vertical")
        seen = [t for t in ((cur.value or "").split(", ") if cur else []) if t]
        merged = seen + [t for t in a.vertical_intents if t not in seen]
        remember(db, lead, ev, category=CAT_INTENT, key="intent.vertical", value=", ".join(merged),
                 quote=X._quote(ev.text))
    if a.primary_intent and not (a.is_bare_ack and a.primary_intent == "ambiguous"):
        remember(db, lead, ev, category=CAT_INTENT, key="intent.current", value=a.primary_intent,
                 quote=X._quote(ev.text))
    flag_keys = {"stop": "consent.stop_requested", "human_request": "flag.human_request",
                 "legal": "flag.complaint", "complaint": "flag.complaint",
                 "wrong_person": "flag.identity", "identity_confusion": "flag.identity",
                 "disputes_record": "flag.disputes_record", "repeated_question": "flag.repeated_question"}
    for flag, reason in a.flags.items():
        key = flag_keys.get(flag)
        if key:
            remember(db, lead, ev, category=CAT_FACT, key=key, value=reason, quote=X._quote(ev.text))
    # re-engagement resolves a "not now"
    if set(a.intents) & {"interested", "wants_appointment", "wants_call", "question", "pricing"} \
            and not any(o.key == "objection.not_now" for o in a.objections):
        cur = _active(db, lead, "objection.not_now")
        if cur is not None:
            resolve(db, cur, ev)
    return a


# ── state ───────────────────────────────────────────────────────────────────

def get_state(db: Session, lead: Lead, create: bool = True) -> Optional[ConversationState]:
    st = db.query(ConversationState).filter(ConversationState.organization_id == lead.organization_id,
                                            ConversationState.lead_id == lead.id).first()
    if st is None and create:
        st = ConversationState(organization_id=lead.organization_id, lead_id=lead.id)
        if not _insert(db, st):
            st = db.query(ConversationState).filter(ConversationState.organization_id == lead.organization_id,
                                                    ConversationState.lead_id == lead.id).first()
    return st


def sync(db: Session, lead: Lead, *, now: Optional[datetime] = None, commit: bool = True) -> ConversationState:
    """Fold every message newer than the cursor into memory. Idempotent."""
    now = now or datetime.utcnow()
    st = get_state(db, lead)
    vertical = lead_vertical(db, lead)
    today = workspace_today(db, lead.organization_id, now)
    since = (st.processed_through - timedelta(minutes=10)) if st.processed_through else None
    events = load_events(db, lead, since=since)
    newest = st.processed_through
    for ev in events:
        apply_event(db, lead, ev, vertical, today)
        db.flush()        # sessions here do not autoflush; later reads must see this event's writes
        if ev.direction == "inbound":
            st.last_inbound_at = max(filter(None, [st.last_inbound_at, ev.at]))
        elif ev.direction == "outbound":
            st.last_outbound_at = max(filter(None, [st.last_outbound_at, ev.at]))
        newest = max(filter(None, [newest, ev.at]))
        st.events_processed = (st.events_processed or 0) + 1
    st.processed_through = newest
    _derive(db, lead, st, vertical, today, now)
    if commit:
        db.commit()
    else:
        db.flush()
    return st


def _human_active(db, lead, st, now) -> Optional[str]:
    if st.mode == MODE_HUMAN_ACTIVE:
        return st.mode_reason or "A team member has taken over this conversation"
    events = load_events(db, lead, since=now - timedelta(hours=HUMAN_ACTIVE_HOURS))
    humans = [e for e in events if e.direction == "outbound" and e.actor == "human"]
    if humans and not (st.mode == MODE_AI_ACTIVE and st.mode_set_at and st.mode_set_at > humans[-1].at):
        return "A team member replied %s; the AI stays quiet for %d hours after a human reply" % (
            humans[-1].at.strftime("%b %d %H:%M UTC"), HUMAN_ACTIVE_HOURS)
    return None


def _derive(db, lead, st, vertical, today, now):
    facts = {i.key: i for i in _items(db, lead, status=ACTIVE)}
    intent = facts.get("intent.current")
    st.current_intent = intent.value if intent else None
    open_q = [i for i in _items(db, lead, category=CAT_QUESTION, status=ACTIVE)]
    st.has_open_question = bool(open_q)
    fu = facts.get("follow_up.when")
    st.follow_up_due_at = fu.value_date if fu and fu.value_date else None

    needs_human = None
    for key, why in (("consent.stop_requested", None), ("flag.complaint", "Complaint or legal language - a person should respond"),
                     ("flag.human_request", "Customer asked for a person"),
                     ("flag.identity", "Wrong person or identity confusion - verify before anything else"),
                     ("flag.disputes_record", "Customer disputes what we recorded"),
                     ("property.subject_correction", "Customer says we have the wrong property")):
        if key in facts and why:
            needs_human = why
            break
    if vertical == X.WHOLESALE and intent and intent.value in ("pricing",) or \
            (vertical == X.WHOLESALE and facts.get("intent.vertical") and
             any(k in (facts["intent.vertical"].value or "") for k in ("wants_offer", "price_objection"))
             and _latest_inbound_after_outbound(st)):
        needs_human = needs_human or "Seller is asking about price/offer - owner judgment required"
    if vertical == X.INSURANCE and facts.get("intent.vertical") and \
            "application_question" in (facts["intent.vertical"].value or "") and _latest_inbound_after_outbound(st):
        needs_human = needs_human or "Application/underwriting question - a licensed agent should answer"
    st.needs_human_reason = needs_human

    stopped = "consent.stop_requested" in facts or _is_dnc(lead)
    awaiting_us = _latest_inbound_after_outbound(st)
    human = _human_active(db, lead, st, now)

    if stopped:
        state = "stopped"
    elif needs_human:
        state = "human_review"
    elif intent and intent.value == "not_interested":
        state = "closed"
    elif open_q and awaiting_us:
        # A question they are waiting on outranks a follow-up date: "call me
        # Monday - and what would it cost?" still deserves an answer today.
        state = "question"
    elif fu and fu.value_date and fu.value_date.date() > today + timedelta(days=NOT_NOW_DAYS):
        state = "not_now"
    elif fu and fu.value_date:
        state = "follow_up_needed"
    elif fu and not fu.value_date:
        state = "follow_up_needed"
    elif intent and intent.value in ("wants_appointment", "wants_call"):
        state = "appointment_ready"
    elif open_q and awaiting_us:
        state = "question"
    elif any(i.status == ACTIVE for i in _items(db, lead, category=CAT_OBJECTION)) and awaiting_us:
        state = "objection"
    elif intent and intent.value in ("interested", "pricing"):
        state = "interested"
    elif awaiting_us:
        state = "waiting_on_staff"
    elif st.last_outbound_at and not st.last_inbound_at:
        state = "new"
    elif st.last_outbound_at:
        state = "waiting_on_customer"
    else:
        state = "new"
    if getattr(lead, "status", None) == "booked" and state not in ("stopped", "human_review"):
        state = "appointment_set"
    st.state = state

    if st.mode not in (MODE_HUMAN_ACTIVE, MODE_AI_PAUSED):
        st.mode = (MODE_HUMAN_ACTIVE if human else MODE_WAITING_HUMAN if needs_human
                   else MODE_WAITING_CUSTOMER if (st.last_outbound_at and not awaiting_us) else MODE_AI_ACTIVE)

    last = st.last_inbound_at or st.last_outbound_at
    if stopped:
        st.health = "stopped"
    elif needs_human:
        st.health = "blocked"
    elif last and (now - last).days >= STALE_DAYS and state not in ("not_now", "closed"):
        st.health = "stale"
    elif awaiting_us and st.last_inbound_at and (now - st.last_inbound_at).total_seconds() > AT_RISK_HOURS * 3600:
        st.health = "at_risk"
    elif any(i.status == ACTIVE for i in _items(db, lead, category=CAT_OBJECTION)) and awaiting_us:
        st.health = "at_risk"
    else:
        st.health = "healthy"

    if stopped or state == "closed":
        st.priority = "low_priority"
    elif needs_human:
        st.priority = "needs_human"
    elif awaiting_us and (open_q or state in ("appointment_ready", "interested", "question", "objection")):
        st.priority = "respond_now"
    elif fu and fu.value_date and fu.value_date.date() <= today:
        st.priority = "follow_up_today"
    elif state == "not_now":
        st.priority = "nurture"
    elif state == "waiting_on_customer":
        st.priority = "waiting_on_customer"
    elif awaiting_us:
        st.priority = "respond_now"
    else:
        st.priority = "low_priority"

    ev = _last_meaningful(db, lead)
    if ev:
        st.last_meaningful_event, st.last_meaningful_at = ev
    st.updated_at = now


def _latest_inbound_after_outbound(st) -> bool:
    return bool(st.last_inbound_at and (not st.last_outbound_at or st.last_inbound_at > st.last_outbound_at))


def _last_meaningful(db, lead):
    items = (db.query(ConversationMemoryItem)
             .filter(ConversationMemoryItem.organization_id == lead.organization_id,
                     ConversationMemoryItem.lead_id == lead.id,
                     ConversationMemoryItem.category.in_([CAT_OBJECTION, CAT_FOLLOW_UP, CAT_QUESTION, CAT_FACT,
                                                          CAT_COMMITMENT]))
             .order_by(ConversationMemoryItem.observed_at.desc()).first())
    if not items:
        return None
    label = {CAT_OBJECTION: "Objection", CAT_FOLLOW_UP: "Asked us to follow up", CAT_QUESTION: "Asked",
             CAT_FACT: "Told us", CAT_COMMITMENT: "We promised"}.get(items.category, "Update")
    return ("%s: %s" % (label, items.source_quote or items.value or ""))[:300], items.observed_at


# ── modes ───────────────────────────────────────────────────────────────────

def set_mode(db: Session, lead: Lead, mode: str, *, user: Optional[User], reason: Optional[str] = None,
             now: Optional[datetime] = None) -> ConversationState:
    if mode not in (MODE_AI_ACTIVE, MODE_HUMAN_ACTIVE, MODE_AI_PAUSED):
        raise ValueError("mode must be ai_active, human_active or ai_paused")
    now = now or datetime.utcnow()
    st = get_state(db, lead)
    st.mode = mode
    st.mode_set_by_user_id = getattr(user, "id", None)
    st.mode_set_at = now
    st.mode_reason = (reason or {"human_active": "Taken over by a team member",
                                 "ai_paused": "AI paused by a team member",
                                 "ai_active": "AI resumed by a team member"}[mode])[:300]
    if mode == MODE_AI_ACTIVE:
        # resuming clears the "a human replied recently" quiet window from now on
        pass
    db.commit()
    return st


# ── the context package ─────────────────────────────────────────────────────

def _fmt_item(i: ConversationMemoryItem) -> Dict[str, Any]:
    return {"id": i.id, "key": i.key, "label": FACT_LABELS.get(i.key, i.key.split(".")[-1].replace("_", " ").title()),
            "value": i.value, "certainty": i.certainty, "status": i.status, "quote": i.source_quote,
            "channel": i.source_channel, "source_type": i.source_type, "source_id": i.source_id,
            "observed_at": i.observed_at.isoformat() + "Z" if i.observed_at else None,
            "date": i.value_date.date().isoformat() if i.value_date else None}


def _is_dnc(lead) -> bool:
    return (getattr(lead, "status", None) == "dnc") or (getattr(lead, "manual_flag", None) == "remove_all")


def _consent(db, lead) -> Dict[str, Any]:
    """Channel by channel. An email conversation never implies SMS consent."""
    dnc = _is_dnc(lead)
    sms = bool(getattr(lead, "sms_consent", False)) and getattr(lead, "allow_sms", None) is not False
    email = bool(getattr(lead, "email", None)) and getattr(lead, "allow_email", None) is not False
    try:
        from app.services.test_records import blocked_reason
        blocked = blocked_reason(lead)
    except Exception:                                            # noqa: BLE001
        blocked = None
    return {"sms": sms and not dnc, "email": email and not dnc, "dnc": dnc, "outreach_blocked": blocked,
            "is_test": bool(getattr(lead, "is_test", False))}


def unknown_slots(vertical: str, active: Dict[str, ConversationMemoryItem], lead: Lead,
                  asked: List[str]) -> List[Dict[str, str]]:
    out = []
    for slot, question, keys in SLOTS.get(vertical, SLOTS[X.GENERAL]):
        known = False
        for k in keys:
            if k.startswith("lead."):
                if getattr(lead, k[5:], None):
                    known = True
            elif k in active:
                known = True
        if not known:
            out.append({"slot": slot, "question": question,
                        "already_asked": any(set(X.content_words(question)) & set(X.content_words(a)) and
                                             len(set(X.content_words(question)) & set(X.content_words(a))) >= 2
                                             for a in asked[-3:])})
    return out


def next_best_action(st: ConversationState, ctx: Dict[str, Any]) -> Dict[str, str]:
    if st.state == "stopped":
        return {"action": "do_nothing", "reason": "They asked us to stop. No outreach on any channel."}
    if st.needs_human_reason:
        return {"action": "human_takeover", "reason": st.needs_human_reason}
    if st.mode == MODE_HUMAN_ACTIVE:
        return {"action": "wait", "reason": "A team member is handling this conversation."}
    if ctx["open_questions"] and (st.state in ("question", "waiting_on_staff", "objection", "interested")
                                  or _latest_inbound_after_outbound(st)):
        return {"action": "reply", "reason": "Answer their open question: “%s”" % ctx["open_questions"][0]["value"]}
    fu = ctx["follow_up"]
    if st.state == "appointment_ready":
        if "wants_call" == st.current_intent:
            return {"action": "call", "reason": "They asked for a call."}
        return {"action": "schedule", "reason": "They want to meet - offer specific times."}
    if fu and fu.get("date"):
        if st.state == "not_now":
            return {"action": "nurture", "reason": "Not now - come back %s (%s)." % (fu["value"], fu["date"])}
        if fu["date"] <= ctx["today"]:
            return {"action": "call" if "phone" in (ctx.get("preferred_channel") or "") else "reply",
                    "reason": "They asked us to follow up %s - that is today." % fu["value"]}
        return {"action": "wait", "reason": "They asked us to follow up %s (%s). Nothing before then." % (fu["value"], fu["date"])}
    if fu and not fu.get("date"):
        return {"action": "wait", "reason": "Waiting until they %s." % fu["value"]}
    if st.state == "closed":
        return {"action": "do_nothing", "reason": "They said they are not interested."}
    if st.state == "waiting_on_customer":
        if st.health == "stale":
            return {"action": "nurture", "reason": "No reply in %d+ days." % STALE_DAYS}
        return {"action": "wait", "reason": "Waiting on their reply."}
    if st.state in ("interested", "waiting_on_staff", "engaged", "objection"):
        nbq = ctx.get("next_best_question")
        if nbq:
            return {"action": "request_info", "reason": "Ask: %s" % nbq}
        return {"action": "reply", "reason": "They are waiting on us."}
    return {"action": "reply" if _latest_inbound_after_outbound(st) else "wait",
            "reason": "Respond to their latest message." if _latest_inbound_after_outbound(st) else "Nothing new from them."}


def decide_ai(ctx: Dict[str, Any], channel: str = "email") -> Dict[str, Any]:
    """May automation reply right now? Never True when a person should."""
    st = ctx["state"]
    def no(code, reason, human=True):
        return {"allowed": False, "code": code, "reason": reason, "human_review": human}
    if st["state"] == "stopped" or ctx["consent"]["dnc"]:
        return no("stop", "Customer asked us to stop / DNC - nothing may be sent", human=False)
    if st["needs_human_reason"]:
        return no("human_review", st["needs_human_reason"])
    if st["mode"] == MODE_HUMAN_ACTIVE:
        return no("human_active", st.get("mode_reason") or "A team member is handling this conversation", human=False)
    if st["mode"] == MODE_AI_PAUSED:
        return no("ai_paused", "AI is paused for this conversation", human=False)
    if not ctx["pending_inbound"]:
        return no("nothing_to_answer", "The last message was ours - nothing to answer", human=False)
    if channel == "sms" and not ctx["consent"]["sms"]:
        return no("no_sms_consent", "No SMS consent on file - an email reply is not SMS consent", human=False)
    if channel == "email" and not ctx["consent"]["email"]:
        return no("no_email", "No usable email address / email opted out", human=False)
    # Bursts ("Yes" / "but my wife" / "call me tomorrow") are handled by the
    # caller: each earlier message defers to the newest, and the newest answers
    # the whole turn (ai_conversation_service._intel_gate). A time-based "still
    # typing" hold here would also hold the LAST message forever.
    if ctx["current_intent"] in ("not_interested",):
        return no("closed", "They said no - no automated reply", human=False)
    return {"allowed": True, "code": "ok", "reason": "Clear to reply", "human_review": False}


def build_context(db: Session, lead: Lead, *, now: Optional[datetime] = None, do_sync: bool = True,
                  recent: int = 8) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    st = sync(db, lead, now=now) if do_sync else get_state(db, lead)
    vertical = lead_vertical(db, lead)
    today = workspace_today(db, lead.organization_id, now)
    all_items = _items(db, lead)
    active = {}
    for i in all_items:
        if i.status == ACTIVE:
            active[i.key] = i
    events = load_events(db, lead, limit=60)
    last_out_idx = max([n for n, e in enumerate(events) if e.direction == "outbound"], default=-1)
    pending = [e for e in events[last_out_idx + 1:] if e.direction == "inbound"]
    burst = len(pending) >= 2 and (pending[-1].at - pending[-2].at).total_seconds() <= COALESCE_SECONDS
    asked = [i.value or "" for i in all_items if i.category == CAT_OUR_QUESTION]

    def cat(c, statuses=(ACTIVE,)):
        return [_fmt_item(i) for i in all_items if i.category == c and i.status in statuses]

    fu_items = [i for i in all_items if i.key == "follow_up.when"]
    fu_active = next((i for i in reversed(fu_items) if i.status == ACTIVE), None)
    follow_up = None
    if fu_active:
        follow_up = _fmt_item(fu_active)
        follow_up["history"] = [_fmt_item(i) for i in fu_items if i.id != fu_active.id]
    unknowns = unknown_slots(vertical, active, lead, asked)
    nbq = next((u["question"] for u in unknowns if not u["already_asked"]), None)
    pref_channel = active.get("pref.channel").value if active.get("pref.channel") else None
    owner = None
    if getattr(lead, "assigned_to_id", None):
        u = db.query(User).filter(User.id == lead.assigned_to_id).first()
        owner = u.full_name if u else None
    ctx: Dict[str, Any] = {
        "lead": {"id": lead.id, "name": " ".join(p for p in (lead.first_name, lead.last_name) if p) or None,
                 "organization_id": lead.organization_id, "vertical": vertical, "status": lead.status,
                 "tier": getattr(lead, "tier", None), "owner": owner, "source": getattr(lead, "source", None)},
        "today": today.isoformat(),
        "voice": VOICE.get(vertical, VOICE[X.GENERAL]),
        "consent": _consent(db, lead),
        "current_intent": st.current_intent,
        "vertical_intents": (active["intent.vertical"].value.split(", ") if active.get("intent.vertical") else []),
        "known_facts": [_fmt_item(i) for i in active.values() if i.category == CAT_FACT and not i.key.startswith("flag.")],
        "inferences": cat(CAT_INFERENCE),
        "unknowns": unknowns,
        "open_questions": cat(CAT_QUESTION),
        "answered_questions": cat(CAT_QUESTION, (RESOLVED,)),
        "objections": cat(CAT_OBJECTION, (ACTIVE, RESOLVED)),
        "commitments": cat(CAT_COMMITMENT),
        "preferences": cat(CAT_PREFERENCE),
        "preferred_channel": pref_channel,
        "follow_up": follow_up,
        "flags": [_fmt_item(i) for i in active.values() if i.key.startswith("flag.") or i.key == "consent.stop_requested"],
        "pending_inbound": [{"at": e.at.isoformat() + "Z", "channel": e.channel, "text": e.text} for e in pending],
        "burst": burst,
        "recent_messages": [{"at": e.at.isoformat() + "Z", "direction": e.direction, "channel": e.channel,
                             "actor": e.actor, "text": e.text[:600]} for e in events[-recent:]],
        "next_best_question": nbq,
        "state": {"state": st.state, "mode": st.mode, "mode_reason": st.mode_reason, "health": st.health,
                  "priority": st.priority, "needs_human_reason": st.needs_human_reason,
                  "last_meaningful_event": st.last_meaningful_event,
                  "last_meaningful_at": st.last_meaningful_at.isoformat() + "Z" if st.last_meaningful_at else None},
    }
    ctx["next_best_action"] = next_best_action(st, ctx)
    ctx["ai_decision"] = {ch: decide_ai(ctx, ch) for ch in ("sms", "email")}
    ctx["summary"] = summarize(ctx)
    ctx["timeline"] = timeline(db, lead, events, all_items)
    return ctx


def summarize(ctx: Dict[str, Any]) -> Dict[str, Any]:
    facts = ["%s: %s" % (f["label"], f["value"]) for f in ctx["known_facts"]]
    want = []
    if ctx["vertical_intents"]:
        want.append(", ".join(v.replace("_", " ") for v in ctx["vertical_intents"]))
    if ctx["current_intent"]:
        want.append(ctx["current_intent"].replace("_", " "))
    told = [c["value"] for c in ctx["commitments"]]
    open_items = [q["value"] for q in ctx["open_questions"]] + \
        ["Objection: %s" % o["quote"] for o in ctx["objections"] if o["status"] == ACTIVE]
    if ctx["follow_up"]:
        open_items.append("Follow up %s%s" % (ctx["follow_up"]["value"],
                                              " (%s)" % ctx["follow_up"]["date"] if ctx["follow_up"]["date"] else ""))
    st = ctx["state"]
    situation = {"stopped": "Stopped - they asked not to be contacted.",
                 "human_review": "Needs a person: %s." % (st["needs_human_reason"] or ""),
                 "closed": "They said no.",
                 "not_now": "Not now - a future opportunity.",
                 "follow_up_needed": "They asked us to follow up later.",
                 "appointment_ready": "Ready to meet / talk.",
                 "appointment_set": "Appointment set.",
                 "question": "They asked something and are waiting on an answer.",
                 "objection": "They raised a concern.",
                 "interested": "Interested.",
                 "waiting_on_staff": "Waiting on us.",
                 "waiting_on_customer": "Waiting on their reply.",
                 "new": "New - no conversation yet."}.get(st["state"], st["state"])
    return {"current_situation": situation, "what_they_want": "; ".join(want) or None,
            "important_facts": facts, "questions_they_asked": [q["value"] for q in ctx["open_questions"] + ctx["answered_questions"]],
            "objections": [o["quote"] for o in ctx["objections"]], "what_we_told_them": told,
            "open_items": open_items, "next_action": ctx["next_best_action"],
            "last_meaningful_interaction": st["last_meaningful_event"]}


def timeline(db, lead, events: List[Event], items: List[ConversationMemoryItem]) -> List[Dict[str, Any]]:
    out = []
    for e in events:
        who = {"customer": "Customer", "human": "Team", "automation": "Automated", "staff_note": "Note"}[e.actor]
        out.append({"at": e.at.isoformat() + "Z", "kind": "message" if e.direction != "note" else "note",
                    "label": "%s %s (%s)" % (who, "wrote" if e.direction != "note" else "note", e.channel),
                    "text": e.text[:240]})
    for i in items:
        if i.category in (CAT_OBJECTION, CAT_FOLLOW_UP, CAT_COMMITMENT) or i.key.startswith(("flag.", "consent.")):
            label = {CAT_OBJECTION: "Objection identified", CAT_FOLLOW_UP: "Follow-up requested",
                     CAT_COMMITMENT: "We promised"}.get(i.category, "Flag")
            if i.status == SUPERSEDED:
                label += " (later changed)"
            out.append({"at": i.observed_at.isoformat() + "Z", "kind": "memory", "label": label,
                        "text": i.value if i.category != CAT_FOLLOW_UP else "%s%s" % (
                            i.value, " - %s" % i.value_date.date().isoformat() if i.value_date else "")})
    out.sort(key=lambda r: r["at"])
    return out[-60:]
