# T2 — BILLING + COMMERCE: COMPLETION REPORT

**Status: shipped to production and live verified.** One cleanup item is
outstanding and needs a signed-in session (details at the end).

Commits, in order, all on `origin/main`:

| Commit | What |
|---|---|
| `aababc6` | Slices 2 + 3 — customer purchase, seller-assisted sales, entitlements |
| `6cbe8ea` | An add-on must not make a scheduled downgrade disappear |
| `becf00d` | A forgotten schedule id must not kill the downgrade button |
| `0bb8c28` | A sale a seller makes, a seller can unmake |

Final regression: **2957 passed, 14 skipped**. Frontend production build clean.
All four commits are deployed and answering live.

---

## 1. WHAT A CUSTOMER CAN DO

On their own Billing page, under the plan:

* **Buy a recurring add-on.** It joins their EXISTING subscription as an item
  and appears on their next invoice. Nothing is charged at the moment of
  purchase; Stripe prorates by its own rules.
* **Buy a one-time service.** A `mode="payment"` checkout that starts no
  subscription and carries no `subscription_data` at all. It is PENDING until
  the webhook says the money arrived — opening a link is not paying.
* **Remove an add-on.** `SubscriptionItem.delete`, never `Subscription.delete`.
  A customer dropping an add-on has not asked to stop being a customer.
* **Withdraw an unpaid checkout**, which expires the link at Stripe.

The browser names an ITEM and a QUANTITY. There is no amount field on the
request model, so a tampered request buys nothing cheaper — the same structural
guarantee the plan catalogue already had.

## 2. WHAT A SELLER CAN DO

`/sales/customers/:orgId/catalogue`, reachable from **Sold / Onboarding**.

Same engine as the customer's own button, so a rep cannot produce an outcome
the customer could not have produced themselves. What differs is the gate:

* **Only their own brands' customers.** Being a sales member somewhere is not
  permission to sell everywhere. A customer outside the seller's brands answers
  404, not 403 — a refusal that distinguished the two would turn the route into
  a directory of other brands' customers.
* **A fixed item cannot be repriced on a deal.** Not by a little, not with a
  note. An item that should be negotiable is configured as QUOTED, which is a
  God Mode decision rather than a rep's.
* **A quoted item needs an amount AND a sales manager**, resolved through the
  existing `pricing_authority.actor_role` machinery rather than a second rule
  that would drift from the package engine's.
* **Resending a link returns the SAME link.** Two live links for one obligation
  is a customer paying twice.
* **Unmaking a sale**, which is new — see §4.

## 3. DEFECTS FOUND IN CODE THAT WAS ALREADY SHIPPED

The catalogue did not just add features; it exposed four things that were
already wrong and would have stayed wrong.

### 3.1 `apply_subscription` read `items[0]` to find the plan

A subscription has SEVERAL items the moment anybody buys an add-on, and Stripe
promises no order. With the add-on first, the plan, the commitment and the
interval could all have come from a $25 line item — and a scheduled downgrade
landing without `plan` metadata, which is the exact case price-first resolution
was built for, would have silently failed to sync.

The plan item is now the one whose price this brand's catalogue recognises,
whatever position it sits in, and the renewal date comes from THAT item rather
than from whichever item happened to have the furthest-out period. An add-on
attached mid-period can carry its own dates; taking the maximum would have
pushed a customer's renewal date out by whatever the newest add-on said.

### 3.2 An add-on made a scheduled downgrade disappear — FOUND LIVE

ZZ Launch Verify A had a commitment-only downgrade booked for Oct 10. Selling
it an add-on made Stripe send `customer.subscription.updated` on the unchanged
plan, and the pending markers were cleared. The platform forgot a change Stripe
still had booked, and nothing would have said so until the rate moved on its own.

Every guard in front of it was reasonable and none caught it: the pending tier
matched because a commitment-only change never changes the tier, and the pending
commitment was NULL because the change was recorded before that column existed —
precisely the case the NULL escape hatch was written to be kind to.

The rule now, in three cases:

* the subscription actually MOVED onto the pending target → landed, clear the
  markers, whatever date was written down (a schedule can fire early, and an
  operator can apply a change by hand);
* the target is INDISTINGUISHABLE from where the customer already was → nothing
  in the price can say, so the effective DATE decides, and a future date means
  it has not landed;
* the target does not match → not landed.

### 3.3 The platform could not answer "is it still booked"

No webhook carries a Subscription Schedule, so when a scheduled downgrade
looked wrong there was no way to check from inside the product — the refresh
button reported "already matches Stripe", which was true of the mirrored
columns and beside the point.

Refresh now reads the schedule too and names a disagreement in either
direction: Stripe holding a change the screens are not showing, or screens
promising a date Stripe will not act on. A schedule Stripe will not return
reports as UNKNOWN rather than as absent — "there is no change booked" and "we
could not ask" are opposite facts.

**This is what found and proved 3.2 in production.**

### 3.4 A forgotten schedule id killed the downgrade button — FOUND LIVE

Repairing 3.2 on the live record hit the next one. With `stripe_schedule_id`
cleared, the scheduling path believed there was no schedule and tried to CREATE
one — and Stripe refuses a second schedule on a subscription that already has
one. That customer's downgrade button was dead permanently, with nothing on any
screen explaining it.

Stripe knows what exists there; the column only remembers. When there is no
usable stored id the subscription itself is now asked which schedule it has, and
the change modifies that one. Creating is the last resort rather than the
assumption — which is what "one schedule per subscription, ever" meant.

### 3.5 God billing's MRR described the plan and only the plan

A customer paying $997 plus $250 of add-ons read as $997, on the screen finance
totals from. Add-ons now count, reported APART from the plan figure so a tier
total and a revenue total never have to be the same number. Monthly add-ons
only: a yearly one's monthly equivalent is a conversion nobody has specified,
so it is excluded and said so rather than guessed at.

### 3.6 Ending a subscription left its add-ons ACTIVE

Stripe deletes the items with the subscription, so AdvisorFlow went on claiming
a cancelled customer paid for things nobody was charging them for — and went on
granting the capacity those add-ons bought. Paid one-time purchases are
untouched: a migration somebody paid for was delivered.

### 3.7 Two tenant-isolation holes in the seller route

`require_sales_member` only establishes that somebody sells for SOMEBODY. A rep
at one brand could have priced and charged another brand's customer, and could
have collected another brand's live payment links by walking purchase ids. Both
are now scoped to the brands the seller actually sells for.

## 4. ENTITLEMENTS ARE WIRED, NOT CLAIMED

Purchased capacity reaches `plan_limits.limit_for` — the guard that refuses the
eleventh user. Selling five seats and then refusing the sixth is the platform
taking money for something it withholds.

* It raises a real ceiling and never invents one. Against an unlimited
  dimension it stays unlimited: a purchase meant to remove a cap must not
  create one.
* Only LIVE purchases count. A pending checkout is somebody who has not paid.
* `entitlement_state` reports the base ceiling, what was bought, and the
  effective ceiling separately, so "why can they add seven users on a two-user
  plan" is answerable.
* A grant keyed to anything the platform does not enforce is refused at
  CONFIGURATION time and the item is unsellable. An item claiming capacity
  nothing reads would sell fine, bill fine, and do nothing — and the person who
  found out would be the customer who paid to raise a ceiling and then hit it.

## 5. A SALE A SELLER MAKES, A SELLER CAN UNMAKE

Selling was one-way. A rep could attach an add-on and nothing in the sales
workspace could take it off; the only remove control lived on the customer's own
Billing page, which is no help for a customer whose account has no users yet —
the exact customer a rep sells to during onboarding.

One action, and the purchase's state decides what it means:

* ACTIVE add-on → removed from the subscription, plan untouched, key freed so
  the correction does not leave them unable to buy what they meant;
* PENDING checkout → withdrawn AND expired at Stripe. A row saying "withdrawn"
  beside a link that still takes money is the worst of both records;
* PAID → refused. Money that arrived is a refund conversation with its own
  authority, and flipping the row would hide it rather than settle it.

## 6. LIVE VERIFICATION (Stripe TEST mode, ZZ Launch Verify A)

Everything below was done on the deployed site, end to end.

| Check | Result |
|---|---|
| Configure two items in God Mode, enable for customer + seller | ✅ |
| Provision to Stripe TEST | ✅ both Mapped, no duplicates |
| Seller-assisted sale of a recurring add-on, quantity 2 | ✅ joined the EXISTING subscription |
| No second subscription created | ✅ ACTIVE subscriptions stayed at 1 |
| Duplicate sale refused | ✅ offer greyed out, "They already have this." |
| Seller-assisted sale of a one-time service | ✅ `cs_test_…`, PENDING, no subscription |
| Resend returns the SAME link | ✅ identical session id, no second checkout |
| MRR reporting | ✅ $1,000 + $2 add-ons = $1,002 |
| Setup fee untouched by any of it | ✅ still Paid $1,500 · Sep 10 |
| Next bill untouched | ✅ Oct 10, 2026 |
| Webhook processed the item addition | ✅ plan and commitment survived (the §3.1 fix) |
| Tenant isolation | ✅ enforced in code and covered by tests |
| Idempotency | ✅ covered by tests; Stripe's own retry visible in the event log as `duplicate` |
| Pending downgrade lost, then detected | ✅ found by the new schedule reporting |
| Pending downgrade repaired | ✅ → Month-to-month Oct 10, 2026, via the existing Stripe schedule |

## 7. OUTSTANDING — ONE CLEANUP, NEEDS YOUR LOGIN

The god session signed out after the last deploy, and I can't sign back in —
entering a password is the one thing I won't do. Nothing is wrong; these are
test records in Stripe TEST mode on a test customer, so no real money is
involved either way.

Three things to clear when you're next in, all on ZZ Launch Verify A:

1. **Sales → Sold / Onboarding → ZZ Launch Verify A → Sell add-ons.**
   Click **Remove** on "ZZ Add-on Check" ($2/mo) and **Withdraw** on
   "ZZ Service Check" ($2, awaiting payment).
2. **God Mode → Billing → EvoSys Pro → Products & Services.**
   Untick **Active** on `zz_addon_check` and `zz_service_check`
   (`zz_live_check` is already off).
3. Nothing else to undo. The ZZ A subscription, its setup fee and its
   Oct 10 downgrade are all in their correct state and should be left alone.

The two Stripe TEST Prices those items created can stay — they cost nothing and
are named for the brand.

## 8. WHAT WAS DELIBERATELY NOT DECIDED

Each of these is a commercial policy, not an engineering gap. Nothing guesses
at any of them, and each refuses or reports rather than inventing an answer.

* **No EvoSys Pro add-on prices were invented.** The only items configured are
  the two named `zz_*` test items above. The real list — AI Voice, Lead
  Scraper, additional users, capacity packs, support tiers, services — is yours
  to price in God Mode, which is where the master commercial configuration
  lives.
* **No commission on services.** Whether selling a service earns compensation
  is a policy question with its own engine; the payment is recorded with the
  reason stated, so an unpaid commission cannot be mistaken for a bug.
* **No monthly equivalent for a yearly add-on.** Excluded from MRR and said so.
* **No overage billing**, unchanged: capacity purchased raises a ceiling; it
  does not meter anything.
* **Entitlement grants are limited to what the platform enforces today**
  (`max_users`, `max_leads`). Adding SMS or voice capacity as a grantable
  dimension means teaching `plan_limits` to enforce it first — otherwise the
  item is refused at configuration time, on purpose.

## 9. TEST COVERAGE ADDED

| File | Covers |
|---|---|
| `test_catalog_purchase.py` | the purchase engine: add-on shape, one-time shape, removal, pricing, entitlement totals, what the customer is shown |
| `test_customer_catalog_routes.py` | the customer's own routes: no price field, self-service only, tenant scope |
| `test_sales_catalog_router.py` | the seller gate: brand scope, repricing refused, quoted authority, resend |
| `test_catalog_webhook.py` | each obligation keeps its own money; idempotency; cancellation cascade |
| `test_purchased_capacity.py` | capacity reaching the guard; unlimited stays unlimited; unenforceable grants refused |
| `test_addon_revenue_reporting.py` | add-ons in MRR, reported apart from the plan |
| `test_purchase_withdrawal.py` | unmaking a sale, and what it refuses |
| `test_schedule_recovery.py` | one schedule per subscription, including when the id was forgotten |
| `test_pending_change_survives.py` | a future-dated change is not cleared by an unrelated event |
| `test_billing_resync.py` | the schedule Stripe still has, and naming the disagreement |

Roughly 180 new tests across the workstream.
