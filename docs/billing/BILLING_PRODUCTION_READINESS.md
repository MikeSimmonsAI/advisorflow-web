# Billing — Production Readiness

**Status: BUILD COMPLETE, SANDBOX VALIDATION NOT STARTED.**

Nothing in this system has ever executed against a real Stripe account, in test
mode or otherwise. Every phase is covered by unit tests with Stripe faked,
which proves what *this code* does with money and with tenancy — the part that
can be wrong in a way Stripe would happily execute. What faked tests cannot
prove is that Stripe behaves as assumed. That gap is what
`STRIPE_SANDBOX_TEST_PLAN.md` exists to close, and it needs a human with a
dashboard.

**This document is not an approval to go live.** It is an honest inventory of
what is built, what still has to be exercised, and what only a person can do.

---

## READY

Built, tested, and reviewed as one system across P0–P8. 396 billing tests
passing; zero regressions against the rest of the application at every phase.

**Money**
- `BillingAgreement` is the authority for negotiated, executable billing.
  Amounts are copied from the approved deal and never recomputed.
- Integer minor units end to end. No float arithmetic anywhere in billing —
  asserted across every billing source file by a single test.
- No frontend calculates money. Every figure rendered is a backend-formatted
  string; the integers travel alongside for anything that must compute.
- Currencies are never summed across each other.
- No MRR/ARR. Normalising a mixed book needs an FX rate and an annualisation
  rule this system has no authority to set — their absence is asserted.
- `PLANS` survives as a self-serve catalogue and display text only, guarded so
  a customer with a negotiated agreement or a live subscription cannot be
  billed from it.

**Charging safely**
- Every Stripe create carries a stable idempotency key.
- Subscription creation is guarded twice: a local check returns the existing
  subscription before any Stripe call, and the key backstops a race.
- The legacy checkout refuses to create a second subscription — a real
  double-charge path that existed before P5.
- Invoice creation supports an opt-in `request_id` for retry collapse.
- Stripe-created-and-we-failed is logged to a dedicated `billing.orphan`
  logger at ERROR, never swallowed.

**Tenancy**
- Customer billing resolves from the ACTIVE workspace, never
  `users.organization_id` — AST-asserted, so a comment cannot hide a real use.
- `billing_view` reads; `billing_manage` acts; both scoped to one organization.
- A real id belonging to another tenant returns the identical refusal as an
  invented one, so no endpoint is an enumeration oracle.
- The back office requires god_admin **and** the non-delegable
  `platform_billing`. A customer holding `billing_manage` is refused every
  cross-organization read and action.
- `platform_scope()` is the one place the tenant guarantee is set aside; it
  takes a loaded row, never an id from a request, and ownership filters still
  apply beneath it.

**Stripe safety**
- `assert_test_mode()` refuses `sk_live_`/`rk_live_` inside the one function
  that configures the client — enforced end to end through HTTP.
- No secret key is stored in any model, reaches any frontend file, or appears
  in any response — asserted.
- Webhook signatures are verified (P0); replay, out-of-order and duplicate
  delivery are all guarded.

**Payment data**
- The mirror holds a method type, a brand and four digits. Never a PAN, an
  account number or a CVV — Stripe does not send them and nothing stores them.
  A payload carrying full account and routing numbers is asserted not to leak.

**Payment methods**
- Not card-only. **No payment method is named as a gate anywhere in billing** —
  no `payment_method_types`, no arrays. The only method names in the codebase
  are a display-label map read with a fallback, so a method Stripe adds
  tomorrow still renders.
- Three flows: one-time setup (broad eligibility, BNPL where Stripe allows),
  recurring autopay (recurring-capable only), manual invoice. Operators and
  customers choose a PURPOSE; Stripe decides eligibility.

**Operations**
- Customer workspace billing screen (P6) and back-office command center (P7),
  separate surfaces with separate authority and no shared code.
- Integrity reconciliation across 22 discrepancy types, dry run with no
  mutation path at all.
- Webhook health: received, processed, failed, stuck, redelivered, repeated
  failure types, ages — and never a payload.
- Safe local-mirror repairs; every business change refused structurally.
- Billing actions audited through the platform's existing audit table.

**Database**
- All three billing model modules are registered in `app/models/registry.py`,
  so `create_all()` creates them. No Alembic dependency anywhere in billing.
- The two columns added to pre-existing tables are registered in
  `auto_migrate.COLUMNS_TO_ADD`. Both assertions are tests.
- Every billing column added is nullable, so existing rows keep working and no
  schema change can prevent a production boot.

---

## SANDBOX VALIDATION REQUIRED

`STRIPE_SANDBOX_TEST_PLAN.md` has 38 numbered steps. None has been run. The
ones that matter most, because they test an assumption rather than our code:

1. **A retried subscribe does not create a second subscription at Stripe.**
   The local guard is tested; Stripe's idempotency key as the backstop is not.
2. **Which payment methods the account actually offers, per flow.** The
   application names none, so what a customer sees is entirely the dashboard's
   configuration. Confirm BNPL appears on a hosted invoice and does NOT appear
   as a subscription's saved method.
3. **A non-card payment renders.** Pay a hosted invoice by ACH or Link and
   confirm the payments table shows a readable method rather than a dash.
4. **The failed-payment and recovery cycle.** Card `4000 0000 0000 0341`,
   then recovery, and confirm the customer screen reflects each state.
5. **Reconciliation against real Stripe changes nothing.** Record a
   subscription's id, amount and status, run the integrity check, compare.
6. **The numbers match Stripe.** Compare open invoice value and payments
   recorded for one organization against the dashboard. A difference is a
   missed webhook, and finding one is what the reconciliation is for.

---

## HUMAN CONFIGURATION REQUIRED

None of this can be done from code, and nothing works until it is.

**Keys — test mode only, never in Git**
| Variable | Value |
|---|---|
| `STRIPE_SECRET_KEY` | `sk_test_...` — a live key is refused by the application |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` for the TEST endpoint |
| `STRIPE_PUBLISHABLE_KEY` | if the checkout flow is exercised from the UI |
| `APP_BASE_URL` | for invoice and portal return links |

They belong in Render's environment configuration. Not in the database, not in
a file, not pasted into a chat.

**Stripe Dashboard, in test mode**
- Webhook endpoint pointing at `POST /billing/webhook`, subscribed to the
  invoice, payment_intent, subscription and checkout events P0 handles. Copy
  the signing secret to the environment.
- Business branding: legal name, support email, address, logo. These appear on
  every hosted invoice and in the portal — the invoice says who is billing.
- Payment method configuration, per flow. This is the decision that determines
  what customers can actually pay with; the application defers to it entirely.
- Invoice settings: default due days, footer, memo.
- Billing Portal configuration: which actions a customer may take
  (payment method update at minimum), and the return URL.
- Record the test account's `acct_...` id and seed it:
  `ensure_evosys_pro_configuration(db, stripe_account_id="acct_...")`. It is a
  non-secret identifier and the only Stripe value P1 stores.

---

## BLOCKERS

**One, and it is procedural rather than technical:**

- **`frontend/dist` is not rebuilt in this worktree.** P6, P7 and P8 all change
  frontend source. `npm run build` in `frontend/` is required before any
  deploy, and the build has been verified clean against a scratch copy (226
  modules, no errors). Related and worth settling separately: `render.yaml`
  publishes `frontend/dist`, `.gitignore` ignores it, and git tracks a **root**
  `dist/`. Which one ships should be decided before the first deploy.

**Not blockers, stated so they are not mistaken for them:** no sandbox run has
happened (that is the next step, not a defect); no live credentials exist
anywhere (deliberate); Connect, Tax and payouts are not implemented (out of
scope by decision).

---

## DEFERRED / FUTURE

**Out of scope by decision, not by omission**
- Stripe Connect — reserved for a future tenant-to-their-customer system.
- Stripe Tax and global tax compliance.
- Merchant payouts and KYC onboarding.
- Radar beyond Stripe defaults.
- Live activation. `assert_test_mode()` should be removed in its own reviewed
  change when that is actually approved — not as a side effect of anything.

**Known gaps, each recorded in `BILLING_FOLLOWUPS.md`**
- Refunds, dunning and payment recovery: nothing creates a PaymentIntent or
  retries a failed payment. Recovery happens in the Stripe portal and arrives
  back by webhook.
- The setup fee has no create-and-send route of its own; it is invoiced from
  the back office by hand.
- Autopay is derived per request rather than mirrored from webhooks, so it is
  not shown in the organization list.
- `organizations.stripe_customer_id` is not UNIQUE. The integrity check now
  detects duplicates, which limits the blast radius; the constraint still wants
  a duplicate sweep first.
- No frontend test runner. Frontend coverage is a backend contract test plus
  source assertions.
- `command_center` and the integrity run read tables into memory. Correct at
  current volume; both want aggregate SQL before they are thousands of rows.
