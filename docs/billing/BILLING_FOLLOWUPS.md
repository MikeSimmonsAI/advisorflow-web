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
