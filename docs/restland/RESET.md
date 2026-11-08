# Restland data clean-up and Batch 1 preparation (Issue #507)

Code: `app/services/programs/workspace_reset.py`. Tables: `app/models/reset_models.py`. Screen: Restland → Program tools → **Clean-up & Batch 1**.

## Production audit (read-only, 2026-11-06 15:37–15:45 CST)

Categories overlap; they are not added together.

- **Families:** 10,576 lead records, 1 of them a test record.
- **Phone format:** 10,493 phones are stored as 11 digits with no `+` (`12145551234`). That's why a strict `+1…` filter matched nobody. Read correctly, they're valid US numbers.
  - The stored value is never rewritten. The normalized value lives in `lead_dispositions.phone_e164`.
- **Other phone values:** 75 missing · 6 three-digit · 1 nine-digit · 1 already `+1…`.
- **Duplicates:** 1,440 flagged.
  - 1,435 are `dnc` with no duplicate reason. 1,336 of those are identical to their canonical record (same name, phone and email) and came from the same import file.
  - **Their `dnc` comes from duplicate handling, not from the family.** It is kept on the duplicate row; the canonical family is the contact.
  - The duplicate rows carry history: 4 texts, 15 emails, 20 unused booking links. So they're linked, not merged. Every row and its history stays.
- **Real suppression:** 5 phone-level entries (`suppression_entries`), plus 28 non-duplicate `dnc` families. Either one blocks every family on that number.
- **`needs_tier_review`:** 7,372. Sources:
  - `Restland_Dallas.csv`: 4,408
  - Google Contacts export part 4: 2,599
  - `All Active Leads (2012).xlsx`: 365
  - None of them carries a source field that classifies it, so they stay in the "Needs classification" queue.
- **Automation that could still fire:**
  - Old cadence: 96 `active` states at touch 5, due 2026-11-06. The runner selects `status == "active"`.
  - Old cadence: 409 `ACTIVE` states at touch 0. Never selected, because the runner compares case-sensitively.
  - Old AI pipeline: 26 conversations with `next_send_at` (2026-09-12 / 09-21). They're held only by the global AI background switch.
  - Default cadence template: "Funeral Home 9-Touch". Its schedule would have driven any new program sends.
- **Queues:** none. No queued, pending, scheduled or retrying rows in any table with a `lead_id` and a status.
- **Campaigns:** one legacy campaign ("Marker Discount", draft) and one program family (`existing_family_follow_up`, off).
- **Program:** AI setter off · approval mode on · daily batch 0 · staff alerts off · `booking_user_id` not set · workspace texts switch not set (off).
- **Sender:** `organizations.org_twilio_phone_number` = `+18553601164` (toll_free).
  - No Restland user has its own number, so `_resolve_twilio_creds` resolves the org's shared number.
  - The old `+14692241155` is not configured anywhere for Restland.
  - SCI's 844 number is untouched.
- **Inbound SMS** to 855 resolves to Restland through the org number. The signature is required (unsigned → 403). Program reply handling records it and nothing auto-replies while texts are off. STOP marks the family `dnc`. Tested in `tests/test_restland_channels.py`.
- **Voice** to 855 resolves to Restland and plays the generic planning-line voicemail greeting, then records.
  - No `phone_numbers` registry row exists, so there's no ring-through to staff.
  - Proven with signed test calls only, not a live call.

## What the apply run changes

Each change is one `workspace_reset_items` row holding the before value.

1. Every old cadence state with status `active` or `ACTIVE` → `paused`. Touch number, due date and history are kept.
2. Every old AI pipeline conversation → `paused`, `next_send_at` cleared. The old value is in the item.
3. "Funeral Home 9-Touch" → no longer the default. "Restland Family File Review (3 texts)", industry `program`, becomes the default schedule:
   - text 1: day 0, 10 AM
   - text 2: day 3, 11 AM
   - text 3: day 8, 2 PM
4. Campaign **"Restland — Family File Review — Batch 1"** (`restland_family_file_review`), **off**:
   - Every text and email step is drafted.
   - It's an own-copy campaign: it only ever speaks its own words, and only a person switching it on in Campaigns starts it.
5. One `lead_dispositions` row per family: retain / merged_duplicate / duplicate_review / manual_review / suppressed / email_only / test. Each row also records the review queue, normalized phone, phone/email/name issues, canonical id and shared-number count.
6. One `campaign_audience_members` row per family in the pre-need pool, with a segment and reasons. **25 are staged as Batch 1.**
   - Staging approves nothing, assigns no campaign to the program record, and starts no journey.

**Not changed:**
- leads' own values;
- `dnc` statuses and suppression entries;
- any history row;
- the Roofing rows (Roofing is off for Restland and the program never reads them);
- any other organization.

## Batch 1 eligibility

The pool is non-test, non-duplicate, status `new`, tier `pre_need`, with a valid US number. Families with `contract_sold`, `at_need` or `imminent` are never in the pool.

A family is excluded, with the reason recorded, when:
- its number is suppressed or do-not-contact (contact-wide);
- it has an upcoming appointment;
- a conversation is waiting on staff;
- it replied before (→ "Replied before" follow-up group);
- it has a past or cancelled appointment (→ "Past appointments");
- it's in the old cadence or AI pipeline (→ "In earlier sequence");
- it was texted or emailed before (→ "Contacted before");
- it has no program record, is in another campaign, or is on hold;
- its name is incomplete.

One family per number: the oldest ready record keeps the number, and the others are "Shared number (held)".

## Running it in production

The run happens in the backend's pre-deploy step (`app/migrate.py` §3f) and is switched by one environment variable:

- `WORKSPACE_RESET=01fed629-9a95-412b-895a-c8b09f37a98c:plan:restland-reset-1` — read-only; logs the counts.
- `WORKSPACE_RESET=01fed629-9a95-412b-895a-c8b09f37a98c:apply:restland-reset-1` — applies once. A later deploy with the same key does nothing.
- `WORKSPACE_RESET=01fed629-9a95-412b-895a-c8b09f37a98c:rollback:<run id>` — restores exactly what that run changed.
  - It refuses if the campaign was switched on or families were approved into it.

A failed run rolls back its whole transaction and never stops the deploy.

## Launch checklist (smallest remaining steps; nothing here is done by this work)

1. **Booking calendar.** Confirm which account is Mike's. The only Restland account with a connected calendar is `michael.simmons@nsmg.com`, but its display name is "Jerome Simmons" and its Google calendar last synced 2026-08-29. Then:
   - set the program's booking user;
   - reconnect the calendar if needed;
   - confirm the hours.
2. **Copy.** Mike approves or edits the 3 texts (and the 5 emails, if email will be used) in Campaigns.
3. **Audience.** Mike reviews the 25 staged families, then approves them in Choose & Send.
4. **Schedule and limits.** Confirm:
   - the daily limit (the daily batch stays 0 until set);
   - spacing between approvals;
   - sending hours (texts follow the cadence hours in Central time).
5. **Staff alerts.** Choose the recipients, then switch alerts on.
6. **Switch on, in this order:**
   1. the campaign, in Campaigns (type its name);
   2. the workspace text switch (owner only);
   3. the AI setter, if wanted.
7. **Voice (optional).** Decide whether calls to 855 should ring a person (needs a `phone_numbers` route) or keep going to voicemail.

---

# Booking, the 14-day campaign and the 30-day rest (Nov 7, owner decisions)

Code:
- `app/services/programs/booking_policy.py` (appointment rules)
- `restland_ffr.py` (sequence and copy)
- `rotation.py` (rounds and rest)
- `workspace_configure.py` (the recorded setup run)

## Booking

- **Account:** the existing account Mike confirmed, which is Restland-only. Its id is passed in from the backend setting `WORKSPACE_BOOKING_USER_ID`, never written into the code.
  - Its display name changes from "Jerome Simmons" to **Mike Simmons**.
  - Its email, password, access and calendar grant are untouched.
- **Rules:**
  - Every day.
  - Starts at **10:00, 11:30, 1:00, 2:30 and 4:00** Central. 4:00 is the last start and ends at 4:45.
  - **45-minute** appointments plus a **45-minute gap**. Each appointment holds start → start + 90 minutes.
  - Local wall time, so daylight saving moves nothing.
- **One engine.** The booking page, staff "pick a time", the appointment setter and the booking confirmation all use `booking_policy` through `compute_day_slots`. Each one checks:
  - existing appointments plus the gap (older visits hold their recorded length, 60 when unknown);
  - the connected calendar, which fails closed;
  - 2 hours' notice on the same day;
  - a 14-day horizon.
- **Office or home.** Both use the same times and the same calendar.
  - A home visit needs a full street, city, state and ZIP.
  - The place is stored on the booking (`meeting_type`, `visit_address`) and shown in:
    - the confirmation text;
    - the Google and Microsoft calendar event;
    - Appointments, for staff.
  - There's no mileage or service-area rule.
  - Program families get no reminder texts today (only the confirmation), so there's no reminder copy to change.
- **Reassignment.** Appointments has an "Assign to" control (`POST /program/command/appointments/{id}/assign`).
  - Only this workspace's team can be chosen.
  - It refuses if that person isn't free (their bookings plus the gap, and their calendar).
  - It keeps the time, place and history, writes an audit entry, and never messages the family.

## The sequence (5 texts, 4 emails, 14 days)

| Day | Touch |
|---|---|
| 1 | text 1 (10 AM), then email 1 at least 4 hours later |
| 3 | text 2 |
| 4 | email 2 |
| 6 | text 3 (Restland or home) |
| 8 | email 3 (the form and the guide) |
| 10 | text 4 (weekday or weekend) |
| 12 | email 4 |
| 14 | text 5 (close the loop) |

- Every touch goes out 9 AM–6 PM Central, every day (`send_window`).
- The emails have their own days (`campaigns.EMAIL_SCHEDULES`), measured from the family's start. A late email never pulls the next one in.
- No usable email: five texts only. Missing emails never become texts.
- **What stops the sequence:**
  - A real reply, on any channel. Automatic replies ("I'm driving", out of office) don't count, for the Restland campaigns only; every other campaign is unchanged.
  - A booking.
  - An opt-out.

## Rounds and the 30-day rest

- `campaign_cycle_members` keeps one row per family per round: staged, active, completed or stopped. Each row records the start, the end, the reason, the last real outreach and the next eligible date.
- **The rest counts from the last ATTEMPT** - any text or email that reached the provider, delivered or not, and any logged cadence or email-touch attempt (retries included) - plus 30 days.
  - A failed retry or a late "undelivered" receipt only changes the reason (`no_reply` vs `undeliverable`); it never makes the rest shorter.
  - A stored rest end only ever moves later. An attempt logged after a round closed pushes it later at every check (approval, dispatch, Return staging, the rotation screen).
- **Replied, booked, opted out or cancelled an appointment:** the family goes to a person or stays suppressed. They are never recycled.
- **Enforced per destination.** A phone or email that is resting, or in an open round, blocks every record that shares it.
  - The audience plan marks these families "in_round" or "resting".
  - Approval refuses them.
- **When the rest is over** the family can appear in a **reviewed** pool ("Rest over: review"). Never automatic enrollment.
- **Returning families** use their own drafted campaign, "Restland — Family File Review — Return" (OFF), whose copy says we wrote before.
- **Second rounds** are built (see "Return rounds" below). The earliest any family could return is about 44 days after launch.

## Running the setup in production

- `WORKSPACE_RESET=01fed629-9a95-412b-895a-c8b09f37a98c:configure:restland-config-1` runs once and is recorded.
- It refuses SCI and refuses while a Restland campaign is on.
- It also does one **read-only** calendar check and reports it in the run summary.
- **Rollback:** `...:rollback:<configure run id>` restores:
  - the booking user and display name;
  - the appointment rules;
  - the location address;
  - the 3-text template;
  - the earlier draft copy;
  - Batch 1 staging.

  It also removes the Return campaign and the round rows. It refuses once a campaign is on or a family is approved.

## Launch checklist (nothing here is done by this work)

1. Mike approves or edits the 5 texts and 4 emails, and the Return drafts.
2. Mike reviews the 25 staged families in Clean-up & Batch 1.
3. Decide the daily number of new families and the spacing. See the proposal in the #507 checkpoint.
4. Choose staff-alert recipients.
5. Reconnect the calendar if the setup run's check says it can't be read.
6. Optional: one live test to an internal number Mike names. No family is ever used.
7. Switch on, in order:
   1. the campaign, in Campaigns;
   2. the workspace texts switch (owner);
   3. approve the families in Choose & Send;
   4. alerts;
   5. the AI setter, only if wanted.

---

# Return rounds, appointment messages and the 24-hour survey (Nov 7, PR after #563)

Code:
- `rotation.py` (rounds)
- `appointment_notifications.py`
- `appointment_ops.py`
- `visit_survey.py` and `routers/visit_survey_router.py`
- The page `frontend/src/pages/public/VisitSurvey.jsx`

## Return rounds (second and later rounds)

- **Rest ends → reviewed pool only.** A manager picks families from "Rest over: review" and presses **Stage for Return** (`POST /program/command/data-review/return/stage`). Each family is re-checked before it's staged:
  - its earlier round finished unanswered;
  - the 30-day rest is fully over (measured from the real last outreach; an undeliverable last touch uses the last attempt);
  - no other open round or rest on its phone or email;
  - not do-not-contact.

  Families that replied, booked, opted out or cancelled are never staged. Staging moves the program record to the **Return** campaign. It is **not approved and not started**; the earlier round's start and approval are kept in the round's notes.
- **Approval** refuses anyone not staged for that round. It also refuses a returning family on the first-round campaign, and anyone whose phone or email is resting or in another open round.
- **Texts:** starting the journey restarts the family's text sequence from text 1 with a new start. The engine only counts attempts since that start, so round 1's attempts stay as history. Every attempt row carries its round (`cadence_touch_logs.cycle_key`).
- **Emails:** round *k* uses touch numbers `100*(k-1)+n` (round 2 = 101–104), so the existing (family, touch) uniqueness still guarantees one email per touch per round. Round 1 rows are never touched. Every row carries its round (`program_email_touches.cycle_key`). SCI's numbering is unchanged.
- **Re-checked again immediately before every text and email:** the phone or email must not have entered another round or a rest since approval.
- **Copy:** the Return drafts say "I wrote a while back", never first-contact wording.

## Appointment messages (confirmation, reminders, change and cancel notices)

- **One schedule per appointment.** Each message is one row in `appointment_notifications`, unique on (appointment, purpose, channel, appointment time). A double click, a webhook retry or two workers can't send twice.
- **Sent by text, and by email when the family has a usable, permitted address.** A booking never erases an opt-out: the channel is re-checked right before each send.
- **Confirmation:** immediate. It gives the title, the assigned advisor, the date, the Central time, 45 minutes, and the place. At Restland: "13005 Greenville Ave – ask for <the assigned advisor>". At home: "<advisor> will come to you at <address>". It ends "Need to change or cancel? Just reply to this message or call <office line>".
- **Reminders:** 24 hours and 2 hours before. They go only between 9:00 AM and 6:00 PM Central. A reminder that would fall outside that moves to the preceding permitted time (5:45 PM the evening before). If that moved time is before the booking was made, or after the visit, it's skipped.
  - **Collision rule (deterministic):** a reminder within 12 hours of another message for the same visit (the confirmation or an earlier reminder) is skipped, unless it is the last reminder going at its true time.
  - **10:00 AM visits get the 24-hour reminder only.** Their 2-hour reminder would be 5:45 PM the evening before, 7 h 45 m after the 24-hour one, so it is skipped with that reason. It is never described as "2 hours before".
  - A booking made 26 hours ahead: the 24-hour reminder would follow the confirmation by 2 hours, so it is skipped; the 2-hour reminder still goes.
  - 11:30, 1:00, 2:30 and 4:00 visits get both reminders at their true times.
  - The text says "today" or "tomorrow" from when it actually goes. A reminder whose time has already passed when the family books is **skipped**, never sent late. The 24-hour reminder is exactly 24 real hours before, across daylight saving.
- **Reschedule:** the old time's unsent messages are superseded, and one "updated" notice plus new reminders are written.
- **Cancel:** everything pending is cancelled, the calendar event is removed, and one cancellation notice per channel is written.
- **Reassign ("Assign to"):** the calendar event moves to the new person's calendar. The family is **not** messaged; their next message names the new advisor.
- **Missed while off:** messages that came due while sending was off are not dumped later. Confirmations more than 1 hour late, reminders more than 2 hours late and surveys more than 3 days late are skipped, with the reason recorded.
- **Retries:** a provider failure retries after 15 minutes, then after 60 minutes, then shows as failed. A refusal (opt-out, texts off) is recorded as skipped with its reason.
- **Staff can see** every message per appointment (Appointments → Messages): scheduled, sent, skipped or failed, why, and the delivery receipt.

## The 24-hour survey

- **Only for visits staff mark Completed** (Appointments → Completed / No-show). It goes 24 hours after the visit's recorded end, or its scheduled end, and never earlier. Outside 9–6 Central it waits for the next 9:00 AM. If attendance is marked late, it goes at the next permitted time.
- **Never for:** a no-show, a cancellation, a visit with attendance not recorded (those are listed under "Record attendance" as staff work), or appointments that predate activation (`notifications.active_since`).
- **One survey per visit.** The text and the email open the same survey, with an unguessable token in the link and nothing else.
- **Questions:**
  1. Overall experience, 1–5.
  2. Was the review helpful? Yes / Somewhat / No.
  3. Comments (optional).
  4. Follow-up wanted? (optional), with a preferred method: phone, text or email.
- **One answer.** A second submit is refused, and expired links say so.
- **A follow-up request** creates an open task on the task list, assigned to the visit's advisor, plus an in-app note. No staff text or email is sent while alerts are off.
- **No review links.** No message goes to the family because of an answer, and nothing restarts the campaign.

## Switches (all OFF after deploy and configuration)

- Appointments → **Appointment messages**: "Turn appointment messages on" (confirmations, reminders, notices) and "Turn survey on". Managers only; both are audited.
- **Order to switch on:**
  1. appointment messages;
  2. the workspace texts switch (owner) for texts;
  3. the survey.

  Saving settings never sends anything.

## Volume (upper bound, per day, steady state)

- **Formula:** N new families a day gives at most **5N texts** and **4N emails** a day, before exits.
  - At 15 a day: 75 texts and 60 emails.
- **Per family, not per day:** each family gets at most 5 texts and 4 emails **in total** over its 14 days. A steady day carries about 5N texts and 4N emails because 14 starting groups overlap.
- **Segments** are counted from the **saved** campaign copy, the real booking link and the **real first names** of the ready families, giving a per-family range (min-max), not one number.
- **Messages, segments, attempts, charges:** a message is one text; a segment is the billing unit (160 GSM-7 / 153 per part, or 70 UCS-2 / 67); every segment Twilio accepts is billed whether delivered or not, and a retry is billed again. Charge = segments x (Twilio per-segment rate + the carrier's toll-free fee) + the number lease. Read current rates from Twilio before quoting dollars (published pages disagreed when checked).
- **Three separate controls:** daily intake (new families started per day; **0 now**), outbound workload (all touches due that day), provider throughput (set by the toll-free verification, not the app).
- **Pace proposal (not set):** 10 new families a day, 8 minutes apart, for the first two weeks.
- **Separate from campaign volume:** appointment messages (up to 4 per booking × 2 channels), conversations and staff alerts.
- **Capacity:** 35 appointment starts a week for one person; reassignment adds another person's capacity.
- **Screen:** Clean-up & Batch 1 shows the table, what's scheduled in the next 7 days, and the daily new-family cap (still 0).


# Launch readiness (Nov 7 evening) - nothing launched

## One calendar for every booking action
- Availability, new events, moves, cancellations and reassignment all use one target: `calendar_service.booking_calendar_id(user)` (a connected Google connection row's calendar, else the user's stored id, else "primary"). Today that is "primary" for the booking user.
- The stored grant has `calendar.events` + `contacts` only, so `calendarList.get` is refused. The probe identifies the account from events the calendar already holds (organizer/creator), masked.
- Reconnect path (only if Mike decides to change accounts): the in-app Calendar connection screen. Reconnecting writes a new grant; nothing here does it.

## Read-only evidence probe
- `WORKSPACE_RESET=<org>:probe:<key>` with `WORKSPACE_BOOKING_USER_ID=<booking user>`; clear both after. It sends nothing and writes nothing.
- It logs: the sending number and its Twilio record, the full toll-free verification claims (category, use-case summary, sample message, opt-in type and evidence links, volume, edit/rejection fields) without contact names, phones, emails or street address, and the calendar identity (masked).
- The full output is kept privately (Drive), not in this repository.

## Activation order (Mike's decision at each step)
1. Designate internal test recipients and an internal test appointment; run the live-test checklist.
2. Approve the copy packet and the 25-family audience.
3. Set the daily intake (proposal: 10 a day, 8 minutes apart).
4. Turn on: workspace texts switch -> Family File Review campaign -> approve families -> start journeys.
5. Appointment messages on; then the survey.
6. Staff alerts once recipients are named.

## Pause
- Fastest: campaign OFF (stops new touches at dispatch). Appointment messages and the survey each have their own off switch. The workspace texts switch stops every text.
- Nothing queued is dumped later: stale reminders/confirmations/surveys are skipped with a reason.

## Recovery
- A wrong or stuck message: Appointments -> Messages shows status, reason and receipt.
- A family who replied/booked/opted out leaves the sequence on its own; check the rotation screen.
- Configuration rollback: `WORKSPACE_RESET=<org>:rollback:<run key>` restores what a configure run changed.

## Dependencies still open
- Twilio verification category vs outreach use (see the private evidence report).
- Calendar account identity (masked in the probe; Mike confirms it is the intended one).
- Copy/audience approval, daily volume and spacing, alert recipients, internal test recipients and a calendar-write test authorization.
