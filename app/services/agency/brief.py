"""AI OPPORTUNITY BRIEF — deterministic rules first.

Output separates:
  facts        - values read from a stored field, with `source_field`
  inferences   - statements derived from facts, with the `basis` fields and a
                 confidence; phrased as "may", never as a conclusion
  insufficient - questions the record cannot answer yet
It never states product fit, underwriting, medical facts, or binding outcomes.
"""
from typing import Any, Dict, List, Optional

from app.services.agency.common import NEED_LABELS, jload

DISCLAIMER = ("Generated from recorded prospect data by deterministic rules. Facts cite the "
              "field they came from; inferences are possibilities for the agent to verify in "
              "conversation. This brief is not insurance advice and does not assess product "
              "fit, underwriting, health, or pricing.")


def build_brief(lead, profile, conversation: List[Dict[str, Any]], recommendation: Optional[Dict]):
    facts, inferences, insufficient = [], [], []

    def fact(label, value, field):
        if value not in (None, "", [], {}):
            facts.append({"label": label, "value": value, "source_field": field})

    name = " ".join(p for p in (lead.first_name, lead.last_name) if p) or None
    fact("Name", name, "lead.first_name/last_name")
    fact("State", lead.state, "lead.state")
    fact("Source", lead.source, "lead.source")
    fact("Lead status", lead.status, "lead.status")
    if lead.sms_consent:
        fact("SMS consent recorded", True, "lead.sms_consent")

    needs = jload(profile.need_categories, []) if profile else []
    household = jload(profile.household, {}) if profile else {}
    goals = jload(profile.financial_goals, []) if profile else []
    concerns = jload(profile.stated_concerns, []) if profile else []
    if profile:
        fact("Need categories", [NEED_LABELS.get(n, n) for n in needs], "profile.need_categories")
        fact("Household", household, "profile.household")
        fact("Preferred contact", profile.preferred_contact, "profile.preferred_contact")
        fact("Stated financial goals", goals, "profile.financial_goals")
        fact("Stated concerns", concerns, "profile.stated_concerns")
        fact("Recorded intent level", profile.intent_level, "profile.intent_level")
        for key, label in (("retirement_interest", "Retirement interest"),
                           ("business_owner_interest", "Business-owner interest"),
                           ("living_benefits_interest", "Living-benefits interest")):
            v = getattr(profile, key)
            if v is not None:
                fact(label, v, "profile.%s" % key)

    inbound = [m for m in conversation if m.get("direction") == "inbound"]
    outbound = [m for m in conversation if m.get("direction") == "outbound" and not m.get("simulated")]
    if inbound:
        fact("Inbound messages", len(inbound), "replies")

    # ── inferences (each cites its basis) ──
    if household.get("children") or household.get("dependents"):
        inferences.append({"statement": "Household includes dependents; a family-protection "
                                        "conversation may be relevant to discuss.",
                           "basis": ["profile.household"], "confidence": "medium"})
    if profile and profile.retirement_interest:
        inferences.append({"statement": "Prospect may be open to a retirement-planning discussion.",
                           "basis": ["profile.retirement_interest"], "confidence": "medium"})
    if profile and profile.business_owner_interest:
        inferences.append({"statement": "Business-owner planning topics may be worth exploring.",
                           "basis": ["profile.business_owner_interest"], "confidence": "medium"})
    if inbound and not outbound:
        inferences.append({"statement": "Prospect has written in but has not received a reply.",
                           "basis": ["replies", "messages"], "confidence": "high"})
    if any("work" in (c or "").lower() or "employer" in (c or "").lower() for c in concerns):
        inferences.append({"statement": "Prospect may already rely on employer coverage; discovery "
                                        "about that coverage may help.",
                           "basis": ["profile.stated_concerns"], "confidence": "low"})

    # ── insufficient information ──
    if not lead.state:
        insufficient.append("Which state does the prospect live in?")
    if not needs:
        insufficient.append("What is the prospect hoping to protect or plan for?")
    if not household:
        insufficient.append("Who is in the household (spouse, dependents)?")
    if not profile or not profile.preferred_contact:
        insufficient.append("How does the prospect prefer to be contacted?")
    if not profile or not profile.intent_level:
        insufficient.append("How soon is the prospect looking to act? (intent not recorded)")
    insufficient.append("Existing coverage amounts and terms are not recorded.")

    category = NEED_LABELS.get(needs[0], needs[0]) if needs else None
    intent = profile.intent_level if profile else None
    if not lead.assigned_to_id:
        action = "Assign an agent"
    elif inbound and not outbound:
        action = "Reply to the prospect's message"
    elif not outbound:
        action = "Make first contact"
    else:
        action = "Schedule a discovery conversation"

    sugg = None
    if recommendation and recommendation.get("recommended"):
        r = recommendation["recommended"]
        sugg = {"id": r["agent_id"], "name": r["name"], "reasons": r["reasons"]}
    return {"facts": facts, "inferences": inferences, "insufficient": insufficient,
            "category": category, "intent_level": intent, "recommended_action": action,
            "suggested_agent": sugg, "generated_by": "rules", "disclaimer": DISCLAIMER}
