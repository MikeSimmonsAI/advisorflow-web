"""Provider-neutral acquisition data: capabilities, adapters, health, routing.

TRUTH RULE — NEVER INVENT AN INTEGRATION.
No real property-data, public-record or skip-trace vendor is connected in
Phase 7. What exists, and is labelled as exactly that everywhere:

    SANDBOX     deterministic synthetic adapters (sandbox_data.py). Their data
                is marked is_test and shown with a SANDBOX badge. Never live.
    MANUAL      a person typing a property, a signal, an owner, a contact.
    IMPORT      a CSV file through the same ingest pipeline as any provider.
    INTERFACE   a capability with a slot and no adapter. Reported, not faked.

Adding a real vendor is one class declaring `key`, `connector_kind = REAL`,
`capabilities`, `costs` and `required_env`, plus one registry line. Credentials
are read from the environment by name (never stored, never in a payload), and
`is_configured()` is computed from the environment on every report, so a
missing key reads MISSING_CREDENTIALS rather than a stale "connected".

Contact enrichment returns the EXISTING `wholesale_enrichment.EnrichmentResult`
shape — one normalized result type in the platform, not two.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.services import wholesale_enrichment as WE
from app.services.evosense import common as C
from app.services.evosense import sandbox_data as SB


class ProviderTimeout(Exception):
    """The provider did not answer in time. Safe to retry once; never forever."""


class ProviderRateLimited(Exception):
    def __init__(self, retry_after_seconds: int = 300):
        super().__init__("rate limited")
        self.retry_after_seconds = retry_after_seconds


class ProviderFailure(Exception):
    pass


class AcquisitionProvider:
    key = "base"
    label = "Base"
    connector_kind = C.INTERFACE_ONLY
    capabilities: tuple = ()
    costs: Dict[str, int] = {}          # capability -> cents per call
    required_env: tuple = ()
    coverage = ""
    freshness_days = 90

    def is_configured(self) -> bool:
        return all(os.environ.get(n) for n in self.required_env)

    def missing_config(self) -> List[str]:
        return [n for n in self.required_env if not os.environ.get(n)]

    def cost(self, capability: str, overrides: Optional[Dict[str, int]] = None) -> int:
        if overrides and capability in overrides:
            return int(overrides[capability])
        return int(self.costs.get(capability, 0))

    # discovery capabilities
    def search(self, capability: str, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        raise NotImplementedError

    # CONTACT_ENRICHMENT
    def enrich(self, data: WE.EnrichmentInput) -> WE.EnrichmentResult:
        raise NotImplementedError

    # PHONE_VALIDATION
    def validate_phone(self, number: str) -> Dict[str, Any]:
        raise NotImplementedError


def _in_query(rec: Dict, q: Dict) -> bool:
    def norm(s):
        return (s or "").strip().lower().replace(" county", "")
    states = [s.upper() for s in q.get("states") or []]
    if states and rec.get("state", "").upper() not in states:
        return False
    counties = [norm(c) for c in q.get("counties") or []]
    cities = [c.strip().lower() for c in q.get("cities") or []]
    zips = [z[:5] for z in q.get("zips") or []]
    checks = []
    if counties:
        checks.append(norm(rec.get("county")) in counties)
    if cities:
        checks.append((rec.get("city") or "").lower() in cities)
    if zips:
        checks.append((rec.get("zip_code") or "")[:5] in zips)
    return any(checks) if checks else True


class SandboxPropertyRecords(AcquisitionProvider):
    key = "sandbox_property_records"
    label = "Sandbox property records"
    connector_kind = C.SANDBOX
    capabilities = (C.PROPERTY_SEARCH, C.PARCEL, C.ASSESSOR, C.OWNERSHIP, C.VALUATION)
    coverage = "Synthetic Dallas + Tarrant County records"

    def search(self, capability, query):
        out = []
        for p in SB.PROPERTIES:
            if not _in_query(p, query):
                continue
            out.append({
                "source_reference": "PR-" + p["ref"],
                "street_address": p["street_address"], "city": p["city"], "state": p["state"],
                "zip_code": p["zip_code"], "county": p["county"], "parcel_apn": p["parcel_apn"],
                "latitude": p.get("lat"), "longitude": p.get("lng"),
                "property_type": p["property_type"], "bedrooms": p["bedrooms"],
                "bathrooms": p["bathrooms"], "square_feet": p["square_feet"],
                "year_built": p["year_built"],
                "valuation": {"value": p["value"], "mortgage": p["mortgage"],
                              "basis": "sandbox assessor + sandbox AVM"},
                "last_sale_date": p["last_sale"],
                "owner": {"name": p["owner"], "mailing_street": p["mailing"][0],
                          "mailing_city": p["mailing"][1], "mailing_state": p["mailing"][2],
                          "mailing_zip": p["mailing"][3]},
                "signals": [],
            })
        return out


class SandboxVacancy(AcquisitionProvider):
    key = "sandbox_vacancy"
    label = "Sandbox vacancy feed"
    connector_kind = C.SANDBOX
    capabilities = (C.VACANCY,)
    coverage = "Synthetic USPS-style vacancy flags"
    freshness_days = 30

    def search(self, capability, query):
        out = []
        for p in SB.PROPERTIES:
            if not p.get("vacancy") or not _in_query(p, query):
                continue
            fmt = SB.VACANCY_FORMAT.get(p["ref"], {})
            out.append({
                "source_reference": "VAC-" + p["ref"][-4:],
                "street_address": fmt.get("street_address", p["street_address"]),
                "city": fmt.get("city", p["city"]), "state": fmt.get("state", p["state"]),
                "zip_code": fmt.get("zip_code", p["zip_code"]), "county": p["county"],
                "signals": [{"type": "VACANT", "confidence": 80, "value": "vacant 90+ days",
                             "raw": "VACANCY_INDICATOR=Y;DAYS=97",
                             "observed_days_ago": 6}],
            })
        return out


class SandboxTaxRoll(AcquisitionProvider):
    key = "sandbox_tax_roll"
    label = "Sandbox tax roll"
    connector_kind = C.SANDBOX
    capabilities = (C.TAX,)
    coverage = "Synthetic county tax delinquency"
    freshness_days = 180

    def search(self, capability, query):
        out = []
        for p in SB.PROPERTIES:
            if not p.get("tax") or not _in_query(p, query):
                continue
            fmt = SB.TAX_FORMAT.get(p["ref"], {})
            out.append({
                "source_reference": "TAX-%s-%d" % (p["parcel_apn"], datetime.utcnow().year - 1),
                "street_address": fmt.get("street_address", p["street_address"]),
                "city": fmt.get("city", p["city"]), "state": fmt.get("state", p["state"]),
                "zip_code": fmt.get("zip_code", p["zip_code"]),
                "county": fmt.get("county", p["county"]),
                "parcel_apn": fmt.get("parcel_apn", p["parcel_apn"]),
                "signals": [{"type": "TAX_DELINQUENT", "confidence": 90,
                             "value": "%d tax year delinquent" % (datetime.utcnow().year - 1),
                             "raw": "DELQ_YEARS=1;AMOUNT_DUE=4812.36",
                             "observed_days_ago": 20}],
            })
        return out


class SandboxPublicRecords(AcquisitionProvider):
    key = "sandbox_public_records"
    label = "Sandbox probate / code / foreclosure"
    connector_kind = C.SANDBOX
    capabilities = (C.PROBATE, C.CODE_VIOLATION, C.FORECLOSURE)
    coverage = "Synthetic court and city filings"

    def search(self, capability, query):
        flag = {C.PROBATE: "probate", C.CODE_VIOLATION: "code_violation",
                C.FORECLOSURE: "pre_foreclosure"}[capability]
        stype = {C.PROBATE: "PROBATE", C.CODE_VIOLATION: "CODE_VIOLATION",
                 C.FORECLOSURE: "PRE_FORECLOSURE"}[capability]
        out = []
        for p in SB.PROPERTIES:
            if not p.get(flag) or not _in_query(p, query):
                continue
            out.append({
                "source_reference": "%s-%s" % (stype[:4], p["ref"]),
                "street_address": p["street_address"], "city": p["city"], "state": p["state"],
                "zip_code": p["zip_code"], "county": p["county"], "parcel_apn": p["parcel_apn"],
                "signals": [{"type": stype, "confidence": 85,
                             "value": {"PROBATE": "probate case filed",
                                       "CODE_VIOLATION": "open code case (overgrowth, structure)",
                                       "PRE_FORECLOSURE": "notice of default"}[stype],
                             "observed_days_ago": 45}],
            })
        return out


class SandboxSkipTrace(AcquisitionProvider):
    key = "sandbox_skiptrace"
    label = "Sandbox skip trace"
    connector_kind = C.SANDBOX
    capabilities = (C.CONTACT_ENRICHMENT,)
    costs = {C.CONTACT_ENRICHMENT: 18}
    coverage = "Synthetic owner contact data"

    def enrich(self, data):
        spec = SB.CONTACTS.get(data.owner_name or "")
        if spec and spec.get("behaviour") == "fail":
            raise ProviderTimeout("sandbox skip trace did not answer within 10s")
        if not spec or spec.get("behaviour") == "no_match":
            return WE.EnrichmentResult(status=WE.STATUS_NO_MATCH, provider=self.key,
                                       message="No contact on file for this owner.",
                                       billable=True, cost_cents=self.costs[C.CONTACT_ENRICHMENT])
        m = spec.get("mailing") or (None, None, None, None)
        res = WE.EnrichmentResult(
            status=WE.STATUS_SUCCEEDED, provider=self.key,
            phones=[WE.EnrichmentPhone(number=n, phone_type=t, confidence=c, source=s)
                    for n, t, c, s in spec.get("phones", [])],
            emails=list(spec.get("emails", [])), owner_name=data.owner_name,
            mailing_street=m[0], mailing_city=m[1], mailing_state=m[2], mailing_zip=m[3],
            confidence=max([c for _, _, c, _ in spec.get("phones", [])] or [0]),
            billable=True, cost_cents=self.costs[C.CONTACT_ENRICHMENT],
            raw_summary="SANDBOX skip trace result")
        if spec.get("person"):
            res.message = "person:%s|%s" % spec["person"]
        return res


class SandboxSkipTraceBackup(SandboxSkipTrace):
    key = "sandbox_skiptrace_backup"
    label = "Sandbox skip trace (fallback)"
    costs = {C.CONTACT_ENRICHMENT: 25}
    coverage = "Synthetic fallback — finds nobody the primary cannot"

    def enrich(self, data):
        spec = SB.CONTACTS.get(data.owner_name or "")
        if spec and spec.get("behaviour") == "fail":
            # The fallback answers, and honestly finds nothing: the review
            # example ends in WAITING FOR DATA rather than an invented number.
            return WE.EnrichmentResult(status=WE.STATUS_NO_MATCH, provider=self.key,
                                       message="No contact on file for this owner.",
                                       billable=True, cost_cents=self.costs[C.CONTACT_ENRICHMENT])
        return super().enrich(data)


class SandboxPhoneValidation(AcquisitionProvider):
    key = "sandbox_phone_validation"
    label = "Sandbox phone validation"
    connector_kind = C.SANDBOX
    capabilities = (C.PHONE_VALIDATION,)
    costs = {C.PHONE_VALIDATION: 1}
    coverage = "Synthetic line-type lookups"

    def validate_phone(self, number):
        line, valid = SB.PHONE_VALIDATION.get(number, ("mobile", "valid"))
        return {"line_type": line, "validation": valid, "carrier": "SANDBOX CARRIER"}


class ManualSource(AcquisitionProvider):
    key = "manual"
    label = "Manual entry"
    connector_kind = C.MANUAL
    capabilities = (C.PROPERTY_SEARCH, C.OWNERSHIP, C.VALUATION, C.CONTACT_ENRICHMENT,
                    C.VACANCY, C.TAX, C.PROBATE, C.CODE_VIOLATION, C.FORECLOSURE,
                    C.LISTING)      # a person may flag an expired / failed listing
    coverage = "Whatever a person types in"


class CsvImportSource(AcquisitionProvider):
    key = "csv_import"
    label = "CSV property import"
    connector_kind = C.IMPORT
    capabilities = (C.PROPERTY_SEARCH, C.OWNERSHIP, C.VALUATION)
    coverage = "A file you upload (fixed columns; see the report)"


PROVIDERS: Dict[str, AcquisitionProvider] = {p.key: p for p in (
    SandboxPropertyRecords(), SandboxVacancy(), SandboxTaxRoll(), SandboxPublicRecords(),
    SandboxSkipTrace(), SandboxSkipTraceBackup(), SandboxPhoneValidation(),
    ManualSource(), CsvImportSource(),
)}
SANDBOX_KEYS = tuple(k for k, p in PROVIDERS.items() if p.connector_kind == C.SANDBOX)

# Capabilities with a slot and no adapter of any kind beyond manual entry.
# Reported to the operator as exactly that.
INTERFACE_ONLY_CAPABILITIES = {
    C.LISTING: "No listing (MLS) connector. Expired / failed listing signals can be entered manually.",
    C.EMAIL_VALIDATION: "No email validation connector. Emails are shown as unverified.",
    C.ENTITY_RESOLUTION: "No LLC / entity resolution connector. LLC owners stay UNRESOLVED ENTITY.",
    C.COMPS: "Comps are entered in the existing Wholesale deal room after promotion.",
}


# ── Per-organization configuration + health ────────────────────────────────

def config(db, org_id: str, key: str):
    from app.models.evosense_models import EvoSenseProviderConfig
    row = (db.query(EvoSenseProviderConfig)
           .filter(EvoSenseProviderConfig.organization_id == org_id,
                   EvoSenseProviderConfig.provider_key == key).first())
    if row is None:
        p = PROVIDERS[key]
        # SANDBOX adapters are OFF for every organization until someone turns
        # them on, so sandbox data can never appear in a real customer's
        # discovery unannounced. Manual and import are always available.
        row = EvoSenseProviderConfig(
            organization_id=org_id, provider_key=key,
            enabled=p.connector_kind in (C.MANUAL, C.IMPORT),
            priority={"sandbox_skiptrace": 10, "sandbox_skiptrace_backup": 20}.get(key, 100))
        db.add(row)
        db.flush()
    return row


def health_state(p: AcquisitionProvider, cfg, when: Optional[datetime] = None) -> str:
    when = when or C.now()
    if p.connector_kind == C.INTERFACE_ONLY:
        return C.H_NOT_CONNECTED
    if p.required_env and not p.is_configured():
        return C.H_MISSING_CREDENTIALS
    if not cfg.enabled:
        return C.H_DISABLED
    if cfg.rate_limited_until and cfg.rate_limited_until > when:
        return C.H_RATE_LIMITED
    if cfg.degraded_until and cfg.degraded_until > when:
        return C.H_DEGRADED
    return C.H_CONNECTED


DEGRADE_AFTER = 3            # consecutive failures
DEGRADE_FOR = timedelta(minutes=15)


def record_success(cfg):
    cfg.calls_total = (cfg.calls_total or 0) + 1
    cfg.successes_total = (cfg.successes_total or 0) + 1
    cfg.consecutive_failures = 0
    cfg.last_success_at = C.now()
    cfg.degraded_until = None


def record_failure(cfg, reason: str, rate_limited_for: Optional[int] = None):
    cfg.calls_total = (cfg.calls_total or 0) + 1
    cfg.consecutive_failures = (cfg.consecutive_failures or 0) + 1
    cfg.last_failure_at = C.now()
    cfg.last_failure_reason = reason[:250]
    if rate_limited_for:
        cfg.rate_limited_until = C.now() + timedelta(seconds=rate_limited_for)
    if cfg.consecutive_failures >= DEGRADE_AFTER:
        cfg.degraded_until = C.now() + DEGRADE_FOR


def route(db, org_id: str, capability: str, *, exclude: tuple = (),
          sandbox_allowed: bool = True, preferences: Optional[List[str]] = None) -> List[tuple]:
    """Providers able to serve `capability` right now, best first.

    Deterministic order: strategy preference, configured priority, cost,
    historical success rate. No learned optimisation is claimed."""
    out = []
    for key, p in PROVIDERS.items():
        if capability not in p.capabilities or key in exclude:
            continue
        if p.connector_kind in (C.MANUAL, C.IMPORT):
            continue                      # a person, not something to call
        if p.connector_kind == C.SANDBOX and not sandbox_allowed:
            continue
        cfg = config(db, org_id, key)
        if health_state(p, cfg) != C.H_CONNECTED:
            continue
        overrides = C.jload(cfg.cost_overrides, {}) or {}
        rate = (cfg.successes_total or 0) / cfg.calls_total if cfg.calls_total else 1.0
        pref_rank = preferences.index(key) if preferences and key in preferences else 99
        out.append((pref_rank, cfg.priority, p.cost(capability, overrides), -rate, key, p, cfg))
    out.sort(key=lambda t: t[:5])
    return [(t[5], t[6], t[2]) for t in out]


def status_report(db, org_id: str) -> Dict[str, Any]:
    """What the provider settings screen shows. No credential ever appears."""
    rows = []
    for key, p in PROVIDERS.items():
        cfg = config(db, org_id, key)
        overrides = C.jload(cfg.cost_overrides, {}) or {}
        rows.append({
            "key": key, "label": p.label, "connector_kind": p.connector_kind,
            "connector_label": C.CONNECTOR_LABELS[p.connector_kind],
            "capabilities": list(p.capabilities), "coverage": p.coverage,
            "costs": {c: p.cost(c, overrides) for c in p.capabilities if p.cost(c, overrides)},
            "status": health_state(p, cfg), "enabled": bool(cfg.enabled),
            "priority": cfg.priority,
            "missing_env_count": len(p.missing_config()),
            "last_success_at": cfg.last_success_at.isoformat() + "Z" if cfg.last_success_at else None,
            "last_failure_at": cfg.last_failure_at.isoformat() + "Z" if cfg.last_failure_at else None,
            "last_failure_reason": cfg.last_failure_reason,
            "consecutive_failures": cfg.consecutive_failures,
            "degraded_until": cfg.degraded_until.isoformat() + "Z" if cfg.degraded_until else None,
            "calls_total": cfg.calls_total, "successes_total": cfg.successes_total,
            "freshness_days": p.freshness_days,
        })
    caps = []
    for cap in C.CAPABILITIES:
        serving = [r for r in rows if cap in r["capabilities"]]
        live = [r for r in serving if r["connector_kind"] == C.REAL and r["status"] == C.H_CONNECTED]
        sandbox = [r for r in serving if r["connector_kind"] == C.SANDBOX and r["enabled"]]
        manual = [r for r in serving if r["connector_kind"] in (C.MANUAL, C.IMPORT)]
        if live:
            kind = C.REAL
        elif sandbox:
            kind = C.SANDBOX
        elif manual:
            kind = manual[0]["connector_kind"]
        elif cap in INTERFACE_ONLY_CAPABILITIES:
            kind = C.INTERFACE_ONLY
        else:
            kind = C.NOT_BUILT
        caps.append({"capability": cap, "kind": kind, "label": C.CONNECTOR_LABELS[kind],
                     "providers": [r["label"] for r in serving],
                     "note": INTERFACE_ONLY_CAPABILITIES.get(cap)})
    return {"providers": rows, "capabilities": caps,
            "real_connectors": [r["key"] for r in rows if r["connector_kind"] == C.REAL]}
