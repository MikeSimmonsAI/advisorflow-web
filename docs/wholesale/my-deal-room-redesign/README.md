# My Deal Room workspace redesign: route map, findings, evidence

Branch `redesign/my-deal-room-workspace`. The changes apply only to the **My Deal Room** shell: EvoSys Pro brand, inside `/wholesale`, outside OLP and outside a configured vertical (`isMdrShell` in `frontend/src/components/mdr/MdrShell.jsx`). BookaBoost Wholesale and the OLP workspace are unchanged; `tests/test_mdr_redesign_browser.py::test_bookaboost_keeps_its_rail` checks this.

## 1. Inventory and old-to-new route map

The inventory comes from the code, not the screenshots: every `/wholesale` route in `frontend/src/App.jsx`, every rail item in `NAV_GROUPS` (`frontend/src/components/Layout.jsx`) with its flags, the custom-access module map (`WS_SCREEN_MODULE` in Layout and `app/services/wholesale_access.py`), and the field-login allow list. No route was removed or renamed, so deep links and bookmarks still work.

Legend for the access column:
- `feature`: `wholesale_real_estate` entitlement (all rows). The route guard and the rail use the same key.
- `admin`: `adminOnly` (org admin or workspace manager).
- `ws-admin`: `workspaceAdminOnly`.
- `module`: the custom-access module a team login needs.
- `field`: a field-specialist or canvasser login can open it.

| Route | Old rail home (Design A) | New home (My Deal Room) | Label | Access | Primary action checked |
|---|---|---|---|---|---|
| `/wholesale/evosense` | pinned | **pinned: Acquisition Command** | Acquisition Command | module `acquisition` | work queue loads, row opens panel, Run hunt / Pause |
| `/wholesale/evosense/activity` | *(new)* | reached from the Acquisition Command header | Activity & system health | module `acquisition` | totals, routing review, funnel, budgets, feed |
| `/wholesale/evosense/inbox` | Acquisition | Acquire | Discovery Inbox | module `acquisition` | list, filters, search |
| `/wholesale/foreclosures` | Acquisition | Acquire | Foreclosures | module `foreclosures` | month view, start or preview month |
| `/wholesale/portfolios`, `/:id` | Acquisition | Acquire | **Seller Portfolios** (was Portfolio Acquisitions) | module `portfolios`; hidden on BookaBoost | import, review, package, export |
| `/wholesale/properties` | Acquisition | Acquire | Properties | module `properties` | list |
| `/wholesale/evosense/strategies`, `/:id` | Acquisition | Acquire | Strategies | module `properties` | list, builder |
| `/wholesale/evosense/inbox?ptype=multifamily` | Acquisition | Acquire | Multifamily | as Discovery Inbox | filtered inbox |
| `/wholesale/evosense/overlooked`, `/land-partners`, `/farm-ranch` | Acquisition | Acquire (BookaBoost/OLP flags, never on My Deal Room) | unchanged | `bookaboostOnly` / `olpOnly` | not shown on My Deal Room |
| `/wholesale/buyers` | Buyers & Deals | Buyers & Deals | Buyer Intelligence | module `buyer_intel` | find, qualify, match |
| `/wholesale/buyers/buy-boxes` | Buyers & Deals | Buyers & Deals | Buy Boxes | module `buy_boxes` | list |
| `/wholesale/deal-room`, `/:view` | Buyers & Deals | Buyers & Deals | Deal Room | module `deal_room` | owner dashboard, invite, add deal |
| `/wholesale/deal-room/investors` | Buyers & Deals | Buyers & Deals | Investor Outreach | module `deal_room` | investors view |
| `/wholesale/dispositions` | Buyers & Deals | Buyers & Deals | Dispositions | module `dispositions` | deals in disposition |
| `/wholesale` | Operations | Buyers & Deals | Deal Operations | module `acquisition` (unchanged) | transaction board |
| `/wholesale/deals/:dealId` | (owned by Deal Operations) | (owned by Deal Operations) | deal workspace | module `deals` | opens from the board, a property, or the queue panel |
| `/wholesale/appointments` | Operations | Closing & Tools | Appointments | module `appointments`; field | day, upcoming, past; book |
| `/wholesale/closing` | Operations | Closing & Tools | Contracts & Closing | module `contracts` | deals under contract |
| `/wholesale/funding` | Operations | Closing & Tools | Funding Partners | module `funding` | partners |
| `/wholesale/calculator` | Operations | Closing & Tools | Deal Calculator | module `underwriting` | calculation |
| `/wholesale/board` | Team & Field | Team & Field | Team Board | module `team_board` | board |
| `/wholesale/canvass` | Team & Field | Team & Field | Canvassing | module `canvassing`; field | routes |
| `/wholesale/callbacks` | Team & Field | Team & Field | Callback Center | module `sellers` | due callbacks |
| `/wholesale/team` | Team & Field | Team & Field | Team | admin + ws-admin; module `team_admin` | members, access |
| `/wholesale/evosense/controls` | Tools & Settings | Settings | Providers & Controls | module `settings` | providers, budgets, pauses |
| `/wholesale/exceptions` | Tools & Settings | Settings | Exceptions | module `team_board` | exceptions list |
| `/wholesale/pilot` | Tools & Settings | Settings | Pilot Controls | module `settings` | switches |
| `/wholesale/settings` | Tools & Settings | Settings | Wholesale Settings | admin (route `requireAdmin`); module `settings` | settings form |
| `/wholesale/evosense/property/:id` | (owned by Acquisition Command) | (owned by Acquisition Command) | property workspace | module `properties`; field | opens from the queue panel ("Work this property", "Open full property") |
| `/wholesale/evosense/lists/:kind` | (owned by Discovery Inbox) | (owned by Discovery Inbox) | cohort lists | module `acquisition` | unchanged |
| `/wholesale/hub/:group` | *(new)* | header link on each rail group | group landing page | each tile is one rail item that passed the same `visible()` test | lists only permitted tools |

Account and platform controls (all kept):

| Control | Old place | New place |
|---|---|---|
| Switch program / Program Access (`/workspaces`) | top bar (`ProgramSwitchLink`, only when the server lists more than one program) | top bar, unchanged |
| Back Office and other workspaces (`ContextSwitcher`) | top bar | account menu ("permitted platform navigation"); renders nothing for someone without them |
| Workspace / person / role | rail footer | account menu |
| Back to website | rail footer | account menu |
| Sign out | rail footer | account menu |
| My settings (`/settings`) | rail (field logins) | account menu; still on the rail for field logins |
| Location picker | top bar | top bar, unchanged |
| Environment (local review) | top bar | top bar, unchanged |
| Global search (to Discovery Inbox) | top bar | top bar, unchanged |

Role behaviour is unchanged because the new rail is computed from the same items and the same `visible()` filter (feature key, `adminOnly`, `workspaceAdminOnly`, brand flags, wholesale-only, field, and custom-access module). The group landing pages read the rail itself, so they cannot list a tool that the rail hides. Custom-access logins may open `/wholesale/hub/*` because those pages show only their own tools.

## 2. The Dec 1 auction next to a task citing Nov 3

**Where each date comes from**
- **Dec 1, 2026**: the property's current sale event, read through the work-priority policy (`services/evosense/work_priority.py` and `views._work`), from the Foreclosure Command Center month or the clerk notice. Texas trustee sales run on the first Tuesday, and Dec 1, 2026 is the first Tuesday of December.
- **Nov 3, 2026**: the text of an **open hand-off**. `opportunity.escalate()` opens a hand-off when a verified sale is critical (14 days or fewer). It writes the date into two places: the `AUCTION_DEADLINE` reason (`sale_date`) and the free-text note `next_action = "Foreclosure auction 2026-11-03 - Find contact"`.

**Why both appeared together:** the property was escalated for the Nov 3 sale. The sale then moved to the next first Tuesday (Dec 1). `escalate()` runs again only when the new date is itself critical. Dec 1 was 15 days out on Nov 16, which is UPCOMING and not CRITICAL, so nothing replaced the open hand-off. The old Acquisition Command printed the hand-off note (`needs_you[].next_action`) next to the live sale (`needs_you[].work`).

**What changed (no record is rewritten)**
- `services/evosense/work_queue.sale_context()` separates the dates:
  - Current sale messaging always comes from the property's sale event.
  - A task date that has **passed**, with a later current sale, is labelled history: "Sale moved. The hand-off was opened for the Nov 3, 2026 sale, which has passed. The current sale is Dec 1, 2026."
  - Two **future** dates that disagree, a task date with no current sale, or a current sale not verified on the official record are shown as **Needs verification**, with both sources named.
- In the queue, "Next action" comes from today's work state. The hand-off note is shown under History as "Original note: …", unchanged.
- Covered by `tests/test_evosense_work_queue.py` (six date cases, including "the note is never rewritten") and by the browser test, which seeds both cases and checks the row and the panel.

**Not changed (for a separate decision):** `escalate()` still does not re-escalate a moved sale that is not yet critical. It will re-escalate when Dec 1 comes within 14 days. Changing that rule changes who gets interrupted and when, so I left it to the owner.

## 3. What was redesigned

- **Brand and shell (My Deal Room only).** Open-door mark (`frontend/public/brands/my-deal-room-door.svg`), a text wordmark and "Acquisition engine · EvoSense". The tab icon and the My Deal Room investor portal's 30×30 mark now use the same door mark. The owner's original house-tile files are kept and still ship, and BookaBoost's marks are unchanged. The rail is charcoal. Groups start closed except the one holding the current page, and the person's own openings are remembered. Each group header has a link to its landing page. "Go to" (press **G**) jumps to any permitted tool. The account menu is in the top bar.
- **Workspace design.** The Wholesale design-system tokens are re-pointed for My Deal Room: a warm neutral canvas, deep green primary actions, compact type and spacing, and smaller radii. Every routed My Deal Room screen renders inside `.evo-app`, so buttons, filters, tables, status labels, drawers, alerts and empty states change together. Decorative hero banners collapse into a one-line page header. The Owner Deal Room welcome banner becomes a compact action bar.
- **Acquisition Command** is one work queue with a compact header, a docked detail panel, URL state and scroll restore. Reporting moved to Activity & system health.
- **Seller Portfolios.** An Import → Review → Offer package strip. "Seller stated" and "Our assumptions" are shown as separate bands and column groups, and every total says how many properties it covers. Missing numbers say "Not stated", "Missing", "Research needed" or "Needs all four" instead of a dash.
- **Buyers & Deals.** A workflow strip across the top of Buyer Intelligence, Buy Boxes, Deal Room, Investor Outreach, Dispositions and Deal Operations.
- **Linked records.** The queue panel opens the property workspace and, when the property was promoted, the deal itself (`promoted_deal_id`). No property record is created.

## 4. Evidence

Before and after screenshots of every destination are in `before-after/before/` (built from `main` 2851b07c) and `before-after/after/` (built from this branch). Both sets are desktop 1440×900 and phone 390×844, with the same seed. They were captured with `MDR_CAPTURE=… MDR_LABEL=before|after pytest tests/test_mdr_capture.py`. The walkthrough recordings are in `walkthrough/`. Addresses and numbers are synthetic.

## 5. Checks run

- `tests/test_evosense_work_queue.py`: 11 passed. Views, counts, one row per property, pagination bound (`limit` over 100 is refused), search, tenant isolation, 402 without the module, and the six sale-date cases.
- `tests/test_mdr_redesign_browser.py`: 5 passed (Chromium against the real build):
  - Rail groups and default state, labels fit, and every destination opens with the right entry highlighted.
  - The five landing pages, Activity & system health, the account menu, and Go to.
  - BookaBoost rail unchanged, and a restricted role (research assistant) sees no admin tools.
  - Queue: all four views, the panel in the first laptop viewport, Back returns to the same row, the empty filter state, and both date cases.
  - Phone: no horizontal scroll, the detail opens full width, and the menu works.
- `tests/test_portfolio_browser.py`: passed. Import preview, evaluation drawer, packages comparison, summary, phone and tablet.
- `tests/test_my_deal_room_logo.py`: passed, updated for the door mark.
- Regression across wholesale, EvoSense, portfolio, branding, Deal Room, command-center, foreclosure, workspace and OLP test files: **2,032 passed, 2 skipped, 2 failed**. Both failures were `test_my_deal_room_logo.py` running its pre-update copy (it was edited mid-run). The re-run passes.
- `node tests/frontend/wholesaleNav.test.mjs`: 19 of 20 pass. Its route/nav parity check now accounts for sub-views and parameterised routes. The remaining failure, "`/wholesale/team` should not be adminOnly", was already failing before this branch; it is a product decision and was left alone.
- Frontend build (`vite build`): clean.

## 6. Known gaps

- The two reference images ("Acquisition Command work queue.png", "Seller Portfolio Underwriting Workspace.png") were not attached and are not in Drive. The design follows the written brief.
- Discovery Inbox, Foreclosures, Properties, Multifamily, Buyer Intelligence, Buy Boxes, Dispositions, Contracts & Closing, Funding Partners, Deal Calculator, Team & Field and Settings use the new shared design (tokens, page header, controls, tables, labels) on their existing layouts. Their filters and rows were not restructured screen by screen. Acquisition Command, Activity, Seller Portfolios, the group landing pages, the Deal Room banner and the Buyers & Deals strip were restructured.
- Rows use the property photo in the detail panel only. Street View costs one paid lookup per photo, so the list does not fetch 25 photos per page.
- There is no per-PR preview for the main web app here. Vercel builds only `booking-app`, and Render has no PR previews for the main services. Acceptance as `platform@evosyspro.live` needs a deploy after approval.
- `escalate()` still waits until a moved sale is critical before re-escalating it (see section 2).


## 7. Round 2: screen-by-screen status

The two reference images ("Acquisition Command work queue.png", "Seller Portfolio Underwriting Workspace.png") were again not attached. They are not in Drive, and a search of the owner's user folder on the linked computer found nothing. This round follows the written brief.

**Rule used:** a screen counts as complete when its primary task is in the first viewport at 1440×900 and its layout supports that task. Each screen is either *rebuilt* (layout changed for the task) or *checked* (the layout was already task-first; it gets the shared header and number strip, and nothing else needed to change).

| Destination | Primary task | Status | What changed |
|---|---|---|---|
| Acquisition Command | work the next property | rebuilt | One-line tabs above the filter, denser rows (one label per row). The first row opens on a laptop, so its next action is on screen without a click. Next action sits above the photo. The panel overlays the list below 1181px; on a phone it is a full-width view. |
| Activity & system health | check engine health | rebuilt (round 1) | Reporting moved off the work screen. |
| Seller Portfolios (list) | open or start a portfolio | checked | Compact header; "+ New portfolio" and the list in the first viewport. |
| Seller Portfolio (detail) | import → review → package | rebuilt | **Empty:** import card first, with Import / Add existing as the main actions and what happens next beside it. **Populated:** the grid comes first. Opening a row docks the review panel beside the grid (a drawer below 1181px) and keeps the deciding columns. Totals ("Seller stated" and "Our assumptions", each with coverage) come after the grid. Selecting rows raises a sticky bar with Build offer package / Assign / Remove / Clear; "Package all" is on the filter bar. |
| Discovery Inbox, Multifamily | scan and open properties | rebuilt | The bar keeps search, opportunity type, work priority and sort. Strategy, signal, county, source, score, rolled-back, economics, confidence and Top-N move under **More filters**, which opens automatically when any of them is set. The list starts in the first viewport. |
| Deal Operations | move deals | rebuilt | The deal list (Pipeline / Needs attention / Closings / At risk) leads. The sandbox switch joins the header actions, and "This morning" moves into the side rail. |
| Contracts & Closing, Dispositions | work deals in that stage | rebuilt | They share the Deal Operations header: the sandbox switch is in the header and the numbers are one strip, so the stage tabs and list sit in the first viewport. |
| Buyer Intelligence | find, qualify, match buyers | rebuilt | The three command cards become header buttons (Find buyers, Invite investor, Open Deal Room with its state). The numbers are one strip, and the tabs and list are in the first viewport. |
| Deal Room, Investor Outreach | manage investors and listings | rebuilt (round 1) | The welcome banner becomes a compact action bar, and the Buyers & Deals strip links the six screens. |
| Buy Boxes | review buyer criteria | checked | |
| Providers & Controls | see sources and switches | rebuilt | The navy banner becomes a page header, and the four summary cards become one strip. |
| Properties, Callback Center, Team Board | work the list | checked | Numbers are one strip. |
| Foreclosures, Appointments, Canvassing, Funding Partners, Exceptions, Team, Strategies, Wholesale Settings, Pilot Controls | — | checked | Primary controls were already at the top. Deal Calculator's bottom line was already sticky. Pilot Controls keeps its two "Automatic distribution: OFF" notices at the top, because they are safety state. |

Every route, permission and action is unchanged. New checks in `tests/test_mdr_redesign_browser.py`:
- An empty portfolio puts Import in the first viewport on desktop and on a phone.
- In the populated grid, the grid sits above the totals and the selection bar offers Build offer package.

`tests/test_portfolio_browser.py` now checks the docked review panel.

## 8. Walkthrough

- `walkthrough/queue-flow.gif` (and `.mp4`) shows the queue flow:
  - Acquisition Command opens with the first row already showing its next action.
  - Upcoming Auctions shows "Sale moved" for the Nov 3 → Dec 1 property and "Needs verification" for Dec 1 vs Dec 8.
  - All → filter "fort worth" → select a row → Open full property → Back returns to the same filtered view and row.
- `walkthrough/portfolio-flow.gif` (and `.mp4`) shows the portfolio flow:
  - The empty portfolio shows the import card.
  - In the populated portfolio, opening a row docks the review panel. Closing it, ticking two rows and pressing Build offer package opens Packages with the new draft next to the others.

There is no working preview. Vercel builds only `booking-app`, and the main web app has no per-PR environment on Render. These recordings come from the real build against an in-process API with synthetic data.

## 9. CI and test baselines

- Foreclosure test: see [`FORECLOSURE_TEST_ROLLOVER.md`](FORECLOSURE_TEST_ROLLOVER.md). It reproduces identically on `main` and on this branch. It fails only on a UTC machine between 6 PM and midnight Central.
- `node tests/frontend/wholesaleNav.test.mjs` on `main` (2851b07c): **18 passed, 2 failed**. The failures are the nav/route parity check (Investor Outreach's `/deal-room/:view`) and "`/wholesale/team` should not be adminOnly". On this branch: **19 passed, 1 failed**. The parity check now handles sub-views and view params; the Team check is the same pre-existing failure on both.
