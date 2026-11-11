"""PROJECT OVERDRIVE PHASE B - THE PROVIDER INTELLIGENCE NETWORK (#439).

ONE workflow over the three paid intelligence sources the owner is connecting,
built on what EvoSense already has (registry, health, budget ledger, ingest,
enrichment decisions) - not a second data system:

    ATTOM       property data: owner, parcel, characteristics, original
                recorded loan, assessment, AVM, sales history. A REAL adapter
                (providers.AttomPropertyData) that only a PERSON triggers, for
                one property, through `attom_lookup()` below.
    PROPSTREAM  no published developer API: exports are imported as CSV
                through the existing importer (propstream_csv + hunt.import_csv).
                IMPORT ONLY, always - never shown as connected.
    TRACERFY    owner phone / email: the existing integration
                (wholesale_enrichment.TracerfyProvider, used by EvoSense's
                enrichment decision chain and the "Get phones & emails" button).

What this module adds:
  * readiness(db, org_id)  - one honest card per provider, derived from the
    same config / health / ledger rows every other screen reads;
  * test_connection()      - a real, bounded probe that changes the status only
    on a verified answer (Tracerfy: account usage, no lookup; ATTOM: one call);
  * attom_lookup()         - the smart router for property data, in order:
        1 what the record already has        -> nothing missing: no call
        2 a free county record covers it     -> use that first: no call
        3 the exact fields still missing
        4 the ATTOM provider for those fields
        5 credentials / switch / price / pause / rate limit / cache / in-flight
        6 budget reservation (atomic), then a bounded call (max 4 requests)
        7 normalize -> ingest (vendor rank: never outranks county records)
        8 a plain result: what was found, what changed, what conflicts, what
          is still missing, what it cost;
  * property_intelligence() - the property screen's "where did each fact come
    from, is it current, what is missing, can a provider help" view.

STABILITY (PR #437): nothing here schedules anything. Every call is one
property, one person, bounded requests, and the HTTP client has a timeout.

NEVER: a CONNECTED badge without a verified answer; a credential value in a
payload or log; a vendor claim overwriting a county record; a paid call
without a configured price, a switch that is on and room in the budget.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.services.evosense import common as C
from app.services.evosense import providers as PV

# ── statuses (owner directive, Phase B §9) ────────────────────────────────
NOT_CONFIGURED = "NOT CONFIGURED"
READY_TO_TEST = "READY TO TEST"
CONNECTED = "CONNECTED"
DEGRADED = "DEGRADED"
RATE_LIMITED = "RATE LIMITED"
IMPORT_ONLY = "IMPORT ONLY"
UNAVAILABLE = "UNAVAILABLE"
ENTITLEMENT_MISSING = "ENTITLEMENT MISSING"
STATUSES = (NOT_CONFIGURED, READY_TO_TEST, CONNECTED, DEGRADED, RATE_LIMITED, IMPORT_ONLY, UNAVAILABLE,
            ENTITLEMENT_MISSING)
TONE = {NOT_CONFIGURED: "neutral", READY_TO_TEST: "attention", CONNECTED: "good", DEGRADED: "attention",
        RATE_LIMITED: "attention", IMPORT_ONLY: "info", UNAVAILABLE: "bad", ENTITLEMENT_MISSING: "bad"}

NETWORK = ("attom", "propstream", "tracerfy")
REGISTRY_KEY = {"attom": "attom", "tracerfy": "tracerfy_contact"}   # the EvoSense registry row
FAIL_AFTER = 3                      # consecutive failures before UNAVAILABLE

ATTOM_CACHE_DAYS = 30               # a profile younger than this is reused, never bought again
ATTOM_MAX_CALLS = 4                 # detail, assessment, avm, history
ATTOM_TEST_ADDRESS = {"address1": "4529 Winona Court", "address2": "Denver, CO"}   # ATTOM's own docs example
TRACERFY_BASE = "https://tracerfy.com/v1/api"
ATTOM_BASE = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/"
TIMEOUT = 15.0

# Fields ATTOM can supply, the property columns that hold them, and a label.
ATTOM_FIELDS = (
    ("owner", "Owner of record"),
    ("parcel_apn", "Parcel / APN"),
    ("characteristics", "Beds, baths, size, year built"),
    ("market_estimate", "Market estimate (AVM)"),
    ("transfer_history", "Sale / transfer history"),
    ("recorded_loan", "Recorded loan (original amount)"),
)
# the subset a free county appraisal record also answers
COUNTY_ANSWERS = {"owner", "parcel_apn", "characteristics"}

_INFLIGHT: set = set()
_INFLIGHT_LOCK = threading.Lock()


def _now() -> datetime:
    return C.now()


def _iso(dt) -> Optional[str]:
    return dt.isoformat() + "Z" if isinstance(dt, datetime) else None


def _ledger_month(db, org_id: str, provider_keys) -> Dict[str, int]:
    from sqlalchemy import func

    from app.models.evosense_models import EvoSenseCostEntry
    row = (db.query(func.count(EvoSenseCostEntry.id), func.coalesce(func.sum(EvoSenseCostEntry.total_cents), 0))
           .filter(EvoSenseCostEntry.organization_id == org_id,
                   EvoSenseCostEntry.provider_key.in_(tuple(provider_keys)),
                   EvoSenseCostEntry.period_month == C.month_key(),
                   EvoSenseCostEntry.status.in_(("charged", "failed_charged"))).first())
    return {"calls": int(row[0] or 0), "cents": int(row[1] or 0)}


def _packages(cfg) -> Dict[str, Any]:
    data = C.jload(getattr(cfg, "capability_health", None), {}) or {}
    return data.get("attom_packages") or {}


def _set_packages(cfg, packages: Dict[str, Any]) -> None:
    data = C.jload(getattr(cfg, "capability_health", None), {}) or {}
    data["attom_packages"] = packages
    cfg.capability_health = C.jdump(data)


def _attom_unit_cents(cfg) -> Optional[int]:
    """The workspace's contract price per ATTOM request (cents), or None when
    nobody has set it - in which case no paid ATTOM call is made."""
    ov = C.jload(getattr(cfg, "cost_overrides", None), {}) or {}
    v = ov.get(C.ASSESSOR)
    try:
        return None if v is None else max(0, int(v))
    except (TypeError, ValueError):
        return None


def _status_of(p, cfg, when: datetime) -> Dict[str, str]:
    """The Phase B status of a REAL provider for one workspace, from its
    config / health row. CONNECTED only after a verified answer."""
    if not p.is_configured():
        return {"status": NOT_CONFIGURED, "why": "The platform credential has not been added yet."}
    if cfg.rate_limited_until and cfg.rate_limited_until > when:
        return {"status": RATE_LIMITED,
                "why": "The provider asked us to slow down; calls resume after %s UTC."
                       % cfg.rate_limited_until.strftime("%Y-%m-%d %H:%M")}
    ok, bad = cfg.last_success_at, cfg.last_failure_at
    failed_last = bad is not None and (ok is None or bad > ok)
    reason = cfg.last_failure_reason or ""
    if failed_last:
        if reason.startswith("ENTITLEMENT"):
            return {"status": ENTITLEMENT_MISSING, "why": reason.split(":", 1)[-1].strip()}
        if (cfg.consecutive_failures or 0) >= FAIL_AFTER or "refused the credentials" in reason:
            return {"status": UNAVAILABLE, "why": reason or "Repeated failures."}
        return {"status": DEGRADED, "why": "Last attempt failed: %s" % (reason or "error")}
    pk = _packages(cfg)
    if pk and pk.get("detail", {}).get("state") == "not_entitled":
        return {"status": ENTITLEMENT_MISSING,
                "why": "The ATTOM account does not include the property detail / owner package."}
    if ok is not None:
        return {"status": CONNECTED, "why": "Verified %s UTC." % ok.strftime("%Y-%m-%d %H:%M")}
    return {"status": READY_TO_TEST, "why": "The credential is present. Run Test Connection to verify it."}


def _card_attom(db, org_id: str, when: datetime) -> Dict[str, Any]:
    p = PV.PROVIDERS["attom"]
    cfg = PV.config(db, org_id, "attom")
    st = _status_of(p, cfg, when)
    unit = _attom_unit_cents(cfg)
    pk = _packages(cfg)
    caps = []
    for name, label in (("detail", "Owner, parcel, characteristics, recorded loan"),
                        ("assessment", "Assessment and tax"), ("avm", "Market estimate (AVM)"),
                        ("history", "Sale / transfer history")):
        state = (pk.get(name) or {}).get("state") or "unknown"
        caps.append({"key": name, "label": label,
                     "state": {"ok": "included", "not_entitled": "not in your plan", "no_record": "included",
                               "unknown": "not checked yet"}.get(state, state)})
    caps.append({"key": "comps", "label": "Comparable sales",
                 "state": "not used: Texas sale amounts from ATTOM are not proof of a closed price"})
    missing = []
    if not p.is_configured():
        missing.append({"code": "credential", "text": "Add the ATTOM API key on the platform server.",
                        "env": list(p.required_env)})
    if not cfg.enabled:
        missing.append({"code": "switch", "text": "Turn ATTOM on for this workspace (Providers & Controls)."})
    if unit is None:
        missing.append({"code": "price", "text": "Set the price per ATTOM request from your contract "
                                                  "(enter 0 for a flat-rate plan). No paid call is made until it is set."})
    nxt = ("Add the credential, then Test Connection." if st["status"] == NOT_CONFIGURED
           else "Run Test Connection." if st["status"] == READY_TO_TEST
           else missing[0]["text"] if missing
           else "Open a property and use Get ATTOM property data." if st["status"] == CONNECTED
           else "Check the last failure, then Test Connection again.")
    return {
        "key": "attom", "label": "ATTOM", "role": "Property intelligence",
        "status": st["status"], "tone": TONE[st["status"]], "why": st["why"],
        "capabilities": caps, "missing": missing,
        "last_success_at": _iso(cfg.last_success_at), "last_failure_at": _iso(cfg.last_failure_at),
        "last_failure": cfg.last_failure_reason if cfg.last_failure_at else None,
        "coverage": p.coverage,
        "usage": _ledger_month(db, org_id, ("attom",)),
        "budget": _org_budget(db, org_id, unit_cents=unit),
        "enabled": bool(cfg.enabled),
        "available": st["status"] == CONNECTED and bool(cfg.enabled) and unit is not None,
        "test": {"available": p.is_configured(), "cost_note": "Uses one ATTOM request against your plan."},
        "how_it_runs": "Only when a person asks, for one property. Never part of a hunt or a scheduled job.",
        "next_action": nxt, "env": list(p.required_env),
    }


def _org_budget(db, org_id: str, *, unit_cents: Optional[int] = None) -> Dict[str, Any]:
    ctl = C.controls(db, org_id)
    from app.services.evosense import budget as B
    return {"daily_cents": ctl.org_daily_budget_cents, "monthly_cents": ctl.org_monthly_budget_cents,
            "remaining_cents": B.remaining(db, org_id, None), "unit_cents": unit_cents,
            "paused": bool(ctl.paused_all or ctl.paused_paid_data)}


def _card_tracerfy(db, org_id: str, when: datetime) -> Dict[str, Any]:
    from app.services import wholesale_enrichment as WE
    from app.services import wholesale_service as WS
    p = PV.PROVIDERS["tracerfy_contact"]
    cfg = PV.config(db, org_id, "tracerfy_contact")
    st = _status_of(p, cfg, when)
    settings = WS.resolve_settings(db, org_id, commit=False)
    daily = getattr(settings, "enrichment_daily_cap", None)
    monthly = getattr(settings, "enrichment_monthly_cap", None)
    used = _tracerfy_requests(db, org_id)
    missing = []
    if not p.is_configured():
        missing.append({"code": "credential", "text": "Add the Tracerfy API token on the platform server.",
                        "env": list(p.required_env)})
    if not cfg.enabled:
        missing.append({"code": "switch", "text": "Automatic owner lookups are off for this workspace "
                                                  "(manual \"Get phones & emails\" still works within the limits)."})
    if daily == 0 or monthly == 0:
        missing.append({"code": "limits", "text": "Paid lookup limits are 0 (off) in Wholesale Settings."})
    nxt = ("Add the credential, then Test Connection." if st["status"] == NOT_CONFIGURED
           else "Run Test Connection." if st["status"] == READY_TO_TEST
           else missing[0]["text"] if missing
           else "Use Enrich Contacts on a property, or Get phones & emails on a list.")
    return {
        "key": "tracerfy", "label": "Tracerfy", "role": "Contact intelligence",
        "status": st["status"], "tone": TONE[st["status"]], "why": st["why"],
        "capabilities": [{"key": "owner_trace", "label": "Owner phones and emails by property address",
                          "state": "instant lookup (answers at once; no background job)"},
                         {"key": "dnc", "label": "Do Not Call numbers", "state": "kept out of usable phones"}],
        "missing": missing,
        "last_success_at": _iso(cfg.last_success_at), "last_failure_at": _iso(cfg.last_failure_at),
        "last_failure": cfg.last_failure_reason if cfg.last_failure_at else None,
        "coverage": p.coverage,
        "usage": dict(_ledger_month(db, org_id, ("tracerfy_contact", "tracerfy")), **used),
        "budget": dict(_org_budget(db, org_id, unit_cents=WE.PROVIDERS["tracerfy"].cost_per_find_cents),
                       lookups_daily_cap=daily, lookups_monthly_cap=monthly),
        "enabled": bool(cfg.enabled),
        "available": st["status"] == CONNECTED,
        "test": {"available": p.is_configured(),
                 "cost_note": "Reads your Tracerfy account usage and balance. No lookup is run, no credits are used."},
        "how_it_runs": ("Charged only when an owner is found ($0.10). Existing contacts, an owner already looked "
                        "up and a recent miss are reused, never bought again."),
        "next_action": nxt, "env": list(p.required_env),
        "shared_account_note": ("One Tracerfy account serves the platform. Each workspace has its own switch, "
                                "limits, ledger and results; one workspace never sees another's."),
    }


def _tracerfy_requests(db, org_id: str) -> Dict[str, int]:
    from app.models.wholesale_models import WholesaleEnrichmentRequest
    since = _now() - timedelta(days=30)
    q = (db.query(WholesaleEnrichmentRequest)
         .filter(WholesaleEnrichmentRequest.organization_id == org_id,
                 WholesaleEnrichmentRequest.billable.is_(True),
                 WholesaleEnrichmentRequest.created_at >= since))
    return {"billable_lookups_30d": q.count()}


def _card_propstream(db, org_id: str) -> Dict[str, Any]:
    from app.models.evosense_models import EvoSenseEvent
    last = None
    for ev in (db.query(EvoSenseEvent)
               .filter(EvoSenseEvent.organization_id == org_id, EvoSenseEvent.action == "import.csv")
               .order_by(EvoSenseEvent.created_at.desc()).limit(25).all()):
        det = C.jload(ev.details, {}) or {}
        if "propstream" in str(det.get("list_source") or "").lower() or det.get("format") == "propstream":
            last = {"at": _iso(ev.created_at), "summary": ev.summary,
                    "counts": {k: det.get(k) for k in ("rows", "created", "merged", "seen", "review", "rejected",
                                                       "conflicts")}}
            break
    return {
        "key": "propstream", "label": "PropStream", "role": "Investor property research",
        "status": IMPORT_ONLY, "tone": TONE[IMPORT_ONLY],
        "why": "PropStream does not publish a developer API. Export a list from PropStream and import the CSV.",
        "capabilities": [{"key": "csv", "label": "CSV export import (any PropStream template)", "state": "available"},
                         {"key": "api", "label": "Direct API", "state": "not offered by PropStream"}],
        "missing": [],
        "last_success_at": last["at"] if last else None, "last_failure_at": None, "last_failure": None,
        "last_import": last,
        "coverage": "Whatever your PropStream export contains; every value is recorded as the export's claim.",
        "usage": None, "budget": None, "enabled": True, "available": True,
        "test": {"available": False, "cost_note": "Nothing to test: there is no connection, only file imports."},
        "how_it_runs": ("Preview first (nothing written), then import. Same property matching, conflict and "
                        "review rules as every other source; county records are never overwritten."),
        "next_action": "Import a PropStream CSV in the Discovery Inbox (Import list).",
        "import_path": "/wholesale/evosense/inbox", "env": [],
    }


def readiness(db, org_id: str) -> Dict[str, Any]:
    when = _now()
    cards = [_card_attom(db, org_id, when), _card_propstream(db, org_id), _card_tracerfy(db, org_id, when)]
    return {"providers": cards, "statuses": list(STATUSES), "as_of": _iso(when),
            "stability_note": ("No provider runs on a schedule from here. Bulk and recurring provider jobs stay "
                               "off until the backend memory fix (PR #437) is approved and live."),
            "notice": "CONNECTED appears only after a real, successful answer from the provider."}


# ── TEST CONNECTION ─────────────────────────────────────────────────────────

def _http_get(url: str, *, headers=None, params=None):
    import httpx
    return httpx.get(url, headers=headers, params=params, timeout=TIMEOUT)


def test_connection(db, org_id: str, key: str, user=None) -> Dict[str, Any]:
    """One bounded probe. Records success or failure on the workspace's row;
    never returns or logs a credential."""
    import os

    import httpx
    if key == "propstream":
        return {"key": key, "ok": False, "status": IMPORT_ONLY,
                "message": "PropStream has no connection to test - it is imported from CSV exports."}
    if key not in REGISTRY_KEY:
        raise ValueError("Unknown provider")
    p = PV.PROVIDERS[REGISTRY_KEY[key]]
    cfg = PV.config(db, org_id, p.key)
    if not p.is_configured():
        return {"key": key, "ok": False, "status": NOT_CONFIGURED,
                "message": "The platform credential has not been added, so nothing was called."}
    cfg.last_attempt_at = _now()
    detail: Dict[str, Any] = {}
    try:
        if key == "tracerfy":
            r = _http_get(TRACERFY_BASE + "/analytics/",
                          headers={"Authorization": "Bearer %s" % os.environ.get("TRACERFY_API_TOKEN", "")})
        else:
            r = _http_get(ATTOM_BASE + "property/detail",
                          headers={"APIKey": os.environ.get("ATTOM_API_KEY", ""), "Accept": "application/json"},
                          params=ATTOM_TEST_ADDRESS)
            _record_attom_calls(db, org_id, cfg, 1, operation="attom_test")
    except httpx.TimeoutException:
        PV.record_failure(cfg, "TIMEOUT: %s did not answer within %ss" % (p.label, int(TIMEOUT)))
        db.flush()
        return _test_result(key, cfg, False, "%s did not answer in time." % p.label)
    except httpx.HTTPError as exc:
        PV.record_failure(cfg, "%s: %s" % (type(exc).__name__, str(exc)[:120]))
        db.flush()
        return _test_result(key, cfg, False, "Could not reach %s." % p.label)
    code = r.status_code
    if code == 429:
        ra = r.headers.get("Retry-After")
        secs = int(ra) if ra and ra.isdigit() else 300
        PV.record_failure(cfg, "rate limited (429)", rate_limited_for=secs)
        db.flush()
        return _test_result(key, cfg, False, "%s asked us to slow down. Try again in a few minutes." % p.label)
    if code in (401, 403):
        PV.record_failure(cfg, "%s refused the credentials (%s)" % (p.label, code))
        db.flush()
        return _test_result(key, cfg, False, "%s refused the credential (%s). Check the key." % (p.label, code))
    if key == "attom" and code in (200, 404):
        # 404 = the key was accepted and the sample address has no record: still a verified connection.
        pass
    elif code >= 400:
        PV.record_failure(cfg, "%s answered %s" % (p.label, code))
        db.flush()
        return _test_result(key, cfg, False, "%s answered with an error (%s)." % (p.label, code))
    if key == "tracerfy":
        try:
            body = r.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and body.get("balance") is not None:
            detail["credit_balance"] = body.get("balance")
    PV.record_success(cfg)
    cfg.last_verified_at = _now()
    C.log_event(db, org_id, "provider.tested", user=user, actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                summary="%s connection verified" % p.label, details={"provider": key})
    db.flush()
    return _test_result(key, cfg, True, "%s answered. The connection works." % p.label, detail)


def _test_result(key, cfg, ok, message, detail=None):
    st = _status_of(PV.PROVIDERS[REGISTRY_KEY[key]], cfg, _now())
    return {"key": key, "ok": ok, "status": st["status"], "why": st["why"], "message": message,
            "detail": detail or {}}


def _record_attom_calls(db, org_id, cfg, n: int, *, operation: str, property_id=None,
                        is_test: bool = False) -> None:
    """Usage units for ATTOM requests that did not go through a reservation
    (Test Connection). Priced at the workspace's contract price when set."""
    from app.models.evosense_models import EvoSenseCostEntry
    unit = _attom_unit_cents(cfg) or 0
    db.add(EvoSenseCostEntry(organization_id=org_id, provider_key="attom", connector_kind=C.REAL,
                             capability=C.ASSESSOR, operation=operation, property_id=property_id,
                             quantity=n, unit_cost_cents=unit, total_cents=unit * n, status="charged",
                             success=True, period_day=C.day_key(), period_month=C.month_key(), is_test=is_test))


# ── WHAT A PROPERTY ALREADY HAS ─────────────────────────────────────────────

def _current_owner(db, prop):
    from app.services.evosense import contacts as CT
    return CT.primary_owner(db, prop)


def attom_gaps(db, prop) -> Dict[str, Dict[str, Any]]:
    """For each field ATTOM can supply: what the record already holds and
    from where. `have` False means it is missing."""
    owner = _current_owner(db, prop)
    out = {
        "owner": {"have": owner is not None, "value": getattr(owner, "display_name", None)},
        "parcel_apn": {"have": bool(prop.parcel_apn), "value": prop.parcel_apn},
        "characteristics": {"have": bool(prop.square_feet and prop.year_built),
                            "value": ", ".join(x for x in (
                                "%s sq ft" % prop.square_feet if prop.square_feet else None,
                                "built %s" % prop.year_built if prop.year_built else None) if x) or None},
        "market_estimate": {"have": prop.estimated_value is not None, "value": prop.estimated_value,
                            "source": prop.estimated_value_source},
        "transfer_history": {"have": prop.last_deed_transfer_date is not None,
                             "value": str(prop.last_deed_transfer_date) if prop.last_deed_transfer_date else None},
        "recorded_loan": {"have": prop.mortgage_balance is not None, "value": prop.mortgage_balance,
                          "source": prop.mortgage_source},
    }
    for k, label in ATTOM_FIELDS:
        out[k]["label"] = label
    return out


def _cached_attom(db, org_id: str, prop):
    from app.models.evosense_models import EvoSenseObservation
    return (db.query(EvoSenseObservation)
            .filter(EvoSenseObservation.organization_id == org_id,
                    EvoSenseObservation.property_id == prop.id,
                    EvoSenseObservation.provider_key == "attom")
            .order_by(EvoSenseObservation.observed_at.desc()).first())


def _free_county_source(db, org_id: str, prop):
    """A free, operational county record source that covers this property's
    county - checked BEFORE any paid ATTOM call."""
    county = (prop.county or "").strip().title()
    if not county:
        return None
    for p, cfg, cost in PV.route(db, org_id, C.ASSESSOR, sandbox_allowed=bool(prop.is_test)):
        if cost != 0 or p.connector_kind != C.REAL:
            continue
        if p.scope in ("county", "city") and county not in tuple(p.counties or ()):
            continue
        st = PV.canonical(p, cfg)
        if st["state"] == PV.S_HEALTHY:
            return p
    return None


def _decision(decision: str, message: str, steps: List[Dict[str, Any]], **extra) -> Dict[str, Any]:
    out = {"decision": decision, "message": message, "steps": steps, "called": False}
    out.update(extra)
    return out


# ── THE SMART ROUTER FOR PROPERTY DATA ──────────────────────────────────────

def attom_plan(db, org_id: str, prop, *, force: bool = False) -> Dict[str, Any]:
    """Steps 1-5 of the router, with NO call and NO spend. `attom_lookup` runs
    this first; the property screen shows it so a person sees why a lookup
    would or would not happen before pressing anything."""
    steps: List[Dict[str, Any]] = []
    gaps = attom_gaps(db, prop)
    missing = [k for k, v in gaps.items() if not v["have"]]
    steps.append({"step": "existing", "ok": True,
                  "text": "On file: %s." % (", ".join(gaps[k]["label"].lower() for k in gaps if gaps[k]["have"])
                                            or "nothing ATTOM could add")})
    if not missing and not force:
        return _decision("USE_EXISTING", "Everything ATTOM could add is already on file. No lookup needed.",
                         steps, missing=[], gaps=gaps)
    free = _free_county_source(db, org_id, prop)
    if free is not None and set(missing) <= COUNTY_ANSWERS and not force:
        steps.append({"step": "county", "ok": True, "text": "%s covers %s County for free." % (free.label, prop.county)})
        return _decision("USE_FREE_SOURCE", "The free county record (%s) supplies what is missing. Refresh it "
                                            "first; ATTOM is not needed." % free.label, steps,
                         missing=missing, gaps=gaps, free_source=free.key)
    steps.append({"step": "county", "ok": free is None or not set(missing) <= COUNTY_ANSWERS,
                  "text": "No free county source can fill these." if free is None
                  else "The county record cannot supply everything missing."})
    steps.append({"step": "gaps", "ok": True,
                  "text": "Missing: %s." % ", ".join(gaps[k]["label"].lower() for k in missing) if missing
                  else "Nothing missing - a refresh was requested."})
    p = PV.PROVIDERS["attom"]
    cfg = PV.config(db, org_id, "attom")
    ctl = C.controls(db, org_id)
    blocks = []
    if not p.is_configured():
        blocks.append(("NOT_CONFIGURED", "ATTOM is not connected yet (the platform credential is missing)."))
    if not cfg.enabled:
        blocks.append(("SWITCHED_OFF", "ATTOM is switched off for this workspace (Providers & Controls)."))
    if _attom_unit_cents(cfg) is None:
        blocks.append(("NO_PRICE", "No price per ATTOM request is set for this workspace, so no paid call is made."))
    if ctl.paused_all or ctl.paused_paid_data:
        blocks.append(("PAUSED", "Paid data is paused for this workspace."))
    if cfg.rate_limited_until and cfg.rate_limited_until > _now():
        blocks.append(("RATE_LIMITED", "ATTOM asked us to slow down until %s UTC."
                       % cfg.rate_limited_until.strftime("%H:%M")))
    st = _status_of(p, cfg, _now())
    if st["status"] in (UNAVAILABLE, ENTITLEMENT_MISSING):
        blocks.append((st["status"].replace(" ", "_"), st["why"]))
    cached = _cached_attom(db, org_id, prop)
    if cached is not None and cached.observed_at and cached.observed_at > _now() - timedelta(days=ATTOM_CACHE_DAYS):
        steps.append({"step": "cache", "ok": True, "text": "ATTOM answered for this property on %s; that "
                                                           "answer is reused." % cached.observed_at.strftime("%b %d")})
        return _decision("CACHED", "ATTOM was already asked about this property on %s. The saved answer is used; "
                                   "nothing is bought again." % cached.observed_at.strftime("%b %d"),
                         steps, missing=missing, gaps=gaps, observation_id=cached.id)
    with _INFLIGHT_LOCK:
        busy = (org_id, prop.id) in _INFLIGHT
    if busy:
        blocks.append(("IN_FLIGHT", "An ATTOM lookup for this property is already running."))
    unit = _attom_unit_cents(cfg)
    steps.append({"step": "checks", "ok": not blocks,
                  "text": " ".join(b[1] for b in blocks) if blocks else
                  "Credential, switch, price, pause, rate limit and duplicates all clear."})
    if blocks:
        return _decision(blocks[0][0], blocks[0][1], steps, missing=missing, gaps=gaps,
                         blocked=[{"code": c, "text": t} for c, t in blocks])
    return _decision("READY", "ATTOM can be asked about this property.", steps, missing=missing, gaps=gaps,
                     max_cost_cents=(unit or 0) * ATTOM_MAX_CALLS, max_calls=ATTOM_MAX_CALLS)


def _attom_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.evosense.vendors import AttomComps
    return AttomComps()._get(path, params)


def attom_lookup(db, org_id: str, prop, user=None, *, force: bool = False) -> Dict[str, Any]:
    """Steps 6-8: reserve, call (bounded), normalize, ingest, settle."""
    from app.services.evosense import budget as B
    from app.services.evosense import ingest as IN
    from app.services.evosense.vendors import AttomComps, attom_profile
    plan = attom_plan(db, org_id, prop, force=force)
    if plan["decision"] != "READY":
        C.log_event(db, org_id, "provider.attom.skipped", property_id=prop.id, user=user,
                    actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM, is_test=prop.is_test,
                    summary="ATTOM lookup not made: %s" % plan["message"][:180],
                    details={"decision": plan["decision"]})
        return plan
    steps = plan["steps"]
    cfg = PV.config(db, org_id, "attom")
    unit = _attom_unit_cents(cfg) or 0
    key = (org_id, prop.id)
    with _INFLIGHT_LOCK:
        if key in _INFLIGHT:
            return _decision("IN_FLIGHT", "An ATTOM lookup for this property is already running.", steps)
        _INFLIGHT.add(key)
    try:
        ok, refusal, entry = B.reserve(db, org_id, None, unit * ATTOM_MAX_CALLS, provider_key="attom",
                                       connector_kind=C.REAL, capability=C.ASSESSOR, operation="attom_profile",
                                       property_id=prop.id, is_test=bool(prop.is_test))
        if not ok:
            steps.append({"step": "budget", "ok": False, "text": refusal})
            return _decision("BUDGET_BLOCKED", "Budget: %s. Nothing was bought." % refusal, steps,
                             missing=plan["missing"], gaps=plan["gaps"])
        steps.append({"step": "budget", "ok": True,
                      "text": "Reserved up to %s (%s requests)." % (C.money(unit * ATTOM_MAX_CALLS), ATTOM_MAX_CALLS)})
        subject = {"street_address": prop.street_address, "city": prop.city, "state": prop.state,
                   "zip_code": prop.zip_code}
        addr = AttomComps._address(subject)
        raw: Dict[str, Any] = {"provider": "attom", "at": datetime.utcnow().isoformat() + "Z", "missing": []}
        packages = dict(_packages(cfg))
        calls = 0
        failure = None
        for name, path in PV.PROVIDERS["attom"].PACKAGES:
            try:
                calls += 1
                body = _attom_get(path, addr)
            except PV.ProviderRateLimited as exc:
                cfg.rate_limited_until = _now() + timedelta(seconds=exc.retry_after_seconds)
                failure = ("RATE_LIMITED", "ATTOM asked us to slow down. Try again later.")
                PV.record_failure(cfg, "rate limited (429)", rate_limited_for=exc.retry_after_seconds)
                break
            except PV.ProviderTimeout:
                failure = ("TIMEOUT", "ATTOM did not answer in time.")
                PV.record_failure(cfg, "TIMEOUT: ATTOM did not answer within the time limit")
                break
            except PV.ProviderFailure as exc:
                msg = str(exc)
                if "refused the credentials" in msg:
                    if name == "detail" and not any((packages.get(n) or {}).get("state") == "ok" for n in packages):
                        # the key itself is refused, or the core package is not licensed
                        packages[name] = {"state": "not_entitled", "at": _iso(_now())}
                        failure = ("ENTITLEMENT_MISSING",
                                   "ATTOM refused the request for property detail. Either the key is wrong or "
                                   "the plan does not include this package.")
                        PV.record_failure(cfg, "ENTITLEMENT: ATTOM refused the property detail / owner package")
                        break
                    packages[name] = {"state": "not_entitled", "at": _iso(_now())}
                    raw["missing"].append(name)
                    continue
                failure = ("PROVIDER_FAILED", "ATTOM failed: %s" % msg[:160])
                PV.record_failure(cfg, msg[:200])
                break
            rows = [] if body.get("_status") == 404 else (body.get("property") or [])
            if not rows:
                packages[name] = {"state": "no_record", "at": _iso(_now())}
                raw["missing"].append(name)
                continue
            packages[name] = {"state": "ok", "at": _iso(_now())}
            raw[name] = rows[0]
        _set_packages(cfg, packages)
        # settle to the requests actually made; the rest of the reservation is released
        B.settle(db, entry, unit * calls, success=failure is None)
        entry.quantity, entry.unit_cost_cents = calls, unit
        steps.append({"step": "call", "ok": failure is None,
                      "text": "%s ATTOM request%s made, %s." % (calls, "" if calls == 1 else "s", C.money(unit * calls))})
        if failure is not None and not any(k in raw for k in ("detail", "assessment", "avm", "history")):
            db.flush()
            return _decision(failure[0], failure[1], steps, called=True, cost_cents=unit * calls,
                             calls=calls, missing=plan["missing"], gaps=plan["gaps"])
        profile = attom_profile(raw, (prop.state or "").upper())
        conflicts_before = len(C.jload(prop.conflicts, []) or [])
        rec = _attom_record(prop, profile, raw)
        outcome, attached = IN.ingest(db, org_id, PV.PROVIDERS["attom"], C.ASSESSOR, rec, target=prop,
                                      user=user, is_test=bool(prop.is_test), cost_cents=unit * calls,
                                      ledger_id=entry.id)
        if failure is None:
            PV.record_success(cfg, C.ASSESSOR)
        conflicts_after = len(C.jload(prop.conflicts, []) or [])
        gaps_after = attom_gaps(db, prop)
        filled = [gaps_after[k]["label"] for k in plan["missing"] if gaps_after[k]["have"]]
        still = [gaps_after[k]["label"] for k, v in gaps_after.items() if not v["have"]]
        steps.append({"step": "record", "ok": True,
                      "text": ("Held for identity review - ATTOM's address did not clearly match this property."
                               if outcome == "review" else
                               "Recorded as ATTOM's claim; county records keep precedence.")})
        C.log_event(db, org_id, "provider.attom.lookup", property_id=prop.id, user=user,
                    actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM, is_test=prop.is_test,
                    summary="ATTOM property data: %s request(s), %s filled, %s new conflict(s)"
                            % (calls, len(filled), max(0, conflicts_after - conflicts_before)),
                    details={"calls": calls, "cost_cents": unit * calls, "filled": filled,
                             "packages": packages, "outcome": outcome})
        db.flush()
        return {"decision": "LOOKED_UP" if outcome != "review" else "REVIEW", "called": True,
                "message": ("ATTOM answered. %s filled; %s new conflict%s kept for review."
                            % (len(filled), max(0, conflicts_after - conflicts_before),
                               "" if conflicts_after - conflicts_before == 1 else "s")) if outcome != "review"
                else "ATTOM answered for an address that did not clearly match; it waits in identity review.",
                "steps": steps, "calls": calls, "cost_cents": unit * calls, "filled": filled, "still_missing": still,
                "new_conflicts": max(0, conflicts_after - conflicts_before), "packages": packages,
                "profile": _public_profile(profile), "partial_failure": failure[1] if failure else None}
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.discard(key)


def _attom_record(prop, profile: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    """ATTOM's answer in ingest's record shape. Only what ATTOM itself said;
    the ORIGINAL recorded loan is kept in the evidence but never becomes a
    mortgage BALANCE, and Texas sale amounts never become a price."""
    det = raw.get("detail") or {}
    addr = det.get("address") or {}
    ch = profile.get("characteristics") or {}
    ident = profile.get("identifier") or {}
    hist = [h for h in profile.get("sales_history") or [] if h.get("date")]
    rec: Dict[str, Any] = {
        "source_reference": "attom:profile:%s" % (ident.get("attom_id") or prop.id),
        "street_address": addr.get("line1") or prop.street_address,
        "city": addr.get("locality") or prop.city,
        "state": (addr.get("countrySubd") or prop.state or "")[:2] or None,
        "zip_code": addr.get("postal1") or prop.zip_code,
        "parcel_apn": ident.get("apn"),
        "square_feet": ch.get("square_feet"), "bedrooms": ch.get("bedrooms"), "bathrooms": ch.get("bathrooms"),
        "year_built": ch.get("year_built"),
        "_raw": raw, "_adapter_version": PV.PROVIDERS["attom"].adapter_version,
        "_source_updated_at": (profile.get("avm") or {}).get("date"),
        "attom": _public_profile(profile),
        "signals": [],
    }
    if ch.get("property_type"):
        rec["property_type"] = ch["property_type"]
    if profile.get("owner_name"):
        rec["owner"] = {"name": profile["owner_name"]}
    avm = profile.get("avm") or {}
    if avm.get("value"):
        rec["valuation"] = {"value": avm["value"], "basis": "ATTOM AVM - vendor estimate, never an ARV"}
    if hist:
        rec["deed_transfer_date"] = sorted(hist, key=lambda h: str(h["date"]))[-1]["date"]
    return rec


def _public_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("identifier", "characteristics", "owner_name", "owner_mailing", "mortgage", "assessment", "avm",
            "sales_history", "texas_rule", "missing", "at")
    return {k: profile.get(k) for k in keep if k in profile}


# ── THE PROPERTY SCREEN'S VIEW ──────────────────────────────────────────────

def property_intelligence(db, org_id: str, prop) -> Dict[str, Any]:
    """Where each key fact came from, whether it is current, what is missing,
    which provider could help and what would block it. Read-only."""
    from app.models.evosense_models import EvoSenseEnrichmentDecision
    gaps = attom_gaps(db, prop)
    plan = attom_plan(db, org_id, prop)
    cached = _cached_attom(db, org_id, prop)
    attom_seen = None
    if cached is not None:
        pl = C.jload(cached.payload, {}) or {}
        attom_seen = {"at": _iso(cached.observed_at), "observation_id": cached.id,
                      "profile": pl.get("attom"), "match": cached.match_type,
                      "current": bool(cached.observed_at and cached.observed_at > _now() - timedelta(days=ATTOM_CACHE_DAYS))}
    lookups = (db.query(EvoSenseEnrichmentDecision)
               .filter(EvoSenseEnrichmentDecision.organization_id == org_id,
                       EvoSenseEnrichmentDecision.property_id == prop.id)
               .order_by(EvoSenseEnrichmentDecision.created_at.desc()).limit(5).all())
    rd = {c["key"]: c for c in readiness(db, org_id)["providers"]}
    sources = []
    for k, label in ATTOM_FIELDS:
        g = gaps[k]
        src = g.get("source")
        if k == "owner" and g["have"]:
            src = "owner of record"
        sources.append({"key": k, "label": label, "have": g["have"], "value": g.get("value"),
                        "source": src if g["have"] else None})
    conflicts = C.jload(prop.conflicts, []) or []
    return {
        "facts": sources,
        "conflicts_open": len([c for c in conflicts if c.get("status", "open") == "open"]),
        "attom": {"status": rd["attom"]["status"], "plan": {k: plan[k] for k in ("decision", "message", "steps")
                                                              if k in plan},
                  "can_lookup": plan["decision"] == "READY", "max_cost_cents": plan.get("max_cost_cents"),
                  "last": attom_seen},
        "tracerfy": {"status": rd["tracerfy"]["status"], "enabled": rd["tracerfy"]["enabled"],
                     "lookups": [{"at": _iso(x.created_at), "decision": x.decision, "outcome": x.outcome,
                                  "provider": x.provider_key, "cost_cents": x.estimated_cost_cents,
                                  "reason": (C.jload(x.reasons, []) or [None])[0]} for x in lookups]},
        "propstream": {"status": IMPORT_ONLY},
        "rule": "County records outrank vendor data. A different value is kept as a conflict, never overwritten.",
    }
