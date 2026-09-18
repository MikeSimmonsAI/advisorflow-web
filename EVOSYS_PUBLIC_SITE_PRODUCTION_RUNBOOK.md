# EvoSys Pro public website — production runbook

Prepared: 2026-09-18
Repository tip at preparation: `96f885a`
Website integration commit: `cec8975`

This is a **deployment preparation** document. Nothing in it has been deployed,
pushed, or enabled. Every outbound email gate remains OFF. No real email, SMS,
voice call, calendar event or Zoom meeting was created while preparing it.

Everything below is either **proven from code and from synthetic verification**,
or explicitly marked **UNVERIFIED**. Where a thing could not be proven from the
repository or from configuration, it says so rather than guessing.

---

## 1. Careers storage outside the public web root

### 1.1 The mechanism already exists and is the only path in use

Every careers storage path in the site derives from one function:

```php
// public-site/private/careers.php
function careers_base_dir(): string {
    $configured = careers_cfg('CAREERS_STORAGE_DIR', '');
    if ($configured !== '') return rtrim($configured, '/\\');
    return dirname(__DIR__) . '/storage/careers';
}
```

`careers_cfg()` delegates to `evosys_cfg()` when it is loaded, which reads an
environment variable first and `private/config.php` second. Both careers entry
points (`careers/index.php` and `careers/admin/index.php`) `require`
`private/form-utils.php` **before** `private/careers.php`, so `evosys_cfg()` is
always defined by the time careers configuration is read. `CAREERS_STORAGE_DIR`
can therefore be set either as an environment variable or as a key in
`private/config.php`, and both work.

Everything careers writes lives under that one directory:

| Path | Contents |
|---|---|
| `<CAREERS_STORAGE_DIR>/careers.json` | published roles, page copy, income scenarios |
| `<CAREERS_STORAGE_DIR>/admin-auth.json` | the Careers Manager password hash after first change |
| `<CAREERS_STORAGE_DIR>/login-attempts.json` | login throttle state |
| `<CAREERS_STORAGE_DIR>/applications/APP-YYYYMMDD-XXXXXXXX.json` | one applicant record per file |
| `<CAREERS_STORAGE_DIR>/resumes/` | uploaded resume files |

**There are no hard-coded absolute server paths anywhere in the site.** The only
default is relative (`dirname(__DIR__).'/storage/careers'`), and setting
`CAREERS_STORAGE_DIR` replaces it entirely. No code change is required to move
applicant storage above the document root.

### 1.2 Recommended value

```
CAREERS_STORAGE_DIR = /home/<account>/evosys-private/careers
```

The requirement is only that the directory is **not under `public_html`** (or
whatever the host's document root is), is writable by the PHP process, and is
not inside the uploaded site tree. A sibling of `public_html` is the usual
shape on shared cPanel-style hosting:

```
/home/<account>/
    public_html/              ← the uploaded site
    evosys-private/careers/   ← CAREERS_STORAGE_DIR
```

The exact account path is host-specific and is **UNVERIFIED** here — it must be
read off the live host during step C and written into `private/config.php` at
that time. Do not copy a path out of this document.

Permissions: the directory must be writable by the web server user. `0750` on
the directory with the PHP user as owner is sufficient; the code creates
`applications/` and `resumes/` itself with `0755` if they are absent.

### 1.3 Why this is the control that matters

Applicant storage inside the document root is protected today by three
independent things: a `Require all denied` `.htaccess` in `storage/`, a second
`<FilesMatch "\.(json|pdf|doc|docx)$">` deny block in the site root `.htaccess`,
and a `robots.txt` disallow. All three depend on the web server honouring
`.htaccess` (`AllowOverride`). Moving the directory above the document root
removes that dependency: no web-server rule has to be correct for applicant
names, phone numbers and resumes to stay private.

### 1.4 Verified synthetically

With `CAREERS_STORAGE_DIR` pointed at a directory outside the served tree:

- a submitted application was written to
  `…/applications/APP-20260918-0DE7CB44.json` in the external directory;
- the in-tree `storage/` directory stayed empty apart from its `.htaccess` and
  `index.html` placeholder;
- HTTP probes for `/private-careers-storage/…`, `/../private-careers-storage/…`
  and `/..%2F…` all returned the site's normal index page, not the file —
  confirmed by grepping the responses for the synthetic applicant address
  `external@example.invalid`, which appeared in none of them;
- the Careers Manager read the applicant back from the external directory;
- `login-attempts.json` was also created outside the tree.

Synthetic identities only. No real applicant data was involved.

---

## 2. Production configuration inventory

Configuration lives in four places. Nothing in this section has been applied.

### 2.1 Website — `public-site/private/config.php`

Created by copying `private/config.example.php`. It is deliberately git-ignored;
it holds secrets.

| Key | Required | Value / meaning |
|---|---|---|
| `PUBLIC_BOOKING_BASE_URL` | **yes** | `https://<api-host>/public-booking/evosyspro` |
| `DEMO_WEBHOOK_URL` | **yes** | fallback intake endpoint, used when online scheduling is unavailable |
| `CAREERS_STORAGE_DIR` | **strongly recommended** | absolute path above the document root — see §1 |
| `CAREERS_ADMIN_PASSWORD_HASH` | **first run** | output of PHP `password_hash()`; after the first sign-in the password is changed from Settings and the hash moves into `admin-auth.json` |
| `NOTIFY_EMAIL` | no | website-side contact notifications only; not on the booking path |
| `OPENAI_API_KEY` | no | the "Ask EvoSys Pro" widget only |
| `OPENAI_MODEL` | no | model for that widget |
| `SMS_OPTIN_WEBHOOK_URL` | no | optional |
| `SUPPORT_WEBHOOK_URL` | no | optional |
| `DEMO_AVAILABILITY_URL` | no | overrides the availability endpoint; otherwise derived from `DEMO_WEBHOOK_URL` |
| `DEMO_BOOK_URL` | no | overrides the book endpoint; otherwise derived from `DEMO_WEBHOOK_URL` |

The site calls exactly four platform endpoints, all **server-side from PHP**, so
the browser never sees the API host and no CORS entry is needed:

```
GET  /public-booking/evosyspro/meeting
GET  /public-booking/evosyspro/slots
POST /public-booking/evosyspro/book
POST /site-intake/evosyspro/demo-request      (fallback)
```

**PHP 8.1 is the minimum.** The binding constraint is the `: never` return type
in `request-demo/index.php`. On PHP 8.0 or lower the page is a parse error, not
a degraded page.

### 2.2 Backend — process role

| Variable | Value | Why |
|---|---|---|
| `SERVICE_ROLE` | `backend` | `app/service_role.py` assigns `JobName.SALES_REMINDERS` to `ROLE_BACKEND`. A process with an unrecognised role starts **no** scheduler loops, so the appointment reminder loop would silently never run. |

The reminder loop runs in-process on a 15-minute interval with a 150-second
startup delay, recorded in the durable job ledger via `record_job_run`. It is
not a separate Render cron service.

### 2.3 Backend — outbound email gates

All three are **fail-closed and OFF by default**. Truthy spellings are
`1`, `true`, `yes`, `on`; anything else, including absence, is off.

| Variable | Governs |
|---|---|
| `OUTBOUND_EMAIL_PUBLIC_BOOKING_CONFIRMATION` | the branded confirmation email to the prospect |
| `OUTBOUND_EMAIL_PUBLIC_BOOKING_INTERNAL` | the internal notification to the assigned salesperson and the booked leadership — **and the emailed `.ics` invitation fallback**, because that goes to the same people and differs only by carrying an attachment |
| `OUTBOUND_EMAIL_PUBLIC_BOOKING_REMINDERS` | the 24-hour and 1-hour appointment reminders |

`OUTBOUND_EMAIL_STAFF_ESCALATION` is a **separate, pre-existing** switch and is
no longer what governs any public booking path. It still governs the in-product
confirmation resend, which is unchanged.

With every flag off the booking still completes, is saved, blocks calendars and
is fully recorded. The response reports `confirmation_email.status` as
`delivery_disabled`, and the website renders that status rather than claiming an
email was sent.

**These flags stay OFF for this deployment.** Turning them on is a separate,
explicitly approved change — step P.

### 2.4 Platform data configuration (database, not environment)

Public booking refuses rather than guesses at every one of these. All are
**UNVERIFIED** against the production database; they must be confirmed on the
live system during steps J–M.

| # | What must exist | Consequence if missing |
|---|---|---|
| 1 | A `Platform` whose slug is `evosyspro` | `404 Unknown site.` |
| 2 | Exactly one **active** `BrandSalesOrg` for that platform | `503` generic unavailable |
| 3 | `BrandSalesOrg.timezone` set to the brand's operating zone | falls back to the platform default |
| 4 | `BrandSalesOrg.inbound_assignment_mode` unset or `default_owner` | any other value is refused, not guessed |
| 5 | `BrandSalesOrg.default_inbound_owner_user_id` set | booking without a personal link fails closed with `no_inbound_owner_configured` |
| 6 | At least one `MeetingType` with `is_active` **and** `public_bookable` true | `503`; `public_bookable` defaults FALSE on every row by design |
| 7 | That meeting type's `duration_minutes`, `requires_video` | drives slot length and whether the confirmation waits for a join link |
| 8 | `leadership_policy` = `reporting_chain` if leadership is wanted | NULL policy means legacy behaviour, no quorum |
| 9 | `leadership_minimum`, `leadership_depth`, `owner_required`, `include_additional_leaders` | govern who is booked alongside the rep |
| 10 | `Membership.reports_to_user_id` forming an unbroken chain for every bookable rep | chain statuses `owner_not_a_member`, `no_leadership`, `broken_link`, `cycle` each produce the single generic public refusal |
| 11 | An availability profile with `accepts_bookings` true **and at least one `AvailabilityWindow`** for the rep and for each leader who can satisfy quorum | **no working hours means no slots at all** — `free_intervals_for_user` returns `[]` immediately |
| 12 | A booking code issued per salesperson who will use a personal link | a supplied-but-bad code is a 404 refusal and never falls through to the default owner |

Item 11 is the one most likely to be missed: it is not an error state, it simply
produces an empty slot list, which looks the same as "fully booked".

### 2.5 What is deliberately NOT configured

There is no master outbound switch, no "any manager in the brand" leadership
policy, and no round-robin inbound assignment in this build. Each absence is
intentional and documented in the code.

---

## 3. Calendar connection readiness

**This section is the result of a read-only inspection. No calendar was
connected, tested, written to, or disconnected. No real calendar event was
created.**

### 3.1 How a calendar becomes connected

Two OAuth flows exist, both requiring an authenticated user:

| Provider | Connect | Callback |
|---|---|---|
| Google | `GET /calendar/connect` | `GET /calendar/oauth/callback` |
| Microsoft | `GET /microsoft/connect` | `GET /microsoft/oauth/callback` |

Both are `Depends(get_current_user)`, so **any authenticated user can connect a
calendar**, including a brand-sales salesperson. Connection state is inspected
and managed through:

```
GET  /calendar/connections
POST /calendar/connections/{provider}/test
POST /calendar/connections/{provider}/disconnect
```

Whether the salesperson-facing UI exposes these routes on a screen a rep can
reach is **UNVERIFIED** — it was not traced through the frontend in this pass.

### 3.2 Which provider a booking uses

`app/services/calendar_providers/resolve_provider_key()`, in order:

1. an explicitly requested provider, if the caller named one;
2. a **configured** provider — `users.calendar_provider`, else
   `organizations.calendar_provider` — obeyed whether or not it currently works;
3. a live `CalendarConnection` row with `calendar_scope_ok` true, Microsoft
   preferred;
4. a stored refresh token on the user, Microsoft preferred;
5. the `.ics` email fallback.

**SCHED-05 fail-closed.** If a user has **two** live external connections and no
`calendar_provider` chosen, the resolver returns `.ics` rather than picking by
preference order. That is deliberate — a booking written to a calendar the
person does not use is worse than one that refuses — but it means such a user
gets no external calendar read or write until a provider is chosen.

**Gap worth knowing before go-live:** there is an API to set
`organizations.calendar_provider` (`PATCH /org-settings/calendar-provider`,
admin-only, tenant organization). There is **no endpoint anywhere in the
codebase that sets `users.calendar_provider`.** A brand-sales salesperson is
resolved with `org=None`, so for a rep connected to both Google and Microsoft
there is currently **no supported way to resolve SCHED-05** other than
disconnecting one provider. This is not a defect in the booking engine and it
does not block deployment; it is a real operational constraint, and the
mitigation is simply: **each salesperson connects exactly one calendar.**

### 3.3 What is written to a salesperson's calendar

`app/services/appointment_sync.py`, after the booking has committed, never
inside the transaction:

- **one event per participant on that participant's own calendar**, under that
  participant's own OAuth grant. Partial success is an accepted outcome — Blake
  on Microsoft, Michael on Google and a third rep on nothing at all is a fully
  successful booking;
- **no attendee list on the provider event**, so neither Graph nor Google mails
  anybody a second, competing invitation;
- **`appointment.notes` is never in the event body**, the `.ics` DESCRIPTION, or
  any invitation. Internal notes and prospect-facing text are built by two
  separate functions so no future edit can leak one into the other;
- idempotent on `external_event_id` — a retry moves the event, it does not
  create a second one.

### 3.4 What is read from a salesperson's calendar, and what is stored

Privacy here is **structural, not a policy setting**:

- Microsoft `get_busy()` requests `$select=id,start,end,isAllDay,showAs` — no
  subject, no attendees, no body — and skips anything marked `free` or
  `workingElsewhere`;
- Google `get_busy()` uses a field-limited `events().list` with
  `singleEvents=True`;
- the cache model `ExternalBusyBlock` has columns `id`, `user_id`, `provider`,
  `starts_at`, `ends_at` and **no title column**;
- `CalendarConnection` stores state only — `is_connected`, `account_email`,
  `calendar_id`, `calendar_scope_ok`, `last_error` (message only, never a
  token).

A dead grant is reported as unreadable rather than as an empty calendar. The
cache is deliberately left in place on a provider error, because stale busy time
is closer to the truth than none.

### 3.5 Freshness: what the public slot list is computed from

This matters operationally and is easy to misread:

- `free_intervals_for_user` subtracts external busy time **from the cache only**.
  It never calls Microsoft or Google.
- The **public slots endpoint does not refresh the cache.** A cache refresh
  happens on the internal availability screen, on a connection test, and at
  **book** time.
- At book time `public_booking.book()` calls `refresh_many(..., force=True)`
  over a window around the chosen slot, bypassing the TTL, and refuses the
  booking with `SLOT_GONE` if an external conflict has appeared.

So the authoritative external check is at commit, not at browse. A visitor can
be offered a slot that is then refused — that is the correct direction to fail.
On a cold, public-only deployment where no one opens the internal availability
screen, published slot lists may not reflect external calendars until somebody
attempts a booking. If that matters operationally, the fix is a scheduled
refresh, which **does not exist today** and is out of scope for this deployment.

A failure to read a calendar during booking is logged and does not refuse the
booking — a vendor outage must not hold the brand's inbound pipeline hostage.

### 3.6 The shared EvoSys calendar at `info.evosyspro@gmail.com`

**Finding: no shared or company calendar concept exists anywhere in the
platform.** A repository-wide search across `app/` and `frontend/src/` for
`info.evosyspro`, `shared_calendar`, `company_calendar` and `SHARED_CALENDAR`
returns nothing.

What this means concretely:

- availability is computed **per person**, from that person's own availability
  profile, their own AdvisorFlow appointments, and their own connected external
  calendar. There is no code path that reads a brand-level or company-level
  calendar when deciding whether a slot is open;
- booking writes **one event per participant to that participant's own
  calendar**. There is no code path that writes a copy to a shared mailbox;
- therefore `info.evosyspro@gmail.com` **plays no role in availability or
  booking today**, and it cannot replace individual salesperson availability
  calendars — there is no mechanism by which it could.

If that address is wanted as a visible company-wide view of booked meetings, the
supported ways to get there **without** touching the availability architecture
are:

1. connect it as a normal user account that is a participant on the meetings it
   should see — it then gets its own event copies through the ordinary
   per-participant path; or
2. share each salesperson's own calendar to it at the provider level (Google or
   Microsoft sharing), entirely outside this platform.

Either is a configuration decision, not a code change. What must **not** happen
is pointing several salespeople at one shared calendar connection: availability
would then be computed from the shared calendar's busy time rather than each
rep's own, and every rep would appear busy whenever any of them was.

Whether such a calendar currently exists, who owns it, and what is on it is
**UNVERIFIED** — it is outside the repository and was not inspected.

---

## 4. Deployment order

Steps A–P. Nothing here has been executed.

**A. Freeze and record the exact tree.**
Confirm `git status` shows no unexpected content changes. Note: ~1,170 files in
this working tree differ from `HEAD` by **line endings only**; exactly three
files carry real content changes and they are Mike's unrelated work-in-progress
(`app/auto_migrate.py`, `app/models/master_contact_models.py`,
`app/routers/god_master_router.py`). Verify with
`git diff --ignore-cr-at-eol --name-only` before doing anything. Do **not**
globally normalise line endings.

**B. Confirm what is being deployed.**
The website integration is commit `cec8975`. Record the tip being deployed and
the backend commit the API host will run — the site and the API it calls change
together and must be deployed from the same tree.

**C. Create the careers storage directory above the document root.**
On the web host, create the directory chosen in §1.2 outside `public_html`,
owned by and writable by the PHP user. Do not create it inside the site tree.

**D. Write `private/config.php`.**
Copy `private/config.example.php` and set at minimum
`PUBLIC_BOOKING_BASE_URL`, `DEMO_WEBHOOK_URL` and `CAREERS_STORAGE_DIR`.
Confirm the file is not world-readable and is not in the repository.

**E. Set the Careers Manager password hash.**
Generate with PHP `password_hash()` on the host — never in a chat window, a
ticket, or this document — and set `CAREERS_ADMIN_PASSWORD_HASH`. After the
first sign-in, change the password from Settings; the working hash then lives in
`admin-auth.json` in the external storage directory and the config key becomes a
bootstrap value only.

**F. Upload the site.**
Deploy the contents of `public-site/` to the document root. Confirm the
`.htaccess` files landed — they are dotfiles and are silently skipped by some
copy tools and FTP clients. There must be one in the site root, one in
`storage/`, and one in `private/`.

**G. Prove applicant storage is unreachable over HTTP.**
Before any real applicant exists, submit one synthetic application, confirm the
JSON landed in the external directory and not in the site tree, then request the
storage path and a traversal variant of it over HTTP and confirm neither returns
the file. Repeat for a resume file.

**H. Confirm the PHP version.**
Must be **8.1 or newer**. Load `/request-demo/` and confirm it renders — on 8.0
it is a parse error, not a degraded page.

**I. Backend: confirm the safety posture.**
`SERVICE_ROLE=backend`. Confirm all three public-booking outbound flags are
absent or explicitly off. Confirm cadence and automatic outreach are off.

**J. Backend: platform and brand sales org.**
Confirm the `evosyspro` platform slug resolves, that exactly one active
`BrandSalesOrg` is attached to it, and that its timezone is correct.

**K. Backend: meeting type.**
Confirm at least one meeting type is `is_active` **and** `public_bookable`, with
the intended duration and `requires_video` value. Confirm nothing internal was
accidentally marked public.

**L. Backend: reporting chain, quorum policy and availability.**
Confirm `reports_to_user_id` forms an unbroken chain for every bookable rep;
confirm the meeting type's `leadership_policy`, `leadership_minimum` and
`leadership_depth`; and confirm every rep and every leader who can satisfy
quorum has `accepts_bookings` true **and at least one availability window**.

**M. Booking codes.**
Issue a booking code per salesperson who will use a personal link. Record which
code belongs to whom. Confirm `default_inbound_owner_user_id` is set for
code-less traffic.

**N. Calendar connections.**
Each salesperson connects **exactly one** calendar — see §3.2 on SCHED-05.
Confirm each connection reports connected with calendar scope OK. Do not connect
a shared mailbox as a rep's calendar.

**O. Synthetic end-to-end booking, gates still OFF.**
With a synthetic prospect only (Dana Prospect / Example Company /
`prospect@example.invalid`): load the public page, fetch slots, book one, and
confirm the appointment exists, the participants are the expected rep and
leader(s), the calendar events appear on the right individual calendars, the
response reports `confirmation_email.status: delivery_disabled`, and the website
renders that status without claiming an email was sent. Then cancel the
synthetic appointment.

**P. Enable outbound email — separate change, separate approval.**
Only after O passes. One flag at a time, confirming delivery and content before
the next: `OUTBOUND_EMAIL_PUBLIC_BOOKING_INTERNAL` first (staff only), then
`OUTBOUND_EMAIL_PUBLIC_BOOKING_CONFIRMATION` (reaches prospects), then
`OUTBOUND_EMAIL_PUBLIC_BOOKING_REMINDERS`. **This step is out of scope for this
deployment and must not be performed as part of it.**

---

## 5. Rollback plan

### 5.1 The rule that governs everything below

**Applicant data is never deleted during a rollback.** Career applications are
real people's submissions, they exist only in `CAREERS_STORAGE_DIR`, and they
are not recoverable from the repository, from a database backup, or from
anywhere else. Every step below is written so that a rollback cannot touch them.

This is the main reason to complete step C before go-live: once
`CAREERS_STORAGE_DIR` is outside the document root, **rolling back the website
is just replacing the document root**, and applicant data is not in the blast
radius at all.

### 5.2 Website rollback

1. Re-upload the previous site tree, or restore the host's snapshot of the
   document root.
2. Leave `CAREERS_STORAGE_DIR` **pointing where it points**. Do not revert it,
   do not move the directory, do not delete it.
3. Leave `private/config.php` in place unless the rollback target needs
   different values; it is not in the repository and re-uploading the site does
   not restore it.
4. Confirm the storage directory still contains `careers.json`,
   `admin-auth.json`, `applications/` and `resumes/` after the rollback.

If the previous site version defaulted to in-tree `storage/careers` and
applications were written there before the move, **copy them out, do not move
them, and do not delete the originals** until the copy is verified.

### 5.3 Backend rollback

The booking engine is additive. Rolling the backend back to before `cec8975`:

- the three public-booking endpoints return 404; the website's fallback
  `POST /site-intake/evosyspro/demo-request` continues to accept demo requests,
  so the site keeps working in degraded form;
- appointments already booked remain rows in the database and remain on the
  calendars they were written to. Nothing is deleted;
- the reminder loop stops running. Scheduled reminders are not sent; they are
  also not lost, because they are claimed rather than consumed.

### 5.4 Schema

Alembic is not in use; schema comes from `Base.metadata.create_all()` plus
`app/auto_migrate.py`. The booking work **added columns and tables only** — no
column was dropped, renamed or retyped. A backend rollback therefore leaves
extra columns present and unused, which is harmless.

**Do not attempt to reverse `auto_migrate` additions as part of a rollback.**
Dropping a column to "clean up" is how applicant and appointment data is lost.

### 5.5 Outbound gates during a rollback

If any outbound flag has been turned on and something is wrong with the email
itself, the correct first action is to **turn that one flag off**. It is
instant, it is reversible, it requires no deploy, and it stops delivery without
stopping booking. A rollback is not the right tool for an email problem.

### 5.6 What cannot be rolled back

- Emails already delivered.
- Calendar events already written to external calendars. They can be cancelled
  through the platform's cancellation path, which updates the provider events;
  they cannot be un-created.
- Applicant submissions, which is why §5.1 exists.

---

## 6. Provenance

| Claim | How it was established |
|---|---|
| Careers storage honours `CAREERS_STORAGE_DIR` with no hard-coded paths | read of `private/careers.php`; all 12 path call sites derive from `careers_base_dir()` |
| Storage outside the docroot works end to end | synthetic application submitted, file located outside the tree, three HTTP probe shapes returned no leak |
| PHP 8.1 minimum | `: never` return type in `request-demo/index.php` |
| Gate names and fail-closed defaults | read of `app/services/outbound_email_gate.py` `_ENV_BY_SOURCE` |
| Reminder loop ownership | `app/service_role.py` `SCHEDULER_OWNER[SALES_REMINDERS] = ROLE_BACKEND` |
| Calendar privacy | read of `microsoft.py` `$select`, `google.py` field list, `ExternalBusyBlock` columns |
| No shared-calendar concept | repository-wide search returning no matches |
| No `users.calendar_provider` setter | repository-wide search; only `org_settings_router` sets the organization value |
| Slot list reads the cache; book time forces a refresh | `availability.free_intervals_for_user` comment and `public_booking.book()` `refresh_many(force=True)` |
| Working tree is line-endings-only apart from three WIP files | `git diff --ignore-cr-at-eol --numstat` |

Items marked **UNVERIFIED** in this document were not provable from code or
configuration available here and must be confirmed on the live host or the
production database.
