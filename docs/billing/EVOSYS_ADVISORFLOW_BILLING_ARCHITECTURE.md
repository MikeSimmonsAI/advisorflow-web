# EvoSys Pro / AdvisorFlow — Billing Architecture

**Status:** approved architecture, recorded after P0 shipped and during P1.
**Scope of authority:** Sections marked APPROVED are Mike's business decisions and
are not open to reinterpretation while coding. Sections marked REPO FACT were
established by reading this repository and are checkable. Sections marked OPEN
are genuinely undecided and must be resolved by Mike, not inferred.

---

## 0. Why this document exists

P0 shipped before this was written down. The architecture lived in conversation,
and the first thing that happened when a later session picked the work up was
that it had no way to tell an approved business decision from a plausible
guess. This file is the answer to that: it is the thing to read before touching
billing, and the thing to change when a decision changes.

It does not restate what the code already says well. `app/models/billing_models.py`
and `app/models/billing_entity_models.py` carry their own reasoning; this is the
layer above them.

---

## 1. The hierarchy — APPROVED

```
MerchantEntity          EVO INTEGRATED SOLUTIONS LLC   legal seller
    |
Brand / Platform        EvoSys Pro                     customer-facing identity
    |
Customer Organization   e.g. Restland                  the SaaS customer billed
    |
Implementation          deal provisioning + intent     historical snapshot
    |
BillingAgreement        executable billing contract    (P2)
    |
Invoice / Subscription / Payment                       (P0 mirror; ops later)
```

**These are three different things and must never be collapsed into one model.**

- **MerchantEntity** is the legal business that owns the Stripe merchant
  relationship. It signs, invoices, banks, and is audited.
- **Brand** is what the customer recognises — name, domain, logo, colours,
  support address.
- **Organization** is the SaaS customer being billed.

One legal entity can stand behind several brands. Today one LLC sells EvoSys
Pro, BookaBoost and Harmony & Hustle. That is the single strongest reason the
three are separate models rather than columns on one.

---

## 2. Stripe model — APPROVED

**Standard Stripe. No Stripe Connect.**

```
Customer Organization  --pays-->  EVO INTEGRATED SOLUTIONS LLC  --through-->  Stripe
```

A future subsystem may let a tenant charge *their own* customers, and that would
use Connect. It is a **completely separate future subsystem** and nothing in the
current design should be shaped in anticipation of it.

---

## 3. What already exists — REPO FACT

Billing is an **extension of a working Stripe integration**, not a greenfield
replacement. Preserve unless a phase explicitly migrates it:

- `app/routers/billing_router.py` — Checkout, Billing Portal, subscriptions
- `stripe==11.1.1`
- webhook handling
- `frontend/src/pages/Billing.jsx`

Identity models that already exist and must be extended, not duplicated:

| Concept | Model | Table | Notes |
|---|---|---|---|
| Brand | `Platform` | `platforms` | `slug` in {`evosyspro`, `bookaboost`, `harmonyhustle`}; holds domain, logo, colours, support email |
| Customer | `Organization` | `organizations` | already has `platform_id`, `stripe_customer_id`, `stripe_subscription_id`, `stripe_plan_interval`, `billing_status` |
| Deal intent | `Implementation` | `implementations` | see §5 |
| Brand sales org | `BrandSalesOrg` | `brand_sales_orgs` | sales-side, not billing identity |

**`Platform` IS the Brand.** There is no separate Brand model to create.

---

## 4. Pricing authority — APPROVED

Do not redesign pricing. Do not invent term math, free-month math, annual math,
discounts or billing formulas.

```
brand_packages / approved pricing
        |
Opportunity / approved deal
        |
explicit approved custom overrides
        |
Implementation snapshot
        |
BillingAgreement
        |
Stripe
```

- `app/services/package_pricing.py` is the pricing authority. Nothing in billing
  duplicates, re-derives or second-guesses it.
- The legacy hardcoded `PLANS` dictionary **must not remain the long-term
  independent pricing authority**. Its retirement is a later phase and is
  read-only preparation until then.
- Do **not** blindly map `BrandPackage.billing_plan_key` to Stripe pricing.
- **No existing customer is silently repriced.** Ever.

---

## 5. Implementation vs BillingAgreement — APPROVED + REPO FACT

`Implementation` already stores billing intent and history: `billing_option`,
`contract_term_months`, `implementation_fee`, `recurring_amount`, `currency`,
`billing_start_date`, trial fields, `external_billing_ref`.

Its established behaviour: **pricing is copied from the Opportunity at
provisioning and is never recomputed when catalogue pricing later changes.**

Therefore:

- **Implementation** = deal/provisioning billing *intent* + historical snapshot.
- **BillingAgreement** = the *executable* billing relationship the billing
  system runs on.

Do not duplicate Implementation's fields into BillingAgreement without a reason
that survives being written down.

---

## 6. BillingAgreement — APPROVED, BUILT IN P2

Represents the executable customer billing contract.

- Must support **multiple agreements per organization over time**.
- **Historical agreements are never overwritten.** Renewals, upgrades,
  replacements and superseding agreements all preserve prior history.
- A `superseded_by` / successor relationship is the expected shape, subject to
  repo inspection at P2 time.

Links: MerchantEntity, Brand, Organization, Implementation/Opportunity context,
executable terms, Stripe customer, Stripe subscription where applicable.

`Invoice.billing_agreement_id` already exists (nullable, no FK) so P2 links
without a migration.

---

## 7. Division of labour — APPROVED

**AdvisorFlow / EvoSys Pro is the operating interface. Stripe is the financial
execution engine.**

Authorized staff should eventually be able to create invoices, review line
items, send invoices, view status, manage subscriptions, view payment history,
see failed/past-due accounts, open Stripe-hosted resources, and view billing
history per organization. Stripe performs the actual payment processing.

Stripe is authoritative for **whether money moved**. It is *not* authoritative
for **who we are** — see §11.

---

## 8. P0 — COMPLETE (commit 90d3bdb)

Tables: `stripe_webhook_events`, `invoices`, `invoice_line_items`, `payments`.
Services: money conversion, Stripe sync, webhook dispatcher, reconciliation.

Webhook processing is signature-verified, idempotent, retry-safe and
order-tolerant. Failed payments are now visible and set
`billing_status = past_due` where appropriate.

Money is **integer minor units**; conversion happens only in
`app/services/money.py`.

**Do not undo P0.**

P0 deliberately left nullable, un-FK'd seams for later phases:
`Invoice.merchant_entity_id`, `Invoice.platform_id`,
`Invoice.billing_agreement_id`, `Payment.merchant_entity_id`,
`StripeWebhookEvent.stripe_account_id`, `StripeWebhookEvent.merchant_entity_id`.

---

## 9. Tenant security — APPROVED, DEFERRED TO P3

- Do **not** expand or restore `current_user.organization_id` as billing
  authority.
- The pre-existing `_require_admin` problem stays deferred to the
  security/capability phase.
- Future permissions: `billing_view`, `billing_manage`.
- All customer billing access must ultimately be **active-workspace / tenant
  scoped**.
- **Do not sneak the Phase 3 refactor into P1 or P2.** Document the dependency
  instead.

---

## 10. Database and migration reality — REPO FACT

This is the part most likely to be got wrong by someone who assumes a normal
Alembic project.

- Alembic exists, **but production does not run `alembic upgrade`.**
- Production relies on SQLAlchemy `create_all()` plus `run_auto_migrations()`.
- **New model modules must be registered in `app/models/registry.py`** — a
  module the registry does not import is a table `create_all()` never builds.
  `tests/test_model_registry.py` enforces this.
- New tables come from ORM/`create_all()`.
- Columns added to **existing** tables go through `COLUMNS_TO_ADD` in
  `app/auto_migrate.py`.
- **Do not add an Alembic-only migration production will never execute.** It
  reads as applied and is not.
- `auto_migrate` adds plain columns. It does **not** add constraints to an
  existing table — which is why new cross-table links are declared without
  `ForeignKey()` and enforced in a service instead.

---

## 11. Stripe configuration and secrets — APPROVED

**Never store Stripe secret keys in ordinary database fields.** Secrets belong
in environment variables / deployment secret management, which is where the
existing code already reads them from.

Databases may store **non-secret references**: Stripe account ID, customer ID,
subscription ID, invoice ID, payment intent ID, and configuration/status
metadata.

**The application explicitly stores its own legal identity.** Stripe's
`account_name` is external confirmation, never the master record — rename the
Stripe account and every future invoice would otherwise silently change who
issued it.

Current legal merchant, explicitly: **EVO INTEGRATED SOLUTIONS LLC**.

---

## 12. Historical integrity — APPROVED

Paid invoices and historical agreements **must not silently change** when legal
entity data, brand data, package pricing, organization information or
opportunity data changes.

Critical historical information is **snapshotted at issue time and never
refreshed**. Both ids and human-readable names are captured: ids so rows can be
joined later, names so the document still reads correctly after a rename. A
join alone cannot promise that.

---

## 13. Phase boundaries — APPROVED

| Phase | Scope | Status |
|---|---|---|
| P0 | webhook / payment reliability | COMPLETE (90d3bdb) |
| P1 | merchant / entity / configuration foundation | COMPLETE (8652900) |
| P2 | BillingAgreement — executable billing relationship | COMPLETE (4d41357) |
| P3 | tenant billing capabilities / authority cleanup | COMPLETE (4cf170f) |
| P4 | Stripe customer / invoice / subscription / payment operations | COMPLETE (uncommitted) |
| P5 | `PLANS` retirement / pricing migration | not started |
| P6 | organization billing UI | not started |
| P7 | platform billing command centre | not started |
| P8 | reconciliation tooling | not started |
| Later | production activation | not started |

**Do not collapse the phases. Do not move to the next phase automatically.**

---

## 14. P1 — the entity/configuration foundation

P1 answers, concretely:

**Where does MerchantEntity live?**
`app/models/billing_entity_models.py`, table `merchant_entities`, registered in
`app/models/registry.py`, created by `create_all()`.

**How is EVO INTEGRATED SOLUTIONS LLC represented?**
One `MerchantEntity` row: `slug='evo-integrated-solutions'`,
`legal_name='EVO INTEGRATED SOLUTIONS LLC'`, `entity_type='llc'`. Seeded by an
explicit call to `app.services.merchant_entity.ensure_evo_entity()` — never on
import, so importing a model never writes to a database. Idempotent.

**How does a Brand point to its merchant entity?**
New nullable column `platforms.merchant_entity_id`, added through
`auto_migrate` (§10). No `ForeignKey()`, because auto_migrate cannot add
constraints to an existing table; `app/services/merchant_entity.py` is the only
writer.

**How does EvoSys Pro map to EVO INTEGRATED SOLUTIONS LLC?**
`Platform(slug='evosyspro').merchant_entity_id -> MerchantEntity.id`. Set by
`ensure_evosys_pro_configuration()`, which looks the platform up and **never
creates one** — a brand is provisioned by the platform tooling, and inventing
one here would manufacture an identity.

**Where do non-secret Stripe merchant identifiers live?**
On `MerchantEntity`: `stripe_account_id` (`acct_...`, UNIQUE),
`stripe_account_name_cached` + `stripe_account_name_checked_at` (external
confirmation only), `stripe_livemode`, `stripe_config_status`,
`stripe_config_note`.

**Where does an Organization's Stripe customer reference live?**
Where it already lives: `organizations.stripe_customer_id` /
`stripe_subscription_id` / `billing_status`. **P1 adds nothing here** — that
would be duplication.

**What prevents incorrect cross-linking?**
`merchant_entities.legal_name` UNIQUE, `slug` UNIQUE, `stripe_account_id`
UNIQUE (nullable, so many entities may be unconfigured). An entity with no
account id matches **no** webhook — "not configured" must never read as
"matches anything". An unrecognised account resolves to `None` and never falls
back to the default.

**How do existing rows stay valid?**
`platforms.merchant_entity_id` is nullable and **not backfilled**. A platform
that predates the column resolves to the default entity — exactly the behaviour
in place before the column existed. An organization with no `platform_id` does
the same. With no entity configured at all, the snapshot returns `None` rather
than inventing an issuer.

**Why the link is on Platform and not Organization**
Derived, not duplicated. If an organization stored its own issuer, moving that
customer to another brand would leave it pointing at the old legal entity and
nothing would notice until an invoice went out in the wrong company's name.

---

## 15. Decisions NOT inferred — OPEN, need Mike

These change billing behaviour, financial ownership or historical records, and
are deliberately **not** decided in code:

1. **Registered address, jurisdiction and contact details for EVO INTEGRATED
   SOLUTIONS LLC.** Columns exist and are empty. Nobody has supplied the values,
   and inventing an address that prints on an invoice is not a repo-level
   decision.
2. **The live Stripe `acct_...` id.** Not read from anywhere; it is an optional
   argument to the seeding function. P1 makes no Stripe call.
3. **Whether a tax identifier is ever stored, and at what precision.** No such
   column exists. This is a compliance decision.
4. **Whether `organizations.stripe_customer_id` should become UNIQUE.** Two
   organizations sharing a Stripe customer would misattribute invoices across
   tenants. Adding the constraint could fail against existing production data,
   so P1 neither adds it nor pretends the gap is closed. Needs a data check
   first.
5. **Backfilling `platforms.merchant_entity_id`.** P1 links only EvoSys Pro via
   an explicit call. BookaBoost and Harmony & Hustle are unlinked and resolve to
   the default; whether they should be linked explicitly is Mike's call.
6. **Whether existing P0 invoices should be re-stamped** with
   `merchant_entity_id` now that entities exist. This is the backfill script,
   explicitly out of scope.

---

## 16. Known dependencies

- **P2 (BillingAgreement)** needs: this entity layer (done), the
  `Invoice.billing_agreement_id` seam (already present), and a decision on the
  supersede relationship.
- **P3 (tenant authority)** owns: `billing_view` / `billing_manage`, the
  `_require_admin` problem, and workspace-scoped billing access. P1 and P2 must
  not pre-empt it.
- **Invoice issuer stamping** — P0's `stripe_sync` still fills
  `merchant_legal_name` from Stripe's `account_name`. Switching it to
  `merchant_entity.issuer_snapshot()` is a small, deliberate change that belongs
  with P2's invoice work, not with P1's schema.

---

## 17. P4 — Stripe operations — BUILT

**Scope executed:** standard Stripe, test mode only. Customers, subscriptions
from a `BillingAgreement`, draft/finalize/send/void invoices, invoice and
payment history, and one overview read for the P6 UI. No Connect, no tax, no
pricing redesign, no production activation.

**Two modules, and the split is the design:**

`app/services/stripe_gateway.py` is the ONE place this application talks to
Stripe. Three things have to be true of every financial call and none survives
being re-implemented per call site: an idempotency key on every create, errors
that mean something locally, and a Stripe-succeeded-we-failed path that records
the orphaned id instead of losing it. It has no opinion about who may call it
and it does not read the database.

`app/services/billing_operations.py` is the business layer. Every function
takes a `BillingScope` rather than an organization id — that is the tenant
safety design and not a style choice, because a function that accepts an org id
can be called with somebody else's and then the check has to exist at every
call site instead of once.

`app/routers/billing_router.py` gained eleven thin handlers. No Stripe call, no
tenant check and no money arithmetic lives in the router.

**The three rules the operations layer is built around**

1. **Money is copied, never derived.** Subscription amount and currency come
   from the `BillingAgreement`, which copied them from the approved deal. A
   Stripe Price is created to carry exactly those values rather than being
   selected from a catalogue that might have moved. No price list is consulted
   — not `package_pricing`, and emphatically not the legacy `PLANS` dict.
2. **A retry is not a second charge.** The local guard runs BEFORE any Stripe
   call: an agreement that already names a subscription returns it with
   `created: false`. Stripe's idempotency key is the backstop, not the plan.
3. **Stripe succeeded and we failed is a reported event.** Every place a local
   write follows a Stripe create logs the id to the dedicated `billing.orphan`
   logger at ERROR.

**Stripe Product and Price objects are an implementation detail** of executing
an agreement. They are not a pricing source of truth, and nothing reads a price
back out of Stripe to decide what to charge.

**Sandbox enforcement is structural, not procedural.** `assert_test_mode()`
refuses `sk_live_` and `rk_live_` inside the one function that configures the
client. Every phase has been told not to touch live Stripe; this is the line
that makes it true if somebody forgets. Removing it should be its own reviewed
change, at production activation.

Operational detail, endpoint table, the double-charge matrix and the test
summary are in `BILLING_IMPLEMENTATION_STATUS.md`.
