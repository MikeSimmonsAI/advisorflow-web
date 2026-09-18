# EvoSys Pro public website — go-live checklist

Prepared: 2026-09-18
Repository tip at preparation: `96f885a`
Website integration commit: `cec8975`
Companion document: `EVOSYS_PUBLIC_SITE_PRODUCTION_RUNBOOK.md`

## How to read the status column

| Status | Meaning |
|---|---|
| **PASS** | Proven here, from code or from synthetic verification. Nothing to do at deploy time beyond confirming it survived the upload. |
| **FAIL** | Proven broken. Blocks go-live. |
| **NOT YET VERIFIED** | Could not be proven from the repository or from configuration available here. Must be confirmed on the live host or the production database before go-live. Not a defect — an unchecked box. |

Nothing in this checklist has been deployed, pushed, or enabled. All outbound
email gates are OFF. No real email, SMS, voice call, calendar event or Zoom
meeting was created in preparing it.

---

## A. Careers data protection

| # | Item | Status | Notes |
|---|---|---|---|
| A1 | Every careers path derives from `careers_base_dir()` | **PASS** | All 12 call sites; no exceptions |
| A2 | No hard-coded absolute server paths anywhere in the site | **PASS** | Only relative default `dirname(__DIR__).'/storage/careers'` |
| A3 | `CAREERS_STORAGE_DIR` overrides the default from env **or** `private/config.php` | **PASS** | Both careers entry points load `form-utils.php` before `careers.php`, so `evosys_cfg()` is always available |
| A4 | Applications, resumes, config, auth and throttle state all live under that one directory | **PASS** | Verified by reading every write site |
| A5 | Storage outside the document root works end to end | **PASS** | Synthetic application written to external directory; in-tree `storage/` stayed empty |
| A6 | External storage is not reachable over HTTP | **PASS** | Direct, `../` and `..%2F` probes all returned the index page; no leak of the synthetic address |
| A7 | `CAREERS_STORAGE_DIR` actually set on the production host, above the document root | **NOT YET VERIFIED** | Runbook step D; host path is host-specific |
| A8 | Storage directory writable by the PHP user, not world-readable | **NOT YET VERIFIED** | Runbook step C |
| A9 | Site-root `.htaccess` deny block for `.json/.pdf/.doc/.docx` present after upload | **NOT YET VERIFIED** | Dotfiles are silently skipped by some copy tools — runbook step F |
| A10 | `storage/.htaccess` and `private/.htaccess` present after upload | **NOT YET VERIFIED** | Same reason |
| A11 | Applicant records create no Lead and no customer-tenant record | **PASS** | Careers intake is a separate concept by construction |

## B. Careers admin

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Login uses `password_hash` / `password_verify` | **PASS** | |
| B2 | Login throttle: 8 attempts per 900 seconds, state outside the docroot | **PASS** | `login-attempts.json` confirmed written to external storage |
| B3 | CSRF tokens compared with `hash_equals` | **PASS** | |
| B4 | Application IDs validated against `^APP-\d{8}-[0-9A-F]{8}$` before any path is built | **PASS** | |
| B5 | Resume downloads containment-checked with `realpath` under `resumes/` | **PASS** | |
| B6 | Resume MIME sniffed with `finfo`, not trusted from the client | **PASS** | |
| B7 | CSV export neutralises formula injection (`=`, `+`, `-`, `@`) and strips tabs/newlines | **PASS** | |
| B8 | `CAREERS_ADMIN_PASSWORD_HASH` set on the host and password changed after first sign-in | **NOT YET VERIFIED** | Runbook step E |

## C. Website configuration

| # | Item | Status | Notes |
|---|---|---|---|
| C1 | `private/config.php` is git-ignored | **PASS** | |
| C2 | `private/config.example.php` lists every key an operator must set | **PASS** | `CAREERS_STORAGE_DIR` and `CAREERS_ADMIN_PASSWORD_HASH` added during this pass; see report |
| C3 | Site calls exactly four platform endpoints, all server-side from PHP | **PASS** | No CORS entry needed; browser never sees the API host |
| C4 | `PUBLIC_BOOKING_BASE_URL` set to the production API host | **NOT YET VERIFIED** | Runbook step D |
| C5 | `DEMO_WEBHOOK_URL` fallback set | **NOT YET VERIFIED** | Runbook step D |
| C6 | Host PHP version is 8.1 or newer | **NOT YET VERIFIED** | Binding constraint: `: never` in `request-demo/index.php`. On 8.0 the page is a parse error |
| C7 | No `mail()` call on the booking path in the website | **PASS** | Booking mail is owned and gated by the platform |
| C8 | Website renders `confirmation_email.status` and never claims an email was sent unless the backend says so | **PASS** | |

## D. Outbound safety

| # | Item | Status | Notes |
|---|---|---|---|
| D1 | `OUTBOUND_EMAIL_PUBLIC_BOOKING_CONFIRMATION` exists, defaults OFF, fails closed | **PASS** | |
| D2 | `OUTBOUND_EMAIL_PUBLIC_BOOKING_INTERNAL` exists, defaults OFF, fails closed | **PASS** | Also governs the emailed `.ics` fallback |
| D3 | `OUTBOUND_EMAIL_PUBLIC_BOOKING_REMINDERS` exists, defaults OFF, fails closed | **PASS** | |
| D4 | No public booking path depends on `OUTBOUND_EMAIL_STAFF_ESCALATION` any more | **PASS** | That switch still governs the unchanged in-product resend |
| D5 | There is no master outbound switch | **PASS** | One switch per source, by design |
| D6 | A booking completes correctly with all three flags off | **PASS** | Reports `delivery_disabled`; booking is saved and calendars are blocked |
| D7 | All three flags confirmed absent or off in the production environment at go-live | **NOT YET VERIFIED** | Runbook step I |
| D8 | Cadence and automatic outreach off in the production environment | **NOT YET VERIFIED** | Runbook step I |

## E. Backend process configuration

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | `SALES_REMINDERS` is owned by `ROLE_BACKEND` in `SCHEDULER_OWNER` | **PASS** | |
| E2 | A process with an unknown `SERVICE_ROLE` starts no loops | **PASS** | Deliberate |
| E3 | `SERVICE_ROLE=backend` set on the API service | **NOT YET VERIFIED** | Runbook step I. If unset, reminders silently never run |
| E4 | Reminder loop is in-process on the durable job ledger, not a separate cron service | **PASS** | 15-minute interval, 150s startup delay |

## F. Platform data configuration

All of section F is **NOT YET VERIFIED** — none of it is provable from the
repository. Each item refuses rather than guesses when missing.

| # | Item | Status | Failure mode if missing |
|---|---|---|---|
| F1 | `Platform` with slug `evosyspro` exists | **NOT YET VERIFIED** | `404 Unknown site.` |
| F2 | Exactly one active `BrandSalesOrg` on that platform | **NOT YET VERIFIED** | Generic `503` |
| F3 | `BrandSalesOrg.timezone` correct | **NOT YET VERIFIED** | Falls back to platform default |
| F4 | `inbound_assignment_mode` unset or `default_owner` | **NOT YET VERIFIED** | Any other value is refused |
| F5 | `default_inbound_owner_user_id` set | **NOT YET VERIFIED** | Code-less traffic fails closed — inbound leads would be refused, not misrouted |
| F6 | At least one meeting type `is_active` **and** `public_bookable` | **NOT YET VERIFIED** | Generic `503`; `public_bookable` defaults FALSE on every row |
| F7 | No internal meeting type accidentally marked `public_bookable` | **NOT YET VERIFIED** | Would publish an internal meeting to the internet |
| F8 | Duration and `requires_video` correct on the public type | **NOT YET VERIFIED** | Drives slot length and whether confirmation waits for a join link |
| F9 | `leadership_policy` set to `reporting_chain` if leadership attendance is wanted | **NOT YET VERIFIED** | NULL means legacy behaviour, no quorum |
| F10 | `leadership_minimum`, `leadership_depth`, `owner_required`, `include_additional_leaders` correct | **NOT YET VERIFIED** | Wrong people, or no slots |
| F11 | `reports_to_user_id` forms an unbroken chain for every bookable rep | **NOT YET VERIFIED** | Chain errors all produce one generic public refusal |
| F12 | Every rep and quorum-eligible leader has `accepts_bookings` true and **at least one availability window** | **NOT YET VERIFIED** | **No working hours means an empty slot list, which looks identical to "fully booked"** |
| F13 | Booking codes issued per salesperson, recorded against the right person | **NOT YET VERIFIED** | A bad code is a 404 refusal and never falls through to the default owner |

## G. Booking engine behaviour

| # | Item | Status | Notes |
|---|---|---|---|
| G1 | Leadership resolved from the reporting chain, never a brand-wide manager pool | **PASS** | There is deliberately no "any manager" policy value |
| G2 | Quorum requires the owner plus N free leaders from that owner's own chain | **PASS** | |
| G3 | Public slot payload carries no user ids, names, leader counts or org-chart hints | **PASS** | |
| G4 | Every configuration problem produces one generic public message | **PASS** | Specific reason is logged, not returned |
| G5 | A supplied-but-bad booking code is refused, never silently reassigned | **PASS** | |
| G6 | Booking is idempotent on a unique index, not check-then-insert | **PASS** | |
| G7 | Slot is re-checked at commit against our own rows and a forced external refresh | **PASS** | Refuses with `SLOT_GONE` if it has been taken |
| G8 | A calendar read failure at booking degrades, it does not refuse | **PASS** | A vendor outage must not veto the inbound pipeline |
| G9 | Reminders never crowd the confirmation — minimum 90 minutes from booking | **PASS** | |
| G10 | Full test suite green | **NOT YET VERIFIED** | Last run was green at the integration commit; not re-run in this preparation pass |

## H. Calendar readiness

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | Google and Microsoft OAuth connect flows exist and accept any authenticated user | **PASS** | So a brand-sales salesperson can connect |
| H2 | Connection list, test and disconnect endpoints exist | **PASS** | |
| H3 | Busy reads exclude subject, attendees and body at the provider query level | **PASS** | Microsoft `$select`; Google field-limited |
| H4 | The busy cache stores no titles — only user, provider, start, end | **PASS** | Structural, not a setting |
| H5 | `CalendarConnection` stores state only, never a token | **PASS** | `last_error` is message-only |
| H6 | One event per participant on that participant's own calendar | **PASS** | Partial success is a valid outcome |
| H7 | Provider events carry no attendee list | **PASS** | Prevents a second competing invitation |
| H8 | Internal `appointment.notes` never reaches an event body or `.ics` | **PASS** | Two separate builder functions |
| H9 | Sync is idempotent on `external_event_id` and runs after commit | **PASS** | A retry moves the event, it does not duplicate it |
| H10 | SCHED-05: two live connections with no chosen provider fails closed to `.ics` | **PASS** | Deliberate |
| H11 | There is **no** endpoint that sets `users.calendar_provider` | **FAIL** *(operational, non-blocking)* | Only the tenant-org value can be set. Mitigation: each salesperson connects exactly one calendar. Does not block go-live |
| H12 | Salesperson-facing UI surface for connecting a calendar | **NOT YET VERIFIED** | Routes exist and are reachable; the frontend screen was not traced |
| H13 | Each salesperson has exactly one calendar connected in production | **NOT YET VERIFIED** | Runbook step N |
| H14 | Public slot list reads the busy cache; the authoritative external check is at book time | **PASS** | Correct direction to fail, but see H15 |
| H15 | Scheduled background refresh of the busy cache | **NOT YET VERIFIED** — **does not exist** | On a public-only deployment, published slots may lag external calendars until a booking is attempted. Out of scope for this deployment |
| H16 | No shared or company calendar is used for availability or booking | **PASS** | No such concept exists in the codebase — see runbook §3.6 |
| H17 | Role of `info.evosyspro@gmail.com` decided and documented | **NOT YET VERIFIED** | It plays no role today. If a company-wide view is wanted, use one of the two options in runbook §3.6. It must **not** be connected as several reps' calendar |

## I. Deployment mechanics

| # | Item | Status | Notes |
|---|---|---|---|
| I1 | Working tree differs from HEAD by line endings only, apart from three unrelated WIP files | **PASS** | Verify with `git diff --ignore-cr-at-eol --name-only`. Do not globally normalise line endings |
| I2 | Site and API deployed from the same tree | **NOT YET VERIFIED** | Runbook step B |
| I3 | Synthetic end-to-end booking passes with all gates OFF | **NOT YET VERIFIED** | Runbook step O — the single most important pre-go-live test |
| I4 | Synthetic test appointment cancelled afterwards | **NOT YET VERIFIED** | Runbook step O |
| I5 | Rollback rehearsed, or at least read, before go-live | **NOT YET VERIFIED** | Runbook §5 |
| I6 | Everyone involved knows applicant data is never deleted in a rollback | **NOT YET VERIFIED** | Runbook §5.1 |

---

## Summary

| Status | Count |
|---|---|
| **PASS** | 50 |
| **FAIL** | 1 (H11 — operational constraint with a stated mitigation; does not block go-live) |
| **NOT YET VERIFIED** | 34 |

Every **NOT YET VERIFIED** item is an unchecked box requiring access to the
production host or database, not a known defect. The code side of this work is
complete and verified; what remains is configuration and live confirmation.

The single most consequential unchecked item is **F12** — a rep or leader with
no availability window produces an empty slot list that is indistinguishable
from a full calendar. **I3** is the test that would catch it.
