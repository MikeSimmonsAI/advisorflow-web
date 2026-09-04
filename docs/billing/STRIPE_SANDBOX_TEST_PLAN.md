# Stripe Sandbox Test Plan

**Test mode only.** No live credentials, no live money, no production
activation. Nothing in this plan should be run against a live Stripe account.

## Nothing here has been executed yet

P0–P2 are covered entirely by unit tests with Stripe mocked. No sandbox call
has been made from this session, and none can be until test-mode credentials
exist in the environment. Secrets come from environment/deployment secret
management — never from chat, never from the database, never committed.

## Environment required

| Variable | Purpose |
|---|---|
| `STRIPE_SECRET_KEY` | test-mode key (`sk_test_...`) |
| `STRIPE_WEBHOOK_SECRET` | signing secret for the test webhook endpoint |
| `STRIPE_PUBLISHABLE_KEY` | if the checkout flow is exercised from the UI |

## Steps that need a human and a Stripe dashboard

1. **Create or confirm the test-mode account** and record its `acct_...` id.
   Then seed it locally — the id is a non-secret identifier and is the only
   Stripe value P1 stores:
   `ensure_evosys_pro_configuration(db, stripe_account_id="acct_...")`
2. **Add a webhook endpoint** in test mode pointing at
   `POST /billing/webhook`, subscribed to at least the events P0 already
   handles. Copy the signing secret into the environment.
3. **Verify signature rejection** — send an unsigned and a wrongly-signed
   payload; both must be refused with nothing written.
4. **Verify idempotency** — redeliver the same event from the dashboard; the
   ledger must record one processed event and the invoice must not change.
5. **Verify out-of-order delivery** — replay an older event after a newer one;
   the older must not overwrite newer state.
6. **Failed payment path** — use test card `4000 0000 0000 0341`; confirm the
   invoice mirrors as failed and `billing_status` becomes `past_due`.
7. **Recovery path** — pay the failed invoice; confirm status returns to active.

## Not yet testable

Subscription creation from a `BillingAgreement`, invoice creation/finalise/send,
and refunds are P4 — the code does not exist, so there is nothing to exercise
in sandbox for them yet.
