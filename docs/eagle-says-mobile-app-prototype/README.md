# EvoSys Pro Mobile (Eagle Says Mobile App Prototype)

App name shown to users: **EvoSys Pro** (home-screen label "EvoSys"). One existing
EvoSys login opens **Programs**, **Chat**, **Activity** and **Account** at `/app`
on the existing EvoSys frontend, API and database.

- Current design: [`revised-design-board.png`](revised-design-board.png) (2026-11-15).
  The first board, [`approved-design-reference.png`](approved-design-reference.png), is kept for history.
- Screenshots (synthetic people, iPhone 13 emulation): [`screenshots/`](screenshots/)
- Timekeeping (Clock In/Out, My Time, Team Time) was in the prototype only and was
  **removed before release**: its tables, API, grants and screens never shipped.
  An old `/app/time…` link lands on Activity.

## Where the code lives

| Part | Path |
|---|---|
| Mobile screens (`/app/*`) | `frontend/src/teamapp/` (TeamApp, ProgramsScreen, ActivityScreen, ChatScreens, AccountScreens) |
| Program list (shared with desktop) | `programInventory()` / `programKey()` in `frontend/src/pages/program/programAccessModel.js`; open rule `programDestinations.js` |
| Return to the app | `frontend/src/pages/program/launcherOrigin.js` (+ `/workspaces` route in `App.jsx`) |
| Install manifest + icons | `frontend/public/manifest-evosys.webmanifest`, `frontend/public/icons/evosys-*.png`, `frontend/public/brands/evosyspro-emblem-96.png` |
| API (`/team-app/*`) | `app/routers/team_app_router.py` |
| Chat rights | `app/services/team_app_access.py`; chat logic `app/services/team_chat.py` |
| Activity | `app/services/team_activity.py` (reads existing records, stores nothing) |
| Chat tables | `app/models/team_app_models.py` (registered in `app/models/registry.py`) |
| Tests | `tests/test_team_app.py`, `frontend/tests/teamApp.test.mjs` |
| Preview (free Render Blueprint) | `render-team-app-preview.yaml`, `scripts/seed_team_app_preview.py`, `scripts/check_team_app_preview.py` |

## Programs = desktop Program Access, nothing more or less

The Programs tab renders `programInventory()` of the same `/auth/my-contexts` answer
desktop Program Access uses: its cards, sales tools (Deal Builders, My Team, Sales
Onboarding), "Other authorized workspaces" and Previews, in the same order, with no
cap and no mobile-only filter. Search narrows the full list; clearing it restores it.
Desktop cards and mobile rows both carry `data-program-key` (the same key), so parity
is checked by comparing the two key sets for one login. Colours and icons
(`programStyle()` in `teamHelpers.js`) are presentation only:

| Program | Accent |
|---|---|
| SCI | `#4F46E5` deep indigo |
| Restland / funeral | `#087F68` evergreen |
| Sales Workspace, Deal Builders, My Team | `#2563EB` cobalt |
| VOLT Marketing | `#C62839` crimson |
| Commercial Cleaning / Acquisition | `#7C3AED` violet |
| Back Office | `#475569` slate blue |
| Roofing / Energy / Wholesale / Insurance / Gold | `#C2410C` / `#B45309` / `#0E7490` / `#1D4ED8` / `#A16207` |
| Anything else | `#3B4A63` fallback |

Opening a program from the app remembers (per tab) that it came from the app, so the
workspace's Program Access / Switch program / Other Workspaces / Deal Builder Back
returns to `/app/programs`. Web sign-in, the web launcher and direct links are unchanged.

## Activity

**Updates** (last 14 days) and **Upcoming** (one day at a time, in the person's own
time zone — America/Chicago by default) from records EvoSys already keeps:

| Source | Who sees it (checked on the server, every request) |
|---|---|
| Workspace bookings (`booking_links` → `leads`) | Only workspaces in `authorized_contexts()` for this login — the list desktop Program Access is built from. Test leads never show. Only appointment type, time, status and place are sent — no family name, phone or email. |
| Sales appointments | Only appointments this person takes part in. |
| Chat @mentions | Only while this person can still read that room. |

Updates show booked / updated / cancelled. Each item uses the accent and icon of its
program and opens that program's own destination (which re-checks access). A source
that fails is shown as "not available right now" and the others still answer.
Not in this release: work scheduling, attendance, payroll, new calendar integrations.

## Chat

DMs (only their two people can read them or their files), **Sales Team** (sales reps
and managers enrolled once; a removed person stays removed), **Company
Announcements** (everyone with Chat reads; only `team_announce` posts). Replies,
@mentions, unread counts, private files (existing private store, `no-store`),
Google Drive links. Messages scroll on their own; the composer and attach button stay
above the keyboard.

| Person | Chat | Post announcements | Manage chat rooms |
|---|---|---|---|
| Platform (`god_admin` / platform `super_admin`) | ✓ | ✓ | ✓ |
| Active sales rep / manager on the platform's sales team | ✓ | grant | grant |
| Anyone else | grant | grant | grant |

Grants are platform-scoped `user_capability_grants` (`team_chat`, `team_announce`,
`team_chat_manage`), set by the owner in **Account → Team access**. Room management
never opens anyone's DMs.

**Live roles (read-only check, 2026-11-15):** the everyday platform login is an
`org_admin` and a sales manager on EvoSys Pro Sales; the owner partner is an
`advisor`, sales manager and EvoSys Pro executive. Both have Chat automatically;
neither yet holds `team_announce` or `team_chat_manage` (the owner account turns
those on in Account → Team access).

## Layout

The navy header and navy bottom tabs never move; only the middle panel scrolls, so the
full program list is always reachable. Safe areas (notch, home bar) are respected. On
staging/preview the "Test environment" strip renders inside the shell, under the header.

## Private HTTPS preview

`render-team-app-preview.yaml` — a separate free-plan Render Blueprint
(`evosyspro-app-preview`) with its own database of synthetic `@preview.test` people,
no messaging keys, no background jobs, noindex. Not production, not SCI staging.

- App: https://evosyspro-app-preview-osba.onrender.com/app
- The free API sleeps after ~15 idle minutes (first request then takes about a minute);
  the free database expires 2026-12-15. The preview password is not in the repo.
- `python scripts/check_team_app_preview.py <api> <web>` (with `PREVIEW_PASSWORD` set)
  runs the end-to-end check.

Production never runs the preview seed: its start command is unchanged
(`render.yaml`), and the seed refuses any database holding a person outside
`@preview.test`.

## Run it locally

**Windows:** double-click `START_EAGLE_SAYS_PREVIEW.bat` (separate worktree, ports
8010/5174, synthetic people, password `Preview123!`). Stop with
`START_EAGLE_SAYS_PREVIEW.bat stop`.

macOS/Linux:

```bash
export DATABASE_URL=sqlite:///./team_app_preview.db
export JWT_SECRET=local-preview-secret-at-least-32-characters
python scripts/seed_team_app_preview.py
python -m uvicorn app.main:app --port 8000
cd frontend && VITE_API_BASE_URL=http://localhost:8000 npx vite --port 5173 --host localhost
```

## Put it on your phone

**iPhone (Safari):** open https://app.evosyspro.live/app, sign in, tap **Share** →
**Add to Home Screen** → **Add**. The icon is the silver-and-gold EP; it opens on Programs.

**Android (Chrome):** open the same address, sign in, tap **⋮** → **Install app**
(or **Add to Home screen**) → **Install**.

## Known limits

- Chat updates by polling (3 s in a conversation, 5–8 s for lists); no push notifications.
- Activity covers bookings, sales appointments and chat mentions; other record types
  (e.g. VOLT recruiting events) are not sources yet.
