"""GROUNDED AI REPHRASING for the Opportunity Brief and the Conversation Copilot.

Rules first, always. The rules engine (brief.py / copilot.py) computes every
fact, inference and suggestion. This module may ask the model to REPHRASE one
already-computed text (the brief narrative, the copilot reply) using only the
facts it is handed, and then VERIFIES the output before it is shown:

  * no number that is not in the input (digits or number words, $, %),
  * no capitalised name / proper noun that is not in the input,
  * no email, phone or URL that is not in the input,
  * no insurance product, carrier-type or plan term that is not in the input,
  * no suitability / eligibility / underwriting / medical / guarantee claim
    unless the same term is already in the input,
  * bounded length.

Any failure - AI disabled, no provider, refusal, exception, or a verifier
violation - returns the rules text unchanged with generated_by="rules" and the
reason recorded in `ai.status`. Nothing here is required for the product (or a
test) to work, and nothing runs unless a signed-in user asked for it
(`assist=ai`), which is the gateway's MANUAL mode with that user as actor.
"""
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services import ai_gateway

FEATURE_BRIEF = "agency_brief_rephrase"
FEATURE_COPILOT = "agency_copilot_rephrase"
CAP_BRIEF = "lead_analysis"       # existing approved capability (gpt-4o-mini)
CAP_COPILOT = "draft_reply"       # existing approved capability (gpt-4o-mini)
MAX_CHARS = 900


def _get_client():
    """Test seam: None in production (the gateway's own client is used)."""
    return None


# ── verifier ────────────────────────────────────────────────────────────────

NUMBER_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
                "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
                "fifteen": "15", "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50",
                "hundred": "100", "thousand": "1000", "million": "1000000", "billion": "1000000000",
                "dozen": "12", "half": "0.5", "once": "1", "twice": "2", "first": "1", "second": "2",
                "third": "3", "single": "1", "double": "2", "triple": "3"}

PRODUCT_TERMS = [
    r"term life", r"whole life", r"universal life", r"\biul\b", r"indexed", r"variable life",
    r"annuit\w*", r"final expense", r"burial", r"mortgage protection", r"critical illness",
    r"disability insurance", r"long[- ]term care", r"\bltc\b", r"\brider\w*", r"cash value",
    r"death benefit", r"face amount", r"premium\w*", r"\bpolicy\b", r"\bpolicies\b", r"\bplan\b",
    r"medicare", r"medicaid", r"401\(?k\)?", r"\bira\b", r"\broth\b", r"carrier\w*", r"underwriter\w*",
]
CLAIM_TERMS = [
    r"qualif\w*", r"eligib\w*", r"suitab\w*", r"approv\w*", r"underwrit\w*", r"guarant\w*",
    r"diagnos\w*", r"medical\w*", r"health\w*", r"condition\w*", r"prescri\w*", r"medicat\w*",
    r"rate class", r"preferred plus", r"you need", r"you should (buy|get|purchase)", r"best option",
    r"recommend (you|that you)", r"will be covered", r"you('re| are) covered", r"insurable",
    r"declin\w*", r"denied|deny", r"cheapest", r"lowest (price|rate)", r"save (you )?money", r"tax[- ]free",
]
_COMMON = set("""a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from further had has
have having he her here hers herself him himself his how i if in into is it its itself just let me more most
my myself no nor not now of off on once only or other our ours ourselves out over own same she should so
some such than that the their theirs them themselves then there these they this those through to too under
until up very was we were what when where which while who whom why will with would you your yours yourself
yourselves hi hello thanks thank great sure absolutely understood happy glad sounds totally fair no okay ok
would could might may please let's lets that's it's i'm i'd i'll we'd we'll we're you're you'll you'd
prospect household recorded recommended next action source state stated intent level need needs category
concerns concern goals goal interest note summary inference inferences fact facts agent agents
yes noted good""".split())
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
URL = re.compile(r"(https?://\S+|www\.\S+|\b\w+\.(com|net|org|io|live|co)\b)", re.I)
PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def _numbers(text: str) -> set:
    out = set()
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        out.add(m.replace(",", "").rstrip("."))
    low = (text or "").lower()
    for w, d in NUMBER_WORDS.items():
        if re.search(r"\b%s\b" % w, low):
            out.add(d)
    return out


def _proper_nouns(text: str) -> List[str]:
    """Capitalised words. Sentence-initial words count only when they are not
    common English words (so "Thanks" is fine but "Acme" is not)."""
    out = []
    for sent in re.split(r"(?<=[.?!:;])\s+|\n+", text or ""):
        words = re.findall(r"[A-Za-z][A-Za-z'’-]*", sent)
        for i, w in enumerate(words):
            if not w[0].isupper():
                continue
            base = w.lower().strip("'’").replace("’", "'")
            if i == 0 or w == "I":
                if base in _COMMON or base.rstrip("s") in _COMMON:
                    continue
            elif base in ("i",):
                continue
            out.append(w.strip("'’"))
    return out


def verify(output: str, sources: Iterable[str],
           claim_sources: Optional[Iterable[str]] = None) -> Tuple[bool, List[str]]:
    """True when `output` introduces nothing that is not in `sources`.
    Suitability / eligibility / medical claim terms are checked against
    `claim_sources` only (the rules DRAFT): a prospect's own words about their
    health, quoted in the facts, never license the model to talk about it."""
    sources = list(sources)
    src = "\n".join(s for s in sources if s)
    claim_low = ("\n".join(s for s in (claim_sources if claim_sources is not None else sources) if s)).lower()
    src_low = src.lower()
    violations: List[str] = []
    out = (output or "").strip()
    if not out:
        return False, ["empty output"]
    if len(out) > MAX_CHARS:
        violations.append("output longer than %d characters" % MAX_CHARS)
    src_nums = _numbers(src)
    for n in sorted(_numbers(out) - src_nums):
        violations.append("number not in input: %s" % n)
    for w in _proper_nouns(out):
        if w.lower() not in src_low:
            violations.append("name/proper noun not in input: %s" % w)
    for rx, kind in ((EMAIL, "email"), (URL, "url"), (PHONE, "phone")):
        for m in rx.finditer(out):
            if m.group(0).lower() not in src_low:
                violations.append("%s not in input: %s" % (kind, m.group(0)))
    out_low = out.lower()
    for rx in PRODUCT_TERMS:
        m = re.search(rx, out_low)
        if m and not re.search(rx, src_low):
            violations.append("product term not in input: %s" % m.group(0))
    for rx in CLAIM_TERMS:
        m = re.search(rx, out_low)
        if m and not re.search(rx, claim_low):
            violations.append("suitability/eligibility/medical claim not in input: %s" % m.group(0))
    return (not violations), list(dict.fromkeys(violations))


# ── the call ────────────────────────────────────────────────────────────────

SYSTEM = ("You rephrase text for an insurance agency CRM. Rewrite the DRAFT so it reads naturally. "
          "Use ONLY information present in FACTS and DRAFT. Do not add numbers, names, products, "
          "carriers, prices, health statements, or any claim about suitability, eligibility, "
          "underwriting or approval. Do not give insurance advice. If you cannot improve it, "
          "return the DRAFT unchanged. Return only the rewritten text.")


def available() -> bool:
    if not ai_gateway.manual_enabled():
        return False
    return bool(_get_client() is not None or os.environ.get("OPENAI_API_KEY"))


def rephrase(*, kind: str, draft: str, facts: List[str], actor_id: Optional[str],
             org_id: Optional[str]) -> Dict[str, Any]:
    """Returns {"text", "generated_by", "ai": {status, violations}}. Never raises."""
    rules = {"text": draft, "generated_by": "rules"}
    if not draft:
        return dict(rules, ai={"status": "not_applicable", "violations": []})
    if not actor_id:
        return dict(rules, ai={"status": "unavailable", "reason": "no signed-in user", "violations": []})
    if not available():
        return dict(rules, ai={"status": "unavailable", "reason": "AI is not enabled for manual actions",
                               "violations": []})
    feature, cap = (FEATURE_BRIEF, CAP_BRIEF) if kind == "brief" else (FEATURE_COPILOT, CAP_COPILOT)
    user = "FACTS:\n%s\n\nDRAFT:\n%s" % ("\n".join("- %s" % f for f in facts if f), draft)
    try:
        resp = ai_gateway.chat_completion(
            feature=feature, capability=cap, mode=ai_gateway.MANUAL, actor=actor_id, org_id=org_id,
            client=_get_client(), messages=[{"role": "system", "content": SYSTEM},
                                            {"role": "user", "content": user}],
            temperature=0, max_tokens=400)
        text = (resp.choices[0].message.content or "").strip()
    except ai_gateway.AIRefused as exc:
        return dict(rules, ai={"status": "unavailable", "reason": str(exc), "violations": []})
    except Exception as exc:                                   # noqa: BLE001
        return dict(rules, ai={"status": "error", "reason": type(exc).__name__, "violations": []})
    ok, violations = verify(text, list(facts) + [draft], claim_sources=[draft])
    if not ok:
        return dict(rules, ai={"status": "rejected", "violations": violations})
    return {"text": text, "generated_by": "ai", "ai": {"status": "verified", "violations": []}}


# ── brief / copilot adapters ────────────────────────────────────────────────

def _fmt(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        return ", ".join(_fmt(x) for x in v)
    if isinstance(v, dict):
        return ", ".join("%s: %s" % (k, _fmt(x)) for k, x in v.items())
    return str(v)


def brief_facts(brief: Dict[str, Any]) -> List[str]:
    out = ["%s: %s" % (f["label"], _fmt(f["value"])) for f in brief.get("facts", [])]
    out += ["Possible (to verify): %s" % i["statement"] for i in brief.get("inferences", [])]
    out += ["Not recorded: %s" % q for q in brief.get("insufficient", [])]
    if brief.get("recommended_action"):
        out.append("Recommended next action: %s" % brief["recommended_action"])
    if brief.get("suggested_agent"):
        out.append("Suggested agent: %s" % brief["suggested_agent"]["name"])
    return out


def brief_narrative(brief: Dict[str, Any]) -> str:
    """Deterministic one-paragraph summary built only from the brief's own fields."""
    f = {x["label"]: x["value"] for x in brief.get("facts", [])}
    parts = []
    who = f.get("Name") or "This prospect"
    where = " in %s" % f["State"] if f.get("State") else ""
    src = " (source: %s)" % f["Source"] if f.get("Source") else ""
    parts.append("%s%s%s." % (who, where, src))
    if f.get("Need categories"):
        parts.append("Recorded needs: %s." % _fmt(f["Need categories"]))
    if f.get("Recorded intent level"):
        parts.append("Recorded intent: %s." % f["Recorded intent level"])
    if f.get("Inbound messages"):
        parts.append("Inbound messages: %s." % f["Inbound messages"])
    if brief.get("recommended_action"):
        parts.append("Recommended next action: %s." % brief["recommended_action"])
    if brief.get("insufficient"):
        parts.append("Still unknown: %d item(s)." % len(brief["insufficient"]))
    return " ".join(parts)


def enhance_brief(brief: Dict[str, Any], assist: bool, actor_id, org_id) -> Dict[str, Any]:
    brief["narrative"] = brief_narrative(brief)
    brief["rules_narrative"] = brief["narrative"]
    if not assist:
        brief["ai"] = {"status": "not_requested", "violations": []}
        return brief
    r = rephrase(kind="brief", draft=brief["narrative"], facts=brief_facts(brief),
                 actor_id=actor_id, org_id=org_id)
    brief["narrative"], brief["generated_by"], brief["ai"] = r["text"], r["generated_by"], r["ai"]
    return brief


def copilot_facts(cp: Dict[str, Any], prospect_name: str) -> List[str]:
    out = ["Prospect name: %s" % prospect_name] if prospect_name else []
    out += ["Detected %s: %s (quote: %s)" % (d["type"], d["label"], d["quote"]) for d in cp.get("detected", [])]
    out += ["Unanswered question: %s" % q for q in cp.get("unanswered", [])]
    if cp.get("next_best_question"):
        out.append("Next best question: %s" % cp["next_best_question"])
    return out


def enhance_copilot(cp: Dict[str, Any], assist: bool, actor_id, org_id, prospect_name: str = "") -> Dict[str, Any]:
    cp["rules_suggested_reply"] = cp.get("suggested_reply")
    if not assist:
        cp["ai"] = {"status": "not_requested", "violations": []}
        return cp
    r = rephrase(kind="copilot", draft=cp.get("suggested_reply") or "", facts=copilot_facts(cp, prospect_name),
                 actor_id=actor_id, org_id=org_id)
    cp["suggested_reply"], cp["generated_by"], cp["ai"] = r["text"], r["generated_by"], r["ai"]
    return cp
