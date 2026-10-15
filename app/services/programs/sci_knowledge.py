"""APPROVED SCI INFORMATION the appointment setter may state as fact.

Everything here is taken from material the program already publishes - the
pre-planning guide served at /planning-guide (planning_guide_page.py) - or from
how the program itself works (the booking engine books 60-minute visits on
the representative's calendar; a phone appointment is offered as well). The
setter answers from this list and NOTHING else: no prices, no eligibility
decisions, no promises about benefits, no claims about a specific family's
plan or property. Anything outside it is a question for the representative.

Changing this file changes what the AI may say to families. Keep it short,
plain and true.
"""
from typing import Dict, List

FACTS: Dict[str, List[str]] = {
    "planning_guide": [
        "The planning guide is a short, plain-language guide to planning funeral and cemetery "
        "arrangements ahead of time.",
        "It covers why people plan ahead, the main decisions (burial or cremation, cemetery property, the kind of "
        "service, personal touches, who should be involved), documents worth gathering, and a simple checklist.",
        "It can be sent as a link by text, by email, or a person can arrange a printed copy by mail.",
    ],
    "why_plan_ahead": [
        "Planning ahead means your wishes are known, your family has fewer decisions to make at a hard time, and "
        "you can take your time, compare and ask questions.",
    ],
    "burial_or_cremation": [
        "Both burial and cremation can be planned ahead. Many people decide based on what feels right for them "
        "and their family, and on faith or tradition.",
        "Cremation can still include a place of memorial, such as a niche or a cemetery space.",
    ],
    "cemetery_property": [
        "Cemetery property can be a ground space, a mausoleum crypt or a cremation niche. People often think about "
        "location and whether they want to be near family.",
        "Someone who already owns property or has prior arrangements can bring the deed or paperwork to a review.",
    ],
    "veterans": [
        "If you served, the guide suggests keeping your military discharge papers (DD-214) with your plans.",
        "Veterans' considerations are something the representative can go over with you; the setter does not "
        "decide eligibility for any benefit.",
    ],
    "existing_plan": [
        "Families who already have arrangements are welcome to a review to keep records, wishes and contacts "
        "current.",
    ],
    "documents": [
        "Helpful documents: full legal name and date and place of birth, parents' names, military discharge papers "
        "if you served, any cemetery deeds, prior arrangements or insurance policies, and contacts to notify.",
        "Never share a Social Security number by text.",
    ],
    "appointments": [
        "A planning visit takes about an hour, in person with the representative.",
        "A phone appointment is also available for families who prefer not to come in.",
        "Family members are welcome to join a visit or a call.",
    ],
    "who_we_are": [
        "Messages come from the local funeral home or cemetery's family services team on behalf of the "
        "representative named in the conversation.",
    ],
}

# Topics the setter must NOT answer itself - they go to the representative,
# usually by offering a short call or visit (never a dead-end hand-off).
REPRESENTATIVE_ONLY = [
    "prices, costs, payment plans or what a specific option would cost",
    "whether a person qualifies for any benefit (veterans, insurance, government)",
    "the details of a specific family's existing plan, contract or property",
    "legal, tax or financial advice",
]


def as_prompt_block() -> str:
    lines = ["APPROVED INFORMATION (you may state these as fact; nothing else):"]
    for topic, facts in FACTS.items():
        for f in facts:
            lines.append("- [%s] %s" % (topic, f))
    lines.append("FOR THE REPRESENTATIVE ONLY (acknowledge, then offer a call or visit to go over it): "
                 + "; ".join(REPRESENTATIVE_ONLY) + ".")
    return "\n".join(lines)
