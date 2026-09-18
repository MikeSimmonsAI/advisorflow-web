# PLATFORM-LEVEL INBOUND DISCOVERY / DEMO BOOKING

**Repo:** `advisorflow-web` · **Branch:** `main`
**Starting HEAD:** `eaf87b5` (end of the production-hardening pass)
**Ending HEAD:** `a0cce4f` (this report; amending it changed its own SHA)
**Date:** 2026-09-17

> **REAL CUSTOMER EMAILS SENT: NO**
> **REAL CUSTOMER SMS SENT: NO**
> **REAL VOICE OUTREACH: NO**
> **REAL CUSTOMER CALENDAR EVENTS CREATED: NO**
> **REAL CUSTOMER ZOOM MEETINGS CREATED: NO**
> **CADENCE ENABLED: NO**
> **AUTOMATIC OUTREACH ENABLED: NO**
> **PUSHED: NO**
> **DEPLOYED: NO**

The nine production-hardening commits are intact and untouched. Mike's
in-progress work — `auto_migrate.py`, `master_contact_models.py`,
`god_master_router.py` — remains uncommitted in the working tree; §26 records
one mistake there and how it was corrected.

---

## 1–3. COMMITS

| SHA | What |
|---|---|
| `2c398b4` | The reporting-chain resolver, the quorum policy, booking codes, the schema |
| `6676c56` | The ten hierarchy cases and five quorum cases — two of which changed the code |
| `68616ac` | The booking transaction, the public router, reminders |
| `0079b92` | The public endpoints from the outside, and backwards-compatibility proof |
| `be0edc2` | Back out master-contacts indexes swept in by mistake (see §26) |
| `a0cce4f` | This report |

---

## 4. ARCHITECTURE CHOSEN

Four new services and one new router. **No new CRM, lead, calendar, appointment,
email, notification or meeting system** — every one of those is reached through
the module that already owns it.

```
public website
      │
      ▼
public_booking_router      ← unauthenticated; resolves brand from URL + Origin
      │
      ├── sales_booking_codes    ← opaque code → salesperson, or the brand's
      │                            configured inbound owner, or a refusal
      ├── leadership_chain       ← Membership.reports_to_user_id, one brand only
      ├── leadership_quorum      ← owner + ≥N free leaders from THAT chain
      │        └── availability  ← EXISTING: hours, PTO, buffers, notice,
      │                            horizon, external busy, DST
      └── public_booking         ← the transaction
               ├── public_capture           EXISTING — website lead + dedupe
               ├── Opportunity / DiscoveryRecord / OpportunityEvent   EXISTING
               ├── appointment_meetings     EXISTING — Zoom
               ├── appointment_sync         EXISTING — calendars
               ├── demo_confirmation        EXISTING — branded confirmation
               └── sales_appointment_reminders   NEW — 24h / 1h lifecycle
```

**Why a quorum and not the existing intersection.** `find_shared_slots`
intersects every required participant: a slot survives only if all of them are
free. On a three-person chain that loses a slot whenever any one of the three is
busy — most of the week on real calendars — and the prospect, who only ever
needed one decision-maker in the room, is shown an empty page.

**Why the policy is five columns and not a rules language.** It answers exactly
five questions — must the owner attend, where do leaders come from, how many are
needed, how far up to look, do extra available leaders get invited — and nothing
else. A general expression language here would be a system nobody can reason
about in exchange for flexibility nobody has asked for.

---

## 5. FILES CHANGED

| File | Lines | What |
|---|---|---|
| `app/services/leadership_chain.py` | +314 | **new** — the reporting-chain resolver |
| `app/services/leadership_quorum.py` | +322 | **new** — quorum slot selection |
| `app/services/sales_booking_codes.py` | +220 | **new** — opaque codes, inbound owner |
| `app/services/public_booking.py` | +1110 | **new** — the transaction |
| `app/services/sales_appointment_reminders.py` | +366 | **new** — reminder lifecycle |
| `app/routers/public_booking_router.py` | +446 | **new** — the three public endpoints |
| `app/models/scheduling_models.py` | +195 | policy columns, booking source, reminder table |
| `app/models/sales_models.py` | +69 | booking code, inbound owner |
| `app/auto_migrate.py` | +86 | columns and indexes, in a fenced dated block |
| `app/services/meeting_roles.py` | +70/−2 | Discovery + Demo config, guarded backfill |
| `app/main.py` | +56 | reminder loop, router mount |
| `app/routers/sales_scheduling_router.py` | +13 | cancel suppresses reminders |
| `app/models/job_models.py` | +6 | `SALES_REMINDERS` job name |
| `app/service_role.py` | +5 | one owner for that loop |
| 5 test files | +2391 | 141 tests |

**No frontend source changed.** No build check was run, and none was needed.

---

## 6. SCHEMA / CONFIG ADDITIONS

All additive. Every default is chosen so the deploy that lands them changes no
behaviour.

**`sales_meeting_types`** — `leadership_policy` (NULL = predates the feature and
behaves exactly as before, which is every row in every brand), `owner_required`
(TRUE), `leadership_minimum` (0), `leadership_depth` (0),
`include_additional_leaders` (FALSE), `public_bookable` (FALSE).

`public_bookable` defaulting FALSE is a **security control**, not a
convenience: without it the public endpoint's meeting-type parameter would let
anyone on the internet book "Internal Sales Meeting" onto a management team's
calendar.

**`memberships`** — `booking_code`, `booking_code_issued_at`,
`booking_code_revoked_at`. All NULL, so nobody has a link until one is issued.

**`brand_sales_orgs`** — `default_inbound_owner_user_id`,
`inbound_assignment_mode`. NULL means refuse, not fall back.

**`sales_appointments`** — `booking_source`, `booking_idempotency_key`.

**`sales_appointment_reminders`** — new table, `UNIQUE(appointment_id, kind,
target_starts_at)`.

**Two unique indexes that are correctness guarantees, not performance:**
`uq_memberships_booking_code` and
`uq_sales_appt_idempotency (brand_sales_org_id, booking_idempotency_key)`. A
check-then-insert has a gap that two simultaneous submissions both fit through;
the index is what makes the second INSERT fail so the handler can return the
first one's result.

---

## 7. REPORTING-CHAIN RESOLVER

`leadership_chain.resolve(db, owner_user_id, brand_sales_org_id, depth)` walks
`Membership.reports_to_user_id` **inside one brand sales org only**, nearest
first. That ordering is load-bearing: when a policy books fewer leaders than are
free, it books the nearest, because the person who manages this rep is a more
appropriate attendee than their manager's manager.

**Five statuses, because they need five different fixes:**

| Status | Means |
|---|---|
| `ok` | resolved |
| `owner_not_a_member` | no active seat in this brand |
| `no_leadership_configured` | nobody was ever named above them |
| `broken_link` | somebody was named and is no longer a live seat |
| `cycle_detected` | self-reference or a loop |

"No times available" and "this rep has no manager configured" look identical to a
visitor and are completely different problems — one is a busy week, the other
loses every inbound lead until somebody notices.

**The one deliberate divergence from `compensation.upline()`.** The two agree
about who is above whom — asserted directly on a healthy chart, so they cannot
drift — but diverge at a **deactivated seat**:

- compensation **stops**. Paying an override to somebody who has left is money
  the business does not owe, and walking past them would pay their manager twice.
- a meeting **continues** up the same line. Stopping would take a rep's booking
  link offline the moment their manager left — precisely when inbound leads must
  not be dropped.

A seat walked *through* is still recorded in `broken`, so the chart reads as
needing repair even though bookings kept working. Both directions are tested.

**Never `users.role`.** Holding a manager role somewhere in the brand does not
make somebody leadership for every rep in it.

---

## 8. LEADERSHIP-QUORUM BEHAVIOUR

Current Discovery + Demo policy: `owner_required=true`, `leadership_minimum=1`,
`leadership_depth=2`, `include_additional_leaders=true`.

| Case | Owner | Direct | Skip-level | Result |
|---|---|---|---|---|
| 1 | free | free | busy | **bookable** — owner + direct |
| 2 | free | busy | free | **bookable** — owner + skip-level |
| 3 | free | free | free | **bookable** — all three |
| 3b | free | free | free | with extras off: owner + direct (nearest) |
| 4 | free | busy | busy | **not offered** |
| 5 | busy | free | free | **not offered** |

**There is no fallback to "any available manager in the brand", and adding one
would be a decision to reintroduce the defect.** Case 4 is tested with an entire
second reporting line sitting fully staffed and completely free in the same
brand; the answer is still nothing.

The quorum computes **no availability of its own** — working hours, lunch, PTO,
buffers, minimum notice, booking horizon, existing meetings, external
Outlook/Google busy and DST are all reached through exactly one call per person
into `availability.free_intervals_for_user`. Every "busy" in the tests is a real
blocking appointment or a real time-off block, so the tests would start failing
the moment this module grew availability logic.

---

## 9. SALESPERSON BOOKING CODE

`secrets.token_urlsafe(24)` — the same CSPRNG choice `AppointmentConfirmationToken`
makes. **On the membership, not the user**: one human can sell for two brands,
and a link resolving to "this person" rather than "this person selling THIS
brand" would let a prospect book a rep into the wrong brand's pipeline. Scoping
it to the membership makes that structurally impossible rather than a check
somebody has to remember to write.

**Four separate refusals**, because they are four different facts:
`unknown_code`, `code_revoked`, `member_inactive`, `wrong_brand`. Revocation is
separate from membership: a link posted somewhere it should not have been dies
without removing anyone from the team, and somebody who leaves stops taking
bookings whether or not anyone remembers to revoke their link.

**One identical public message for all four.** "That code was revoked" versus
"no such code" tells an outsider which codes exist.

**Rotation keeps history.** `issue_code(..., rotate=True)` replaces the value;
appointments and opportunities record the *owner*, never the code, so nothing
downstream is touched and the old link simply stops resolving.

---

## 10. GENERIC WEBSITE TRAFFIC (NO CODE)

`resolve_inbound_owner` in fixed order:

1. **A code that resolves wins.** Somebody who followed a specific person's link
   is booking with that person.
2. **A supplied-but-invalid code is a refusal**, never a reason to fall through
   to the default — silently routing a typo'd link to somebody else is worse
   than refusing.
3. **No code → `BrandSalesOrg.default_inbound_owner_user_id`.**
4. **Nothing configured → fail closed.** `no_inbound_owner_configured`, and
   nothing is booked.

**There is no branch that picks a user.** Not the first manager, not the least
busy rep, not the brand's creator. An arbitrary assignment puts a stranger's
meeting on a real person's calendar and a real prospect into a pipeline nobody is
watching, and it does it silently.

`inbound_assignment_mode` exists so round-robin is later a new value plus one
branch in one resolver, rather than a rewrite of the public flow. Building the
engine now, for a platform with no inbound volume to distribute, would be
inventing requirements.

---

## 11. OPPORTUNITY CREATION AND DEDUPE

Created at stage `prospect`, status `open`, owner = the resolved salesperson,
source = `"<Brand> Website — Discovery + Demo"` built from the platform's own
name rather than one brand's wording compiled into core code.

Preserved: name, company, email, phone, industry, **timezone only when the
browser actually stated it** (the model carries an explicit note that a
hardcoded `America/Chicago` was a real defect here once).

**Dedupe is email-first and email-only when it matches.** Two people at one
company booking two calls is two conversations; the same person booking twice is
one. Matching on company alone would merge the first case, which is how a rep
loses a deal to a colleague's record. Company is a fallback only when no email
was supplied, and is scoped to open deals. Phone is deliberately not used — a
shared switchboard number would merge unrelated people.

**An existing deal is updated, never duplicated and never reassigned.** A
booking through a different rep's link is recorded on the timeline but is not a
transfer; letting it be one would make a public URL a way to take a colleague's
deal. Stages never move backwards.

**The primary challenge lands in `DiscoveryRecord`, not a new model** — its
columns are literally "Bottlenecks / challenges" and "Current systems / tools",
and that is where the demo builder and the proposal already read from. Appended,
never replaced, so a rep who filled discovery in properly does not lose it
because the prospect re-booked.

The website lead still goes through `public_capture`, which owns dedupe, consent
evidence and the note format for every public form on every brand site. Best
effort: a failure to file a marketing lead must not cost the brand a booked
meeting.

---

## 12. AVAILABILITY

Real, and computed by the existing engine. Nothing about free/busy is
reimplemented — see §8.

Public payloads carry **only** the times: UTC ISO, plus a rendering in the
meeting's zone and in the visitor's zone when known. Sending only a local string
would make the page's correctness depend on the browser agreeing with the server
about the offset, which is exactly what breaks across a DST boundary.

**Withheld from public responses:** user ids, names other than the assigned
salesperson, which leaders were free, how many were free, calendar event titles,
and any indication of why other times are missing. A visitor able to diff two
reps' free time is a visitor mapping the sales team's week.

Windows are clamped to 60 days — an unbounded range is a free way to make the
server compute a year of availability for four people.

---

## 13. THE BOOKING TRANSACTION

Side effects are ordered so the ones that can refuse happen first and the ones
that cannot be undone happen last:

1. **Retry guard** — look up the idempotency key before any side effect.
2. **Re-resolve and re-check the slot** against the same engine, now.
3. **`find_conflicts`** — our own appointments, at row level.
4. **Forced external-busy refresh** bypassing the 10-minute cache TTL.
5. Website lead, opportunity, discovery record.
6. Appointment + participants + timeline event → **COMMIT**.
7. Video, calendars, confirmation, notification, reminders.

**Nothing after the commit can un-book the meeting.** A Zoom outage produces a
booking flagged for attention, never a lost booking and never a 500 to the person
who just booked — the same decision the internal path already made.

**Failing to read an external calendar is not a refusal.** A Microsoft outage
must not stop a brand taking inbound meetings; it degrades to the state every
booking was in before external busy existed. Refusing would hand a vendor a veto
over the brand's inbound pipeline.

**`created_by` is NULL and the timeline event has no actor.** A website visitor
is not a user, and naming one of our own people would be a forged audit actor —
the convention this codebase applies everywhere else.

---

## 14. ZOOM / VIDEO

`appointment_meetings.ensure_meeting` — the existing architecture, not a second
integration. Idempotent, brand-scoped, keeps the host URL away from anything
prospect-facing, and writes the attendee link onto the appointment where calendar
sync and the confirmation both already read it.

**No meeting was created during this build.** No provider is configured in the
test environment, which is the production-safe state; the flow records
`not_configured` and carries on.

---

## 15. CALENDAR SYNC — AND A REAL DEFECT FOUND

Per-participant, through `appointment_sync`, using each person's own connected
provider.

**The finding.** When a participant has no Microsoft or Google calendar
connected, the registry falls back to `calendar_providers/ics.py`, which delivers
the invitation **as an email with an .ics attachment** — and that path does not
pass the platform's outbound gate.

For a rep clicking "book" inside the product that is correct: they asked for it
and they are watching. For an **inbound** booking nobody asked and nobody is
watching — a stranger on a website could cause mail to be sent, at any hour,
through an ungated path.

The public flow now holds that fallback to
`OUTBOUND_EMAIL_PUBLIC_BOOKING_INTERNAL` — the .ics goes to the assigned
salesperson and the booked leadership, the same people the internal
notification goes to, so it is one notification to one set of people differing
only in whether it carries an attachment. Anyone with a real provider connected
is still synced regardless (a Graph API call is not an email and is not what the
gate governs).
**The internal path is deliberately unchanged** — that is existing behaviour and
changing it was not asked for. It is flagged in §24.

Unavailable leaders are never put on the calendar: participants are the quorum's
selection for that specific slot, decided from the same free/busy data that made
the slot bookable.

---

## 16. INTERNAL NOTIFICATION

**Who:** the assigned salesperson and the leadership participants the quorum
actually booked — the people whose calendars now carry the meeting. Deliberately
**not** the whole resolved chain: a leader who was busy and is not attending does
not need a notification about a meeting they are not in, and sending one teaches
everybody to filter these out.

**In-app is the opportunity timeline** (`OpportunityEvent`), which already
exists, is already what the Sales Workspace reads, and is already append-only.
The customer-tenant `Notification` table is deliberately **not** used: it is keyed
to a Lead in a customer organization, and reaching across the brand-sales /
customer-tenant boundary is the one thing the sales models are emphatic about not
doing.

**Email is prepared and gated** on `OUTBOUND_EMAIL_PUBLIC_BOOKING_INTERNAL`,
which is separate from the customer confirmation's switch — a brand can let its
sales team start seeing bookings without emailing a single prospect. It carries
prospect name, company, date/time, timezone, assigned salesperson, primary
challenge, meeting type and the **join** link — never the host link.

**Notifying the full chain when a leader is not attending** is a real question
and is left as a follow-up (§24) rather than guessed at: it needs a configuration
surface and a decision about default noise, and hardcoding "notify everyone" is
how these become ignored.

---

## 17. CUSTOMER CONFIRMATION

Reuses `demo_confirmation`, which already enforces the rule that the graphic
must not contain a fake Zoom button, a JOIN DEMO button, a meeting URL or any
fake clickable control. The CTA is real HTML outside the image, built from the
actual meeting URL.

**It waits for the artifacts it would describe.** A Discovery + Demo is a video
call; a confirmation saying "you're booked" while carrying no join link is worse
than none — the prospect has nothing to click at the appointed hour and no reason
to believe a second email will be better. The public response returns
`pending_meeting_link`, and the page can honestly say the booking is confirmed
and details are coming.

**Delivery is disabled and reported as itself**, not as a failure — this path
runs `OUTBOUND_EMAIL_PUBLIC_BOOKING_CONFIRMATION`, which is off by default and
shared with nothing. An operations view must never confuse "we chose not to
send" with "sending broke".

---

## 18. REMINDERS

Confirmation at booking, then **24 hours** and **1 hour** before.

**A table, not two booleans.** `BookingLink` uses booleans and that shape cannot
say whether a reminder was skipped or has not fired, cannot record why one
failed, and cannot survive a reschedule. More importantly a boolean is written
*after* sending, so two overlapping runs both read False and both send.
`UNIQUE(appointment_id, kind, target_starts_at)` makes **the claim the atomic
act**.

| Rule | Behaviour |
|---|---|
| Booked <24h out | 24-hour reminder written as **skipped**, not left absent — a gap is indistinguishable from a job that never ran |
| Booked just over 24h out | 24-hour reminder **suppressed** — it would land on the heels of the confirmation, saying the same thing to somebody who just read it |
| Cancelled | all pending reminders suppressed, at cancel time *and* re-checked at send time |
| Rescheduled | old rows settled, new rows claimed against the new time |
| Missed its window | **skipped**, not sent late — "your meeting is tomorrow" arriving two hours before is not a reminder |
| Job run twice | second run sends nothing |

**Correction made during the build:** the stacking rule was originally written
as a gap between the two reminders. They are 23 hours apart by construction and
can never crowd each other; what they *can* crowd is the confirmation, so the
guard is measured from the booking.

**No second scheduler.** `process_due` is a plain function registered in the
existing loop registry with one named owner in `service_role`, so it cannot run
in two processes. `appointment_reminder_cron` operates on `BookingLink`, is on
the customer-tenant side of the boundary, and is an orphan nothing runs — it was
not extended.

---

## 19. PUBLIC API CONTRACT

Base: `https://<api-host>/public-booking/{platform_slug}`

Brand resolution is server-side from the path slug plus the `Origin` header.
**The browser never names an organization.** All three endpoints share the
existing public-intake rate ceiling.

### 19.1 `GET /{platform_slug}/meeting`

Query: `code` (optional), `meeting_type` (optional), `timezone` (optional, IANA).

```json
{
  "brand":   { "name": "Acme Suite", "slug": "acme" },
  "meeting": { "key": "discovery_demo", "name": "Discovery + Demo",
               "description": "…", "duration_minutes": 60,
               "is_video": true, "timezone": "America/Chicago" },
  "salesperson": { "name": "Assigned Rep" },
  "assigned_via": "link",
  "bookable": true,
  "visitor_timezone": "America/New_York",
  "types": [ { "key": "discovery_demo", "name": "Discovery + Demo",
               "duration_minutes": 60 } ],
  "form": {
    "required": ["full_name","company","email","phone","industry",
                 "primary_challenge"],
    "primary_challenge_options": [ { "value": "lead_followup",
                                     "label": "Following up with leads consistently" } ],
    "optional_context_fields": ["current_system","locations","lead_volume","notes"],
    "package_of_interest": false
  }
}
```

`assigned_via` is `"link"` or `"general"`. When `bookable` is `false` a
`message` field is present and the page should offer "contact us" rather than an
empty calendar. **Render the challenge options from this response** — that is
what keeps the wording on the form and the values that arrive back identical by
construction.

`404` unknown site · `503` brand not configured · `404` bad/revoked code.

### 19.2 `GET /{platform_slug}/slots`

Query: `code`, `meeting_type`, `timezone`, `from` (YYYY-MM-DD), `to`.
Window defaults to 21 days and is clamped to 60.

```json
{
  "brand": {...}, "meeting": {...}, "salesperson": {...},
  "assigned_via": "link",
  "visitor_timezone": "America/New_York",
  "window": { "from": "2026-09-18", "to": "2026-10-09" },
  "slots": [
    { "start_utc": "2026-09-21T15:00:00Z",
      "end_utc":   "2026-09-21T16:00:00Z",
      "meeting_timezone": "America/Chicago",
      "start_meeting_local": "2026-09-21T10:00:00",
      "visitor_timezone": "America/New_York",
      "start_visitor_local": "2026-09-21T11:00:00" }
  ]
}
```

Empty `slots` comes with a `message`. **Send `start_utc` back verbatim when
booking.** A malformed `timezone` or date is ignored rather than erroring.

### 19.3 `POST /{platform_slug}/book`

```json
{
  "code": "pQ7mHk2xR9tLvN4wYc3bZa",
  "meeting_type": "discovery_demo",
  "start_utc": "2026-09-21T15:00:00Z",

  "full_name": "Dana Prospect",
  "company": "Prospect Co",
  "email": "dana@prospect.example",
  "phone": "+1 214 555 0199",
  "industry": "Home services",
  "primary_challenge": "lead_followup",
  "primary_challenge_detail": "optional free text",

  "current_system": "Spreadsheets",
  "locations": "3",
  "lead_volume": "400",
  "notes": "optional",

  "timezone": "America/New_York",
  "submission_id": "a-stable-uuid-per-form-instance",
  "page_url": "https://acme.example/request-demo",
  "referrer": "https://google.com/"
}
```

**`submission_id` is the retry guard.** Generate it once when the form is
rendered and send the same value on every resubmission of that form; generate a
new one for a genuinely new booking. It is what makes a lost response safe.

`201` on success:

```json
{
  "status": "booked",
  "already_booked": false,
  "reference": "b6f1…",
  "start_utc": "2026-09-21T15:00:00Z",
  "end_utc": "2026-09-21T16:00:00Z",
  "meeting_timezone": "America/Chicago",
  "start_meeting_local": "2026-09-21T10:00:00",
  "visitor_timezone": "America/New_York",
  "start_visitor_local": "2026-09-21T11:00:00",
  "duration_minutes": 60,
  "join_url": null,
  "confirmation_email": { "sent": false, "status": "pending_meeting_link" }
}
```

A replay returns `201` with `"status": "already_booked"`,
`"already_booked": true` and the same `reference`.

**Render from `confirmation_email.status`, not from an assumption:**

| status | Say |
|---|---|
| `sent` | "Check your inbox for the details." |
| `pending_meeting_link` | "You're booked — your meeting link is on its way." |
| `delivery_disabled` | same as above |
| `no_recipient` / `failed` | "You're booked. We'll be in touch to confirm." |

Promising an email that is not coming is how a real booking looks broken.

**Errors:** `409` slot gone (message is safe to show verbatim — ask them to pick
another time) · `404` unknown site or bad code · `503` brand not configured ·
`422` invalid payload.

### 19.4 What the website must NOT send

There is no field — not validated-and-rejected, **absent** — for
`organization_id`, `brand_sales_org_id`, `owner_user_id`, `salesperson_user_id`,
`user_id`, `participant_user_ids`, `required_user_ids`, `meeting_url`,
`appointment_id`, `opportunity_id`, `created_by`, `booking_source`, `status` or
`platform_id`. Extra keys are ignored. A test asserts against the request model
that none has been added.

**No cPanel/PHP file was touched.**

---

## 20. SECURITY AND ISOLATION

| Attack | Result |
|---|---|
| Invalid / revoked code | identical `404`, identical message |
| Inactive rep with a live code | refused |
| Code replayed against another brand | refused — the query does not find it |
| Spoofed user / org / brand / participant / meeting URL / appointment id | **ignored**; booking is byte-for-byte what it would have been |
| Internal meeting type requested by key | `503` — `public_bookable` is FALSE |
| HTML / script in any text field | stripped before storage |
| Header injection in the email field | `422`, nothing written |
| Malformed timezone | dropped, no 500 |
| 900-day window | clamped to 60 |
| Repeated submission | original returned |
| Simultaneous submission | unique index decides; loser returns the winner's result |
| Rate abuse | existing public-intake limiter |

Public responses were asserted to contain **no** internal user id, brand sales
org id, staff email address, leader name, or calendar event title.

---

## 21–23. TESTS AND RESULTS

**141 new tests across five files**, all passing:

| File | Tests | Covers |
|---|---|---|
| `test_leadership_chain.py` | 22 | the ten required hierarchy cases |
| `test_leadership_quorum.py` | 20 | the five required quorum cases + availability rules |
| `test_public_booking.py` | 53 | codes, transaction, idempotency, confirmation, reminders |
| `test_public_booking_api.py` | 24 | the endpoints and the spoofing matrix |
| `test_scheduling_backwards_compatibility.py` | 9 | existing scheduling is unchanged |

**Full regression on the final tree, all 237 test files:**

```
4,933 passed
   14 skipped
    0 failed
    0 errors
```

Baseline before this work was 4,742 passed / 14 skipped / 0 failed. The delta is
+191 (141 new plus 50 from parametrisation). **The 14 skips are the same two
pre-existing environment gaps** as last pass — 8 need a customer CSV that is not
in the repo, 6 need `TEST_POSTGRES_URL`. No failure occurred, so no failure
needed reproducing against pre-change HEAD.

**Two bugs were found by tests and fixed:**

1. **`limit=0` meant "one slot", not "unlimited".** `slots_from_intervals`
   returns after its first slot when given 0, because its guard is
   `len(out) >= limit`. The quorum engine forwarded the caller's 0 straight
   through, and the booking path calls it uncapped to re-check one specific
   time — so **every booking except the earliest offered slot of the day would
   have been refused as unavailable.** Found by a test that books a second slot
   instead of the first.

2. **The .ics calendar fallback sends ungated email** (§15).

---

## 24. KNOWN LIMITATIONS

1. **The .ics fallback is ungated on the internal path.** Fixed for public
   bookings only; changing the internal path was not asked for and is a
   behaviour change for a working feature. Worth a decision.
2. **Notifying the full chain when a leader is not attending** is not built.
   Needs a configuration surface and a default-noise decision.
3. **Round-robin inbound assignment** is not built — the seam exists
   (`inbound_assignment_mode`), the engine does not.
4. **Reschedule from the public side** is not exposed. The backend handles a
   reschedule correctly (reminders re-target, `ensure_meeting` updates the same
   provider meeting); there is no public endpoint for it.
5. **One brand sales org per platform** is assumed. A second one resolves to the
   oldest rather than guessing.
6. **`product_specialist` still resolves brand-wide** in `meeting_roles` — it is
   used by the internal screen only, and was out of scope.
7. **Availability is computed per request.** At current volume that is correct;
   at real inbound volume the slots endpoint is the first thing worth caching.
8. **No public cancellation link.** The confirmation token exists and the
   confirm/decline flow works; cancel-by-prospect was not in scope.
9. **The three gates are deployment-wide, not per-brand.** They are environment
   variables, so a second brand on the same deployment inherits whatever the
   first one's operator set. At one live brand that is not yet a problem; with
   two it becomes one, and the fix is a per-brand column read alongside the
   variable, the way `outbound_email_sources` already works for customer
   outreach.

---

## 25. CONFIGURATION REQUIRED BEFORE PRODUCTION

Nothing below is done, and none of it is code.

1. **Platform intake org** — `platforms.public_intake_organization_id` must be
   set, or the endpoints return 404. (Already required by the existing site
   forms.)
2. **A reporting chain.** Every rep who will take inbound bookings needs
   `reports_to_user_id` set on their brand-sales membership. Without it their
   link resolves but offers no times, and the API reports `bookable: false`.
3. **A default inbound owner** — `brand_sales_orgs.default_inbound_owner_user_id`
   — or the main "Book a demo" button fails closed with 503. This is a business
   decision about who takes unattributed leads.
4. **Booking codes issued** to each rep who wants a personal link:
   `sales_booking_codes.issue_code(db, membership)`. There is no UI for this yet.
5. **A Zoom provider configured** for the brand, or every booking returns
   `join_url: null` and the confirmation waits indefinitely.
6. **Calendar connections** for participants, or invitations fall back to email
   — which this build holds behind the gate.
7. **The outbound gates.** Three separate variables, each off by default, each
   governing one path and nothing else:

   | Variable | Releases |
   |---|---|
   | `OUTBOUND_EMAIL_PUBLIC_BOOKING_CONFIRMATION` | the customer's booking confirmation |
   | `OUTBOUND_EMAIL_PUBLIC_BOOKING_INTERNAL` | the internal notification, and the emailed .ics invitation to those same people |
   | `OUTBOUND_EMAIL_PUBLIC_BOOKING_REMINDERS` | the 24-hour and 1-hour reminders |

   Accepted truthy spellings: `1`, `true`, `yes`, `on`. Unset is off.
   **Leaving all three off is the current, deliberate state.**

   `OUTBOUND_EMAIL_STAFF_ESCALATION` does **not** control any of them. It keeps
   its own unrelated feature, and it still governs a salesperson pressing
   "resend confirmation" from inside the product — a human doing one thing on
   purpose, which is a different act from the public flow sending one
   automatically to a stranger.
8. **Verify the meeting-type backfill** landed on Discovery + Demo for the live
   brand and on nothing else.

---

## 26. MIGRATION AND DEPLOYMENT ORDER

1. **Schema first, on its own.** `auto_migrate` runs at startup via
   `preDeployCommand`. All additions are `ADD COLUMN IF NOT EXISTS` / `CREATE
   INDEX IF NOT EXISTS`; the reminder table comes from `create_all()`. Every
   default leaves behaviour unchanged, so this step is safe with no
   configuration in place.
2. **Confirm the meeting-type backfill.** It touches only rows whose
   `leadership_policy` is NULL **and** that have never been edited by hand
   (`updated_at == created_at`) — the same guard the existing `requires_video`
   backfill uses. A brand that has customised its Discovery + Demo keeps its
   settings; `required_slots` is never modified; no other meeting type is
   touched.
3. **Configure** per §25 — chain, default owner, codes, provider. Nothing is
   bookable until this is done, which is the intended order.
4. **Verify with the API** before pointing the website at it: `meeting` should
   report `bookable: true`, `slots` should return times.
5. **Then the public website.**
6. **The gates last**, deliberately, and one at a time — each is a separate
   decision. A sensible order is INTERNAL first (the sales team starts seeing
   bookings, no customer is emailed), then CONFIRMATION, then REMINDERS.

**One mistake, corrected.** Commit `2c398b4` staged the whole of
`auto_migrate.py` rather than only its own block, and carried in 8 index lines
belonging to Mike's in-progress master-contacts work. Commit `be0edc2` backs them
out of the branch and leaves them exactly where they were — uncommitted in the
working tree — so that WIP is untouched and still his to commit. The fenced dated
block was supposed to make this impossible and would have, if the staging had
matched it.

---

## 27. ROLLBACK

**Before configuration** (§25 not done): nothing can be booked, so nothing needs
rolling back. The code is inert.

**After configuration, to stop inbound bookings immediately:** set
`public_bookable = false` on the brand's Discovery + Demo. One row, instant,
reversible, and it leaves every existing appointment intact.

**To disable a single rep's link:** `revoke_code`. Their seat and their
appointments are unaffected.

**To revert the code:** `git revert` the four feature commits in reverse order. The
columns can stay — they are additive and inert with `leadership_policy` NULL.
`auto_migrate` never drops anything.

**Appointments already booked survive any of the above.** They are ordinary
`SalesAppointment` rows and the internal product manages them normally.

**The one thing to check before reverting:** pending rows in
`sales_appointment_reminders`. Reverting the code leaves the table without a
process to drain it. Either set them to `suppressed` or accept that those
reminders will not be sent.

---

> **REAL CUSTOMER EMAILS SENT: NO**
> **REAL CUSTOMER SMS SENT: NO**
> **REAL VOICE OUTREACH: NO**
> **REAL CUSTOMER CALENDAR EVENTS CREATED: NO**
> **REAL CUSTOMER ZOOM MEETINGS CREATED: NO**
> **CADENCE ENABLED: NO**
> **AUTOMATIC OUTREACH ENABLED: NO**
> **PUSHED: NO**
> **DEPLOYED: NO**
