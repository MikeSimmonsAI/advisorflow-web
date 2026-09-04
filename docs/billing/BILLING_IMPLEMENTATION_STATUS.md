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

## P4 — Stripe operations (commit 264c452)

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

---

## P5 — retiring `PLANS` as a pricing authority (commit 9de5b62)

**Not a pricing redesign, and nobody was repriced.** The goal was narrow: new,
deal-driven billing must not read the legacy catalogue, and finding out where
the legacy catalogue still matters must be a report rather than a repair.

### The `PLANS` audit — every usage, classified

| # | Location | Class | Verdict |
|---|---|---|---|
| 1 | `billing_router.PLANS` (the dict) | **A + C** legacy compatibility / display | **Stays.** Retiring the dict is not the goal; retiring it as *authority* is. |
| 2 | `GET /billing/plans` → `{"plans": PLANS}` | **C** display-only | **Stays.** The self-serve catalogue a customer may pick from. Bills nothing. |
| 3 | `GET /billing/subscription` → `plan_details` | **C** display-only | **Stays.** Enriches a plan KEY for the UI. `GET /billing/overview` is what reports what the customer is actually billed. |
| 4 | `POST /billing/checkout` → `plan_info = PLANS[req.plan]` | **B** new-customer billing | **Stays, now guarded.** The only remaining path that prices from the dict, and only for an organization with no BillingAgreement and no live subscription. |
| 5 | `billing_migration.catalogue_reference()` | **C** display-only | **New in P5.** Reads `PLANS` purely to put a comparison number in a report, and says so in its return value. |
| 6 | `frontend/src/pages/Billing.jsx` `PLANS` array | **C** display-only | **Stays.** The plan-picker UI; posts a plan KEY, never an amount. Duplicated prices — see follow-ups. |
| 7 | `frontend/src/pages/OrgManager.jsx` `PLANS` array | **C** display-only | **Stays.** Plan-name labels in an admin dropdown. No prices. |
| 8 | Docstrings in `billing_agreement*.py`, `billing_operations.py` | **D** prose | Comments explaining that the dict is *not* consulted. |

No dead (**D**) or test-only (**E**) usage exists. **No usage was deleted.**

### The new billing path

```
brand_packages / approved pricing → Opportunity → approved overrides
   → Implementation → BillingAgreement → app/services/billing_operations.py → Stripe
```

Every amount that reaches Stripe on this path is
`BillingAgreement.recurring_amount_cents`, copied from the approved deal.
Asserted end to end: an organization whose `plan` column says `professional`
($1997 in the catalogue) with an agreement at $499 causes Stripe to be asked
for **49900**.

### The two checkout guards

`POST /billing/checkout` is otherwise unchanged — same catalogue price, same
annual `× 11` formula, same Stripe Checkout session. Two refusals were added in
front of it:

- **409 when a live `BillingAgreement` exists.** That customer has a negotiated
  amount; billing them list price here would be a silent repricing. The
  agreement path is preferred by *refusing* rather than by quietly substituting
  the agreement's number behind a UI still showing the catalogue price.
- **409 when Stripe reports a live subscription.** Previously absent, and a
  real double-charge: an organization already carrying a subscription that
  called this route got a second one. Stripe is asked rather than the local
  column trusted, so a cancelled-and-returning customer can still sign up; if
  Stripe cannot be reached the route refuses with 503, which costs no signup
  that would have succeeded (the route needs Stripe regardless).

Neither guard fires for the customers this route was written for.

### Reconciliation — read-only, and there is no other mode

`app/services/billing_migration.py`. `reconcile_organization` and
`reconcile_all` have **no mutation code path at all** — not a disabled one, not
one behind a flag. A reconciliation tool that can also fix things is one
somebody eventually runs with the wrong flag against production billing.

Findings reported, never resolved:

| Code | Meaning |
|---|---|
| `agreement_vs_stripe` | the agreement and the live subscription name different amounts |
| `deal_vs_stripe` | the approved deal and the live subscription differ — often a legitimate approved discount |
| `live_without_agreement` | Stripe is billing this customer and no agreement exists (the P5 worklist) |
| `subscription_mismatch` | agreement and organization name different subscriptions |
| `stripe_unreadable` | a local subscription id Stripe does not resolve |
| `catalogue_drift` | this customer differs from list price — **information, not a defect** |

| Endpoint | Surface | Requires |
|---|---|---|
| `GET /billing/reconciliation` | customer workspace | `billing_view`, active workspace only |
| `GET /billing/reconciliation/platform` | back office | `god_admin` **and** `platform_billing` |

### Legacy agreement reconstruction

`propose_legacy_agreement(db, org, apply=False)` — dry run unless `apply=True`
is passed explicitly. Evidence priority:

1. **Stripe.** What the customer is actually charged, by the system that
   actually charges them. Not a record of an intention — the intention already
   executed.
2. **The approved deal.** `Implementation.recurring_amount`; a human approved
   this number for this customer.
3. **Local billing columns.** Weakest: a plan KEY is not a price.

**The catalogue is not in that list.** It appears in the output as
`catalogue_reference` so a human can see the gap, and is never consulted for
the amount. Refuses and writes nothing when there is no price evidence, when
Stripe and the deal disagree, or when an agreement already exists. Applying
writes a `source=migration` agreement recording what is already happening —
and makes **no Stripe call at all**, because creating or modifying a
subscription here is exactly the repricing this phase forbids.

### Tests: 35 new, 228 across the billing surface, all passing

Includes both repricing tests (edit `PLANS` under a live agreement; edit
`brand_packages` under a live agreement — the amount does not move), read-only
proofs asserted against the recorded Stripe call list and a field-by-field
database snapshot, and legacy-checkout compatibility. Three deliberate
mutations — a catalogue fallback in the proposal, a quiet local write in
reconciliation, and the removal of the checkout agreement guard — were each
caught before being reverted.

### Limitations

- Nothing migrates legacy customers automatically. `apply=True` is per
  organization, by a human, after reading the proposal.
- `reconcile_all` reads Stripe once per organization; it is an ops tool, not a
  page-load.
- The `× 11` annual formula stays as it is. P5 does not redesign pricing.

---

## P6 — the customer workspace billing screen (uncommitted)

The first user-visible billing phase. **The customer workspace surface only** —
cross-organization back-office billing is P7 and shares nothing with this.

### Where it lives and who sees it

`/billing`, in the workspace navigation under Administration.

The nav item previously sat behind `capability: 'platform_billing'` — a
**platform** capability — while opening the customer's own billing. Two things
were wrong with that and both mattered:

1. `GET /settings/my-capabilities` resolves against `users.organization_id`,
   the legacy tenant column P3 removed from billing precisely because it names
   the wrong organization for anyone holding more than one membership. The
   sidebar was answering for a workspace the person may not be standing in.
2. `capabilities.resolve` refuses a non-admin role **before** it reads grants,
   so a bookkeeper holding `billing_view` — the exact case those capabilities
   were created for — never saw the item.

So P6 added `GET /billing/access`, which runs the same `resolve_billing_scope`
the billing routes run, against the **active workspace**, and reports rather
than refuses. It answers 200 for everyone, including "nothing", because the
sidebar has to tell "denied" from "the request failed".

| Caller | Sees Billing | Can act |
|---|---|---|
| `org_admin` of the active workspace | yes | yes |
| holder of `billing_view` in the active workspace | yes | read only |
| holder of `billing_manage` | yes | yes |
| anyone else | no | no |

**Hiding is not the control.** `/billing` no longer carries `requireAdmin`
either — that locked out the bookkeeper — and every billing route enforces its
own dependency. The security tests call the routes directly with the nav item
hidden.

### Sections on the screen

| Section | Source |
|---|---|
| Past-due / no-payment-method alerts | `past_due`, `subscription.requires_payment_method` |
| Account status strip — status, outstanding balance, next billing date, autopay | `subscription`, `past_due` |
| Your agreement — amount, interval, package, legal seller, brand, term, dates, units | `agreement` |
| **Setup / implementation — its own section** | `setup` |
| Subscription & autopay | `subscription` |
| Invoices — number, issued, due, amount, balance, status, hosted link, PDF | `invoices` |
| Payments — date, amount, method, status, refunded, failure reason | `payments` |

Loading renders skeletons; permission-denied, backend-unavailable and every
empty case render a sentence rather than a broken table.

### Payment methods and autopay

**No payment method is named anywhere in the application** — not in the page,
not in the router, not in the services. Verified by test. Which methods are
eligible is Stripe's answer per flow, and the UI expresses the **purpose**:

- **setup / one-time** — its own section, its own hosted invoice, where Stripe
  presents whatever is eligible including BNPL. Never shown as the recurring
  method.
- **recurring autopay** — reported as a state, updated through the Stripe
  Billing Portal, which is where Stripe decides what may be saved for
  off-session collection.
- **manual invoice** — hosted invoice links, Stripe's own payment surface.

`autopay_active` is three facts, all read from the retrieve `get_subscription`
was already doing (**no new Stripe call**): a live subscription, set to charge
automatically, with a method saved. `null` means **not answered** and renders
as "Unknown" — rendering a Stripe outage as "autopay is off" tells a paying
customer their service is about to stop.

### Backend additions (smallest that would do)

| Change | Why |
|---|---|
| `GET /billing/access` | the only honest source for nav visibility (above) |
| `billing_overview` gains `setup` | the setup fee is a separate payment at a separate time and may use methods a subscription cannot |
| `get_subscription` gains autopay fields | derived from the existing retrieve |
| `describe_payment` gains `payment_method_label` / `payment_method_type` | one safe string, built server-side, so no frontend learns Stripe's method taxonomy |
| `Payment.payment_method_type` column + `auto_migrate` entry | `payments` is an existing table; `create_all()` would never add it |
| `stripe_sync.upsert_payment_from_stripe` widened | **the P5 defect**: it read only the `card` sub-object, so ACH, Link and wallet payments mirrored blank |

No P4 endpoint was redesigned and no billing endpoint was duplicated.

**The mirror still holds only a type, a brand and four digits.** A payload
carrying a full account and routing number is asserted not to leak either into
the mirror or into the response.

### Money

Every amount rendered is a backend-formatted string. Asserted by test: no
division, no `toFixed`, no `Intl.NumberFormat`, no arithmetic on any value
named amount/cents/total/balance/price/fee/outstanding/refunded, and no `_cents`
integer rendered as a whole expression. Integers travel alongside for anything
that must compute.

### Workspace context

Already correct at the transport layer: `api/client.js` sends `X-Workspace-Id`
on **every** request, and the server re-derives the workspace against an active
`customer_org` membership and ignores an id the caller does not hold. P6 added
the tests that prove billing follows it — one person, two workspaces, admin in
one, and the answer moves when only the header changes.

### Tests: 40 new, 268 across the billing surface, all passing

The project has no frontend test runner, so `test_billing_p6.py` covers the
frontend two ways: a **contract test** asserting every key the page renders is
present in `GET /billing/overview` (the drift that actually breaks this
screen), and narrow source assertions for the rules that are invisible at
runtime until they are already wrong in production. Five deliberate mutations —
gating the nav on the platform capability, answering `/access` from the legacy
column, reporting autopay as off during an outage, reverting the mirror to
card-only, and rendering a raw cents integer — were each caught before being
reverted.

### Limitations

- **`frontend/dist` was not rebuilt.** The Linux mount cannot delete files, so
  a vite build cannot run against the repo tree. P6 changes frontend source, so
  `npm run build` in `frontend/` is required before deploy. The build itself
  was verified clean against a scratch copy (224 modules, no errors).
- No invoice creation from the customer workspace. Deliberate: the customer
  surface is for viewing and managing **their** account, and full invoice
  administration is P7's back office. The API supports it; authority was not
  expanded because it exists.
- Payment history is read-only. Nothing here retries a failed payment; recovery
  happens through the Stripe portal and arrives back via the P0 webhook.
