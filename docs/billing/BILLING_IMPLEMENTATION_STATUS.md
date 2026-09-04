# Billing Implementation Status

Branch: `feat/billing-p0-reliability`
Architecture: `docs/billing/EVOSYS_ADVISORFLOW_BILLING_ARCHITECTURE.md`

| Phase | Scope | Status | Commit |
|---|---|---|---|
| P0 | webhook / payment reliability | **complete** | `90d3bdb` |
| P1 | merchant entity + brand configuration | **complete** | `8652900` |
| P2 | BillingAgreement — executable relationship | **complete, uncommitted** | — |
| P3 | billing security / tenant authority | **complete, uncommitted** | — |
| P4 | billing backend operations | not started | — |
| P5 | legacy `PLANS` migration | not started | — |
| P6 | customer organization billing UI | not started | — |
| P7 | platform billing command centre | not started | — |
| P8 | reconciliation / operational hardening | not started | — |

---

## P0 — reliability foundation (`90d3bdb`)

Tables `stripe_webhook_events`, `invoices`, `invoice_line_items`, `payments`.
Services: money conversion, Stripe sync, webhook dispatcher, reconciliation.
Webhooks are signature-verified, idempotent, retry-safe, order-tolerant.

**Tests:** 38 (`test_billing_money`, `test_billing_webhook_route`,
`test_billing_webhooks`). Green throughout P1 and P2.

---

## P1 — merchant entity foundation (`8652900`)

**Created**
- `app/models/billing_entity_models.py` — `MerchantEntity` / `merchant_entities`
- `app/services/merchant_entity.py` — resolution, seeding, issuer snapshot
- `tests/test_merchant_entity.py`
- `docs/billing/EVOSYS_ADVISORFLOW_BILLING_ARCHITECTURE.md`

**Modified**
- `app/models/models.py` — `Platform.merchant_entity_id` (nullable, indexed, no FK)
- `app/models/registry.py` — registry import
- `app/auto_migrate.py` — `("platforms", "merchant_entity_id", "VARCHAR")`

**Tests:** 25. **Limitations:** EVO's address/jurisdiction/contact columns are
empty pending real values; no live Stripe account id is recorded; existing P0
invoices are not re-stamped with `merchant_entity_id` (that is the deferred
backfill).

---

## P2 — BillingAgreement (uncommitted)

**Created**
- `app/models/billing_agreement_models.py` — `BillingAgreement` / `billing_agreements`
- `app/services/billing_agreement.py` — conversion, lifecycle, supersession
- `tests/test_billing_agreement.py`

**Modified**
- `app/models/registry.py` — registry import

**Tests:** 26. Combined P0+P1+P2: **89 passed**.

**What it guarantees**
- Amounts are **copied** from the approved Implementation, never recomputed —
  a package price change cannot reprice a live customer (asserted).
- Money crosses into billing **once**, through `money.to_cents`; stored as
  integer minor units thereafter.
- A deal with no approved price **refuses** rather than inventing one.
- Retried provisioning yields **one** agreement — service short-circuit plus a
  unique constraint on `implementation_id`.
- Superseding **never edits** the replaced agreement's terms, dates or Stripe
  references; only status, `ended_at` and the forward link change.
- The commercial snapshot (legal name, brand, org, package) is written once, so
  a later rename does not rewrite history (asserted).
- An agreement cannot be re-pointed at a different Stripe subscription.

**Limitations**
- No Stripe object is created yet — `stripe_subscription_id` is recorded, not
  provisioned. That is P4.
- `next_billing_date` and `term_end_date` are columns only; nothing computes
  them yet (they need Stripe's period data, which arrives with P4).
- `activate()` does not verify a Stripe subscription exists. Deliberate: P2 has
  no Stripe client.
- No router or endpoint yet — service layer only.

---

## P3 — billing authority (commit 4cf170f)

**Created**
- `app/services/billing_access.py` — `BillingScope`, `resolve_billing_scope`,
  `require_billing_view` / `require_billing_manage`, `assert_owns_stripe_customer`
- `tests/test_billing_access.py`

**Modified**
- `app/services/capabilities.py` — registered `billing_view`, `billing_manage`
  (both delegable)
- `app/routers/billing_router.py` — `_require_admin` removed; `/plans`,
  `/subscription`, `/checkout`, `/portal` re-gated

**Authorization model**

| Caller | Access |
|---|---|
| god / platform | per the existing platform model; the SUBJECT organization still comes from the active workspace |
| org_admin of the ACTIVE workspace | baseline `billing_view` + `billing_manage`, that organization only |
| anyone else | explicit two-gate capability grant only |

Tenant authority is `lead_scope.active_workspace_org_id`; the org_admin test is
`lead_scope.effective_role` (the membership role), not `users.role`.
`current_user.organization_id` no longer appears in any billing route.
No route accepts an organization id from the caller, so URL editing and UUID
guessing have nothing to act on.

**Deliberate deviation:** `_has_grant` calls `caps.org_may_self_manage` +
`caps.user_has_grant` directly rather than `caps.resolve`, because `resolve`
refuses non-admin roles before it reads grants — which would make a
bookkeeper grant impossible, the exact case these capabilities exist for.
Both gates still apply. The framework itself is unchanged.

**Tests:** 16, mostly negative. Combined P0+P1+P2+P3: **105 passed**.

**Limitations:** `assert_owns_stripe_customer` is a guard for the operations
P4 adds — no current route accepts a customer id. Frontend still sends no
`X-Workspace-Id` on billing calls, so single-workspace users are unaffected and
multi-workspace users need the header wired in P6.

---

## P4 — Stripe operations (uncommitted)

Customers, invoices, subscriptions and payments. **Standard Stripe, test mode
only. No Connect. No pricing redesign.**

**Created**
- `app/services/stripe_gateway.py` — the one place this application talks to
  Stripe: `client()`, `call()`, `idempotency_key()`, `log_orphan()`,
  `assert_test_mode()`, and the typed failures `StripeUnavailable`,
  `StripeOperationFailed`, `LiveModeRefused`
- `app/services/billing_operations.py` — customers, subscriptions, invoices,
  history, and the billing overview
- `tests/test_billing_operations.py` (68) and `tests/test_billing_router_p4.py`
  (20)

**Modified**
- `app/routers/billing_router.py` — eleven P4 routes appended; `_handle()`
  translates service failures into status codes

**Endpoints** (no organization id anywhere — the subject is the active
authorized workspace)

| Method | Path | Requires |
|---|---|---|
| GET | `/billing/overview` | `billing_view` |
| GET | `/billing/invoices` | `billing_view` |
| GET | `/billing/invoices/{invoice_id}` | `billing_view` |
| GET | `/billing/payments` | `billing_view` |
| GET | `/billing/agreement` | `billing_view` |
| POST | `/billing/invoices` | `billing_manage` |
| POST | `/billing/invoices/{id}/finalize` | `billing_manage` |
| POST | `/billing/invoices/{id}/send` | `billing_manage` |
| POST | `/billing/invoices/{id}/void` | `billing_manage` |
| POST | `/billing/agreements/{id}/subscribe` | `billing_manage` |
| POST | `/billing/agreements/{id}/cancel` | `billing_manage` |

**Failure translation:** 400 refused for this data · 402 Stripe declined ·
503 Stripe unreachable or a live key refused. Never a 500.

**Money**
- Subscription amount and currency are **copied from the `BillingAgreement`**.
  A Stripe Price is created to carry exactly those values; no catalogue, no
  `package_pricing`, and never the legacy `PLANS` dict.
- Invoice line amounts are integer minor units and the type is enforced at the
  request schema and again in the service. `bool` is rejected explicitly
  (it is a subclass of `int`, and `True` would bill one cent).
- No float arithmetic on any billing path. Display strings go through
  `money.from_cents`, and the integer is returned alongside.
- One invoice cannot mix currencies — refused before any Stripe call, so no
  half-built invoice is left behind.

**Double-charge protection**

| Stripe create | Local guard | Idempotency key |
|---|---|---|
| `Customer.create` | existing `stripe_customer_id` short-circuits | `customer:{org_id}` |
| `Product.create` | — | `product:{agreement_id}` |
| `Price.create` | — | `price:{agreement_id}` |
| `Subscription.create` | agreement already naming a subscription returns it, `created=false`, before any Stripe call | `subscription:{agreement_id}` |
| `Invoice.create` | — | opt-in `invoice:{org_id}:{request_id}` |
| `InvoiceItem.create` | attached to one invoice created in the same call | — |

Invoice creation is deliberately opt-in: billing the same organization the same
amount twice is normal, so a content-derived key would silently return the
FIRST invoice and look like it worked. A caller that wants retry collapse sends
`request_id`. Nothing is charged either way — the result is a draft, and
finalizing is a separate explicit call.

**Orphan handling.** Where a local write follows a Stripe create, failure logs
the Stripe id to the dedicated `billing.orphan` logger at ERROR rather than
losing it: customer creation, subscription attachment, partial invoice line
items, and a mirror that refuses Stripe's answer.

**Tenant safety.** Every operation takes a `BillingScope`, never an
organization id, so there is no parameter another tenant's id could go in.
Invoice and agreement ids ARE accepted and are matched **inside** the
organization-scoped query — not fetched and checked afterwards. A real id
belonging to another organization and an invented one produce the identical
400 and the identical body, so the endpoints are not an enumeration oracle.

**Sandbox enforcement is structural.** `assert_test_mode()` refuses `sk_live_`
and `rk_live_` in the one function that configures the client, and the refusal
is asserted end to end through HTTP.

**Tests:** 88 new, 155 across P1–P4, all passing. The suite includes negative
tenant tests for every id-accepting operation at both the service and HTTP
layers. Two deliberate mutations (weakening `finalize` to `billing_view`, and
dropping the organization filter from the invoice lookup) were each caught by
multiple tests before being reverted.

**Limitations**
- Stripe is faked in these tests. Behaviour against the real sandbox is
  `docs/billing/STRIPE_SANDBOX_TEST_PLAN.md`, which a human runs.
- Payments are read from the P0 mirror only. Nothing here creates a
  PaymentIntent or retries a failed payment; dunning and recovery are not
  implemented.
- `cancel_subscription` does not write local status — the P0 webhook owns that
  transition, and writing it here would contradict Stripe for anything
  cancelled at period end.
- `next_billing_date` and `term_end_date` are still columns only.
- No UI. That is P6.
