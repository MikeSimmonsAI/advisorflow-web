"""SCI campaigns that are not one of the original ten families (#385).

CENTRAL ALABAMA 2025–2026 MS - its own campaign, never routed to Veteran
Planning Guide, Veteran Official, General Survey, Re-engagement or any other
family because nothing else matched.

What the source data establishes (all 473 rows, Oct 25):
  * Campaign Channel = "Direct Mail" on every row;
  * the source designation "Central Alabama 2025–2026 MS" ("MS" is NOT
    expanded - nobody has said what it stands for);
  * 16 Central Alabama locations.
What it does NOT establish: which mailer, what offer, what the family asked
for, the approved tone. So the campaign is created OFF with NO message copy,
and it cannot be switched on until approved copy is entered
(program_router.set_campaign_active refuses a family without it).
"""
import json
from typing import Dict, Optional

from sqlalchemy.orm import Session

CENTRAL_ALABAMA_KEY = "central_alabama_ms"
CENTRAL_ALABAMA_NAME = "Central Alabama 2025–2026 MS"
CENTRAL_ALABAMA = {
    "key": CENTRAL_ALABAMA_KEY, "name": CENTRAL_ALABAMA_NAME,
    # Matches the file's own campaign text in either dash style - and nothing
    # any other family uses.
    "patterns": ["Central Alabama 2025-2026 MS", "Central Alabama 2025–2026 MS", "Central Alabama 2025 2026 MS"],
    "asset_category": "central_alabama_ms",
}

# What the AI may and may not say about WHY we contacted a family, by campaign.
# A campaign without an entry gets nothing extra (its outreach copy speaks for it).
AI_NOTES: Dict[str, Dict[str, str]] = {
    CENTRAL_ALABAMA_KEY: {
        "campaign": CENTRAL_ALABAMA_NAME,
        "what_we_know": "This family is on a Central Alabama list that came in through direct mail.",
        "rules": ("The offer, mailer and reason for contact for this campaign are NOT verified. Do not mention any "
                  "veteran guide or benefits, planning survey, seminar, savings certificate, Life Story, cremation "
                  "information or any other offer, and do not say the family requested anything. Do not guess what "
                  "'MS' means. Be warm and brief, answer their actual question from approved information, and invite "
                  "a short appointment the ways this location offers."),
    },
}

# Campaigns that speak ONLY with their own approved copy: never a playbook,
# never the re-engagement follow-up line, never the program's generic touches.
OWN_COPY_KEYS = {CENTRAL_ALABAMA_KEY}

MISSING_FOR_APPROVAL = [
    "Which direct-mail piece these families received (a copy or photo of the mailer).",
    "What the mailer offered or asked the family to do (the reason we are contacting them).",
    "What 'MS' stands for in 'Central Alabama 2025–2026 MS', if it should ever be said to a family.",
    "The approved tone, and approved wording for the first text and the first email.",
    "Whether these families should hear from Kerry Allan like the other SCI campaigns, or from someone else.",
]


def ensure_central_alabama(db: Session, organization_id: str):
    """Create the campaign OFF with no copy, once. Never switches it on and never
    overwrites copy a person has entered."""
    from app.models.program_models import CampaignFamily
    fam = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == organization_id,
                                           CampaignFamily.key == CENTRAL_ALABAMA_KEY).first())
    if fam is not None:
        return fam
    fam = CampaignFamily(
        organization_id=organization_id, key=CENTRAL_ALABAMA_KEY, name=CENTRAL_ALABAMA_NAME,
        source_campaign_patterns=json.dumps(CENTRAL_ALABAMA["patterns"]),
        asset_category=CENTRAL_ALABAMA["asset_category"], language="en",
        first_touch_email_mode="none", followup_email_mode="none",
        sms_template=None, email_subject_template=None, email_body_template=None,
        is_active=False)
    db.add(fam)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - another request created it first
        db.rollback()
        fam = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == organization_id,
                                               CampaignFamily.key == CENTRAL_ALABAMA_KEY).first())
    return fam


def ensure_for_sci(db: Session) -> Optional[str]:
    from app.services import sms_programs
    org_id = sms_programs.sci_org_id(db)
    if org_id:
        ensure_central_alabama(db, org_id)
    return org_id


def ai_notes_for(campaign_family: Optional[str]) -> Optional[Dict[str, str]]:
    return AI_NOTES.get(campaign_family or "")


def activation_blocker(fam) -> Optional[str]:
    """Why this campaign may not be switched on yet, or None."""
    if not (fam.sms_template or "").strip() or not (fam.email_body_template or "").strip() \
            or not (fam.email_subject_template or "").strip():
        missing = "" if fam.key != CENTRAL_ALABAMA_KEY else " Still needed: " + " ".join(MISSING_FOR_APPROVAL)
        return ("This campaign has no approved text and email copy yet, so it can't be switched on." + missing)
    return None
