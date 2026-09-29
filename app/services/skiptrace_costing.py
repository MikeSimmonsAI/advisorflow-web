"""Skip-trace economics - LOWEST TOTAL COST for the actual volume, shown
BEFORE any paid run, and a confirmation gate a paid run must pass.

NOTHING IN THIS MODULE CALLS A VENDOR. It has no HTTP client import. It prices,
dedupes, ranks and records human confirmation; executing a paid run is the job
of a trigger path that has passed `gate_paid_run()` (see skiptrace_adapters.py
for the adapter interface, which also never sends without an explicit
transport + a confirmed estimate).

WHY THE OLD EFFECTIVE COST WAS ~$0.10
  The Tracerfy adapter (app/services/evosense/vendors.py, `tracerfy`) calls the
  INSTANT single-record API `POST https://tracerfy.com/v1/api/trace/lookup/`
  with `find_owner: true`. Tracerfy's API docs price that endpoint at 5 credits
  per hit (0 on a miss); credits are $0.02 pay-as-you-go -> $0.10 per HIT.
  Tracerfy's Normal trace is 1 credit ($0.02) and Advanced 2 credits ($0.04),
  but those are the BATCH (async: submit -> queue -> webhook -> CSV) products.
  So the integration pays the 5x synchronous premium on every hit. For a
  250-500 record pilot nothing needs sub-second answers: the batch Normal trace
  is the cheaper documented product.

CATALOGUE HONESTY
  Every price carries `source_url` + `verified_at` (the date the page was read)
  and a `price_status`:
    vendor_published      read on the vendor's own page on that date
    third_party_reported  read on a review / comparison site - owner to confirm
    unverified            not read by us at all - owner to confirm
  A value nobody published stays NULL. `expected_hit_rate` is NULL for every
  vendor (no vendor publishes a verified rate for our records); the estimator
  uses a stated PLANNING ASSUMPTION the caller can override, and also shows the
  MAXIMUM (every record a hit / every request charged).

  Derrick's reported ~$0.01/record is carried as a BENCHMARK row whose provider
  is not identified. It is never recommended and never priced as a real
  product; the config-driven custom adapter slot is where his provider plugs in
  once the owner names it (SKIPTRACE_CUSTOM_* env, disabled by default).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List, Optional

CATALOGUE_VERSION = "2026-09-28.xe1"
DEFAULT_HIT_RATE = Decimal("0.70")          # PLANNING ASSUMPTION, never presented as measured
FRESHNESS_DAYS = 90                         # a property traced within this window is not bought again
ESTIMATE_TTL_HOURS = 24
PAID_RUN_MAX_RECORDS = 5000

TRACERFY_PRODUCT_ENV = "TRACERFY_TRACE_PRODUCT"     # normal (default) | advanced | instant

HIT_RATE_NOTE = ("Hit rate is a planning assumption (%s%%), not a measured or vendor-published rate. "
                 "The maximum assumes every billable record is charged.")

# Egress note, recorded once: on 2026-09-28 tracerfy.com was not reachable from
# the build environment (proxy policy), so Tracerfy rows carry the 2026-09-27
# reading recorded in handoff/PROVIDER_SELECTION_PACKAGE.md.

_TRACERFY_DOCS = "https://www.tracerfy.com/skip-tracing-api-documentation/"
_TRACERFY_PRICING = "https://www.tracerfy.com/pricing"

CATALOGUE: List[Dict[str, Any]] = [
    # ── Tracerfy ───────────────────────────────────────────────────────────
    {"provider": "tracerfy", "product": "instant", "label": "Tracerfy instant API (single lookup)",
     "endpoint_label": "POST https://tracerfy.com/v1/api/trace/lookup/ (find_owner=true), synchronous",
     "cost_per_hit_cents": "10", "cost_per_request_cents": "0", "misses_charged": False,
     "monthly_minimum_cents": "0", "minimum_kind": "none", "minimum_purchase_cents": "2000",
     "batch_supported": False, "webhook_supported": False, "api_available": True,
     "credential_env": "TRACERFY_API_TOKEN", "price_status": "vendor_published",
     "source_url": _TRACERFY_DOCS, "verified_at": "2026-09-27", "is_current": True,
     "notes": ("THE PRODUCT THE INTEGRATION USES TODAY (evosense/vendors.py `tracerfy`, and the PLANNED "
               "evaluation 078a8709). 5 credits x $0.02 = $0.10 per hit, 0 credits on a miss (API docs). "
               "$20 minimum credit purchase (one-time, not monthly). No retry, no idempotency key.")},
    {"provider": "tracerfy", "product": "normal_batch", "label": "Tracerfy Normal trace (batch)",
     "endpoint_label": ("Tracerfy batch trace, trace type 'normal' - async: submit CSV -> queue -> webhook/poll "
                        "-> results. Exact path + field names: owner to confirm against the API docs."),
     "cost_per_hit_cents": "2", "cost_per_request_cents": "0", "misses_charged": True,
     "monthly_minimum_cents": "0", "minimum_kind": "none", "minimum_purchase_cents": "2000",
     "batch_supported": True, "webhook_supported": True, "api_available": True,
     "credential_env": "TRACERFY_API_TOKEN", "price_status": "vendor_published",
     "source_url": _TRACERFY_PRICING, "verified_at": "2026-09-27",
     "notes": ("$0.02 per hit on the pricing page ('misses free'); the API docs say batch is 1 credit per LEAD. "
               "Until Tracerfy confirms in writing, this catalogue assumes the WORST case: every uploaded "
               "record is charged. Plans $700 / $1,500 / $3,000 per month exist; their per-credit rate was "
               "not published where we read it.")},
    {"provider": "tracerfy", "product": "advanced_batch", "label": "Tracerfy Advanced trace (batch)",
     "endpoint_label": "Tracerfy batch trace, trace type 'advanced' (async, same flow as Normal)",
     "cost_per_hit_cents": "4", "cost_per_request_cents": "0", "misses_charged": True,
     "monthly_minimum_cents": "0", "minimum_kind": "none", "minimum_purchase_cents": "2000",
     "batch_supported": True, "webhook_supported": True, "api_available": True,
     "credential_env": "TRACERFY_API_TOKEN", "price_status": "vendor_published",
     "source_url": _TRACERFY_PRICING, "verified_at": "2026-09-27",
     "notes": "2 credits per lead ($0.04). Same per-lead vs per-hit conflict as Normal; worst case assumed."},
    # ── DataSkip ───────────────────────────────────────────────────────────
    {"provider": "dataskip", "product": "single", "label": "DataSkip API (single)",
     "endpoint_label": "POST https://app.dataskip.io/api/v1/skip-trace",
     "cost_per_hit_cents": "4", "cost_per_request_cents": "0", "misses_charged": False,
     "monthly_minimum_cents": "0", "minimum_kind": "none",
     "batch_supported": False, "webhook_supported": None, "api_available": True,
     "credential_env": "DATASKIP_API_TOKEN", "price_status": "vendor_published",
     "source_url": "https://dataskip.io/skip-tracing-api", "verified_at": "2026-09-28",
     "notes": "'A flat 4c per matched lookup'; a miss returns found:false and 'charges nothing'; no subscription, no minimum."},
    {"provider": "dataskip", "product": "bulk", "label": "DataSkip API (bulk, 100 per call)",
     "endpoint_label": "POST https://app.dataskip.io/api/v1/skip-trace-bulk (<=100 addresses per call)",
     "cost_per_hit_cents": "4", "cost_per_request_cents": "0", "misses_charged": False,
     "monthly_minimum_cents": "0", "minimum_kind": "none",
     "batch_supported": True, "webhook_supported": None, "api_available": True,
     "credential_env": "DATASKIP_API_TOKEN", "price_status": "vendor_published",
     "source_url": "https://dataskip.io/skip-tracing-api", "verified_at": "2026-09-28",
     "notes": "Same 4c per match at any volume; CSV jobs up to 250,000 records. Webhook not documented where read."},
    # ── RealEstateAPI ──────────────────────────────────────────────────────
    {"provider": "reapi", "product": "skiptrace", "label": "RealEstateAPI SkipTrace",
     "endpoint_label": "POST https://api.realestateapi.com/v1/SkipTrace",
     "cost_per_hit_cents": "5", "cost_per_request_cents": "0", "misses_charged": False,
     "monthly_minimum_cents": "59900", "minimum_kind": "subscription", "included_records": 0,
     "batch_supported": None, "webhook_supported": None, "api_available": True,
     "credential_env": "REAPI_API_KEY", "price_status": "unverified",
     "source_url": "https://www.realestateapi.com/pricing", "verified_at": None,
     "notes": ("Reported $0.05/match WITH a property-data subscription (Starter $599/mo). Our tools could not "
               "render the pricing page - unverified, owner to confirm.")},
    # ── BatchData ──────────────────────────────────────────────────────────
    {"provider": "batchdata", "product": "growth", "label": "BatchData skip trace (Growth plan)",
     "endpoint_label": "BatchData property skip-trace API (<=100 properties per request)",
     "cost_per_hit_cents": None, "cost_per_request_cents": None, "misses_charged": True,
     "monthly_minimum_cents": "200000", "minimum_kind": "subscription", "included_records": 100000,
     "batch_supported": True, "webhook_supported": None, "api_available": True,
     "credential_env": "BATCHDATA_API_KEY", "price_status": "vendor_published",
     "source_url": "https://batchdata.io/pricing", "verified_at": "2026-09-28",
     "notes": ("$2,000/mo for 100,000 records/mo, annual commitment billed monthly (about 2c/record only at full "
               "use). Overage and pay-as-you-go prices not published - contact sales.")},
    # ── Pay-per-hit specialists (third-party reported) ─────────────────────
    {"provider": "reiskip", "product": "standard", "label": "REISkip",
     "endpoint_label": "REISkip web upload (API limited)",
     "cost_per_hit_cents": "15", "cost_per_request_cents": "0", "misses_charged": False,
     "monthly_minimum_cents": "0", "minimum_kind": "none",
     "batch_supported": True, "webhook_supported": None, "api_available": None,
     "price_status": "third_party_reported",
     "source_url": "https://bestskiptracingservice.com/bulk-skip-tracing/", "verified_at": "2026-09-28",
     "notes": "$0.15 per match, misses not charged, no subscription (comparison page dated 2026-08-21). Owner to confirm."},
    {"provider": "directskip", "product": "standard", "label": "DirectSkip",
     "endpoint_label": "DirectSkip web upload (API not documented where read)",
     "cost_per_hit_cents": "15", "cost_per_request_cents": "0", "misses_charged": False,
     "monthly_minimum_cents": "0", "minimum_kind": "none",
     "batch_supported": True, "webhook_supported": None, "api_available": None,
     "price_status": "third_party_reported",
     "source_url": "https://www.propertyleads.com/direct-skip-reviews/", "verified_at": "2026-09-28",
     "notes": ("$0.15 per hit to start, $0.10 at 1,000 hits, down to $0.06 at volume (review dated 2023-10-05 - "
               "stale). Pay per hit. Owner to confirm.")},
    {"provider": "bulkskiptrace", "product": "payg", "label": "Bulk Skip Trace (pay-as-you-go)",
     "endpoint_label": "Bulk Skip Trace web upload (API not documented where read)",
     "cost_per_hit_cents": "14", "cost_per_request_cents": "0", "misses_charged": False,
     "monthly_minimum_cents": "0", "minimum_kind": "none",
     "batch_supported": True, "webhook_supported": None, "api_available": None,
     "price_status": "third_party_reported",
     "source_url": "https://www.bulkskiptrace.com/compare/propstream", "verified_at": "2026-09-28",
     "notes": "$0.08-$0.14 by volume (top of range used for 250-500 records); unmatched credits refunded; no subscription."},
    {"provider": "skipgenie", "product": "subscription", "label": "Skip Genie",
     "endpoint_label": "Skip Genie web app (no API documented where read)",
     "cost_per_hit_cents": "17", "cost_per_request_cents": "0", "misses_charged": True,
     "monthly_minimum_cents": "5800", "minimum_kind": "subscription", "included_records": 100,
     "batch_supported": True, "webhook_supported": None, "api_available": None,
     "price_status": "third_party_reported",
     "source_url": "https://www.propertyleads.com/skip-genie-reviews/", "verified_at": "2026-09-28",
     "notes": ("$58/mo incl. 100 searches; $0.17/record (1-500), $0.15 (501-1,000), $0.13 (1,001+) "
               "(review dated 2023-03-07 - stale). Charged per search. Owner to confirm.")},
    {"provider": "propstream", "product": "essentials", "label": "PropStream Essentials",
     "endpoint_label": "PropStream app (no public API)",
     "cost_per_hit_cents": "12", "cost_per_request_cents": "0", "misses_charged": False,
     "monthly_minimum_cents": "9900", "minimum_kind": "subscription", "included_records": 0,
     "batch_supported": True, "webhook_supported": False, "api_available": False,
     "price_status": "third_party_reported",
     "source_url": "https://www.bulkskiptrace.com/compare/propstream", "verified_at": "2026-09-28",
     "notes": "$99/mo subscription + $0.12 per contact returned ($0.10 on Pro $199 / Elite $699). No public API - not integrable."},
    {"provider": "enformion", "product": "contact_enrichment", "label": "EnformionGO Contact Enrichment",
     "endpoint_label": "EnformionGO Contact Enrichment API",
     "cost_per_hit_cents": "25", "cost_per_request_cents": "0", "misses_charged": False,
     "monthly_minimum_cents": "0", "minimum_kind": "none",
     "batch_supported": None, "webhook_supported": None, "api_available": True,
     "credential_env": "ENFORMION_API_KEY", "price_status": "vendor_published",
     "source_url": "https://go.enformion.com/pricing/", "verified_at": "2026-09-27",
     "notes": ("From $0.25/match, pay only for matches, 100 free matches/mo; Pro 'as low as $0.01' is custom "
               "(sales). Terms forbid resale without written consent.")},
    # ── Benchmark (not a product) ──────────────────────────────────────────
    {"provider": "benchmark_derrick", "product": "reported", "label": "Derrick's reported rate (provider not identified)",
     "endpoint_label": "Unknown - plugs into the custom adapter slot once named",
     "cost_per_hit_cents": "1", "cost_per_request_cents": "0", "misses_charged": None,
     "monthly_minimum_cents": None, "minimum_kind": "none",
     "batch_supported": None, "webhook_supported": None, "api_available": None,
     "price_status": "unverified", "source_url": None, "verified_at": None, "is_active": False,
     "notes": ("Reported ~$0.01 per record. A BENCHMARK to investigate, not a product: provider, "
               "miss charging and minimums are unknown - owner to confirm. Never recommended.")},
]

_D0 = Decimal("0")


def _dec(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _money(c: Optional[Decimal]) -> Optional[str]:
    if c is None:
        return None
    return "$%s" % (Decimal(c) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _num(c: Optional[Decimal]) -> Optional[float]:
    """JSON number for a cents value (4 dp is plenty; cents * 10^-4)."""
    return None if c is None else float(Decimal(c).quantize(Decimal("0.0001")))


# ── Config: which Tracerfy product is the default, and the custom slot ─────

def tracerfy_default_product() -> str:
    """Normal batch unless the owner explicitly configures otherwise."""
    v = (os.environ.get(TRACERFY_PRODUCT_ENV) or "normal").strip().lower()
    return {"normal": "normal_batch", "normal_batch": "normal_batch", "advanced": "advanced_batch",
            "advanced_batch": "advanced_batch", "instant": "instant"}.get(v, "normal_batch")


def _truthy(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def custom_product_entry() -> Optional[Dict[str, Any]]:
    """The config-driven slot for a provider the owner names later (e.g.
    Derrick's). Present only when SKIPTRACE_CUSTOM_PRODUCT (JSON) is set; it is
    recommended only when SKIPTRACE_CUSTOM_ENABLED is true. Prices come from
    the owner's config and are labelled as such."""
    raw = os.environ.get("SKIPTRACE_CUSTOM_PRODUCT")
    if not raw:
        return None
    try:
        cfg = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(cfg, dict) or not cfg.get("label"):
        return None
    return {"provider": "custom", "product": str(cfg.get("product") or "default"),
            "label": str(cfg["label"])[:120],
            "endpoint_label": str(cfg.get("endpoint_label") or "Configured by the owner")[:250],
            "cost_per_hit_cents": cfg.get("cost_per_hit_cents"),
            "cost_per_request_cents": cfg.get("cost_per_request_cents", 0),
            "misses_charged": cfg.get("misses_charged"),
            "monthly_minimum_cents": cfg.get("monthly_minimum_cents", 0),
            "minimum_kind": cfg.get("minimum_kind", "none"), "included_records": cfg.get("included_records"),
            "batch_supported": cfg.get("batch_supported"), "webhook_supported": cfg.get("webhook_supported"),
            "api_available": cfg.get("api_available", True),
            "credential_env": cfg.get("token_env"), "price_status": "unverified",
            "source_url": cfg.get("source_url"), "verified_at": cfg.get("verified_at"),
            "is_active": _truthy(os.environ.get("SKIPTRACE_CUSTOM_ENABLED")),
            "notes": "Owner-configured provider (SKIPTRACE_CUSTOM_PRODUCT). Price as configured - confirm with the vendor."}


def _entries() -> List[Dict[str, Any]]:
    out = [dict(e) for e in CATALOGUE]
    c = custom_product_entry()
    if c:
        out.append(c)
    return out


# ── Catalogue persistence ───────────────────────────────────────────────────

_FIELDS = ("label", "endpoint_label", "cost_per_hit_cents", "cost_per_request_cents", "misses_charged",
           "monthly_minimum_cents", "minimum_kind", "included_records", "minimum_purchase_cents",
           "expected_hit_rate", "batch_supported", "webhook_supported", "api_available", "credential_env",
           "price_status", "source_url", "verified_at", "notes", "is_active", "is_current")
_DEC_FIELDS = {"cost_per_hit_cents", "cost_per_request_cents", "monthly_minimum_cents",
               "minimum_purchase_cents", "expected_hit_rate"}


def _sig(e: Dict[str, Any]) -> str:
    import hashlib
    h = hashlib.sha1(json.dumps(e, sort_keys=True, default=str).encode()).hexdigest()[:10]
    return "%s:%s" % (CATALOGUE_VERSION, h)


def ensure_catalogue(db) -> None:
    """Upsert the code catalogue into skiptrace_provider_products. Idempotent;
    platform-wide (no tenant data). The code is the source of truth, so a
    catalogue edit reaches the table on the next read. A row is written only
    when its catalogue signature changed, and each insert runs in a SAVEPOINT
    so two concurrent first reads cannot fail each other (UNIQUE on key)."""
    from sqlalchemy.exc import IntegrityError
    from app.models.skiptrace_cost_models import SkipTraceProviderProduct as P
    existing = {r.key: r for r in db.query(P).all()}
    now = datetime.utcnow()
    seen = set()
    for e in _entries():
        key = "%s:%s" % (e["provider"], e["product"])
        seen.add(key)
        sig = _sig(e)
        row = existing.get(key)
        if row is not None and row.catalogue_version == sig:
            continue
        values = {}
        for f in _FIELDS:
            v = e.get(f, True if f == "is_active" else False if f == "is_current" else
                      "none" if f == "minimum_kind" else None)
            if f in _DEC_FIELDS:
                v = _dec(v)
            if f in ("is_current", "is_active"):
                v = bool(v)
            values[f] = v
        if row is None:
            try:
                with db.begin_nested():
                    db.add(P(key=key, provider=e["provider"], product=e["product"], created_at=now,
                             updated_at=now, catalogue_version=sig, **values))
            except IntegrityError:
                continue                         # a concurrent request inserted it
        else:
            for f, v in values.items():
                setattr(row, f, v)
            row.catalogue_version = sig
            row.updated_at = now
    for key, row in existing.items():            # removed from code (e.g. custom slot unset)
        if key not in seen and row.is_active:
            row.is_active = False
    db.flush()


def configured(row) -> bool:
    """Read-only presence check of the credential env var(s). Never returns
    or logs a value."""
    names = [n.strip() for n in (row.credential_env or "").split(",") if n.strip()]
    return bool(names) and all(bool(os.environ.get(n)) for n in names)


def product_payload(row) -> Dict[str, Any]:
    return {
        "key": row.key, "provider": row.provider, "product": row.product, "label": row.label,
        "endpoint_label": row.endpoint_label,
        "cost_per_hit_cents": _num(row.cost_per_hit_cents),
        "cost_per_request_cents": _num(row.cost_per_request_cents),
        "misses_charged": row.misses_charged,
        "monthly_minimum_cents": _num(row.monthly_minimum_cents),
        "minimum_kind": row.minimum_kind, "included_records": row.included_records,
        "minimum_purchase_cents": _num(row.minimum_purchase_cents),
        "expected_hit_rate": _num(row.expected_hit_rate),
        "batch_supported": row.batch_supported, "webhook_supported": row.webhook_supported,
        "api_available": row.api_available,
        "price_status": row.price_status, "price_verified": row.price_status == "vendor_published",
        "source_url": row.source_url, "verified_at": row.verified_at, "notes": row.notes,
        "is_active": bool(row.is_active), "is_current": bool(row.is_current),
        "is_default": row.provider == "tracerfy" and row.product == tracerfy_default_product(),
        "configured": configured(row),
        "credential_env": row.credential_env,        # the NAME of the variable, never its value
    }


def list_products(db) -> List[Dict[str, Any]]:
    from app.models.skiptrace_cost_models import SkipTraceProviderProduct as P
    ensure_catalogue(db)
    rows = db.query(P).order_by(P.provider, P.product).all()
    return [product_payload(r) for r in rows]


def get_product(db, key: str):
    from app.models.skiptrace_cost_models import SkipTraceProviderProduct as P
    ensure_catalogue(db)
    if key == "tracerfy" or key == "tracerfy:default":
        key = "tracerfy:%s" % tracerfy_default_product()
    return db.query(P).filter(P.key == key).first()


# ── Pure cost maths (Decimal throughout) ────────────────────────────────────

def cost_for(row, billable: int, hit_rate: Decimal) -> Dict[str, Any]:
    """Usage cost, maximum and monthly-incl.-minimum for `billable` requests.
    None where the price is not known - never a guess."""
    per_hit = _dec(row.cost_per_hit_cents)
    per_req = _dec(row.cost_per_request_cents) or _D0
    minimum = _dec(row.monthly_minimum_cents) or _D0
    kind = row.minimum_kind or "none"
    included = row.included_records or 0
    n = Decimal(billable)
    hits = (n * hit_rate)
    if kind == "subscription" and included:
        over = max(0, billable - included)
        n_usage = Decimal(over)
        hits_usage = n_usage * hit_rate
    else:
        n_usage, hits_usage = n, hits
    if per_hit is None and n_usage > 0:
        usage = maximum = None
    else:
        ph = per_hit or _D0
        if row.misses_charged is False:
            usage = hits_usage * ph + n_usage * per_req
        else:                       # True, or unknown -> worst case: every request charged
            usage = n_usage * ph + n_usage * per_req
        maximum = n_usage * ph + n_usage * per_req
    if usage is None:
        monthly = None
    elif kind == "subscription":
        monthly = minimum + usage
    elif kind == "spend_floor":
        monthly = max(minimum, usage)
    else:
        monthly = usage
    return {"expected_hits": hits, "usage": usage, "maximum": maximum, "monthly": monthly,
            "minimum": minimum, "kind": kind, "included": included}


def _minimum_note(row, c) -> str:
    if c["kind"] == "subscription":
        inc = (" incl. %s records" % "{:,}".format(c["included"])) if c["included"] else ""
        return "Subscription %s/month%s is owed regardless of this batch." % (_money(c["minimum"]), inc)
    if c["kind"] == "spend_floor":
        return "Monthly spend floor %s." % _money(c["minimum"])
    if row.minimum_purchase_cents:
        return "No monthly minimum; one-time minimum credit purchase %s." % _money(_dec(row.minimum_purchase_cents))
    if row.monthly_minimum_cents is None:
        return "Monthly minimum unknown - owner to confirm."
    return "No monthly minimum."


def _pct(rate: Decimal) -> str:
    return format((rate * 100).quantize(Decimal("0.1")).normalize(), "f")


def _hit_rate(v) -> Decimal:
    if v is None:
        return DEFAULT_HIT_RATE
    d = _dec(v)
    if d < 0 or d > 1:
        raise ValueError("hit_rate must be between 0 and 1")
    return d


# ── Dedupe: never buy a property twice inside the freshness window ─────────

def _already_traced(db, org_id: str, kind: str, ids: List[str], days: int) -> set:
    since = datetime.utcnow() - timedelta(days=days)
    done: set = set()
    if not ids:
        return done
    if kind == "wholesale":
        from app.models.wholesale_models import WholesaleEnrichmentRequest as R
        rows = (db.query(R.property_id)
                .filter(R.organization_id == org_id, R.property_id.in_(ids),
                        R.status.in_(("succeeded", "no_match", "manual")),
                        R.created_at >= since).all())
        done |= {r[0] for r in rows}
    else:
        from app.models.evosense_models import EvoSenseCostEntry as L, EvoSenseEnrichmentDecision as D
        rows = (db.query(L.property_id)
                .filter(L.organization_id == org_id, L.property_id.in_(ids),
                        L.capability == "CONTACT_ENRICHMENT", L.status.in_(("charged", "failed_charged")),
                        L.created_at >= since).all())
        done |= {r[0] for r in rows}
        rows = (db.query(D.property_id)
                .filter(D.organization_id == org_id, D.property_id.in_(ids),
                        D.capability == "CONTACT_ENRICHMENT",
                        D.outcome.in_(("found", "no_match")), D.created_at >= since).all())
        done |= {r[0] for r in rows}
    return done


def _owned_ids(db, org_id: str, kind: str, ids: List[str]) -> set:
    if kind == "wholesale":
        from app.models.wholesale_models import WholesaleProperty as M
    else:
        from app.models.evosense_models import EvoSenseProperty as M
    rows = db.query(M.id).filter(M.organization_id == org_id, M.id.in_(ids)).all()
    return {r[0] for r in rows}


# ── Estimate ────────────────────────────────────────────────────────────────

class EstimateRefused(ValueError):
    pass


def estimate_batch(db, org_id: str, *, product_key: str, record_count: Optional[int] = None,
                   property_ids: Optional[Iterable[str]] = None, property_kind: str = "wholesale",
                   dedupe: bool = True, hit_rate=None, freshness_days: int = FRESHNESS_DAYS,
                   user=None, persist: bool = True) -> Dict[str, Any]:
    """ESTIMATED TOTAL COST of a proposed batch, before anything runs."""
    row = get_product(db, product_key)
    if row is None:
        raise EstimateRefused("Unknown provider product %r." % product_key)
    rate = _hit_rate(hit_rate)
    assumptions: List[str] = [HIT_RATE_NOTE % _pct(rate)]
    not_found = already = 0
    billable_ids: Optional[List[str]] = None
    if property_ids is not None:
        if property_kind not in ("wholesale", "evosense"):
            raise EstimateRefused("property_kind must be 'wholesale' or 'evosense'.")
        ids = list(dict.fromkeys(str(i) for i in property_ids if i))      # de-duplicate the request itself
        if len(ids) > PAID_RUN_MAX_RECORDS:
            raise EstimateRefused("At most %d records per estimate." % PAID_RUN_MAX_RECORDS)
        owned = _owned_ids(db, org_id, property_kind, ids) if ids else set()
        not_found = len([i for i in ids if i not in owned])
        ids = [i for i in ids if i in owned]                               # another org's ids are ignored
        done = _already_traced(db, org_id, property_kind, ids, freshness_days) if dedupe else set()
        already = len(done)
        billable_ids = [i for i in ids if i not in done]
        records = len(ids)
        billable = len(billable_ids)
        if dedupe:
            assumptions.append("Properties traced (or answered 'no match') in the last %d days are excluded, "
                               "so they are not bought again." % freshness_days)
        if not_found:
            assumptions.append("%d id(s) are not properties of this workspace and were ignored." % not_found)
    else:
        if record_count is None or int(record_count) < 0:
            raise EstimateRefused("Give record_count or property_ids.")
        records = billable = int(record_count)
        if records > PAID_RUN_MAX_RECORDS:
            raise EstimateRefused("At most %d records per estimate." % PAID_RUN_MAX_RECORDS)
        assumptions.append("Count-only estimate: no dedupe against records already traced.")
    c = cost_for(row, billable, rate)
    if row.misses_charged is None:
        assumptions.append("Whether misses are charged is unknown; every request is priced as charged.")
    elif row.misses_charged:
        assumptions.append("Every billable record is charged (hit or miss).")
    else:
        assumptions.append("Misses are free; only expected hits are charged.")
    if row.price_status != "vendor_published":
        assumptions.append("Price is %s - owner to confirm before any run." % row.price_status.replace("_", " "))
    if not row.is_active:
        assumptions.append("This row is a benchmark / inactive product and cannot be run.")
    per_rec = (c["monthly"] / records) if (c["monthly"] is not None and records) else None
    out = {
        "product_key": row.key, "provider": row.provider, "product": row.product, "label": row.label,
        "records": records, "already_traced": already, "not_found": not_found,
        "billable_requests": billable,
        "expected_hit_rate": _num(rate), "expected_hits": _num(c["expected_hits"]),
        "estimated_total_cents": _num(c["usage"]), "estimated_total": _money(c["usage"]),
        "maximum_total_cents": _num(c["maximum"]), "maximum_total": _money(c["maximum"]),
        "monthly_total_cents": _num(c["monthly"]), "monthly_total": _money(c["monthly"]),
        "monthly_minimum_note": _minimum_note(row, c),
        "per_record_effective_cents": _num(per_rec),
        "assumptions": assumptions, "price_status": row.price_status,
        "source_url": row.source_url, "verified_at": row.verified_at,
        "estimate_id": None, "status": None, "expires_at": None,
        "confirmation_phrase": None,
        "approvable": property_ids is not None and row.is_active,
    }
    if persist:
        from app.models.skiptrace_cost_models import SkipTraceCostEstimate as E
        e = E(organization_id=org_id, product_key=row.key, provider=row.provider,
              property_kind=property_kind if property_ids is not None else None,
              property_ids=json.dumps(billable_ids) if billable_ids is not None else None,
              records=records, already_traced=already, not_found=not_found, billable_requests=billable,
              expected_hit_rate=rate, expected_hits=c["expected_hits"],
              estimated_total_cents=c["usage"], maximum_total_cents=c["maximum"],
              monthly_total_cents=c["monthly"], assumptions=json.dumps(assumptions),
              status="estimated", created_by_id=getattr(user, "id", None),
              expires_at=datetime.utcnow() + timedelta(hours=ESTIMATE_TTL_HOURS))
        db.add(e)
        db.flush()
        out.update(estimate_id=e.id, status=e.status, expires_at=e.expires_at.isoformat() + "Z",
                   confirmation_phrase=confirmation_phrase(e))
    return out


def compare_providers(db, record_count: int, *, hit_rate=None) -> Dict[str, Any]:
    """Every product priced for the SAME volume, ranked by TOTAL monthly cost
    (incl. minimum/subscription). Unknown totals and non-runnable rows sort
    last; the recommendation is the cheapest ACTIVE, API-available product."""
    if record_count is None or record_count < 0 or record_count > PAID_RUN_MAX_RECORDS * 100:
        raise EstimateRefused("records must be between 0 and %d." % (PAID_RUN_MAX_RECORDS * 100))
    from app.models.skiptrace_cost_models import SkipTraceProviderProduct as P
    ensure_catalogue(db)
    rate = _hit_rate(hit_rate)
    rows = db.query(P).all()
    items = []
    for r in rows:
        c = cost_for(r, record_count, rate)
        runnable = bool(r.is_active) and r.api_available is True
        items.append({
            **product_payload(r),
            "this_batch_cents": _num(c["usage"]), "this_batch": _money(c["usage"]),
            "maximum_cents": _num(c["maximum"]), "maximum": _money(c["maximum"]),
            "monthly_incl_minimum_cents": _num(c["monthly"]), "monthly_incl_minimum": _money(c["monthly"]),
            # all-in: includes any subscription / minimum owed for the month
            "per_record_effective_cents": _num(c["monthly"] / record_count) if (c["monthly"] is not None and record_count) else None,
            "monthly_minimum_note": _minimum_note(r, c),
            "runnable": runnable,
            "_sort": (0 if runnable else 1, c["monthly"] is None,
                      c["monthly"] if c["monthly"] is not None else Decimal("1e18"),
                      c["maximum"] if c["maximum"] is not None else Decimal("1e18"), r.key),
        })
    items.sort(key=lambda i: i["_sort"])
    for n, i in enumerate(items, 1):
        i.pop("_sort")
        i["rank"] = n
    best = next((i for i in items if i["runnable"] and i["monthly_incl_minimum_cents"] is not None), None)
    current = next((i for i in items if i["is_current"]), None)
    reasoning: List[str] = []
    if best:
        reasoning.append("%s has the lowest total for %d records: %s expected this batch, %s worst case, "
                         "%s for the month including any minimum." % (
                             best["label"], record_count, best["this_batch"], best["maximum"],
                             best["monthly_incl_minimum"]))
        if not best["price_verified"]:
            reasoning.append("Its price is %s - confirm before relying on it." % best["price_status"].replace("_", " "))
        if best["misses_charged"] is not False:
            reasoning.append("Priced as if every record is charged (misses charged or unconfirmed), so the "
                             "estimate is already the conservative case.")
        if current and current["key"] != best["key"] and current["monthly_incl_minimum_cents"] is not None:
            saved = Decimal(str(current["monthly_incl_minimum_cents"])) - Decimal(str(best["monthly_incl_minimum_cents"]))
            reasoning.append("Versus the product in use today (%s, %s): saves %s on this volume." % (
                current["label"], current["monthly_incl_minimum"], _money(saved)))
    subs = [i for i in items if i["minimum_kind"] == "subscription" and i["runnable"]]
    if subs:
        reasoning.append("Subscription products (%s) carry their monthly fee even for a small batch - that is "
                         "why they rank below pay-per-use at this volume." % ", ".join(i["label"] for i in subs))
    bench = next((i for i in items if i["provider"] == "benchmark_derrick"), None)
    if bench:
        reasoning.append("Derrick's ~$0.01/record is shown as a benchmark only (%s at this volume); the "
                         "provider is not identified, so it is not recommended." % bench["this_batch"])
    reasoning.append(HIT_RATE_NOTE % _pct(rate))
    return {"records": record_count, "expected_hit_rate": _num(rate), "items": items,
            "recommendation": ({"key": best["key"], "label": best["label"],
                                "this_batch_cents": best["this_batch_cents"],
                                "monthly_incl_minimum_cents": best["monthly_incl_minimum_cents"]}
                               if best else None),
            "current": ({"key": current["key"], "label": current["label"],
                         "this_batch_cents": current["this_batch_cents"]} if current else None),
            "reasoning": reasoning}


# ── Confirmation gate: no paid run without a confirmed estimate ─────────────

class ConfirmationRequired(PermissionError):
    pass


def confirmation_phrase(e) -> str:
    return "APPROVE SKIP TRACE %s" % e.id


def _load(db, org_id: str, estimate_id: str, *, lock: bool = False):
    from app.models.skiptrace_cost_models import SkipTraceCostEstimate as E
    q = db.query(E).filter(E.id == estimate_id, E.organization_id == org_id)
    if lock:
        q = q.with_for_update()          # row lock on Postgres; SQLAlchemy omits it on SQLite
    return q.first()


def _transition(db, e, org_id: str, frm: str, to: str, **values) -> None:
    """Atomic compare-and-set: UPDATE ... WHERE status = frm. Exactly one row
    or the transition is refused - two concurrent requests cannot both win."""
    from app.models.skiptrace_cost_models import SkipTraceCostEstimate as E
    n = (db.query(E)
         .filter(E.id == e.id, E.organization_id == org_id, E.status == frm)
         .update(dict(status=to, **values), synchronize_session=False))
    if n != 1:
        db.expire(e)
        raise ConfirmationRequired("Estimate %s is no longer %s (already used or changed)." % (e.id, frm))
    db.flush()
    db.refresh(e)


def estimate_payload(e) -> Dict[str, Any]:
    return {"estimate_id": e.id, "product_key": e.product_key, "provider": e.provider,
            "records": e.records, "already_traced": e.already_traced, "not_found": e.not_found,
            "billable_requests": e.billable_requests,
            "estimated_total_cents": _num(e.estimated_total_cents), "estimated_total": _money(e.estimated_total_cents),
            "maximum_total_cents": _num(e.maximum_total_cents), "maximum_total": _money(e.maximum_total_cents),
            "monthly_total_cents": _num(e.monthly_total_cents),
            "assumptions": json.loads(e.assumptions or "[]"), "status": e.status,
            "confirmed_at": e.confirmed_at.isoformat() + "Z" if e.confirmed_at else None,
            "expires_at": e.expires_at.isoformat() + "Z" if e.expires_at else None,
            "confirmation_phrase": confirmation_phrase(e),
            "approvable": e.property_ids is not None,
            "executes_anything": False}


def confirm_estimate(db, org_id: str, estimate_id: str, phrase: Optional[str], user) -> Any:
    """A person approves the ESTIMATED TOTAL. Records the approval; runs
    nothing. Cross-org -> None (caller 404s)."""
    e = _load(db, org_id, estimate_id, lock=True)
    if e is None:
        return None
    if e.status in ("consumed", "expired"):
        raise ConfirmationRequired("This estimate is %s; make a new estimate." % e.status)
    if e.expires_at and e.expires_at < datetime.utcnow():
        e.status = "expired"
        db.flush()
        raise ConfirmationRequired("This estimate expired; make a new estimate.")
    if e.property_ids is None:
        raise ConfirmationRequired("A count-only estimate is informational and cannot be approved for a run. "
                                   "Estimate the actual property selection (property ids) to approve it.")
    from app.models.skiptrace_cost_models import SkipTraceProviderProduct as P
    prod = db.query(P).filter(P.key == e.product_key).first()
    if prod is None or not prod.is_active:
        raise ConfirmationRequired("This product is a benchmark or inactive and cannot be approved.")
    if (phrase or "").strip() != confirmation_phrase(e):
        raise ConfirmationRequired("Type exactly: %s" % confirmation_phrase(e))
    _transition(db, e, org_id, "estimated", "confirmed",
                confirmed_by_id=getattr(user, "id", None), confirmed_at=datetime.utcnow())
    return e


def gate_paid_run(db, org_id: str, estimate_id: Optional[str], *, provider: Optional[str] = None,
                  record_count: Optional[int] = None, property_ids: Optional[Iterable[str]] = None,
                  consumer: str = "unspecified", consume: bool = True):
    """The check every paid skip-trace trigger path must pass BEFORE queueing:
    an estimate of THIS org, CONFIRMED by a person, unexpired, unconsumed, for
    the same provider, and covering no more records than were approved (and,
    when ids are given, only ids that were approved - dedupe included).
    Consumed once, so a confirmation can never authorize a second run."""
    if not estimate_id:
        raise ConfirmationRequired("A paid skip trace needs a confirmed cost estimate: estimate the batch, "
                                   "review the ESTIMATED TOTAL COST, then approve it.")
    e = _load(db, org_id, estimate_id, lock=True)
    if e is None:
        raise ConfirmationRequired("Estimate not found for this workspace.")
    if e.property_ids is None:
        raise ConfirmationRequired("Estimate %s is count-only (informational) and can never authorize a run." % e.id)
    if e.status != "confirmed":
        raise ConfirmationRequired("Estimate %s is %s, not confirmed." % (e.id, e.status))
    if e.expires_at and e.expires_at < datetime.utcnow():
        raise ConfirmationRequired("Estimate %s expired; estimate again." % e.id)
    if provider and provider != e.provider:
        raise ConfirmationRequired("Estimate %s was approved for %s, not %s." % (e.id, e.provider, provider))
    if record_count is not None and record_count > (e.billable_requests or 0):
        raise ConfirmationRequired("This run asks for %d records; %d were approved."
                                   % (record_count, e.billable_requests or 0))
    if property_ids is None:
        raise ConfirmationRequired("A paid run must name the property ids it will trace.")
    property_ids = list(property_ids)
    approved = set(json.loads(e.property_ids))
    extra = [i for i in property_ids if i not in approved]
    if extra:
        raise ConfirmationRequired("%d record(s) in this run were not in the approved estimate "
                                   "(or were already traced)." % len(extra))
    if len(set(property_ids)) != len(property_ids):
        raise ConfirmationRequired("This run lists the same property more than once.")
    if consume:
        _transition(db, e, org_id, "confirmed", "consumed",
                    consumed_at=datetime.utcnow(), consumed_by=consumer[:120])
    return e


# ── Current-integration facts (for the panel and the report) ────────────────

def integration_facts() -> Dict[str, Any]:
    return {
        "current": {
            "provider": "tracerfy", "product": "instant",
            "endpoint": "POST https://tracerfy.com/v1/api/trace/lookup/ (find_owner=true)",
            "adapter": "app/services/evosense/vendors.py TracerfySkipTrace (EVALUATION ONLY - never routed to production)",
            "cost": "5 credits x $0.02 = $0.10 per hit; 0 on a miss (billed from credits_deducted)",
            "why_010": ("It is Tracerfy's synchronous single-lookup API, priced at 5 credits per hit. Normal "
                        "(1 credit) and Advanced (2 credits) are the batch products, which the integration never used."),
            "batch": "not used (single lookups only)", "webhook": "not used",
            "retry": ("None inside the adapter (one call, no loop). EvoSense execute(): a timeout / failure / 429 "
                      "is refunded and it falls back to the next routed provider; the evaluation harness settles to "
                      "what the vendor billed."),
            "duplicate_billing": ("No idempotency key is sent to Tracerfy. EvoSense decide() reuses a lookup made "
                                  "in the last 90 days and waits 7 days after a miss (an approved decision skips "
                                  "the wait); the evaluation harness can re-buy the same property in a second "
                                  "evaluation; /wholesale/enrichment/run has caps but no per-property dedupe. "
                                  "This module's estimate excludes anything traced in the last %d days and "
                                  "gate_paid_run() refuses any id not in the approved estimate." % FRESHNESS_DAYS),
        },
        "default_product": "tracerfy:%s" % tracerfy_default_product(),
        "default_product_env": TRACERFY_PRODUCT_ENV,
        "paid_execution_in_this_build": False,
    }
