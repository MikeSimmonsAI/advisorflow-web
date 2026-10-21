# EvoSys Pro Roofing — build handoff

**Branch:** `feature/roofing-development` (lane E, folder `C:\Dev\lanes\roofing`)
**Started from:** production main `e778c585` (Oct 20, 2026, 3:19 PM CT)
**Status:** built and tested on the lane branch. **Not merged, not deployed.** The Mission Control redesign is waiting for the owner's visual approval.

## What it is

Roofing is the fourth EvoSys Pro vertical (with SCI, Wholesale and Atlantis). It turns the leads a roofing company **already owns** into conversations, booked inspections and tracked sales. It does not generate leads.

The flow: the company uploads its contacts. EvoSys qualifies them. The AI follow-up texts and emails the eligible ones. Interested homeowners are offered real inspection times. Confirmed inspections go on the salesperson's calendar. The salesperson takes over, and estimates and won/lost are tracked.

Per the course correction (`EVOSYS_ROOFING_AI_OPERATING_SYSTEM_VISION.md`), the owner lands on **Mission Control**. It shows what the AI is doing, what needs a person, and what the work produced. The CRM parts (pipeline board, tables) sit one level down.

## Screens

| Route | Screen | Purpose |
|---|---|---|
| `/roofing` | Public landing page | "Turn Roofing Leads Into Booked Inspections." How it works, a sample product preview and the demo request form. All stat cards are labelled as sample results. |
| `/program/roofing` | Mission Control | Shows what the AI is doing now, the outcome strip (every % with what it's a share of), the AI workboard (Human needed / Inspection requested / Booked / Homeowner replied / AI working), Needs your attention, and a 30-day snapshot. |
| `/program/roofing/queue` | AI Work Queue | Shows the running playbook and its results, every homeowner by lane, and the "ready, not started" list. Pause AI / Resume AI per homeowner. Start follow-up for ready homeowners. |
| `/program/roofing/conversations` | Conversations | Prioritized inbox, the full thread (AI, team and homeowner messages), AI draft, offer real inspection times, take over / hand back, mark read, and the homeowner's roof details, stage, qualification, touches, next action and notes. |
| `/program/roofing/inspections` | Inspections | Upcoming confirmed inspections, past ones waiting on "Mark inspected", homeowners who asked but aren't booked, and 3-step booking against real openings. |
| `/program/roofing/performance` | Performance | Every number with its denominator: funnel, reply rate by touch 1/2/3/4+, trend by day or week, booking conversion, follow-up cost, opt-outs. Date and salesperson filters. |
| `/program/roofing/pipeline` | Pipeline | Imported → Qualified → Contacted → Engaged → Inspection Offered → Inspection Booked → Inspected → Estimate Sent → Won / Lost, with salesperson filter and Move to. |
| `/program/roofing/leads` | Leads & Data | Upload into the platform's real import wizard, qualification counts (Ready / Needs review / Excluded with reasons), and the homeowner table. |
| `/program/roofing/settings` | Settings & Playbooks | Company profile (name, logo, colors, service areas, inspection types and length, default salesperson, who gets alerts), automated-text switch status, playbooks, team. |

## Server: `/roofing-ops/*` (app/routers/roofing_router.py)

Every route requires the `roofing_program` feature **listed explicitly** on the workspace. A legacy NULL allow-list is not a roofing company. Homeowners always come through lead scope, so an owner sees the company and a salesperson sees only their own book. Another company's IDs return 404. **Nothing in this router sends a message.**

- `GET /status`, `POST /setup`, `PUT /profile`, `GET|POST /text-switch` (only the platform owner can flip it)
- `GET /mission`, `GET /playbooks`, `GET /dashboard?start&end&owner`, `GET /inbox?bucket&q`
- `GET /qualification`, `POST /outreach/enroll`
- `GET /leads`, `GET|PATCH /leads/{id}`, `POST /leads/{id}/stage`, `POST /leads/{id}/notes`, `POST /leads/{id}/takeover`
- `GET /leads/{id}/openings`, `POST /leads/{id}/book`
- `GET /pipeline`, `GET /team`
- Platform owner only: `POST /god/roofing/setup {organization_id}` grants the roofing feature set and configures the company. `POST /god/roofing/demo` creates or refreshes the SAMPLE workspace.

## What it reuses (no second engine)

| Need | Existing engine used |
|---|---|
| Homeowner record | `Lead` (+ `Lead.custom_fields` for roof issue, repair/replacement, inspection interest, urgency, roof age, insurance claim) |
| Import, column matching, duplicates | Universal Intake `/intake/*`. Setup registers the roofing fields on `Organization.crm_custom_fields`, so a "Roof Issue" column maps automatically. |
| READY / REVIEW / EXCLUDED and score | `app/services/qualification.py` |
| Follow-up sequence | `CadenceTemplate` "Roofing Inspection Follow-Up" (5 touches), run by `cadence_service` behind the per-workspace text switch |
| Conversations, sending, read state | `/communications/*` (all consent / DNC / quiet-hour gates), `/conversation-intel/*` (AI draft, takeover mode) |
| Openings and booking | `tenant_scheduling.availability` (salesperson hours, buffer, daily cap, blocks, connected calendar, fail-closed). The inspection is a `BookingLink`. |
| Alerts | One in-app `Notification` per person per booking |
| Tenant scope and roles | `lead_scope`, `require_tenant_user`, `is_manager_here` |

**New tables (only these):** `roofing_profiles`, `roofing_opportunities` (one per homeowner: stage, estimate and won amounts, lost reason) and `roofing_stage_events` (every move, by activity or by a person). They are created by `create_all`, so there is no `auto_migrate` entry.

**How stages move:** activity only moves a homeowner forward, and only from rows that exist: a text or email that actually left, a reply, a booking. Inspected, Estimate Sent, Won and Lost are set only by a person. Lost requires a reason.

## Shared files touched (append-only)

- `app/main.py`: 3 lines to mount the router
- `app/models/registry.py`: 1 import in each of the two blocks
- `app/services/entitlements.py`: the `roofing_program` key and its `REQUIRES leads`
- `frontend/src/App.jsx`: the roofing lazy imports and routes

`Layout.jsx`, `api/client.js`, `deps.py`, `auto_migrate.py` and `render*.yaml` were **not** changed.

## Turning it on for a roofing company

1. As the platform owner: `POST /god/roofing/setup {"organization_id": "<org id>"}`. This adds the roofing feature set, sets the industry to roofing (if it was generic), creates the profile, registers the roofing import fields and creates the follow-up sequence. It is made the default only if the company has no default sequence. It creates no users and turns no texting on.
2. The owner fills in Settings and uploads leads from Leads & Data.
3. The platform owner turns on automated texts for that workspace only when ready.

## Sample workspace (for demos)

`POST /god/roofing/demo`, or `app.services.roofing.demo_seed.seed(db, presenter_password=...)`, creates **"Summit Peak Roofing (SAMPLE)"**:
- 64 homeowners, all `is_test` with example.com emails and 555-01xx phone numbers
- 4 users: owner `demo.roof.owner@example.com`, salespeople alex / jordan / casey
- No sms, email or cadence features, so it cannot send

Every screen shows the "Sample data" label there.

## Tests

- `tests/test_roofing_program.py`: **15 passed**. Covers:
  - the feature gate, including that a NULL allow-list is not roofing
  - tenant isolation, and owner vs. salesperson scope
  - setup and import auto-mapping
  - stages moving only from real activity
  - field validation
  - dashboard denominators and touch attribution
  - honest empty states
  - inbox priority order
  - booking on real capacity, including refusing a taken slot and refusing an opted-out homeowner
  - pipeline
  - enrollment respecting qualification and the text switch
  - takeover pausing and resuming the sequence
  - the sample workspace being labelled and unable to send
- Existing entitlement and model-registry suites: **193 passed**.
- Frontend: `vite build` passes. `node --test tests/frontend` has 2 failures (`leadDetailTabs`, `wholesaleNav`) that **also fail on production main `e778c585`**. They are not caused by this work.
- Screenshots: real pages on a local server with the sample workspace (desktop 1440 and phone 390). No page-level sideways scroll on phones.

## Not done yet / remaining integration

- **More than one playbook running at once.** The cadence engine runs one default sequence per company. The five extra playbooks (old estimate, stalled inspection, repair vs. replacement, storm inquiries from your own list, past-customer check-in) are shown as "Available, not running".
- **Unattended AI replies.** These stay behind the existing `AI_BACKGROUND_AUTOMATION_ENABLED` switch, which is off by default. Today the AI drafts and a person sends. Automated touches go out through the cadence once texting is on.
- **Homeowner confirmation text on booking.** Off by default. It is opt-in per booking and goes through the normal send checks.
- **Calendar push.** Only when the salesperson has a connected calendar. Otherwise the booking screen says so.
- **Demo form on `/roofing`.** Posts to `/site-intake/evosyspro/demo-request`, which only accepts submissions once a public intake destination is set for EvoSys Pro.
- **Roofing nav link inside the main EvoSys app.** Not added, to avoid editing the shared `Layout.jsx`. Roofing users go straight to `/program/roofing`. Adding a link is a one-line Layout change to coordinate with the other lanes.
- **Revenue.** Shown only from estimate and won amounts your team enters. No outside job-management system is connected.

## Previewing locally

Backend:
```
uvicorn app.main:app --port 8000
```
Use a SQLite `DATABASE_URL` and `OUTBOUND_EMERGENCY_STOP=1`, then seed the sample workspace.

Frontend:
```
cd frontend && VITE_API_BASE_URL=http://127.0.0.1:8000 npx vite --port 5173
```
Sign in as `demo.roof.owner@example.com` with the presenter password you seeded, then open `/program/roofing`.
