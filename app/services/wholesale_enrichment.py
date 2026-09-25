"""Skip trace / contact enrichment — provider-agnostic, and functional with none.

THE POINT OF THIS FILE IS THE INTERFACE, NOT A VENDOR.
No skip-trace vendor is integrated here, and that is a decision rather than an
omission. Committing to one tonight would put a vendor's response shape into the
database and into the UI, and the second vendor would then require a migration
and a screen change. So what ships is:

    EnrichmentProvider   the interface every provider implements
    EnrichmentResult     the ONE normalized shape the rest of the module reads
    ManualProvider       always present, always configured, never fails
    PROVIDERS            the registry a real adapter is added to

ADDING A REAL PROVIDER LATER is one class and one registry line. Nothing above
this module changes: the router, the models and the screens all read
`EnrichmentResult`, never a vendor payload.

"MISSING API CREDENTIALS MUST NOT PREVENT THE MODULE ITSELF FROM WORKING."
That is why `ManualProvider` exists and why it is the default. With no provider
configured, enrichment is a person typing a phone number or a CSV import — and
both of those are first-class paths that write the same rows, emit the same
events and move the deal along the same way an API response would. The only
difference in the record is `provider = "manual"`.

NOTHING HERE EVER MANUFACTURES A RESULT. A provider that is not configured
returns `status="not_configured"` with an explanation. It does not return a
plausible phone number, and there is no code path in this file that can invent
one.
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Status values a request can end in. `manual` is a real outcome, not a failure.
STATUS_PENDING = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_NO_MATCH = "no_match"
STATUS_FAILED = "failed"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_CAPPED = "capped"
STATUS_MANUAL = "manual"


@dataclass
class EnrichmentPhone:
    number: str
    phone_type: Optional[str] = None      # mobile, landline, voip, unknown
    confidence: Optional[int] = None      # 0-100, provider-reported
    source: Optional[str] = None


@dataclass
class EnrichmentResult:
    """The ONLY shape anything outside this module reads.

    A provider adapter's job is to translate its vendor's response into this and
    nothing else. `raw_summary` is a short human string for the request log — it
    is deliberately not the raw payload, because a vendor payload in the
    database is a vendor payload in the UI a year later.
    """
    status: str
    provider: str
    phones: List[EnrichmentPhone] = field(default_factory=list)
    emails: List[str] = field(default_factory=list)
    owner_name: Optional[str] = None
    mailing_street: Optional[str] = None
    mailing_city: Optional[str] = None
    mailing_state: Optional[str] = None
    mailing_zip: Optional[str] = None
    confidence: Optional[int] = None
    message: Optional[str] = None          # why, in words, when there is a why
    billable: bool = False
    cost_cents: Optional[int] = None
    raw_summary: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @property
    def found_anything(self) -> bool:
        return bool(self.phones or self.emails)


@dataclass
class EnrichmentInput:
    """What we know going in. Every field optional; providers say what they need."""
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    county: Optional[str] = None
    parcel_apn: Optional[str] = None
    owner_name: Optional[str] = None
    business_name: Optional[str] = None
    mailing_street: Optional[str] = None
    mailing_city: Optional[str] = None
    mailing_state: Optional[str] = None
    mailing_zip: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


class EnrichmentProvider:
    """The interface. Two questions and one verb.

    `key`          stable identifier stored on every request row
    `label`        what the settings screen shows
    `billable`     whether a call costs money, which decides whether the cost
                   controls apply at all
    `is_configured()`  can this provider actually run right now
    `lookup(...)`  do it, and return an EnrichmentResult — never raise for a
                   vendor-side failure, because a raised exception is how one
                   bad response takes down a batch
    """

    key = "base"
    label = "Base"
    billable = False
    required_env: tuple = ()

    def is_configured(self) -> bool:
        return all(os.environ.get(name) for name in self.required_env)

    def missing_config(self) -> List[str]:
        return [n for n in self.required_env if not os.environ.get(n)]

    def lookup(self, data: EnrichmentInput) -> EnrichmentResult:  # pragma: no cover
        raise NotImplementedError


class ManualProvider(EnrichmentProvider):
    """Always available. Returns nothing, and says so plainly.

    This is not a stub standing in for a real provider — it is the honest answer
    to "look this owner up" when no vendor is connected: the platform does not
    know, and a person or an import has to supply it. It returns STATUS_MANUAL
    rather than STATUS_FAILED because nothing failed; there was simply nothing
    automatic to do.
    """

    key = "manual"
    label = "Manual entry / CSV import"
    billable = False

    def is_configured(self) -> bool:
        return True

    def lookup(self, data: EnrichmentInput) -> EnrichmentResult:
        return EnrichmentResult(
            status=STATUS_MANUAL,
            provider=self.key,
            message=("No skip-trace provider is connected, so nothing was looked "
                     "up. Enter the owner's phone or email on this property, or "
                     "import a file that carries it — both write the same record "
                     "an API response would."),
        )


# The registry. A real adapter is added here and nowhere else.
#
# A worked example of what the next entry looks like, kept as a comment rather
# than as dead code so nothing half-wired can be selected in settings:
#
#     class AcmeSkipTraceProvider(EnrichmentProvider):
#         key = "acme"
#         label = "Acme Skip Trace"
#         billable = True
#         required_env = ("WHOLESALE_ACME_API_KEY",)
#
#         def lookup(self, data):
#             if not self.is_configured():
#                 return EnrichmentResult(status=STATUS_NOT_CONFIGURED, provider=self.key,
#                                         message="WHOLESALE_ACME_API_KEY is not set.")
#             try:
#                 ... one HTTP call, short timeout ...
#             except Exception as exc:
#                 return EnrichmentResult(status=STATUS_FAILED, provider=self.key,
#                                         message=str(exc)[:200])
#             return EnrichmentResult(status=STATUS_SUCCEEDED, provider=self.key,
#                                     phones=[...], emails=[...], billable=True)
#
PROVIDERS: Dict[str, EnrichmentProvider] = {
    ManualProvider.key: ManualProvider(),
}


def get_provider(key: Optional[str]) -> EnrichmentProvider:
    """The provider for a settings key, falling back to manual.

    An unknown key falls back rather than raising: a provider removed in a
    future deploy must not make every existing customer's enrichment screen
    throw. The fallback is logged so it is visible rather than invisible.
    """
    if key and key in PROVIDERS:
        return PROVIDERS[key]
    if key and key != ManualProvider.key:
        log.warning("wholesale enrichment: unknown provider %r, using manual", key)
    return PROVIDERS[ManualProvider.key]


def provider_report() -> List[Dict[str, Any]]:
    """What the settings screen renders: every provider and whether it can run."""
    out = []
    for key, p in sorted(PROVIDERS.items()):
        out.append({
            "key": key,
            "label": p.label,
            "billable": p.billable,
            "configured": p.is_configured(),
            "missing_env": p.missing_config(),
            "status": "ready" if p.is_configured() else "not_connected",
        })
    return out


# ── Cost control ────────────────────────────────────────────────────────────
#
# Applied ONLY to billable providers. The manual provider is never capped —
# capping "a person typing a phone number" would be absurd, and a cap of 0 (the
# default) would switch the whole module off on day one.

def admit(db, org_id: str, provider: EnrichmentProvider, settings: Any,
          requested: int = 1) -> Optional[str]:
    """Why this enrichment run is refused, or None to proceed.

    Counts only BILLABLE requests already made in the window, so a month of
    manual entries never eats a paid cap.
    """
    if not provider.billable:
        return None

    from app.models.wholesale_models import WholesaleEnrichmentRequest

    per_run = getattr(settings, "enrichment_max_records_per_run", None)
    if per_run is not None and requested > per_run:
        return ("This run asks for %d records and this organization's limit is %d "
                "per run. Reduce the selection or raise the limit in Wholesale "
                "Settings." % (requested, per_run))

    now = datetime.utcnow()
    for cap_attr, since, label in (
        ("enrichment_daily_cap", now - timedelta(days=1), "daily"),
        ("enrichment_monthly_cap", now - timedelta(days=30), "monthly"),
    ):
        cap = getattr(settings, cap_attr, None)
        if cap is None:
            continue
        used = (db.query(WholesaleEnrichmentRequest)
                .filter(WholesaleEnrichmentRequest.organization_id == org_id,
                        WholesaleEnrichmentRequest.billable.is_(True),
                        WholesaleEnrichmentRequest.created_at >= since)
                .count())
        if used + requested > cap:
            return ("This would exceed the %s paid-lookup cap for this organization "
                    "(%d used of %d in the last window, %d requested). Raise the cap "
                    "in Wholesale Settings, or enter the contact details manually — "
                    "the manual path is never capped."
                    % (label, used, cap, requested))
    return None
