# SCI campaign intelligence and inbox attribution

## Where it is

| Screen | What is there |
|---|---|
| **SCI Live → Analytics** | Campaign picker and date buttons, all metrics for the selection, and a "Compare campaigns" table. |
| **Any number on Analytics** | Opens the exact records behind it, for the same campaign and dates. |
| **SCI Live → Conversations (Inbox)** | Campaign picker above the existing filters, and a campaign stamp on every card. |
| **A family's page** | Every campaign the family has been in, with the date and file it came from. Each message is tagged with its campaign. |

The selection lives in the web address (`?c=` campaign, `?r=` range, `?rs=`/`?re=` custom dates). Analytics, drill-downs, the inbox and a family's page all share it, and Back or a shared link restores it.

## What a campaign is

Nothing new was created; these are existing records.

- **Campaign.** An existing `program_campaign_families` row, identified by its stable `key` within the organization. Its status is that row's own switch (Active / Off).
- **Membership.** A family belongs to a campaign through the existing `program_source_records` row that brought it in. This module never edits that row.
- **Whole import.** An import file that brought in more than one campaign can be picked as one scope, for example the original SCI file with its ten message families. The selector names it after the file itself ("All of sci_poc_561.csv (Oct 12, 2026)"); no campaign name is invented.
- **Unattributed.** Families or events with no campaign on record.

## Attribution (`app/services/programs/campaign_attribution.py`)

Evidence order for each event:

1. **Explicit.** The event row stores `campaign_family` (`program_responses`, `program_email_touches`).
2. **Membership.** The family belonged to exactly one campaign when the event happened. A membership created later never claims an earlier event.
3. **Ambiguous.** The family was in two or more campaigns at that time and nothing on the event says which. The event is shown as "Ambiguous" and never guessed.
4. **Unattributed.** No campaign membership at all.

Location and dates are never used to guess a campaign.

**Read-only.** Attribution is computed when a screen is read. No campaign ID is written onto any message, reply or booking, so there is no backfill and nothing to roll back, and no history can be lost.

## Metric timestamps (Central time, America/Chicago)

| Metric | Counted on |
|---|---|
| Text attempts / delivered / failed / pending | The send attempt time (`cadence_touch_logs.attempted_at`), with the carrier's final status. A failure is counted on the day the text was sent, not the day the carrier reported it. |
| Email attempts / failed | `program_email_touches.attempted_at` |
| Replies, Interested, Opt-outs | When the reply was received (`program_responses.received_at`) |
| Times offered | `program_conversations.offered_at` |
| Appointments confirmed | When the booking was confirmed (`booking_links.confirmed_at`, else created) |
| AI / staff replies | `messages.sent_at` / `email_messages.sent_at` |

**Date ranges** use Central calendar days:

- **Today:** today so far.
- **7 Days:** today plus the 6 days before it.
- **30 Days:** today plus the 29 days before it.
- **Custom:** both dates included.
- **All Time:** the full recorded history.

## Reconciliation

**Message, reply, opt-out and appointment counts.** Each event has exactly one attribution, so:

    All campaigns = sum of campaign rows + Unattributed + Ambiguous

The Compare table checks this live and says "Reconciled".

**Unique families** are distinct counts. A family in two campaigns counts in both rows and once in All campaigns. Whole-import rows overlap the campaigns they contain.

## Production preview (read-only queries, Nov 3, 2026)

**SCI workspace** `0a884078-…`, real families only (test and simulation contacts excluded):

| Scope | Key / import | Families |
|---|---|---|
| Original campaign | import `sci_poc_561.csv` (`b3bdb258…`), 10 families | 561 |
| Central Alabama 2025–2026 MS | `central_alabama_ms` (import `602a0bee…`) | 440 |
| No family on record (in the original import) | — | 2 |

No family is in both campaigns.

**Attribution of recorded activity:**

| Activity | Count | How it was attributed |
|---|---|---|
| Text attempts | 2,323 | Membership, one campaign each |
| Email touches | 1,051 | Explicit, matching membership: 0 conflicts |
| Replies | 205 | 204 explicit (0 conflicts with membership), 1 by membership |
| Bookings | 2 | Membership |

Unattributed activity: only the 2 families without a family assignment. Ambiguous: 0.

## Not measured (shown honestly, not estimated)

- **Email "delivered"** means accepted by the mail service (the existing definition). Arrival in the inbox is not instrumented.
- **Appointments completed, no-shows and cancellations** are not tracked in SCI analytics yet.
- **Staff response time** is not reported.

## Rollback

This adds no table, column or migration, and has no background job. To roll back:

1. Revert the merge commit.
2. Redeploy the backend and frontend.
