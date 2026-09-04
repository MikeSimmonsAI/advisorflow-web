"""THE ONE PLACE THIS APPLICATION TALKS TO STRIPE.

WHY A GATEWAY AND NOT `import stripe` WHEREVER IT IS NEEDED

Three things have to be true of every financial call, and none of them survives
being re-implemented per call site:

  1. AN IDEMPOTENCY KEY ON EVERY CREATE. Stripe deduplicates a retried create
     only if the retry carries the same key. A browser double-click, a proxy
     retry and a job re-run are all the same request as far as the customer's
     statement is concerned, and the key is what stops them becoming three
     subscriptions.
  2. ERRORS THAT MEAN SOMETHING LOCALLY. A raw StripeError reaching a router
     becomes a 500 and tells the advisor nothing. Here it becomes a typed
     failure that names what could not be done.
  3. STRIPE SUCCEEDED, WE FAILED - RECORDED, NEVER SWALLOWED. If Stripe
     creates an object and the local write then fails, the id must survive in
     the log or that object is orphaned money nobody can find. `log_orphan`
     exists for exactly that moment and is called from the callers that can
     hit it.

TEST MODE ONLY, AND IT IS ENFORCED HERE

`assert_test_mode()` refuses a live secret key. Nothing in this project is
authorised to move real money, and the cheapest place to make that structural
rather than procedural is the one function that sets the api key.

WHAT THIS IS NOT

Not a pricing authority. It converts BillingAgreement amounts into Stripe
arguments and never derives one. Not a business layer: it has no opinion about
who may call it - that is app/services/billing_access.py - and it does not read
the database.
"""

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# The orphan log gets its own logger so a reconciliation run (P8) can find
# these lines without reading everything billing ever printed.
orphan_logger = logging.getLogger("billing.orphan")


class StripeUnavailable(RuntimeError):
    """Stripe is not configured, or could not be reached at all."""


class StripeOperationFailed(RuntimeError):
    """Stripe refused the operation. `code` is Stripe's own, when it gave one."""

    def __init__(self, message, code=None, stripe_object=None):
        super().__init__(message)
        self.code = code
        self.stripe_object = stripe_object


class LiveModeRefused(RuntimeError):
    """A live secret key was supplied. This project is sandbox-only."""


def _stripe():
    try:
        import stripe as _s
    except ImportError as exc:                            # pragma: no cover
        raise StripeUnavailable("The stripe library is not installed.") from exc
    return _s


def assert_test_mode(key: str) -> None:
    """Refuse a live key.

    STRUCTURAL, NOT PROCEDURAL. Every phase of this project has been told not
    to touch live Stripe; a check in the one function that configures the
    client is worth more than that instruction repeated in eight documents.
    Remove this deliberately, in its own reviewed change, when production
    activation is actually approved.
    """
    if key.startswith("sk_live_") or key.startswith("rk_live_"):
        raise LiveModeRefused(
            "A LIVE Stripe key is configured. This build is sandbox-only and "
            "refuses to move real money. Use a test-mode key (sk_test_...).")


def client():
    """The configured Stripe module, or StripeUnavailable.

    The key is read from the environment on every call rather than cached, so
    a deployment that rotates it does not keep using the old one, and so no
    copy of it is held anywhere in this process longer than necessary. It is
    never logged, never returned, and never written to the database.
    """
    key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        raise StripeUnavailable(
            "Billing is not configured: no Stripe secret key in the "
            "environment.")
    assert_test_mode(key)
    s = _stripe()
    s.api_key = key
    return s


def is_configured() -> bool:
    """Whether a Stripe call could be attempted. Never raises - this is for
    screens that want to say "billing is not set up" instead of erroring."""
    try:
        client()
        return True
    except Exception:
        return False


def idempotency_key(*parts) -> str:
    """A stable key for one logical financial operation.

    Built from what makes the operation unique - the agreement id, the invoice
    id, the organization - and NOT from a timestamp or a random value, because
    a key that differs between two attempts at the same operation is the same
    as having no key at all.
    """
    return ":".join(str(p) for p in parts if p not in (None, ""))


def call(fn, *args, idempotency_key_: Optional[str] = None, **kwargs) -> Any:
    """Run one Stripe call, translating its failures.

    Stripe's own exception hierarchy is imported lazily so this module can be
    imported (and most of it tested) without the library present.
    """
    s = _stripe()
    if idempotency_key_:
        kwargs["idempotency_key"] = idempotency_key_
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        code = getattr(exc, "code", None)
        name = type(exc).__name__
        # A connection or authentication problem is not the caller's mistake
        # and must not be reported as a refusal of what they asked for.
        if name in ("APIConnectionError", "AuthenticationError",
                    "APIError", "RateLimitError"):
            logger.warning("stripe unavailable during %s: %s",
                           getattr(fn, "__name__", fn), exc)
            raise StripeUnavailable(str(exc)) from exc
        logger.info("stripe refused %s: %s (code=%s)",
                    getattr(fn, "__name__", fn), exc, code)
        raise StripeOperationFailed(str(exc), code=code) from exc


def log_orphan(operation: str, stripe_id: Optional[str],
               context: Optional[Dict[str, Any]] = None) -> None:
    """Stripe created something and we failed to record it. SAY SO.

    This is the one failure mode that silently costs money: the object exists,
    the customer may be charged by it, and nothing locally points at it. The id
    goes to a dedicated logger at ERROR so P8's reconciliation - and a human
    reading logs today - can find it.
    """
    orphan_logger.error(
        "ORPHANED STRIPE OBJECT: operation=%s stripe_id=%s context=%s. "
        "Stripe holds this object and the local write failed; it must be "
        "reconciled manually or by the P8 tooling.",
        operation, stripe_id, context or {})
