# Billing Follow-ups

Non-blocking improvements found while building. **None of these stop the
project.** Recorded here so they are not lost and not argued about mid-phase.

## Schema hardening

- **`organizations.stripe_customer_id` is not UNIQUE.** Two organizations
  sharing a Stripe customer would misattribute invoices across tenants. Adding
  the constraint could fail against existing production data, so it needs a
  duplicate check first. Until then `BillingAgreement.stripe_customer_id`
  pins the customer per agreement, which limits the blast radius but does not
  close the hole.
- **`merchant_entities.is_default` is enforced in a service, not the schema.**
  A partial unique index would be stronger but is not portable to the SQLite
  the tests run on. Revisit if the suite ever moves to Postgres.
- **Cross-table billing links carry no `ForeignKey`.** `platforms.merchant_entity_id`,
  `billing_agreements.merchant_entity_id` and `.platform_id` are plain columns
  because `auto_migrate` cannot add constraints to an existing table. Consistent
  with what P0 did, but worth a single migration pass if the deploy mechanism
  ever gains real migrations.

## Correctness / behaviour

- **`Implementation` money is `Numeric(12,2)`; billing is integer cents.** One
  conversion point exists (`money.to_cents`) and is tested, but the two
  representations still coexist. A future pass could move Implementation to
  minor units too.
- **`BillingAgreement.quantity` defaults to `min_units`.** For a per-unit deal
  the contracted minimum is the only quantity known at provisioning time. Real
  usage-based quantity belongs with metered billing, which is not in scope.
- **`supersede()` drops the setup fee by design.** A replacement does not
  re-charge implementation. If a renewal ever should carry a fee, it must be
  passed explicitly in `new_terms`.

## Found during P3

- **The frontend does not send `X-Workspace-Id` on billing calls.** Billing now
  resolves the active workspace, but a multi-workspace user's browser never
  names one, so they fall through to the legacy column - the same organization
  they got before. Not a hole (the fallback is still a workspace they hold),
  but the switcher is not yet real for billing. Wire it in P6.
- **`caps.resolve` gates on admin role before grants.** Billing bypasses that
  wrapper deliberately (see the status document). If other non-administrative
  capabilities appear later, the framework may want a role-optional variant
  rather than each caller re-deriving the two gates.
- **`billing_router.py` contains 42 bare-LF blank lines** from P0 in an
  otherwise CRLF file. Left exactly as found - normalising them would be a
  mass line-ending change. Worth one deliberate pass someday.

## Deferred to their own phases

- **P3:** `billing_view` / `billing_manage` capabilities, and removing
  `current_user.organization_id` as billing authority — see the note in the
  status document.
- **P4:** Stripe object creation, `next_billing_date` / `term_end_date`
  population, invoice operations.
- **P5:** `PLANS` retirement and the existing-customer reconciliation.
- **Invoice issuer stamping:** P0's `stripe_sync` still fills
  `merchant_legal_name` from Stripe's `account_name`. Switching it to
  `merchant_entity.issuer_snapshot()` is what actually retires that stopgap; it
  belongs with P4's invoice work.

## Out of scope, recorded so nobody re-opens them

- Stripe Connect — reserved for a future tenant-to-their-customer system.
- Stripe Tax / global tax compliance.
- Merchant payouts, KYC onboarding.
- Radar beyond Stripe defaults.

## Found during P4

- **`upsert_invoice_from_stripe` returns `(row, ignored_reason)` and flushes
  without committing.** Both are easy to get wrong from a non-webhook caller,
  and both were got wrong in the first draft of `billing_operations._mirror`.
  The signature is fine; a docstring line saying "the caller commits" would
  stop the next caller repeating it.
- **A mirror that declines Stripe's answer is reported as an orphan.** If an
  organization's `stripe_customer_id` does not match the invoice's customer,
  `organization_for_customer` returns None and the finalize or void is
  Stripe-side-only. Correct behaviour, but it means customer-id drift shows up
  as orphan log lines rather than an obvious error. P8's reconciliation should
  treat `operation=Invoice.mirror` as a data-integrity signal, not a retry
  queue.
- **`Payment.refunded_cents` vs `Invoice.amount_refunded_cents`.** The two
  models name the same concept differently. Not worth a migration on its own,
  but a live trap for anyone writing a describe function — it was one here.
- **Stripe Product and Price objects accumulate.** One of each per agreement
  executed, keyed idempotently, and nothing ever archives them. Fine in
  sandbox; worth a housekeeping pass before there are thousands.
- **`gw.call` classifies failures by exception class NAME.** That keeps the
  gateway importable and testable without the stripe library, but a future SDK
  renaming `APIConnectionError` would silently reclassify an outage as a
  refusal. Worth a pinned test if the SDK is upgraded.
- **`create_draft_invoice` has no per-line quantity.** Every line is a single
  amount. Enough for the current deals; a per-unit invoice would need Stripe's
  `quantity`/`unit_amount` pair instead of `amount`.

## Found during P5

- **`PLANS` prices are duplicated in the frontend.** `frontend/src/pages/Billing.jsx`
  carries its own price array and it currently matches the backend dict exactly
  (497/1500, 997/2500, 1997/5000). Nothing keeps them in step. The picker posts
  a plan KEY, so a drift cannot mis-bill — but it can show a customer one price
  and charge another. P6 should serve the catalogue from `GET /billing/plans`
  instead of hardcoding it.
- **`organizations.plan` is a plan KEY doing duty as a price.** For any customer
  on a negotiated rate the key is a label, not a price, and every place that
  reads it for money is a repricing bug waiting to happen. The
  `catalogue_drift` finding exists to make that visible.
- **The `× 11` annual formula lives in the checkout route.** Eleven months
  billed for twelve. Left exactly as-is because P5 does not redesign pricing,
  but it is business pricing logic embedded in an HTTP handler.
- **`propose_legacy_agreement(apply=True)` has no endpoint and no script.** It
  is deliberately callable only from code, one organization at a time, so that
  a bulk migration cannot be launched by accident. If a script is wanted later
  it should read the dry-run report first and refuse anything not `status: ok`.
- **The legacy checkout guards are new refusals.** Two 409s that did not exist
  before. Neither fires for an organization with no agreement and no live
  subscription, which is every customer this route was written for — but they
  are behaviour changes and should be watched after deploy.

## Payment-method architecture — seams for P6/P7

Recorded per the approved billing UX / payment-method requirement. **No
payment method is named anywhere in the billing code today** — no
`payment_method_types`, no method array — so P4/P5 created nothing for P6 to
undo. What is missing is modelling, and it belongs in P6:

- **The three payment flows are not named in code.** `ONE_TIME_SETUP`,
  `RECURRING_AUTOPAY` and `MANUAL_INVOICE` exist as an approved concept
  (architecture §19) and as three call sites that each happen to defer to
  Stripe. They should become one configuration layer that a caller selects by
  *purpose*, so a back-office operator picks "setup invoice" rather than a
  Stripe method list. Keep it abstract enough to hold Stripe payment method
  configuration ids later — they are non-secret references, not pricing
  authority.
- **Autopay is not represented anywhere.** Nothing models autopay
  enabled/disabled, whether a recurring-capable method exists, or "payment
  method requires update". `GET /billing/overview` reports past-due from the
  local mirror but cannot say *why* collection failed or whether it will retry.
- **The payment mirror is card-shaped.** `stripe_sync.upsert_payment_from_stripe`
  reads only the `card` sub-object for brand and last4, so an ACH, Link or
  wallet payment mirrors with no method summary. Display-only and harmless
  today; it becomes wrong the moment a non-card method is used. Widening it is
  a P0-mirror change with its own tests, deliberately not done inside P5.
- **Setup fee and subscription are not separately payable.** `BillingAgreement`
  carries `setup_fee_cents`, and nothing bills it as its own one-time payment.
  The one-time flow — where BNPL is eligible and matters most, on a large
  implementation fee — has no route yet.
- **`Subscription.create` does not assert a recurring-capable method.** It
  relies on Stripe refusing a single-use method. That is probably correct, but
  the refusal should be translated into a sentence naming autopay rather than
  surfacing as a bare 402.

## Found during P6

- **`frontend/dist` is not rebuilt by this work, and the two dist directories
  disagree.** `render.yaml` publishes `frontend/dist`, `.gitignore` ignores
  `frontend/dist/`, and git tracks a **root** `dist/` with 12 files. Whichever
  is authoritative, P6 changes frontend source and needs `npm run build` in
  `frontend/` before deploy. Worth settling which directory ships.
- **`GET /settings/my-capabilities` resolves against `users.organization_id`.**
  Billing routed around it; every other capability-gated nav item still uses
  it, so A2P and System Health visibility are decided for a workspace a
  dual-membership user may not be standing in. Not a billing bug and not fixed
  here, but the same defect class P3 closed.
- **`capabilities.resolve` gates on role before grants.** The reason billing
  needs its own access endpoint at all. Any future capability meant for a
  non-admin specialist will hit this too.
- **The setup fee has no payment route of its own.** `_describe_setup` reports
  `not_invoiced` when an agreement carries a setup fee nobody has billed, and
  the customer can do nothing about it from the screen. The one-time flow —
  where BNPL is eligible and matters most on a large implementation fee — still
  needs a create-and-send path, most naturally from P7's back office.
- **Autopay is derived, not stored.** Every read costs a `Subscription.retrieve`
  and the answer is unavailable during a Stripe outage. Fine for one customer's
  billing page; it will not do for a back-office list of every organization's
  autopay state, so P7 should mirror it from webhooks instead.
- **`Payment.payment_method_type` is only populated going forward.** Existing
  rows keep a null type; a card row still renders from its brand and last4, but
  historical non-card payments stay blank until they are re-mirrored. A
  backfill through `POST /billing/reconcile/{org_id}` would fix it if it
  matters.
- **The billing page is one component.** It is readable at its current size,
  but the invoice and payment tables are the obvious first extraction when P7
  wants the same tables in a back-office view — and they should be extracted
  rather than copied.
- **No frontend test runner exists in this project.** P6's frontend coverage is
  a backend contract test plus source assertions, which catch API drift and
  rule violations but not rendering bugs. Adding vitest + testing-library is a
  dependency decision worth making deliberately, not inside a billing phase.

## Found during P7

- **`command_center` reads every invoice and payment into memory.** Correct and
  fast at current volume, and deliberately simple while the shape of the
  dashboard is still settling. It wants aggregate SQL — `SUM ... GROUP BY
  currency` — before the tables are in the thousands.
- **Autopay is not in the organization list.** It costs one
  `Subscription.retrieve` per organization, so showing it for fifty customers
  would be fifty Stripe calls on a page load. It appears in the detail view
  only. Mirroring autopay state from webhooks (already noted in the P6
  follow-ups) would let the list carry it.
- **The Billing Portal session is the one direct Stripe call in P7.** P4 never
  wrapped `billing_portal.Session.create`, so both the customer router and this
  one call it directly. It goes through `gw.client()` first, so the live-key
  refusal applies, but it is the last billing Stripe call not behind the
  gateway and should move there.
- **No line-item editing on a draft.** The command center creates a draft with
  its lines and then finalizes, sends or voids. Editing lines while still draft
  means deleting and recreating Stripe InvoiceItems, which is a real workflow
  worth having but is its own piece of work.
- **Invoice creation has no `request_id` from the UI.** P5 added opt-in
  duplicate protection to `create_draft_invoice`; the command center does not
  pass one, so a double-submit creates two drafts. Neither charges anything and
  either can be voided, but generating a request id per form open would be one
  line and would remove the possibility.
- **`needs_attention` has no dismissal or snooze.** Every row reappears until
  the underlying fact changes. That is honest, and it will get noisy for a
  known-bad legacy account nobody intends to fix this quarter.
- **The two surfaces are asserted separate by source inspection, not by
  architecture.** A test checks the command center imports nothing from the
  customer page. If a genuinely shared component ever appears — an invoice
  table is the obvious candidate — it should be extracted to a neutral
  component both import, and that test updated deliberately rather than
  deleted.

