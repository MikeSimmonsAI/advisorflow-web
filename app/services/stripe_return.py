"""WHERE A CUSTOMER LANDS WHEN THEY COME BACK FROM STRIPE.

THE RULE
--------
    THE SERVER DECIDES THE DESTINATION. THE BROWSER MAY NAME A SURFACE,
    NEVER A URL.

Every Stripe flow this platform starts — subscription checkout, the setup
fee, an add-on, a one-time service, the billing portal — hands the customer
to a page we do not own and then hands them back. Before this module each
call site built its own return string, and all of them ended in the same
three-step fallback:

    public_identity.public_base_url(org)        the brand's own domain
    os.environ["APP_BASE_URL"]                  one value for three brands
    "https://advisorflow-frontend.onrender.com" a hostname belonging to us

The third line is the defect. A funeral home that has just paid EvoSys Pro
is returned to an AdvisorFlow Render hostname: a name they have never seen,
on a page their browser has no session for, which tells them who their
software really belongs to. `public_identity` already refuses to hand back an
infrastructure host as a PUBLIC LINK; the billing paths had their own copy of
the fallback and it did not go through that guard.

WHAT THIS MODULE GUARANTEES
---------------------------
1. THE BASE IS RESOLVED SERVER-SIDE from the organization the server loaded
   for the authenticated caller. No request field reaches it.
2. THE PATH COMES FROM AN ALLOWLIST. `SAFE_SURFACES` is the complete set of
   places a Stripe flow may return to. A surface key that is not in it
   resolves to billing; it is never concatenated, never followed.
3. AN INFRASTRUCTURE HOST IS NEVER A DESTINATION. Same marker list
   `public_identity` uses, applied to the environment fallback as well.
4. UNRESOLVED IS AN ERROR, NOT A GUESS. A brand with no domain configured
   raises `ReturnTargetUnavailable` and the caller refuses the checkout with
   a sentence an operator can act on. A checkout that cannot say where the
   customer comes back to is a checkout that should not start.

WHAT STRIPE ACTUALLY SUPPORTS, AND WHAT IT DOES NOT
---------------------------------------------------
Verified against Stripe's own documentation (September 2026):

    Checkout Session         `success_url` and `cancel_url`.        SUPPORTED
    Billing Portal Session   `return_url` — "the default URL to
                             redirect customers to when they click
                             on the portal's link to return to your
                             website".                              SUPPORTED
    Portal deep-link flows   `flow_data.after_completion.redirect`. SUPPORTED
    Hosted invoice page      Brand colour, logo and icon; public
                             business information (support email,
                             website, phone).                       CUSTOMISABLE
    Hosted invoice page      An application-controlled return URL
                             or "back to <us>" button.              NOT SUPPORTED

The last line is a real limitation and this module does not pretend
otherwise. `HOSTED_PAGE_LIMITATIONS` states it in the words the API exposes
to the screen, so the product tells the truth in one place instead of each
page inventing a reassurance. The mitigation is on our side of the handoff:
an invoice or receipt link is opened in a NEW TAB, so the customer's session
with us is still sitting behind it, and the Billing screen carries an
explicit "Back to billing" / "Open account" action either side of every
external hop.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import quote, urlparse

log = logging.getLogger(__name__)


class ReturnTargetUnavailable(RuntimeError):
    """No safe destination exists for this organization.

    Raised rather than returning a plausible host. The caller turns this into
    a refusal the operator can act on — "set the brand's domain" — instead of
    a customer discovering the problem after their card has been charged.
    """


# ── The complete set of places a Stripe flow may return to ──────────────────
#
# AN ALLOWLIST OF PATHS, NOT A VALIDATOR OF URLS. The difference matters: a
# validator has to be right about every hostile string anybody can invent, and
# this only has to contain four entries. Nothing outside this table can be
# produced by any function in this module, whatever the caller passes.
#
# Each path is a real route in `frontend/src/App.jsx`. A key that names a
# route that does not exist would return the customer to a blank page, which
# is its own kind of dead end.
SAFE_SURFACES: Dict[str, str] = {
    "billing": "/billing",     # <Route path="/billing" ... <Billing />
    "account": "/settings",    # <Route path="/settings" ... <Settings />
    "support": "/help",        # <Route path="/help" ... <HelpSupport />
    "home": "/",               # <Route path="/" ... <HomeRedirect />
}

DEFAULT_SURFACE = "billing"

# What was bought, carried back so the confirmation can say which obligation
# the money settled rather than a neutral "payment received".
PART_SUBSCRIPTION = "subscription"
PART_SETUP = "setup"
PART_PURCHASE = "purchase"
PART_ADDON = "addon"
VALID_PARTS = (PART_SUBSCRIPTION, PART_SETUP, PART_PURCHASE, PART_ADDON)


# Hosts that belong to the plumbing. Mirrors
# `public_identity._INFRASTRUCTURE_HOST_MARKERS` deliberately rather than
# importing it, because that list is about links a FAMILY receives and this
# one is about where a PAYING CUSTOMER lands; they happen to agree today and
# either may grow its own entry tomorrow.
_INFRASTRUCTURE_HOST_MARKERS = (
    ".onrender.com",
    ".vercel.app",
    ".railway.app",
    ".herokuapp.com",
    ".netlify.app",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
)

# Stated once, served to the screen, and quoted in the release notes. If
# Stripe ships a return URL for hosted invoices this is the line that changes.
HOSTED_PAGE_LIMITATIONS = [
    {
        "surface": "stripe_hosted_invoice",
        "supported": ["brand colour", "logo", "icon",
                      "public business information (support email, website, "
                      "phone)"],
        "not_supported": "an application-controlled return URL or "
                         "'back to our app' button",
        "mitigation": "Invoice and receipt links open in a new tab, so the "
                      "customer's session with us stays open behind them, and "
                      "the Billing screen carries an explicit Back to billing "
                      "/ Open account action.",
    },
]


def _is_infrastructure_host(host: Optional[str]) -> bool:
    if not host:
        return False
    low = str(host).lower()
    return any(marker in low for marker in _INFRASTRUCTURE_HOST_MARKERS)


def _clean_base(value: Optional[str]) -> Optional[str]:
    """A usable https origin, or None. NEVER a best effort.

    Everything that is not unambiguously an absolute http(s) origin on a
    non-infrastructure host is rejected: a scheme-relative `//evil.com`, a
    `javascript:` URL, an origin carrying userinfo (`https://app@evil.com`),
    a bare path, an empty string. A rejected base is not repaired — a repaired
    base is a guess, and this is the one place a guess sends somebody's
    customer to somebody else's website.
    """
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if "\n" in raw or "\r" in raw or "\t" in raw:
        return None
    if not (raw.startswith("http://") or raw.startswith("https://")):
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    # Userinfo in an origin is how `https://app.evosyspro.live@evil.com` reads
    # as our domain to a person and as someone else's to a browser.
    if "@" in parsed.netloc:
        return None
    if _is_infrastructure_host(parsed.hostname):
        return None
    # A base carrying a query or a fragment would put our parameters after
    # theirs and change what the page reads.
    if parsed.query or parsed.fragment:
        return None
    path = (parsed.path or "").rstrip("/")
    if ".." in path:
        return None
    return "%s://%s%s" % (parsed.scheme, parsed.netloc, path)


def env_base() -> Optional[str]:
    """The configured deployment host, if it is fit to send a customer to.

    Kept as a fallback for a brand whose platform row has no domain yet — the
    same compatibility path `public_identity` keeps for booking links — but it
    goes through `_clean_base`, so the Render hostname that used to sit here as
    a hard-coded literal is now rejected wherever it comes from.
    """
    return _clean_base(os.environ.get("APP_BASE_URL", "").strip())


def base_url_for_org(db, org) -> str:
    """THE ONE RESOLUTION. The brand's own domain, or a refusal.

    Precedence, most specific first:

      1. `public_identity.public_base_url` — the platform row's `domain`,
         then the brand registry. This is the answer in production.
      2. `APP_BASE_URL`, cleaned. A single-brand deployment, or a brand whose
         row has not been given a domain yet.

    There is no third level. The literal that used to be here
    (`advisorflow-frontend.onrender.com`) is exactly the leak this module
    exists to close, and replacing it with a different literal would be the
    same mistake with a different string.
    """
    if org is None:
        raise ReturnTargetUnavailable(
            "No organization: a Stripe return destination is always a "
            "particular customer's brand.")

    resolved = None
    try:
        from app.services.public_identity import public_base_url
        resolved = _clean_base(public_base_url(db, getattr(org, "id", None)))
    except Exception:                                          # noqa: BLE001
        log.exception("stripe_return: brand host lookup failed for org %s",
                      getattr(org, "id", None))
        resolved = None

    base = resolved or env_base()
    if not base:
        log.error(
            "stripe_return: no branded return host for org %s — set the "
            "platform's domain or APP_BASE_URL to the customer-facing host",
            getattr(org, "id", None))
        raise ReturnTargetUnavailable(
            "This brand has no customer-facing domain configured, so there is "
            "nowhere safe to return you to after payment. Set the brand's "
            "domain in God Mode → Platform, or APP_BASE_URL for this "
            "deployment, and try again. Nothing was charged.")
    return base


def base_source(db, org) -> str:
    """Which level answered. Diagnostics only; never a secret, never a URL."""
    try:
        from app.services.public_identity import public_base_url
        if _clean_base(public_base_url(db, getattr(org, "id", None))):
            return "brand"
    except Exception:                                          # noqa: BLE001
        pass
    return "environment" if env_base() else "unresolved"


def resolve_surface(requested: Optional[str]) -> str:
    """Turn whatever the caller sent into one of four known keys.

    THIS IS THE OPEN-REDIRECT DEFENCE AND IT IS FOUR LINES LONG. A caller may
    say "account"; a caller may also say "https://evil.example", "//evil",
    "../../wire-transfer" or a 2KB unicode string. All of the second kind
    resolve to billing, because the value is used as a DICTIONARY KEY and
    never as a path fragment.
    """
    key = (requested or "").strip().lower()
    if key in SAFE_SURFACES:
        return key
    if key:
        log.info("stripe_return: ignoring unknown return surface %r", key[:80])
    return DEFAULT_SURFACE


def url_for(db, org, surface: str = DEFAULT_SURFACE,
            **params: Any) -> str:
    """An absolute, brand-owned URL for one allowlisted surface.

    Query values are percent-encoded here rather than by each caller, so a
    plan key with a space in it cannot break the return URL Stripe validates.
    """
    base = base_url_for_org(db, org)
    path = SAFE_SURFACES[resolve_surface(surface)]
    clean = {k: v for k, v in params.items() if v is not None and v != ""}
    if not clean:
        return "%s%s" % (base, path)
    query = "&".join("%s=%s" % (quote(str(k), safe=""), quote(str(v), safe=""))
                     for k, v in sorted(clean.items()))
    return "%s%s?%s" % (base, path, query)


def _valid_part(part: Optional[str]) -> Optional[str]:
    """A `part` outside the known set is dropped, not passed through.

    It ends up in a URL the customer sees and the confirmation banner reads,
    so an arbitrary caller-supplied string does not belong in it.
    """
    if part is None:
        return None
    value = str(part).strip().lower()
    if value in VALID_PARTS:
        return value
    log.info("stripe_return: ignoring unknown checkout part %r", value[:40])
    return None


def checkout_targets(db, org, *, part: Optional[str] = None,
                     surface: str = DEFAULT_SURFACE) -> Dict[str, str]:
    """`success_url` and `cancel_url` for one Stripe Checkout Session.

    Both land on the SAME surface. A cancel that dumped the customer on the
    home page would leave them hunting for the screen they were on thirty
    seconds ago — the cancellation is the thing that changed, not where they
    were.
    """
    key = resolve_surface(surface)
    part = _valid_part(part)
    return {
        "success_url": url_for(db, org, key, success=1, part=part),
        "cancel_url": url_for(db, org, key, canceled=1, part=part),
    }


def portal_return_url(db, org, surface: str = DEFAULT_SURFACE) -> str:
    """`return_url` for a Billing Portal Session.

    Stripe renders this as the portal's own "Return to <business>" link. It is
    small and it is the only one Stripe gives us, which is why the Billing
    screen also carries its own action either side of the hop.
    """
    return url_for(db, org, resolve_surface(surface), returned="portal")


def describe(db, org) -> Dict[str, Any]:
    """What the app may render around an external handoff, and what it may not.

    Serves the Billing screen's "Back to billing" / "Open account" actions
    from the SAME resolution the Stripe sessions use, so the button the
    customer sees and the URL Stripe was given cannot drift apart. Also
    carries `hosted_page_limitations` verbatim, so the one place the product
    describes Stripe's constraint is the place that knows it.
    """
    try:
        base = base_url_for_org(db, org)
    except ReturnTargetUnavailable as exc:
        return {
            "resolved": False,
            "source": base_source(db, org),
            "detail": str(exc),
            "surfaces": {},
            "hosted_page_limitations": HOSTED_PAGE_LIMITATIONS,
        }
    return {
        "resolved": True,
        "source": base_source(db, org),
        "base_url": base,
        "surfaces": {key: "%s%s" % (base, path)
                     for key, path in SAFE_SURFACES.items()},
        "hosted_page_limitations": HOSTED_PAGE_LIMITATIONS,
    }
