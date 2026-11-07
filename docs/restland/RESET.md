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
- **Finished with no reply:** next eligible = the **actual** last delivered outreach + 30 days.
- **Last touch undelivered:** the round still ends, recorded as `undeliverable`, measured from the last attempt.
- **Replied, booked, opted out or cancelled an appointment:** the family goes to a person or stays suppressed. They are never recycled.
- **Enforced per destination.** A phone or email that is resting, or in an open round, blocks every record that shares it.
  - The audience plan marks these families "in_round" or "resting".
  - Approval refuses them.
- **When the rest is over** the family can appear in a **reviewed** pool ("Rest over: review"). Never automatic enrollment.
- **Returning families** use their own drafted campaign, "Restland — Family File Review — Return" (OFF), whose copy says we wrote before.
- **Not built yet: sending a second round.** Email touches are numbered once per family, and the text cadence keeps one state per family. Until that's built, approving a returning family is refused with that reason. The earliest any family could return is about 44 days after launch.

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
