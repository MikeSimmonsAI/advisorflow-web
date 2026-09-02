"""
import_staging_service.py — Parse uploaded file → ImportStagedRow records.
NO live Lead is created here. DNC blocks are authoritative and not reversible.

BULK DEDUP: Instead of per-row SELECT (N+1), we collect all candidate
phone/email/source_id values upfront and load matching existing leads in
bounded IN-query batches, then classify rows from an in-memory lookup.
Tenant isolation is enforced: the org_id filter is applied to every query.

COMPLIANCE: Four consent channels (email, bulk_email, sms, voice) are
independently extracted and normalized.  Ambiguous values NEVER silently
become consent — they enter REVIEW.  More-restrictive existing compliance
is enforced at commit time (import_commit_service.py).
"""
from __future__ import annotations
import json, logging, re, unicodedata
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.import_models import (
    ImportBatch, ImportBatchStatus, ImportDuplicateStatus,
    ImportMatchConfidence, ImportRowReviewStatus, ImportStagedRow,
    ImportValidationStatus,
)
from app.models.models import Lead
from app.services.import_service import (
    HEADER_MAP, TIER_TO_TRACK, _infer_tier, _check_email_quality,
    split_full_name, _build_column_lookup,
)
log = logging.getLogger(__name__)

_DIGIT_RE = re.compile(r"\D")
_DNC_STATUSES = {"dnc", "do_not_contact", "deceased"}

# ── Phone / email normalisation ────────────────────────────────────────────

def _norm_phone(raw: Optional[str]) -> Optional[str]:
    if not raw: return None
    d = _DIGIT_RE.sub("", str(raw))
    if len(d) == 11 and d.startswith("1"): d = d[1:]
    return f"+1{d}" if len(d) == 10 else None

def _norm_email(raw: Optional[str]) -> Optional[str]:
    return (raw or "").strip().lower() or None

def _norm_name(name: Optional[str]) -> Optional[str]:
    if not name: return None
    n = unicodedata.normalize("NFKD", name)
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in n if not unicodedata.combining(c)).lower()) or None

def _is_dnc(lead: Lead) -> bool:
    return bool(lead and lead.status and lead.status.lower() in _DNC_STATUSES)

# ── Consent normalisation ──────────────────────────────────────────────────
# Returns (normalized: bool|None, ambiguous: bool)
#   True  = consent granted
#   False = consent denied
#   None  = unknown / ambiguous — MUST NOT be treated as consent

# THE VALUE TABLES LIVE IN ONE MODULE NOW.
#
# These two sets were the second copy of the platform's opinion about what
# "allow" and "do not allow" mean. Two copies is how the operational pipeline
# and the historical layer end up disagreeing about a single family's consent.
# They are gone; permission_values owns the vocabulary, and a gate asserts this
# module declares no value table of its own.
from app.services import permission_values as _pv  # noqa: E402

def _norm_consent(raw: Optional[str], col_key: str) -> tuple[Optional[bool], bool]:
    """
    Normalize a consent field value.

    DELEGATES to the single platform interpreter. Returns (value, ambiguous)
    exactly as before, so every caller and every consent_* column is unchanged.

    For 'allow_bulk_emails' the column name carries negative polarity
    ("Do not allow Bulk Emails") but the VALUES are self-descriptive:
        "Allow"        → True  (bulk email IS allowed)
        "Do Not Allow" → False (bulk email is NOT allowed)
    A bare boolean on that column is read in the RESTRICTIVE direction only:
    "Yes" → False (denied), "No" → None + review, because granting marketing
    permission on a guess about what the column meant is the one outcome worth
    refusing.

    For all other consent columns (positive polarity), standard mapping applies.
    """
    # ONE INTERPRETER, PLATFORM-WIDE.
    #
    # This function keeps its name, its signature and its callers; what it no
    # longer keeps is its own opinion about what a cell means. Interpretation
    # lives in app/services/permission_values.py and NOWHERE ELSE, so the
    # operational import pipeline and the historical source layer cannot drift
    # into disagreeing about the word "Allow".
    #
    # The unified rule is strictly more restrictive than the two it replaces.
    # This function used to send BOTH "Do not allow Bulk Emails = Yes" and
    # "= No" to review; the first has a restrictive reading (deny), and a
    # denial reached by a plausible reading is still a denial, so it is now
    # taken rather than deferred. The permissive direction is still refused.
    # Nothing that previously denied now allows.
    state, ambiguous = _pv.interpret_canonical(raw, col_key)
    return _pv.to_bool(state), ambiguous

# ── Historical activity date normalisation ─────────────────────────────────

_DATE_FMTS = [
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y",
    "%d/%m/%Y",
]

def _norm_datetime(raw: Optional[str]) -> Optional[datetime]:
    if not raw: return None
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "null", "n/a", ""): return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None

# ── Bulk dedup helpers ─────────────────────────────────────────────────────

_CHUNK = 900  # stay well under SQLite's 999-variable limit; PostgreSQL handles larger

def _build_dedup_index(db: Session, org_id: str,
                       phones: set, emails: set, source_ids: set) -> dict:
    """
    One pass: load all existing leads for this org that match any candidate
    phone, email, or source_id.  Returns three in-memory lookups:
        { "phone": {norm_phone: [Lead, ...]}, "email": {...}, "source_id": {...} }

    Tenant isolation: org_id is always included in every query.
    """
    idx: dict = {"phone": {}, "email": {}, "source_id": {}}

    def _chunked_query(field, values):
        vals = list(v for v in values if v)
        results = []
        for i in range(0, len(vals), _CHUNK):
            chunk = vals[i:i+_CHUNK]
            results.extend(
                db.query(Lead).filter(
                    Lead.organization_id == org_id,
                    field.in_(chunk),
                ).all()
            )
        return results

    for lead in _chunked_query(Lead.phone, phones):
        idx["phone"].setdefault(lead.phone, []).append(lead)
    for lead in _chunked_query(Lead.email, emails):
        idx["email"].setdefault(lead.email, []).append(lead)
    # source_id matching against Lead.id is only meaningful for internal IDs;
    # for external IDs we check against extra_fields (best-effort) or skip.
    # For now, source_id dedup uses phone/email results (no dedicated column on Lead).

    return idx


def _classify_dedup(phone: Optional[str], ln_norm: Optional[str],
                    email: Optional[str], idx: dict) -> tuple:
    """Classify this row against the pre-built dedup index.

    CONFIDENCE IS GRADED BY THE STRENGTH OF THE IDENTIFIER THAT MATCHED, not
    merely by whether something matched.

        phone + last name  -> MATCHED_EXISTING / HIGH
        email + last name  -> MATCHED_EXISTING / LOW
        identifier only    -> POSSIBLE_DUPLICATE / MEDIUM
        nothing            -> NEW / NONE

    Email and phone are not equally strong evidence of the same PERSON. A
    phone number is normally held by one individual; an email address is
    routinely shared by a household, and in this market the household shares
    the last name too - so "email + last name" is exactly the shape a husband
    and wife produce, and reporting it as HIGH is how one family member's
    record gets merged onto another's. It is still a match and still links to
    the lead; it is reported as the weak match it is so the reviewer looks.
    """
    candidates = []
    if phone and phone in idx["phone"]:
        for r in idx["phone"][phone]:
            if ln_norm and _norm_name(r.last_name) == ln_norm:
                return r, ImportDuplicateStatus.MATCHED_EXISTING, ImportMatchConfidence.HIGH
        candidates.extend(idx["phone"][phone])
    if email and email in idx["email"]:
        for r in idx["email"][email]:
            if ln_norm and _norm_name(r.last_name) == ln_norm:
                return r, ImportDuplicateStatus.MATCHED_EXISTING, ImportMatchConfidence.LOW
        candidates.extend(idx["email"][email])
    if candidates:
        return candidates[0], ImportDuplicateStatus.POSSIBLE_DUPLICATE, ImportMatchConfidence.MEDIUM
    return None, ImportDuplicateStatus.NEW, ImportMatchConfidence.NONE


# ── Row parsers ────────────────────────────────────────────────────────────

def _parse_df_rows(df, org_id: str, batch_id: str, db: Session) -> list:
    lookup = _build_column_lookup(list(df.columns))

    # ── Pass 1: collect all candidate identifiers for bulk dedup ──────────
    cand_phones: set   = set()
    cand_emails: set   = set()
    cand_src_ids: set  = set()
    parsed_rows = []

    for idx_r, raw_row in df.iterrows():
        def g(key, _r=raw_row, _l=lookup):
            v = str(_r[_l[key]]).strip() if key in _l and str(_r[_l[key]]).strip() not in ("", "nan") else None
            return v

        phone_raw   = g("phone")
        phone       = _norm_phone(phone_raw)
        # Mobile phone: dedicated column, if present
        mob_raw     = g("mobile_phone")
        mob_norm    = _norm_phone(mob_raw)
        # If no primary phone but mobile exists, use mobile as primary
        if not phone and mob_norm:
            phone = mob_norm
            phone_raw = mob_raw

        email_raw   = g("email")
        email       = _norm_email(email_raw)
        source_id   = g("source_id")

        if phone:   cand_phones.add(phone)
        if email:   cand_emails.add(email)
        if source_id: cand_src_ids.add(source_id)

        parsed_rows.append((idx_r, raw_row, g, phone_raw, phone, mob_raw, mob_norm,
                            email_raw, email, source_id))

    # ── Pass 2: bulk dedup query ──────────────────────────────────────────
    dedup_idx = _build_dedup_index(db, org_id, cand_phones, cand_emails, cand_src_ids)

    # ── Pass 3: classify and build staged rows ────────────────────────────
    staged_rows = []
    for (idx_r, raw_row, g, phone_raw, phone, mob_raw, mob_norm,
         email_raw, email, source_id) in parsed_rows:

        first = g("first_name")
        last  = g("last_name")
        if not first and not last:
            full = g("full_name")
            if full:
                first, last = split_full_name(full)
        ln_norm = _norm_name(last)

        live, dup_status, confidence = _classify_dedup(phone, ln_norm, email, dedup_idx)

        errors, val_status = [], ImportValidationStatus.VALID
        if not phone and not email:
            errors.append("Missing both phone and email")
            val_status = ImportValidationStatus.INVALID
        email_err = _check_email_quality(email) if email else None
        if email_err:
            errors.append(f"Email: {email_err}")
            if val_status == ImportValidationStatus.VALID:
                val_status = ImportValidationStatus.WARNING
        if live and _is_dnc(live):
            dup_status = ImportDuplicateStatus.DNC_BLOCKED

        tier_raw     = g("tier") or g("status_reason")
        status_reason = g("status_reason")
        tier = _infer_tier(tier_raw, status_reason) if (tier_raw or status_reason) else None

        # Phone type provenance
        if mob_norm:
            phone_type = "known_mobile"
        elif phone_raw:
            phone_type = "unknown"
        else:
            phone_type = None

        # Consent fields
        def _c(key):
            raw_v = g(key)
            norm_v, amb = _norm_consent(raw_v, key)
            return raw_v, norm_v, amb

        em_raw, em_norm, em_amb  = _c("allow_emails")
        be_raw, be_norm, be_amb  = _c("allow_bulk_emails")
        sm_raw, sm_norm, sm_amb  = _c("allow_sms")
        vc_raw, vc_norm, vc_amb  = _c("allow_calls")  # voice/calls
        review_required = any([em_amb, be_amb, sm_amb, vc_amb])

        # Historical activity date
        lad_raw  = g("last_activity_date")
        lad_norm = _norm_datetime(lad_raw)

        staged = ImportStagedRow(
            batch_id=batch_id, organization_id=org_id,
            row_number=int(idx_r) + 2,
            first_name=first, last_name=last,
            phone_raw=phone_raw, phone_normalized=phone,
            email_raw=email_raw, email_normalized=email,
            street_address=g("street_address"), city=g("city"),
            state=g("state"), zip_code=g("zip_code"),
            source_category=g("source_category"), tier=tier,
            raw_data=json.dumps({k: str(v) for k, v in dict(raw_row).items()}),
            validation_status=val_status,
            validation_errors=json.dumps(errors) if errors else None,
            duplicate_status=dup_status, match_confidence=confidence,
            matched_lead_id=live.id if live else None,
            review_status=ImportRowReviewStatus.PENDING,
            # compliance
            consent_email=em_norm,           consent_email_raw=em_raw,
            consent_bulk_email=be_norm,      consent_bulk_email_raw=be_raw,
            consent_sms=sm_norm,             consent_sms_raw=sm_raw,
            consent_voice=vc_norm,           consent_voice_raw=vc_raw,
            consent_review_required=review_required,
            # source identity
            source_id=source_id,
            source_id_type="dynamics_contact_guid" if source_id else None,
            # historical activity
            last_activity_date=lad_norm,     last_activity_date_raw=lad_raw,
            # mobile phone provenance
            mobile_phone_raw=mob_raw,        mobile_phone_normalized=mob_norm,
            phone_type=phone_type,
        )
        staged_rows.append(staged)
    return staged_rows


def _parse_google_contacts_rows(contacts: list, org_id: str, batch_id: str, db: Session) -> list:
    # Pass 1: collect identifiers
    parsed = []
    cand_phones: set = set()
    cand_emails: set = set()
    for idx, c in enumerate(contacts):
        names  = (c.get("names") or [{}])[0]
        first  = names.get("givenName") or None
        last   = names.get("familyName") or None
        phones = c.get("phoneNumbers") or []
        phone_raw = phones[0].get("value") if phones else None
        phone     = _norm_phone(phone_raw)
        emails    = c.get("emailAddresses") or []
        email     = _norm_email(emails[0].get("value")) if emails else None
        addrs     = (c.get("addresses") or [{}])[0]
        if phone: cand_phones.add(phone)
        if email: cand_emails.add(email)
        parsed.append((idx, c, first, last, phone_raw, phone, email, addrs))

    # Pass 2: bulk dedup
    dedup_idx = _build_dedup_index(db, org_id, cand_phones, cand_emails, set())

    # Pass 3: build rows
    rows = []
    for idx, c, first, last, phone_raw, phone, email, addrs in parsed:
        ln_norm = _norm_name(last)
        live, dup_status, confidence = _classify_dedup(phone, ln_norm, email, dedup_idx)
        errors, val_status = [], ImportValidationStatus.VALID
        if not phone and not email:
            errors.append("Missing both phone and email")
            val_status = ImportValidationStatus.INVALID
        if live and _is_dnc(live):
            dup_status = ImportDuplicateStatus.DNC_BLOCKED
        staged = ImportStagedRow(
            batch_id=batch_id, organization_id=org_id, row_number=idx + 1,
            first_name=first, last_name=last,
            phone_raw=phone_raw, phone_normalized=phone, email_normalized=email,
            street_address=addrs.get("streetAddress"), city=addrs.get("city"),
            state=addrs.get("region"), zip_code=addrs.get("postalCode"),
            raw_data=json.dumps(c),
            validation_status=val_status,
            validation_errors=json.dumps(errors) if errors else None,
            duplicate_status=dup_status, match_confidence=confidence,
            matched_lead_id=live.id if live else None,
            review_status=ImportRowReviewStatus.PENDING,
            consent_review_required=False,
        )
        rows.append(staged)
    return rows


def stage_batch(batch_id: str, org_id: str, file_path: Optional[str],
                source_type: str, db: Session,
                google_contacts: Optional[list] = None) -> ImportBatch:
    """Main entry: parse source → write ImportStagedRows → update batch status.
    source_type: 'csv' | 'xlsx' | 'google_contacts'
    """
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id,
                                         ImportBatch.organization_id == org_id).first()
    if not batch:
        raise ValueError(f"Batch {batch_id} not found")
    batch.status = ImportBatchStatus.PROCESSING
    db.commit()
    try:
        if source_type == "google_contacts":
            rows = _parse_google_contacts_rows(google_contacts or [], org_id, batch_id, db)
        else:
            import pandas as pd
            df = (pd.read_excel(file_path, dtype=str) if source_type == "xlsx"
                  else pd.read_csv(file_path, dtype=str)).fillna("")
            rows = _parse_df_rows(df, org_id, batch_id, db)
        for r in rows:
            db.add(r)
        db.flush()
        batch.recount(db)
        batch.status = ImportBatchStatus.READY_FOR_REVIEW
        db.commit()
        log.info("Staged %d rows for batch %s", len(rows), batch_id)
        return batch
    except Exception as exc:
        db.rollback()
        try:
            batch.status = ImportBatchStatus.FAILED
            batch.error_message = str(exc)[:500]
            db.commit()
        except Exception:
            pass
        raise
