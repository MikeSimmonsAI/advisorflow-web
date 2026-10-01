"""ASK EVOAI — a scored intent parser over SUPPORTED agency questions.

How it stays honest
  * Every supported intent maps to ONE real query in app.services.agency.queries
    (or distribution.recommend), run with the caller's Ctx, so tenant scope and
    the advisor-sees-own-work rule are inherited, never re-implemented.
  * Every answer carries the exact filter it used (`filter.api` names the list
    endpoint + params that return the same rows wherever one exists), links to
    each record, DEMO flags, and an `insufficient` list when the data a
    question depends on is not stored.
  * Nothing here generates prose about a record it did not read. A question
    it cannot map returns supported:false with the closest supported
    questions as suggestions.

Parsing: normalise (lowercase, contractions, punctuation), correct typos
against the parser vocabulary (difflib, conservative cutoff), map synonyms to
canonical concepts, then score every intent pattern. An intent needs all of its
`require` concept groups; ties go to the higher weight. Entities (state, need
category, intent level, agent name, prospect name, recruit stage, application
status) are extracted from the same normalised tokens.
"""
import difflib
import re
from calendar import monthrange
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from app.services.agency import queries as Q
from app.services.agency.common import NEED_LABELS

# ── vocabulary ──────────────────────────────────────────────────────────────

# canonical concept -> surface words (single tokens; multi-word phrases go in PHRASES)
CONCEPTS: Dict[str, Tuple[str, ...]] = {
    "prospect": ("prospect", "prospects", "lead", "leads", "opportunity", "opportunities", "client",
                 "clients", "people", "family", "families", "contact", "contacts", "customer", "customers"),
    "agent": ("agent", "agents", "advisor", "advisors", "producer", "producers", "rep", "reps", "team"),
    "capacity": ("capacity", "available", "availability", "room", "bandwidth", "free", "space", "slack"),
    "application": ("application", "applications", "app", "apps", "case", "cases", "submission",
                    "submissions", "paperwork"),
    "stalled": ("stalled", "stall", "stuck", "stale", "slow", "aging", "idle", "sitting", "delayed"),
    "attention": ("attention", "priority", "priorities", "urgent", "focus", "triage", "important"),
    "today": ("today", "now", "morning", "tonight"),
    "recruit": ("recruit", "recruits", "recruiting", "candidate", "candidates", "hire", "hires",
                "hiring", "trainee", "trainees", "pipeline"),
    "activation": ("activation", "activate", "activated", "close", "near", "almost", "nearly",
                   "ready", "soon", "finishing"),
    "appointment": ("appointment", "appointments", "meeting", "meetings", "appt", "appts", "call",
                    "calls", "consultation", "consultations"),
    "confirm": ("confirm", "confirmation", "confirmed", "unconfirmed", "pending", "tentative"),
    "reassign": ("reassign", "reassigned", "reassignment", "reroute", "redistribute", "redistributed"),
    "high": ("high", "hot", "strong", "highest"),
    "medium": ("medium", "warm", "moderate"),
    "low": ("low", "cold", "weak"),
    "intent": ("intent", "interest", "interested", "motivated", "intention"),
    "uncontacted": ("uncontacted", "untouched", "unworked", "new"),
    "contacted": ("contacted", "contact", "reached", "called", "touched", "worked"),
    "negation": ("not", "no", "never", "without", "havent", "hasnt", "nobody", "none"),
    "unassigned": ("unassigned", "unowned", "unclaimed", "orphan", "orphaned"),
    "assigned": ("assigned", "owned", "own", "owner"),
    "task": ("task", "tasks", "todo", "todos", "followup", "followups", "reminder", "reminders"),
    "overdue": ("overdue", "late", "missed", "past", "behind", "expired"),
    "offer": ("offer", "offers", "offered", "assignment", "assignments", "handoff", "handoffs"),
    "acceptance": ("accept", "accepted", "acceptance", "awaiting", "waiting", "outstanding", "open", "pending",
                   "unanswered"),
    "timedout": ("timed", "timeout", "timedout", "expired", "escalated", "escalation", "declined",
                 "rejected", "bounced"),
    "review": ("review", "reviews", "annual", "anniversary", "checkup", "renewal", "renewals"),
    "policy": ("policy", "policies", "inforce", "book"),
    "month": ("month", "monthly"),
    "client_wait": ("client", "customer", "prospect", "insured", "applicant"),
    "waiting": ("waiting", "awaiting", "pending", "blocked", "need", "needs", "needing"),
    "stage": ("stage", "stages", "phase", "step", "funnel", "pipeline"),
    "who": ("who", "whom", "which"),
    "route": ("get", "take", "handle", "work", "receive", "assign", "route", "own", "best",
              "match", "recommend", "recommendation", "go"),
    "have": ("have", "has", "holding", "working", "carrying", "load", "workload", "plate", "queue",
             "doing", "book"),
    "show": ("show", "list", "find", "give", "which", "what", "who", "display", "pull", "see", "any"),
    "state": ("state", "states", "located", "living", "lives", "live", "from", "in"),
}

# phrase -> replacement token(s), applied on the normalised string before tokenising
PHRASES = [
    (r"\bfollow[\s-]?ups?\b", "followup"),
    (r"\bto[\s-]?dos?\b", "todo"),
    (r"\btimed[\s-]?out\b", "timedout"),
    (r"\btime[\s-]?out\b", "timedout"),
    (r"\bin[\s-]?force\b", "inforce"),
    (r"\bre[\s-]?assign", "reassign"),
    (r"\bnot (yet )?(been )?contacted\b", "uncontacted"),
    (r"\bnever (been )?contacted\b", "uncontacted"),
    (r"\bno (outreach|contact|touch|call)\b", "uncontacted"),
    (r"\bhave ?n[o']?t (been )?(contacted|reached|called|touched)\b", "uncontacted"),
    (r"\bhas ?n[o']?t (been )?(contacted|reached|called|touched)\b", "uncontacted"),
    (r"\bwaiting on (the )?(client|customer|applicant|insured)\b", "awaiting_client"),
    (r"\bawaiting (the )?(client|customer|applicant|insured)\b", "awaiting_client"),
    (r"\bon the client\b", "awaiting_client"),
    (r"\bwaiting on (the )?agent\b", "awaiting_agent"),
    (r"\bawaiting (the )?agent\b", "awaiting_agent"),
    (r"\brequirements? requested\b", "requirements_requested"),
    (r"\bthis month\b", "month"),
    (r"\bnext 30 days\b", "month"),
    (r"\bactive agents?\b", "active_agent"),
    (r"\bwho should (get|take|handle|work|receive)\b", "who route"),
    (r"\bwho('s| is) (best|right) for\b", "who route"),
    (r"\bwhat does\b", "what"),
    (r"\bwhat is on\b", "what have"),
    (r"\bon (his|her|their) plate\b", "have"),
]
CONTRACTIONS = [(r"n't\b", " not"), (r"'s\b", ""), (r"'re\b", " are"), (r"'ll\b", " will"),
                (r"'ve\b", " have"), (r"'d\b", " would"), (r"'m\b", " am")]

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
STATE_CODES = set(STATE_NAMES.values())
# two-letter codes that are also common English words: only accepted in UPPERCASE in the original
AMBIGUOUS_CODES = {"IN", "OR", "ME", "OK", "HI", "OH", "AL", "LA", "MA", "PA", "DE", "CO", "ID",
                   "MI", "MO", "MS", "MT", "NE", "WA", "VA", "GA", "AR", "IA", "SC", "ND", "SD"}

NEED_SYNONYMS = {
    "family_protection": ("family protection", "family", "protection", "income protection",
                          "life insurance", "dependents", "kids"),
    "retirement": ("retirement", "retire", "retiring", "pension", "401k"),
    "living_benefits": ("living benefits", "living benefit", "critical illness", "chronic illness"),
    "business_owner": ("business owner", "business", "entrepreneur", "entrepreneurs", "builder",
                       "builders", "key person", "buy sell"),
    "final_expense": ("final expense", "burial", "funeral"),
    "mortgage_protection": ("mortgage protection", "mortgage", "homeowner", "homeowners"),
    "wealth_transfer": ("wealth transfer", "legacy", "estate", "inheritance"),
}

APP_STATUS_WORDS = {
    "awaiting_client": "awaiting_client", "awaiting_agent": "awaiting_agent",
    "requirements_requested": "requirements_requested", "underwriting": "underwriting",
    "submitted": "submitted", "draft": "draft", "drafts": "draft", "prepared": "prepared",
    "approved": "approved", "declined": "declined", "withdrawn": "withdrawn", "issued": "issued",
    "modified": "modified",
}

# Questions about data this system does NOT store. Answered as insufficient
# information rather than "unsupported", because the honest answer is "not recorded".
NOT_STORED = [
    (r"\b(premium|premiums|face amount|death benefit|coverage amount|commission|commissions|"
     r"revenue|income(?! protection)|salary|net worth|cash value)\b",
     "Premiums, coverage amounts, commissions and personal finances are not stored in agency records."),
    (r"\b(health|medical|diagnos\w*|condition|conditions|medication|smoker|underwriting decision|"
     r"rate class|approval odds|likely to be approved)\b",
     "Health, medical and underwriting-outcome information is not stored, and EvoAI does not "
     "predict underwriting or eligibility."),
    (r"\b(qualif\w*|eligib\w*|suitab\w*|best product|which product|what product|should buy)\b",
     "Suitability and eligibility are not assessed by EvoAI; a licensed agent determines fit with "
     "the client and the carrier decides eligibility."),
    (r"\b(conversion rate|close rate|closing rate|win rate|forecast|predict\w*)\b",
     "Conversion history and forecasts are not tracked, so there is no basis for that number."),
    (r"\b(quote|quotes|pricing|price|cost)\b",
     "Carrier quotes and pricing are not stored in agency records."),
]

STOPWORDS = {"the", "a", "an", "of", "for", "to", "me", "my", "our", "us", "we", "i", "is", "are",
             "be", "been", "do", "does", "did", "with", "and", "or", "on", "at", "by", "that", "this",
             "these", "those", "there", "any", "all", "every", "please", "can", "could", "would",
             "you", "should", "right", "currently", "still", "yet", "about", "it", "them", "they",
             "how", "many", "much", "up", "out", "get", "got", "have", "has", "who", "what", "which",
             "show", "list", "find", "give", "tell", "need", "needs", "whose", "where", "when",
             "will", "am", "was", "were", "into", "over", "so", "just", "some", "more", "most",
             "evoai", "hey", "hi", "hello", "thanks"}


def _vocab() -> List[str]:
    words = set()
    for ws in CONCEPTS.values():
        words.update(ws)
    for syns in NEED_SYNONYMS.values():
        for s in syns:
            words.update(s.split())
    words.update(w for name in STATE_NAMES for w in name.split())
    words.update(APP_STATUS_WORDS)
    return sorted(w for w in words if len(w) >= 3)


VOCAB = _vocab()
WORD_TO_CONCEPTS: Dict[str, List[str]] = {}
for _c, _ws in CONCEPTS.items():
    for _w in _ws:
        WORD_TO_CONCEPTS.setdefault(_w, []).append(_c)


def normalise(question: str) -> str:
    s = (question or "").lower().replace("’", "'")
    for rx, rep in CONTRACTIONS:
        s = re.sub(rx, rep, s)
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", s)
    s = re.sub(r"[^a-z0-9_'\s-]", " ", s)
    s = s.replace("'", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def correct_tokens(tokens: List[str], extra_vocab: Tuple[str, ...] = ()) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Typo correction against the parser vocabulary (+ names). Conservative:
    only tokens >= 4 chars that are not already known, cutoff 0.8."""
    vocab = VOCAB + [v for v in extra_vocab if v]
    known = set(vocab) | STOPWORDS
    out, fixes = [], []
    for t in tokens:
        if len(t) >= 4 and t not in known and not t.isdigit():
            m = difflib.get_close_matches(t, vocab, n=1, cutoff=0.8)
            if m:
                fixes.append((t, m[0]))
                out.append(m[0])
                continue
        out.append(t)
    return out, fixes


class Parsed:
    def __init__(self, question: str, names: Tuple[str, ...] = ()):
        self.question = question or ""
        base = normalise(question)
        toks, self.corrections = correct_tokens(base.split(), names)
        text = " ".join(toks)
        for rx, rep in PHRASES:
            text = re.sub(rx, rep, text)
        self.text = text
        self.tokens = text.split()
        self.concepts = set()
        for t in self.tokens:
            self.concepts.update(WORD_TO_CONCEPTS.get(t, ()))
        if "uncontacted" in self.tokens:
            self.concepts.add("uncontacted")
        if "awaiting_client" in self.tokens:
            self.concepts.update(("waiting", "client_wait", "application"))
        if "who" in self.tokens and "route" in self.tokens:
            self.concepts.update(("who", "route"))

    def has(self, concept: str) -> bool:
        return concept in self.concepts


# ── intents ─────────────────────────────────────────────────────────────────
# key, example question, require (all groups: each group = any-of concepts), bonus concepts, weight

INTENTS = [
    ("uncontacted_high_intent", "Show every high-intent family protection opportunity not contacted.",
     [("high",), ("uncontacted",)], ("intent", "prospect"), 6),
    ("agents_with_capacity", "Which agents have capacity?", [("agent",), ("capacity",)], ("who",), 5),
    ("stalled_applications", "What applications are stalled?", [("application",), ("stalled",)], (), 5),
    ("attention_today", "What needs my attention today?", [("attention",)], ("today",), 3),
    ("recruits_near_activation", "Show recruiting candidates close to activation.",
     [("recruit",), ("activation",)], (), 5),
    ("appointments_need_confirmation", "Which appointments need confirmation?",
     [("appointment",), ("confirm",)], (), 5),
    ("prospects_to_reassign", "Which prospects should be reassigned?", [("reassign",)], ("prospect",), 5),
    ("offers_awaiting_acceptance", "Which offers are awaiting acceptance?",
     [("offer",), ("acceptance",)], ("agent",), 5),
    ("offers_timed_out", "Which offers timed out or were escalated?", [("offer",), ("timedout",)], (), 6),
    ("overdue_tasks", "Which tasks are overdue?", [("task",), ("overdue",)], (), 5),
    ("reviews_due_month", "Which annual reviews are due this month?", [("review",)], ("policy", "month"), 4),
    ("applications_awaiting_client", "Which applications are waiting on the client?",
     [("application",), ("waiting",), ("client_wait",)], (), 6),
    ("applications_by_status", "Which applications are in underwriting?", [("application",)], (), 2),
    ("recruits_by_stage", "Which recruits are in licensing?", [("recruit",)], ("stage",), 2),
    ("recommend_agent_for_prospect", "Who should get Jordan Rivera?", [("who",), ("route",)], (), 3),
    ("agent_open_work", "What does Maya have open?", [("have",)], ("agent", "show"), 2),
    ("unassigned_prospects", "Which prospects are unassigned?", [("unassigned",)], ("prospect",), 4),
    ("appointments_today", "What appointments are on today?", [("appointment",), ("today",)], (), 4),
    ("prospects_filtered", "Show high-intent retirement prospects in TX.", [("prospect",)], (), 1),
]
SUPPORTED_QUESTIONS = [ex for _, ex, _, _, _ in INTENTS]
EXAMPLES = {k: ex for k, ex, _, _, _ in INTENTS}


# ── entity extraction ───────────────────────────────────────────────────────

def extract_state(question: str, parsed: Parsed) -> Optional[str]:
    low = " %s " % parsed.text
    for name, code in sorted(STATE_NAMES.items(), key=lambda kv: -len(kv[0])):
        if " %s " % name in low:
            return code
    for tok in re.findall(r"\b[A-Za-z]{2}\b", question or ""):
        up = tok.upper()
        if up in STATE_CODES and (up not in AMBIGUOUS_CODES or tok == up):
            return up
    return None


def extract_need(parsed: Parsed) -> Optional[str]:
    low = " %s " % parsed.text
    best = None
    for key, syns in NEED_SYNONYMS.items():
        for s in sorted(syns, key=len, reverse=True):
            if " %s " % s in low and (best is None or len(s) > best[1]):
                best = (key, len(s))
    return best[0] if best else None


def extract_intent_level(parsed: Parsed) -> Optional[str]:
    for lvl in ("high", "medium", "low"):
        if parsed.has(lvl) and (parsed.has("intent") or parsed.has("prospect") or "uncontacted" in parsed.tokens):
            return lvl
    return None


def extract_app_status(parsed: Parsed) -> Optional[str]:
    for t in parsed.tokens:
        if t in APP_STATUS_WORDS:
            return APP_STATUS_WORDS[t]
    return None


def extract_stage(parsed: Parsed, stages: List[str]) -> Optional[str]:
    for st in sorted(stages, key=len, reverse=True):
        variants = {st, st.replace("_", " "), st.rstrip("s"), st + "s"}
        if any(re.search(r"\b%s\b" % re.escape(v), parsed.text) for v in variants):
            return st
    return None


NAME_NOISE = {"demo", "test", "sample", "agent", "advisor", "owner", "manager", "jr", "sr", "mr", "ms", "mrs", "dr"}


def match_agent(parsed: Parsed, agents: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Agents are resolved ONLY against the agents the caller can see
    (Q.list_agents applies the role rule). Returns (unique match, all matches)."""
    hits = []
    toks = set(parsed.tokens)
    for a in agents:
        parts = [p for p in re.split(r"\s+", (a.get("name") or "").lower())
                 if len(p) >= 2 and p not in NAME_NOISE]
        if parts and (any(p in toks for p in parts)):
            hits.append(a)
    return (hits[0] if len(hits) == 1 else None), hits


# ── helpers ─────────────────────────────────────────────────────────────────

def _api(path: str, params: Dict[str, Any]) -> str:
    clean = {k: ("true" if v is True else v) for k, v in params.items() if v not in (None, False, "")}
    return path + ("?" + urlencode(clean) if clean else "")


def _flt(description: str, path: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
    return {"description": description, "api": _api(path, params) if path else None,
            "params": {k: v for k, v in params.items() if v not in (None, False, "")}}


def _item(label, lk, is_demo, kind=None):
    return {"label": label, "link": lk, "is_demo": bool(is_demo), "kind": kind}


def _prospect_items(rows):
    from app.services.agency.intelligence import link
    return [_item("%s%s%s" % (r["name"], " - %s" % r["state"] if r.get("state") else "",
                              " (%s intent)" % r["intent_level"] if r.get("intent_level") else ""),
                  link("prospect", r["id"]), r["is_demo"], "prospect") for r in rows]


def _task_link(t):
    from app.services.agency.intelligence import link
    if t.lead_id:
        return link("prospect", t.lead_id)
    if t.application_id:
        return link("application", t.application_id)
    if t.policy_id:
        return link("policy", t.policy_id)
    if t.recruit_id:
        return link("recruit", t.recruit_id)
    return link("task", t.id)


NEEDS_A_NAME = {"recommend_agent_for_prospect", "agent_open_work"}


def _closest(parsed: Parsed, n: int = 4) -> List[str]:
    """Supported questions ranked by closeness to what was asked."""
    scored = []
    for key, ex, req, bonus, _w in INTENTS:
        if key in NEEDS_A_NAME:
            continue  # their examples name a person; a chip would ask about someone not in this book
        ep = Parsed(ex)
        overlap = len(parsed.concepts & ep.concepts)
        ratio = difflib.SequenceMatcher(None, parsed.text, ep.text).ratio()
        scored.append((overlap * 2 + ratio, ex))
    scored.sort(key=lambda s: -s[0])
    return [ex for _, ex in scored[:n]]


def score_intents(parsed: Parsed, entities: Dict[str, Any]) -> List[Tuple[float, str]]:
    out = []
    for key, _ex, req, bonus, weight in INTENTS:
        if not all(any(parsed.has(c) for c in grp) for grp in req):
            continue
        s = float(weight) + sum(1 for b in bonus if parsed.has(b))
        # entity-driven adjustments
        if key == "agent_open_work":
            if not entities.get("agent_hits"):
                continue
            s += 4
        if key == "recommend_agent_for_prospect" and entities.get("agent_hits") and not parsed.has("prospect"):
            s -= 1
        if key == "prospects_filtered":
            if not (entities.get("state") or entities.get("need") or entities.get("intent_level")):
                continue
            s += sum(1 for k in ("state", "need", "intent_level") if entities.get(k))
        if key == "applications_by_status":
            if not entities.get("app_status"):
                continue
            s += 2
        if key == "recruits_by_stage":
            if not entities.get("stage"):
                continue
            s += 2
        if key == "uncontacted_high_intent" and not parsed.has("prospect") and not entities.get("need"):
            s -= 1
        if key == "attention_today" and (parsed.has("appointment") or parsed.has("task")):
            s -= 3
        out.append((s, key))
    out.sort(key=lambda t: -t[0])
    return out


def parse(question: str, names: Tuple[str, ...] = (), stages: Optional[List[str]] = None,
          agents: Optional[List[Dict[str, Any]]] = None) -> Tuple[Optional[str], Parsed, Dict[str, Any]]:
    parsed = Parsed(question, names)
    ents: Dict[str, Any] = {
        "state": extract_state(question, parsed),
        "need": extract_need(parsed),
        "intent_level": extract_intent_level(parsed),
        "app_status": extract_app_status(parsed),
        "stage": extract_stage(parsed, stages or []),
    }
    if agents is not None:
        ents["agent"], ents["agent_hits"] = match_agent(parsed, agents)
    scored = score_intents(parsed, ents)
    return (scored[0][1] if scored else None), parsed, ents


def parse_intent(question: str) -> Optional[str]:
    """Context-free parse (no agent names) — used by tests / the legacy wrapper."""
    from app.services.agency.common import DEFAULT_RECRUIT_STAGES
    return parse(question, stages=DEFAULT_RECRUIT_STAGES, agents=[])[0]


# ── answer builders ─────────────────────────────────────────────────────────

def _prospect_name_candidates(ctx: Q.Ctx, parsed: Parsed, agents: List[Dict[str, Any]]):
    """Tokens left after removing intent vocabulary and agent names -> scoped
    name search. Returns the matching prospect rows (deduplicated)."""
    agent_parts = {p for a in agents for p in (a.get("name") or "").lower().split()} - {"demo"}
    skip = STOPWORDS | set(WORD_TO_CONCEPTS) | agent_parts | {"route"}
    toks = [t for t in normalise(parsed.question).split() if len(t) >= 3 and t not in skip]
    seen, rows = set(), []
    for t in toks:
        for r in Q.list_prospects(ctx, q=t):
            if r["id"] not in seen:
                seen.add(r["id"])
                r["_hits"] = 1
                rows.append(r)
            else:
                next(x for x in rows if x["id"] == r["id"])["_hits"] += 1
    if rows:
        top = max(r["_hits"] for r in rows)
        rows = [r for r in rows if r["_hits"] == top]
    return toks, rows


def _answer(ctx: Q.Ctx, intent: str, parsed: Parsed, ents: Dict[str, Any],
            agents: List[Dict[str, Any]]) -> Dict[str, Any]:
    from app.services.agency.intelligence import attention, link
    items: List[Dict[str, Any]] = []
    insufficient: List[str] = []
    cfg = ctx.cfg

    if intent == "uncontacted_high_intent":
        need = ents.get("need")
        rows = Q.list_prospects(ctx, intent="high", need=need, uncontacted=True)
        items = _prospect_items(rows)
        answer = "%d high-intent%s prospect(s) with no recorded outbound contact." % (
            len(items), " %s" % NEED_LABELS[need] if need else "")
        flt = _flt("intent_level = high%s AND no sent message AND no recorded last contact date"
                   % (" AND need includes %s" % need if need else ""), "/agency/prospects",
                   {"intent": "high", "need": need, "uncontacted": True})
        missing = Q.count_prospects(ctx) - Q.count_prospects(ctx, intent="high") - \
            Q.count_prospects(ctx, intent="medium") - Q.count_prospects(ctx, intent="low")
        if missing > 0:
            insufficient.append("%d prospect(s) have no recorded intent level, so they cannot be "
                                "counted as high-intent." % missing)
        note = "'Contacted' means a sent message or a recorded last contact date; simulated sends do not count."
    elif intent == "agents_with_capacity":
        rows = Q.list_agents(ctx, with_capacity=True)
        items = [_item("%s (%d of %d active)" % (r["name"], r["active_count"], r["max_active"]),
                       link("agent", r["user_id"]), r["is_demo"], "agent") for r in rows]
        answer = "%d agent(s) are active, available and under their configured cap." % len(items)
        flt = _flt("agent active AND marked available AND active prospects < max_active", "/agency/agents",
                   {"with_capacity": True})
        note = "Capacity uses each agent's configured max_active (or the workspace default %d)." % cfg["max_active_per_agent"]
    elif intent == "stalled_applications":
        rows = Q.list_applications(ctx, stalled=True)
        items = [_item("%s - %s %d days" % (r["prospect"]["name"], r["status"], r["days_in_status"]),
                       link("application", r["id"]), r["is_demo"], "application") for r in rows]
        answer = "%d application(s) have been in the same open status for %d+ days." % (len(items), cfg["stalled_days"])
        flt = _flt("open application AND days in current status >= %d" % cfg["stalled_days"],
                   "/agency/applications", {"stalled": True})
        note = None
    elif intent == "attention_today":
        rows = attention(ctx)
        items = [_item(r["title"], r["link"], r["is_demo"], r["kind"]) for r in rows]
        answer = "%d item(s) need attention." % len(items)
        flt = _flt("the attention feed: overdue response targets, timed-out/escalated offers, unassigned "
                   "prospects, stalled applications, unconfirmed appointments, workload, overdue tasks, "
                   "licensing milestones, annual reviews", "/agency/attention", {})
        note = None
    elif intent == "recruits_near_activation":
        rows = Q.list_recruits(ctx, near_activation=True)
        items = [_item("%s - %s" % (r["name"], r["stage"]), link("recruit", r["id"]), r["is_demo"], "recruit")
                 for r in rows]
        answer = "%d recruit(s) are in the last stages before active agent." % len(items)
        flt = _flt("recruit stage within the last 3 configured stages, not yet %s" % cfg["recruit_stages"][-1],
                   "/agency/recruits", {"near_activation": True})
        note = None
    elif intent == "appointments_need_confirmation":
        rows = Q.list_appointments(ctx, needs_confirmation=True)
        items = [_item("%s - %s" % (r["prospect"]["name"], r["starts_at"]), link("appointment", r["id"]),
                       r["is_demo"], "appointment") for r in rows]
        answer = "%d upcoming appointment(s) are still pending confirmation." % len(items)
        flt = _flt("status = pending AND starts_at >= now", "/agency/appointments", {"needs_confirmation": True})
        note = None
    elif intent == "appointments_today":
        rows = Q.list_appointments(ctx, range_="today")
        items = [_item("%s - %s (%s)" % (r["prospect"]["name"], r["starts_at"], r["status"]),
                       link("appointment", r["id"]), r["is_demo"], "appointment") for r in rows]
        answer = "%d appointment(s) today (UTC day)." % len(items)
        flt = _flt("starts_at within today (UTC)", "/agency/appointments", {"range": "today"})
        note = None
    elif intent == "prospects_to_reassign":
        rows = (Q.list_prospects(ctx, assignment="timed_out") + Q.list_prospects(ctx, assignment="escalated")
                + Q.list_prospects(ctx, assignment="declined"))
        items = [_item("%s (%s)" % (r["name"], r["assignment_state"]), link("prospect", r["id"]),
                       r["is_demo"], "prospect") for r in rows]
        answer = "%d prospect(s) whose latest offer timed out, was declined, or was escalated." % len(items)
        flt = _flt("latest assignment state IN (timed_out, escalated, declined)", "/agency/prospects",
                   {"assignment": "timed_out|escalated|declined"})
        flt["api"] = None
        flt["apis"] = ["/agency/prospects?assignment=%s" % s for s in ("timed_out", "escalated", "declined")]
        note = None
    elif intent in ("offers_awaiting_acceptance", "offers_timed_out"):
        states = ("offered",) if intent == "offers_awaiting_acceptance" else ("timed_out", "escalated")
        rows = []
        for st in states:
            rows += Q.list_prospects(ctx, assignment=st)
        items = [_item("%s - %s%s" % (r["name"], r["assignment_state"].replace("_", " "),
                                      " to %s" % r["assigned_agent"]["name"] if r.get("assigned_agent") else ""),
                       link("prospect", r["id"]), r["is_demo"], "prospect") for r in rows]
        if intent == "offers_awaiting_acceptance":
            answer = "%d offer(s) are waiting for the agent to accept (timeout %d min)." % (
                len(items), cfg["acceptance_timeout_minutes"])
            flt = _flt("latest assignment state = offered", "/agency/prospects", {"assignment": "offered"})
        else:
            answer = "%d prospect(s) whose latest offer timed out or was escalated." % len(items)
            flt = _flt("latest assignment state IN (timed_out, escalated)", None, {})
            flt["apis"] = ["/agency/prospects?assignment=timed_out", "/agency/prospects?assignment=escalated"]
        note = "Run the sweep to time out expired offers; until then an expired offer still shows as offered."
    elif intent == "overdue_tasks":
        rows = Q.overdue_tasks(ctx)
        items = [_item("%s (due %s)" % (t.title, t.due_at.date().isoformat() if t.due_at else "?"),
                       _task_link(t), t.is_demo, "task") for t in rows]
        answer = "%d open task(s) are past their due date." % len(items)
        flt = _flt("task status = open AND due_at < now%s" % ("" if ctx.manager else " AND assigned to you"),
                   None, {"status": "open", "overdue": True})
        note = "Each task links to the record it belongs to. Tasks without a due date are never counted as overdue."
    elif intent == "reviews_due_month":
        this_month = parsed.has("month")
        if this_month:
            today = date.today()
            start, end = today.replace(day=1), today.replace(day=monthrange(today.year, today.month)[1])
            rows = Q.list_policies(ctx, review_month=start.strftime("%Y-%m"))
            desc = "annual_review_date between %s and %s" % (start.isoformat(), end.isoformat())
            flt = _flt(desc, "/agency/policies", {"review_month": start.strftime("%Y-%m")})
            answer = "%d annual review(s) fall in %s." % (len(rows), start.strftime("%B %Y"))
        else:
            rows = Q.list_policies(ctx, review_due=True)
            flt = _flt("annual_review_date <= today + %d days (includes overdue reviews)" % cfg["review_window_days"],
                       "/agency/policies", {"review_due": True})
            answer = "%d annual review(s) are due within %d days or overdue." % (len(rows), cfg["review_window_days"])
        rows.sort(key=lambda p: p["annual_review_date"] or "")
        items = [_item("%s - review %s" % (p["client"]["name"], p["annual_review_date"]), link("policy", p["id"]),
                       p["is_demo"], "policy") for p in rows]
        no_date = sum(1 for p in Q.list_policies(ctx) if not p["annual_review_date"])
        if no_date:
            insufficient.append("%d policy record(s) have no annual review date recorded." % no_date)
        note = None
    elif intent in ("applications_awaiting_client", "applications_by_status"):
        status = "awaiting_client" if intent == "applications_awaiting_client" else ents["app_status"]
        rows = Q.list_applications(ctx, status=status)
        items = [_item("%s - %s, %d days" % (r["prospect"]["name"], r["status"].replace("_", " "), r["days_in_status"]),
                       link("application", r["id"]), r["is_demo"], "application") for r in rows]
        answer = "%d application(s) with status %s." % (len(items), status.replace("_", " "))
        flt = _flt("application status = %s" % status, "/agency/applications", {"status": status})
        note = "Status is as recorded by the agent; EvoAI does not see carrier systems."
    elif intent == "recruits_by_stage":
        stage = ents["stage"]
        rows = Q.list_recruits(ctx, stage=stage)
        items = [_item("%s - %s%s" % (r["name"], r["stage"].replace("_", " "),
                                       ", %d overdue milestone(s)" % r["milestones_overdue"] if r["milestones_overdue"] else ""),
                       link("recruit", r["id"]), r["is_demo"], "recruit") for r in rows]
        answer = "%d recruit(s) in stage %s." % (len(items), stage.replace("_", " "))
        flt = _flt("recruit stage = %s" % stage, "/agency/recruits", {"stage": stage})
        note = "Exam and licensing status are as entered; there is no state-licensing integration."
    elif intent == "unassigned_prospects":
        rows = Q.list_prospects(ctx, unassigned=True)
        items = _prospect_items(rows)
        answer = "%d prospect(s) have no agent and no open offer." % len(items)
        flt = _flt("no assigned agent AND no open/escalated/accepted offer", "/agency/prospects", {"unassigned": True})
        note = None if ctx.manager else "As an agent you only see prospects assigned to you."
    elif intent == "prospects_filtered":
        st, need, lvl = ents.get("state"), ents.get("need"), ents.get("intent_level")
        un = parsed.has("unassigned")
        rows = Q.list_prospects(ctx, state=st, need=need, intent=lvl, unassigned=un or None)
        items = _prospect_items(rows)
        parts = []
        if lvl:
            parts.append("intent_level = %s" % lvl)
        if need:
            parts.append("need includes %s" % need)
        if st:
            parts.append("state = %s" % st)
        if un:
            parts.append("unassigned")
        answer = "%d prospect(s) match: %s." % (len(items), ", ".join(parts))
        flt = _flt(" AND ".join(parts), "/agency/prospects",
                   {"intent": lvl, "need": need, "state": st, "unassigned": un or None})
        if st:
            n = Q.count_prospects(ctx, state_missing=True)
            if n:
                insufficient.append("%d prospect(s) have no state recorded and cannot match a state filter." % n)
        if need:
            n = Q.count_prospects(ctx, need_missing=True)
            if n:
                insufficient.append("%d prospect(s) have no need category recorded." % n)
        if lvl:
            n = Q.count_prospects(ctx, intent_missing=True)
            if n:
                insufficient.append("%d prospect(s) have no intent level recorded." % n)
        note = None
    elif intent == "agent_open_work":
        ag = ents.get("agent")
        hits = ents.get("agent_hits") or []
        if not ag:
            items = [_item(a["name"], link("agent", a["user_id"]), a["is_demo"], "agent") for a in hits]
            return {"answer": "That name matches %d agents - which one?" % len(hits), "items": items,
                    "filter": _flt("agent name matches", "/agency/agents", {}), "insufficient":
                    ["More than one agent matches that name."], "note": None, "status": "insufficient_information"}
        aid = ag["user_id"]
        pros = Q.list_prospects(ctx, agent_id=aid)
        offers = [p for p in Q.list_prospects(ctx, assignment="offered") if (p.get("assigned_agent") or {}).get("id") == aid]
        apps = Q.list_applications(ctx, agent_id=aid, open_only=True)
        appts = Q.list_appointments(ctx, agent_id=aid, range_="upcoming")
        tasks = [t for t in Q.open_tasks(ctx) if t.assigned_user_id == aid]
        items = ([_item("Prospect: %s" % p["name"], link("prospect", p["id"]), p["is_demo"], "prospect") for p in pros]
                 + [_item("Application: %s - %s" % (a["prospect"]["name"], a["status"].replace("_", " ")),
                          link("application", a["id"]), a["is_demo"], "application") for a in apps]
                 + [_item("Appointment: %s - %s" % (a["prospect"]["name"], a["starts_at"]),
                          link("appointment", a["id"]), a["is_demo"], "appointment") for a in appts]
                 + [_item("Task: %s%s" % (t.title, " (due %s)" % t.due_at.date().isoformat() if t.due_at else ""),
                          _task_link(t), t.is_demo, "task") for t in tasks])
        answer = ("%s has %d assigned prospect(s), %d open application(s), %d upcoming appointment(s) "
                  "and %d open task(s)." % (ag["name"], len(pros), len(apps), len(appts), len(tasks)))
        if offers:
            answer += " %d offer(s) are waiting for %s to accept." % (len(offers), ag["name"])
        flt = _flt("assigned agent = %s (prospects assigned, open applications, upcoming appointments, open tasks)"
                   % ag["name"], None, {"agent_id": aid})
        flt["apis"] = ["/agency/prospects?agent_id=%s" % aid, "/agency/applications?agent_id=%s&open=true" % aid,
                       "/agency/appointments?range=upcoming&agent_id=%s" % aid]
        note = None
    elif intent == "recommend_agent_for_prospect":
        from app.services.agency import distribution as dist
        from app.models.models import Lead
        toks, rows = _prospect_name_candidates(ctx, parsed, agents)
        if not toks:
            return {"answer": "Insufficient information: name the prospect, e.g. 'Who should get Jordan Rivera?'",
                    "items": [], "filter": _flt("prospect name search", "/agency/prospects", {}),
                    "insufficient": ["No prospect name was given."], "note": None,
                    "status": "insufficient_information"}
        if not rows:
            return {"answer": "No prospect in this workspace matches '%s'." % " ".join(toks), "items": [],
                    "filter": _flt("prospect name/email/phone contains %s" % " or ".join(toks), "/agency/prospects",
                                   {"q": " ".join(toks)}),
                    "insufficient": ["No matching prospect record."], "note": None,
                    "status": "insufficient_information"}
        if len(rows) > 1:
            return {"answer": "%d prospects match - pick one to see the recommendation." % len(rows),
                    "items": _prospect_items(rows), "insufficient": ["More than one prospect matches that name."],
                    "filter": _flt("prospect name contains %s" % " or ".join(toks), "/agency/prospects",
                                   {"q": " ".join(toks)}), "note": None, "status": "insufficient_information"}
        p = rows[0]
        lead = ctx.db.query(Lead).filter(Lead.id == p["id"], Lead.organization_id == ctx.org_id).first()
        rec = dist.recommend(ctx.db, ctx.org_id, lead)
        items = [_item("Prospect: %s" % p["name"], link("prospect", p["id"]), p["is_demo"], "prospect")]
        r = rec["recommended"]
        if r:
            cand = next(c for c in rec["candidates"] if c["agent_id"] == r["agent_id"])
            items.append(_item("Recommended: %s - %s" % (r["name"], "; ".join(r["reasons"]) or "eligible"),
                               link("agent", r["agent_id"]), cand.get("is_demo"), "agent"))
            answer = "Recommended agent for %s: %s (score %s)." % (p["name"], r["name"], r["score"])
        else:
            answer = "No eligible agent for %s right now." % p["name"]
        for c in rec["candidates"]:
            if r and c["agent_id"] == r["agent_id"]:
                continue
            items.append(_item("%s: %s" % (c["name"], "eligible" if c["eligible"] else "blocked - " + "; ".join(c["blockers"])),
                               link("agent", c["agent_id"]), c.get("is_demo"), "agent"))
        if p.get("assigned_agent"):
            answer += " Already assigned to %s." % p["assigned_agent"]["name"]
        insufficient += ["Not used: %s" % u for u in rec["unavailable_factors"]]
        flt = _flt("distribution recommendation for prospect %s (enabled factors: %s)"
                   % (p["name"], ", ".join(cfg["factors_enabled"])),
                   "/agency/prospects/%s/recommendation" % p["id"], {})
        note = "Reasons come only from stored agent profiles, workload and assignment history."
    else:  # pragma: no cover - every INTENTS key is handled above
        raise ValueError(intent)
    if not items:
        answer += " Nothing matches right now."
    return {"answer": answer, "items": items, "filter": flt, "insufficient": insufficient, "note": note,
            "status": "insufficient_information" if (insufficient and not items) else "answered"}


def ask(ctx: Q.Ctx, question: str) -> Dict[str, Any]:
    agents = Q.list_agents(ctx)
    names = tuple(sorted({p.lower() for a in agents for p in (a.get("name") or "").split()
                          if len(p) >= 3 and p.lower() not in NAME_NOISE}))
    intent, parsed, ents = parse(question, names, ctx.cfg["recruit_stages"], agents)
    corrections = [{"from": a, "to": b} for a, b in parsed.corrections]
    base = {"question": question, "normalized": parsed.text, "corrections": corrections,
            "supported_questions": list(SUPPORTED_QUESTIONS)}
    not_stored = [msg for rx, msg in NOT_STORED if re.search(rx, normalise(question))]
    if not_stored and intent not in ("agent_open_work",):
        return dict(base, intent="not_stored", supported=True, status="insufficient_information",
                    answer="Insufficient information: the agency records EvoAI can read do not hold this.",
                    items=[], filter=None, insufficient=list(dict.fromkeys(not_stored)),
                    note="EvoAI answers only from stored agency records.", demo_data=False,
                    suggestions=_closest(parsed))
    if not intent:
        if parsed.has("have") and not ents.get("agent_hits") and re.search(r"\b[A-Z][a-z]{2,}\b", question[1:]):
            return dict(base, intent="agent_open_work", supported=True, status="insufficient_information",
                        items=[], filter=_flt("agent name matches an agent visible to you", "/agency/agents", {}),
                        insufficient=["No agent you can see matches that name."],
                        answer="Insufficient information: no agent visible to you matches that name.",
                        note=None if ctx.manager else "Agents see only their own work.", demo_data=False,
                        suggestions=_closest(parsed))
        return dict(base, intent=None, supported=False, status="unsupported", items=[], filter=None,
                    insufficient=[], answer="I can't answer that from agency records yet.",
                    note="Try one of the suggested questions.", demo_data=False,
                    suggestions=_closest(parsed))
    out = _answer(ctx, intent, parsed, ents, agents)
    entities = {k: v for k, v in ents.items() if k not in ("agent", "agent_hits") and v}
    if ents.get("agent"):
        entities["agent"] = {"id": ents["agent"]["user_id"], "name": ents["agent"]["name"]}
    demo = any(i.get("is_demo") for i in out["items"])
    return dict(base, intent=intent, supported=True, entities=entities, demo_data=demo,
                answered_from="workspace records" + (" (includes DEMO records)" if demo else ""),
                suggestions=[], **out)
