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

* The concierge uses the AI gateway on an authorized investor path (below); the rules answer, labelled,
  when the AI is off, capped or unavailable.
* Real investor email delivery uses the existing email path; practice
  (`.invalid`) invitations are never sent.

## Owner experience (Oct 27 follow-up)

* **Sidebar:** Wholesale now has a **Buyers** group (Buyers, Deal Room) right after Acquisition.
* **Buyers command center** (`/wholesale/buyers`): Buyer Intelligence header, live metrics, three primary
  actions (Find buyers, Invite investor, Open Deal Room), always-visible quick filters.
* **Deal Room** (`/wholesale/deal-room`): overview, investors, listings, exclusive reviews (pending / active /
  expiring soon / completed), messages (assign to staff), documents & signatures, activity, settings.
* **Setup** (`deal_room_settings`): five steps, prefilled from the workspace, saved server-side. Activation
  re-checks requirements and returns the real result. While not active, only practice invitations and
  listings work. Settings drive defaults (audience, match threshold, closing window), hold rules (allowed
  lengths, custom, note / proof-of-funds requirements, open requests per investor), notifications and the
  AI concierge switch.
* **Add deal:** candidates with package status and the exact missing step; the package panel and package
  approval are reachable from the same drawer; audience, availability window and visible documents are
  chosen before publishing. A listing can only hide documents the package publishes, never add them.
* **Preview:** `/investors/preview` renders the investor design from `/wholesale/deal-room/portal-preview`
  (staff session, read-only).
* **Package rule:** a listing is visible only while its investor package is published and unchanged since
  approval (`wholesale_deal_package.serving`).

## AI concierge (authorized path)

Investor messages are manual actions by an authenticated user, so they go through the AI gateway in
`manual` mode with `actor="investor:<user id>"` (subject to `AI_MANUAL_ACTIONS_ENABLED`). The model sees only
`authorized_context()` - the investor's own profile and the listings visible to them through the same
visibility rules. Every figure in an answer must appear in that context or the answer is discarded and
the team is asked. Per-process ceilings: `DEAL_ROOM_AI_PER_HOUR` (default 30 per investor) and
`DEAL_ROOM_AI_PER_ORG_DAY` (default 400). When the AI is off, capped or unavailable the rules answer and the
turn is labelled `engine: automatic` ("automatic reply"); AI turns are labelled `engine: ai`.

## Dashboards (Oct 28 visual correction)

* **Owner dashboard** (`/wholesale/deal-room`) is served by `GET /wholesale/deal-room/dashboard` (metrics, growth
  series, status checks, live listings) and `GET /wholesale/deal-room/feed?kind=activity|holds|signups|messages`. Sub-views
  live at `/wholesale/deal-room/<view>` (`?view=` still works). Sidebar Buyers group: Buyer Intelligence, Deal Room,
  Investor Outreach (`/wholesale/deal-room/investors`), Buy Boxes (`/wholesale/buyers/buy-boxes`, a read view over buyer
  intelligence rows).
* The growth chart and the investor "Opportunities Over Time" chart only render with two or more months of real data;
  otherwise an empty state / opportunity summary is shown. Status items never say "enabled" without a server check.
* Investor criteria add `min_cash_flow` (number), `timeline` (`0_30|30_60|60_90|90_plus`) and `preferences` (≤500 chars)
  inside the buy box's `criteria` JSON - no new columns.
