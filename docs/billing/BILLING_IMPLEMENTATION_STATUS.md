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

## P3 — billing authority (uncommitted)

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
