"""COMMERCIAL VENDOR ADAPTERS - built from each vendor's published API docs,
EVALUATION ONLY until Mike approves a provider for production.

    tracerfy          Tracerfy instant address trace   CONTACT_ENRICHMENT
    reapi_skiptrace   RealEstateAPI /v1/SkipTrace       CONTACT_ENRICHMENT
    dataskip          DataSkip /api/v1/skip-trace       CONTACT_ENRICHMENT
    twilio_lookup     Twilio Lookup v2 line type        PHONE_VALIDATION
    reapi_comps       RealEstateAPI /v3/PropertyComps   COMPS
    sandbox_comps     synthetic comps (test records)    COMPS (SANDBOX)

WHAT "EVALUATION ONLY" MEANS (`evaluation_only = True`):
  * `providers.route()` never returns them, so no hunt, enrichment or
    validation in production can call them - whatever the tenant config says;
  * the Capability Registry shows them as EVALUATION ONLY, never operational;
  * only the provider evaluation harness (`provider_eval.py`) calls them, and
    only for an admin, only with each vendor's credential in the platform
    environment, the tenant switch on, and - because every call costs money -
    the owner's typed confirmation.
Flipping a vendor to production is a one-line change (`evaluation_only =
False`) that is Mike's decision, not the code's.

Nothing here invents data. A field the vendor did not return stays None. Every
comp carries a PRICE SOURCE classification (comp_rules.PRICE_*), and only a
closed sale can ever count toward an ARV.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services import comp_rules as CR
from app.services import wholesale_enrichment as WE
from app.services.evosense import common as C
from app.services.evosense.providers import (AcquisitionProvider, ProviderFailure,
                                             ProviderRateLimited, ProviderTimeout)

TIMEOUT = 20

# Sale prices are not public record in these states, so a "last sale price"
# from public records there is NOT evidence of a closed sale price unless its
# origin is proven. (Missouri is non-disclosure in part; listed conservatively.)
NON_DISCLOSURE_STATES = {"AK", "ID", "KS", "LA", "MS", "MO", "MT", "NM", "ND", "TX", "UT", "WY"}


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _http(method: str, url: str, *, headers=None, json=None, params=None, auth=None) -> Dict[str, Any]:
    """One vendor call. Maps transport failures to the engine's exceptions;
    never retries in a loop (the harness and the budget decide what happens)."""
    import httpx
    try:
        r = httpx.request(method, url, headers=headers, json=json, params=params, auth=auth,
                          timeout=TIMEOUT)
    except httpx.TimeoutException:
        raise ProviderTimeout("%s did not answer within %ss" % (_host(url), TIMEOUT))
    except httpx.HTTPError as exc:
        raise ProviderFailure("%s: %s" % (_host(url), str(exc)[:160]))
    if r.status_code == 429:
        ra = r.headers.get("Retry-After")
        raise ProviderRateLimited(int(ra) if ra and ra.isdigit() else 300)
    if r.status_code in (401, 403):
        raise ProviderFailure("%s refused the credentials (%s)" % (_host(url), r.status_code))
    if r.status_code == 404:
        return {"_status": 404}
    if r.status_code >= 400:
        raise ProviderFailure("%s answered %s" % (_host(url), r.status_code))
    try:
        body = r.json()
    except ValueError:
        raise ProviderFailure("%s did not return JSON" % _host(url))
    if isinstance(body, dict):
        body.setdefault("_status", r.status_code)
    return body


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1) if m else url[:40]


def _digits(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())[-10:]


def _line(v) -> Optional[str]:
    t = (v or "").strip().lower()
    if not t:
        return None
    if "mobile" in t or "wireless" in t or t == "cell":
        return "mobile"
    if "land" in t:
        return "landline"
    if "voip" in t:
        return "voip"
    return "unknown"


def _tokens(name: Optional[str]) -> set:
    return {t for t in re.split(r"[^A-Z]+", (name or "").upper()) if len(t) > 1}


def name_agreement(owner_name: Optional[str], candidate: Optional[str]) -> Optional[str]:
    """How the vendor's person name compares with the owner of record:
    'full' (every token of the shorter name appears), 'surname' (the last
    names agree), 'none', or None when either side is missing. Evidence for a
    person to weigh - never a verdict."""
    a, b = _tokens(owner_name), _tokens(candidate)
    if not a or not b:
        return None
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    if small <= big:
        return "full"
    last_b = (candidate or "").upper().split()[-1:] if candidate else []
    if last_b and last_b[0] in a:
        return "surname"
    return "none"


class VendorAdapter(AcquisitionProvider):
    connector_kind = C.REAL
    evaluation_only = True
    scope = "national"
    jurisdiction = "United States"
    source_type = "Commercial data vendor (paid)"
    access_method = "Vendor REST API (key required)"
    refresh = ""
    terms_note = ("EVALUATION ONLY. Paid vendor; storage, redistribution and multi-tenant rights "
                  "require written confirmation before any production use.")
    price_confirmed = False          # is costs[] the vendor's PUBLISHED price, confirmed?
    price_note = ""

    def _env(self, name: str) -> str:
        v = os.environ.get(name)
        if not v:
            raise ProviderFailure("%s is not configured (%s)" % (self.label, name))
        return v


# ── Contact / skip trace ───────────────────────────────────────────────────

class TracerfySkipTrace(VendorAdapter):
    key = "tracerfy"
    label = "Tracerfy skip trace"
    capabilities = (C.CONTACT_ENRICHMENT,)
    required_env = ("TRACERFY_API_TOKEN",)
    # Instant (API) address trace: 5 credits per hit, 0 on a miss; credits
    # are $0.02 pay-as-you-go -> $0.10 per hit. (The $0.02 / $0.04 "per hit"
    # prices are the BATCH normal / advanced traces, not the instant API.)
    costs = {C.CONTACT_ENRICHMENT: 10}
    charge_on_miss = False
    price_confirmed = True
    price_note = "Instant API: 5 credits ($0.10) per hit, misses free (vendor docs + pricing page)."
    public_url = "https://www.tracerfy.com/skip-tracing-api-documentation/"
    coverage = "US owner skip trace by property address (instant API)"
    adapter_version = "tracerfy/v1-instant"
    BASE = "https://tracerfy.com/v1/api"

    def enrich(self, data: WE.EnrichmentInput) -> WE.EnrichmentResult:
        if not (data.street_address and data.city and data.state):
            return WE.EnrichmentResult(status=WE.STATUS_NO_MATCH, provider=self.key,
                                       message="Tracerfy needs street, city and state.")
        body = _http("POST", self.BASE + "/trace/lookup/",
                     headers={"Authorization": "Bearer %s" % self._env("TRACERFY_API_TOKEN")},
                     json={"address": data.street_address, "city": data.city, "state": data.state,
                           "zip": data.zip_code or "", "find_owner": True})
        credits = int(body.get("credits_deducted") or 0)
        persons = body.get("persons") or []
        if not body.get("hit") or not persons:
            return WE.EnrichmentResult(status=WE.STATUS_NO_MATCH, provider=self.key,
                                       message="No match.", billable=credits > 0,
                                       cost_cents=credits * 2, looked_up_at=_now_iso())
        # The person whose name agrees best with the owner of record; ties
        # keep the vendor's own order.
        rank = {"full": 0, "surname": 1, None: 2, "none": 3}
        best = sorted(persons, key=lambda p: rank[name_agreement(
            data.owner_name, p.get("full_name") or " ".join(
                x for x in (p.get("first_name"), p.get("last_name")) if x))])[0]
        full = best.get("full_name") or " ".join(x for x in (best.get("first_name"), best.get("last_name")) if x)
        agree = name_agreement(data.owner_name, full)
        evidence = {"name_match": agree, "vendor_property_owner": best.get("property_owner"),
                    "persons_returned": len(persons), "deceased": best.get("deceased"),
                    "litigator": best.get("litigator")}
        phones = [WE.EnrichmentPhone(
            number=_digits(ph.get("number")), phone_type=_line(ph.get("type")),
            confidence=None, source=self.key, dnc_flag=ph.get("dnc"),
            match_evidence=dict(evidence, rank=ph.get("rank"), carrier=ph.get("carrier")))
            for ph in (best.get("phones") or []) if _digits(ph.get("number"))]
        emails = [WE.EnrichmentEmail(address=(e.get("email") or "").strip().lower(),
                                     match_evidence=dict(evidence, rank=e.get("rank")))
                  for e in (best.get("emails") or []) if e.get("email")]
        mail = best.get("mailing_address") or {}
        return WE.EnrichmentResult(
            status=WE.STATUS_SUCCEEDED if (phones or emails) else WE.STATUS_NO_MATCH,
            provider=self.key, phones=phones, emails=emails, owner_name=full or None,
            mailing_street=mail.get("street"), mailing_city=mail.get("city"),
            mailing_state=mail.get("state"), mailing_zip=mail.get("zip"),
            billable=credits > 0, cost_cents=credits * 2, looked_up_at=_now_iso(),
            match_evidence=evidence, raw_summary="Tracerfy instant trace: %s person(s)" % len(persons))


class RealEstateApiSkipTrace(VendorAdapter):
    key = "reapi_skiptrace"
    label = "RealEstateAPI skip trace"
    capabilities = (C.CONTACT_ENRICHMENT,)
    required_env = ("REAPI_API_KEY",)
    # Reported at $0.05/match with a property-data subscription; the pricing
    # page could not be read by our tools -> NOT confirmed. Mike can set the
    # real figure as a cost override before any evaluation.
    costs = {C.CONTACT_ENRICHMENT: 5}
    charge_on_miss = False
    price_confirmed = False
    price_note = "Reported $0.05/match with a subscription; UNCONFIRMED - set a cost override."
    public_url = "https://developer.realestateapi.com/reference/skiptrace-api"
    coverage = "US skip trace by name + property / mailing address"
    adapter_version = "reapi/skiptrace-v1"
    URL = "https://api.realestateapi.com/v1/SkipTrace"

    def enrich(self, data: WE.EnrichmentInput) -> WE.EnrichmentResult:
        first, last = _split_name(data.owner_name)
        req = {"address": data.street_address, "city": data.city, "state": data.state,
               "zip": data.zip_code, "first_name": first, "last_name": last,
               "mail_address": data.mailing_street, "mail_city": data.mailing_city,
               "mail_state": data.mailing_state, "mail_zip": data.mailing_zip}
        body = _http("POST", self.URL, headers={"x-api-key": self._env("REAPI_API_KEY")},
                     json={k: v for k, v in req.items() if v})
        ident = ((body.get("output") or {}).get("identity") or {})
        names = ident.get("names") or []
        if not body.get("match") or not (ident.get("phones") or ident.get("emails")):
            return WE.EnrichmentResult(status=WE.STATUS_NO_MATCH, provider=self.key, message="No match.",
                                       billable=False, provider_reference=body.get("requestId"),
                                       looked_up_at=_now_iso())
        full = (names[0].get("fullName") if names else None)
        agree = name_agreement(data.owner_name, full)
        evidence = {"name_match": agree, "vendor_match": bool(body.get("match")),
                    "vendor_cached": body.get("cached"), "credits": body.get("credits")}
        phones = [WE.EnrichmentPhone(
            number=_digits(ph.get("phone")), phone_type=_line(ph.get("phoneType")), source=self.key,
            provider_reference=ph.get("personId"), last_seen=ph.get("lastSeen"),
            dnc_flag=ph.get("doNotCall"), match_evidence=dict(evidence, connected=ph.get("isConnected")))
            for ph in ident.get("phones") or [] if _digits(ph.get("phone"))]
        emails = [WE.EnrichmentEmail(address=(e.get("email") or "").strip().lower(),
                                     provider_reference=e.get("personId"), match_evidence=evidence)
                  for e in ident.get("emails") or [] if e.get("email")]
        return WE.EnrichmentResult(
            status=WE.STATUS_SUCCEEDED, provider=self.key, phones=phones, emails=emails,
            owner_name=full, billable=True, cost_cents=self.costs[C.CONTACT_ENRICHMENT],
            provider_reference=body.get("requestId"), looked_up_at=_now_iso(),
            match_evidence=evidence, raw_summary="REAPI skip trace: %s name(s)" % len(names))


class DataSkipSkipTrace(VendorAdapter):
    key = "dataskip"
    label = "DataSkip skip trace"
    capabilities = (C.CONTACT_ENRICHMENT,)
    required_env = ("DATASKIP_API_TOKEN",)
    costs = {C.CONTACT_ENRICHMENT: 4}          # 4 cents per match, misses free (published)
    charge_on_miss = False
    price_confirmed = True
    price_note = "$0.04 per match, misses free, no subscription (vendor page)."
    public_url = "https://dataskip.io/skip-tracing-api"
    coverage = "US owner skip trace by property address"
    adapter_version = "dataskip/v1"
    URL = "https://app.dataskip.io/api/v1/skip-trace"

    def enrich(self, data: WE.EnrichmentInput) -> WE.EnrichmentResult:
        body = _http("POST", self.URL,
                     headers={"Authorization": "Bearer %s" % self._env("DATASKIP_API_TOKEN")},
                     json={"address": data.street_address, "city": data.city, "state": data.state,
                           "zip": data.zip_code})
        if not body.get("found"):
            return WE.EnrichmentResult(status=WE.STATUS_NO_MATCH, provider=self.key, message="No match.",
                                       billable=bool(body.get("charged")), looked_up_at=_now_iso())
        contact = body.get("contact") or {}
        full = contact.get("fullName")
        evidence = {"name_match": name_agreement(data.owner_name, full)}
        phones = [WE.EnrichmentPhone(number=_digits(ph.get("number")), phone_type=_line(ph.get("type")),
                                     source=self.key, dnc_flag=ph.get("dnc"), match_evidence=evidence)
                  for ph in body.get("phones") or [] if _digits(ph.get("number"))]
        emails = [WE.EnrichmentEmail(address=str(e).strip().lower(), match_evidence=evidence)
                  for e in body.get("emails") or [] if e]
        charged = body.get("charged")
        return WE.EnrichmentResult(
            status=WE.STATUS_SUCCEEDED if (phones or emails) else WE.STATUS_NO_MATCH,
            provider=self.key, phones=phones, emails=emails, owner_name=full,
            billable=True, cost_cents=int(round(float(charged) * 100)) if charged else self.costs[C.CONTACT_ENRICHMENT],
            looked_up_at=_now_iso(), match_evidence=evidence, raw_summary="DataSkip trace")


class TwilioLookupLineType(VendorAdapter):
    key = "twilio_lookup"
    label = "Twilio Lookup (line type)"
    capabilities = (C.PHONE_VALIDATION,)
    required_env = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")
    costs = {C.PHONE_VALIDATION: 1}            # $0.008 per request, rounded UP to 1 cent
    price_confirmed = True
    price_note = "$0.008 per Line Type Intelligence request (Twilio pricing); metered as 1 cent."
    public_url = "https://www.twilio.com/docs/lookup/v2-api/line-type-intelligence"
    coverage = "Line type / carrier for US numbers. Lookup only - sends nothing."
    adapter_version = "twilio_lookup/v2-lti"
    terms_note = ("EVALUATION ONLY. A lookup, never a message: nothing is sent to the number. "
                  "Uses the platform's Twilio account credentials.")

    def validate_phone(self, number: str) -> Dict[str, Any]:
        e164 = "+1" + _digits(number)
        body = _http("GET", "https://lookups.twilio.com/v2/PhoneNumbers/%s" % e164,
                     params={"Fields": "line_type_intelligence"},
                     auth=(self._env("TWILIO_ACCOUNT_SID"), self._env("TWILIO_AUTH_TOKEN")))
        if body.get("_status") == 404 or body.get("valid") is False:
            return {"line_type": None, "validation": "invalid", "carrier": None}
        lti = body.get("line_type_intelligence") or {}
        raw = (lti.get("type") or "").lower()
        line = {"mobile": "mobile", "landline": "landline", "fixedvoip": "voip",
                "nonfixedvoip": "voip"}.get(raw, "unknown" if raw else None)
        return {"line_type": line, "raw_type": lti.get("type"), "validation": "valid",
                "carrier": lti.get("carrier_name")}


def _split_name(name: Optional[str]):
    """Owner-of-record names come as 'LAST FIRST M' (DCAD) or 'First Last'.
    Only a person's name is split; an entity name yields (None, None)."""
    n = (name or "").strip()
    if not n or re.search(r"\b(LLC|INC|CORP|TRUST|LP|LTD|ESTATE|BANK|CHURCH|CITY|COUNTY)\b", n.upper()):
        return None, None
    if "," in n:
        last, first = [x.strip() for x in n.split(",", 1)]
        return (first.split() or [None])[0], last or None
    parts = n.split()
    if len(parts) < 2:
        return None, parts[0] if parts else None
    if n.isupper():                      # DCAD style: LAST FIRST [MIDDLE]
        return parts[1], parts[0]
    return parts[0], parts[-1]


# ── Sold comps ─────────────────────────────────────────────────────────────

class RealEstateApiComps(VendorAdapter):
    key = "reapi_comps"
    label = "RealEstateAPI comps"
    capabilities = (C.COMPS,)
    required_env = ("REAPI_API_KEY",)
    costs = {C.COMPS: 10}        # reported $0.10/property PAYG; UNCONFIRMED
    price_confirmed = False
    price_note = "Reported $0.10/property pay-as-you-go; UNCONFIRMED - set a cost override."
    public_url = "https://developer.realestateapi.com/reference/v3-comps-response-object"
    coverage = "US comparables; MLS fields where the vendor holds MLS data (DFW origin UNCONFIRMED)"
    adapter_version = "reapi/comps-v3"
    URL = "https://api.realestateapi.com/v3/PropertyComps"

    def search(self, capability: str, subject: Dict[str, Any]) -> List[Dict[str, Any]]:
        if capability != C.COMPS:
            return []
        address = ", ".join(x for x in (subject.get("street_address"), subject.get("city"),
                                        " ".join(y for y in (subject.get("state"), subject.get("zip_code")) if y))
                            if x)
        body = _http("POST", self.URL, headers={"x-api-key": self._env("REAPI_API_KEY")},
                     json={"address": address, "max_radius_miles": subject.get("max_radius_miles") or 1,
                           "max_days_back": subject.get("max_days_back") or 365,
                           "arms_length": True, "max_results": 25})
        state = (subject.get("state") or "").upper()
        return [reapi_comp(c, state) for c in body.get("comps") or []]


def reapi_comp(c: Dict[str, Any], subject_state: str) -> Dict[str, Any]:
    """One REAPI comp -> the evidence shape, with the price's ORIGIN stated.

    MLS closed    mlsSoldPrice + mlsLastSaleDate (vendor labels them MLS; for
                  DFW, that they come from NTREIS is UNCONFIRMED until written)
    Public record lastSaleAmount + lastSaleDate outside non-disclosure states
    Unverified    lastSaleAmount in a non-disclosure state (TX): origin unknown
    List / AVM    kept as reference, never a price"""
    addr = c.get("address") or {}
    state = (addr.get("state") or subject_state or "").upper()
    price, date, source = None, None, None
    if c.get("mlsSoldPrice") and c.get("mlsLastSaleDate"):
        price, date, source = c["mlsSoldPrice"], c["mlsLastSaleDate"], CR.PRICE_MLS_CLOSED
    elif c.get("lastSaleAmount") and c.get("lastSaleDate"):
        price, date = c["lastSaleAmount"], c["lastSaleDate"]
        source = CR.PRICE_UNVERIFIED_RECORD if state in NON_DISCLOSURE_STATES else CR.PRICE_PUBLIC_RECORD
    elif c.get("mlsListingPrice"):
        price, source = c.get("mlsListingPrice"), CR.PRICE_LIST
    return {
        "provider_record_id": c.get("id"),
        "street_address": addr.get("street") or addr.get("address"),
        "city": addr.get("city"), "state": state, "zip_code": addr.get("zip"),
        "sale_price": _num(price), "sale_date": date, "price_source": source,
        "square_feet": _num(c.get("squareFeet")), "bedrooms": _num(c.get("bedrooms")),
        "bathrooms": _num(c.get("bathrooms")), "year_built": _num(c.get("yearBuilt")),
        "lot_size_sqft": _num(c.get("lotSquareFeet")), "property_type": _ptype(c.get("propertyType")),
        "latitude": c.get("latitude"), "longitude": c.get("longitude"),
        "distance_miles": c.get("distance"),
        "sale_type": "arms_length" if c.get("preForeclosure") is False else None,
        "reference": {"avm": _num(c.get("estimatedValue")), "avm_type": "vendor AVM - never ARV",
                      "list_price": _num(c.get("mlsListingPrice"))},
    }


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _ptype(v):
    t = (v or "").upper()
    return {"SFR": "single_family", "CONDO": "condo", "MFR": "multi_family",
            "MOBILE": "mobile_home", "LAND": "land"}.get(t, (v or "").lower() or None)


class SandboxComps(AcquisitionProvider):
    """Synthetic comps for software testing of the COMPS evaluation mode. Only
    ever runs on test properties (the harness refuses a real one). Returns a
    deliberate mix of price origins so the classification is exercised."""
    scope = "sandbox"
    key = "sandbox_comps"
    label = "Sandbox comps"
    connector_kind = C.SANDBOX
    capabilities = (C.COMPS,)
    costs = {C.COMPS: 2}
    coverage = "Synthetic comparables with mixed price origins"

    def search(self, capability, subject):
        if capability != C.COMPS:
            return []
        from datetime import date, timedelta
        sq = _num(subject.get("square_feet")) or 1500
        base = 180.0
        out = []
        kinds = [CR.PRICE_MLS_CLOSED, CR.PRICE_MLS_CLOSED, CR.PRICE_MLS_CLOSED, CR.PRICE_MLS_CLOSED,
                 CR.PRICE_UNVERIFIED_RECORD, CR.PRICE_LIST, CR.PRICE_ESTIMATED]
        for i, kind in enumerate(kinds):
            psf = base + (i - 3) * 4
            out.append({"provider_record_id": "SBX-%s" % i,
                        "street_address": "%s Sandbox Comp Ln" % (100 + i), "city": subject.get("city"),
                        "state": subject.get("state"), "sale_price": round(psf * sq, -2),
                        "sale_date": (date.today() - timedelta(days=30 + 25 * i)).isoformat(),
                        "price_source": kind, "square_feet": sq, "bedrooms": _num(subject.get("bedrooms")) or 3,
                        "bathrooms": _num(subject.get("bathrooms")) or 2,
                        "year_built": _num(subject.get("year_built")) or 1985,
                        "property_type": subject.get("property_type") or "single_family",
                        "distance_miles": 0.2 + 0.1 * i, "sale_type": "arms_length",
                        "reference": {}})
        return out


VENDOR_PROVIDERS = (TracerfySkipTrace(), RealEstateApiSkipTrace(), DataSkipSkipTrace(),
                    TwilioLookupLineType(), RealEstateApiComps(), SandboxComps())
