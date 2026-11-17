# Program Access V2 — Premium Command Center

UI-REDESIGN lane. Replaces the compact EvoSys Pro launcher at `/workspaces` with the owner-approved
hybrid design: navy sidebar, welcome header, summary tiles, a featured photo banner, large photographic
workspace cards with category filters, and a separate Prospect Previews section.

The BookaBoost launcher (`BookaBoostProgramAccess.jsx`) is untouched. The only change in
`ProgramAccessPortal.jsx` is that the EvoSys launcher body now renders `v2/CommandCenter.jsx`.

## Files

| File | What it does |
|---|---|
| `frontend/src/pages/program/ProgramAccessPortal.jsx` | Loads the same server data as before (`/auth/my-contexts` via the caller, `/launcher/pins`, `/launcher/attention`, Deal Builder access) plus `/health/ready`, then renders the command center. |
| `frontend/src/pages/program/v2/CommandCenter.jsx` | The page: sidebar, header, tiles, featured banner, workspace grid/list, previews. Presentation only. |
| `frontend/src/pages/program/v2/commandCenterModel.js` | Pure rules: categories, photo choice, featured choice, summary counts, health label. Tested in `frontend/tests/programAccessV2.test.mjs`. |
| `frontend/src/pages/program/v2/commandCenter.css` | All styles, scoped under `.pa2`. |
| `frontend/src/pages/program/v2/photos.js` + `img/` | Bundled licensed photos (WebP). Credits: `PHOTO_CREDITS.md`. |

## Where every number and control comes from

| On screen | Source | When there is no real answer |
|---|---|---|
| Cards | `programCards()` + `salesToolCards()` from `/auth/my-contexts` (unchanged) | No card |
| Authorized Workspaces | Number of cards this login can open (previews not counted) | — |
| Active Clients | Distinct client organizations behind those cards. Back Office, Executive Suite, Deal Builders, My Team, onboarding and the Acquisition hub are internal, not clients. VOLT Marketing and Consumer Leads are one organization and count once. | — |
| Prospect Previews | Preview cards (`preview_contexts`) | Tile hidden at 0 |
| Platform Health | Public readiness report `GET /health/ready` (app, database, jobs): ok → "Live", degraded → "Degraded", failed → "Down" | Tile hidden |
| Notifications (bell + sidebar) | `GET /launcher/attention` (up to 3 real items) | Bell and nav item hidden |
| Featured workspace | First pinned card the server still lists; otherwise the first card in server order. There is no "recently opened" record in the system, so that step is not invented. Previews are never featured. | — |
| Pins | `GET/PUT /launcher/pins` (unchanged) | Nothing pinned |
| God Mode link | Only when the user's role is `god_admin` **and** the server lists `/god` in `platform_contexts` | Hidden |
| People & Access link | Owner → `/god/access`; a sales manager with `sales_team.my_team` → `/sales/my-team` | Hidden |
| Settings | `/change-password` (the account setting every signed-in login has) | — |
| My Day | `/sales`, only when the server gives this login a sales Back Office at `/sales` | Hidden |
| Resources | `/training` (every signed-in login; the server decides what it shows) | — |
| Manage Access (banner) | `/god/access`, owner only (same check as the God Mode link) | Hidden |
| "Active" on a card | Every card is a destination the server lists for this login right now; a sample workspace says "Sample data" and a view-only one says "View only" | — |
| Name, role, initials | The signed-in user (`af_user`), never hard-coded | — |

"Activity" from the mockup is left out: the app's activity page needs a workspace to be chosen first,
so from the launcher it would be a wrong page. "My Workspaces" and "Notifications" jump to or open the
matching part of this page.

## Categories

| Filter | Cards |
|---|---|
| Clients | Every client business workspace (SCI, Restland, Atlantis, Roofing, Wholesale, Blue Ridge, Insurance, Vincent Greene, Commercial Cleaning, any new client) |
| Operations | Acquisition Control Center, VOLT Marketing, Consumer Leads |
| Sales & Growth | Executive / Salesperson Deal Builder, My Team, Sales Onboarding, EvoSys Sales |
| Administration | Back Office, Executive Suite |

A filter appears only when that login has a card in it. Filters and search work together.

## Security

No permission, route, server endpoint or membership changed. The page renders only what the server
listed; every destination re-checks on the server, as before. Tests cover: salesperson sees no Executive
Deal Builder, Back Office, My Team, God Mode or People & Access; God Mode needs the server's `/god` row.

## Evidence

- Screenshots: `screenshots/` (rendered from the built app in Chrome with test data — see below)
- Click-through checks: `qa/interactions.py` (33 checks)
- Screenshot script: `qa/shots.py`; test logins: `qa/fixtures.json`

The screenshots use **test data** shaped like the owner's account (same 16 program names and 3 previews,
fake ids, fake API). They are not production screenshots. Production screenshots are taken after the
owner approves and the change is deployed.

## Visual comparison with the approved mockup (round 2, after the owner's rejection)

The first version used tall portrait cards and was rejected. Round 2 was rebuilt against the approved image,
rendered at the image's own size (1672 x 941) and compared side by side (the comparison image is in the owner's
Drive inbox, not in this public repo, because the mockup shows the owner's name).

| Measure (at 1672 x 941) | Mockup | Implementation |
|---|---|---|
| Sidebar width | ~250 px | 250 px |
| Dark header incl. summary tiles | ~155 px | ~150 px |
| Summary tiles | 4 white tiles ~170 x 60, top right | 4 white tiles ~165 x 60, top right |
| Featured banner | ~185 px tall, building left, cleaning equipment right, 3 actions | 185 px, building left, cleaning supplies right, 3 actions (Manage Access owner-only) |
| Workspace cards, first row | 4 columns, ~333 x 140 | 4 columns, 334 x 139 (2.4 : 1) |
| Workspace cards, later rows | ~333 x 92 | 334 x 108 (one-line names) to ~112 (two-line names; text is never clipped, the card grows) |
| Grid gap | ~18 px | 18 px |
| Card layout | icon tile + name left, green "Active", blue Open bottom-right | same |
| Previews | one row: intro + 3 cards with photo and outlined "Open Preview" | same |

Remaining differences (intentional or data-driven):
- 16 workspaces instead of the mockup's 12, so at 1672 x 941 the 4th card row and the previews sit just below the first screen.
- Later rows are 108 px rather than the mockup's ~92 px so a two-line business name, its subtitle, "Active" and "Open" all fit without clipping.
- Card order: the pinned workspaces first (the first pin is the featured one), then the server's stable order (SCI first). The mockup's order is not a rule the server knows.
- Official EvoSys Pro emblem instead of the mockup's white "EP" drawing; initials instead of a profile photo
  (profile photos are no longer loaded on every request, #654).
- No "Activity" nav item, no "?" help button and no verified badge: there is no real destination or data for them.
- God Mode stays in the sidebar for the owner only (required by the brief; not drawn in the mockup).
- Photos are licensed stock chosen to match the mockup's subjects; they are not the mockup's generated images.

## Working preview (test data)

`preview/make_preview.mjs` turns a normal build into a static design preview that answers every request from
`qa/fixtures.json` (no real server, account or customer data) and shows a "Design preview - test data" ribbon with
owner / salesperson / operator views.

## Known limitations

- A login with exactly one workspace and an Acquisition operator grant is still sent straight into its
  workspace (existing rule in `App.jsx` `WorkspaceSelector`, unchanged here).
