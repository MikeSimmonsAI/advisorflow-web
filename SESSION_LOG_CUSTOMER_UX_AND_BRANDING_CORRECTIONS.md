# POST-LAUNCH CUSTOMER UX + BRANDING CORRECTIONS

Branch: `fix/customer-ux-branding` (worktree `C:\Dev\af-uxfix`, cut from `origin/main` @ `9f197f6`)
Date: 2026-09-11

A focused correction pass on three production customer-experience defects found
after Billing/Commerce (T2) and Support Intelligence shipped, plus one
white-label leak found live while verifying them.

Billing was not redesigned. Support was not redesigned. No second package
authority and no second branding system were created — every fix consumes the
authority that already existed.

---

## DEFECT 1 — STRIPE EXIT / RETURN PATH

### Before

Four call sites each built their own return URL, and all four ended the same
way:

```
public_identity.public_base_url(org)            the brand's own domain
os.environ["APP_BASE_URL"]                      one value for three brands
"https://advisorflow-frontend.onrender.com"     ours, and nobody's brand
```

- `billing_router._brand_base_url` — subscription checkout
- `billing_router.create_portal` — a SECOND copy of the same chain, which
  still ended in the Render hostname after the checkout path had been fixed
- `deal_billing_router` — seller-assisted subscription and setup-fee checkout
- `catalog_purchase` — add-ons and one-time services

Consequences: a paying EvoSys Pro customer could be returned to an AdvisorFlow
Render hostname their browser has no session for; the subscription cancel URL
carried no `part`, so the page could not say what had been cancelled; and the
hosted invoice/receipt page was a dead end with nothing to click.

### After

New module **`app/services/stripe_return.py`** — the single server-side
authority for where a Stripe flow returns.

- **The base is resolved server-side** from the organization the server loaded
  for the authenticated caller. No request field reaches it.
- **The path comes from an allowlist** (`SAFE_SURFACES`: `billing`, `account`,
  `support`, `home`). A "surface" is a DICTIONARY KEY, never a path fragment,
  so a hostile value is not sanitised — it simply is not one of four words and
  resolves to `billing`.
- **No infrastructure host is ever a destination**, from the platform row, the
  environment or anywhere else. `_clean_base` also rejects scheme-relative
  values, `javascript:`, userinfo (`https://app.evosyspro.live@evil.example`),
  query/fragment-carrying bases, and CR/LF.
- **Unresolved is a refusal**, not a guess: `ReturnTargetUnavailable` → HTTP
  409 with a sentence an operator can act on. Nothing is charged.
- Query values are percent-encoded centrally; an unknown `part` is dropped
  rather than echoed into a URL the customer reads.

Wired into: subscription checkout, subscription cancel, setup-fee checkout,
add-on / one-time-service checkout, seller-assisted checkout, and the billing
portal (`return_url`, now `…/billing?returned=portal` so the page can
acknowledge the round trip).

New endpoint **`GET /billing/return-targets`** serves the app's own
"← Back to billing" / "Open account" actions from the SAME resolver that builds
the URLs handed to Stripe, so the button and the Stripe URL cannot drift.

### What Stripe permits, verified against Stripe's documentation

| Surface | Application-controlled return | Status |
|---|---|---|
| Checkout Session | `success_url`, `cancel_url` | SUPPORTED — used |
| Billing Portal Session | `return_url` (rendered as the portal's own "Return to …" link) | SUPPORTED — used |
| Portal deep-link flows | `flow_data.after_completion.redirect` | supported; not needed yet |
| Hosted invoice / receipt page | brand colour, logo, icon; public business information (support email, website, phone) | CUSTOMISABLE |
| Hosted invoice / receipt page | **a return URL or "back to our app" button** | **NOT SUPPORTED** |

The last row is a real Stripe limitation and is not faked. It is stated once,
in `stripe_return.HOSTED_PAGE_LIMITATIONS`, served on
`GET /billing/return-targets`, and asserted by a test so nobody can later write
a reassurance we cannot keep.

Mitigation on our side of the handoff: receipt links open in a **new tab**
(the signed-in Billing page stays open behind them), the page says so *before*
the click, and the "Back to billing" / "Open account" actions are always
present.

Sources:
- [Stripe — hosted invoice page](https://docs.stripe.com/invoicing/hosted-invoice-page)
- [Stripe — create a portal session](https://docs.stripe.com/api/customer_portal/sessions/create)

---

## DEFECT 2 — PACKAGE DISPLAY WAS STALE

### Root cause

The Change Plan screen rendered `brand_billing_plans.features_json` — a list of
sentences a person typed into `evosys_billing_seed.EVOSYS_PLANS`. It was a
marketing card pretending to be configuration, and it had drifted:

| Shown live | Reality |
|---|---|
| Starter "Up to 2 users" | settled at **1** |
| Growth "AI email + SMS 1,000/mo" | settled at **1,500 SMS**, 5,000 emails |
| Growth "AI voice 300 min/mo" | AI Voice is a **separate add-on** |
| Professional "AI voice 750 min/mo" | likewise |
| Professional "Priority support + 24-month price lock" | **no term was ever configured**; support belongs to the support entitlement authority |
| Professional `max_leads` 7,500 | settled at **10,000** |
| "Enterprise" | the settled tier is **Custom** |

None of it could be corrected without a deploy.

### Fix — capacity becomes data

New nullable columns on `brand_billing_plans` (+ `auto_migrate` entries):
`max_locations`, `email_monthly_allowance`, `sms_monthly_allowance`,
`term_months`.

- `billing_catalog.capacity_for(plan)` builds the card's capacity from those
  columns. **NULL on an enforced dimension (users, leads) reads as
  *unlimited*** — matching `plan_limits.limit_for`, which refuses nothing
  there. **NULL on an unenforced dimension is omitted entirely**: no "0 SMS",
  no invented "unlimited". Zero is treated as unset, for the same reason
  `plan_limits` refuses to enforce a non-positive ceiling.
- **There is deliberately no voice column.** AI Voice is a catalogue add-on;
  a `voice_minutes` column is exactly how the stale line would grow back. A
  test asserts no capacity dimension contains "voice". Lead Scraper likewise
  remains a separate add-on.
- `billing_catalog.commitment_label(commitment, term_months)` now reads the
  plan's own `term_months`. Unconfigured reads **"Term agreement"** — never
  "24-month".
- `GET /billing/plans` and `GET /billing/subscription` carry `capacity`,
  `term_months`, `commitments[].label` / `.term_months`, and a new `support`
  block resolved from **`support_entitlements`** — so "Priority support" on
  the card and the queue the ticket actually joins are the same fact, and a
  brand running on the frozen default is marked `configured: false` rather
  than presenting an assumption as a promise.
- God Mode writes all of it: `PlanIn` and `upsert_plan` accept the new fields,
  and `_plan_full` returns them. **A God Mode edit changes the customer's card
  with no deploy** — asserted end-to-end by a test.

### The seed

`EVOSYS_PLANS` now carries the settled capacity and **empty** feature lists:

| Tier | users | active leads | emails/mo | SMS/mo |
|---|---|---|---|---|
| Starter | 1 | 2,500 | 2,500 | 500 |
| Growth | 3 | 5,000 | 5,000 | 1,500 |
| Professional | 5 | 10,000 | 10,000 | 3,000 |
| Custom | configured / quoted (all NULL, not purchasable) |

Prices are untouched ($500/$597, $1,000/$1,297, $2,000/$2,597). Applying the
seed writes `features_json = []`, which is what **clears the stale sentences
off existing rows**.

`term_months` is **not seeded** — nobody has decided it. Neither are voice or
AI usage allowances. "Not decided" is reported in the seed's `not_seeded`
block rather than guessed.

The Custom tier: **the key stays `enterprise`, the display name becomes
`Custom`.** The key is written into `organizations.billing_plan_key`, Stripe
subscription metadata and historical entitlement snapshots; renaming it to fix
a display string would orphan all three.

### Also cleaned

`concierge_router` (the public marketing chatbot) carried a hand-written rate
card as its fallback — per-tier prices, "AI voice 300 min/mo", "voice 750
min/mo", "Priority support + 24-month price lock", "month 13 free", overage
rates — which it would recite to a prospect whenever the catalogue lookup
failed. It now says plainly that it cannot pull pricing and must not estimate.
The DB-sourced blurb no longer appends invented terms either; it quotes both
configured rates and the configured capacity.

---

## DEFECT 3 — SUPPORT EMAIL WHITE-LABEL LEAK

### Root cause (exact)

`app/services/support_tickets._notify` called:

```python
send_email_via_provider(to_email, subject, body_html)     # no org=
```

`email_service.send_email_via_provider` reads its from-address off whatever it
is handed. Handed nothing, it fell through to the module-level constant:

```python
FROM_EMAIL = os.environ.get("EMAIL_FROM_ADDRESS", "noreply@bookaboost.com")
```

So a support ticket raised inside EvoSys Pro produced an email whose envelope
said BookaBoost. The **body** was already correct — it reads the brand's
display name out of the ticket's own entitlement snapshot — it was the
**envelope**, which is the half a mail client shows first. Nothing chose
BookaBoost; a literal in a `.get()` default did, for every brand on the
deployment.

### Fix

- `support_branding.brand_for_ticket(db, ticket)` — resolves from the
  **ticket's own `platform_id`** first (authoritative: it records which product
  the customer was inside), falling back to the organization's platform as a
  repair path for rows written before the column was populated. Never a
  default, never the first brand in the table, never the environment.
- `support_branding.SupportSendingIdentity` +
  `sending_identity_for_ticket(db, ticket)` — brand-level FROM and REPLY-TO,
  walked as `Platform.support_email` → brand registry → **nothing**, with
  `resolved = True` so an unresolved brand makes the sender refuse rather than
  substitute.
  *Deliberately NOT `public_identity.sending_identity_for_org`*, whose first
  level is `Organization.from_email` — the customer's own sending domain,
  right for mail the customer's business sends to a family, wrong here. A
  support acknowledgement comes from the brand that sells them the software.
- `_notify` resolves that identity, passes it, and **refuses to send** with an
  error-level log when no brand sender exists. A ticket that was not emailed
  about is recoverable; one delivered under another brand's name is not.
- `_email_shell` now wears the brand's face: display name, accent rule, logo
  (**omitted** when the brand has none — never borrowed), a link to the
  brand's own app, and a footer naming the brand's reply address.
- `email_service.FROM_EMAIL` no longer defaults to a brand:
  `os.environ.get("EMAIL_FROM_ADDRESS", "").strip() or None`. A send with no
  resolved identity and no configured address returns a refusal naming the
  three places to fix, instead of an empty From.

Support SLA, severity, queueing, threading and the `is_internal` audience
boundary are untouched — asserted by tests.

---

## FOUND LIVE — `/branding` ANSWERED WITH THE BACKEND'S OWN BRAND

Not one of the three briefed defects; found while verifying them against
production, and the same class of failure.

```
GET https://advisorflow-backend.onrender.com/branding
-> {"brand":"advisorflow","displayName":"AdvisorFlow",
    "supportEmail":"mike@simmonsstrong.com","accentColor":"#f59e0b", ...}
```

`branding_router.get_branding` read the `Host` header only. The frontend is a
static site on the brand's own domain calling the API at
`advisorflow-backend.onrender.com`, so the Host this endpoint saw was always
the **backend's** — and "advisorflow" is a substring of it, so it matched the
AdvisorFlow platform row for every brand's customer. `theme.js` caches that
answer in `localStorage` and applies it synchronously on the next load, so an
EvoSys Pro customer's app chrome took AdvisorFlow's name, accent and support
address from their second page load onward. AdvisorFlow is the engine
underneath and a customer must never see it.

Fix: resolve from **`Origin`** first (the browser sets it from the page's real
address; page script cannot forge it), then `Referer`, then `Host` — and prefer
a candidate that matched a real platform row over one that only matched the
frozen fallback. A same-origin deployment behaves exactly as before.

Second, related: `brand_config.config_for_host` ended with
`return config_for_slug(db, "bookaboost")` — handing an unrecognised host a
real brand's name, colours, website and support address, in the one module
whose docstring says "never guess". It now returns the neutral `UNKNOWN_BRAND`
shape that already existed for exactly this case.

---

## FILES CHANGED

```
NEW   app/services/stripe_return.py
      app/auto_migrate.py                    4 new brand_billing_plans columns
      app/models/billing_models.py           the same 4 columns
      app/routers/billing_router.py          return targets, capacity, support block
      app/routers/branding_router.py         Origin/Referer host resolution
      app/routers/concierge_router.py        stale rate card removed
      app/routers/deal_billing_router.py     server-built return targets
      app/routers/god_billing_router.py      new capacity fields writable
      app/services/billing_catalog.py        capacity_for(), term-aware label
      app/services/brand_config.py           no BookaBoost fallback
      app/services/catalog_purchase.py       server-built return targets
      app/services/email_service.py          no brand in a default; refuse on none
      app/services/evosys_billing_seed.py    settled capacity; features emptied
      app/services/support_branding.py       brand_for_ticket, sending identity
      app/services/support_tickets.py        _notify sends AS the brand
      frontend/src/pages/Billing.jsx         capacity + support card, way-back actions

NEW   tests/test_stripe_return_paths.py          48 tests
NEW   tests/test_support_brand_isolation.py      14 tests
NEW   tests/test_plan_card_authority.py          15 tests
NEW   tests/test_customer_ux_attack.py           12 tests
NEW   tests/test_branding_host_resolution.py      7 tests
      tests/conftest.py                       branded APP_BASE_URL
      tests/test_billing_authorization.py     portal return_url assertion
      tests/test_catalog_purchase.py          patches _return_targets
      tests/test_customer_catalog_routes.py   patches _return_targets
      tests/test_purchase_withdrawal.py       patches _return_targets
      tests/test_sales_catalog_router.py      patches _return_targets
      tests/test_deal_billing.py              asserts the seller-assisted URLs
```

### Test results

| Run | Result |
|---|---|
| Targeted — Stripe return | 48 passed |
| Targeted — support brand isolation | 14 passed |
| Targeted — plan card authority | 15 passed |
| Attack pass | 12 passed |
| Branding host resolution | 7 passed |
| **Full regression** | **3276 passed, 14 skipped, 0 failed, 0 errors (26m56s)** |
| Frontend build | `vite build` exit 0, 303 modules |
| Secret audit (`scripts/_secret_audit.py`) | 5 pre-existing placeholders, **0 LIVE-SHAPED** |

Four existing test files patched `catalog_purchase._brand_base_url`, which the
refactor replaced with `_return_targets`; they now patch the new seam and
produce the same shape the real resolver does. `test_deal_billing` keeps its
(now inert) `_brand_base_url` patches — it never asserted on the base — and
gained real assertions on the seller-assisted return URLs instead.

T6 / T7 / T8 architecture untouched. The only entitlement consumed is the
existing `support_entitlements` resolution, read-only, for the plan card.

---

## OPERATIONAL NOTES FOR DEPLOY

1. **`APP_BASE_URL` is not declared on `advisorflow-backend` in
   `render.yaml`.** It never was. With the Render hostname fallback removed,
   the return path now depends entirely on each active `Platform.domain`
   being set. **Before/immediately after deploy, confirm every active
   platform row has a `domain`** (God Mode → Platform). A brand without one
   now returns HTTP 409 from checkout and the portal with an actionable
   message — fail closed, by design — rather than bouncing the customer to an
   `onrender.com` host. Do NOT "fix" that by pointing `APP_BASE_URL` at a
   single brand's domain on a multi-brand deployment; that is the leak this
   pass removed.
2. The new columns are added by `app/migrate.py` / `auto_migrate` in the
   Render pre-deploy step. All nullable, no backfill, no data change.
3. The settled capacity reaches customers only when a God admin **previews
   and applies** the EvoSys billing seed (`POST /god/pricing/seed/evosys`).
   Deploying the code alone changes no customer's plan row — the card will
   show the columns as they currently stand until the seed is applied.
4. `frontend/dist` is not committed; Render builds the frontend from source.

---

## DEPLOYED AND LIVE-VERIFIED

Merged to `main` as `292c08f` (fast-forward over T8's `8d15ce2`) and pushed;
Render auto-deployed both services.

**Backend** — `GET /health` reports
`{"commit_short":"292c08f","branch":"main","environment":"production"}`.

**The `/branding` leak is closed, live:**

| Request | Before | After |
|---|---|---|
| `Origin: https://app.evosyspro.live` | `advisorflow` / "AdvisorFlow" / mike@simmonsstrong.com / #f59e0b | **`evosyspro` / "EvoSys Pro" / support@evosyspro.live / #087cff, `source: database`** |
| `Origin: https://app.bookaboost.live` | `advisorflow` (same for every brand) | **`bookaboost` / "BookaBoost" / support@bookaboost.live / #c9973d, `source: database`** |
| no Origin (the backend's own host) | `advisorflow` | `advisorflow` — unchanged, and no customer browser produces this |

Both brands resolved `source: database`, which also confirms their platform
rows are real and populated — so `stripe_return` resolves a branded host for
both and the 409 refusal path is not live for either.

**`GET /billing/return-targets`** unauthenticated → `401 Not authenticated`.
It never hands a destination map to an anonymous caller.

**Frontend** — `app.evosyspro.live` and `advisorflow-frontend.onrender.com`
serve the same bundle (200). The served JavaScript carries this pass's
strings: `return-targets`, "Back to billing", "Open account", `returned`,
"Payment pages are hosted by Stripe", "You're back from the secure billing
portal", "Capacity for this plan has not been configured yet", and the
receipt "secure page" note — alongside T8's "My AI Workforce", confirming the
deploy is current.

### What was NOT verified live, and why

- **A real Stripe TEST checkout / portal round trip.** No Stripe test
  credentials or an authenticated session were available in this pass, and
  the brief is explicit that only Stripe TEST may be used for this. The
  return URLs Stripe receives are asserted at the route level instead (the
  checkout test captures `stripe.checkout.Session.create` kwargs), and the
  destinations they are built from are the ones the live `/branding`
  resolution above proves exist.
- **A real support email.** Deliberately not sent: the brief forbids sending
  real support mail to customers to prove branding. The sender, reply-to and
  the whole rendered body are asserted against a recorder in
  `test_support_brand_isolation.py`, in both brand directions and in the
  unresolved case.
- **The settled capacity on production plan cards.** The columns ship empty
  until a God admin previews and applies the EvoSys billing seed; see
  operational note 3 above. Until then the cards render whatever those
  columns currently hold — correctly, from configuration, with unconfigured
  dimensions omitted.
