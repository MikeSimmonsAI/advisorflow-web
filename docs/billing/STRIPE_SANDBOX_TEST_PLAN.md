# Stripe Sandbox Test Plan

**Test mode only.** No live credentials, no live money, no production
activation. Nothing in this plan should be run against a live Stripe account.

## Nothing here has been executed yet

P0–P4 are covered entirely by unit tests with Stripe faked. No sandbox call
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

## P4 steps — now testable, and only by a human with a sandbox

The unit tests fake Stripe, which pins down what THIS code does with money and
with tenancy. What they cannot prove is that Stripe behaves as assumed. These
steps close that gap and each names the assumption it is checking.

8. **Customer creation is idempotent across processes.** Call
   `POST /billing/invoices` twice for an organization with no
   `stripe_customer_id`, concurrently if possible. Stripe must show **one**
   customer. *Assumption: `idempotency_key` on `Customer.create` deduplicates.*
9. **The subscription charges the agreed amount, to the cent.** Execute a real
   `BillingAgreement` via `POST /billing/agreements/{id}/subscribe`, then read
   the Price in the dashboard. `unit_amount` must equal the agreement's
   `recurring_amount_cents` exactly, and the currency must match.
10. **A retried subscribe does not make a second subscription.** Call subscribe
    twice. The second must return `created: false` and the same id, and the
    dashboard must show one subscription. Then clear the local
    `stripe_subscription_id` and call again — Stripe's own key must return the
    SAME subscription rather than creating a second. *This is the one that
    proves the backstop works, not just the local guard.*
11. **Draft, finalize, send.** Create a draft invoice, confirm in the dashboard
    that it is a draft and nothing is being collected, then finalize and send.
    Confirm the hosted URL and the PDF both resolve, and that the email
    arrives.
12. **Void refuses a paid invoice.** Pay a finalized invoice with
    `4242 4242 4242 4242`, then attempt `POST /billing/invoices/{id}/void`.
    Must be refused with a 400 naming refund as the alternative.
13. **`request_id` collapses a retried invoice submission.** Post the same
    invoice twice with the same `request_id`; Stripe must show one invoice.
    Post twice without one; Stripe must show two.
14. **A live key is refused.** Temporarily set `STRIPE_SECRET_KEY` to any
    `sk_live_...` value and call any billing mutation. Must be a 503 naming
    sandbox, and Stripe must show no attempted call. **Restore the test key
    immediately afterwards.**
15. **Cancel at period end does not cut off service.** Cancel a live
    subscription with the default, and confirm in the dashboard that it remains
    active until the period ends. Then confirm the local agreement status is
    still `active` — the webhook, not this call, applies the transition.

## Not yet testable

Refunds, dunning and payment recovery. Nothing in P4 creates a PaymentIntent or
retries a failed payment; payments are read from the P0 mirror only.
