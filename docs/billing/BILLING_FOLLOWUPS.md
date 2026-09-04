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

