# Billing Implementation Status

Branch: `feat/billing-p0-reliability`
Architecture: `docs/billing/EVOSYS_ADVISORFLOW_BILLING_ARCHITECTURE.md`

| Phase | Scope | Status | Commit |
|---|---|---|---|
| P0 | webhook / payment reliability | **complete** | `90d3bdb` |
| P1 | merchant entity + brand configuration | **complete** | `8652900` |
| P2 | BillingAgreement — executable relationship | **complete, uncommitted** | — |
| P3 | billing security / tenant authority | not started | — |
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
