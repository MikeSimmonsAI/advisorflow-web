# Private Buyer Deal Room and Shared Intelligence (#415)

One backend, two experiences. Wholesale staff keep the Buyers screen, Deal
Inbox, Foreclosures and Multifamily; invited investors get a restricted portal.
Nothing here is a second buyer table, CRM, contact database, inventory,
messaging platform or matching engine.

## What it reuses

| Need | Existing record / service |
| --- | --- |
| Investor = buyer | `wholesale_buyers` (one canonical buyer per investor; entities via `wholesale_buyer_entities`) |
| Buy box | `wholesale_buy_boxes` (saving in the portal = the buyer's confirmation) |
| Matching | `wholesale_buyer_match` / matching v2 (#413) |
| Concierge | `wholesale_buyer_qualify` reader + `wholesale_buyer_conversations` (`kind` concierge / relationship / deal) |
| What an investor may see | `wholesale_publication.buyer_room_payload` whitelist + `leaked_keys` alarm |
| Deal approval | `wholesale_deal_package` (a listing needs an approved, published package) |
| Interest / offers | `wholesale_buyer_outreach` (channel `deal_room`) |
| Files | `wholesale_files` (25 MB default, sniffed types, DB / disk / S3) |
| Signatures | `evosys_esign` envelopes + approved `wholesale_contract_docs` templates |
| Work queue / bell | `wholesale_exceptions`, `wholesale_notify` |

## New tables

`investor_invites`, `investor_accounts`, `investor_entity_roles`,
`deal_room_listings`, `deal_room_holds` (partial unique index: one active hold
per listing). New columns on `wholesale_buyer_conversations` and
`wholesale_files` are added by `auto_migrate`.

## Access

* Investor logins are `users.role = 'investor'` with no organization. `app/deps.py`
  fences them to `/investor/*` plus refresh / logout / password / sessions.
* Investors sign in at `/investor/auth/login` (frontend `/investors/login`);
  invitations are single-use, hashed, expiring tokens (`/investors/invite/:token`).
* Staff endpoints live at `/wholesale/deal-room/*` and `/wholesale/intel/*`,
  organization-scoped (another workspace's id is a 404) and covered by
  `tests/test_wholesale_cross_tenant.py`.

## Listing states

DRAFT → APPROVED → PUBLISHED → (EXCLUSIVE REVIEW) → EXPIRED / WITHDRAWN / CLOSED.

## Exclusive review

A temporary visibility hold: 24h / 48h / 72h / 7d / custom (max 30 days),
approved by an owner or wholesale manager, atomic (row lock + unique index),
real countdown, auto-reopens on expiry, existing offers are kept. Every screen
states: "It is not an option, a contract or a right to purchase."

## Uploads

PDF, JPEG, PNG, WEBP, DOCX, XLSX. Limits: `DEAL_ROOM_MAX_FILE_MB` (default 25),
`DEAL_ROOM_BUYER_QUOTA_MB` (default 250). Built-in content checks block PDF
JavaScript / launch actions, Office macros, executables and legacy `.doc`.
No antivirus engine is connected; the file record says so.

## Shared intelligence (`/wholesale/intel`)

* **Verification** - every property fact as VERIFIED / LIKELY / UNVERIFIED /
  CONFLICTED / STALE / UNKNOWN with source, original value, source date,
  retrieval date and the next free lookup. Official records outrank statewide
  layers, which outrank claims; a weaker source never overwrites a stronger one.
* **Underwriting** - `strategy=wholesale|investment|both`. Wholesale reuses
  `evosense.economics.preliminary` and the deal's own prices; Investment reuses
  `evosense.type_details` (NOI, cap rate) and labels debt service / cash flow as
  scenarios from the assumptions entered. Unknown stays unknown.
* **Deal Opportunity Report** - internal JSON / printable HTML with all eleven
  sections; an investor-safe version built only from the published package.
* **Providers** - ATTOM, PropStream (CSV import), Tracerfy, Reonomy, Trepp
  readiness only. Nothing is activated or purchased.

## Not connected / limits

* The portal concierge uses the rules reader when the AI gateway refuses an
  unauthenticated actor (investor sessions are not staff actors).
* Real investor email delivery uses the existing email path; practice
  (`.invalid`) invitations are never sent.
