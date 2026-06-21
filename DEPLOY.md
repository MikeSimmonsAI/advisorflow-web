# AdvisorFlow Web — Phase 1 Deployment Guide

## What's built (Phase 1 + 2 + early Phase 3, tested and working)

- **Multi-tenant auth** — login, JWT tokens, roles (advisor / org_admin / super_admin)
- **Excel lead import** — tested against a REAL Restland Dynamics CRM export
  (1,000-row "All Active Leads (2012)" file), not just synthetic test data.
  Flexible header matching handles the actual export column names.
- **Org-wide dedup engine** — phone + last name matching across ALL advisors, even with
  inconsistent phone formatting (dashes, parens, dots, plain digits — all tested working).
  Also handles historical numbers seeded from the old desktop ADB pipeline's sent log
  (see Scripts section) WITHOUT breaking the household-sharing case (one phone, multiple
  real people with different last names) — this was caught as a real bug during testing
  and fixed: a naive phone-only fallback would have incorrectly merged different family
  members sharing a landline, so the fallback is scoped to placeholder/historical entries only.
- **Inclusive tier routing** — ALL lead tiers imported and contacted, each on its own
  message track (Pre-Need price-lock, At-Need/Imminent support, Contract Sold upsell,
  email-only nurture, needs-review hold for untyped leads).
- **AI lead quality analysis** — analyzes Lead Type, Status Reason, and Last Action to
  classify quality (hot/warm/cold/dead/unknown). Falls back to a rule-based heuristic if
  the OpenAI call fails.
- **SMS sending** — via each advisor's own Twilio credentials, booking link injection,
  message templating.
- **9-touch re-engagement cadence (Phase 2)** — `app/services/cadence_service.py`.
  Day 1, 3, 7, 10, 14, 21, 30, 45, 60 schedule, matching the original LAP spec. Each touch
  uses the lead's message_track for the right offer, with light tone rotation per touch
  number. Auto-stops on any reply, booking, or DNC flag (wired into the inbound webhook).
  Tested: cadence starts correctly, skips DNC/duplicate/needs-review/email-only leads,
  stops correctly on reply.
- **Email-only outreach (Phase 2)** — `app/services/email_service.py`. Leads with no phone
  get a tier-matched email template (mirrors the SMS message-track system) instead of being
  excluded. Sends via SendGrid. Tested template rendering against a real lead from the file.
- **Google Calendar booking sync (Phase 2/3)** — `app/services/calendar_service.py`. Per-advisor
  OAuth connection, creates a real calendar event when a lead books a slot via the existing
  stateless booking-link system. **Requires YOUR Google Cloud Console setup** (OAuth client
  ID/secret, Calendar API enabled) — this is account-level setup I can't do for you, same
  category as Twilio Trust Hub registration. The OAuth callback route needs the actual
  request URL wired in once you're ready to connect it to a real frontend.
- **HOT reply email notifications (Phase 1 priority item)** — `app/services/notification_service.py`.
  Wired directly into the inbound SMS webhook — the moment a reply matches a hot keyword,
  an email fires to the advisor's notification address and a Notification record is logged
  in-app regardless of email delivery success.
- **Inbound reply webhook** — matches replies to leads, hot-lead keyword detection,
  STOP/unsubscribe handling, now also stops the re-engagement cadence and fires notifications.
- **Replies screen** — fixed the inverted-filter bug from the desktop version.
- **Master admin dashboard** — cross-advisor KPIs, total duplicates prevented org-wide.
- **Tier review workflow** — `GET /leads/needs-review` + `PATCH /leads/{id}/tier` lets
  advisors manually classify the 368 untyped leads from the real file instead of guessing.
- **Upload preview with zero side effects** — dry-run mode runs the exact same import logic
  as the real import, verified to leave the database completely untouched, with numbers
  that exactly match what confirming will actually do.

## Scripts (in `scripts/`) — bridges the old desktop pipeline into the web app

- **`clean_wupa_spam.py` / `.bat`** — removes WUPA/carrier-spam messages from the Pixel 9's
  SMS inbox via ADB, so they don't clutter the `content://sms` provider the reply-capture
  logic reads from. `.bat` launcher provided per your stated preference for pairing `.py`
  files with `.bat` launchers instead of inline multi-line `python -c` in CMD.
- **`rebuild_sent_log.py`** — reconstructs `FINAL_sent_log.csv` from the phone's SMS outbox
  via ADB, so the old desktop pipeline's send history is recoverable.
- **`seed_registry_from_sent_log.py`** — imports that CSV into the web app's
  ContactRegistry, so the ~1,150 numbers already protected by the old system are respected
  by the new dedup engine too. **Limitation documented in the script**: since the raw SMS
  outbox has no last-name metadata, these are registered under a placeholder bucket that
  blocks re-sends to that exact phone number specifically — if you still have the original
  CRM exports those sends came from, re-importing those through the normal upload flow is
  more accurate (captures real names) and is the better path where possible.

- **Bulk send** — the Leads screen now supports selecting multiple leads via checkboxes and
  sending one message to all of them at once, with the same template placeholders
  (`{first_name}`, `{advisor_name}`, `{booking_link}`) filled in per lead.
- **Search and filtering on the Leads screen** — search by name, phone (digits-only match,
  ignores formatting), or email; filter by tier and status, all client-side and instant.
- **Automated test suite** — 42 tests in `tests/`, covering the dedup engine (including the
  household-sharing edge case and the placeholder-historical-entry fix), the real Restland
  file import (locks in the exact verified numbers: 856 imported, 775 active SMS, 340
  Contract Sold upsell, 368 needing review), auth/password lifecycle, the 9-touch cadence
  exclusion rules, and the template override system. Run with `pytest` from the `backend/`
  folder. **Verified to actually catch regressions**, not just pass trivially — deliberately
  reintroduced the household-sharing bug during testing and confirmed the suite failed loudly
  with a clear assertion pointing at the exact problem, then confirmed it passes again once
  fixed.
- **Message template editor** — org_admin/super_admin can now customize the SMS and email
  wording for every lead tier (Pre-Need, At-Need, Imminent, Contract Sold upsell, email-only)
  directly in the UI, with a "reset to default" option per template. Customizations are
  stored per-organization and take effect immediately on the next send — verified the
  cadence engine and email service both check for an org override before falling back to
  the hardcoded default.

## Real-file test results (the actual Restland 2012 export, 1,000 rows)

| Outcome | Count |
|---|---|
| Total rows | 1,000 |
| Imported (any channel) | 856 |
| Active SMS leads | 775 |
| Email-only leads queued for email outreach | 55 |
| Tier: Pre-Need | 86 |
| Tier: At-Need | 6 |
| Tier: Imminent | 1 |
| Tier: Contract Sold (upsell track) | 340 |
| Tier: needs manual review (untyped) | 368 |
| Duplicates within this file/batch | 1 |
| Call-restricted (compliance DNC) | 25 |
| Skipped — no usable contact info at all | 136 |
| Skipped — internal NSMG/Restland system records | 8 |

- **Manual send** — `POST /sms/send` (already existed) is now reachable from the UI: the
  Lead Detail page (`/leads/{id}`) shows the full conversation thread and a compose box to
  write and send a message right now, not just through the automatic cadence.
- **Lead detail page** — click any lead to see their full timeline (every message and reply,
  merged chronologically), the AI quality read, and contact-card details in one place.
  Backed by `GET /leads/{id}/timeline`.
- **Forced password change** — every seeded account has `must_change_password=True` by
  default. The frontend redirects to `/change-password` automatically until the advisor
  sets their own password via `POST /auth/change-password`.
- **Settings page** — each advisor can now enter their own Twilio Account SID, auth token,
  phone number, and caller ID name directly in the UI (`PUT /settings/twilio`), plus their
  notification email and hot-reply alert preference (`PUT /settings/notifications`). The
  auth token is encrypted before it touches the database and is never returned to the
  frontend once saved.

- **Google Calendar OAuth — now fully wired** — the callback route that was previously a
  stub now actually captures the OAuth code from Google's redirect, stores the encrypted
  refresh token, and redirects the advisor back to the Settings page with a clear
  success/error message. The Settings page has a working "Connect Google Calendar" button.
- **Test coverage expanded to 66 tests** — added full coverage for the SMS service (template
  rendering, DNC blocking, booking link creation, batch send/skip logic — Twilio's actual
  API call is mocked, so these test OUR logic, not Twilio's), the email service (template
  rendering including org overrides, send success/failure handling), and the notification
  service (confirmed the in-app Notification record is always created even when the email
  itself fails to send, and confirmed one advisor cannot mark another advisor's notification
  as read).
- **`render.yaml` Blueprint** — one-click deploy config for Render that provisions the
  database, backend, and frontend together instead of manual UI clicking through each piece.
  Validated as syntactically correct YAML.
- **`.env.example`** — a complete reference for every environment variable the backend
  needs, with explanations of where each value comes from and why Twilio credentials are
  deliberately NOT a global env var (each advisor sets their own via Settings).

- **AI lead analysis — wired in for real** — previously built but completely disconnected
  from the rest of the app (no router, no UI button). Now has a real `/ai/analyze/{lead_id}`
  endpoint and a working "Run analysis" button on the Lead Detail page. Also fixed a real
  bug along the way: the OpenAI client was being constructed at module import time, which
  meant the entire backend would crash on startup if `OPENAI_API_KEY` was missing or
  invalid — not just the AI feature. Fixed with lazy initialization; confirmed the whole
  app now boots fine and the analysis function falls back gracefully with zero OpenAI
  configuration at all.
- **Real scheduled job runner for the cadence engine** — `app/jobs/run_cadence_job.py`,
  designed to run as a Render Cron Job (already added to `render.yaml`) instead of relying
  on someone manually clicking "Run due touches now" every day. Processes every
  organization independently so one org's failure can't block another's — tested by
  deliberately injecting a failure for one org and confirming the other still completed.
  Exits with a non-zero status on any error so Render's Cron Job dashboard flags failed
  runs automatically.
- **Test suite expanded to 88 tests, now including real router-level (HTTP) tests** —
  every test before this point exercised a *service* function directly. Added
  `test_admin_router.py` using FastAPI's TestClient to hit actual HTTP endpoints with real
  auth headers, confirming auth is enforced, the role check correctly rejects a regular
  advisor from admin routes, and — critically — that an org_admin can never see another
  organization's leads or advisors, even when both exist in the same database (relevant
  once North Star Memorial Group or other customers share this platform later).
  **This work caught a real, subtle test-infrastructure bug**: the in-memory SQLite
  database used for tests needed `StaticPool` — without it, a request going through
  FastAPI's TestClient could silently open a second, completely empty database instead of
  the one the test had set up, surfacing as a confusing "no such table" error. Fixed in
  `conftest.py`; reran the full suite afterward to confirm the fix didn't disturb any of
  the other 80+ tests.

- **Notification bell — built but invisible, now wired in for real** — the backend has had
  a full notification system (unread tracking, mark-as-read, per-user isolation) since early
  on, but there was no UI for it anywhere. Added a real bell icon in the top bar of every
  page, polling every 30 seconds, with a dropdown showing unread hot-reply alerts that
  navigate straight to the lead when clicked.
- **Active cadences list** — the Cadence page only ever showed summary counts ("12 active").
  Added `GET /cadence/active` and a real table showing exactly which leads are queued up,
  their touch progress (e.g. "3 / 9"), and when their next message goes out — clicking a row
  goes straight to that lead.
- **Admin org-wide leads view — fixed a real data bug** — `GET /admin/leads` existed on the
  backend but was never called from the frontend at all. Wired it in as a searchable
  "All leads" tab on the Master Dashboard. While building it, caught and fixed a real bug:
  the endpoint was returning the raw Lead ORM object, which only exposes `assigned_to_id` —
  a bare UUID, meaningless to look at. Fixed to join in the advisor's actual name
  (`assigned_to_name`), with "Unassigned" for leads with no owner. Tested both the fix and
  the unassigned-lead edge case.
- **Click-through navigation across Overview and Replies** — every stat card on the Overview
  page and every reply card on the Replies page now navigates to the relevant lead or
  filtered view instead of being a dead end. Fixed a real wiring gap along the way: the
  Replies page had its own internal hot-only toggle but never read the `?hot_only=true` URL
  parameter, so a link built to filter straight to hot replies would have silently landed
  on the unfiltered view.
- **Test suite grew to 97 tests** — added router-level tests for the cadence active-list
  endpoint (confirming cross-advisor isolation, same security category as the admin
  dashboard) and the admin leads advisor-name fix.

- **Booking status — another real gap closed** — the `BookingLink` table tracked whether a
  lead booked, what time, and whether a Google Calendar event was created since early in
  the build, but none of it was ever visible anywhere. Added it to the Lead Detail page:
  pending/booked/cancelled status, the actual booked time, whether it made it onto Google
  Calendar, and a cancel button.
- **Real security fix: `cancel-booking` had zero ownership check** — found while auditing
  the booking flow. Any logged-in advisor, in ANY organization, could cancel any other
  advisor's booking just by knowing or guessing the booking ID — no auth boundary at all
  beyond being logged in as *someone*. Fixed to verify the booking's lead belongs to the
  current advisor's organization. Wrote a test that specifically simulates an attacker in
  a different organization attempting this and confirms it's now rejected with a 404 and
  the booking is left untouched.
- **Fixed a real but narrow timestamp-ordering bug** — caught while testing "show the most
  recent booking link" logic: confirmed during testing that two records created within the
  same second get identical timestamps at the database level, making "most recent" briefly
  ambiguous. Documented the real-world impact (negligible — this only matters if two
  booking links are created for the same lead within the same second, which doesn't happen
  in normal human-paced usage) rather than papering over it with a UUID tie-breaker that
  would have been actively misleading (UUID4 ordering has no relationship to creation time).
- **Test suite grew to 111 tests**, including new router-level coverage for `leads_router.py`
  and `calendar_router.py` — both previously untested at the HTTP layer.

- **Visual redesign deployed** — the dark "command console" look got pushed further into a
  "2075 command center" aesthetic (closer to Palantir/Anduril/Linear) per direct feedback:
  glowing corner-bracket accents on every panel and stat card via CSS pseudo-elements (no
  JSX changes needed), a glowing left accent bar and gradient fill on the active sidebar
  item, a faint fixed scanline grid behind all content, and a richer color system with a
  purple accent added alongside the original blue/green/red/amber. Tuned for restraint after
  initial feedback — badges, scrollbars, and repeated card chrome dialed back from full glow
  to plain color/border, since those repeat dozens of times per screen and don't need the
  same visual weight as primary actions and KPI numbers. Verified at every step: all 112
  CSS class names preserved exactly, zero dangling CSS variables, and the compiled
  JavaScript bundle is byte-for-byte identical to the pre-redesign build — confirming this
  was a pure visual change with zero functional risk.

## What's NOT built yet

- Caller ID Name (CNAM) actual Twilio registration (needs your Twilio Trust Hub/A2P 10DLC
  setup — this is a real account-level action only you can take, not a coding task)

## Deploying with the Render Blueprint (fastest path)

Instead of manually clicking through Render's UI step by step, use the included
`render.yaml`:

1. Push this whole repo to GitHub (backend + frontend together, in the structure shown
   in `FILE_MAP.md`)
2. In Render: **New > Blueprint**, connect the repo
3. Render reads `render.yaml` and provisions the database, backend, and frontend together
4. Render will prompt you for the secret values it can't generate itself: `OPENAI_API_KEY`,
   `SENDGRID_API_KEY`, `EMAIL_FROM_ADDRESS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
5. Everything else (`DATABASE_URL`, `JWT_SECRET`, `ENCRYPTION_KEY`) is generated
   automatically — no manual key generation needed

## Deploying to Render (recommended — simplest)

1. Create a free Render account at render.com
2. Push this `backend/` folder to a GitHub repo
3. In Render: **New > Web Service**, connect the repo
4. Build command: `pip install -r requirements.txt`
5. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
6. Add a **PostgreSQL** database (Render has a free tier, then ~$7/mo for persistent storage)
7. Set environment variables in Render's dashboard:
   - `DATABASE_URL` — Render gives you this automatically when you attach the Postgres instance
   - `JWT_SECRET` — generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`
   - `ENCRYPTION_KEY` — generate with `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `BOOKING_BASE_URL` — `https://advisorflow-booking.vercel.app` (your existing booking backend)
   - `OPENAI_API_KEY` — your LAP key (the one currently hitting 429s — add billing at
     platform.openai.com before relying on AI analysis in production; the fallback
     heuristic covers you in the meantime)
   - `SENDGRID_API_KEY` — for email-only lead outreach and HOT reply notifications
   - `EMAIL_FROM_ADDRESS` — the "from" address for outbound emails (must be verified in SendGrid)
   - `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — from Google Cloud Console, for Calendar sync
   - `GOOGLE_REDIRECT_URI` — your deployed domain + `/calendar/oauth/callback`
8. Deploy. Render auto-builds on every git push after this.

## Deploying to Railway (alternative — also simple)

Same steps, but Railway auto-detects the `requirements.txt` and Procfile. Add a `Procfile`:
```
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```
Railway's PostgreSQL add-on works the same way — attach it, copy the `DATABASE_URL` it gives you.

## After first deploy — seeding accounts

SSH into the running instance (Render/Railway both support a web shell) or run locally
pointed at the production `DATABASE_URL`:
```
python -m app.seed
```
This creates your organization, your super_admin login, and 5 placeholder advisor accounts.
**Edit the `ADVISORS` list in `app/seed.py` with real names/emails first.**

Everyone should change their temp password immediately — Phase 2 adds a proper
"change password" + "force reset on first login" flow.

## Twilio setup per advisor

Each advisor needs, stored on their `User` record:
- `twilio_account_sid`
- `twilio_auth_token_encrypted` (encrypt with `app.utils.crypto.encrypt_value()` before storing)
- `twilio_phone_number`
- `twilio_caller_id_name` (optional, e.g. "Restland Cemetery")

There's no UI for this yet in Phase 1 — set these directly via the database for the
proof-of-concept group of 5, or I can build a quick `/users/me/twilio` settings endpoint
next if that's higher priority than other Phase 2 items.

## Twilio webhook configuration

For each advisor's Twilio number, set the inbound SMS webhook to:
```
https://<your-render-domain>/sms/webhook/inbound
```
This is what lets replies get captured and matched back to leads automatically.

## Running the test suite

```bash
cd backend
pip install -r requirements.txt --break-system-packages
pytest
```

The import tests reference the real Restland export file at
`/mnt/user-data/uploads/All_Active_Leads__2012_.xlsx` and will skip automatically if that
file isn't present in your environment (e.g. running in CI) rather than failing.

## Local testing (already verified working)

```
cd backend
pip install -r requirements.txt --break-system-packages
export DATABASE_URL="sqlite:///./advisorflow.db"
export JWT_SECRET="dev-secret"
export ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
uvicorn app.main:app --reload --port 8000
```
Then visit `http://localhost:8000/docs` for interactive API testing (Swagger UI).
