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

import json
import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models.models import Lead, LeadTier
from app.services import permission_values as pv
from app.services.dedup_service import (check_and_register, normalize_phone,
                                         normalize_last_name, PLACEHOLDER_LAST_NAME)

HEADER_MAP = {
    "first_name": ["first name", "firstname", "fname", "first", "buyerfirstname", "buyer first name", "given name"],
    "last_name": ["last name", "lastname", "lname", "last", "surname", "buyerlastname", "buyer last name", "family name"],
    # ONE name column is the common CRM export shape, not an edge case.
    # Only consulted when no explicit last-name column exists - see split_full_name.
    "full_name": ["full name", "fullname", "name", "contact name", "lead name",
                  "customer name", "client name", "display name", "primary contact"],
    "phone": ["phone", "phone number", "cell", "cell phone", "mobile", "telephone", "buyerphone", "buyer phone", "phone 1 - value", "phone 2 - value",
              # E.164 is how any export that normalises its numbers labels them.
              "phone e164", "phone (e164)", "phone_e164", "e164", "e.164",
              "primary phone", "phone1", "home phone"],
    "email": ["email", "email address", "e-mail", "buyeremail", "buyer email", "e-mail 1 - value", "e-mail 2 - value"],
    "tier": ["tier", "data tier", "lead type", "status type", "salescontractneedtypedescription", "sales contract need type description", "need type"],
    "status_reason": ["status reason", "status", "lead status"],
    # Kept for the legacy call-restriction path. The AUTHORITATIVE permission
    # reader is app/services/permission_values.py, which covers all four
    # channels; this entry only keeps the historical `allow_calls_raw` field
    # populated for callers that already read it.
    "allow_calls": ["allow phone calls?", "allow phone calls", "do not call"],
    # ── Compliance channels — preserved independently, never collapsed ─────
    "allow_emails":      ["allow emails?", "allow emails", "allow email",
                          "email opt-in", "email consent"],
    # NOTE: "Do not allow Bulk Emails" has INVERTED column-name polarity.
    # Values "Allow"/"Do Not Allow" are self-descriptive and authoritative.
    # Boolean-like values (Yes/No/1/0) are ambiguous → REVIEW, not assumed.
    "allow_bulk_emails": ["do not allow bulk emails", "bulk email", "allow bulk emails",
                          "bulk email opt-in", "bulk email consent"],
    "allow_sms":         ["allow text message?", "allow text messages", "allow sms",
                          "allow texts", "text consent", "sms consent", "sms opt-in"],
    # ── Source identity ────────────────────────────────────────────────────
    "source_id":         ["contact guid", "contactid", "contact id", "contactguid",
                          "crm id", "external id", "external_id", "dynamics id",
                          "dynamics contact guid"],
    # ── Mobile phone provenance ────────────────────────────────────────────
    # Distinct from primary phone; only mapped when a dedicated column exists.
    "mobile_phone":      ["mobile phone", "mobile number"],
    # ── Historical activity timestamp ──────────────────────────────────────
    # "Last Activity Date" is the canonical timestamp — NOT "Last Action" text.
    "last_activity_date": ["last activity date", "last logged activity"],
    "last_action": ["last action"],
    # HISTORICAL CONTACT DATE. "last activity date" was the missing one: a real
    # production export writes that header, this list did not name it, matching
    # is EXACT, and so 92 of 100 real activity dates were parked in
    # custom_fields. The lead then read as never contacted - a silence the
    # platform created and then scored.
    #
    # "last logged activity" is deliberately here and NOT in `last_action`: in
    # these exports it holds a timestamp, and a timestamp is not an action.
    "last_contact_date": ["last activity date", "last activity/note",
                          "last activity", "last contact date",
                          "open activity date", "last logged activity",
                          "last completed activity", "last activity note"],
    "street_address": ["street address", "address", "street", "addr", "address 1", "address1", "street addr", "mailing address", "home address"],
    "city": ["city", "town", "municipality"],
    "state": ["state", "st", "province", "state/province", "state abbr", "state abbreviation"],
    "zip_code": ["zip", "zip code", "zipcode", "postal code", "zip/postal", "postal", "zip+4"],
}

# Internal NSMG/Restland distribution-list and system entries to exclude -
# these are not real prospects (e.g. "NSMG-DL-All Home Office").
INTERNAL_EMAIL_MARKERS = ["@nsmg.com"]

# Email prefixes that belong to notification systems, not real inboxes
# What a CRM writes into an email column when it has no email. These are not
# typos to be corrected - they are the absence of an address, wearing the shape
# of one.
BAD_EMAIL_PLACEHOLDERS = {
    "unknow", "unknown", "none", "null", "na", "n/a", "nan", "test",
    "noemail", "no-email", "no_email", "nomail", "email", "blank", "empty",
    "notprovided", "not-provided", "missing", "xxx", "tbd", "placeholder",
}

BAD_EMAIL_PREFIXES = {
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "do_not_reply",
    "notifications", "notification", "automated", "mailer", "mailer-daemon",
    "postmaster", "bounce", "bounces", "autoresponder", "newsletter",
    "alerts", "alert", "system", "info",
}

# Domains that belong to SaaS tools, not personal inboxes
BAD_EMAIL_SYSTEM_DOMAINS = {
    "domo.com", "salesforce.com", "hubspot.com", "marketo.com", "mailchimp.com",
    "constantcontact.com", "sendgrid.net", "amazonses.com", "mailgun.org",
    "auto-maildelivery.com", "mail-delivery.com", "bulk-mailer.com",
    "massmail.com", "emaildelivery.com", "mailinglist.com",
}

# If any of these appear anywhere in the domain, it's a bulk/system sender
BAD_EMAIL_DOMAIN_PATTERNS = [
    "auto-mail", "automail", "bulk-mail", "bulkmail", "mass-mail", "massmail",
    "mail-delivery", "maildelivery", "email-delivery", "emaildelivery",
    "noreply", "no-reply", "donotreply", "newsletter", "mailinglist",
    "notification", "auto-send", "autosend",
]

# Common misspelled personal email domains
BAD_EMAIL_TYPO_DOMAINS = {
    # gmail
    "gnail.com", "gmial.com", "gamil.com", "gmai.com", "gmail.co", "gmail.org",
    "gmail.net", "gmaill.com", "gmil.com", "gmal.com", "gmali.com", "gimail.com",
    "gemail.com", "gmaol.com", "gmaul.com",
    # yahoo
    "yahoa.com", "yaho.com", "yahooo.com", "yaoo.com", "ymail.co",
    "yahomail.com", "yhaoo.com", "yahou.com", "yhoo.com",
    # hotmail / outlook
    "hotmial.com", "homail.com", "hotmai.com", "hotmal.com", "hotmale.com",
    "outlok.com", "outllok.com", "outook.com", "otlook.com", "ourlook.com",
    "outlookl.com", "outlook.co", "outloook.com",
    # aol / icloud / other
    "aoll.com", "aol.co", "aoo.com", "icloud.co", "iclould.com", "iclod.com",
    "comast.net", "comacast.net",
}


def _check_email_quality(email: str) -> str | None:
    """
    Returns a string describing why the email is suspect, or None if it looks fine.
    Used to flag leads as needs_review instead of silently importing bad addresses.
    """
    if not email:
        return None
    low = email.strip().lower()
    if "@" not in low:
        return "invalid_format"
    prefix, domain = low.split("@", 1)

    # A DOMAIN WITH NO DOT IS NOT A DOMAIN. "unknow@unknown" passed every check
    # below - the domain is not a known system domain, matches no pattern and
    # is not a typo of a real one - so it counted as a usable address and the
    # lead looked emailable. It cannot receive anything. Two rows of Restland's
    # first 100 were exactly this.
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        return "invalid_format"
    if prefix in BAD_EMAIL_PLACEHOLDERS or domain.split(".")[0] in BAD_EMAIL_PLACEHOLDERS:
        return "placeholder"
    if prefix in BAD_EMAIL_PREFIXES:
        return "system_address"
    if domain in BAD_EMAIL_SYSTEM_DOMAINS:
        return "system_domain"
    if any(pat in domain for pat in BAD_EMAIL_DOMAIN_PATTERNS):
        return "system_domain"
    if domain in BAD_EMAIL_TYPO_DOMAINS:
        return "typo_domain"
    return None

# Tier -> message track mapping. Every tier maps to SOMETHING now; nothing
# maps to "excluded."
TIER_TO_TRACK = {
    "pre_need": "pre_need_lock_price",
    "at_need": "at_need_support",
    "imminent": "imminent_support",
    "contract_sold": "upsell_existing",
    "email_only": "email_only_nurture",
    "partial": "needs_review",
    "addr_only": "needs_review",
}


def _merge_custom_fields(
    existing_json: str | None,
    email_quality_issue: str | None,
    campaign_purpose: str | None = None,
    offer_hook: str | None = None,
) -> str | None:
    """Merges campaign metadata and email quality flag into the existing custom_fields JSON blob."""
    base = {}
    if existing_json:
        try:
            base = json.loads(existing_json)
        except Exception:
            base = {}
    if email_quality_issue:
        base["email_quality_issue"] = email_quality_issue
    if campaign_purpose:
        base["campaign_purpose"] = campaign_purpose
    if offer_hook:
        base["offer_hook"] = offer_hook
    return json.dumps(base) if base else None


# A CRM export very often carries ONE name column, not two. Restland's own
# Dynamics export is exactly that shape: "Full Name" and "Phone E164", with no
# "Last Name" anywhere. The importer required a last-name column and knew none
# of those spellings, so it raised before reading a single row and a legitimate
# 100-lead file could not be imported at all.
#
# Honorifics and suffixes are stripped so the LAST TOKEN is genuinely the
# family name: "Dr. Daniel Pham" is Pham, and "John Smith Jr." is Smith, not
# Jr. Getting this wrong matters more here than in most systems - the name is
# what a funeral home says out loud to a grieving family, and dedup keys on the
# last name, so "Jr." as a surname both misaddresses people and silently
# corrupts duplicate matching.
_HONORIFICS = {
    "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "rev", "fr", "sr.",
    "sir", "madam", "rabbi", "pastor", "capt", "col", "lt", "sgt", "hon",
}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "md", "phd", "dds", "esq", "do", "rn"}


def _strip_dots(token: str) -> str:
    return token.replace(".", "").replace(",", "").strip().lower()


def split_full_name(full: str) -> tuple[str, str]:
    """('Dr. Daniel Pham') -> ('Daniel', 'Pham'). Returns (first, last).

    Handles the "Last, First" form some exports use, drops honorifics and
    generational suffixes, and keeps middle names with the first name rather
    than discarding information the office may recognise a person by.

    A single-token name becomes the LAST name, never the first: last name is
    what dedup and the greeting both rely on, so a lone "Cher" is safer as a
    surname than as a first name with an empty surname.
    """
    raw = (full or "").strip()
    if not raw:
        return "", ""

    # "Cordon, Donna Barrows" — the comma form is unambiguous, so trust it.
    if "," in raw:
        head, _, tail = raw.partition(",")
        head, tail = head.strip(), tail.strip()
        if head and tail and _strip_dots(tail) not in _SUFFIXES:
            return tail, head

    tokens = [t for t in raw.split() if t.strip()]
    while tokens and _strip_dots(tokens[0]) in _HONORIFICS:
        tokens.pop(0)
    while len(tokens) > 1 and _strip_dots(tokens[-1]) in _SUFFIXES:
        tokens.pop()

    if not tokens:
        return "", raw
    if len(tokens) == 1:
        return "", tokens[0]
    return " ".join(tokens[:-1]), tokens[-1]


def _build_column_lookup(columns) -> dict:
    """
    Map canonical field -> the file's column name. EXACT MATCH, ALIAS ORDER.

    This is DETERMINISTIC ALIAS MATCHING, not fuzzy matching. A header must
    appear verbatim (case- and whitespace-insensitive) in the alias list; a
    header the list does not name is not matched approximately, it is parked in
    custom_fields.

    Two properties matter and neither is accidental:

    1. ALIAS ORDER DECIDES, NOT COLUMN ORDER. The previous version iterated the
       FILE's columns and took whichever appeared first, so a file holding both
       "Last Activity/Note" and "Open Activity Date" mapped whichever the export
       happened to put on the left. The alias lists are written most-preferred
       first, and that preference is now what wins - the same file always maps
       the same way.

    2. NO FUZZY MATCHING, DELIBERATELY. Approximate header matching on a
       COMPLIANCE column is how "Do not allow Bulk Emails" gets read as
       "Allow Emails?". Permission columns are resolved by their own explicit
       table in app/services/permission_values.py and never by similarity.
    """
    lowered = {str(c).strip().lower(): c for c in columns}
    lookup = {}
    for canonical, variants in HEADER_MAP.items():
        for variant in variants:
            if variant in lowered:
                lookup[canonical] = lowered[variant]
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
        return "contract_sold"

    if not raw_value:
        return "partial"

    val = str(raw_value).strip().lower()
    if "imminent" in val:
        return "imminent"
    if "at" in val and "need" in val:
        return "at_need"
    if "pre" in val and "need" in val:
        return "pre_need"
    return "partial"


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


PERMISSION_FIELDS = ("allow_email", "allow_bulk_email", "allow_sms", "allow_voice")


def inherit_restrictions(db: Session, lead, organization_id: str) -> bool:
    """
    A LATER IMPORT MAY NOT WEAKEN AN EARLIER DENIAL.

    Import creates rows; it does not update them. That alone looked safe - the
    old row keeps its denial - but it is not, because the NEW row is a live,
    sendable record for the same human. A person who opted out in March and is
    re-uploaded in September from a file that says "Allow" becomes reachable
    again through the second row. The opt-out was never overwritten; it was
    out-voted.

    So every newly imported lead inherits the MOST RESTRICTIVE state held by any
    existing lead in the same organization that identifies the same person -
    by normalized phone, or by normalized email. Restriction only ever travels
    in one direction: `more_restrictive` cannot return allow where either side
    said deny, and this function has no branch that sets a permission to True.

    Returns True if anything was tightened, so the import can report it.
    """
    match = []
    if lead.phone:
        match.append(Lead.phone == lead.phone)
    if lead.email:
        match.append(func.lower(Lead.email) == lead.email.strip().lower())
    if not match:
        return False

    from sqlalchemy import or_
    priors = db.query(Lead).filter(
        Lead.organization_id == organization_id,      # NEVER across tenants
        Lead.id != lead.id,
        or_(*match),
    ).all()
    if not priors:
        return False

    tightened = False
    for field in PERMISSION_FIELDS:
        state = pv.from_bool(getattr(lead, field, None))
        for prior in priors:
            state = pv.more_restrictive(state, pv.from_bool(getattr(prior, field, None)))
        # A prior row that is itself suppressed denies every channel.
        for prior in priors:
            if (prior.status or "").strip().lower() == "dnc" or \
                    (prior.manual_flag or "").strip().lower() == "remove_all":
                state = pv.DENY
            if field == "allow_email" and \
                    (prior.manual_flag or "").strip().lower() == "bad_email":
                state = pv.DENY
        new_value = pv.to_bool(state)
        if new_value != getattr(lead, field, None):
            setattr(lead, field, new_value)
            tightened = True
    return tightened


def _is_call_restricted(allow_calls_raw: str) -> bool:
    if not allow_calls_raw:
        return False
    return "do not allow" in allow_calls_raw.strip().lower()


def parse_excel_file(file_path: str) -> list[dict]:
    """
    Reads Excel (.xlsx, .xls) or CSV files including Google Contacts exports.
    For Excel, reads the FIRST sheet only - real Restland exports include a
    second "hiddenSheet" used internally by the CRM export tool for
    column-mapping metadata, which is not lead data and must be ignored.
    """
    lower_path = file_path.lower()
    if lower_path.endswith(".csv") or lower_path.endswith(".vcf"):
        # Try CSV first, then fall back with different encodings
        try:
            df = pd.read_csv(file_path, dtype=str, encoding="utf-8-sig")
        except Exception:
            try:
                df = pd.read_csv(file_path, dtype=str, encoding="latin-1")
            except Exception as exc:
                raise ValueError(f"Could not read this file as CSV: {exc}") from exc
    else:
        try:
            df = pd.read_excel(file_path, sheet_name=0, dtype=str)
        except Exception as exc:
            raise ValueError(f"Could not read this file as Excel: {exc}") from exc
    df = df.fillna("")

    lookup = _build_column_lookup(df.columns)

    if "phone" not in lookup and "email" not in lookup:
        raise ValueError(
            f"Could not find a phone OR email column in file. "
            f"Found columns: {list(df.columns)}"
        )
    if "last_name" not in lookup and "full_name" not in lookup:
        raise ValueError(
            "Could not find a name column. Expected 'Last Name' or 'Full Name'. "
            f"Found columns: {list(df.columns)}"
        )

    # Build a set of all column names already mapped to canonical keys
    # so we can capture everything else as custom_fields
    mapped_raw_cols = set(lookup.values())

    # PERMISSION COLUMNS ARE NEVER "EXTRA".
    #
    # They are read by their own table, so they are not in `lookup` and would
    # otherwise be swept into custom_fields - which is exactly the bug: an
    # opt-out that exists only inside a JSON blob is an opt-out no send path
    # will ever see. They are excluded from the parked set AND still recorded
    # raw, so the states below can always be audited against the cells.
    perm_cols = {c for c in df.columns
                 if str(c).strip().lower() in pv.ALL_PERMISSION_COLUMNS}
    mapped_raw_cols |= perm_cols

    rows = []
    for _, row in df.iterrows():
        # Capture extra columns not in HEADER_MAP as a JSON blob
        custom = {
            str(col): str(row[col]).strip()
            for col in df.columns
            if col not in mapped_raw_cols and str(row[col]).strip()
        }

        first_name = row.get(lookup.get("first_name", ""), "").strip()
        last_name = row.get(lookup.get("last_name", ""), "").strip()
        # An explicit Last Name column always wins; the single-column form is
        # only consulted to fill what is genuinely missing, so a file carrying
        # both is never second-guessed.
        if not last_name and "full_name" in lookup:
            split_first, split_last = split_full_name(
                row.get(lookup["full_name"], "").strip())
            last_name = split_last
            if not first_name:
                first_name = split_first

        rows.append({
            "first_name": first_name,
            "last_name": last_name,
            "phone": row.get(lookup.get("phone", ""), "").strip(),
            "email": row.get(lookup.get("email", ""), "").strip(),
            "tier_raw": row.get(lookup.get("tier", ""), "").strip(),
            "status_reason_raw": row.get(lookup.get("status_reason", ""), "").strip(),
            "allow_calls_raw": row.get(lookup.get("allow_calls", ""), "").strip(),
            "last_action_raw": row.get(lookup.get("last_action", ""), "").strip(),
            "last_contact_date_raw": row.get(lookup.get("last_contact_date", ""), "").strip(),
            "street_address": row.get(lookup.get("street_address", ""), "").strip(),
            "city": row.get(lookup.get("city", ""), "").strip(),
            "state": row.get(lookup.get("state", ""), "").strip(),
            "zip_code": row.get(lookup.get("zip_code", ""), "").strip(),
            "custom_fields": json.dumps(custom) if custom else None,
            "permissions": pv.read_all(
                {str(c).strip().lower(): row[c] for c in df.columns}),
        })
    return rows


def import_leads_from_excel(
    db: Session,
    file_path: str | None,
    organization_id: str,
    uploading_user_id: str,
    source_year: int = None,
    source_filename: str = None,
    dry_run: bool = False,
    force_new_inquiry: bool = False,
    _preloaded_rows: list | None = None,
    relationship_type: str = None,   # applied to every lead in this batch
    import_list_name: str = None,    # human-readable name for this import list
    source_category: str = None,     # e.g. "crm_export", "referral", "web_form"
    campaign_purpose: str = None,    # e.g. "file_review", "markers", "pre_need", "event_invite"
    offer_hook: str = None,          # e.g. "lunch_and_learn", "free_tour", "free_space", "custom"
    imported_by_name: str = None,    # full name of user who ran this import
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
    rows = _preloaded_rows if _preloaded_rows is not None else parse_excel_file(file_path)

    created_leads = []
    duplicate_count = 0
    skipped_no_contact_info = 0
    skipped_internal_records = 0
    flagged_call_restricted = 0
    flagged_needs_tier_review = 0
    email_only_count = 0
    flagged_bad_email = 0
    usable_phone_count = 0
    usable_email_count = 0
    tier_counts = {}

    # Within-batch dedup sets (catch duplicates inside the same uploaded file)
    seen_phone_keys: set = set()   # (norm_phone, norm_last_name)
    seen_email_keys: set = set()   # (norm_email, norm_last_name) for email-only leads
    inherited_restrictions = 0     # rows tightened by a denial already on record
    permission_review_count = 0    # rows carrying a permission cell nobody can read
    permission_denials = {p: 0 for p in pv.PERMISSIONS}

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

        # CHANNEL PERMISSION, ALL FOUR, FROM THE CANONICAL TABLE.
        #
        # `perm[...]` is one of allow / deny / unknown. Unknown is not consent
        # and never becomes one. `_is_call_restricted` is kept alongside so the
        # legacy DNC behaviour is unchanged, and a denial from EITHER reader
        # restricts - the new reader can only add restriction, never remove it.
        perm_read = row.get("permissions") or {
            "permissions": {}, "needs_review": False, "evidence": {}}
        perm = perm_read.get("permissions", {})
        call_restricted = (_is_call_restricted(row["allow_calls_raw"])
                           or perm.get(pv.VOICE) == pv.DENY)
        if perm_read.get("needs_review"):
            permission_review_count += 1
        for _p in pv.PERMISSIONS:
            if perm.get(_p) == pv.DENY:
                permission_denials[_p] += 1
        email_quality_issue = _check_email_quality(row["email"]) if row["email"] else None

        # Route: phone present -> SMS channel. No phone but email present -> email-only channel.
        if phone_norm:
            contact_channel = "sms"
        else:
            contact_channel = "email_only"
            tier = "email_only"  # channel overrides tier classification for routing purposes
            email_only_count += 1

        message_track = TIER_TO_TRACK.get(tier, "needs_review")
        if tier == "partial":
            flagged_needs_tier_review += 1

        if phone_norm:
            usable_phone_count += 1
        if row["email"] and not email_quality_issue:
            usable_email_count += 1

        if email_quality_issue:
            flagged_bad_email += 1

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
            status="new",
            source_year=source_year,
            source_file=source_filename,
            last_action_raw=row["last_action_raw"] or None,
            last_contact_date=last_contact_dt,
            status_reason_raw=row["status_reason_raw"] or None,
            # A DEAD ADDRESS IS FLAGGED, NOT QUIETLY KEPT.
            #
            # The quality issue was recorded in custom_fields and nowhere the
            # send paths look, so a lead carrying "unknow@unknown" was a
            # perfectly ordinary emailable lead as far as the product was
            # concerned. `manual_flag` is the field the email queue already
            # excludes on, so setting it here stops the send instead of merely
            # describing the problem. Only the EMAIL channel is affected -
            # these leads still have good phone numbers and remain textable.
            manual_flag=("bad_email" if email_quality_issue else None),
            manual_flag_reason=(("Imported with an unusable email address (%s)"
                                 % email_quality_issue)
                                if email_quality_issue else None),
            street_address=row.get("street_address") or None,
            city=row.get("city") or None,
            state=row.get("state") or None,
            zip_code=row.get("zip_code") or None,
            # New context fields — set batch-level values; default cold if not specified
            relationship_type=relationship_type or "cold_lead",
            import_list_name=import_list_name or None,
            source_category=source_category or None,
            imported_by_name=imported_by_name or None,
            custom_fields=_merge_custom_fields(
                row.get("custom_fields"),
                email_quality_issue,
                campaign_purpose=campaign_purpose,
                offer_hook=offer_hook,
            ),
            # A bad email address is a denial of the EMAIL channel, stated as
            # one. It was already flagged; now it is also a permission, so the
            # two cannot disagree.
            allow_email=pv.to_bool(
                pv.more_restrictive(
                    perm.get(pv.EMAIL, pv.UNKNOWN),
                    pv.DENY if email_quality_issue else pv.UNKNOWN)),
            allow_bulk_email=pv.to_bool(perm.get(pv.BULK_EMAIL, pv.UNKNOWN)),
            allow_sms=pv.to_bool(perm.get(pv.SMS, pv.UNKNOWN)),
            allow_voice=pv.to_bool(
                pv.DENY if call_restricted else perm.get(pv.VOICE, pv.UNKNOWN)),
            permission_review=bool(perm_read.get("needs_review")),
            permission_source=("import:%s" % source_filename) if source_filename else "import",
            permission_raw=(json.dumps(perm_read.get("evidence"))
                            if perm_read.get("evidence") else None),
        )
        db.add(lead)
        db.flush()

        # The one-way valve, applied to every row: whatever this file says, a
        # denial already on record for this person in this organization wins.
        if inherit_restrictions(db, lead, organization_id):
            inherited_restrictions += 1

        tier_counts[tier] = tier_counts.get(tier, 0) + 1

        # ── Dedup: phone-based leads use ContactRegistry ──────────────────
        #
        # A DUPLICATE IS NOT A DNC. Every branch below used to set
        # `status = "dnc"` alongside the flag, which put a lead into the
        # do-not-contact population for a bookkeeping reason. DNC means a human
        # asked not to be contacted; duplicate means we may already hold this
        # person under another row. The flag alone already excludes the lead
        # from every send path (see sms_service.send_batch, cadence_service,
        # ai_conversation_service), so the DNC added nothing except an
        # irreversible-looking state and a wrong number on the DNC screen.
        #
        # call_restricted is a REAL suppression and still sets DNC.
        if phone_norm:
            phone_key = (phone_norm, normalize_last_name(row["last_name"]))
            if phone_key in seen_phone_keys:
                # Duplicate within this same file — flagged, not suppressed.
                lead.is_duplicate = True
                lead.duplicate_reason = "same_file_phone"
                lead.duplicate_match_field = "phone+last_name"
                lead.duplicate_match_value = phone_norm
                lead.status = "needs_tier_review" if tier == "partial" else "new"
                duplicate_count += 1
            else:
                seen_phone_keys.add(phone_key)
                is_dup, registry_entry = check_and_register(
                    db,
                    organization_id=organization_id,
                    phone_raw=row["phone"],
                    last_name_raw=row["last_name"],
                    lead_id=lead.id,
                    user_id=uploading_user_id,
                )
                if call_restricted:
                    lead.status = "dnc"
                    flagged_call_restricted += 1
                elif is_dup:
                    lead.is_duplicate = True
                    lead.duplicate_of_lead_id = registry_entry.first_seen_lead_id
                    # A placeholder registry entry carries no real last name -
                    # it was seeded phone-only from the old desktop sent log -
                    # so say which of the two rules actually fired.
                    _placeholder = (getattr(registry_entry, "normalized_last_name", None)
                                    == PLACEHOLDER_LAST_NAME)
                    lead.duplicate_reason = ("registry_placeholder" if _placeholder
                                             else "registry_exact")
                    lead.duplicate_match_field = "phone" if _placeholder else "phone+last_name"
                    lead.duplicate_match_value = phone_norm
                    lead.status = "needs_tier_review" if tier == "partial" else "new"
                    duplicate_count += 1
                else:
                    lead.status = (
                        "needs_tier_review" if tier == "partial" else "new"
                    )

        # ── Dedup: email-only leads — check by normalized email + last name ─
        else:
            norm_email = (row["email"] or "").strip().lower()
            norm_last  = normalize_last_name(row["last_name"])
            email_key  = (norm_email, norm_last)

            if norm_email and email_key in seen_email_keys:
                # Duplicate within this same file — flagged, not suppressed.
                lead.is_duplicate = True
                lead.duplicate_reason = "same_file_email"
                lead.duplicate_match_field = "email+last_name"
                lead.duplicate_match_value = norm_email
                lead.status = "new"
                duplicate_count += 1
            elif norm_email:
                seen_email_keys.add(email_key)
                # Check the database for an existing lead with same email+last_name in this org
                from app.models.models import Lead as LeadModel
                existing_email_lead = (
                    db.query(LeadModel)
                    .filter(
                        LeadModel.organization_id == organization_id,
                        func.lower(LeadModel.email) == norm_email,
                        func.lower(LeadModel.last_name) == norm_last,
                        LeadModel.id != lead.id,  # exclude the row we just flushed
                        LeadModel.is_duplicate.is_(False),
                    )
                    .first()
                )
                if existing_email_lead:
                    lead.is_duplicate = True
                    lead.duplicate_of_lead_id = existing_email_lead.id
                    lead.duplicate_reason = "existing_email"
                    lead.duplicate_match_field = "email+last_name"
                    lead.duplicate_match_value = norm_email
                    lead.status = "new"
                    duplicate_count += 1
                else:
                    lead.status = "new"  # queued for email outreach
            else:
                lead.status = "new"  # no email either — unusual but let it through

        created_leads.append(lead)

    if dry_run:
        db.rollback()
    else:
        db.commit()
        # Auto-start cadence for every eligible lead in this batch.
        # start_cadence() handles its own eligibility checks (skips DNC,
        # duplicates, needs_tier_review, email_only) so it's safe to call
        # on the full created_leads list — it just no-ops on ineligibles.
        try:
            from app.services.cadence_service import start_cadence
            cadence_started = 0
            for lead in created_leads:
                state = start_cadence(db, lead)
                if state:
                    cadence_started += 1
            if cadence_started:
                db.commit()
        except Exception:
            pass  # cadence auto-start is best-effort — never fail the import over it

    return {
        "total_rows": len(rows),
        "imported": len(created_leads),
        "new_active_sms_leads": len(created_leads) - duplicate_count - flagged_call_restricted - email_only_count,
        "email_only_leads_queued": email_only_count,
        "duplicates_flagged": duplicate_count,
        "flagged_call_restricted": flagged_call_restricted,
        # Compliance the file actually stated, per channel, stated back rather
        # than absorbed silently. An import that drops 5,612 email opt-outs
        # should say so on the screen that says it succeeded.
        "permission_denials": dict(permission_denials),
        "permission_needs_review": permission_review_count,
        "permission_inherited_restrictions": inherited_restrictions,
        "flagged_needs_tier_review": flagged_needs_tier_review,
        "flagged_bad_email": flagged_bad_email,
        # What the preview must be able to state plainly BEFORE anything is
        # committed: how many of these people we can actually reach, on which
        # channel. "email_only_leads_queued" answers a different question - it
        # counts rows with an email and NO usable phone - and reading it as
        # "did the emails come through" is what sent Mike looking for a bug
        # that was not there.
        "usable_phone": usable_phone_count,
        "usable_email": usable_email_count,
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


def import_leads_from_rows(
    db,
    rows: list[dict],
    organization_id: str,
    uploading_user_id: str,
    source_filename: str = "API import",
    source_year: int = None,
    dry_run: bool = False,
) -> dict:
    """
    Same import logic as import_leads_from_excel but takes pre-parsed rows
    directly instead of a file path. Used by Google Contacts import and
    any other source that produces rows programmatically rather than from
    a file upload.
    """
    return import_leads_from_excel(
        db=db,
        file_path=None,
        organization_id=organization_id,
        uploading_user_id=uploading_user_id,
        source_year=source_year,
        source_filename=source_filename,
        dry_run=dry_run,
        _preloaded_rows=rows,
    )
