"""Skip-trace BATCH adapter interface - request construction only.

Clean seam for the cheaper batch products (Tracerfy Normal by default) and for
a provider the owner names later (Derrick's, via the config-driven custom
slot). This module:

  * has NO HTTP client import and NO default transport. `submit()` sends only
    through a transport object the caller passes in (tests pass a fake);
  * refuses to submit unless the adapter is ENABLED by config (every adapter is
    disabled by default) AND the caller presents an estimate that passed
    `skiptrace_costing.gate_paid_run()` for the same provider and records;
  * never reads or prints a credential value except to put it in the
    Authorization header of a request that is actually being submitted;
    `build_request()` returns a REDACTED header view.

Tracerfy batch details not confirmed from the public docs in this build (the
exact path and field names; tracerfy.com was not reachable from the build
environment on 2026-09-28) are CONFIG, with the values below as placeholders
marked "owner to confirm" - that is also why the adapter is disabled by
default.
"""
from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from app.services import skiptrace_costing as SC


@dataclass
class SkipTraceRecord:
    property_id: str
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    owner_first: Optional[str] = None
    owner_last: Optional[str] = None
    mailing_street: Optional[str] = None
    mailing_city: Optional[str] = None
    mailing_state: Optional[str] = None
    mailing_zip: Optional[str] = None


@dataclass
class PreparedRequest:
    method: str
    url: str
    headers: Dict[str, str]                 # REDACTED view (no secret values)
    data: Dict[str, Any] = field(default_factory=dict)       # form fields / JSON body
    files: Dict[str, Any] = field(default_factory=dict)      # {"csv_file": (name, text, mime)}
    json_body: Optional[Dict[str, Any]] = None
    record_count: int = 0
    product_key: str = ""


class Transport(Protocol):
    def send(self, request: PreparedRequest, secret_headers: Dict[str, str]) -> Dict[str, Any]: ...


class AdapterRefused(PermissionError):
    pass


def _truthy(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


class SkipTraceBatchAdapter:
    provider = "base"
    required_env: tuple = ()
    enabled_env = ""

    @property
    def product_key(self) -> str:
        raise NotImplementedError

    def enabled(self) -> bool:
        return bool(self.enabled_env) and _truthy(os.environ.get(self.enabled_env))

    def configured(self) -> bool:
        return bool(self.required_env) and all(os.environ.get(n) for n in self.required_env)

    def build_request(self, records: List[SkipTraceRecord]) -> PreparedRequest:  # pragma: no cover
        raise NotImplementedError

    def _secret_headers(self) -> Dict[str, str]:  # pragma: no cover
        raise NotImplementedError

    def submit(self, db, org_id: str, records: List[SkipTraceRecord], *, estimate_id: Optional[str],
               transport: Optional[Transport]) -> Dict[str, Any]:
        """Queue ONE batch with the vendor. Refused unless enabled, configured,
        given a transport, and backed by a confirmed estimate (consumed here)."""
        if not self.enabled():
            raise AdapterRefused("%s is disabled (%s is not true)." % (self.product_key, self.enabled_env))
        if not self.configured():
            raise AdapterRefused("%s is not configured (%s)." % (self.product_key, ", ".join(self.required_env)))
        if transport is None:
            raise AdapterRefused("No transport given - this module never sends by default.")
        SC.gate_paid_run(db, org_id, estimate_id, provider=self.provider, record_count=len(records),
                         property_ids=[r.property_id for r in records],
                         consumer="adapter:%s" % self.product_key, consume=True)
        req = self.build_request(records)
        return transport.send(req, self._secret_headers())


def _csv(records: List[SkipTraceRecord], columns: Dict[str, str]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(list(columns.values()))
    for r in records:
        w.writerow([getattr(r, attr) or "" for attr in columns.keys()])
    return buf.getvalue()


class TracerfyBatchAdapter(SkipTraceBatchAdapter):
    """Tracerfy batch trace (Normal by default; Advanced by config).

    Config (all optional; placeholders are OWNER TO CONFIRM against
    https://www.tracerfy.com/skip-tracing-api-documentation/):
        TRACERFY_TRACE_PRODUCT     normal (default) | advanced   ('instant' = the old single API; not a batch)
        TRACERFY_BATCH_ENABLED     must be true to submit (default false)
        TRACERFY_API_BASE          default https://tracerfy.com/v1/api
        TRACERFY_BATCH_PATH        default /trace/            (placeholder)
        TRACERFY_TRACE_TYPE_VALUE  default = the product name (placeholder)
        TRACERFY_WEBHOOK_URL       sent only when set          (field name placeholder)
    """
    provider = "tracerfy"
    required_env = ("TRACERFY_API_TOKEN",)
    enabled_env = "TRACERFY_BATCH_ENABLED"
    COLUMNS = {"street_address": "address", "city": "city", "state": "state", "zip_code": "zip",
               "owner_first": "first_name", "owner_last": "last_name",
               "mailing_street": "mail_address", "mailing_city": "mail_city",
               "mailing_state": "mail_state", "mailing_zip": "mail_zip", "property_id": "external_id"}

    def __init__(self, product: Optional[str] = None):
        p = product or SC.tracerfy_default_product()
        if p == "instant":
            # The instant API is not a batch product; the batch adapter falls
            # back to the cheaper default rather than silently using a premium.
            p = "normal_batch"
        self.product = p

    @property
    def product_key(self) -> str:
        return "tracerfy:%s" % self.product

    @property
    def trace_type(self) -> str:
        return os.environ.get("TRACERFY_TRACE_TYPE_VALUE") or self.product.replace("_batch", "")

    def build_request(self, records: List[SkipTraceRecord]) -> PreparedRequest:
        base = (os.environ.get("TRACERFY_API_BASE") or "https://tracerfy.com/v1/api").rstrip("/")
        path = os.environ.get("TRACERFY_BATCH_PATH") or "/trace/"
        data = {"trace_type": self.trace_type}
        for attr, col in self.COLUMNS.items():
            data["%s_column" % col] = col
        hook = os.environ.get("TRACERFY_WEBHOOK_URL")
        if hook:
            data["webhook_url"] = hook
        return PreparedRequest(
            method="POST", url=base + path,
            headers={"Authorization": "Bearer ***redacted***"},
            data=data, files={"csv_file": ("batch.csv", _csv(records, self.COLUMNS), "text/csv")},
            record_count=len(records), product_key=self.product_key)

    def _secret_headers(self) -> Dict[str, str]:
        return {"Authorization": "Bearer %s" % os.environ["TRACERFY_API_TOKEN"]}


class CustomHttpAdapter(SkipTraceBatchAdapter):
    """The slot for a provider the owner names later (e.g. Derrick's).
    Everything comes from SKIPTRACE_CUSTOM_PRODUCT (JSON):
        {"label": "...", "url": "https://...", "token_env": "MY_VENDOR_TOKEN",
         "auth_header": "Authorization", "auth_scheme": "Bearer",
         "records_field": "records", "cost_per_hit_cents": 1, "misses_charged": false, ...}
    and it submits only when SKIPTRACE_CUSTOM_ENABLED is true (default false)."""
    provider = "custom"
    enabled_env = "SKIPTRACE_CUSTOM_ENABLED"

    def __init__(self):
        try:
            self.cfg = json.loads(os.environ.get("SKIPTRACE_CUSTOM_PRODUCT") or "{}")
        except ValueError:
            self.cfg = {}
        tok = self.cfg.get("token_env")
        self.required_env = (tok,) if tok else ()

    @property
    def product_key(self) -> str:
        return "custom:%s" % (self.cfg.get("product") or "default")

    def build_request(self, records: List[SkipTraceRecord]) -> PreparedRequest:
        if not self.cfg.get("url"):
            raise AdapterRefused("SKIPTRACE_CUSTOM_PRODUCT has no url.")
        hdr = self.cfg.get("auth_header") or "Authorization"
        body = {self.cfg.get("records_field") or "records": [
            {k: v for k, v in r.__dict__.items() if v not in (None, "")} for r in records]}
        return PreparedRequest(method="POST", url=self.cfg["url"], headers={hdr: "***redacted***"},
                               json_body=body, record_count=len(records), product_key=self.product_key)

    def _secret_headers(self) -> Dict[str, str]:
        hdr = self.cfg.get("auth_header") or "Authorization"
        scheme = self.cfg.get("auth_scheme")
        tok = os.environ[self.required_env[0]]
        return {hdr: ("%s %s" % (scheme, tok)) if scheme else tok}


def default_adapter() -> SkipTraceBatchAdapter:
    """The adapter a paid batch would use by default: Tracerfy Normal."""
    return TracerfyBatchAdapter()


def adapters_report() -> List[Dict[str, Any]]:
    out = []
    for a in (TracerfyBatchAdapter(), CustomHttpAdapter()):
        out.append({"product_key": a.product_key, "provider": a.provider, "enabled": a.enabled(),
                    "configured": a.configured(), "enabled_env": a.enabled_env,
                    "required_env": list(a.required_env)})
    return out
