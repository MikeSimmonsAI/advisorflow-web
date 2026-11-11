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
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

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
    # WHERE it can serve, so a capability is rated per market, not globally:
    #   scope   national | county | city | any (manual) | sandbox
    #   counties  the (Texas) counties it covers when scope is county/city
    #   scope_name  e.g. "City of Fort Worth" for a city-only source
    scope = "national"
    counties: tuple = ()
    scope_name = ""

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
        from app.services.evosense.sources.lgbs import wants_all_texas
        checks.append(norm(rec.get("county")) in counties
                      or (wants_all_texas(counties) and (rec.get("state") or "").upper() == "TX"))
    if cities:
        checks.append((rec.get("city") or "").lower() in cities)
    if zips:
        checks.append((rec.get("zip_code") or "")[:5] in zips)
    return any(checks) if checks else True


class SandboxPropertyRecords(AcquisitionProvider):
    scope = "sandbox"
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
    scope = "sandbox"
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
    scope = "sandbox"
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
    scope = "sandbox"
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
    scope = "sandbox"
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
    scope = "sandbox"
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
    scope = "any"
    key = "manual"
    label = "Manual entry"
    connector_kind = C.MANUAL
    capabilities = (C.PROPERTY_SEARCH, C.OWNERSHIP, C.VALUATION, C.CONTACT_ENRICHMENT,
                    C.VACANCY, C.TAX, C.PROBATE, C.CODE_VIOLATION, C.FORECLOSURE,
                    C.LISTING)      # a person may flag an expired / failed listing
    coverage = "Whatever a person types in"


class CsvImportSource(AcquisitionProvider):
    scope = "any"
    key = "csv_import"
    label = "CSV property import"
    connector_kind = C.IMPORT
    capabilities = (C.PROPERTY_SEARCH, C.OWNERSHIP, C.VALUATION)
    coverage = "A file you upload (fixed columns; see the report)"


# ── Real public-record sources (DFW) ────────────────────────────────────────
# Free, public, no account. OFF for every organization until an admin turns
# one on; HEALTHY only after a verified probe or run (see source_registry()).

class Records(list):
    """A list of ingest-shaped records that also carries the read's stats."""
    stats: Dict[str, Any] = {}
    source_url: Optional[str] = None


class PublicRecordSource(AcquisitionProvider):
    connector_kind = C.REAL
    discovery = False
    jurisdiction = ""
    source_type = ""
    access_method = ""
    public_url = ""
    refresh = ""
    terms_note = "Public record, published free by the government body for download or query."
    adapter_version = ""
    # Records saved by these older adapter versions lack a field the matching
    # now needs: they are looked up again even inside the freshness window.
    stale_adapter_versions: Tuple[str, ...] = ()
    lookup_capability: Optional[str] = None

    def applies(self, target: Dict[str, Any]) -> bool:
        return True

    def lookup_one(self, target: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def lookup_many(self, targets: List[Dict[str, Any]]) -> Dict[str, Any]:
        """{"results": {target id: record | None}, "errors": {id: str}, "stats": {}}.
        One target's failure is recorded against that target; a systemic
        failure (rate limit, auth, format change) stops the batch."""
        from app.services.evosense.sources import base as SB_
        out: Dict[str, Any] = {"results": {}, "errors": {}, "stats": {"calls": 0}}
        for t in targets:
            out["stats"]["calls"] += 1
            try:
                out["results"][t["id"]] = self.lookup_one(t)
            except SB_.SourceError as exc:
                out["errors"][t["id"]] = exc
                if exc.code in (SB_.RATE_LIMITED, SB_.AUTH_FAILED, SB_.SOURCE_FORMAT_CHANGED):
                    raise
        return out

    def verify(self) -> Dict[str, Any]:
        raise NotImplementedError


# Bulk files are read top to bottom; without rotation every daily hunt re-read
# the same first accounts (live: 334 of 500 were "seen" again). Each day starts
# a different window; the readers wrap to the top when a window runs past the end.
ROTATE_TARRANT_POOL = 9000     # delinquent residential accounts on the roll (approx.)
ROTATE_DCAD_POOL = 7000        # POOR / VERY POOR / UNDESIRABLE residences (approx.)
ROTATE_FW_OPEN_POOL = 1_000_000     # wrapped by the reader to the open-case count
ROTATE_DALLAS_TRW_POOL = 1_000_000  # wrapped again by the reader to the real pool size
ROTATE_TRAVIS_POOL = 1_000_000    # wrapped by the reader to the real pool
ROTATE_HCAD_POOL = 150_000      # absentee / estate-owned Harris houses (approx.); the reader wraps


def rotation_skip(window: int, pool: int, today: Optional[datetime] = None) -> int:
    day = (today or datetime.utcnow()).timetuple().tm_yday
    return (day * max(1, window)) % max(1, pool)


def _county(q: Dict[str, Any]) -> List[str]:
    return [re.sub(r"\s+county$", "", (c or "").strip().lower()) for c in q.get("counties") or []]


def _wants(q: Dict[str, Any], county: str) -> bool:
    from app.services.evosense.sources.lgbs import wants_all_texas
    counties = _county(q)
    states = [s.upper() for s in q.get("states") or []]
    if states and "TX" not in states:
        return False
    return not counties or county in counties or wants_all_texas(counties)


class TarrantTaxRollSource(PublicRecordSource):
    scope, counties = "county", ("Tarrant",)
    key = "tarrant_tax_roll"
    label = "Tarrant County tax roll (delinquency)"
    capabilities = (C.TAX,)
    discovery = True
    coverage = "Tarrant County, TX — every account on the county tax roll"
    jurisdiction = "Tarrant County, TX"
    source_type = "County tax assessor-collector roll"
    access_method = "Bulk fixed-width file (daily zip), read with HTTP byte ranges"
    public_url = "https://www.tarrantcountytx.gov/en/tax/property-tax/tarrant-county-tax-roll.html"
    refresh = "Published daily"
    freshness_days = 30
    adapter_version = "tarrant_tax_roll/3"

    def search(self, capability, query):
        from app.services.evosense.sources.tarrant import TarrantTaxRollReader
        out = Records()
        if not _wants(query, "tarrant"):
            out.stats = {"skipped": "strategy does not include Tarrant County"}
            return out
        limit = int(query.get("limit") or 25)
        res = TarrantTaxRollReader().discover(limit=limit,
                                              scan_limit=int(query.get("scan_limit") or 800_000),
                                              min_due=float(query.get("min_tax_due") or 250),
                                              skip=rotation_skip(limit, ROTATE_TARRANT_POOL))
        out.extend(res["records"])
        out.stats, out.source_url = res["stats"], res["source_url"]
        return out

    def verify(self):
        from app.services.evosense.sources import base as SB_
        from app.services.evosense.sources.tarrant import TarrantTaxRollReader
        url = TarrantTaxRollReader().locate()
        oz = SB_.open_zip(self.key, url)
        try:
            names = oz.zip.namelist()
            if "Master.dat" not in names:
                raise SB_.SourceError(SB_.SOURCE_FORMAT_CHANGED, "Master.dat missing from the tax roll")
            first = next(SB_.text_lines(oz.zip, "Master.dat"))
            from app.services.evosense.sources.tarrant import parse_master
            parse_master(first)
        finally:
            oz.close()
        return {"url": url, "members": names, "mode": oz.meta.get("mode"), "size": oz.meta.get("size")}


def _categories(query) -> tuple:
    """The property categories this hunt's strategy asks for (houses only ->
    residential), so a widened county reader never imports a type nobody hunts."""
    from app.services.evosense import property_class as PCL
    return PCL.query_categories(query or {})


class DallasTrwSource(PublicRecordSource):
    scope, counties = "county", ("Dallas",)
    key = "dallas_trw"
    label = "Dallas County tax roll (TRW delinquency file)"
    capabilities = (C.TAX,)
    discovery = True
    coverage = "Dallas County, TX - every account the county Tax Office collects for"
    jurisdiction = "Dallas County, TX"
    source_type = "County tax assessor-collector roll"
    access_method = "Bulk fixed-width file (weekly zip, free), read with HTTP byte ranges"
    public_url = "https://www.dallascounty.org/departments/tax/tax-roll.php"
    refresh = "Published weekly"
    freshness_days = 30
    adapter_version = "dallas_trw/3"

    def search(self, capability, query):
        from app.services.evosense.sources.dallas_trw import TrwReader
        out = Records()
        if not _wants(query, "dallas"):
            out.stats = {"skipped": "strategy does not include Dallas County"}
            return out
        limit = int(query.get("limit") or 25)
        # The reader adds up the whole county block, so the window can move
        # through every delinquent account: day-of-year x window, wrapped there.
        res = TrwReader().discover(limit=limit,
                                   min_due_cents=int(float(query.get("min_tax_due") or 250) * 100),
                                   skip=rotation_skip(limit, ROTATE_DALLAS_TRW_POOL),
                                   categories=_categories(query))
        out.extend(res["records"])
        out.stats, out.source_url = res["stats"], res["source_url"]
        return out

    def verify(self):
        from app.services.evosense.sources import base as SB_
        from app.services.evosense.sources.dallas_trw import TrwReader, parse, COUNTY_UNIT
        url = TrwReader().locate()
        oz = SB_.open_zip(self.key, url, allow_download=False)
        try:
            names = oz.zip.namelist()
            member = next((n for n in names if "flat404" in n.lower()), None)
            if member is None:
                raise SB_.SourceError(SB_.SOURCE_FORMAT_CHANGED, "flat404 member missing from the TRW zip")
            first = parse(next(SB_.text_lines(oz.zip, member)))
            if not first["jurisdiction"].isdigit() or not first["year"].isdigit():
                raise SB_.SourceError(SB_.SOURCE_FORMAT_CHANGED, "TRW line does not match layout TRW-02")
        finally:
            oz.close()
        return {"url": url, "members": names, "mode": oz.meta.get("mode"), "size": oz.meta.get("size"),
                "county_unit": COUNTY_UNIT}


class TadSource(PublicRecordSource):
    scope, counties = "county", ("Tarrant",)
    key = "tad"
    label = "Tarrant Appraisal District (TAD) property data"
    capabilities = (C.ASSESSOR, C.OWNERSHIP)
    lookup_capability = C.ASSESSOR
    coverage = "Tarrant County, TX — owner of record, situs, appraised value, building facts"
    jurisdiction = "Tarrant County, TX"
    source_type = "Appraisal district export"
    access_method = "Bulk pipe-delimited file (zip), read with HTTP byte ranges"
    public_url = "https://www.tad.org/resources/data-downloads"
    refresh = "Published weekly (appraisal year)"
    freshness_days = 120
    adapter_version = "tad_property_data/2"

    def applies(self, target):
        if (target.get("county") or "").lower() != "tarrant":
            return False
        # No account number: only a Fort Worth house, matched by its exact situs address.
        return bool(target.get("parcel_apn")) or (
            bool(target.get("street_address")) and (target.get("city") or "").lower() == "fort worth")

    def lookup_many(self, targets):
        from app.services.evosense.sources.tarrant import TadReader, apn, _norm_addr
        by_apn = {apn(t["parcel_apn"]): t["id"] for t in targets if t.get("parcel_apn")}
        by_addr = {_norm_addr(t["street_address"]): t["id"] for t in targets
                   if not t.get("parcel_apn") and t.get("street_address")}
        res = TadReader().lookup_many(list(by_apn), addresses=list(by_addr))
        results = {tid: res["records"].get(a) for a, tid in by_apn.items()}
        results.update({tid: (res.get("by_address") or {}).get(a) for a, tid in by_addr.items()})
        return {"results": results, "errors": {}, "stats": res["stats"]}

    def verify(self):
        from app.services.evosense.sources import base as SB_
        from app.services.evosense.sources.tarrant import TAD_REQUIRED, TAD_URL
        oz = SB_.open_zip(self.key, TAD_URL)
        try:
            member = next(n for n in oz.zip.namelist() if n.lower().endswith(".txt"))
            header = next(SB_.text_lines(oz.zip, member)).split("|")
        finally:
            oz.close()
        missing = [c for c in TAD_REQUIRED if c not in header]
        if missing:
            raise SB_.SourceError(SB_.SOURCE_FORMAT_CHANGED, "TAD file lacks %s" % ", ".join(missing))
        return {"url": TAD_URL, "columns": len(header), "mode": oz.meta.get("mode")}


class DcadSource(PublicRecordSource):
    scope, counties = "county", ("Dallas",)
    key = "dcad"
    label = "Dallas Central Appraisal District (DCAD) certified export"
    capabilities = (C.PROPERTY_SEARCH, C.ASSESSOR, C.OWNERSHIP)
    discovery = True
    coverage = "Dallas County, TX — owner of record, situs, value, appraiser condition rating"
    jurisdiction = "Dallas County, TX"
    source_type = "Appraisal district export"
    access_method = "Bulk CSV archive (~200 MB zip), downloaded to a size-capped cache"
    public_url = "https://www.dallascad.org/DataProducts.aspx"
    refresh = "Certified roll, refreshed by DCAD through the year"
    freshness_days = 180
    adapter_version = "dcad_certified/3"
    lookup_capability = C.ASSESSOR

    def applies(self, target):
        # Lookup by account for a Dallas property another source found; with no
        # account (a clerk posting, an imported list), by its exact situs address.
        if (target.get("county") or "").lower() != "dallas":
            return False
        return bool(target.get("parcel_apn")) or bool(target.get("street_address"))

    def lookup_many(self, targets):
        from app.services.evosense.sources.dallas import DcadReader
        reader = DcadReader()
        by_acct = {(t["parcel_apn"] or "").strip(): t["id"] for t in targets if t.get("parcel_apn")}
        need = {t["id"]: dict(t) for t in targets if not t.get("parcel_apn") and t.get("street_address")}
        stats = {}
        if need:
            found = reader.accounts_for_addresses(need)
            stats["address"] = found["stats"]
            for tid, acct in found["accounts"].items():
                by_acct.setdefault(acct, tid)
        res = reader.lookup_many(list(by_acct))
        stats.update(res["stats"])
        results = {tid: res["records"].get(a) for a, tid in by_acct.items()}
        results.update({tid: None for tid in need if tid not in results})
        return {"results": results, "errors": {}, "stats": stats}

    def search(self, capability, query):
        from app.services.evosense.sources.dallas import DcadReader
        out = Records()
        if capability != C.PROPERTY_SEARCH or not _wants(query, "dallas"):
            out.stats = {"skipped": "strategy does not include Dallas County"}
            return out
        limit = int(query.get("limit") or 25)
        res = DcadReader().discover(limit=limit, skip=rotation_skip(limit * 3, ROTATE_DCAD_POOL),
                                    categories=_categories(query))
        out.extend(res["records"])
        out.stats, out.source_url = res["stats"], res["source_url"]
        return out

    def verify(self):
        from app.services.evosense.sources import base as SB_
        from app.services.evosense.sources.dallas import DcadReader
        url = DcadReader().locate()
        if SB_.local_override(self.key):
            return {"url": url, "mode": "local_file"}
        info = SB_._probe(url)
        if not info.get("size"):
            raise SB_.SourceError(SB_.DOWNLOAD_FAILED, "DCAD export did not report a size")
        return {"url": url, "size": info["size"], "ranged": info["ranged"]}


class CollinCadSource(PublicRecordSource):
    scope, counties = "county", ("Collin",)
    discovery_family = "OWNER_ROLLS"   # appraisal rolls that find absentee / estate-owned houses
    key = "collin_cad"
    label = "Collin Central Appraisal District (Collin CAD) appraisal roll"
    capabilities = (C.PROPERTY_SEARCH, C.ASSESSOR, C.OWNERSHIP)
    discovery = True
    coverage = ("Collin County, TX - owner of record and mailing address, situs, category, "
                "tax values, homestead, deed date, year built, living area (no beds / baths)")
    jurisdiction = "Collin County, TX"
    source_type = "Appraisal district roll on the State of Texas open-data portal"
    access_method = "Official Socrata API on data.texas.gov (SoQL queries, paged)"
    public_url = "https://collincad.org/open-data-portal/"
    refresh = "Republished by the district on data.texas.gov (preliminary, then certified)"
    freshness_days = 120
    adapter_version = "collin_cad_socrata/1"
    lookup_capability = C.ASSESSOR
    terms_note = ("Published by Collin CAD on the State of Texas open-data portal for public use; "
                  "queried through the portal's own API.")

    def applies(self, target):
        return (target.get("county") or "").lower() == "collin" and bool(target.get("parcel_apn"))

    def lookup_many(self, targets):
        from app.services.evosense.sources.collin import CollinReader
        by_acct = {(t["parcel_apn"] or "").strip(): t["id"] for t in targets if t.get("parcel_apn")}
        res = CollinReader().lookup_many(list(by_acct))
        return {"results": {tid: res["records"].get(a) for a, tid in by_acct.items()},
                "errors": {}, "stats": res["stats"]}

    def search(self, capability, query):
        from app.services.evosense.sources.collin import CollinReader
        out = Records()
        if capability != C.PROPERTY_SEARCH or not _wants(query, "collin"):
            out.stats = {"skipped": "strategy does not include Collin County"}
            return out
        types = [t for t in query.get("property_types") or []]
        houses = bool(query.get("houses_only")) or (bool(types) and "land" not in types
                                                    and "commercial" not in types)
        res = CollinReader().discover(
            limit=int(query.get("limit") or 25),
            min_years=int(query.get("min_ownership_years") or 10),
            houses_only=houses or not types,
            cities=query.get("cities") or None, zips=query.get("zips") or None)
        out.extend(res["records"])
        out.stats, out.source_url = res["stats"], res["source_url"]
        return out

    def verify(self):
        from app.services.evosense.sources.collin import CollinReader
        r = CollinReader()
        return {"dataset": r.dataset(), "rows": r.count()}


class DentonGisSource(PublicRecordSource):
    scope, counties = "county", ("Denton",)
    discovery_family = "OWNER_ROLLS"   # appraisal rolls that find absentee / estate-owned houses
    key = "denton_gis"
    label = "Denton County GIS parcel layer (Denton CAD roll fields)"
    capabilities = (C.PROPERTY_SEARCH, C.ASSESSOR, C.OWNERSHIP)
    discovery = True
    coverage = ("Denton County, TX - owner of record and mailing address, situs, category, certified "
                "value, homestead, year built, living area; deed YEAR from the instrument number "
                "(no deed date, no beds / baths)")
    jurisdiction = "Denton County, TX"
    source_type = "County public GIS map service"
    access_method = "ArcGIS REST query on gis.dentoncounty.gov (one property, or one page of <=1,000)"
    public_url = "https://gis.dentoncounty.gov/arcgis/rest/services/CAD/MapServer/0"
    refresh = "Maintained by Denton County GIS from the appraisal roll"
    freshness_days = 120
    adapter_version = "denton_county_gis/1"
    lookup_capability = C.ASSESSOR
    terms_note = ("Public county map service, no account. The Denton CAD website itself says its property "
                  "search is not for bulk transfer, so EvoSense never uses that site and never mirrors the "
                  "layer: it asks small questions (one property, or one page per hunt).")

    def applies(self, target):
        return (target.get("county") or "").lower() == "denton" and bool(target.get("parcel_apn"))

    def lookup_many(self, targets):
        from app.services.evosense.sources.denton import DentonReader
        by_acct = {re.sub(r"\D", "", t["parcel_apn"] or ""): t["id"] for t in targets if t.get("parcel_apn")}
        by_acct.pop("", None)
        res = DentonReader().lookup_many(list(by_acct))
        return {"results": {tid: res["records"].get(a) for a, tid in by_acct.items()},
                "errors": {}, "stats": res["stats"]}

    def search(self, capability, query):
        from app.services.evosense.sources.denton import DentonReader
        out = Records()
        if capability != C.PROPERTY_SEARCH or not _wants(query, "denton"):
            out.stats = {"skipped": "strategy does not include Denton County"}
            return out
        types = [t for t in query.get("property_types") or []]
        houses = bool(query.get("houses_only")) or not types or ("land" not in types and "commercial" not in types)
        res = DentonReader().discover(limit=int(query.get("limit") or 25), houses_only=houses,
                                      min_years=int(query.get("min_ownership_years") or 10),
                                      cities=query.get("cities") or None, zips=query.get("zips") or None)
        out.extend(res["records"])
        out.stats, out.source_url = res["stats"], res["source_url"]
        return out

    def verify(self):
        from app.services.evosense.sources.denton import DentonReader
        return {"layer": "Denton County GIS CAD/0", "rows": DentonReader().count()}


class CensusGeocoderSource(PublicRecordSource):
    key = "census_geocoder"
    label = "U.S. Census geocoder"
    capabilities = (C.GEOCODING,)
    lookup_capability = C.GEOCODING
    coverage = "United States — ZIP, coordinates, county check"
    jurisdiction = "United States"
    source_type = "Federal geocoding API"
    access_method = "Public REST API, one address per call"
    public_url = "https://geocoding.geo.census.gov/geocoder/"
    refresh = "Census address ranges (current benchmark)"
    freshness_days = 365
    adapter_version = "census_geocoder/1"

    def applies(self, target):
        # The geocoder needs a city (or ZIP); without one it answers 400.
        return bool(target.get("street_address")) and bool(target.get("city")) and not target.get("zip_code")

    def lookup_one(self, target):
        from app.services.evosense.sources.census import CensusGeocoder
        g = CensusGeocoder()
        res = g.geocode(target["street_address"], target.get("city"), target.get("state") or "TX",
                        target.get("zip_code"), target.get("county"))
        return g.to_record(target, res)

    def verify(self):
        from app.services.evosense.sources.census import CensusGeocoder
        res = CensusGeocoder().geocode("100 E Weatherford St", "Fort Worth", "TX", None, "Tarrant")
        return {"matches": res["matches"]}


class TexasParcelOwnerSource(PublicRecordSource):
    """Owner of record anywhere in Texas from the state's assembled parcel
    layer, for properties no county-specific source can look up (any county
    outside DFW; Tarrant outside Fort Worth and Collin / Denton without an
    account number). See sources/txgio.py for the strict address check."""
    scope, counties, scope_name = "state", (), "Texas"
    key = "txgio_parcels"
    label = "Texas statewide parcels - owner of record (TxGIO StratMap)"
    capabilities = (C.ASSESSOR, C.OWNERSHIP)
    lookup_capability = C.ASSESSOR
    coverage = ("Texas, all 254 counties - owner of record and mailing address from each appraisal "
                "district's roll, assembled by the Texas Geographic Information Office")
    jurisdiction = "Texas (statewide)"
    source_type = "State GIS office - statewide appraisal-district parcels"
    access_method = "Census geocode, then the parcel at that point (public ArcGIS identify), two calls per address"
    public_url = "https://geographic.texas.gov/"
    refresh = "Refreshed by TxGIO from each county's certified roll (tax year shown on each record)"
    freshness_days = 180
    adapter_version = "txgio_stratmap_parcels/3"
    # /1 did not keep LEGAL_DESC, which the foreclosure matcher reads (lot/block);
    # /2 did not keep the roll's tax values and year built (free, same answer)
    stale_adapter_versions = ("txgio_stratmap_parcels/1", "txgio_stratmap_parcels/2")
    terms_note = "Public statewide parcel data published by the Texas Geographic Information Office."

    @staticmethod
    def _county_source_covers(t) -> bool:
        c = (t.get("county") or "").strip().lower().replace(" county", "")
        apn = bool(t.get("parcel_apn"))
        if c == "dallas":
            return True                                   # DCAD: by account or by address
        if c == "tarrant":
            return apn or (t.get("city") or "").strip().lower() == "fort worth"
        if c in ("collin", "denton"):
            return apn
        return False

    def applies(self, target):
        return ((target.get("state") or "TX").upper() == "TX" and bool(target.get("street_address"))
                and bool(target.get("city") or target.get("zip_code")) and not self._county_source_covers(target))

    def lookup_one(self, target):
        from app.services.evosense.sources.txgio import TxParcelReader
        return TxParcelReader().lookup(target)

    def verify(self):
        from app.services.evosense.sources.txgio import TxParcelReader
        rows = TxParcelReader().identify(-95.3980, 29.7900)
        return {"parcels_at_test_point": len(rows)}


class JohnsonCadSource(PublicRecordSource):
    """Johnson County's own appraisal roll (JCAD certified records, free download).
    Adds what the statewide layer lacks for Johnson: the legal description
    (subdivision / block / lot) the clerk indexes foreclosure notices by, the
    mailing address, and the last deed's recording date. See sources/johnson.py."""
    scope, counties = "county", ("Johnson",)
    key = "johnson_cad"
    label = "Johnson County appraisal roll (JCAD certified records)"
    capabilities = (C.ASSESSOR, C.OWNERSHIP, C.PARCEL)
    lookup_capability = C.ASSESSOR
    coverage = "Johnson County, TX - owner, mailing address, legal description, deed date (no sale prices)"
    jurisdiction = "Johnson County, Texas"
    source_type = "County appraisal district - certified roll"
    access_method = "Published zip read with byte ranges once, indexed on disk, then looked up locally"
    public_url = "https://johnsoncad.com/downloads"
    refresh = "Certified each August; supplements during the year"
    freshness_days = 120
    adapter_version = "johnson_cad_roll/1"
    terms_note = "Public appraisal roll, published free by the Central Appraisal District of Johnson County."

    def applies(self, target):
        c = (target.get("county") or "").strip().lower().replace(" county", "")
        return c == "johnson" and bool(target.get("street_address") or target.get("parcel_apn"))

    def lookup_one(self, target):
        from app.services.evosense.sources.johnson import JohnsonReader
        return JohnsonReader().lookup(target)

    def verify(self):
        from app.services.evosense.sources.johnson import JohnsonReader
        return {"accounts": JohnsonReader().count()}


class TexasParcelRollSource(PublicRecordSource):
    """Find houses anywhere in Texas from each county's 2026 appraisal roll as
    published by TxGIO (sources/txgio_roll.py). Absentee- or estate-owned
    residences only; the engine scores them like any appraisal-roll record.
    Dallas, Collin and Denton have their own live discovery and are skipped."""
    scope, counties, scope_name = "state", (), "Texas"
    discovery_family = "OWNER_ROLLS"   # appraisal rolls that find absentee / estate-owned houses
    key = "txgio_roll"
    label = "Texas statewide appraisal rolls - find houses (TxGIO 2026 parcels)"
    capabilities = (C.PROPERTY_SEARCH,)
    discovery = True
    coverage = ("Texas, all 254 counties - each appraisal district's 2026 roll: absentee- or estate-owned "
                "houses with owner of record, mailing address and tax value. A few counties per hunt.")
    jurisdiction = "Texas (statewide)"
    source_type = "State GIS office - county appraisal rolls (2026 land parcels)"
    access_method = "One county file per hunt slot; only its attribute table is streamed (HTTP byte ranges)"
    public_url = "https://geographic.texas.gov/stratmap/land-parcels.html"
    refresh = "TxGIO 2026 collection (acquired 2026-09-25); each record carries its district's tax year"
    freshness_days = 180
    adapter_version = "txgio_parcel_roll/2"
    OWN_DISCOVERY = ("Dallas", "Collin", "Denton", "Harris")
    COUNTIES_PER_HUNT = 2
    terms_note = "Public statewide parcel data published by the Texas Geographic Information Office."

    def _targets(self, query) -> List[str]:
        from app.services.evosense.sources.lgbs import wants_all_texas
        from app.services.evosense.sources.txgio_roll import TEXAS_COUNTIES, county_name
        named = _county(query)
        if not named or wants_all_texas(named):
            pool = list(TEXAS_COUNTIES) if named else []
        else:
            pool = [c for c in (county_name(n) for n in named) if c]
        return [c for c in pool if c not in self.OWN_DISCOVERY]

    def search(self, capability, query):
        from app.services.evosense.sources.txgio_roll import TxRollReader
        out = Records()
        states = [s.upper() for s in query.get("states") or []]
        if capability != C.PROPERTY_SEARCH or (states and "TX" not in states):
            out.stats = {"skipped": "not a Texas property search"}
            return out
        pool = self._targets(query)
        if not pool:
            out.stats = {"skipped": "no county without its own discovery source in this strategy"}
            return out
        day = int(query.get("rotate") if query.get("rotate") is not None else datetime.utcnow().timetuple().tm_yday)
        k = min(self.COUNTIES_PER_HUNT, len(pool))
        start = (day * k) % len(pool)
        picks = [pool[(start + i) % len(pool)] for i in range(k)]
        limit = int(query.get("limit") or 25)
        per = max(1, limit // k)
        stats: Dict[str, Any] = {"counties": picks, "pool": len(pool), "counties_checked": ", ".join(picks)}
        reader = TxRollReader()
        for c in picks:
            res = reader.discover(c, limit=per, rotate=(day // max(1, len(pool) // k)) * per,
                                  cities=query.get("cities"), zips=query.get("zips"),
                                  categories=_categories(query))
            out.extend(res["records"])
            stats[c] = res["stats"]
            for key, v in res["stats"].items():     # the per-type counts, summed for the run record
                if key.startswith(("found_", "kept_", "imported_")) and isinstance(v, int):
                    stats[key] = stats.get(key, 0) + v
            out.source_url = res["source_url"]
        out.stats = stats
        return out

    def verify(self):
        from app.services.evosense.sources import base as SB_
        from app.services.evosense.sources.txgio_roll import county_url
        info = SB_._probe(county_url("King"))
        if not info.get("ranged") or not info.get("size"):
            raise SB_.SourceError(SB_.DOWNLOAD_FAILED, "TxGIO parcel files do not answer byte ranges")
        return {"king_county_bytes": info["size"]}


class HarrisRollSource(PublicRecordSource):
    """Find houses in Harris County from HCAD's own public data file
    (sources/hcad.py): absentee- or estate-owned residences, same rule as the
    statewide rolls. A different window of the file each day."""
    scope, counties = "county", ("Harris",)
    discovery_family = "OWNER_ROLLS"   # appraisal rolls that find absentee / estate-owned houses
    key = "hcad_roll"
    label = "Harris County appraisal roll - find houses (HCAD public data)"
    capabilities = (C.PROPERTY_SEARCH,)
    discovery = True
    coverage = ("Harris County, TX - HCAD's current appraisal roll: absentee- or estate-owned houses with "
                "owner of record, mailing address, tax value and year built.")
    jurisdiction = "Harris County, TX"
    source_type = "Appraisal district public data file"
    access_method = "HCAD Real_acct_owner.zip from download.hcad.org; only real_acct.txt is read"
    public_url = "https://hcad.org/pdata/pdata-property-downloads.html"
    refresh = "Published by HCAD (preliminary, then certified values) for the current tax year"
    freshness_days = 120
    adapter_version = "hcad_real_acct/1"
    terms_note = "HCAD publishes these files on its website for public download."

    def search(self, capability, query):
        from app.services.evosense.sources.hcad import HcadRollReader
        out = Records()
        if capability != C.PROPERTY_SEARCH or not _wants(query, "harris"):
            out.stats = {"skipped": "strategy does not include Harris County"}
            return out
        limit = int(query.get("limit") or 25)
        skip = (int(query["rotate"]) * limit) if query.get("rotate") is not None else \
            rotation_skip(limit, ROTATE_HCAD_POOL)
        res = HcadRollReader().discover(limit=limit, rotate=skip,
                                        cities=query.get("cities"), zips=query.get("zips"))
        out.extend(res["records"])
        out.stats, out.source_url = res["stats"], res["source_url"]
        return out

    def verify(self):
        from app.services.evosense.sources import base as SB_
        from app.services.evosense.sources.hcad import HcadRollReader
        url = HcadRollReader().url()
        info = SB_._probe(url)
        if not info.get("size"):
            raise SB_.SourceError(SB_.DOWNLOAD_FAILED, "HCAD file did not report a size")
        return {"url": url, "bytes": info["size"], "byte_ranges": bool(info.get("ranged"))}


class TravisDelinquentSource(PublicRecordSource):
    """Travis County delinquent-tax houses from the county Tax Office's own
    published file (sources/travis_delinquent.py): single-family (A1),
    past-due balance, not in bankruptcy; a tax lawsuit when a cause number
    is on file."""
    scope, counties = "county", ("Travis",)
    key = "travis_delinquent"
    label = "Travis County delinquent taxes (Tax Office file)"
    capabilities = (C.TAX,)
    discovery = True
    coverage = ("Travis County, TX - every account the Tax Office lists as delinquent: houses (A1) with a "
                "past-due balance, owner and mailing address, appraised value, lawsuit cause number")
    jurisdiction = "Travis County, TX"
    source_type = "County tax assessor-collector delinquent file"
    access_method = "Published CSV on tax-office.traviscountytx.gov (about 2 MB, whole file per hunt)"
    public_url = "https://tax-office.traviscountytx.gov/about-us/reports-data/property-taxes"
    refresh = "Republished by the Tax Office (file dated in its Last-Modified header)"
    freshness_days = 30
    adapter_version = "travis_delinquent/3"
    terms_note = "Open data the Travis County Tax Office publishes for download."

    def search(self, capability, query):
        from app.services.evosense.sources.travis_delinquent import TravisDelinquentReader
        out = Records()
        if capability != C.TAX or not _wants(query, "travis"):
            out.stats = {"skipped": "strategy does not include Travis County"}
            return out
        limit = int(query.get("limit") or 25)
        res = TravisDelinquentReader().discover(
            limit=limit, min_due=float(query.get("min_tax_due") or 250),
            skip=rotation_skip(limit, ROTATE_TRAVIS_POOL), zips=query.get("zips"),
            categories=_categories(query))
        out.extend(res["records"])
        out.stats, out.source_url = res["stats"], res["source_url"]
        return out

    def verify(self):
        from app.services.evosense.sources import base as SB_
        from app.services.evosense.sources.travis_delinquent import FILE, rows_from_text
        rows = rows_from_text(SB_.get_text(FILE, max_bytes=20 * 1024 * 1024))
        return {"rows": len(rows)}


class FortWorthOpenCasesSource(PublicRecordSource):
    scope, counties, scope_name = "city", ("Tarrant",), "City of Fort Worth"
    key = "fw_open_cases"
    label = "Fort Worth open code cases (substandard, neglect, high grass)"
    capabilities = (C.CODE_VIOLATION,)
    discovery = True
    coverage = "City of Fort Worth, TX - open city code cases of distress types"
    jurisdiction = "City of Fort Worth, TX"
    source_type = "City code-enforcement case table (open data)"
    access_method = "Public ArcGIS feature service query"
    public_url = "https://data.fortworthtexas.gov/"
    refresh = "Updated daily by the city"
    freshness_days = 30
    adapter_version = "fw_open_cases/1"

    def search(self, capability, query):
        from app.services.evosense.sources import fortworth as FW
        out = Records()
        if not _wants(query, "tarrant"):
            out.stats = {"skipped": "strategy does not include Tarrant County (Fort Worth)"}
            return out
        limit = int(query.get("limit") or 25)
        res = FW.discover(limit, skip=rotation_skip(limit, ROTATE_FW_OPEN_POOL))
        out.extend(res["records"])
        out.stats, out.source_url = res["stats"], res["source_url"]
        return out

    def verify(self):
        from app.services.evosense.sources import fortworth as FW
        res = FW.discover(1)
        return {"url": FW.LAYER, "open_cases": res["stats"].get("open_cases")}


class FortWorthCodeSource(PublicRecordSource):
    scope, counties, scope_name = "city", ("Tarrant",), "City of Fort Worth"
    key = "fw_code_violations"
    label = "City of Fort Worth code violations"
    capabilities = (C.CODE_VIOLATION,)
    lookup_capability = C.CODE_VIOLATION
    coverage = "City of Fort Worth — code cases opened by the city"
    jurisdiction = "City of Fort Worth, TX"
    source_type = "City open data (ArcGIS feature service)"
    access_method = "Public ArcGIS REST query, one address per call"
    public_url = "https://data.fortworthtexas.gov/"
    refresh = "Updated by the city (near daily)"
    freshness_days = 30
    adapter_version = "fw_code_violations/2"

    def applies(self, target):
        return (target.get("city") or "").strip().lower() == "fort worth" and bool(target.get("street_address"))

    def lookup_one(self, target):
        from app.services.evosense.sources.fortworth import FortWorthCodeReader
        r = FortWorthCodeReader()
        return r.to_record(target, r.lookup(target["street_address"])["cases"])

    def verify(self):
        from app.services.evosense.sources import base as SB_
        from app.services.evosense.sources.fortworth import LAYER
        meta = SB_.get_json(LAYER, {"f": "json"})
        names = [f.get("name") for f in (meta or {}).get("fields", [])]
        if "Violation_Address" not in names:
            raise SB_.SourceError(SB_.SOURCE_FORMAT_CHANGED, "Violation_Address field missing")
        return {"fields": len(names)}


class Dallas311Source(PublicRecordSource):
    scope, counties, scope_name = "city", ("Dallas",), "City of Dallas"
    key = "dallas_311_code"
    label = "City of Dallas 311 — Code Compliance requests"
    capabilities = (C.CODE_VIOLATION,)
    lookup_capability = C.CODE_VIOLATION
    coverage = "City of Dallas — resident service requests routed to Code Compliance"
    jurisdiction = "City of Dallas, TX"
    source_type = "City open data (Socrata)"
    access_method = "Public Socrata SODA query, one address per call"
    public_url = "https://www.dallasopendata.com/d/d7e7-envw"
    refresh = "Updated by the city (daily)"
    freshness_days = 30
    adapter_version = "dallas_311/2"

    def applies(self, target):
        return (target.get("city") or "").strip().lower() == "dallas" and bool(target.get("street_address"))

    def lookup_one(self, target):
        from app.services.evosense.sources.dallas import Dallas311Reader
        r = Dallas311Reader()
        return r.to_record(target, r.lookup(target["street_address"])["cases"])

    def verify(self):
        from app.services.evosense.sources import base as SB_
        from app.services.evosense.sources.dallas import DALLAS_311
        rows = SB_.get_json(DALLAS_311, {"$limit": 1, "department": "Code Compliance"})
        if not isinstance(rows, list):
            raise SB_.SourceError(SB_.SOURCE_FORMAT_CHANGED, "Dallas 311 did not return a list")
        return {"rows": len(rows)}


class AustinCodeSource(PublicRecordSource):
    scope, counties, scope_name = "city", ("Travis",), "City of Austin"
    key = "austin_code"
    label = "City of Austin — Austin Code complaint cases"
    capabilities = (C.CODE_VIOLATION,)
    lookup_capability = C.CODE_VIOLATION
    coverage = "City of Austin — code complaint cases worked by Austin Code inspectors (last 2 years)"
    jurisdiction = "City of Austin, TX"
    source_type = "City open data (Socrata)"
    access_method = "Public Socrata SODA query, one address (house number + ZIP) per call"
    public_url = "https://data.austintexas.gov/d/6wtj-zbtb"
    refresh = "Updated by the city (continuously)"
    freshness_days = 30
    adapter_version = "austin_code/1"

    def applies(self, target):
        county = (target.get("county") or "").strip().lower().replace(" county", "")
        city = (target.get("city") or "").strip().lower()
        return bool(target.get("street_address")) and (city == "austin" or (county == "travis" and not city))

    def lookup_one(self, target):
        from app.services.evosense.sources.austin import AustinCodeReader
        r = AustinCodeReader()
        return r.to_record(target, r.lookup(target["street_address"], target.get("zip_code")))

    def verify(self):
        from app.services.evosense.sources import base as SB_
        from app.services.evosense.sources.austin import API
        rows = SB_.get_json(API, {"$limit": 1, "$order": "opened_date DESC"})
        if not isinstance(rows, list) or not rows or "case_id" not in rows[0]:
            raise SB_.SourceError(SB_.SOURCE_FORMAT_CHANGED, "Austin code cases did not return case rows")
        return {"newest_case_opened": (rows[0].get("opened_date") or "")[:10]}


class LgbsTaxSaleSource(PublicRecordSource):
    scope, counties, scope_name = "state", (), "Texas"
    key = "lgbs_tax_sales"
    label = "Tax-foreclosure lawsuits & sales (Linebarger, all Texas counties)"
    capabilities = (C.TAX,)
    discovery = True
    coverage = ("Texas statewide - every county the firm lists (95 on 2026-10-21): properties taken to "
                "judgment for unpaid property tax and listed for the county tax sale (owner still holds title). "
                "A strategy with no county list keeps the 9 North Texas counties; name any county, or "
                "'All Texas' for all of them.")
    jurisdiction = "Texas (statewide)"
    source_type = "Taxing units' delinquent-tax attorneys - public sale list"
    access_method = "Public JSON list (taxsales.lgbs.com), a few paged requests per hunt"
    public_url = "https://taxsales.lgbs.com/"
    refresh = "Updated by the firm as suits, judgments and sales change"
    freshness_days = 14
    adapter_version = "lgbs_tax_sales/2"
    terms_note = ("Public list published by the taxing units' attorneys; robots.txt allows all agents. "
                  "Struck-off, sold and cancelled rows are skipped - only properties the owner still holds.")

    def search(self, capability, query):
        from app.services.evosense.sources.lgbs import COUNTIES, LgbsTaxSaleReader
        out = Records()
        states = [s.upper() for s in query.get("states") or []]
        if states and "TX" not in states:
            out.stats = {"skipped": "strategy is not in Texas"}
            return out
        # No county list: the North Texas default (unchanged). Otherwise any
        # Texas county named, or 'All Texas' for every county listed.
        wanted = _county(query) or list(COUNTIES)
        res = LgbsTaxSaleReader().discover(limit=int(query.get("limit") or 25), counties=wanted)
        out.extend(res["records"])
        out.stats, out.source_url = res["stats"], res["source_url"]
        return out

    def verify(self):
        from app.services.evosense.sources import base as SB_
        from app.services.evosense.sources.lgbs import API
        j = SB_.get_json(API, {"county": "DALLAS COUNTY", "state": "TX", "limit": 1})
        if not isinstance(j, dict) or "count" not in j:
            raise SB_.SourceError(SB_.SOURCE_FORMAT_CHANGED, "taxsales.lgbs.com did not return a count")
        return {"dallas_listed": j["count"]}


class ClerkForeclosureSource(PublicRecordSource):
    """Posted foreclosure notices filed with the county clerk (Dallas, Tarrant,
    Denton, Johnson): substitute trustee / trustee sales, HOA assessment-lien sales,
    judicial and tax sales, each with its document number and sale date.
    Collin County posts notices on a bot-protected page (read by a person and
    recorded with Record a notice) - reported as
    not connected, never as "no foreclosure"."""
    scope, counties = "county", ("Dallas", "Tarrant", "Denton", "Johnson")
    key = "tx_clerk_foreclosure"
    label = "County clerk foreclosure notices (Dallas, Tarrant, Denton, Johnson)"
    capabilities = (C.FORECLOSURE,)
    discovery = True
    discovery_opt_in = True      # only a strategy whose search names FORECLOSURE (or this source) hunts it
    coverage = ("Dallas, Tarrant, Denton and Johnson County, TX - every foreclosure sale notice filed with the "
                "county clerk (trustee, substitute trustee, HOA assessment-lien, judicial and tax sales), with "
                "document number, sale date and the clerk's indexed address. Not read automatically: Collin "
                "(its notice page is bot-protected), Ellis, Kaufman and Rockwall (the county posts scanned "
                "notice files - check them by hand and use Record a notice) and every other county.")
    jurisdiction = "Dallas, Tarrant, Denton and Johnson County, TX"
    source_type = "County Clerk - posted foreclosure notices (official record)"
    access_method = ("The clerks' free public record search (publicsearch.us), as an anonymous visitor: "
                     "a few paged requests per county, spaced apart")
    public_url = "https://dallas.tx.publicsearch.us/"
    refresh = "Notices are filed daily; sales are the first Tuesday of each month"
    freshness_days = 7
    adapter_version = "tx_clerk_fc/2"
    terms_note = ("Official public record, free and anonymous. Only notice index data and the clerk's own "
                  "text excerpt are read - no paid document images. A notice proves a sale was scheduled, "
                  "not that it happened.")

    def search(self, capability, query):
        from app.services.evosense.sources.clerk_fc import COUNTIES, ClerkForeclosureReader
        out = Records()
        states = [s.upper() for s in query.get("states") or []]
        if states and "TX" not in states:
            out.stats = {"skipped": "strategy is not in Texas"}
            return out
        wanted = _county(query)
        from app.services.evosense.sources.lgbs import wants_all_texas
        if not wanted or wants_all_texas(wanted):
            wanted = list(COUNTIES)
        res = ClerkForeclosureReader().discover(wanted, limit=int(query.get("limit") or 25))
        out.extend(res["records"])
        out.stats, out.source_url = res["stats"], res["source_url"]
        return out

    def verify(self):
        from app.services.evosense.sources.clerk_fc import ClerkForeclosureReader
        return ClerkForeclosureReader().verify("dallas")


class TracerfyContactSource(AcquisitionProvider):
    """Owner phone / email for EvoSense's automatic lookups - the SAME Tracerfy
    provider the "Get phones & emails" button uses (wholesale_enrichment), so
    there is one Tracerfy integration, one set of Do Not Call rules and one
    set of paid-lookup limits. Do Not Call numbers never come back as a phone
    to use. A miss is free; a find costs 5 credits ($0.10).

    Every automatic lookup is ALSO held to the organization's Wholesale
    Settings paid-lookup limits (per day / per month; 0 = off) and counted
    against them - see enrichment.decide / execute."""
    connector_kind = C.REAL
    key = "tracerfy_contact"
    label = "Tracerfy owner lookup"
    capabilities = (C.CONTACT_ENRICHMENT,)
    required_env = ("TRACERFY_API_TOKEN",)
    costs = {C.CONTACT_ENRICHMENT: 10}
    charge_on_miss = False
    scope = "national"
    coverage = "US owner lookup by property address (Tracerfy instant API)"
    jurisdiction = "United States"
    source_type = "Commercial data vendor (paid, per find)"
    access_method = "Vendor REST API (TRACERFY_API_TOKEN)"
    public_url = "https://www.tracerfy.com/skip-tracing-api-documentation/"
    terms_note = "Paid per find. Results are evidence, not verified ownership."

    def enrich(self, data):
        res = WE.PROVIDERS["tracerfy"].lookup(data)
        if res.status == WE.STATUS_NOT_CONFIGURED:
            raise ProviderFailure("Tracerfy is not connected (TRACERFY_API_TOKEN).")
        if res.status == WE.STATUS_FAILED:
            if "slow down" in (res.message or ""):
                raise ProviderRateLimited(900)
            raise ProviderFailure(res.message or "Tracerfy failed")
        return res


class ManualOnlySource(AcquisitionProvider):
    """A real source EvoSense will not automate (no free bulk export, or a
    portal behind search forms / CAPTCHA). Fed through manual entry or CSV."""
    connector_kind = C.MANUAL
    jurisdiction = ""
    source_type = ""
    access_method = "Manual entry or CSV import"
    public_url = ""
    refresh = ""
    terms_note = ""


class DallasForeclosureManual(ManualOnlySource):
    # The key is kept (stored tenant settings refer to it); the channel now
    # covers every county WITHOUT a connected clerk source (Collin and the rest).
    scope, counties = "county", ()
    key = "dallas_foreclosure_manual"
    label = "Foreclosure notices - manual entry (counties without a clerk source)"
    capabilities = (C.FORECLOSURE,)
    coverage = ("Any Texas county whose clerk notices are not read automatically (Collin and others): "
                "a person reads the posted notice and records it with its document number and sale date")
    jurisdiction = "Texas"
    source_type = "County Clerk foreclosure postings"
    public_url = "https://www.collincountytx.gov/"
    terms_note = ("Dallas, Tarrant, Denton and Johnson notices are read automatically (tx_clerk_foreclosure). "
                  "Elsewhere a person enters the notice; without a document number it stays a claim.")


class DallasTaxManual(ManualOnlySource):
    scope, counties = "county", ("Dallas",)
    key = "dallas_tax_manual"
    label = "Dallas County tax delinquency (manual)"
    capabilities = (C.TAX,)
    coverage = "Dallas County, TX — delinquent tax accounts"
    jurisdiction = "Dallas County, TX"
    source_type = "County tax office"
    public_url = "https://www.dallasact.com/"
    terms_note = ("No free bulk delinquency file; account lookups are one-at-a-time web "
                  "searches. Entered manually (signal TAX_DELINQUENT) or imported by CSV.")


class CommercialInterface(AcquisitionProvider):
    """A commercial data vendor with a slot and NO adapter. NOT CONFIGURED
    until someone buys it, adds the key, and a real adapter is written."""
    connector_kind = C.INTERFACE_ONLY
    jurisdiction = "United States"
    source_type = "Commercial property data (paid)"
    access_method = "Vendor API (key required)"
    refresh = ""
    terms_note = "Paid vendor. Not purchased. Nothing is called."


class RentCastInterface(CommercialInterface):
    key = "rentcast"
    label = "RentCast (not configured)"
    capabilities = (C.VALUATION, C.LISTING, C.COMPS)
    required_env = ("RENTCAST_API_KEY",)
    public_url = "https://www.rentcast.io/api"
    coverage = "AVM, rent estimates, active listings"


class RegridInterface(CommercialInterface):
    key = "regrid"
    label = "Regrid parcels (not configured)"
    capabilities = (C.PARCEL, C.OWNERSHIP)
    required_env = ("REGRID_API_TOKEN",)
    public_url = "https://regrid.com/api"
    coverage = "National parcel boundaries and owners"


class AttomPropertyData(AcquisitionProvider):
    """ATTOM property data - Project OVERDRIVE Phase B (#439).

    A REAL adapter, but PERSON-TRIGGERED ONLY: `route()` never returns it, so no
    hunt, scheduler pass or background job can call ATTOM (memory-stability
    gate, PR #437; and no paid call nobody asked for). It is called by
    `provider_network.attom_lookup()` for ONE property, after that function has
    checked what is already on file, the cache, an in-flight request, the
    workspace's switch and its budget.

    The HTTP work and the field mapping are the existing, documented
    `vendors.AttomComps.profile()` / `attom_profile()` - one ATTOM integration,
    not two. ATTOM's answer is a VENDOR CLAIM: `fact_rank` keeps it below county
    records, so it can fill a blank but never replaces county evidence (a
    different value is recorded as a conflict). Sale amounts in Texas stay
    UNVERIFIED, the AVM is an estimate (never an ARV), and the recorded loan is
    the ORIGINAL amount (never today's payoff, so it never becomes a balance).
    ATTOM prices by contract, so a per-call cost must be set for the workspace
    (0 for a flat-rate plan) before the first lookup."""
    connector_kind = C.REAL
    person_triggered_only = True
    fact_rank = 40                       # ingest.RANK_FEED - below county records (60)
    key = "attom"
    label = "ATTOM property data"
    capabilities = (C.ASSESSOR, C.OWNERSHIP, C.VALUATION)
    required_env = ("ATTOM_API_KEY",)
    costs: Dict[str, int] = {}           # contract pricing: the workspace sets cost_overrides
    scope = "national"
    coverage = "US property, owner, mortgage (original amount), assessment, AVM and sales history"
    jurisdiction = "United States"
    source_type = "Commercial property data (paid, contract pricing)"
    access_method = "Vendor REST API (ATTOM_API_KEY)"
    public_url = "https://api.developer.attomdata.com/docs"
    terms_note = ("Paid vendor. Called only when a person asks, for one property. Results are the vendor's "
                  "claim - county records outrank them.")
    adapter_version = "attom/propertyapi-v1-profile"
    PACKAGES = (("detail", "property/detailmortgageowner"), ("assessment", "assessment/detail"),
                ("avm", "attomavm/detail"), ("history", "saleshistory/detail"))


PROVIDERS: Dict[str, AcquisitionProvider] = {p.key: p for p in (
    SandboxPropertyRecords(), SandboxVacancy(), SandboxTaxRoll(), SandboxPublicRecords(),
    SandboxSkipTrace(), SandboxSkipTraceBackup(), SandboxPhoneValidation(),
    ManualSource(), CsvImportSource(),
    TarrantTaxRollSource(), DallasTrwSource(), TadSource(), DcadSource(), CollinCadSource(), DentonGisSource(), TexasParcelOwnerSource(), JohnsonCadSource(), TexasParcelRollSource(), HarrisRollSource(), TravisDelinquentSource(), AustinCodeSource(), CensusGeocoderSource(),
    FortWorthCodeSource(), FortWorthOpenCasesSource(), Dallas311Source(), LgbsTaxSaleSource(),
    ClerkForeclosureSource(),
    DallasForeclosureManual(), DallasTaxManual(),
    RentCastInterface(), RegridInterface(), AttomPropertyData(), TracerfyContactSource(),
)}
# Commercial vendor adapters: EVALUATION ONLY until Mike approves a vendor.
from app.services.evosense.vendors import VENDOR_PROVIDERS  # noqa: E402
PROVIDERS.update({p.key: p for p in VENDOR_PROVIDERS})
SANDBOX_KEYS = tuple(k for k, p in PROVIDERS.items() if p.connector_kind == C.SANDBOX)

# Capabilities with a slot and no adapter of any kind beyond manual entry.
# Reported to the operator as exactly that.
INTERFACE_ONLY_CAPABILITIES = {
    C.LISTING: "No listing (MLS) connector. Expired / failed listing signals can be entered manually.",
    C.EMAIL_VALIDATION: "No email validation connector. Emails are shown as unverified.",
    C.ENTITY_RESOLUTION: "No LLC / entity resolution connector. LLC owners stay UNRESOLVED ENTITY.",
    C.COMPS: ("No approved comps provider. Sold comps are entered by a person, with a source "
              "reference, on the EvoSense property or the Wholesale deal; vendor adapters exist for "
              "provider evaluation only."),
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
        # Two screens loading at once (Providers + Controls) both create the
        # row for a newly added source; the loser must read the winner's row,
        # not fail the whole request. A SAVEPOINT keeps the outer work intact.
        from sqlalchemy.exc import IntegrityError
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            row = (db.query(EvoSenseProviderConfig)
                   .filter(EvoSenseProviderConfig.organization_id == org_id,
                           EvoSenseProviderConfig.provider_key == key).first())
            if row is None:
                raise
    return row


def health_state(p: AcquisitionProvider, cfg, when: Optional[datetime] = None, *,
                 blocked: bool = False) -> str:
    """The ENGINE's routing view (CONNECTED = may be called now). Derived from
    the canonical state below; screens never show these values."""
    st = canonical(p, cfg, when, blocked=blocked)
    if st["state"] == S_NOT_PURCHASED:
        return C.H_NOT_CONNECTED
    if st["state"] == S_NOT_CONFIGURED:
        return C.H_MISSING_CREDENTIALS
    if st["state"] == S_DISABLED:
        return C.H_DISABLED
    if st["state"] == S_BLOCKED:
        return C.H_BLOCKED
    if st["state"] == S_RATE_LIMITED:
        return C.H_RATE_LIMITED
    if not st["routable"]:
        return C.H_DEGRADED
    return C.H_CONNECTED


DEGRADE_AFTER = 3            # consecutive failures
DEGRADE_FOR = timedelta(minutes=15)


# ── Platform layer: shared public-source access ─────────────────────────────
# A public source that refuses the platform's servers refuses every tenant.
# The block is recorded once, platform-wide, holding no tenant data (see
# EvoSenseSourceAccess), and it stops EVERY automatic call to that source.

PLATFORM_ADMIN_ROLES = ("god_admin", "super_admin")


def is_public_source(p: AcquisitionProvider) -> bool:
    return isinstance(p, PublicRecordSource) and not p.required_env and not p.costs


def access_row(db, key: str, *, create: bool = False):
    from app.models.evosense_models import EvoSenseSourceAccess
    q = db.query(EvoSenseSourceAccess).filter(EvoSenseSourceAccess.provider_key == key)
    row = q.first()
    if row is None and create:
        # One row per source, platform-wide. Two requests racing to create it
        # meet the unique key; the loser reads the winner's row.
        from sqlalchemy.exc import IntegrityError
        try:
            with db.begin_nested():
                row = EvoSenseSourceAccess(provider_key=key, blocked=False)
                db.add(row)
                db.flush()
        except IntegrityError:
            row = q.first()
    return row


def block_platform(db, key: str, code: str, reason: str):
    """Record that a public source refused the platform. Idempotent. The reason
    must be the generic code + host phrase the adapter produced - never
    anything naming a tenant, a property or a record."""
    row = access_row(db, key, create=True)
    if not row.blocked:
        row.blocked = True
        row.blocked_at = C.now()
        C.log.warning("evosense: public source %s blocked platform-wide (%s)", key, code)
    row.blocked_code = code
    row.blocked_reason = (reason or code)[:250]
    return row


def clear_platform_block(db, key: str):
    row = access_row(db, key)
    if row is not None and row.blocked:
        row.blocked = False
        row.cleared_at = C.now()
    return row


def _auth_refused_last(cfg) -> bool:
    """A tenant row whose latest outcome is an authorization refusal (state
    recorded before the platform layer existed)."""
    reason = (getattr(cfg, "last_failure_reason", None) or "")
    if not reason.startswith("AUTH_FAILED"):
        return False
    ok, bad = cfg.last_success_at, cfg.last_failure_at
    return bad is not None and (ok is None or bad > ok)


def platform_blocked(db, key: str, cfg=None) -> bool:
    """Is this public source blocked for the whole platform? A tenant row whose
    latest outcome was a 401/403 (recorded before this layer existed) raises
    the block too, so the first read after deploy already stops the calls."""
    p = PROVIDERS.get(key)
    if p is None or not is_public_source(p):
        return False
    row = access_row(db, key)
    if row is not None and row.blocked:
        return True
    if cfg is not None and _auth_refused_last(cfg):
        block_platform(db, key, "AUTH_FAILED", cfg.last_failure_reason)
        return True
    return False


def platform_access(db, key: str) -> Optional[Dict[str, Any]]:
    """What any tenant may see about the platform block: state and the generic
    reason. Nothing else."""
    row = access_row(db, key)
    if row is None:
        return None
    return {"blocked": bool(row.blocked), "code": row.blocked_code if row.blocked else None,
            "reason": row.blocked_reason if row.blocked else None,
            "since": row.blocked_at.isoformat() + "Z" if row.blocked and row.blocked_at else None,
            "last_platform_probe_at": row.last_probe_at.isoformat() + "Z" if row.last_probe_at else None}


def note_source_failure(db, provider: AcquisitionProvider, cfg, code: str, message: str,
                        retry_after: Optional[int] = None) -> None:
    """Record a source failure on the tenant row and, for an authorization
    refusal from a public source, on the platform layer."""
    from app.services.evosense.sources import base as SRC
    record_failure(cfg, "%s: %s" % (code, (message or "")[:200]),
                   rate_limited_for=retry_after if code == SRC.RATE_LIMITED else None)
    if code == SRC.AUTH_FAILED and is_public_source(provider):
        block_platform(db, provider.key, code, "%s: %s" % (code, (message or "")[:200]))


def _cap_health(cfg, capability, *, ok: bool, reason: Optional[str] = None):
    if not capability:
        return
    data = C.jload(getattr(cfg, "capability_health", None), {}) or {}
    row = data.get(capability) or {}
    now = C.now().isoformat()
    if ok:
        row.update(last_success_at=now, failures=0, reason=None)
    else:
        row.update(last_failure_at=now, failures=int(row.get("failures") or 0) + 1,
                   reason=(reason or "")[:200])
    data[capability] = row
    cfg.capability_health = C.jdump(data)


def record_success(cfg, capability: Optional[str] = None):
    cfg.calls_total = (cfg.calls_total or 0) + 1
    cfg.successes_total = (cfg.successes_total or 0) + 1
    cfg.consecutive_failures = 0
    cfg.last_success_at = C.now()
    cfg.degraded_until = None
    _cap_health(cfg, capability, ok=True)


def record_failure(cfg, reason: str, rate_limited_for: Optional[int] = None,
                   capability: Optional[str] = None):
    _cap_health(cfg, capability, ok=False, reason=reason)
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
        if getattr(p, "evaluation_only", False):
            continue                      # the evaluation harness only - never production
        if getattr(p, "person_triggered_only", False):
            continue                      # a person asks for ONE property (provider_network) - never a hunt
        if capability == C.COMPS and p.connector_kind == C.REAL:
            from app.services.evosense import truth_gate as TG
            if not TG.passes(db, org_id, key):
                continue                  # DFW truth gate not met: no comps call, ever
        cfg = config(db, org_id, key)
        if not canonical(p, cfg, blocked=platform_blocked(db, key, cfg))["routable"]:
            continue
        overrides = C.jload(cfg.cost_overrides, {}) or {}
        rate = (cfg.successes_total or 0) / cfg.calls_total if cfg.calls_total else 1.0
        pref_rank = preferences.index(key) if preferences and key in preferences else 99
        out.append((pref_rank, cfg.priority, p.cost(capability, overrides), -rate, key, p, cfg))
    out.sort(key=lambda t: t[:5])
    return [(t[5], t[6], t[2]) for t in out]


# ── THE CANONICAL OPERATIONAL STATE ─────────────────────────────────────────
#
# ONE function decides what every provider IS, and every screen and endpoint
# derives from it: the Source Registry, the Service Providers table, the
# capability tiles, the header's connector count and the engine's routing.
# Two screens can therefore never disagree about the same source again (TAD
# read FAILED on one tab and "Connected" on the next).
#
# The dimensions are kept apart on purpose, so nothing can imply more than it
# knows:
#   configured   the adapter exists and any credential it needs is present
#   enabled      this tenant switched it on
#   reachable    the last attempt got an answer from the source (True), got
#                no answer (False), or nothing has been attempted (None)
#   healthy      the latest outcome is a verified SUCCESS
#   operational  healthy AND nothing stops it being used right now
#   failed       the latest outcome is a failure
#   blocked      the source refused the platform (401/403) - platform-wide
#   routable     the engine may call it now (an UNVERIFIED source is routable
#                so a hunt can verify it; it is still NOT "operational")
#
# HEALTHY only means retrieval succeeded. It says nothing about whether any
# field derived from that retrieval is true; derived values keep their own
# truth labels.

S_HEALTHY = "HEALTHY"
S_UNVERIFIED = "UNVERIFIED"
S_DEGRADED = "DEGRADED"
S_RATE_LIMITED = "RATE LIMITED"
S_FAILED = "FAILED"
S_BLOCKED = "BLOCKED"
S_DISABLED = "DISABLED"
S_NOT_CONFIGURED = "NOT CONFIGURED"
S_NOT_PURCHASED = "NOT PURCHASED"
S_MANUAL_ONLY = "MANUAL ONLY"
S_SANDBOX = "SANDBOX"
S_EVALUATION_ONLY = "EVALUATION ONLY"
STATES = (S_HEALTHY, S_UNVERIFIED, S_DEGRADED, S_RATE_LIMITED, S_FAILED, S_BLOCKED, S_DISABLED,
          S_NOT_CONFIGURED, S_NOT_PURCHASED, S_MANUAL_ONLY, S_SANDBOX, S_EVALUATION_ONLY)
TONE = {S_HEALTHY: "good", S_SANDBOX: "attention", S_UNVERIFIED: "attention", S_DEGRADED: "attention",
        S_RATE_LIMITED: "attention", S_FAILED: "danger", S_BLOCKED: "danger", S_DISABLED: "quiet",
        S_NOT_CONFIGURED: "quiet", S_NOT_PURCHASED: "quiet", S_MANUAL_ONLY: "quiet",
        S_EVALUATION_ONLY: "attention"}

# Registry vocabulary kept for callers and history; every value is a canonical state.
REG_HEALTHY, REG_DEGRADED, REG_FAILED, REG_BLOCKED = S_HEALTHY, S_DEGRADED, S_FAILED, S_BLOCKED
REG_NOT_CONFIGURED, REG_MANUAL_ONLY, REG_UNVERIFIED = S_NOT_CONFIGURED, S_MANUAL_ONLY, S_UNVERIFIED
REGISTRY_STATES = STATES

_NO_ANSWER = ("TIMEOUT", "DOWNLOAD_FAILED", "ProviderTimeout")


def _fmt(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def canonical(p: AcquisitionProvider, cfg, when: Optional[datetime] = None, *,
              blocked: bool = False, block_reason: Optional[str] = None) -> Dict[str, Any]:
    """The state of provider `p` for the tenant whose config row is `cfg`."""
    when = when or C.now()
    kind = p.connector_kind
    configured = kind != C.INTERFACE_ONLY and (not p.required_env or p.is_configured())
    enabled = bool(cfg.enabled) or kind in (C.MANUAL, C.IMPORT)
    ok, bad = cfg.last_success_at, cfg.last_failure_at
    failed_last = bad is not None and (ok is None or bad > ok)
    healthy = ok is not None and not failed_last
    reason = cfg.last_failure_reason or ""
    if ok is None and bad is None:
        reachable = None
    elif failed_last:
        reachable = not reason.startswith(_NO_ANSWER)
    else:
        reachable = True
    rate_limited = bool(cfg.rate_limited_until and cfg.rate_limited_until > when)
    in_backoff = bool(cfg.degraded_until and cfg.degraded_until > when)

    if kind == C.MANUAL:
        state, why = S_MANUAL_ONLY, "Fed by manual entry or CSV import; nothing is called automatically"
    elif kind == C.IMPORT:
        state, why = S_MANUAL_ONLY, "A file a person uploads"
    elif kind == C.INTERFACE_ONLY:
        state, why = S_NOT_PURCHASED, ("Paid vendor — not purchased; no adapter and no credentials (%s). "
                                       "Nothing is called." % ", ".join(p.required_env)) if p.required_env \
            else "No adapter"
    elif not configured:
        state, why = S_NOT_CONFIGURED, "Missing credentials: %s" % ", ".join(p.missing_config())
    elif not cfg.enabled:
        state, why = S_DISABLED, ("Sandbox adapter — off" if kind == C.SANDBOX
                                  else "Configured, but switched off for this organization")
    elif kind == C.SANDBOX and not failed_last and not in_backoff:
        state, why = S_SANDBOX, "Synthetic sandbox data — serves sandbox properties only, never a real one"
    elif blocked:
        state, why = S_BLOCKED, ("Blocked platform-wide: %s. Automatic calls stopped; a platform admin "
                                 "may re-test." % (block_reason or "the source refused the platform's requests"))
    elif rate_limited:
        state, why = S_RATE_LIMITED, "Rate limited by the source until %s UTC" % \
            cfg.rate_limited_until.strftime("%Y-%m-%d %H:%M")
    elif failed_last:
        if (cfg.consecutive_failures or 0) >= DEGRADE_AFTER:
            state, why = S_FAILED, reason or "Repeated failures"
        else:
            state, why = S_DEGRADED, "Last attempt failed: %s" % (reason or "error")
    elif ok is None:
        state, why = S_UNVERIFIED, "Enabled; no successful probe or run yet"
    elif in_backoff:
        state, why = S_DEGRADED, "Recovering from repeated failures"
    else:
        state, why = S_HEALTHY, "Last verified %s UTC" % ok.strftime("%Y-%m-%d %H:%M")

    routable = (configured and enabled and kind in (C.REAL, C.SANDBOX) and not blocked and not rate_limited
                and not in_backoff and state != S_DISABLED)
    operational = state in (S_HEALTHY, S_SANDBOX) and routable
    # EVALUATION ONLY vendors (evosense/vendors.py): callable by the provider
    # evaluation harness alone. The engine never routes to them and no screen
    # may count them operational until Mike approves the vendor.
    evaluation_only = bool(getattr(p, "evaluation_only", False))
    eval_routable = bool(routable)
    if evaluation_only:
        routable = operational = False
        if state in (S_HEALTHY, S_UNVERIFIED):
            state = S_EVALUATION_ONLY
        why = "EVALUATION ONLY - not approved for production use. " + why
    return {"state": state, "why": why, "tone": TONE[state], "evaluation_only": evaluation_only,
            "eval_routable": eval_routable,
            "configured": bool(configured), "enabled": bool(enabled), "reachable": reachable,
            "healthy": bool(healthy) if kind in (C.REAL, C.SANDBOX) else None,
            "operational": bool(operational), "failed": bool(failed_last), "blocked": bool(blocked),
            "routable": bool(routable), "manual": kind in (C.MANUAL, C.IMPORT)}


def state_for(db, org_id: str, key: str, when: Optional[datetime] = None) -> Dict[str, Any]:
    p = PROVIDERS[key]
    cfg = config(db, org_id, key)
    blocked = platform_blocked(db, key, cfg)
    acc = platform_access(db, key) if blocked else None
    st = canonical(p, cfg, when, blocked=blocked, block_reason=(acc or {}).get("reason"))
    st["platform"] = acc
    return st


def _cost_text(p: AcquisitionProvider, overrides=None) -> str:
    if p.connector_kind == C.INTERFACE_ONLY:
        return "paid (not purchased)"
    if p.connector_kind == C.SANDBOX and p.costs:
        return "simulated (sandbox)"
    if p.costs:
        return "paid"
    if getattr(p, "person_triggered_only", False):
        return "paid (contract pricing - set per workspace)"
    return "free" if p.connector_kind == C.REAL else "—"


def _coverage_json(p: AcquisitionProvider) -> Dict[str, Any]:
    return {"scope": p.scope, "counties": list(p.counties), "scope_name": p.scope_name or None}


def _provider_row(db, org_id: str, key: str) -> Dict[str, Any]:
    p = PROVIDERS[key]
    cfg = config(db, org_id, key)
    st = state_for(db, org_id, key)
    overrides = C.jload(cfg.cost_overrides, {}) or {}
    return {
        "key": key, "label": p.label, "connector_kind": p.connector_kind,
        "connector_label": C.CONNECTOR_LABELS[p.connector_kind],
        "capabilities": list(p.capabilities), "coverage": p.coverage, "coverage_area": _coverage_json(p),
        "costs": {c: p.cost(c, overrides) for c in p.capabilities if p.cost(c, overrides)},
        "cost": _cost_text(p, overrides),
        # THE canonical state. `status` and `state` are the same value.
        "status": st["state"], "state": st["state"], "why": st["why"], "tone": st["tone"],
        "configured": st["configured"], "enabled": st["enabled"], "reachable": st["reachable"],
        "healthy": st["healthy"], "operational": st["operational"], "failed": st["failed"],
        "blocked": st["blocked"], "platform": st["platform"],
        "priority": cfg.priority,
        "missing_env_count": len(p.missing_config()),
        "last_success_at": _fmt(cfg.last_success_at),
        "last_failure_at": _fmt(cfg.last_failure_at),
        "last_failure_reason": cfg.last_failure_reason,
        "consecutive_failures": cfg.consecutive_failures,
        "degraded_until": _fmt(cfg.degraded_until),
        "last_attempt_at": _fmt(cfg.last_attempt_at),
        "last_verified_at": _fmt(cfg.last_verified_at),
        "last_record_count": cfg.last_record_count,
        "calls_total": cfg.calls_total, "successes_total": cfg.successes_total,
        "freshness_days": p.freshness_days,
    }


def _markets(db, org_id: str) -> List[str]:
    """The counties this tenant's active strategies hunt in (its markets)."""
    from app.models.evosense_models import EvoSenseStrategy
    out = []
    for s in (db.query(EvoSenseStrategy)
              .filter(EvoSenseStrategy.organization_id == org_id, EvoSenseStrategy.status == "active").all()):
        from app.services.evosense.sources.lgbs import wants_all_texas
        for c in C.jload(getattr(s, "counties", None), []) or []:
            if wants_all_texas([str(c)]):
                continue                       # "All Texas" is not a county; statewide sources cover it
            name = re.sub(r"\s+county$", "", str(c).strip(), flags=re.I).title()
            if name and name not in out:
                out.append(name)
    return out


def verified_counties(db, org_id: str, keys) -> Dict[str, set]:
    """Counties where a statewide source has actually returned a record for
    this workspace. A source that DECLARES statewide scope is not counted as
    covering a county until it has produced data there."""
    from app.models.evosense_models import EvoSenseObservation, EvoSenseProperty
    keys = list(keys)
    if not keys:
        return {}
    rows = (db.query(EvoSenseObservation.provider_key, EvoSenseProperty.county)
            .join(EvoSenseProperty, EvoSenseProperty.id == EvoSenseObservation.property_id)
            .filter(EvoSenseObservation.organization_id == org_id,
                    EvoSenseObservation.provider_key.in_(keys),
                    EvoSenseProperty.county.isnot(None))
            .group_by(EvoSenseObservation.provider_key, EvoSenseProperty.county).all())
    from app.services.evosense.sources.txgio_roll import county_name
    out: Dict[str, set] = {}
    for key, county in rows:
        c = county_name(county or "")          # canonical spelling: "MCLENNAN" -> "McLennan"
        if c:
            out.setdefault(key, set()).add(c)
    return out


def attach_verified_counties(db, org_id: str, rows: List[Dict[str, Any]]) -> None:
    wide = [r for r in rows if (r.get("coverage_area") or {}).get("scope") in ("state", "national")]
    ver = verified_counties(db, org_id, [r["key"] for r in wide])
    for r in wide:
        r["coverage_area"] = dict(r["coverage_area"], verified_counties=sorted(ver.get(r["key"], ())))


def _covers(p, m: str, by_key) -> bool:
    if p.scope in ("national", "state"):
        ca = (by_key.get(p.key) or {}).get("coverage_area") or {}
        return m in (ca.get("verified_counties") or ())
    return m in p.counties


def capability_report(db, org_id: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each capability, rated honestly overall AND per market (county).

    A provider "serves" only when it can actually be used: an operational or
    routable real source, an ENABLED sandbox adapter, or manual entry. A
    blocked, failed-over, disabled or unpurchased provider is listed as
    unavailable with its state, never as serving."""
    markets = _markets(db, org_id)
    by_key = {r["key"]: r for r in rows}
    out = []
    for cap in C.CAPABILITIES:
        cands = [PROVIDERS[k] for k in by_key if cap in PROVIDERS[k].capabilities]
        live = [p for p in cands if p.connector_kind == C.REAL and by_key[p.key]["state"] in
                (S_HEALTHY, S_UNVERIFIED, S_DEGRADED) and not getattr(p, "evaluation_only", False)]
        sandbox = [p for p in cands if p.connector_kind == C.SANDBOX and by_key[p.key]["state"] == S_SANDBOX]
        manual = [p for p in cands if p.connector_kind in (C.MANUAL, C.IMPORT)]
        unavailable = [p for p in cands if p not in live and p not in sandbox and p not in manual]
        if live:
            kind = C.REAL
        elif sandbox:
            kind = C.SANDBOX
        elif manual:
            kind = manual[0].connector_kind
        elif cap in INTERFACE_ONLY_CAPABILITIES:
            kind = C.INTERFACE_ONLY
        else:
            kind = C.NOT_BUILT
        per_market = []
        for m in markets:
            here = [p for p in live if _covers(p, m, by_key)]
            man = [p for p in manual if p.scope == "any" or m in p.counties]
            down = [p for p in unavailable if p.connector_kind == C.REAL and (p.scope in ("national", "state") or m in p.counties)]
            if here:
                city_only = all(p.scope == "city" for p in here)
                per_market.append({"market": "%s County" % m, "kind": C.REAL,
                                   "label": "Real connector" + (" (%s only)" % ", ".join(
                                       sorted({p.scope_name for p in here})) if city_only else ""),
                                   "providers": [p.label for p in here]})
            elif down:
                per_market.append({"market": "%s County" % m, "kind": "unavailable",
                                   "label": "Unavailable — " + "; ".join(
                                       "%s %s" % (p.label, by_key[p.key]["state"].lower()) for p in down),
                                   "providers": []})
            elif [p for p in man if p.scope != "any"]:
                per_market.append({"market": "%s County" % m, "kind": C.MANUAL, "label": "Manual entry only",
                                   "providers": [p.label for p in man]})
            else:
                per_market.append({"market": "%s County" % m, "kind": "none",
                                   "label": "No source" + (" (manual entry possible)" if man else ""),
                                   "providers": [p.label for p in man]})
        out.append({"capability": cap, "kind": kind, "label": C.CONNECTOR_LABELS[kind],
                    "providers": [p.label for p in live + sandbox + manual],
                    "unavailable": [{"label": p.label, "state": by_key[p.key]["state"]} for p in unavailable],
                    "by_market": per_market,
                    "note": INTERFACE_ONLY_CAPABILITIES.get(cap)})
    return out


def status_report(db, org_id: str) -> Dict[str, Any]:
    """The Service Providers screen. Every status comes from `canonical()`.
    No credential ever appears."""
    rows = [_provider_row(db, org_id, key) for key in PROVIDERS]
    attach_verified_counties(db, org_id, rows)
    return {"providers": rows, "capabilities": capability_report(db, org_id, rows),
            "markets": ["%s County" % m for m in _markets(db, org_id)],
            "states": list(STATES),
            # counted from the canonical state: operational = verified AND usable now
            "real_connectors": [r["key"] for r in rows
                                if r["connector_kind"] == C.REAL and r["operational"]],
            "real_connectors_unavailable": [r["key"] for r in rows if r["connector_kind"] == C.REAL
                                            and r["enabled"] and not r["operational"]]}


def registry_state(p: AcquisitionProvider, cfg, when: Optional[datetime] = None, *,
                   blocked: bool = False, block_reason: Optional[str] = None) -> Dict[str, str]:
    """Kept for callers; the answer is the canonical state."""
    st = canonical(p, cfg, when, blocked=blocked, block_reason=block_reason)
    return {"state": st["state"], "why": st["why"]}


def source_registry(db, org_id: str) -> Dict[str, Any]:
    """The Source Registry: every real, manual and paid source (sandbox
    adapters are not sources) with jurisdiction, access and the SAME
    canonical state the Service Providers table shows."""
    rows = []
    for key, p in PROVIDERS.items():
        if p.connector_kind == C.SANDBOX:
            continue
        r = _provider_row(db, org_id, key)
        r.update({
            "jurisdiction": getattr(p, "jurisdiction", "") or "",
            "source_type": getattr(p, "source_type", "") or "",
            "access_method": getattr(p, "access_method", "") or "",
            "public_url": getattr(p, "public_url", "") or "",
            "refresh": getattr(p, "refresh", "") or "",
            "terms_note": getattr(p, "terms_note", "") or "",
            "role": "discovery" if getattr(p, "discovery", False) else
                    ("lookup" if getattr(p, "lookup_capability", None) else
                     ("manual" if p.connector_kind in (C.MANUAL, C.IMPORT) else "interface")),
            "adapter_version": getattr(p, "adapter_version", "") or None,
        })
        rows.append(r)
    order = {S_HEALTHY: 0, S_DEGRADED: 1, S_RATE_LIMITED: 1, S_UNVERIFIED: 2, S_FAILED: 3, S_BLOCKED: 3,
             S_DISABLED: 4, S_MANUAL_ONLY: 5, S_NOT_CONFIGURED: 6, S_NOT_PURCHASED: 6}
    rows.sort(key=lambda r: (order.get(r["state"], 9), r["jurisdiction"], r["label"]))
    return {"sources": rows, "states": list(STATES)}


def verify_source(db, org_id: str, key: str, *, platform_admin: bool = False) -> Dict[str, Any]:
    """A cheap, real probe (a zip's directory, a one-row query, one geocode).
    Success is what makes a source HEALTHY; failure is recorded with its code.

    A source BLOCKED platform-wide is not probed on a tenant's request: the
    server has already refused the platform, and asking again on behalf of
    every tenant is exactly the hammering the block exists to stop. A platform
    admin may re-test it with ONE request; a success clears the block."""
    from app.services.evosense.sources import base as SB_
    p = PROVIDERS[key]
    cfg = config(db, org_id, key)
    if not isinstance(p, PublicRecordSource):
        return {"key": key, "ok": False, "error": "Only automated public sources can be verified"}
    blocked = platform_blocked(db, key, cfg)
    if blocked and not platform_admin:
        acc = platform_access(db, key) or {}
        return {"key": key, "ok": False, "code": "PLATFORM_BLOCKED",
                "error": "Blocked platform-wide (%s). The source is not called again until a platform "
                         "admin re-tests it." % (acc.get("reason") or "authorization refused")}
    now = C.now()
    cfg.last_attempt_at = now
    row = access_row(db, key, create=blocked) if blocked else None
    if row is not None:
        row.last_probe_at = now
    try:
        detail = p.verify()
    except SB_.SourceError as exc:
        note_source_failure(db, p, cfg, exc.code, exc.message, retry_after=exc.retry_after)
        return {"key": key, "ok": False, "code": exc.code, "error": exc.message}
    except Exception as exc:  # noqa: BLE001 - recorded, never raised to the operator raw
        record_failure(cfg, "%s: %s" % (type(exc).__name__, str(exc)[:160]))
        return {"key": key, "ok": False, "code": "ERROR", "error": str(exc)[:200]}
    record_success(cfg)
    cfg.last_verified_at = C.now()
    if row is not None:
        row.last_probe_ok_at = cfg.last_verified_at
        clear_platform_block(db, key)
    return {"key": key, "ok": True, "detail": detail}


# ── uploaded copies of a public source's file ───────────────────────────────
# TAD refuses cloud servers but serves the same free file to a person's own
# computer. A platform admin (or the operator's nightly feed job) uploads it;
# the adapter then reads that copy. Only sources listed here accept an upload.
UPLOADABLE = {"tad": {"label": "TAD Property Data (Delimited) - Residential",
                      "file_name": "PropertyData(Delimited)_R.ZIP",
                      "download_page": "https://www.tad.org/resources/data-downloads",
                      "max_bytes": 400 * 1024 * 1024,
                      "publisher": "TAD", "county": "Tarrant",
                      "missing": "Tarrant owner, size, year built and tax value"},
              # JCAD answers a person's browser but shows cloud servers a JavaScript
              # bot check (403). We never try to get past it: a person downloads the
              # free file and a platform admin uploads it here.
              "johnson_cad": {"label": "Johnson County Appraisal District - Certified Records",
                              "file_name": "the certified-records zip (e.g. BATCH_896.all_.zip)",
                              "download_page": "https://johnsoncad.com/downloads",
                              "max_bytes": 300 * 1024 * 1024,
                              "publisher": "JCAD", "county": "Johnson",
                              "missing": "Johnson owner, legal description and deed date"}}


def validate_uploaded_zip(key: str, path: str) -> Dict[str, Any]:
    """The file must be the publisher's own export: a readable zip with the
    data member and every column the adapter reads. Raises ValueError."""
    import zipfile
    from app.services.evosense.sources import base as SB_
    if key == "tad":
        from app.services.evosense.sources.tarrant import TAD_REQUIRED
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            raise ValueError("That is not a zip file. Upload the .ZIP exactly as TAD publishes it.")
        try:
            member = next((n for n in zf.namelist() if n.lower().endswith(".txt")), None)
            if member is None:
                raise ValueError("No .txt data file inside the zip - is this the TAD Property Data file?")
            header = next(SB_.text_lines(zf, member)).split("|")
        finally:
            zf.close()
        missing = [c for c in TAD_REQUIRED if c not in header]
        if missing:
            raise ValueError("This is not the TAD residential property file (missing columns: %s)."
                             % ", ".join(missing[:6]))
        return {"member": member, "columns": len(header)}
    if key == "johnson_cad":
        from app.services.evosense.sources import johnson as J
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            raise ValueError("That is not a zip file. Upload the certified-records .zip exactly as JCAD publishes it.")
        try:
            names = set(zf.namelist())
            missing_files = [m for m in (J.MEMBER, J.LEGAL_MEMBER) if m not in names]
            if missing_files:
                raise ValueError("This is not the JCAD certified-records zip (missing %s). Upload it "
                                 "exactly as downloaded - do not unzip and re-zip it." % ", ".join(missing_files))
            header = [c.strip() for c in next(SB_.text_lines(zf, J.MEMBER)).split("\t")]
        finally:
            zf.close()
        missing = [c for c in J.REQUIRED if c not in header]
        if missing:
            raise ValueError("This is not the JCAD certified-records file (missing columns: %s)."
                             % ", ".join(missing[:6]))
        return {"member": J.MEMBER, "columns": len(header)}
    raise ValueError("This source does not take uploads.")


def accept_upload(db, key: str, tmp_path: str, *, source_date: Optional[str] = None,
                  by: Optional[str] = None, via: str = "upload") -> Dict[str, Any]:
    """Validate, store, and make the source usable again: the platform block
    is lifted and every workspace's row for it is marked as answering, because
    the adapter now reads this copy instead of calling the publisher."""
    from app.models.evosense_models import EvoSenseProviderConfig
    from app.services.evosense.sources import base as SB_
    if key not in UPLOADABLE:
        raise ValueError("This source does not take uploads.")
    detail = validate_uploaded_zip(key, tmp_path)
    meta = SB_.store_upload(key, tmp_path, {
        "uploaded_at": C.now().isoformat() + "Z", "source_date": source_date or None,
        "by": by, "via": via, **detail})
    meta["durable"] = persist_upload(db, key, SB_.uploaded_path(key), meta)
    if key == "johnson_cad":
        from app.services.evosense.sources import johnson as J
        J.forget_index()                                   # read the new copy, not the old one
    clear_platform_block(db, key)
    p = PROVIDERS.get(key)
    cap = getattr(p, "lookup_capability", None)
    for cfg in db.query(EvoSenseProviderConfig).filter(EvoSenseProviderConfig.provider_key == key).all():
        record_success(cfg, capability=cap)
        cfg.last_verified_at = C.now()
    db.commit()
    C.log.info("evosense: %s file uploaded via %s (%s bytes)", key, via, meta.get("size"))
    return {"key": key, "ok": True, **meta}


DURABLE_CHUNK = 4 * 1024 * 1024


def persist_upload(db, key: str, path: str, meta: Dict[str, Any]) -> bool:
    """Keep the uploaded file in the database (chunked), replacing the previous
    copy, so a deploy that wipes the disk does not lose it."""
    import hashlib
    from app.models.evosense_models import EvoSenseSourceFile
    size = os.path.getsize(path)
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(DURABLE_CHUNK), b""):
            h.update(block)
    digest = h.hexdigest()
    count = max(1, -(-size // DURABLE_CHUNK))
    db.query(EvoSenseSourceFile).filter(EvoSenseSourceFile.provider_key == key).delete()
    with open(path, "rb") as fh:
        for i in range(count):
            db.add(EvoSenseSourceFile(provider_key=key, chunk_index=i, chunk_count=count,
                                      data=fh.read(DURABLE_CHUNK), sha256=digest, size=size,
                                      meta=C.jdump(meta)))
            db.flush()
    db.commit()
    return True


def restore_upload(key: str, factory=None) -> bool:
    """Rebuild the disk copy from the durable one. True when restored.
    `factory` makes the database session (default: the app's own)."""
    import hashlib
    import json as _json
    import tempfile as _tf
    from app.deps import SessionLocal
    from app.models.evosense_models import EvoSenseSourceFile
    from app.services.evosense.sources import base as SB_
    if key not in UPLOADABLE:
        return False
    db = (factory or SessionLocal)()
    try:
        ids = [r[0] for r in db.query(EvoSenseSourceFile.id).filter(EvoSenseSourceFile.provider_key == key)
               .order_by(EvoSenseSourceFile.chunk_index).all()]
        if not ids:
            return False
        fd, tmp = _tf.mkstemp(dir=SB_.upload_dir(), suffix=".part")
        h, meta, want, count = hashlib.sha256(), {}, None, 0
        with os.fdopen(fd, "wb") as out:
            for rid in ids:                       # one chunk in memory at a time
                row = db.query(EvoSenseSourceFile).filter(EvoSenseSourceFile.id == rid).one()
                out.write(row.data)
                h.update(row.data)
                want, count = row.sha256, row.chunk_count
                meta = _json.loads(row.meta or "{}")
                db.expunge(row)
        if len(ids) != count or h.hexdigest() != want:
            os.remove(tmp)
            C.log.warning("evosense: durable copy of %s is incomplete - not restored", key)
            return False
        meta = dict(meta, restored_at=C.now().isoformat() + "Z")
        SB_.store_upload(key, tmp, meta)
        C.log.info("evosense: %s file restored from durable storage (%s bytes)", key, meta.get("size"))
        return True
    finally:
        db.close()


def upload_status(key: str) -> Dict[str, Any]:
    from app.services.evosense.sources import base as SB_
    info = UPLOADABLE.get(key)
    if info is None:
        return {"key": key, "uploadable": False}
    return {"key": key, "uploadable": True, **{k: v for k, v in info.items() if k != "max_bytes"},
            "max_mb": info["max_bytes"] // (1024 * 1024), "current": SB_.uploaded_info(key)}


# The adapters read the uploaded copy from disk; after a deploy wiped it, they
# rebuild it from the durable copy through this hook.
def _install_restore_hook():
    from app.services.evosense.sources import base as SB_
    SB_.set_restore_hook(restore_upload)


_install_restore_hook()
