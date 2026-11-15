# Eagle Says Mobile App Prototype

App name shown to users: **EvoSys Pro** (home-screen label "EvoSys").
One existing EvoSys login opens **Programs**, **Chat**, **Time** and **Account** on a phone.

- Approved design reference: [`approved-design-reference.png`](approved-design-reference.png)
  (extended with a Time screen). The "EvoSys Pro logo correction board.png" was not
  in the repo or the request, so the build follows its written description: full
  lockup on sign-in, the official silver-and-gold EP emblem for the home-screen icon
  and compact headers, both cut from the official logo used by the EvoSys sign-in page
  (`frontend/public/brands/evosyspro-logo.png`).
- Screenshots (synthetic people, phone emulation): [`screenshots/`](screenshots/)

## Where the code lives

| Part | Path |
|---|---|
| Mobile screens (`/app/*`) | `frontend/src/teamapp/` |
| Shared Program Access open rules (web launcher + app) | `frontend/src/pages/program/programDestinations.js`, `salesToolCards` in `programAccessModel.js` |
| Install manifest + icons | `frontend/public/manifest-evosys.webmanifest`, `frontend/public/icons/evosys-*.png`, `frontend/public/brands/evosyspro-emblem-96.png` |
| API (`/team-app/*`) | `app/routers/team_app_router.py` |
| Who may do what | `app/services/team_app_access.py` |
| Chat / Time logic | `app/services/team_chat.py`, `app/services/team_time.py` |
| Tables | `app/models/team_app_models.py` (registered in `app/models/registry.py`) |
| Tests | `tests/test_team_app.py`, `frontend/tests/teamApp.test.mjs` |
| Local preview seed (synthetic only) | `scripts/seed_team_app_preview.py` |

## Implementation decisions

**Stack.** Reuses the existing React/Vite frontend (react-router), FastAPI backend,
Postgres with the repo's `create_all` + `registry.py` convention for new tables, the
normal EvoSys session (`get_current_user`), the existing PWA service worker and the
existing private file store (`wholesale_files`: database by default, S3 when set).
Nothing depends on Lovable.

**Program Access is not reimplemented.** The Programs tab reads the same
`/auth/my-contexts` answer as `/workspaces` and opens cards with the same rule. That
rule was moved, unchanged, from `App.jsx` / `ProgramAccessPortal.jsx` into
`programDestinations.js` so both screens share it. SCI and every other workspace
appear only when the server lists them; each destination re-checks on the server.

**Who gets Chat and Time — decided on the server on every call, never by email:**

| Person | Chat | Time | Announce | Manage rooms | Team Time |
|---|---|---|---|---|---|
| Platform (`god_admin`, or `super_admin` of the platform) | ✓ | ✓ | ✓ | ✓ | everyone |
| Active sales rep / sales manager on the platform's sales team | ✓ | ✓ | – | – | – |
| Anyone else (e.g. Mike Schlueter) | by grant | by grant | by grant | by grant | by grant, only assigned people |

Grants are the existing platform-scoped `user_capability_grants` rows (same table the
Demo Suite uses), keys `team_chat`, `team_time`, `team_announce`, `team_chat_manage`,
`team_time_manage`. The platform sets them in **Account → Team access**. A Team Time
grant reviews only the people ticked for that manager (`team_time_scopes`). Room
management never opens DMs and never opens Team Time. Changing someone's team rights
keeps their other platform grants (Demo Suite) exactly as they were.

> Live-role note: the live Render database was not read for this build. If the
> everyday platform login is not `super_admin`, give it the five rights in
> Team access (or tell Claude to check its role).

**Chat.** DMs (one per pair, only those two can read them or their files), **Sales
Team** (sales people auto-enrolled once; a removed person stays removed), **Company
Announcements** (everyone with Chat reads; only announce rights post). Replies,
@mentions (only people who can read the room), unread counts, private uploads
(validated by file bytes, 25 MB limit, served only through an authenticated endpoint,
`Cache-Control: no-store`), Google Drive / Docs links only. Updates by short polling
(3 s in an open conversation, 5–8 s for lists and the badge) — no new service. Sends
carry a client key, so a retried send never posts twice.

**Time.** Server timestamps only, stored in UTC. One open shift per person is enforced
by a unique column in the database, so a double tap or two phones at once can't open
two shifts; retries return the saved record. Weeks run Monday–Monday in the person's
time zone (their EvoSys booking time zone, else their organization's, else
America/Chicago); shifts crossing midnight, a week boundary or a daylight-saving change
are split at real boundaries and measured in real elapsed time. Corrections need a
reason, can't overlap another shift or be in the future, and record who changed what,
when, before and after (also written to the audit log). Payroll, GPS and automatic
deductions are out of scope.

**Offline caching.** The service worker caches only the app shell (HTML, hashed
assets, icons). The API is on a different origin and is never cached; every
`/team-app` response is also `no-store`.

**Workspace locks.** `/team-app/` is its own named exception in
`program_experience.enforce` (`TEAM_APP_PREFIX`), because it is company-wide rather
than a workspace screen and authorizes itself.

## Run it locally (preview)

**Windows: double-click `START_EAGLE_SAYS_PREVIEW.bat`** (repo root). It gets this
branch into its own folder (`C:\Dev\lanes\eagle-says-mobile`) without touching your
current checkout, installs what is missing, seeds the made-up people, starts the API
(port 8010) and web app (port 5174), and opens `http://localhost:5174/app`.
Stop it with `START_EAGLE_SAYS_PREVIEW.bat stop`.

Manual steps (macOS/Linux shell):

No per-branch preview environment exists for this repo (the only staging blueprint
is tied to the SCI lane's `sci-program` branch). Local run with synthetic people:

```bash
# 1. API (from the repo root)
export DATABASE_URL=sqlite:///./team_app_preview.db
export JWT_SECRET=local-preview-secret-at-least-32-characters
python scripts/seed_team_app_preview.py
python -m uvicorn app.main:app --port 8000

# 2. Web app (second terminal)
cd frontend
VITE_API_BASE_URL=http://localhost:8000 npx vite --port 5173 --host localhost

# 3. Open http://localhost:5173/app  (use "localhost", not 127.0.0.1 — CORS)
```

Synthetic logins (password
`Preview123!`): `platform@`, `partner@`, `chatmgr@`, `timemgr@`, `rep1@`, `rep2@`,
`outsider@` — all `@preview.test`.

To test on a phone on the same Wi-Fi, run vite with `--host 0.0.0.0` and the API with
`--host 0.0.0.0`, set `VITE_API_BASE_URL=http://<computer-ip>:8000`, and add that
origin to the API's allowed origins. Real install (Add to Home Screen) needs HTTPS, so
the install check on a physical phone happens on a deployed address.

## Install checks

| Check | How it was run |
|---|---|
| Manifest, icons, start on Programs, standalone | Verified in Chromium (manifest link swaps to `manifest-evosys.webmanifest` on `/app`) |
| iPhone layout (iPhone 13 profile) | Emulation |
| Android layout (Pixel 7 profile) | Emulation |
| iPhone Safari "Add to Home Screen" | **Not run on a physical device** — needs an HTTPS deployment |
| Android Chrome "Install app" | **Not run on a physical device** — needs an HTTPS deployment |

## Known limits of the prototype

- Opening a program from the Programs tab moves into the existing web screens; those
  screens' own "Program Access" links go to the web launcher (`/workspaces`), not back
  to `/app`. In an installed app on iPhone there is no browser back button there.
- Chat updates by polling; no push notifications for new messages yet.
- The Program Access list for a given live account was not checked against production
  data (no production access was used); its rules are the existing, tested ones.
