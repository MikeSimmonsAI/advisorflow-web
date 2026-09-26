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


def _county(q: Dict[str, Any]) -> List[str]:
    return [re.sub(r"\s+county$", "", (c or "").strip().lower()) for c in q.get("counties") or []]


def _wants(q: Dict[str, Any], county: str) -> bool:
    counties = _county(q)
    states = [s.upper() for s in q.get("states") or []]
    if states and "TX" not in states:
        return False
    return not counties or county in counties


class TarrantTaxRollSource(PublicRecordSource):
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
    adapter_version = "tarrant_tax_roll/1"

    def search(self, capability, query):
        from app.services.evosense.sources.tarrant import TarrantTaxRollReader
        out = Records()
        if not _wants(query, "tarrant"):
            out.stats = {"skipped": "strategy does not include Tarrant County"}
            return out
        res = TarrantTaxRollReader().discover(limit=int(query.get("limit") or 25),
                                              scan_limit=int(query.get("scan_limit") or 600_000),
                                              min_due=float(query.get("min_tax_due") or 250))
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


class TadSource(PublicRecordSource):
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
    adapter_version = "tad_property_data/1"

    def applies(self, target):
        return (target.get("county") or "").lower() == "tarrant" and bool(target.get("parcel_apn"))

    def lookup_many(self, targets):
        from app.services.evosense.sources.tarrant import TadReader, apn
        by_apn = {apn(t["parcel_apn"]): t["id"] for t in targets if t.get("parcel_apn")}
        res = TadReader().lookup_many(list(by_apn))
        results = {tid: res["records"].get(a) for a, tid in by_apn.items()}
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
    adapter_version = "dcad_certified/1"

    def search(self, capability, query):
        from app.services.evosense.sources.dallas import DcadReader
        out = Records()
        if capability != C.PROPERTY_SEARCH or not _wants(query, "dallas"):
            out.stats = {"skipped": "strategy does not include Dallas County"}
            return out
        res = DcadReader().discover(limit=int(query.get("limit") or 25))
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


class FortWorthCodeSource(PublicRecordSource):
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
    adapter_version = "fw_code_violations/1"

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
    adapter_version = "dallas_311/1"

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
    key = "dallas_foreclosure_manual"
    label = "Dallas County foreclosure notices (manual)"
    capabilities = (C.FORECLOSURE,)
    coverage = "Dallas County, TX — notices of trustee sale posted with the County Clerk"
    jurisdiction = "Dallas County, TX"
    source_type = "County Clerk foreclosure postings"
    public_url = "https://www.dallascounty.org/"
    terms_note = ("No free structured export. Postings are read by a person and entered "
                  "manually (signal PRE_FORECLOSURE) or imported by CSV.")


class DallasTaxManual(ManualOnlySource):
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


class AttomInterface(CommercialInterface):
    key = "attom"
    label = "ATTOM property data (not configured)"
    capabilities = (C.ASSESSOR, C.FORECLOSURE, C.VALUATION, C.LISTING)
    required_env = ("ATTOM_API_KEY",)
    public_url = "https://api.developer.attomdata.com/"
    coverage = "Assessor, recorder, foreclosure, AVM"


PROVIDERS: Dict[str, AcquisitionProvider] = {p.key: p for p in (
    SandboxPropertyRecords(), SandboxVacancy(), SandboxTaxRoll(), SandboxPublicRecords(),
    SandboxSkipTrace(), SandboxSkipTraceBackup(), SandboxPhoneValidation(),
    ManualSource(), CsvImportSource(),
    TarrantTaxRollSource(), TadSource(), DcadSource(), CensusGeocoderSource(),
    FortWorthCodeSource(), Dallas311Source(),
    DallasForeclosureManual(), DallasTaxManual(),
    RentCastInterface(), RegridInterface(), AttomInterface(),
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


def health_state(p: AcquisitionProvider, cfg, when: Optional[datetime] = None, *,
                 blocked: bool = False) -> str:
    when = when or C.now()
    if p.connector_kind == C.INTERFACE_ONLY:
        return C.H_NOT_CONNECTED
    if p.required_env and not p.is_configured():
        return C.H_MISSING_CREDENTIALS
    if not cfg.enabled:
        return C.H_DISABLED
    if blocked:
        return C.H_BLOCKED
    if cfg.rate_limited_until and cfg.rate_limited_until > when:
        return C.H_RATE_LIMITED
    if cfg.degraded_until and cfg.degraded_until > when:
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
        if health_state(p, cfg, blocked=platform_blocked(db, key, cfg)) != C.H_CONNECTED:
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
            "status": health_state(p, cfg, blocked=platform_blocked(db, key, cfg)), "enabled": bool(cfg.enabled),
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
            "real_connectors": [r["key"] for r in rows
                                if r["connector_kind"] == C.REAL and r["status"] == C.H_CONNECTED]}


# ── Source Registry (spec vocabulary) ──────────────────────────────────────

REG_HEALTHY = "HEALTHY"
REG_DEGRADED = "DEGRADED"
REG_FAILED = "FAILED"
REG_NOT_CONFIGURED = "NOT CONFIGURED"
REG_MANUAL_ONLY = "MANUAL ONLY"
REG_UNVERIFIED = "UNVERIFIED"          # enabled, but no probe or run has succeeded yet
REG_BLOCKED = "BLOCKED"                # refused the platform (401/403); no automatic call
REGISTRY_STATES = (REG_HEALTHY, REG_DEGRADED, REG_FAILED, REG_BLOCKED, REG_NOT_CONFIGURED,
                   REG_MANUAL_ONLY, REG_UNVERIFIED)


def registry_state(p: AcquisitionProvider, cfg, when: Optional[datetime] = None, *,
                   blocked: bool = False, block_reason: Optional[str] = None) -> Dict[str, str]:
    """HEALTHY only after a verified success that is newer than any failure.
    A source is never shown as operational on the strength of being enabled."""
    when = when or C.now()
    if p.connector_kind == C.MANUAL:
        return {"state": REG_MANUAL_ONLY, "why": "Fed by manual entry or CSV import"}
    if p.connector_kind == C.IMPORT:
        return {"state": REG_MANUAL_ONLY, "why": "A file a person uploads"}
    if p.connector_kind == C.SANDBOX:
        return {"state": REG_NOT_CONFIGURED if not cfg.enabled else REG_HEALTHY,
                "why": "Synthetic sandbox data (never shown for real properties)"}
    if p.connector_kind == C.INTERFACE_ONLY:
        return {"state": REG_NOT_CONFIGURED,
                "why": "No adapter and no credentials (%s)" % ", ".join(p.required_env) if p.required_env
                else "No adapter"}
    if p.required_env and not p.is_configured():
        return {"state": REG_NOT_CONFIGURED, "why": "Missing credentials"}
    if not cfg.enabled:
        return {"state": REG_NOT_CONFIGURED, "why": "Disabled for this organization"}
    if blocked:
        return {"state": REG_BLOCKED,
                "why": "Blocked platform-wide: %s. Automatic calls stopped; a platform admin may re-test."
                       % (block_reason or "the source refused the platform's requests")}
    if cfg.rate_limited_until and cfg.rate_limited_until > when:
        return {"state": REG_DEGRADED, "why": "Rate limited by the source until %s UTC"
                % cfg.rate_limited_until.strftime("%Y-%m-%d %H:%M")}
    ok = cfg.last_success_at
    bad = cfg.last_failure_at
    if ok is None:
        if bad is not None:
            return {"state": REG_FAILED, "why": cfg.last_failure_reason or "Every attempt failed"}
        return {"state": REG_UNVERIFIED, "why": "Enabled; no successful probe or run yet"}
    if bad is not None and bad > ok:
        if (cfg.consecutive_failures or 0) >= DEGRADE_AFTER:
            return {"state": REG_FAILED, "why": cfg.last_failure_reason or "Repeated failures"}
        return {"state": REG_DEGRADED, "why": "Last attempt failed: %s" % (cfg.last_failure_reason or "error")}
    if cfg.degraded_until and cfg.degraded_until > when:
        return {"state": REG_DEGRADED, "why": "Recovering from repeated failures"}
    return {"state": REG_HEALTHY, "why": "Last verified %s UTC" % ok.strftime("%Y-%m-%d %H:%M")}


def source_registry(db, org_id: str) -> Dict[str, Any]:
    rows = []
    for key, p in PROVIDERS.items():
        if p.connector_kind == C.SANDBOX:
            continue
        cfg = config(db, org_id, key)
        blocked = platform_blocked(db, key, cfg)
        st = registry_state(p, cfg, blocked=blocked,
                            block_reason=(platform_access(db, key) or {}).get("reason") if blocked else None)
        rows.append({
            "key": key, "label": p.label, "state": st["state"], "why": st["why"],
            "connector_kind": p.connector_kind, "enabled": bool(cfg.enabled),
            "jurisdiction": getattr(p, "jurisdiction", "") or "",
            "source_type": getattr(p, "source_type", "") or "",
            "access_method": getattr(p, "access_method", "") or "",
            "public_url": getattr(p, "public_url", "") or "",
            "refresh": getattr(p, "refresh", "") or "",
            "terms_note": getattr(p, "terms_note", "") or "",
            "capabilities": list(p.capabilities),
            "role": "discovery" if getattr(p, "discovery", False) else
                    ("lookup" if getattr(p, "lookup_capability", None) else
                     ("manual" if p.connector_kind in (C.MANUAL, C.IMPORT) else "interface")),
            "cost": "free" if not p.costs and p.connector_kind == C.REAL else
                    ("paid (not purchased)" if p.connector_kind == C.INTERFACE_ONLY else "—"),
            "adapter_version": getattr(p, "adapter_version", "") or None,
            "freshness_days": p.freshness_days,
            "last_success_at": cfg.last_success_at.isoformat() + "Z" if cfg.last_success_at else None,
            "last_failure_at": cfg.last_failure_at.isoformat() + "Z" if cfg.last_failure_at else None,
            "last_failure_reason": cfg.last_failure_reason,
            "last_attempt_at": cfg.last_attempt_at.isoformat() + "Z" if cfg.last_attempt_at else None,
            "last_verified_at": cfg.last_verified_at.isoformat() + "Z" if cfg.last_verified_at else None,
            "last_record_count": cfg.last_record_count,
            "calls_total": cfg.calls_total, "successes_total": cfg.successes_total,
        })
    order = {REG_HEALTHY: 0, REG_DEGRADED: 1, REG_UNVERIFIED: 2, REG_FAILED: 3, REG_BLOCKED: 3,
             REG_MANUAL_ONLY: 4, REG_NOT_CONFIGURED: 5}
    rows.sort(key=lambda r: (order.get(r["state"], 9), r["jurisdiction"], r["label"]))
    return {"sources": rows, "states": list(REGISTRY_STATES)}


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
