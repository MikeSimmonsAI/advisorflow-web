"""
AI Lead Quality Analysis Service

Per Mike's request: analyze each lead's CRM history (Last Action, Status
Reason, Lead Type, Last Contact Date) so the AI can judge what TYPE of
lead this really is and how warm/cold it is - not just rely on the raw
Lead Type field, which is blank on 368 of 1000 rows in the real export.

IMPORTANT NOTE ON THE REAL DATA: the column literally named "Last
Activity/Note" in Restland's CRM export turned out to be a TIMESTAMP,
not free-text notes, when actually inspected. So this analysis works
from what's really there: Last Action (short categorical outcomes like
"Called: LM/No Answer", "Called: Scheduled Appt.", "Called: Non Viable
Lead"), Status Reason, Lead Type, and Last Contact Date. If a future
export includes genuine free-text notes, feed those into the prompt too -
the function signature already accepts an optional notes field for that.

Uses the existing OpenAI key setup (same one hitting 429s in the desktop
app - this will hit the same rate limit until billing is added at
platform.openai.com, so batch calls and handle 429 gracefully).
"""

import os
import json
from datetime import datetime
from openai import OpenAI
from sqlalchemy.orm import Session
from app.models.models import Lead

# Lazily initialized so importing this module never crashes when
# OPENAI_API_KEY isn't set yet (e.g. during tests, or if billing hasn't
# been added to the key referenced in DEPLOY.md). The client is only
# actually constructed the first time analyze_lead_quality runs, and a
# missing key surfaces as a normal exception there, caught by the
# existing fallback heuristic - not a hard crash on app startup.
_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client

ANALYSIS_PROMPT = """You are analyzing a cemetery/funeral home sales lead's history to classify lead quality for an advisor. Given the data below, respond with ONLY a JSON object (no markdown, no preamble):

{{
  "quality": "hot" | "warm" | "cold" | "dead" | "unknown",
  "recommended_approach": "<one short sentence on how the advisor should approach this lead>",
  "reasoning": "<one short sentence on why>"
}}

Lead data:
- Lead Type (tier): {tier}
- Status Reason: {status_reason}
- Last Action (most recent call/contact outcome): {last_action}
- Last Contact Date: {last_contact_date}
- Notes (if any): {notes}
"""


def analyze_lead_quality(
    tier: str,
    status_reason: str = None,
    last_action: str = None,
    last_contact_date: str = None,
    notes: str = None,
) -> dict:
    """
    Single-lead analysis call. Returns a dict with quality, recommended_approach,
    and reasoning. Falls back to a rule-based heuristic if the API call fails
    (e.g. 429 rate limit) so the pipeline doesn't hard-stop on AI errors.
    """
    prompt = ANALYSIS_PROMPT.format(
        tier=tier or "unknown",
        status_reason=status_reason or "none recorded",
        last_action=last_action or "no contact attempts recorded",
        last_contact_date=last_contact_date or "never",
        notes=notes or "none",
    )

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=200,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        # Fallback heuristic - keeps the pipeline moving if OpenAI billing/
        # rate limit issue isn't resolved yet (this is the same key that's
        # been hitting 429s - see DEPLOY.md).
        return _fallback_heuristic(tier, status_reason, last_action, error=str(e))


def _fallback_heuristic(tier, status_reason, last_action, error=None) -> dict:
    """Rule-based fallback when the OpenAI call fails."""
    last_action_low = (last_action or "").lower()
    status_low = (status_reason or "").lower()

    if "non viable" in last_action_low or "not interested" in last_action_low:
        quality = "dead"
        approach = "Likely not worth re-contacting based on prior call outcome."
    elif "scheduled appt" in last_action_low or status_low == "contract sold":
        quality = "warm"
        approach = "Has prior engagement - approach with continuity, not a cold pitch."
    elif "lm/no answer" in last_action_low:
        quality = "cold"
        approach = "No live contact yet - standard outreach cadence applies."
    else:
        quality = "unknown"
        approach = "Insufficient history - treat as a standard new lead."

    return {
        "quality": quality,
        "recommended_approach": approach,
        "reasoning": f"Rule-based fallback (AI call failed: {error})" if error else "Rule-based fallback",
    }


def analyze_lead(db: Session, lead: Lead) -> dict:
    """Runs analysis on a single Lead record and writes the result back to ai_lead_quality_note."""
    last_contact_str = lead.last_contact_date.isoformat() if lead.last_contact_date else None
    result = analyze_lead_quality(
        tier=lead.tier.value if lead.tier else None,
        status_reason=lead.status_reason_raw,
        last_action=lead.last_action_raw,
        last_contact_date=last_contact_str,
        notes=lead.notes,
    )
    lead.ai_lead_quality_note = json.dumps(result)
    db.commit()
    return result


def analyze_batch(db: Session, leads: list[Lead]) -> dict:
    """
    Analyzes a batch of leads. Use sparingly given the current 429 rate
    limit situation on the shared OpenAI key - consider running this as a
    background job with delays between calls rather than synchronously
    on upload, until billing is fixed.
    """
    results = {}
    for lead in leads:
        results[lead.id] = analyze_lead(db, lead)
    return results
