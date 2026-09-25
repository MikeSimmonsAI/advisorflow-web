# Wholesale Phase 7.2 — Premium Product Experience + Canonical Local Review

Status: **built locally, not committed, not pushed, not deployed.** Returned for Mike's visual review.

Repository: `C:\Dev\advisorflow-web` (branch `main`). No commit, push, merge, Render deploy, real
vendor connection or production configuration change was made.

---

## 1. Why the local review was empty — the root cause

Diagnosed on the Windows machine itself, not guessed. Four separate faults stacked up:

| # | Fault | Evidence | Effect |
|---|---|---|---|
| 1 | **The backend was not running.** | Port 8000 had no listener. The last API log (`%TEMP%\p71-api.log`) ends mid-request at 4:58 PM with no shutdown line — the process was killed, not stopped. Nothing restarts it. `frontend/.env` correctly points the web app at `http://localhost:8000`. | The web app loaded, but every call behind the login failed. |
| 2 | **There was no one-step way to start it correctly.** | `start-backend.bat` runs the bare `python` command (not the project's `.venv`) with `--reload`, and relies on whatever `DATABASE_URL`/`SERVICE_ROLE` the shell happens to have. | Restarting by hand was fragile. |
| 3 | **The review data was split across three organizations.** | `Wholesale Review (TEST)` held the deals and buyers (Phase 6). `EvoSense Review (TEST)` and `EvoSense Autonomy Review (TEST)` held the EvoSense data (Phase 7 / 7.1). Mike's own account (`god_admin`) lives in his real organization. | No single login saw the whole product. Logging in as Mike showed an ordinary workspace with nowhere useful to go. |
| 4 | **Deal Operations hid sandbox records by default.** | `GET /wholesale/operating-board` excludes `is_test` rows unless asked — correct for a real workspace, but the review data is entirely sandbox. | Even in the right org, the old Command Center opened on zeros. |

## 2. How it was fixed

* **ONE canonical review organization — `EvoSense Review (TEST)`** (`acc04387-…`, slug `evosense-review-test`).
  It already carried the complete, engine-generated EvoSense scenario (flagship 97 / 94 / 81). The new seed
  adds Wholesale Operations to it through the real Wholesale API, and links it to the EvoSys Pro platform.
* **ONE login** — `evosense.review@example.test` / `EvoSense-Review-2026!` (org admin of that org only).
* **ONE seed** — `scripts/seed_evosys_review.py`: safe (SQLite only, this org only), idempotent (every step
  looks for what it would create first; a second run changes nothing), non-destructive (deletes nothing; never
  re-hunts an org that already has EvoSense data), makes no AI call (manual AI is switched off inside the seed
  process and the OpenAI key is dropped from its environment).
* **ONE launcher** — `START_EVOSYS_REVIEW.bat` (and `STOP_EVOSYS_REVIEW.bat`), see §4.
* **Local review shows sandbox by default.** Deal Operations includes sandbox records by default only when the
  backend reports the LOCAL REVIEW environment (§5); production behaviour is unchanged. The toggle is visible.
* The other two review orgs and their logins were **left untouched** (non-destructive). They are no longer
  needed; the one canonical account covers everything they did.

### What the canonical org contains (deterministic sandbox data)

| State | Example |
|---|---|
| Needs You (flagship) | **1418 Cedar Springs Rd** — Opportunity **97**, Contact **94**, Seller Intent **81**; seller: *“Yes, I inherited it last year. It's vacant and needs work. I'd probably sell around 150 if we can close quickly.”* Asking $150,000, preliminary MAO $153,100 (estimate), spread +$3,100 |
| High opportunity / contact found | 5316 Wellesley Ave, 2124 S Ervay St, 909 W Division St, 1405 E Magnolia Ave, 2917 Hemphill St |
| Waiting for data | 2611 Glenfield Ave, 1805 Nolte Dr |
| Budget blocked | 2710 Stadium Dr, 3102 Avenue J |
| Nurture | 7302 Ferguson Rd (“Maybe after the holidays” → Jan 2027) |
| Wrong person / closed out | 4915 Live Oak St |
| Suppressed | 3330 Hatcher St |
| Needs review (identity) | 5530 Bonnie View Rd |
| Provider failure | sandbox skip-trace timeout (refunded), shown on Providers & Controls |
| Promoted → deal | 6120 Wedgwood Dr (EvoSense → Deal Operations hand-off) |
| Cash buyers | 5 buyers with buy boxes (verified cash, POF, one out-of-state) |
| Deals across the workflow | 3308 Hamilton Ave (analysis) · 1522 E Ohio Ave (offer awaiting approval) · 6613 Lovett Ave (under contract → disposition, buyers matched, closing Oct 9) · 4119 Bonnie View Rd (buyer assigned, title / closing Oct 2) · 2847 Kilburn Ave (closed, $12,000 fee collected) |
| Seller Portal / Investor Deal Room | published for 6613 Lovett Ave, with share links |

## 3. Review data survives restart

Everything lives in the local SQLite file `C:\Dev\advisorflow-web\advisorflow.db` — the file `DATABASE_URL`
already names in `.env`, and the one the launcher pins explicitly. Nothing is created in a temporary harness,
nothing is reseeded destructively, and the seed that runs on every start only adds what is missing.
**Verified by two full stop → restart → login cycles on the Windows machine (§13).**

## 4. One-step local review

```
Start:  double-click  C:\Dev\advisorflow-web\START_EVOSYS_REVIEW.bat
Stop:   double-click  C:\Dev\advisorflow-web\STOP_EVOSYS_REVIEW.bat
```

`START_EVOSYS_REVIEW.bat`:
1. uses the project's own `.venv\Scripts\python.exe`;
2. pins review-safe settings for everything it starts: `DATABASE_URL=sqlite:///./advisorflow.db`,
   `SERVICE_ROLE=local_review` (so **no background loop starts** — the fail-closed default),
   `AI_BACKGROUND_AUTOMATION_ENABLED=false`, `VITE_API_BASE_URL=http://localhost:8000`;
3. runs the review seed (safe to repeat; log in `%TEMP%\evosys-review-seed.log`);
4. starts the API (port 8000) and the web app (port 5173) **only if they are not already running**, each in its
   own minimized window (logs `%TEMP%\evosys-review-api.log`, `%TEMP%\evosys-review-web.log`);
5. waits until both answer, prints the login, and opens `http://localhost:5173/login`.

`STOP_EVOSYS_REVIEW.bat` stops only what is listening on ports 8000 and 5173. It touches no data.
Neither script touches production, git, Render, or any real provider.

## 5. Environment truth

`GET /demo/environment` now also returns `local_review: true` when the process runs on a local SQLite
database — which production never does (Render runs PostgreSQL). Every Wholesale/EvoSense screen shows **one**
quiet pill in its product bar: **● LOCAL REVIEW · SANDBOX DATA**. Record-level SANDBOX tags appear only where they
carry information (a list that mixes sandbox and live records, a simulated message, a fictional phone number),
not on every row of a wholly-sandbox workspace.

## 6. Branding root cause and fix

**Cause.** The application shell chose its brand from the browser's **hostname only**
(`theme.js detectTheme()`): `app.evosyspro.live` → EvoSys Pro, and every other host — `localhost` included —
fell through to BookaBoost. The organization already says which white-label platform it belongs to
(`organizations.platform_id`), but nothing on the client ever consulted it; and the review orgs had no
`platform_id` at all.

**Fix (respects the multi-brand architecture; hardcodes nothing):**
* `GET /branding/org` now also returns `platform` — the workspace's platform presentation (slug, display name,
  accent, theme), resolved through the existing `brand_config.config_for_slug`.
* `theme.js` gains `hostTheme()` (the brand the hostname names, or `null` — it never guesses) and
  `shellTheme(branding)`: **a brand domain still decides its own chrome, always**; only when the host names no
  brand (localhost, preview hosts) does the workspace's platform decide. BookaBoost domains are unaffected.
* `applyWorkspaceTheme` applies that theme on first paint (from the cached `/branding/org`) and after login;
  `Layout` and `ThemeToggle` render the shell brand from it.
* The seed links the review org (and its login) to the EvoSys Pro platform row — configuration, not a hack.

## 7. Design architecture

`frontend/src/pages/wholesale/ds/` is the shared product design system:

* **`evo-ds.css`** — semantic tokens on `.evo-app` / `.evo-tokens`: `--evo-primary`, `--evo-success`,
  `--evo-warning`, `--evo-danger`, `--evo-info`, `--evo-violet`, `--evo-surface`, `--evo-surface-elevated`,
  `--evo-surface-raised`, `--evo-surface-inset`, `--evo-text-primary/secondary/tertiary`, line, radius, shadow,
  focus ring, and the three score accents. Plus every primitive: product bar, page head, buttons, panels,
  metric strip, score cards, compact score numbers, status pills, chips, tags, property imagery, table →
  mobile cards, forms, search, segmented controls, tabs, drawer, empty/loading/error states, feed, key–value
  lists, meters, quotes. A **bridge** re-points the older module stylesheets' variables at these tokens and
  calms the platform's glassy `.panel`/`.btn`, so every existing Wholesale/EvoSense screen (the deal workspace
  and its nine tabs included) speaks the same language without being re-typed.
* **`evo-pages.css`** — page compositions only (Needs You hero, rails, property story, strategy cards,
  builder, control room, pipeline, closing calendar, settings rail).
* **`ds.jsx`** — `EvoApp` (workspace + module bar + environment pill), `PageHead`, `Panel`, `Metrics/Metric`,
  **`Score`/`Scores`/`Num`/`ScoreWhy`** (one score component, three accents, deterministic drilldown),
  **`Status`** (ONE status vocabulary for EvoSense buckets *and* deal stages), `Chips`, `Tag`, `SandboxTag`,
  **`PropertyThumb`**, `Drawer` (portalled, focus-trapped, Esc to close), `Seg`, `Tabs`, `Search`, `Field`,
  `Feed`, `Empty`, `Alert`, `Skeleton`, and formatting helpers.

## 8. White-label behaviour

`--evo-primary` follows the organization's own brand colour (`--brand-primary` from `/branding/org`), then the
platform accent, then EvoSys blue. Filled primary controls use `--evo-primary-fill` — the brand colour taken down
until white text clears WCAG AA — so **a customer's accent cannot break contrast**. Status colours are semantic and
are never re-branded. The EvoSense module keeps its own identity (cyan) because the module is the product; the
organization's logo and name come from the existing branding columns. The Seller Portal and Investor Deal Room use
the same rule (`--wr-accent-strong`).

## 9. Navigation

The sidebar now says there are two operating worlds, and no longer has two "Command Centers":

```
ACQUISITION · EVOSENSE            WHOLESALE OPERATIONS
  Acquisition Command               Deal Operations        (was "Command Center")
  Discovery Inbox                   Properties
  Strategies                        Cash Buyers
  Providers & Controls              Contracts & Closing    (new focused view)
                                    Dispositions           (new focused view)
                                    Wholesale Settings
```

Every screen repeats its world's tabs in the product bar. **Contracts & Closing** and **Dispositions** are
focused views of the same real deals (the stages they are actually in), not invented pages; each has a route and
a feature key the server registers (`tests/frontend/wholesaleNav.test.mjs` checks nav ⇄ routes ⇄ entitlements).

## 10. Screen by screen

| Screen | What changed |
|---|---|
| **Acquisition Command** | EVOSENSE / Acquisition Engine head with live automation state (active strategies, automatic hunting, last hunt, next hunt — only real timestamps), Run hunt now (the same scheduled-hunt service) and Pause. Metric strip: properties evaluated, contacts found, needs you, spent today, spent this month, in nurture. **Needs You hero**: property imagery, address, signals, the three scores, the seller's own words, seller asking / estimated MAO (labelled ESTIMATE) / spread, why it is here, the next recommended action, **Open opportunity** and **Call seller** (a real `tel:` link — for a sandbox record the fictional number is shown, not dialled). Recent Opportunities table. Intelligence rail: System Status, Budget Usage, Recent Activity. Pipeline at a glance. Routing reviews. |
| **Discovery Inbox** | Summary strip, state buttons with counts (ordered by what needs you), search/strategy/signal/sort filters kept in the URL, address-dominant rows with scores, compact signals, status + next action, server-side paging. |
| **Property Intelligence** | Hero (imagery, value, equity, occupancy, size, ownership), unmistakable Next Action card with its buttons, section nav, the three scores each opening its deterministic WHY, the conversation (SELLER SAID vs SYSTEM EXTRACTED per message), seller facts with provenance, deal intelligence (every estimate labelled), why EvoSense found it, owner & contact, data intelligence (sources, freshness, conflicts, providers, cost ledger), timeline with milestones. Promotion is confirmed in a drawer. |
| **Strategies** | Live strategy cards: hunting pulse, market, target, priority signals, budget, hand-off threshold, cadence, last/next hunt, results (found, needs you, promoted, spent). One primary action (Open strategy); pause/clone/archive quieter; archive confirmed in a drawer. |
| **Strategy Builder** | "Tell EvoSense what to hunt": eight plain questions (where, what property, which signals, what to avoid, how much to spend, when to bring you in, how often to hunt, how owners are worked) and a sticky **YOUR STRATEGY** plain-English readback written by the server from the actual settings. |
| **Providers & Controls** | Control room: summary strip; EvoSense controls as switches with RUNNING / PAUSED / NOT CONFIGURED (Email and Voice — EvoSense has no such outreach yet); budget with actual spend; data capabilities (REAL CONNECTOR / SANDBOX / MANUAL / IMPORT / INTERFACE ONLY — sandbox never made to look live); providers table where failures are prominent and healthy providers quiet. |
| **Deal Operations** | Active deals, under contract, awaiting approval, closing in 30 days, needs attention, at risk; expected pipeline value and fees collected; needs-your-attention list; pipeline by group and stage; active deals table; upcoming closings calendar, at risk, activity, funnel totals. |
| **Contracts & Closing** | Every deal from contract to title: closing date badge, where it stands (progress), contract price, buyer price, assignment fee, outstanding item. |
| **Dispositions** | Property → matched buyers (buy-box fit) → contacted → offers → selected buyer, per deal; seller details never shown. |
| **Properties** | Workspace first (summary, search, filters, the list); **Add property** and **Import list** are drawers. Backend unchanged. |
| **Cash Buyers** | Summary (total, active, verified cash, POF, buy boxes), search, the buyer list; add / import / edit / buy boxes are drawers. |
| **Deal workspace** | Same screens and tabs, in the product shell and language (header, stage status, tab underline, surfaces). |
| **Wholesale Settings** | A settings rail — Deal rules, Markets, Approvals, Automation, Providers, Buyer outreach, Budget, Pipeline, Contracts, Seller assistant — one section at a time; unsaved changes are kept across sections. |
| **Seller Portal / Investor Deal Room** | Kept customer-facing, light and simple. Same product family: Space Grotesk / Inter type, tabular figures, softer surfaces, contrast-safe accent. **Fixed a real bug:** three of the Deal Room's action cards had invisible titles (their text inherited the app's dark-theme colour). |

## 11. Property imagery

No property in the review has a photo, and none is invented. `PropertyThumb` renders a real uploaded photo
(lazy-loaded) when one exists; otherwise a designed placeholder — a parcel outline on a street grid, tinted per
address — that is plainly not a photograph, is labelled "No photo on file" / "Sandbox · no photo", and has an
accessible name saying there is no photo.

## 12. Accessibility and responsive results

**Real Windows browser review** (Chromium, the real local stack, logged in through the real login form with the
canonical account; script `C:\Users\simmo\Downloads\p72\p72look.py`): **15 pages × 4 widths (1550 / 1280 / 820 /
390) = 60 checks, run three times** (before the restarts, and after each of the two restarts):

| Check | Result |
|---|---|
| Horizontal overflow | **0** of 60 |
| Page (JavaScript) errors | **0** |
| Required content present (branding, flagship 97/94/81, seller conversation, scores, provider truth, cost ledger, cash buyers, transaction workflow, seller portal, deal room) | **60 / 60** |
| Shell brand seen | **EvoSys Pro** on every page |
| Touch targets < 44px at 820 / 390 (touch emulated) | first run: controls on 12 pages were 34–42px → fixed (width-based 44px rule; toggle switch rebuilt with a 52×44 hit area) → **0** on both later runs |

**Accessibility (axe-core 4, WCAG 2 A + AA rules, every product page + both public pages):** the first pass found
colour-contrast failures only — white text on EvoSys blue #087cff is 3.9:1, and tertiary text on raised surfaces was
4.1:1. Fixed with `--evo-primary-fill` (5.2:1), tertiary text #8193ae (≥ 4.5:1 on every surface) and
`--wr-accent-strong` on the public pages. **Final: 0 violations on all 13 pages audited.** Also in place: visible
focus rings, labelled controls and search, table captions, scores announced with their meaning (never colour alone),
status pills always carry a text label, arrow-key tabs, a focus-trapped drawer that closes on Esc and returns focus,
`prefers-reduced-motion` honoured, landmarks for navigation and the intelligence rail.

**Responsive:** desktop uses a main column plus an intelligence rail (≥ 1180px); the rail reflows to a two-column
grid, then one column; at ≤ 860px every data table becomes stacked cards led by the address; the phone order on
Acquisition Command is Needs You → next action → scores → seller message → economics → status. Desktop UI is
reflowed, not shrunk.

**Performance:** no new libraries, no animation library, no polling. Placeholder imagery is inline SVG; real photos
are lazy-loaded. Server-side paging is kept (Inbox: 50 per page). The Dispositions view reads at most ten deal rooms,
in parallel. The production bundle builds clean.

## 13. Tests, regression and canonical review acceptance

**Canonical review acceptance (Windows).** `STOP_EVOSYS_REVIEW.bat` → 0 listeners → `START_EVOSYS_REVIEW.bat`
→ seed "review data ready" (idempotent: nothing duplicated) → API and web answering → real-browser check of every
page: **run twice, both 60/60 clean.** Verified each time: EvoSys Pro branding · the EvoSense Review (TEST)
organization · Wholesale and EvoSense visible · Acquisition Command, Discovery Inbox, flagship (97 / 94 / 81) and
its seller conversation populated · strategy cards · provider truth states · cost ledger · cash buyers ·
transaction workflow (Deal Operations, Contracts & Closing, Dispositions, the deal workspace) · Seller Portal and
Investor Deal Room reachable. The review data was still there after each restart.

| Suite | Result |
|---|---|
| Windows, full repo — `-k "wholesale or evosense or budget or sms_router or backend_availability or service_role or branding or environment or brand or vertical_workspace or workspace_entry or deploy_hygiene or public_sites or navigation or build_version"` | **959 passed**, 0 failed (includes Phase 7.1 autonomy, EvoSense, Wholesale, cross-tenant, SMS, budget concurrency) |
| Cloud full backend regression (6 workers) | **5,658 passed · 16 skipped · 1 failed · 5 errors** |
| — `test_zoom_integration::test_requires_video_not_overwritten_when_user_edited_row` | **pre-existing** (fails at the Phase 7 / 7.1 baseline too) |
| — `test_startup_memory` × 5 errors | **environment-only**: the import subprocess timed out after 300 s while the box was at load 13; re-run alone: **10 passed** |
| Frontend `node --test frontend/tests/*.test.mjs` | 12 pass · 2 fail — the two God Mode token tests, **pre-existing and unchanged** |
| Frontend `node --test tests/frontend/*.test.mjs` | **6 / 6 pass** (incl. wholesaleNav: nav ⇄ routes ⇄ server entitlements) |
| Production build (`vite build`) | **clean** |
| axe-core WCAG A/AA | **0 violations** on 13 pages |

No new failures. Nothing was called green that was not.

## 14. Business logic

Nothing in scoring, seller intent, budgets, the scheduler, provider routing, inbound SMS, DNC/suppression, deal
analysis, contracts, buyer matching or tenant isolation was changed. Backend changes are read-only and additive:
`/branding/org` gains `platform`; `/demo/environment` gains `local_review`; the EvoSense command-center payload
gains `totals`, `recent_activity`, signals on `found`, and hero details on `needs_you` (scores, signals, the
seller's latest words, best contact, preliminary economics — the same values the property page already shows).

## 15. Known gaps

1. **The approved mockup image was not available to this session** (not on the machine, not in the artifact
   gallery). The design was built from the written specification — every element it lists — and the EvoSys design
   tokens. Compare against the mockup during review; adjustments are CSS-token level.
2. **No real property photos exist** in the review data, so every property shows the designed placeholder.
3. **The local server runs no background loops** (`SERVICE_ROLE=local_review`), so "Next hunt: tomorrow 2:52 PM"
   is the real schedule but will not fire locally; Run hunt now works. Production (`SERVICE_ROLE=backend`) is unchanged.
4. **Call seller** is a real `tel:` link; there is no in-app dialer. For sandbox records the fictional number is shown,
   not dialled.
5. The deal workspace's nine tabs (property, comps, offers, documents, buyer board, closing, sharing, audit) keep
   their Phase 4–6 layouts; they now wear the product language through the design-system bridge rather than being
   re-composed screen by screen.
6. **The Investor Deal Room header shows `469-553-7417` and `support@evosyspro.live`** — the support contact on the
   EvoSys Pro platform row in the local database. That is existing configuration; it was not changed (see §17).
7. The two superseded review organizations (`Wholesale Review (TEST)`, `EvoSense Autonomy Review (TEST)`) and their
   logins still exist — left untouched on purpose (non-destructive).
8. On Acquisition Command at desktop width, a row's signal chips stack; the column is intentionally narrow so the
   address and scores keep the space.
9. Pre-existing, unrelated: `test_zoom_integration` (1 backend test) and two God Mode frontend token tests.

## 17. Needs Mike's decision

* **Visual sign-off against the mockup.** Anything that differs is a token/CSS change, not a rebuild.
> **SUPERSEDED (Phase 7.3):** the Investor Deal Room and Seller Portal no longer show the platform phone at all - see WHOLESALE_PHASE7_3_VISUAL_REBUILD_REPORT.md section 5.

* **The support phone on the EvoSys Pro platform row** (shown on the Investor Deal Room) is `469-553-7417`. Keep it,
  or set the platform's support contact to the EvoSys number before this reaches production.
* **Whether to remove the two superseded review orgs** from the local database (not done: non-destructive).
* **The deployment gate**: this is local only. Nothing has been committed, pushed or deployed.

## 16. Files

**New**
* `START_EVOSYS_REVIEW.bat`, `STOP_EVOSYS_REVIEW.bat`, `scripts/review/run_api.bat`, `scripts/review/run_web.bat`
* `scripts/seed_evosys_review.py`
* `frontend/src/pages/wholesale/ds/ds.jsx`, `ds/evo-ds.css`, `ds/evo-pages.css`
* `handoff/WHOLESALE_PHASE7_2_PRODUCT_EXPERIENCE.md`, `handoff/p7-2-shots/` (13 required screenshots + extras,
  `all-widths/` with every page at 1550/1280/820/390, `review-report.json`)

**Changed — backend (read-only, additive)**
* `app/routers/branding_router.py` — `/branding/org` returns the workspace `platform`
* `app/services/environment.py` — `/demo/environment` returns `local_review`
* `app/services/evosense/views.py` — command-center `totals`, `recent_activity`, `found` signals, Needs You details

**Changed — frontend**
* `frontend/src/theme.js`, `frontend/src/api/client.js`, `frontend/src/components/Layout.jsx` (branding + navigation)
* `frontend/src/App.jsx` (routes: `/wholesale/closing`, `/wholesale/dispositions`)
* `frontend/src/pages/wholesale/evosense/EvoCommand.jsx`, `EvoInbox.jsx`, `EvoProperty.jsx`, `EvoStrategies.jsx`, `EvoControls.jsx`
* `frontend/src/pages/wholesale/WholesaleCommand.jsx`, `WholesaleProperties.jsx`, `WholesaleBuyers.jsx`, `WholesaleDeal.jsx`, `WholesaleSettings.jsx`
* `frontend/src/pages/public/rooms.css`
* `handoff/WHOLESALE_REAL_ESTATE_HANDOFF.md` (sections 66–68)

**Safety copies (outside the repo):** `C:\Users\simmo\Downloads\p72\p72_originals_backup.tar` (the 18 files as
they were before this phase) and `C:\Users\simmo\Downloads\p72\advisorflow.db.pre-p72.bak` (the database before
the new seed ran).
