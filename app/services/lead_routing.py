"""LEAD INTELLIGENCE PIPELINE — normalize -> dedupe -> suppression -> qualify -> route.

One module, so the scraper, the Lead Intelligence API and the tests all ask the
same questions the same way.

HARD GUARANTEES (asserted by tests/test_lead_intelligence.py)
------------------------------------------------------------
* Qualification is `app.services.qualification.qualify_one`, unchanged. Its
  thresholds (HIGH >= 45, MEDIUM 22-44, LOW < 22) are read from that module and
  never redefined here.
* Routing is an explicit human action. It writes a STAGED Universal Intake
  batch (import_batches / import_staged_rows) in the destination organization
  only, for that organization to review and commit. It never creates a Lead,
  never grants SMS consent, never enrolls a cadence and never sends.
* EXCLUDED, suppressed, duplicate and invalid prospects cannot be routed, and
  the destination organization's suppression / DNC records are re-checked at
  routing time (a prospect that was clean when scraped may not be now).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models.lead_intel_models import (LeadIntelProspect, LeadIntelRoutingRule,
                                          LeadIntelScrapeJob)
from app.services import qualification as Q
from app.services.dedup_service import normalize_phone as _digits11
from app.utils.time_fmt import iso_utc  # S19: explicit-UTC timestamps

log = logging.getLogger(__name__)

GOD_PLATFORM_ORG_ID = "org-god-platform"

# ── industry -> query terms ─────────────────────────────────────────────────
#
# The industry is a convenience for the operator: it maps to search terms the
# provider understands. A free-text query is still accepted and wins when both
# are given (the industry's terms are appended only if the query does not
# already mention them).
INDUSTRY_TERMS: Dict[str, str] = {
    "funeral_home": "funeral homes",
    "insurance": "insurance agency",
    "financial_advisor": "financial advisor",
    "real_estate": "real estate agent",
    "real_estate_investor": "real estate investors we buy houses",
    "car_dealership": "car dealership",
    "isp": "internet service provider",
    "law_firm": "law firm",
    "accounting": "CPA accounting firm",
    "chiropractor": "chiropractor",
    "roofing": "roofing contractor",
    "hvac": "HVAC contractor",
    "plumbing": "plumber",
    "dental": "dentist",
    "restaurant": "restaurant",
}

INDUSTRY_LABELS: Dict[str, str] = {
    "funeral_home": "Funeral Homes", "insurance": "Insurance Agencies",
    "financial_advisor": "Financial Advisors", "real_estate": "Real Estate Agents",
    "real_estate_investor": "Real Estate Investors", "car_dealership": "Car Dealerships",
    "isp": "Internet / Fiber Providers", "law_firm": "Law Firms",
    "accounting": "Accountants", "chiropractor": "Chiropractors",
    "roofing": "Roofing Contractors", "hvac": "HVAC", "plumbing": "Plumbing",
    "dental": "Dental", "restaurant": "Restaurants",
}


def industry_query(industry: Optional[str], query: Optional[str]) -> str:
    q = (query or "").strip()
    terms = INDUSTRY_TERMS.get((industry or "").strip().lower(), "")
    if not terms and industry:
        # An industry the table does not know is used as its own search term,
        # rather than silently ignored.
        terms = str(industry).replace("_", " ").strip()
    if q and terms and terms.lower() not in q.lower():
        return "%s %s" % (q, terms)
    return q or terms


# ── normalize ───────────────────────────────────────────────────────────────

def to_e164(raw: Optional[str]) -> Optional[str]:
    d = _digits11(raw or "")
    return ("+" + d) if d else None


_ADDR_TAIL = re.compile(r",\s*([^,]+),\s*([A-Z]{2})\s*(\d{5})?(?:-\d{4})?\s*(?:,\s*(USA|United States))?\s*$")


def split_address(address: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """'123 Main St, Dallas, TX 75201, USA' -> ('Dallas', 'TX', '75201')."""
    if not address:
        return None, None, None
    m = _ADDR_TAIL.search(address.strip())
    if not m:
        return None, None, None
    return m.group(1).strip() or None, m.group(2), m.group(3)


def _get(obj, key):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def normalize_business(raw: Any) -> Dict[str, Any]:
    name = (_get(raw, "name") or "").strip() or None
    phone_raw = (_get(raw, "phone") or "").strip() or None
    email = (_get(raw, "email") or "").strip().lower() or None
    website = (_get(raw, "website") or "").strip() or None
    address = (_get(raw, "address") or "").strip() or None
    city, state, zip_code = split_address(address)
    rating = _get(raw, "rating")
    return {
        "external_id": _get(raw, "place_id") or None,
        "name": name,
        "phone_raw": phone_raw,
        "phone_e164": to_e164(phone_raw),
        "email": email,
        "website": website,
        "address": address,
        "city": city,
        "state": state,
        "zip_code": zip_code,
        "rating": None if rating is None else str(rating),
        "reviews_count": _get(raw, "reviews_count"),
    }


def is_valid(n: Dict[str, Any]) -> bool:
    """Something a person could actually reach: a name and a usable phone or email."""
    return bool(n.get("name") and (n.get("phone_e164") or n.get("email")))


# ── suppression ─────────────────────────────────────────────────────────────

def phone_forms(raw: Optional[str]) -> set:
    """Every stored spelling one number may have: as typed, the 11-digit form
    the compliance service and most ingestion paths store (usable_us_phone /
    dedup_service.normalize_phone), and E.164 (intake OrgContact)."""
    out = set()
    if raw:
        out.add(raw.strip())
        d = _digits11(raw)
        if d:
            out.add(d)
            out.add("+" + d)
    out.discard("")
    return out


# Intake email statuses that mean "do not email this address".
_EMAIL_BLOCKED = ("unsubscribed", "suppressed", "hard_bounce")


def suppression_reason(db: Session, org_id: Optional[str], phone: Optional[str],
                       email: Optional[str]) -> Optional[str]:
    """Why this contact may not be put in front of `org_id`, or None.

    Reads the records the compliance and intake services own, each IN THE
    FORMAT THAT TABLE STORES:
      suppression_entries   compliance_service.is_phone_suppressed (11 digits)
      leads                 status 'dnc' or allow_email False; phone in any
                            stored spelling (digits / E.164 / as typed)
      org_contacts          sms_status 'dnc' (E.164 phone or mobile), or an
                            email_status of unsubscribed / suppressed / bounce
    """
    if not org_id or not (phone or email):
        return None
    from sqlalchemy import func as _f, or_ as _or
    from app.models.models import Lead
    from app.models.intake_models import OrgContact
    from app.services.compliance_service import is_phone_suppressed
    forms = phone_forms(phone)
    if forms:
        if is_phone_suppressed(db, org_id, phone):
            return "suppression_list"
        if (db.query(Lead.id).filter(Lead.organization_id == org_id,
                                     Lead.phone.in_(list(forms)),
                                     Lead.status == "dnc").first()):
            return "dnc_lead"
        if (db.query(OrgContact.id).filter(OrgContact.organization_id == org_id,
                                           _or(OrgContact.phone.in_(list(forms)),
                                               OrgContact.mobile_phone.in_(list(forms))),
                                           OrgContact.sms_status == "dnc").first()):
            return "dnc_contact"
    em = (email or "").strip().lower()
    if em:
        if (db.query(Lead.id).filter(Lead.organization_id == org_id,
                                     _f.lower(Lead.email) == em,
                                     _or(Lead.status == "dnc", Lead.allow_email.is_(False)))
                .first()):
            return "dnc_lead"
        if (db.query(OrgContact.id).filter(OrgContact.organization_id == org_id,
                                           _f.lower(OrgContact.email) == em,
                                           _or(OrgContact.email_status.in_(_EMAIL_BLOCKED),
                                               OrgContact.sms_status == "dnc")).first()):
            return "email_suppressed"
    return None


# ── qualification (the existing engine, unchanged) ──────────────────────────

_BUCKET_RANK = {Q.READY: 0, Q.REVIEW: 1, Q.EXCLUDED: 2}
# SMS is evaluated so the answer is explained ("no SMS consent"), and it will
# be EXCLUDED for every scraped business because nobody has consented. That is
# the correct answer, not a defect: consent is never inferred from a number.
QUALIFY_CHANNELS = (Q.CHANNEL_EMAIL, Q.CHANNEL_VOICE, Q.CHANNEL_SMS)


def _as_lead(n: Dict[str, Any], pid: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=pid, first_name=n.get("name"), last_name=None, email=n.get("email"),
        phone=n.get("phone_e164"), status="new", is_test=False, zip_code=n.get("zip_code"),
        street_address=n.get("address"), sms_consent=False, allow_email=None,
        allow_sms=None, allow_voice=None, relationship_type=None, manual_flag=None,
        is_duplicate=False, capacity_state=None,
    )


def qualify_normalized(n: Dict[str, Any], pid: str = "prospect") -> Dict[str, Any]:
    """Best decision across channels + every channel's decision, from qualify_one."""
    lead = _as_lead(n, pid)
    ctx = Q.QualificationContext(None, [], None, [])
    decisions = {ch: Q.qualify_one(lead, ch, ctx) for ch in QUALIFY_CHANNELS}
    best_ch = min(decisions, key=lambda ch: (_BUCKET_RANK[decisions[ch]["bucket"]],
                                              -(decisions[ch]["score"] or 0)))
    best = decisions[best_ch]
    return {"bucket": best["bucket"], "priority": best["priority"], "score": best["score"],
            "best_channel": best_ch, "channels": decisions}


def band_for(score: Optional[int]) -> Optional[str]:
    if score is None:
        return None
    return Q._band(int(score))


def thresholds() -> Dict[str, Any]:
    return {
        "high_min": Q.HIGH_THRESHOLD,
        "medium_min": Q.MEDIUM_THRESHOLD,
        "medium_max": Q.HIGH_THRESHOLD - 1,
        "low_max": Q.MEDIUM_THRESHOLD - 1,
        "max_score_without_evidence": getattr(Q, "MAX_SCORE_WITHOUT_EVIDENCE", None),
        "buckets": list(Q.BUCKETS),
        "priorities": list(Q.PRIORITIES),
    }


# ── ingest (normalize -> dedupe -> suppression -> qualify) ──────────────────

def ingest(db: Session, results: Sequence[Any], *, job: Optional[LeadIntelScrapeJob] = None,
           industry: Optional[str] = None, destination_org_id: Optional[str] = None,
           source: str = "google_places") -> Dict[str, Any]:
    """Put scraped results into the prospect pool. Every result becomes a row so
    the funnel is countable; duplicates/invalid/suppressed rows are marked, not
    dropped. Never touches tenant tables. Does NOT commit."""
    counts = {"discovered": 0, "invalid": 0, "duplicate": 0, "suppressed": 0,
              "qualified": 0, Q.READY: 0, Q.REVIEW: 0, Q.EXCLUDED: 0}
    seen_phone: Dict[str, str] = {}
    seen_ext: Dict[str, str] = {}
    rows: List[LeadIntelProspect] = []
    dest = destination_org_id or (job.destination_org_id if job else None)
    ind = industry or (job.industry if job else None)

    for raw in results:
        counts["discovered"] += 1
        n = normalize_business(raw)
        p = LeadIntelProspect(job_id=job.id if job else None, source=source,
                              industry=ind, destination_org_id=dest, **n)
        p.id = str(uuid.uuid4())
        if not is_valid(n):
            p.stage = "invalid"
            counts["invalid"] += 1
        else:
            dup_of = None
            if n["phone_e164"]:
                dup_of = seen_phone.get(n["phone_e164"])
            if not dup_of and n["external_id"]:
                dup_of = seen_ext.get(n["external_id"])
            if not dup_of:
                q = db.query(LeadIntelProspect.id).filter(LeadIntelProspect.stage != "duplicate")
                if n["phone_e164"]:
                    hit = q.filter(LeadIntelProspect.phone_e164 == n["phone_e164"]).first()
                    dup_of = hit[0] if hit else None
                if not dup_of and n["external_id"]:
                    hit = q.filter(LeadIntelProspect.external_id == n["external_id"]).first()
                    dup_of = hit[0] if hit else None
            if dup_of:
                p.stage = "duplicate"
                p.duplicate_of_id = dup_of
                counts["duplicate"] += 1
            else:
                if n["phone_e164"]:
                    seen_phone[n["phone_e164"]] = p.id
                if n["external_id"]:
                    seen_ext[n["external_id"]] = p.id
                reason = suppression_reason(db, dest, n["phone_e164"], n["email"])
                decision = qualify_normalized(n, p.id)
                p.decision_json = json.dumps(decision["channels"], default=str)
                if reason:
                    p.stage = "suppressed"
                    p.suppressed_reason = reason
                    p.bucket = Q.EXCLUDED
                    counts["suppressed"] += 1
                    counts[Q.EXCLUDED] += 1
                else:
                    p.stage = "qualified"
                    p.bucket = decision["bucket"]
                    p.priority = decision["priority"]
                    p.score = decision["score"]
                    p.best_channel = decision["best_channel"]
                    counts["qualified"] += 1
                    counts[decision["bucket"]] += 1
        db.add(p)
        rows.append(p)
    if job is not None:
        job.staged_count = (job.staged_count or 0) + len(rows)
        job.status = "staged"
        if dest and not job.destination_org_id:
            job.destination_org_id = dest
    db.flush()
    return {"counts": counts, "prospects": rows}


def prospect_dict(p: LeadIntelProspect, org_names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    org_names = org_names or {}
    try:
        channels = json.loads(p.decision_json) if p.decision_json else None
    except Exception:  # noqa: BLE001
        channels = None
    reasons = []
    if channels and p.best_channel and p.best_channel in channels:
        reasons = channels[p.best_channel].get("reasons") or []
    return {
        "id": p.id, "job_id": p.job_id, "source": p.source, "external_id": p.external_id,
        "industry": p.industry, "name": p.name, "phone": p.phone_e164 or p.phone_raw,
        "email": p.email, "website": p.website, "address": p.address, "city": p.city,
        "state": p.state, "zip_code": p.zip_code, "stage": p.stage,
        "duplicate_of_id": p.duplicate_of_id, "suppressed_reason": p.suppressed_reason,
        "bucket": p.bucket, "priority": p.priority, "score": p.score,
        "best_channel": p.best_channel, "reasons": reasons, "channels": channels,
        "destination_org_id": p.destination_org_id,
        "destination_org_name": org_names.get(p.destination_org_id),
        "routed_org_id": p.routed_org_id, "routed_org_name": org_names.get(p.routed_org_id),
        "routed_batch_id": p.routed_batch_id,
        "routed_at": iso_utc(p.routed_at),
        "created_at": iso_utc(p.created_at),
        "routable": routable_reason(p) is None,
        "not_routable_reason": routable_reason(p),
    }


# ── routing ─────────────────────────────────────────────────────────────────

class RoutingError(ValueError):
    pass


def routable_reason(p: LeadIntelProspect) -> Optional[str]:
    if p.routed_org_id:
        return "already_routed"
    if p.stage == "invalid":
        return "invalid"
    if p.stage == "duplicate":
        return "duplicate"
    if p.stage == "suppressed":
        return "suppressed"
    if p.bucket == Q.EXCLUDED or p.bucket is None:
        return "excluded"
    return None


def rule_matches(rule: LeadIntelRoutingRule, p: LeadIntelProspect) -> bool:
    if not rule.is_active:
        return False
    allowed = {b.strip() for b in (rule.allowed_buckets or "").split(",") if b.strip()}
    allowed.discard(Q.EXCLUDED)
    if p.bucket not in allowed:
        return False
    if rule.match_industry and (p.industry or "").lower() != rule.match_industry.lower():
        return False
    if rule.match_state and (p.state or "").upper() != rule.match_state.upper():
        return False
    if rule.match_city and (p.city or "").lower() != rule.match_city.lower():
        return False
    if rule.min_score is not None and (p.score is None or p.score < rule.min_score):
        return False
    return True


def suggest(db: Session, prospects: Iterable[LeadIntelProspect]) -> List[Dict[str, Any]]:
    """Which rule WOULD route each prospect where. A preview; routes nothing."""
    rules = (db.query(LeadIntelRoutingRule).filter(LeadIntelRoutingRule.is_active.is_(True))
             .order_by(LeadIntelRoutingRule.sort_order.asc(),
                       LeadIntelRoutingRule.created_at.asc()).all())
    out = []
    for p in prospects:
        why = routable_reason(p)
        hit = None if why else next((r for r in rules if rule_matches(r, p)), None)
        out.append({"prospect_id": p.id, "not_routable_reason": why,
                    "rule_id": hit.id if hit else None,
                    "rule_name": hit.name if hit else None,
                    "destination_org_id": hit.destination_org_id if hit else None})
    return out


def _csv_for(prospects: Sequence[LeadIntelProspect]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Company", "First Name", "Email", "Phone", "Street Address", "City", "State",
                "Zip Code", "Notes"])
    for p in prospects:
        notes = ["Lead Intelligence prospect %s" % p.id]
        if p.website:
            notes.append("Website: %s" % p.website)
        if p.industry:
            notes.append("Industry: %s" % p.industry)
        if p.score is not None:
            notes.append("Qualification: %s %s (score %s)" % (p.bucket, p.priority or "", p.score))
        w.writerow([p.name or "", p.name or "", p.email or "", p.phone_e164 or p.phone_raw or "",
                    p.address or "", p.city or "", p.state or "", p.zip_code or "",
                    " | ".join(notes)])
    return buf.getvalue().encode("utf-8")


def _undo_batch(db: Session, batch_id: str, org_id: str, prospect_ids: Sequence[str]) -> None:
    from app.models.import_models import ImportBatch, ImportStagedRow
    from app.models.intake_models import ImportBatchFile
    db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == batch_id,
                                     ImportStagedRow.organization_id == org_id
                                     ).delete(synchronize_session=False)
    db.query(ImportBatchFile).filter(ImportBatchFile.batch_id == batch_id
                                     ).delete(synchronize_session=False)
    db.query(ImportBatch).filter(ImportBatch.id == batch_id,
                                 ImportBatch.organization_id == org_id
                                 ).delete(synchronize_session=False)
    (db.query(LeadIntelProspect).filter(LeadIntelProspect.id.in_(list(prospect_ids)),
                                        LeadIntelProspect.routed_batch_id == batch_id)
     .update({LeadIntelProspect.routed_org_id: None, LeadIntelProspect.routed_batch_id: None,
              LeadIntelProspect.routed_at: None, LeadIntelProspect.routed_by_id: None,
              LeadIntelProspect.routed_rule_id: None}, synchronize_session=False))
    db.commit()


def route(db: Session, prospect_ids: Sequence[str], destination_org_id: str, user,
          rule_id: Optional[str] = None) -> Dict[str, Any]:
    """Route prospects into ONE destination organization's Universal Intake as a
    staged batch awaiting that organization's review. COMMITS.

    Refusals are per prospect and reported; if nothing is routable, nothing is
    written and a RoutingError is raised."""
    from app.models.models import Organization
    from app.services.intake import engine as ENG
    from app.services.intake.capture import user_context

    if not destination_org_id or destination_org_id == GOD_PLATFORM_ORG_ID:
        raise RoutingError("Pick a client organization to route into.")
    org = db.query(Organization).filter(Organization.id == destination_org_id).first()
    if org is None:
        raise LookupError("Organization not found.")
    ids = list(dict.fromkeys(prospect_ids or []))
    if not ids:
        raise RoutingError("Select at least one prospect.")
    found = {p.id: p for p in db.query(LeadIntelProspect)
             .filter(LeadIntelProspect.id.in_(ids)).all()}

    accepted: List[LeadIntelProspect] = []
    refused: List[Dict[str, str]] = []
    for pid in ids:
        p = found.get(pid)
        if p is None:
            refused.append({"prospect_id": pid, "reason": "not_found"})
            continue
        why = routable_reason(p)
        if not why:
            # Re-checked against the DESTINATION now, whatever was true at scrape time.
            sup = suppression_reason(db, destination_org_id, p.phone_e164, p.email)
            if sup:
                why = "suppressed:" + sup
        if why:
            refused.append({"prospect_id": pid, "reason": why})
        else:
            accepted.append(p)

    if not accepted:
        raise RoutingError("None of the selected prospects can be routed: %s" % ", ".join(sorted({r["reason"] for r in refused})))

    ctx = user_context(org, user)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    batch = ENG.create_batch(db, ctx, content=_csv_for(accepted),
                             filename="lead-intelligence-%s.csv" % stamp,
                             source="lead_scraper",
                             source_detail="Lead Intelligence routing",
                             list_name="Lead Intelligence %s" % stamp,
                             display_name="Lead Intelligence routing %s" % stamp)
    # Classification for anything the organization later commits: a cold
    # prospect, never an inquiry — nobody asked to hear from this business.
    cfg = json.loads(batch.classification_json or "{}")
    cfg["fallback"] = "cold_prospect"
    batch.classification_json = json.dumps(cfg)

    # The batch and the prospects' routed state commit TOGETHER, so a retry
    # can never create a second batch for the same prospects.
    now = datetime.utcnow()
    for p in accepted:
        p.routed_org_id = org.id
        p.routed_batch_id = batch.id
        p.routed_at = now
        p.routed_by_id = getattr(user, "id", None)
        p.routed_rule_id = rule_id
    db.commit()
    batch_id, batch_code = batch.id, batch.batch_code

    # Analysis stages rows only (import_* tables). The ORGANIZATION reviews and
    # commits from its own Universal Intake; nothing is committed here. If it
    # fails, the batch is removed and the prospects released, so the operator's
    # retry starts clean rather than leaving a half-built batch behind.
    try:
        ENG.run_analysis(db, batch_id, org.id)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.exception("lead_intel.route: analysis failed for batch %s", batch_id)
        _undo_batch(db, batch_id, org.id, [p.id for p in accepted])
        raise RoutingError("Staging failed and was rolled back; nothing was routed. (%s)"
                           % exc.__class__.__name__)

    try:
        from app.routers.audit_log_router import log_action
        log_action(db, org.id, getattr(user, "id", None), "lead_intel.route", "import_batch",
                   batch_id, details={"routed": len(accepted), "refused": len(refused),
                                      "rule_id": rule_id}, commit=False)
    except Exception:  # noqa: BLE001
        log.warning("lead_intel.route audit write failed", exc_info=True)
    db.commit()
    return {"batch_id": batch_id, "batch_code": batch_code,
            "destination_org_id": org.id, "destination_org_name": org.name,
            "routed": len(accepted), "refused": refused,
            "status": "staged_for_review",
            "note": ("Staged in the organization's Universal Intake for review. "
                     "No leads were created, no consent was granted and nothing was sent.")}
