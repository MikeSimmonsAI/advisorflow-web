"""CONVERSATION COPILOT — rule-based detection and education-framed suggestions.

Detects questions / concerns / objections in INBOUND messages, surfaces
unanswered questions (inbound after the last real outbound), and suggests a
reply framed as EDUCATION + DISCOVERY questions. It never states that a
prospect needs a product, qualifies, or will be approved.
Simulated sends are recorded by the router in agency_copilot_events; this
module never calls a provider.
"""
import re
from typing import Any, Dict, List

RULES = [
    # (type, key, label, regex)
    ("objection", "employer_coverage", "Employer coverage concern",
     r"\b(through|from|at|via)\s+(my\s+)?(work|job|employer|company)\b|\bgroup (life|coverage|policy)\b|\b(work|employer)\s+(policy|coverage|insurance|plan)\b"),
    ("objection", "cost", "Cost concern",
     r"\b(too expensive|expensive|can'?t afford|afford|cost|price|how much|budget)\b"),
    ("objection", "not_now", "Timing / not now",
     r"\b(not (right )?now|maybe later|think about it|need to think|not ready|next year)\b"),
    ("concern", "spouse", "Wants to involve spouse/partner",
     r"\b(my (wife|husband|spouse|partner)|talk to (my )?(wife|husband|spouse|partner))\b"),
    ("concern", "health", "Health-related question",
     r"\b(medical|health|exam|diabet|cancer|heart|medication|condition|smok)\w*"),
    ("concern", "trust", "Trust / legitimacy concern",
     r"\b(scam|legit|is this real|how did you get my)\b"),
    ("objection", "not_interested", "Not interested",
     r"\b(not interested|stop|unsubscribe|leave me alone|remove me)\b"),
    ("concern", "human_request", "Asked for a person",
     r"\b(real person|speak to (someone|a person|an agent)|call me)\b"),
]

REPLIES = {
    "employer_coverage": (
        "That's a great thing to have, and a lot of families start there. It can help to "
        "understand a few details about it: roughly how much it pays, whether it stays with "
        "you if you change jobs or retire, and how it fits with the rest of your family's "
        "plans. Would you be open to a few quick questions so we can look at it together?"),
    "cost": (
        "Understood - cost matters. Options and pricing depend on details we haven't gone over "
        "yet, so I don't want to guess. Could I ask a couple of questions about what you're "
        "hoping to cover so we can see what's realistic for your budget?"),
    "not_now": (
        "No pressure at all. If it helps, I can share some general information you can look at "
        "whenever it's convenient. Is there a better time to reconnect?"),
    "spouse": (
        "That makes sense - these are family decisions. Would a time when you can both join "
        "be easier?"),
    "health": (
        "Thanks for sharing that. I'm not able to speak to how any health detail affects "
        "coverage - that's decided by the carrier during their process. A licensed agent can "
        "walk you through how that process works. Would a short call help?"),
    "trust": (
        "Fair question. You reached us through the request you submitted; I'm happy to share "
        "who we are and you can decide whether to continue."),
    "human_request": "Absolutely - I'll have a licensed agent reach out to you directly.",
}
NEXT_QUESTION = {
    "employer_coverage": "Do you know roughly how much your employer plan provides, and whether "
                         "you could keep it if you left that job?",
    "cost": "What would you most want this coverage to take care of for your family?",
    "not_now": "What would need to be true for this to feel like the right time?",
    "spouse": "When would be a good time to talk with both of you?",
    "health": "Would you like a licensed agent to explain how the carrier's process works?",
}
TAKEOVER_KEYS = {"health": "Health-related question needs a licensed human agent",
                 "not_interested": "Prospect asked to stop or is not interested",
                 "trust": "Trust concern - human response recommended",
                 "human_request": "Prospect asked for a person"}


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.?!])\s+|\n+", text or "") if s.strip()]


def analyze(conversation: List[Dict[str, Any]], prospect_name: str = "") -> Dict[str, Any]:
    real = [m for m in conversation if not m.get("simulated")]
    inbound = [m for m in real if m.get("direction") == "inbound"]
    last_out_idx = max((i for i, m in enumerate(real) if m.get("direction") == "outbound"), default=-1)
    detected, keys = [], []
    for m in inbound:
        body = m.get("body") or ""
        for typ, key, label, rx in RULES:
            mm = re.search(rx, body, re.I)
            if mm:
                quote = next((s for s in _sentences(body) if re.search(rx, s, re.I)), body)[:240]
                detected.append({"type": typ, "key": key, "label": label, "quote": quote,
                                 "message_id": m.get("id")})
                if key not in keys:
                    keys.append(key)
        for s in _sentences(body):
            if s.endswith("?"):
                detected.append({"type": "question", "key": "question", "label": "Question",
                                 "quote": s[:240], "message_id": m.get("id")})
    unanswered = []
    for i, m in enumerate(real):
        if i > last_out_idx and m.get("direction") == "inbound":
            unanswered += [s for s in _sentences(m.get("body") or "") if s.endswith("?")]

    primary = next((k for k in keys if k in REPLIES), None)
    if primary:
        reply = REPLIES[primary]
    elif unanswered:
        reply = ("Thanks for your question. I want to make sure I give you accurate information - "
                 "could you tell me a little more about what you're hoping to plan for?")
    elif inbound:
        reply = "Thanks for getting back to me. What would be most helpful to talk through first?"
    else:
        reply = None
    nbq = NEXT_QUESTION.get(primary) or (
        "What prompted you to reach out about coverage right now?" if inbound else
        "Is now a good time for a short introduction call?")
    takeover = [TAKEOVER_KEYS[k] for k in keys if k in TAKEOVER_KEYS]
    if sum(1 for d in detected if d["type"] == "objection") >= 3:
        takeover.append("Multiple objections raised")
    if not inbound:
        summary = "No inbound messages from %s yet." % (prospect_name or "the prospect")
    else:
        summary = "%d inbound and %d outbound message(s). Detected: %s." % (
            len(inbound), len(real) - len(inbound),
            ", ".join(dict.fromkeys(d["label"] for d in detected)) or "nothing notable")
    if unanswered:
        task = {"title": "Answer prospect's open question(s)", "kind": "follow_up"}
    elif primary == "spouse":
        task = {"title": "Schedule a joint conversation", "kind": "follow_up"}
    elif inbound:
        task = {"title": "Follow up on conversation", "kind": "follow_up"}
    else:
        task = {"title": "Make first contact", "kind": "follow_up"}
    return {"summary": summary, "detected": detected, "unanswered": unanswered,
            "suggested_reply": reply, "reply_framing": "education + discovery (not advice)",
            "next_best_question": nbq, "suggested_task": task,
            "recommend_human_takeover": bool(takeover), "takeover_reasons": takeover,
            "generated_by": "rules"}
