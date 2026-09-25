# Phase 7.3: Wholesale Premium Visual Rebuild

**Status:** ready for human visual review.
**Nothing has been committed, pushed, merged or deployed.**
The Windows repo is still at `main` / `184dc41`, and the new work is uncommitted in the working tree.

> KEEP THE ENGINE. REBUILD THE EXPERIENCE.

The dark Phase 7.2 look was rejected. Phase 7.3 rebuilds the whole Wholesale / EvoSense experience as a **light premium operating system**. It matches the ONE approved mockup board and changes no business logic.

## 1. Review access

| | |
|---|---|
| Startup | `C:\Dev\advisorflow-web\START_EVOSYS_REVIEW.bat` (stop: `STOP_EVOSYS_REVIEW.bat`) |
| Main URL | http://localhost:5173/wholesale/evosense |
| Login | `evosense.review@example.test` / `EvoSense-Review-2026!` |
| Org | `EvoSense Review (TEST)`: one org, one DB (`advisorflow.db`), with sandbox data populated |

### Direct URLs (Windows review DB)

| Screen | URL |
|---|---|
| Acquisition Command | http://localhost:5173/wholesale/evosense |
| Discovery Inbox | http://localhost:5173/wholesale/evosense/inbox |
| Property Intelligence (flagship) | http://localhost:5173/wholesale/evosense/property/a4d0364e-0c59-4d1c-90db-7122d3cdb92d |
| Strategies | http://localhost:5173/wholesale/evosense/strategies |
| Strategy Builder (new) | http://localhost:5173/wholesale/evosense/strategies/new |
| Strategy (edit) | http://localhost:5173/wholesale/evosense/strategies/fdfb5806-6d1a-436b-922b-d78a9c2594a4 |
| Providers & Controls | http://localhost:5173/wholesale/evosense/controls (tabs: Service Providers · Controls & Compliance · Usage & Costs; `#budget` opens Usage) |
| Deal Operations | http://localhost:5173/wholesale |
| Contracts & Closing | http://localhost:5173/wholesale/closing |
| Dispositions | http://localhost:5173/wholesale/dispositions |
| Properties | http://localhost:5173/wholesale/properties (Add: `?add=1`; Import list button) |
| Deal workspace (6613 Lovett) | http://localhost:5173/wholesale/deals/af419fe9-bd4a-4ef5-bc73-47e841edb877 (all 9 tabs) |
| Cash Buyers | http://localhost:5173/wholesale/buyers (+ Add buyer, Import buyers) |
| Wholesale Settings | http://localhost:5173/wholesale/settings |
| Seller Portal | http://localhost:5173/my-property/cFh_CJusA4hRry_mnjPwKylF9L_A5spyHnkBtVJlMM0 |
| Investor Deal Room | http://localhost:5173/investor/YbVorX6SktHbsZCpLa96PzRPsvHvi2Mns1hJDRZvD4c |

## 2. What changed visually

- **Light canvas everywhere.** The page uses a cool-white canvas, white cards and soft shadows, with serif hero titles, big coloured metric numbers and pastel status pills. The navy dark canvas is gone from every screen, drawer and deal tab. `p73look.py` fails if a large dark surface reappears.
- **Contextual hero on every primary page**, using licensed photography:

| Page | Photo | Quote |
|---|---|---|
| Acquisition Command | skyline | *Opportunity doesn't wait to be listed.* |
| Discovery Inbox | street of homes | — |
| Strategies | bridge | — |
| Providers | global network | — |
| Deal Operations | home at dusk | — |
| Properties | modern home | — |
| Cash Buyers | handshake | — |
| Contracts & Closing | signing | — |
| Dispositions | home | — |
| Settings | circuit | — |
| Seller Portal | family home | — |
| Investor Deal Room | skyline | — |
| Property pages | city aerial | — |

  Each hero has an overlay, eyebrow, title, subtitle, quote, live meta chips, actions and a score ring where one applies.
- **Board compositions:**
  - Acquisition Command: five or six metric cards, a Needs You panel, then Top Opportunities as image cards with score rings.
  - Discovery Inbox: count tabs with score rings in the table.
  - Strategies: All / Active / Paused & drafts / Archived tabs, plus + New Strategy and photo-headed cards.
  - Providers: three tabs and capability tiles, each carrying its truth label.
  - Deal Operations: Pipeline / Needs attention / Closings / At risk tabs, plus + New Deal.
  - Contracts & Closing: stage tabs.
  - Cash Buyers: a Verified-only filter.
  - Settings: horizontal tabs.
  - Deal workspace: a property hero with a Stage control and seller-qualification ring, a **Key numbers** card, and all nine tabs light.
- **Property photos:** a real uploaded photo always wins. When a property has none, a representative house photo is shown, **labelled "Representative photo"**. On small table thumbnails the label is an "R" marker and a tooltip, plus the accessible name "Representative photo — not ⟨address⟩". A stock house is never presented as the property.
- The drawn SVG scenes are kept only as a fallback.

## 3. Navigation: before and after

- **Before (7.2):** the dark platform rail showed ~30 items. The two wholesale groups sat in the middle of Workspace / Engagement / Operations / Administration / Help.
- **After (7.3):** inside `/wholesale` the rail is light and focused. It shows only:
  - **Acquisition · EvoSense:** Acquisition Command, Discovery Inbox, Strategies, Providers & Controls.
  - **Wholesale Operations:** Deal Operations, Properties, Cash Buyers, Contracts & Closing, Dispositions, Wholesale Settings.

  Everything else is folded under one collapsible **EvoSys Platform** section. Nothing was deleted: the section still holds Overview, Leads, My Work, Replies, Activity, Availability, AI Hub, Your AI Team, My AI Workforce, Workforce Command, Proposals, Cadence, Re-engagement, DNC List, CRM/Reports/Imports, Users/Settings/Org/Audit/A2P/Health/Billing and Help, each under its original visibility rules.
- The top bar has one search (it routes to the Discovery Inbox with `?q=`), the LOCAL REVIEW pill and the signed-in person.
- Screens outside `/wholesale` and vertical workspaces are unchanged.
- AI Hub and Workforce Command are platform-wide screens and are not exposed in the Wholesale nav. They live under EvoSys Platform, so no Wholesale AI page was invented and no fake agents or numbers were added.

## 4. Source-of-truth confirmations

- **One board only.** Every composition, the palette and the hero/metric/tab patterns came from the single approved board. No other board and no Restland or cemetery design was used.
- **No mockup data is hard-coded.** Every number, address, score, count and status comes from the API and database.
  - "Top opportunity 100" is the maximum real opportunity score.
  - Tab counts are real counts.
  - The quotes are marketing copy, not data.
- **Populated data remains.** It is the same canonical review org as 7.2: 29 EvoSense properties, 6 deals, 5 buyers, seller and investor links, and the flagship 1418 Cedar Springs Rd scored 97/94/81.

## 5. Investor Deal Room phone (469-553-7417)

**Source:** `wholesale_publication.branding()` fell back to the **platform** brand's `support_phone`. That is the EvoSys Pro frozen default in `brand_config.py`, `appointment_invites.py` and the platforms row.

**Fix:** no wholesale-specific public contact setting exists anywhere, so the fallback was removed rather than replaced with an invented number. `support_phone` is now always `None` on both public wholesale pages. The seller page still shows the named contact the operator put on the deal.

**Test:** `tests/test_wholesale_p73_public_contact.py`. `p73look.py` also fails if 469-553-7417 appears on any screen.

**Known gap:** the platform **support email** (`support@evosyspro.live`) still shows in the public masthead. It needs a product decision on a wholesale public contact (name, phone and email) in Wholesale Settings.

## 6. Responsive and accessibility

- **Visual acceptance walk** (`scripts/review/p73look.py`): 30 screens × 1550 / 1280 / 820 / 390 = **120 screen-widths**. It covers all 9 deal tabs, Add Property, Import list, Add Buyer, Import buyers, the Strategy builder and edit views, and the Controls and Usage tabs.
  - **Windows:** run 1 gave 120 / 0 problems, and run 2 (after the photo change) gave 120 / 0 problems.
  - **Cloud:** 120 / 0.
  - Each run checks five things: horizontal overflow, touch targets under 44 px at ≤1024, required text, leftover dark surfaces, and the platform phone.
- **Heroes stack below 1100 px:** actions and the score ring move into the flow, so nothing overlaps the title.
- **axe-core, WCAG 2 A/AA, whole document including the shell:** 0 violations on all 15 primary pages (13 app pages plus Seller Portal and Investor Room).
  - Fixed along the way: nav hint contrast, score-ring contrast, `a.evo-btn--primary` inheriting dark text, empty-stage opacity, the intent pill ink, and `aria-controls` on panel-less tabs.

## 7. Startup file

`START_EVOSYS_REVIEW.bat` was kept unchanged. It was tested **twice** (STOP → 0 listeners → START → ready → API `local_review:true`), each time on the new code.

## 8. Tests

| Suite | Result |
|---|---|
| Windows targeted slice (34 files: wholesale, evosense, autonomy, SMS/inbound, budget, cross-tenant, seller/investor rooms, hygiene, public sites, vertical workspace, branding, environment, demo) | **696 passed, 0 failed** |
| New: `test_wholesale_p73_public_contact.py` | 1 passed |
| `test_wholesale_rooms.py` (seller portal + investor room) | 31 passed |
| Frontend `tests/frontend/*.test.mjs` | 6/6 files pass (`wholesaleNav` 11/11) |
| Frontend `frontend/tests/*.test.mjs` | `workspaceGuard` pass. `godTheme` shows 2 failures, both **PRE-EXISTING** (God Mode tokens; identical before 7.3) |
| Frontend production build (`vite build`) | passes |
| **Cloud full regression** | **5,663 passed, 16 skipped, 2 failed** |

The two regression failures:

- `test_zoom_integration::test_requires_video_not_overwritten_when_user_edited_row` is **PRE-EXISTING**. It is the same failure recorded in 7.1 and 7.2 and is unrelated to Wholesale.
- `test_wholesale_cross_tenant::test_every_id_bearing_wholesale_route_is_in_the_attack_list` is **ENVIRONMENT**. The xdist worker crashed under load (load average ~15). Re-run alone, the file gave **4 passed**.

**NEW failures: 0.**

## 9. Files

**New:**

- `frontend/src/pages/wholesale/ds/scenes.jsx`
- `frontend/src/pages/wholesale/ds/photos/` (14 hero photos, 6 card photos, 11 representative house photos, `CREDITS.md`, ~2.7 MB)
- `frontend/src/components/WholesaleShell.jsx`
- `frontend/src/components/wholesale-shell.css`
- `tests/test_wholesale_p73_public_contact.py`
- `scripts/review/p73look.py`
- this report
- `handoff/p7-3-shots/`

**Modified:**

- `ds/ds.jsx`, `ds/evo-ds.css` (rewritten light) and `ds/evo-pages.css`
- `components/Layout.jsx` (focused shell, additive)
- EvoCommand, EvoInbox, EvoStrategies, EvoControls, EvoProperty
- WholesaleCommand, WholesaleProperties, WholesaleBuyers, WholesaleSettings, WholesaleDeal
- `public/InvestorRoom.jsx`, `public/SellerTransaction.jsx`, `public/rooms.css`
- `app/services/wholesale_publication.py` (the phone fallback only)
- `handoff/WHOLESALE_REAL_ESTATE_HANDOFF.md` (§69–70)

**Backups on Windows:**

- `Downloads\p72\p73_originals_backup.tar` (the 18 originals)
- `Downloads\p72\advisorflow.db.pre-p73.bak`

## 10. Screenshots

`handoff/p7-3-shots/` holds full-resolution files:

- one `*-desktop.png` per screen (30 files)
- mobile shots of Acquisition Command, Deal Operations and the Investor Room
- `all-widths/` with 120 images
- `review-report.json`

## 11. Known gaps

1. The public masthead still shows the platform support **email**. A wholesale public-contact setting is needed (§5).
2. Hero and card photographs are generic stock (Unsplash License). Replace them with owned brand photography when available by dropping same-named files into `ds/photos/`.
3. Representative house photos only appear because no sandbox property has an uploaded photo. Real photos replace them automatically.
4. The board's "Calendar" and "Tasks" tabs on Deal Operations, and "My Properties / Watch List" on Properties, were not built. No backend exists for them, and a fake tab would violate data truth.
5. The dark/light toggle is hidden inside Wholesale by design (one light product). It still works elsewhere in the platform.
6. In full-page screenshots the sticky top bar can appear mid-image. That is a capture artifact, not a layout bug.

## 12. Constraints honoured

- Nothing was committed, pushed, merged or deployed to Render.
- The production database was not touched.
- No real property vendor was connected, and there is no real skip tracing.
- No production SMS or hunting was started.
- `feature/universal-intake` was not merged and Phase 8 was not started.
- No other workstream's files were touched: base hashes were verified before every extract.
