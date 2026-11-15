"""Consumer Leads - lead sources (external acquisition).

Every source feeds the same inbox through leads.create_or_merge, so dedup,
consent records, history and the test/real split work the same everywhere.

What is operational without anyone else's account:
  custom_webhook  signed JSON (HMAC-SHA256 over "<timestamp>.<raw body>")
  google_ads      Google Ads lead-form webhook (Google sends the key you set)
  csv             controlled CSV import from the back office
What needs an outside account connected first (shown as pending, cannot be
turned on here): facebook / instagram (Meta Lead Ads), tiktok (Instant Forms).

Rules:
  * a REAL source (is_test False) accepts deliveries only while public intake
    is on - the same compliance switch as the website;
  * a TEST source writes only to the test partition, any time;
  * deliveries are idempotent per (source, external id); a repeat is counted,
    not processed twice;
  * the raw payload is kept only until it is processed (or while it waits for
    a retry), then cleared;
  * a delivery that failed for an unexpected reason is retried by the VOLT
    watch loop with backoff (max MAX_ATTEMPTS); one that failed validation
    waits for a person to fix the field mapping and press Retry.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.volt_leads_models import VoltLeadDelivery, VoltLeadSettings, VoltLeadSource
from app.services.volt import common as C
from app.services.volt import leads as L

MAX_ATTEMPTS = 5
MAX_CSV_ROWS = 500
SIGNATURE_WINDOW = 300  # seconds

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "custom_webhook": {"label": "Custom webhook", "ready": True, "connect": "signed",
                       "how": "Send JSON to the URL below with headers X-Volt-Timestamp (Unix seconds) and "
                              "X-Volt-Signature: sha256=<HMAC-SHA256 of \"<timestamp>.<body>\" with the secret>. "
                              "Include an id (or an Idempotency-Key header) so repeats are recognized."},
    "google_ads": {"label": "Google Ads lead form", "ready": True, "connect": "key",
                   "how": "In Google Ads, open the lead form, choose Webhook integration, paste the URL below and "
                          "the key shown once when the source is created."},
    "csv": {"label": "CSV import", "ready": True, "connect": "upload",
            "how": "Upload a CSV from the Lead Sources page. Every row needs a recorded consent statement."},
    "facebook": {"label": "Facebook Lead Ads", "ready": False, "connect": "oauth",
                 "needs": "A Meta business app approved for lead retrieval (leads_retrieval), and the ad account "
                          "owner's Page authorization. Not connected."},
    "instagram": {"label": "Instagram Lead Ads", "ready": False, "connect": "oauth",
                  "needs": "Instagram lead forms run through Meta Lead Ads: the same approved Meta app and Page "
                           "authorization as Facebook. Not connected."},
    "tiktok": {"label": "TikTok Instant Forms", "ready": False, "connect": "oauth",
               "needs": "A TikTok for Business developer app approved for lead generation, and the advertiser's "
                        "authorization. Not connected."},
}
TARGETS = ("first_name", "last_name", "full_name", "phone", "email", "zip", "state", "age_range",
           "coverage_status", "household_size", "income_range", "contact_preference", "best_time",
           "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "consent")
DEFAULT_MAPS = {
    "custom_webhook": {t: t for t in TARGETS},
    "csv": {t: t for t in TARGETS},
    "google_ads": {"first_name": "FIRST_NAME", "last_name": "LAST_NAME", "full_name": "FULL_NAME",
                   "phone": "PHONE_NUMBER", "email": "EMAIL", "zip": "POSTAL_CODE", "state": "REGION"},
}
PROVIDER_CHANNEL = {"google_ads": ("google", "lead_form"), "facebook": ("facebook", "lead_ads"),
                    "instagram": ("instagram", "lead_ads"), "tiktok": ("tiktok", "instant_form")}


def _utc(dt):
    return L._utc(dt)


def _secret_store(plain: str) -> str:
    from app.services.crm_secrets import store_secret
    return store_secret(plain)


def _secret_read(stored: Optional[str]) -> str:
    from app.services.crm_secrets import read_secret
    return read_secret(stored) if stored else ""


def _map(src: VoltLeadSource) -> Dict[str, str]:
    try:
        m = json.loads(src.field_map or "null")
    except ValueError:
        m = None
    return m if isinstance(m, dict) else dict(DEFAULT_MAPS.get(src.provider, {}))


def _intake_on(db: Session, org_id: str) -> bool:
    cfg = db.get(VoltLeadSettings, org_id)
    return bool(cfg and cfg.intake_enabled)


def source_dict(db: Session, src: VoltLeadSource, slug: str) -> Dict[str, Any]:
    p = PROVIDERS.get(src.provider, {})
    last_fail = (db.query(VoltLeadDelivery).filter(VoltLeadDelivery.source_id == src.id,
                                                   VoltLeadDelivery.status == "failed")
                 .order_by(VoltLeadDelivery.received_at.desc()).first())
    live_blocked = (not src.is_test) and src.status == "active" and not _intake_on(db, src.organization_id)
    return {"id": src.id, "provider": src.provider, "provider_label": p.get("label", src.provider),
            "name": src.name, "campaign": src.campaign, "test": bool(src.is_test), "status": src.status,
            "state_label": ("Awaiting account connection" if src.status == "pending_connection"
                            else "Paused" if src.status == "paused"
                            else "On, but held: public intake is off" if live_blocked else "On"),
            "ready": bool(p.get("ready")), "needs": p.get("needs"), "how": p.get("how"),
            "field_map": _map(src),
            "webhook_url": ("/volt/public/%s/leads/hooks/%s" % (slug, src.id))
            if src.provider in ("custom_webhook", "google_ads") else None,
            "received": src.received_count or 0, "failed": src.failed_count or 0,
            "duplicates": src.duplicate_count or 0, "rejected": src.rejected_count or 0,
            "last_received_at": _utc(src.last_received_at), "created_at": _utc(src.created_at),
            "last_error": last_fail.last_error if last_fail else None}


def _source(db: Session, org_id: str, source_id: str) -> VoltLeadSource:
    src = db.get(VoltLeadSource, source_id)
    if src is None or src.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Source not found.")
    return src


def _require_manage(db, user, org_id):
    if L.access(db, user, org_id) != "all":
        raise HTTPException(status_code=403, detail="Lead sources need Consumer Leads manager access.")


def listing(db: Session, user: User, org_id: str) -> Dict[str, Any]:
    _require_manage(db, user, org_id)
    slug = C.program_for_org(db, org_id).slug
    rows = (db.query(VoltLeadSource).filter(VoltLeadSource.organization_id == org_id)
            .order_by(VoltLeadSource.created_at).all())
    return {"intake_enabled": _intake_on(db, org_id),
            "providers": [{"key": k, "label": v["label"], "ready": v["ready"], "needs": v.get("needs"),
                           "how": v.get("how")} for k, v in PROVIDERS.items()],
            "website": {"label": "Customer website", "path": "/volt/check-coverage",
                        "state": "On" if _intake_on(db, org_id) else "Visible, not accepting requests (intake off)"},
            "targets": list(TARGETS), "sources": [source_dict(db, r, slug) for r in rows]}


def create(db: Session, user: User, org_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    _require_manage(db, user, org_id)
    provider = str(body.get("provider") or "")
    if provider not in PROVIDERS:
        L._bad("provider", "Choose a source type.")
    name = str(body.get("name") or "").strip()[:80] or PROVIDERS[provider]["label"]
    src = VoltLeadSource(organization_id=org_id, provider=provider, name=name,
                         campaign=L._clean_campaign(body.get("campaign")), is_test=body.get("test") is True,
                         status="paused" if PROVIDERS[provider]["ready"] else "pending_connection",
                         created_by=user.id, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    secret = None
    if provider in ("custom_webhook", "google_ads"):
        secret = secrets.token_urlsafe(32)
        src.secret_enc = _secret_store(secret)
    db.add(src)
    db.flush()
    C.audit(db, org_id, user, "lead_source", src.id, "created", None, provider)
    out = source_dict(db, src, C.program_for_org(db, org_id).slug)
    out["secret"] = secret          # shown ONCE; only its encrypted form is kept
    return out


def update(db: Session, user: User, org_id: str, source_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    _require_manage(db, user, org_id)
    src = _source(db, org_id, source_id)
    if "status" in body:
        want = body.get("status")
        if want not in ("active", "paused"):
            L._bad("status", "Choose on or paused.")
        if want == "active":
            if src.status == "pending_connection" or not PROVIDERS.get(src.provider, {}).get("ready"):
                raise HTTPException(status_code=409, detail="This source needs its account connected first: %s"
                                    % PROVIDERS.get(src.provider, {}).get("needs", ""))
            if not src.is_test and not _intake_on(db, org_id):
                raise HTTPException(status_code=409, detail="Real lead sources can be turned on only after the "
                                    "owner turns public intake on (compliance review). Use a test source to try it.")
        if src.status != "pending_connection" and want != src.status:
            C.audit(db, org_id, user, "lead_source", src.id, "status", src.status, want)
            src.status = want
    if isinstance(body.get("field_map"), dict):
        clean = {k: str(v)[:80] for k, v in body["field_map"].items() if k in TARGETS and v}
        src.field_map = json.dumps(clean)
        C.audit(db, org_id, user, "lead_source", src.id, "field_map")
    if "name" in body:
        src.name = str(body.get("name") or "").strip()[:80] or src.name
    if "campaign" in body:
        src.campaign = L._clean_campaign(body.get("campaign"))
    src.updated_at = datetime.utcnow()
    db.flush()
    return source_dict(db, src, C.program_for_org(db, org_id).slug)


def deliveries(db: Session, user: User, org_id: str, source_id: str) -> Dict[str, Any]:
    _require_manage(db, user, org_id)
    _source(db, org_id, source_id)
    rows = (db.query(VoltLeadDelivery).filter(VoltLeadDelivery.source_id == source_id)
            .order_by(VoltLeadDelivery.received_at.desc()).limit(100).all())
    return {"deliveries": [{"id": d.id, "external_id": d.external_id, "status": d.status, "lead_id": d.lead_id,
                            "attempts": d.attempts, "error": d.last_error, "received_at": _utc(d.received_at),
                            "processed_at": _utc(d.processed_at), "next_retry_at": _utc(d.next_retry_at),
                            "can_retry": d.status == "failed" and d.payload is not None} for d in rows]}


# ── processing ──────────────────────────────────────────────────────────────

def _flatten(provider: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if provider == "google_ads":
        flat = {c.get("column_id"): c.get("string_value") for c in payload.get("user_column_data") or []
                if isinstance(c, dict)}
        flat.update({"lead_id": payload.get("lead_id"), "campaign_id": payload.get("campaign_id"),
                     "form_id": payload.get("form_id")})
        return flat
    return payload


def _to_body(src: VoltLeadSource, payload: Dict[str, Any]) -> Dict[str, Any]:
    flat = _flatten(src.provider, payload)
    m = _map(src)
    body = {t: flat.get(k) for t, k in m.items() if flat.get(k) not in (None, "")}
    if body.get("full_name") and not body.get("first_name"):
        parts = str(body["full_name"]).strip().split(None, 1)
        body["first_name"] = parts[0] if parts else ""
        body.setdefault("last_name", parts[1] if len(parts) > 1 else None)
    if src.provider in PROVIDER_CHANNEL:
        body.setdefault("utm_source", PROVIDER_CHANNEL[src.provider][0])
        body.setdefault("utm_medium", PROVIDER_CHANNEL[src.provider][1])
    if src.campaign:
        body.setdefault("utm_campaign", src.campaign)
    if src.provider == "google_ads":
        body["consent"] = True       # the advertiser's Google lead form carries its own consent statement
    else:
        c = body.get("consent")
        body["consent"] = c is True or str(c).strip().lower() in ("true", "yes", "1", "y")
    return body


def _consent_note(src: VoltLeadSource, payload: Dict[str, Any]) -> str:
    if src.provider == "google_ads":
        return "Consent captured by Google Ads lead form %s (campaign %s)" % (payload.get("form_id") or "?",
                                                                           payload.get("campaign_id") or "?")
    if src.provider == "csv":
        return "CSV import: %s" % (str(payload.get("_attestation") or "")[:300])
    return "Consent recorded by the sending system (%s)" % src.name


def process(db: Session, d: VoltLeadDelivery) -> None:
    """Turns one stored delivery into a lead. Never raises: the outcome is
    written on the delivery."""
    src = db.get(VoltLeadSource, d.source_id)
    d.attempts = (d.attempts or 0) + 1
    try:
        payload = json.loads(d.payload or "{}")
        body = _to_body(src, payload)
        v = L.validate(body, strict=False)
        with db.begin_nested():
            lead, _created = L.create_or_merge(
                db, d.organization_id, v, test=bool(src.is_test), source_id=src.id,
                consent_version="source:%s" % src.provider, consent_text=_consent_note(src, payload),
                consent_note=_consent_note(src, payload),
                origin="%s · %s%s" % (PROVIDERS[src.provider]["label"], src.name, " (test)" if src.is_test else ""))
        d.status, d.lead_id, d.last_error, d.payload = "processed", lead.id, None, None
        d.processed_at, d.next_retry_at = datetime.utcnow(), None
    except HTTPException as e:       # bad data: wait for a person to fix the mapping and retry
        det = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
        d.status, d.last_error, d.next_retry_at = "failed", "%s: %s" % (det.get("field", "data"), det.get("message")), None
        src.failed_count = (src.failed_count or 0) + 1
    except Exception as e:  # noqa: BLE001 - unexpected: retry later with backoff
        d.status, d.last_error = "failed", "Temporary error: %s" % type(e).__name__
        d.next_retry_at = (datetime.utcnow() + timedelta(minutes=2 ** min(d.attempts, 6))
                           if d.attempts < MAX_ATTEMPTS else None)
        src.failed_count = (src.failed_count or 0) + 1


def _record(db: Session, src: VoltLeadSource, external_id: str, raw: str) -> Dict[str, Any]:
    ext = (external_id or "").strip()[:200] or ("sha256:" + hashlib.sha256(raw.encode()).hexdigest())
    dup = db.query(VoltLeadDelivery).filter(VoltLeadDelivery.source_id == src.id,
                                            VoltLeadDelivery.external_id == ext).first()
    now = datetime.utcnow()
    src.last_received_at = now
    if dup is not None:
        src.duplicate_count = (src.duplicate_count or 0) + 1
        return {"received": True, "duplicate": True, "status": dup.status}
    d = VoltLeadDelivery(source_id=src.id, organization_id=src.organization_id, external_id=ext,
                         status="received", payload=raw, received_at=now)
    db.add(d)
    db.flush()
    src.received_count = (src.received_count or 0) + 1
    process(db, d)
    db.flush()
    return {"received": True, "duplicate": False, "status": d.status}


def _accepting(db: Session, src: VoltLeadSource) -> None:
    if src.status != "active" or (not src.is_test and not _intake_on(db, src.organization_id)):
        raise HTTPException(status_code=503, detail="This source is not accepting leads right now.")


def receive_webhook(db: Session, org_id: str, source_id: str, raw: bytes, headers) -> Dict[str, Any]:
    src = db.get(VoltLeadSource, source_id)
    if src is None or src.organization_id != org_id or src.provider not in ("custom_webhook", "google_ads"):
        raise HTTPException(status_code=404, detail="Not found.")
    text = raw.decode("utf-8", errors="replace")
    if len(raw) > 64 * 1024:
        raise HTTPException(status_code=413, detail="Too large.")
    secret = _secret_read(src.secret_enc)
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    ok = False
    if secret and src.provider == "custom_webhook":
        ts = str(headers.get("x-volt-timestamp") or "")
        sig = str(headers.get("x-volt-signature") or "")
        if ts.isdigit() and abs(time.time() - int(ts)) <= SIGNATURE_WINDOW and sig.startswith("sha256="):
            want = hmac.new(secret.encode(), ("%s.%s" % (ts, text)).encode(), hashlib.sha256).hexdigest()
            ok = hmac.compare_digest(want, sig[7:])
    elif secret and src.provider == "google_ads" and isinstance(payload, dict):
        ok = hmac.compare_digest(str(payload.get("google_key") or ""), secret)
    if not ok:
        src.rejected_count = (src.rejected_count or 0) + 1   # nothing from the request is stored
        db.flush()
        raise HTTPException(status_code=401, detail="Signature check failed.")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Send a JSON object.")
    _accepting(db, src)
    if src.provider == "google_ads":
        payload.pop("google_key", None)          # never keep the shared key with the payload
        if payload.get("is_test") is True and not src.is_test:
            # Google's "Send test data" button: acknowledge, store nothing in the real inbox.
            return {"received": True, "google_test": True}
        ext = str(payload.get("lead_id") or "")
    else:
        ext = str(headers.get("idempotency-key") or payload.get("id") or payload.get("lead_id") or "")
    return _record(db, src, ext, json.dumps(payload))


def import_csv(db: Session, user: User, org_id: str, source_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Rows already parsed by the browser (header -> value). Each row becomes a
    delivery, so repeats of the same row are recognized and failures can be
    reviewed and retried like any other source."""
    _require_manage(db, user, org_id)
    src = _source(db, org_id, source_id)
    if src.provider != "csv":
        L._bad("source_id", "Choose a CSV import source.")
    _accepting(db, src)
    attest = str(body.get("attestation") or "").strip()
    if len(attest) < 20:
        L._bad("attestation", "Describe where and when these people agreed to be contacted (at least 20 characters).")
    rows = body.get("rows")
    if not isinstance(rows, list) or not rows:
        L._bad("rows", "The file has no rows.")
    if len(rows) > MAX_CSV_ROWS:
        L._bad("rows", "Import at most %d rows at a time." % MAX_CSV_ROWS)
    counts = {"processed": 0, "failed": 0, "duplicate": 0}
    for r in rows:
        if not isinstance(r, dict):
            counts["failed"] += 1
            continue
        clean = {str(k)[:60]: (str(v)[:200] if v is not None else None) for k, v in r.items()}
        raw = json.dumps(clean, sort_keys=True)
        clean["_attestation"] = attest
        out = _record(db, src, "row:" + hashlib.sha256(raw.encode()).hexdigest(), json.dumps(clean))
        counts["duplicate" if out["duplicate"] else out["status"] if out["status"] in counts else "failed"] += 1
    C.audit(db, org_id, user, "lead_source", src.id, "csv_import", None, json.dumps(counts), attest[:500])
    db.flush()
    return counts


def retry(db: Session, user: User, org_id: str, delivery_id: str) -> Dict[str, Any]:
    _require_manage(db, user, org_id)
    d = db.get(VoltLeadDelivery, delivery_id)
    if d is None or d.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Delivery not found.")
    if d.status != "failed" or d.payload is None:
        raise HTTPException(status_code=409, detail="Only a failed delivery that still has its data can be retried.")
    _accepting(db, db.get(VoltLeadSource, d.source_id))
    process(db, d)
    C.audit(db, org_id, user, "lead_source", d.source_id, "delivery_retry", None, d.status)
    db.flush()
    return {"status": d.status, "error": d.last_error, "lead_id": d.lead_id}


def retry_pass(db: Session, limit: int = 50) -> int:
    """Called by the VOLT watch loop: retries deliveries that failed for a
    temporary reason, once their backoff time has passed."""
    now = datetime.utcnow()
    rows = (db.query(VoltLeadDelivery).filter(VoltLeadDelivery.status == "failed",
                                              VoltLeadDelivery.next_retry_at.isnot(None),
                                              VoltLeadDelivery.next_retry_at <= now,
                                              VoltLeadDelivery.attempts < MAX_ATTEMPTS)
            .limit(limit).all())
    n = 0
    for d in rows:
        src = db.get(VoltLeadSource, d.source_id)
        if src is None or src.status != "active" or (not src.is_test and not _intake_on(db, src.organization_id)):
            continue
        process(db, d)
        n += 1
    db.flush()
    return n
