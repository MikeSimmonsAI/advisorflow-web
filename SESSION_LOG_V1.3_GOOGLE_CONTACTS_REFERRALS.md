# Session Log — v1.3: Google Contacts Sync + Referral Leads

**Version: v1.3** (previous: v1.2 — Action Center, Feature Toggles, Role Docs, Email Tone)

Continues from SESSION_LOG_V1.2_ACTION_CENTER_TOGGLES_ROLES_TONE.md.
Completes two of the bigger backlog items: automatic Google Contacts
sync, and a real referral-lead system (what started as "Next of Kin"
but turned into something bigger once the actual use case got
clarified directly).

All changes verified by actually running them, including a full
clean-state rebuild before calling this done:
- Backend: **537 passed, 8 skipped, 0 failed**
- Frontend: clean `npm install` + `npm run build` from a fully clean state

**No manual migration needed** for either feature - one new column
(`leads.google_contact_resource_name`) gets picked up by the existing
auto-migration system, and one brand-new table (`lead_referrals`) is
created automatically by `Base.metadata.create_all()` on next deploy,
the same way every table always has been - new tables were never the
auto-migration system's job; that's for columns/enum values added to
tables that already exist.

---

## Google Contacts Sync

**New model field:** `Lead.google_contact_resource_name` in
`app/models/models.py`
**New service:** `app/services/google_contacts_service.py`
**Changed:** `app/services/calendar_service.py` (imports `SCOPES` from
the new module instead of defining its own, narrower scope),
`app/services/import_service.py` (sync hook after a real, non-dry_run
import), `app/routers/leads_router.py` (sync hook in
`create_lead_manually` and the new referral endpoint)
**New tests:** 10 in `tests/test_google_contacts_service.py`

Mike's exact words: "I need the leads to sync into Google Contacts so
when I call someone from my phone, their name and number are already
there... if I upload a spreadsheet, those contacts need to be able to
go into Google Contacts too." Confirmed automatic (his explicit
choice, not a manual/triggered sync) - "no review step."

**Real architectural finding worth preserving:** the existing Google
Calendar OAuth scope (`calendar.events` only) doesn't cover Contacts
access - that needs the separate `contacts` scope. Rather than build a
second, separate "Connect Google Contacts" flow, widened the existing
Calendar consent to request both scopes together, so one Connect
button covers both going forward. **Real, unavoidable consequence:**
any advisor who already connected Google Calendar before this change
will need to reconnect once to grant the new scope - their existing
refresh token doesn't retroactively cover it. Flagged directly, not
hidden.

Sync is automatic but never blocking - a lead is always created
successfully whether or not Google sync works. If the advisor hasn't
connected Google, sync silently no-ops (not an error). If the Google
API call itself fails (rate limit, network), that's caught and logged
but never surfaces as a lead-creation failure. Idempotent - a lead
that's already synced (tracked via `google_contact_resource_name`)
is never synced a second time, confirmed with a direct test proving
zero additional API calls happen on a re-sync attempt. Batch sync
(used after a bulk import) isolates each lead's sync attempt
independently, so one bad lead in a batch of 500 never stops the rest.

---

## Referral Leads ("Next of Kin," reshaped through a real conversation)

**New model:** `RelationshipType` enum, `LeadReferral` table in
`app/models/models.py`
**New backend:** `POST /{lead_id}/referrals`, `GET /{lead_id}/referrals`
in `app/routers/leads_router.py`; refactored `create_lead_manually`'s
core logic into a shared `_create_lead_core` function so both manual
entry and referrals go through the exact same dedup/tier logic, not
two diverging copies of it
**New frontend:** `frontend/src/components/ReferralPanel.jsx` + `.css`,
wired into `LeadDetail.jsx` right after the Outcome Tracker
**New tests:** 9 in `tests/test_leads_router.py`

**This is the most important conversation in this session, worth
preserving in full.** Mike's original notes described "Next of Kin" as
a field - name, relationship, phone, permission status - attached to a
lead's record. Before building that, asked him to walk through a real
scenario, and what came back was genuinely different: "I'm dealing
with Deborah Brown and... she's now given me Lisa and Tom [through a
permission-to-access form]... I need to be able to send out some
messages to Lisa and Tom so that they know I'm dealing with [Deborah]
and I need to get them in for pre-need."

That's not a notes field - Lisa and Tom are real prospects Mike wants
to message directly and close on their own. Asked directly whether
they should become full, separate Lead records (own cadence, replies,
outcomes) or stay as reference info on Deborah's lead - Mike confirmed
the former. Building the originally-described "Next of Kin field"
would have buried the actual valuable part of this idea (the ability
to reach out to them) inside a static field nobody could act on.

**What got built instead:** a real referral-lead system. Adding a
referral creates a genuine, independent Lead row - eligible for
cadence, replies, AI drafting, outcome tracking, everything any other
lead gets - going through the EXACT same dedup check and tier-to-track
mapping as manual entry (via the new shared `_create_lead_core`, not a
second implementation that could drift). A `LeadReferral` row links
the two leads and records the relationship type (Spouse, Child,
Parent, Sibling, Decision Maker, Power of Attorney, Other Family -
Mike's confirmed list). Lead Detail now shows both directions: "this
lead referred: Lisa, Tom" and, on Lisa's own page, "referred by:
Deborah Brown (Child)."

Referral leads default to Pre-Need tier (confirmed directly - "the
goal is getting them in for pre-need"), overridable at the moment of
adding them, same as manual entry. They also get the automatic Google
Contacts sync described above, since they're genuinely normal leads
in every other respect.

### A real test-setup bug caught and fixed during this build
While writing the dedup test for referrals, found that constructing a
`Lead` row directly in a test (bypassing the real creation endpoints)
never populates `ContactRegistry` - the table `check_and_register`
actually checks. The first version of the test created a "duplicate"
this way and got a false failure, which on investigation turned out to
be a gap in the test's own setup, not the real code. Fixed by routing
the test's "existing lead" through the real `/leads/manual` endpoint
instead, confirming the dedup protection genuinely works end-to-end
rather than asserting against data that was never actually registered.

---

## Suggested manual smoke test

1. Settings → reconnect Google (existing connections need to
   re-consent for the new Contacts scope) → confirm System Health shows
   connected.
2. Add a manual lead with a real phone number → check that advisor's
   actual Google Contacts → confirm a new contact appeared, tagged
   "Added via AdvisorFlow."
3. Import a small batch via Excel → confirm multiple contacts sync
   without errors, and the import itself completes normally even if
   sync is slow.
4. Open any lead → "+ Add referral" → fill in a plus-one with a real
   relationship type → confirm a genuinely new, separate lead appears
   in the main Leads list, not just a note on the original lead.
5. Open the new referral lead's own page → confirm "Referred by:
   [original lead] ([relationship])" shows up.
6. Confirm the referral lead is fully workable - send it a message,
   confirm it shows up in Replies/Cadence like any other lead.

---

## Still ahead

Campaign Builder overhaul, the auto-send queue plan, Compliance
Preflight / full Conversation Timeline, the Certified Hot Lead /
Certified Appointment strategic definition (logged as its own
dedicated planning session, not started), AI Objection Library /
Appointment Interest classifications, the Twilio A2P resubmission
(tabled by Mike), and rotating the Microsoft/Google client secrets
that were shared in chat during setup a couple sessions back.
