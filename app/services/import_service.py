"""
Lead Import Service
Handles advisor Excel uploads.

Updated June 19 2026 against a REAL Restland Dynamics CRM export
("All Active Leads (2012)") plus Mike's correction on lead-tier handling.

CURRENT RULES (superseding the original "Pre-Need only" assumption):
  - EVERY lead gets imported and contacted in some form - nobody is
    excluded just because of their tier. Pre-Need, At-Need, Imminent,
    and even already-purchased (Contract Sold) leads are all kept active.
  - Each tier gets routed to a different `message_track` (see MessageTrack
    enum in models.py) so the OFFER matches the person, instead of
    everyone getting the same Pre-Need price-lock pitch:
      * Pre-Need          -> pre_need_lock_price (the original price-freeze pitch)
      * At-Need           -> at_need_support
      * Imminent          -> imminent_support
      * Contract Sold     -> upsell_existing (memorials, markers, extra plots/services
                              for people who already bought - they're a warm upsell
                              audience, not someone to exclude)
      * Untyped/blank     -> needs_review (held until a human assigns a real tier)
  - Leads with NO PHONE but a real email are NOT discarded. They're
    imported with contact_channel="email_only" and message_track=
    email_only_nurture, queued for the email-blast feature (Phase 2)
    instead of SMS.
  - Hard exclusions remaining: explicit "Allow Phone Calls? = Do Not Allow"
    compliance flag (still honored - this is a real opt-out signal, not a
    tier assumption), and obvious internal NSMG/Restland distribution-list
    records (not real prospects at all).
  - Last Action, Status Reason, and Last Contact Date are carried over from
    the CRM export onto the Lead record specifically so a later AI pass
    can analyze lead quality/intent from real history, not just the bare
    Lead Type field.

Column names vary between exports, so headers are fuzzy-matched (First
Name / FirstName / fname, etc.) against a real Restland export's actual
column names: First Name, Middle Name, Last Name, Phone, Email, Lead Date,
Lead Type, Status Reason, Sale Made?, Allow Phone Calls?, Last Action,
Last Activity/Note, Street Address, City, State, ZIP Code, etc.
"""

import pandas as pd
from sqlalchemy.orm import Session
from app.models.models import Lead, LeadTier, LeadStatus, MessageTrack
from app.services.dedup_service import check_and_register, normalize_phone

HEADER_MAP = {
    "first_name": ["first name", "firstname", "fname", "first"],
    "last_name": ["last name", "lastname", "lname", "last", "surname"],
    "phone": ["phone", "phone number", "cell", "cell phone", "mobile", "telephone"],
    "email": ["email", "email address", "e-mail"],
    "tier": ["tier", "data tier", "lead type", "status type"],
    "status_reason": ["status reason", "status", "lead status"],
    "allow_calls": ["allow phone calls?", "allow phone calls", "do not call"],
    "last_action": ["last action"],
    "last_contact_date": ["last activity/note", "last activity", "last contact date", "open activity date"],
}

# Internal NSMG/Restland distribution-list and system entries to exclude -
# these are not real prospects (e.g. "NSMG-DL-All Home Office").
INTERNAL_EMAIL_MARKERS = ["@nsmg.com"]

# Tier -> message track mapping. Every tier maps to SOMETHING now; nothing
# maps to "excluded."
TIER_TO_TRACK = {
    LeadTier.PRE_NEED: MessageTrack.PRE_NEED_LOCK_PRICE,
    LeadTier.AT_NEED: MessageTrack.AT_NEED_SUPPORT,
    LeadTier.IMMINENT: MessageTrack.IMMINENT_SUPPORT,
    LeadTier.CONTRACT_SOLD: MessageTrack.UPSELL_EXISTING_CUSTOMER,
    LeadTier.EMAIL_ONLY: MessageTrack.EMAIL_ONLY_NURTURE,
    LeadTier.PARTIAL: MessageTrack.NEEDS_REVIEW,
    LeadTier.ADDR_ONLY: MessageTrack.NEEDS_REVIEW,
}


def _build_column_lookup(columns) -> dict:
    lookup = {}
    lowered = {c: str(c).strip().lower() for c in columns}
    for canonical, variants in HEADER_MAP.items():
        for col, low in lowered.items():
            if low in variants:
                lookup[canonical] = col
                break
    return lookup


def _infer_tier(raw_value: str, status_reason: str) -> LeadTier:
    """
    Determines lead tier. Status Reason "Contract Sold" takes priority over
    Lead Type, since a sold contract is the more important signal for
    which message track applies (upsell vs. acquisition pitch).
    Blank/unrecognized Lead Type -> PARTIAL (needs manual review), never
    silently assumed to be Pre-Need.
    """
    if status_reason and status_reason.strip().lower() == "contract sold":
        return LeadTier.CONTRACT_SOLD

    if not raw_value:
        return LeadTier.PARTIAL

    val = str(raw_value).strip().lower()
    if "imminent" in val:
        return LeadTier.IMMINENT
    if "at" in val and "need" in val:
        return LeadTier.AT_NEED
    if "pre" in val and "need" in val:
        return LeadTier.PRE_NEED
    return LeadTier.PARTIAL


def _is_internal_record(email: str, last_name: str) -> bool:
    if email:
        low = email.strip().lower()
        if any(marker in low for marker in INTERNAL_EMAIL_MARKERS):
            return True
    if last_name:
        low = last_name.strip().lower()
        if "nsmg-dl" in low or "restland-dl" in low or low.endswith("-dl-all employees"):
            return True
    return False


def _is_call_restricted(allow_calls_raw: str) -> bool:
    if not allow_calls_raw:
        return False
    return "do not allow" in allow_calls_raw.strip().lower()


def parse_excel_file(file_path: str) -> list[dict]:
    """
    Reads the FIRST sheet only - real Restland exports include a second
    "hiddenSheet" used internally by the CRM export tool for column-mapping
    metadata, which is not lead data and must be ignored.
    """
    df = pd.read_excel(file_path, sheet_name=0, dtype=str)
    df = df.fillna("")

    lookup = _build_column_lookup(df.columns)

    if "phone" not in lookup and "email" not in lookup:
        raise ValueError(
            f"Could not find a phone OR email column in file. "
            f"Found columns: {list(df.columns)}"
        )
    if "last_name" not in lookup:
        raise ValueError(
            f"Could not find required column 'last name' in file. "
            f"Found columns: {list(df.columns)}"
        )

    rows = []
    for _, row in df.iterrows():
        rows.append({
            "first_name": row.get(lookup.get("first_name", ""), "").strip(),
            "last_name": row.get(lookup["last_name"], "").strip(),
            "phone": row.get(lookup.get("phone", ""), "").strip(),
            "email": row.get(lookup.get("email", ""), "").strip(),
            "tier_raw": row.get(lookup.get("tier", ""), "").strip(),
            "status_reason_raw": row.get(lookup.get("status_reason", ""), "").strip(),
            "allow_calls_raw": row.get(lookup.get("allow_calls", ""), "").strip(),
            "last_action_raw": row.get(lookup.get("last_action", ""), "").strip(),
            "last_contact_date_raw": row.get(lookup.get("last_contact_date", ""), "").strip(),
        })
    return rows


def import_leads_from_excel(
    db: Session,
    file_path: str,
    organization_id: str,
    uploading_user_id: str,
    source_year: int = None,
    source_filename: str = None,
    dry_run: bool = False,
) -> dict:
    """
    Full import pipeline: parse -> route by tier/channel -> dedup check
    (phone-based leads only) -> insert. Everyone gets imported; nothing
    gets silently discarded except internal CRM system records and
    explicit compliance opt-outs.

    dry_run=True: builds and dedup-checks everything the same way, but
    rolls back at the end instead of committing, so the advisor can preview
    the exact breakdown before confirming. This is the only safe way to
    preview, since the real function commits internally - a caller-side
    savepoint can't wrap a commit.
    """
    rows = parse_excel_file(file_path)

    created_leads = []
    duplicate_count = 0
    skipped_no_contact_info = 0
    skipped_internal_records = 0
    flagged_call_restricted = 0
    flagged_needs_tier_review = 0
    email_only_count = 0
    tier_counts = {}

    for row in rows:
        phone_norm = normalize_phone(row["phone"])
        has_email = bool(row["email"])

        if not row["last_name"] or (not phone_norm and not has_email):
            skipped_no_contact_info += 1
            continue

        if _is_internal_record(row["email"], row["last_name"]):
            skipped_internal_records += 1
            continue

        tier = _infer_tier(row["tier_raw"], row["status_reason_raw"])
        call_restricted = _is_call_restricted(row["allow_calls_raw"])

        # Route: phone present -> SMS channel. No phone but email present -> email-only channel.
        if phone_norm:
            contact_channel = "sms"
        else:
            contact_channel = "email_only"
            tier = LeadTier.EMAIL_ONLY  # channel overrides tier classification for routing purposes
            email_only_count += 1

        message_track = TIER_TO_TRACK.get(tier, MessageTrack.NEEDS_REVIEW)
        if tier == LeadTier.PARTIAL:
            flagged_needs_tier_review += 1

        # Parse last contact date if present (best-effort, don't fail import on bad dates)
        last_contact_dt = None
        if row["last_contact_date_raw"]:
            try:
                last_contact_dt = pd.to_datetime(row["last_contact_date_raw"])
            except Exception:
                last_contact_dt = None

        lead = Lead(
            organization_id=organization_id,
            assigned_to_id=uploading_user_id,
            first_name=row["first_name"] or None,
            last_name=row["last_name"] or None,
            phone=phone_norm or None,
            phone_raw=row["phone"] or None,
            email=row["email"] or None,
            tier=tier,
            message_track=message_track,
            contact_channel=contact_channel,
            status=LeadStatus.NEW,
            source_year=source_year,
            source_file=source_filename,
            last_action_raw=row["last_action_raw"] or None,
            last_contact_date=last_contact_dt,
            status_reason_raw=row["status_reason_raw"] or None,
        )
        db.add(lead)
        db.flush()

        tier_counts[tier.value] = tier_counts.get(tier.value, 0) + 1

        # Dedup only applies to phone-based leads - email-only leads don't
        # have a phone to check against the registry.
        if phone_norm:
            is_dup, registry_entry = check_and_register(
                db,
                organization_id=organization_id,
                phone_raw=row["phone"],
                last_name_raw=row["last_name"],
                lead_id=lead.id,
                user_id=uploading_user_id,
            )
            if call_restricted:
                lead.status = LeadStatus.DNC
                flagged_call_restricted += 1
            elif is_dup:
                lead.is_duplicate = True
                lead.duplicate_of_lead_id = registry_entry.first_seen_lead_id
                lead.status = LeadStatus.DNC  # someone already owns this relationship
                duplicate_count += 1
            else:
                lead.status = (
                    LeadStatus.NEEDS_TIER_REVIEW if tier == LeadTier.PARTIAL else LeadStatus.NEW
                )
        else:
            lead.status = LeadStatus.NEW  # queued for email outreach, not SMS

        created_leads.append(lead)

    if dry_run:
        db.rollback()
    else:
        db.commit()

    return {
        "total_rows": len(rows),
        "imported": len(created_leads),
        "new_active_sms_leads": len(created_leads) - duplicate_count - flagged_call_restricted - email_only_count,
        "email_only_leads_queued": email_only_count,
        "duplicates_flagged": duplicate_count,
        "flagged_call_restricted": flagged_call_restricted,
        "flagged_needs_tier_review": flagged_needs_tier_review,
        "skipped_no_contact_info": skipped_no_contact_info,
        "skipped_internal_records": skipped_internal_records,
        "tier_breakdown": tier_counts,
        # IDs of every lead actually created in this batch - needed so a
        # caller can immediately build the "review AI-drafted messages
        # before sending" screen for exactly this import, not the org's
        # entire lead history. Real gap fixed here: this didn't exist
        # before, meaning there was no way to look back at "what did THIS
        # import just create" once the response left the import call.
        #
        # IMPORTANT: for dry_run, the leads were rolled back and never
        # actually persisted, even though each Lead object already has a
        # client-side-generated UUID (gen_uuid is a Python default, not a
        # database default) - returning those IDs would look valid but
        # silently resolve to nothing if a caller tried to fetch them
        # afterward. Explicitly empty for dry runs to avoid that trap.
        "created_lead_ids": [] if dry_run else [lead.id for lead in created_leads],
    }
