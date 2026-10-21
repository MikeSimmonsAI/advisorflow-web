# Sales Workspace redesign — what was built (Lane F, #281)

Approved design: Mike's package **"Simplified Sales Workflow – My Day (New Design)"**
(six desktop screens + mobile, received 2026-10-21 01:21 CT). Colours, sizes and
layout are taken from that package's prototype; a handful of greys and the
green/blue button colours were darkened slightly so text meets the 4.5:1
contrast standard.

## Screens and routes

| Screen | Route | Built from (existing, unchanged) |
|---|---|---|
| My Day | `/sales` | `GET /sales/my-day`, `GET /sales/opportunities` |
| Follow Ups | `/sales/follow-ups` (new) | `GET /sales/opportunities`, `/sales/my-day` recent activity |
| Lead Details | `/sales/leads/:oppId` (new) | `GET/PATCH /sales/opportunities/{id}`, `POST …/notes`, `GET …/implementation` |
| Book a Demo | `/sales/book` (new) | `GET /sales/meeting-types`, `POST /sales/availability/find`, `POST /sales/appointments` |
| Pipeline | `/sales/pipeline` | `GET /sales/opportunities?include_lost=true` |
| Appointments | `/sales/appointments` (new) | `GET /sales/calendar/view?scope=mine`, existing Reschedule and Outcome dialogs |
| Reports | `/sales/my-results` (new) | same data as My Day |

Nothing was removed. The original every-stage board moved to
`/sales/pipeline/stages`; the full deal record (`/sales/opportunities/:id`) is one
click from Lead Details; Availability, Demo Suite, Sold / Onboarding, My
Compensation and Team Availability sit under **More** in the sidebar. The
manager's **MY TEAM** group is unchanged.

## The simple pipeline

| Column | Real stages |
|---|---|
| New Leads | prospect |
| Follow Up | contacted, discovery |
| Demo & Closing | demo_build, demo_proposal, closing |
| Won | won, onboarding, live |

Lost and any unknown stage are listed under the board. Every card shows its
real stage; the stage picker on Lead Details offers the real stages grouped by
column. Won and Lost ask for one confirming click.

## Calls, texts and "What happened?"

- **Call** hands the number to the rep's phone (`tel:`) and opens *What happened?*:
  Connected, No Answer, Left Voicemail, Interested, Not Interested, Book a Meeting,
  an optional note, and next action + date (suggested from the choice).
- Saving writes one timeline entry (`event_type: call_outcome`) and, when something
  changed, one deal update (next action, due date; Prospect → Contacted after a real
  conversation). *Book a Meeting* then opens Book a Demo for that contact.
- **Text** opens the phone's messages app (`sms:`) with a starting message. It is
  only added to history when the rep clicks *I sent it*.
- No messaging provider is called from these screens.

## Won → onboarding

Marking Won shows the existing onboarding status and links to the existing
**Sold / Onboarding** area. No customer organisation is created here — that stays
the existing, deliberate provisioning step (owner/manager).

## Whose numbers (managers)

- **My Day cards, Follow Ups, Reports** are always the viewer's own work
  (`GET /sales/my-day?scope=mine`). For a manager they are labelled
  **Your numbers**.
- A manager also gets a separate, dashed **Your team** row (unscoped
  `GET /sales/my-day`): follow ups due, deals needing action, demos to build,
  appointments today, won this month, with a link to Team Command.
- The Follow Ups badge in the sidebar is counted from the same list the Follow
  Ups page shows, so the two always match.
- A rep's view is unchanged: `scope=mine` never widens anything, and a rep gets
  identical numbers with or without it.

## Completed (Follow Ups)

`GET /sales/my-day` returns `worked_today` — every deal in the caller's view they
called or texted (`call_outcome` / `text_logged`) since the team's midnight,
counted in SQL over the whole day. It no longer depends on the 15-row recent
activity feed. Covered by `tests/test_sales_my_day_scope.py` (20 deals worked
→ 20 counted while the feed still shows 15).

## Backend additions (read-only, no schema change)

`app/routers/sales_router.py`, `/sales/my-day` only:
- optional `scope=auto|mine` (default `auto` = previous behaviour)
- new response keys `scope`, `is_manager`, `worked_today`
- `_visible_sales_appointments(..., personal=False)` — can only narrow

## Final refinements (2026-10-21, master prompt 11:20 CT)

**Pipeline** — one **+ Add Lead** (header); the per-column buttons are gone. Every
card shows its exact stage (Prospect, Contacted / Qualified, Discovery, Demo Build,
Demo / Proposal, Closing, Won, Onboarding, Live), so a Closing deal under *Demo &
Closing* is never read as "a demo is booked". The footnote names which stages
each column groups. **Column renamed "Demo Scheduled" → "Demo & Closing"** — approved by Mike
2026-10-21 12:26 CDT (label only; stages, data and permissions unchanged).

**Follow Ups** — filters **Due Now | Overdue | Today | Completed**, each with its
count; heading **People to Contact Now**, "Overdue first, then today's follow-ups."
- Due Now = open deals due today or earlier = Overdue + Today (never overlapping).
- Overdue / Today use the server's own rule (`workspace_time.is_overdue` /
  `is_due_by_today`) in the **sales team's time zone** — same for the list, the
  sidebar badge, My Day and Reports.
- Completed = server `worked_today` (whole day, any status — includes a lead closed
  as Lost after today's call).
- Counts ignore the search box; the badge refreshes after any save.

**Call / Text** — Call hands the number to the device dialer (`tel:`) and opens
*What happened?*; the click records nothing. The panel shows the number with
**Copy number** for a desktop with no dialer. Text opens the device messages app
(`sms:`); opening it records nothing; only **I sent it — add to history** writes
"Text sent (confirmed by rep)". Delivery is never claimed (EvoSys does not send
these). Call/Text appear only when the lead has a usable number.

**Call outcomes**
| Choice | What it suggests (editable) | What is saved |
|---|---|---|
| Connected | Follow up, +2 days | note + next step; Prospect → Contacted |
| No Answer | Try calling again, +1 day | note + retry date; stage unchanged |
| Left Voicemail | Call back, +2 days (optional) | note (+ follow-up if kept) |
| Interested | Book a demo, +1 day; **Save & book a demo** | note + next step; Prospect → Contacted |
| Not Interested | nothing — rep must choose | **Close as Lost**: stage lost, loss reason, next action and date cleared. **Follow up later**: rep-chosen action + date required; stage kept |
| Book a Meeting | Book the meeting, today | "agreed to a meeting (not booked yet)"; opens the booking form. The scheduler alone logs a booking |

**Booking** — one booking per click (client guard; the server's clash check is
the second line). The confirmation reports the invitation's real state from the
booking response (handed to the mail service / not sent yet / no email on the lead).

**Dialogs** — focus moves inside on open, Tab stays inside, Escape closes, focus
returns to the button that opened it.

## Do Not Contact (sales prospects)

**Why a sales table.** The existing list, `SuppressionEntry` (`app/models/models.py`,
enforced in `compliance_service`), is keyed to a customer organization and covers
phone only; sales prospects belong to a brand sales org and are reached by phone and
email. So the record is `sales_contact_suppressions`
(`app/models/sales_suppression_models.py`), using the **same phone normaliser**
(`compliance_service.usable_us_phone`) so a number means the same thing on both
sides. Coordinated on #239 before the shared files (`app/models/registry.py`, the
invitation/reminder/confirmation/proposal senders) were touched.

**What a row means.** Within one brand sales org, do not contact this phone and/or
this email. Matching is by contact detail, so every duplicate record of the same
person (other phone formatting, other capitalisation of the email) is covered,
including duplicates created later. Rows are never deleted.

**Recording.** A rep picks Not Interested → *Asked not to be contacted* on the
call result (or the link on Lead Details). The call note is saved, the deal's
follow-up is cleared, and `POST /sales/opportunities/{id}/do-not-contact` writes
the row with who recorded it and why. It is distinct from Lost: the deal keeps its
stage; it simply can no longer be contacted.

**Enforced on the server** (`app/services/sales_suppression.py`):
- scheduling a next action or due date → 409 (clearing is allowed)
- logging a call result or a sent text → 409
- booking a meeting → 409
- prospect invitations, 24-hour reminders, demo confirmations and proposal emails
  → nothing is sent (recorded as skipped, never raised to the caller)
- My Day / Follow Ups never count a suppressed deal as due; a new duplicate gets
  no follow-up.

**Correction.** Only a sales manager of that brand (or the owner) can lift it,
with a required reason: `POST /sales/do-not-contact/{id}/lift`. The row keeps
recorded-by, lifted-by, both times and both reasons, and every matching deal gets
a timeline entry. Lifting never schedules anything; a rep must set a new
follow-up by hand. Nothing lifts a request automatically. Another brand's ids
return 404.

**Tests.** `tests/test_sales_do_not_contact.py` (12): recording, distinct from
Lost, nothing-to-suppress, other rep refused, every enforcement path, every email
sender sends nothing, duplicates by phone format and email case, new duplicate,
other brand unaffected and unprobeable, manager-only lift with history kept.
End-to-end DNC-0…DNC-14 cover the same through the screens.

## Known limits

- Booking still emails the prospect the usual invitation, exactly as before.
- Do Not Contact is sales-scoped: it does not add the number to a customer
  workspace's own suppression list (different owner, different list), and the
  customer list does not feed it.
- Calls and texts go through the rep's own phone; the app removes Call/Text and
  refuses to log them, but cannot stop someone dialling the number by hand.
- A lead with no usable phone or email cannot be suppressed (nothing to match);
  the server says so rather than saving an empty record.
