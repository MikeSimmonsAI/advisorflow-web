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
| Platform Health | Public readiness report `GET /health/ready` (app, database, jobs) | Tile hidden |
| Notifications (bell + sidebar) | `GET /launcher/attention` (up to 3 real items) | Bell and nav item hidden |
| Featured workspace | First pinned card the server still lists; otherwise the first card in server order. There is no "recently opened" record in the system, so that step is not invented. Previews are never featured. | — |
| Pins | `GET/PUT /launcher/pins` (unchanged) | Nothing pinned |
| God Mode link | Only when the user's role is `god_admin` **and** the server lists `/god` in `platform_contexts` | Hidden |
| People & Access link | `sales_team.my_team` from the server → `/sales/my-team` | Hidden |
| Settings | `/change-password` (the account setting every signed-in login has) | — |
| Name, role, initials | The signed-in user (`af_user`), never hard-coded | — |

Sidebar items from the brief that have no launcher-level page in the app (My Day, Activity, Resources)
are left out rather than linked to dead or wrong pages. "My Workspaces", "Prospect Previews" and
"Notifications" jump to or open the matching part of this page.

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

## Visual comparison with the approved mockup

The approved hybrid mockup image was not attached to the request and was not found in the Drive inbox,
so a side-by-side comparison has not been possible yet. The build follows the written brief point by point
(sidebar 252 px, banner ~210 px, 4-column grid of ~280 px photo cards, navy/blue palette, compact tiles,
separate light preview section). Once the image is supplied, a side-by-side will be added here and any
differences corrected before approval.

## Known limitations

- Comparison with the mockup image is still to do (above).
- Program logos: no official third-party logos are drawn; cards show the existing program symbol.
- A login with exactly one workspace and an Acquisition operator grant is still sent straight into its
  workspace (existing rule in `App.jsx` `WorkspaceSelector`, unchanged here).
- Restland and SCI photos are dignified memorial-garden scenes, not either business's own grounds.
