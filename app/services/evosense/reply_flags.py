"""Situations in a seller's reply that a PERSON must handle, read alongside
conversation.classify (which still decides the outcome, and whose opt-out
handling always wins):

    HUMAN_REQUESTED      "is this a bot?", "let me talk to a real person"
    ANGRY                hostile replies ("stop harassing me", "scam", "how did
                         you get my number") - automated outreach stops for
                         this conversation; only an opt-out adds the number to
                         do-not-contact
    MULTIPLE_PROPERTIES  "I have three houses", "other properties too"
    AUCTION_UPDATE       the seller says the sale was postponed / cancelled /
                         the loan reinstated - a seller statement, never a
                         change to the clerk record
    UNCERTAIN_OWNERSHIP  "it's my mother's house", "I'm not the only owner"
    DECEASED_OWNER       "the owner died", "she is deceased" - who can sell is
                         unknown until a person confirms the estate

Deterministic. Each flag reports the words it matched; nothing is inferred.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

FLAG_RULES = (
    ("HUMAN_REQUESTED", re.compile(
        r"\b(real person|a human|actual person|live person|talk to (someone|somebody|a person|you)|"
        r"(is|are) (this|you) (a )?(bot|robot|automated|ai|machine|real)|speak (to|with) (someone|a person|a human)|"
        r"have (someone|somebody) call)\b", re.I)),
    ("ANGRY", re.compile(
        r"\b(harass(ing|ment)?|scam(mer)?s?|how did you get my (number|info|information)|report(ing)? you|"
        r"sue you|my (lawyer|attorney)|vultures?|piss(ed)? off|f+u+c+k\w*|bullsh\w*|shut up|go to hell)\b", re.I)),
    ("MULTIPLE_PROPERTIES", re.compile(
        r"\b((two|three|four|five|several|multiple|a few|other|more) (\w+ )?(houses|homes|properties|rentals|units)|"
        r"other propert(y|ies)|own (a few|several|multiple) (houses|homes|properties))\b", re.I)),
    ("AUCTION_UPDATE", re.compile(
        r"\b((sale|auction|foreclosure) (was |got |has been |is )?(postponed|cancell?ed|stopped|moved|rescheduled)|"
        r"postponed the (sale|auction)|(loan|mortgage) (was |is |got )?reinstated|caught up on (the )?(payments|mortgage)|"
        r"loan modification|filed (for )?bankruptcy|in bankruptcy)\b", re.I)),
    ("UNCERTAIN_OWNERSHIP", re.compile(
        r"\b((my|our) (mother|father|mom|dad|parents|grand\w+|brother|sister|aunt|uncle|son|daughter|ex)'?s "
        r"(house|home|property|place)|not sure (if|whether) (i'?m|i am|we'?re|we are|i|we) (the one|allowed|able|can)|"
        r"(i'?m|i am) not the only owner|co-?owners?|(is|are) on the (deed|title)|whose name is on)", re.I)),
    ("DECEASED_OWNER", re.compile(
        r"\b(deceased|(he|she|owner|they) (has |have )?(died|passed)\b(?!\s+(on it|the|a|an|it|inspection|up)\b)|"
        r"passed away|no longer (alive|living)|funeral)\b", re.I)),
)

NEXT_ACTION = {
    "HUMAN_REQUESTED": "Call the seller - they asked for a person",
    "ANGRY": "A person decides whether to reply at all",
    "MULTIPLE_PROPERTIES": "Call the seller - more than one property",
    "AUCTION_UPDATE": "Confirm the sale status on the clerk record, then call",
    "DECEASED_OWNER": "Confirm the estate and who can sell before any reply",
    "UNCERTAIN_OWNERSHIP": "Confirm on the county record who can sell before going further",
}


def read(text: str) -> List[Dict[str, Any]]:
    out = []
    for code, rx in FLAG_RULES:
        m = rx.search(text or "")
        if m:
            out.append({"code": code, "quote": m.group(0)})
    return out
