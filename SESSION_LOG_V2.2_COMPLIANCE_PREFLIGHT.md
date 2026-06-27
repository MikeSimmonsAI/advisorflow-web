# Session Log — v2.2: Compliance Preflight Engine

**Version: v2.2** (previous: v2.1 — Auto-Send Queue Phase 1)

Continues from SESSION_LOG_V2.1_AUTO_SEND_QUEUE_PHASE1.md. Picked the
biggest, hardest item left on the backlog by deliberate choice: one
real, shared compliance gate every send path must call, instead of
each path independently re-implementing (or in several real cases,
never implementing at all) its own DNC/suppression check.

All changes verified by actually running them, including a full
clean-state rebuild before calling this done:
- Backend: **689 passed, 8 skipped, 0 failed**
- Frontend: no changes this session (clean rebuild confirms nothing
  else regressed) - this was entirely a backend safety consolidation

No migration needed - no schema changes at all this session.

---

## What was actually found before any code was written

Mapped every real send path in the app before touching anything, and
the result was more serious than expected going in:

- send_sms and send_exact_sms (SMS) - each already had their own
  correct, but separately duplicated, DNC + suppression checks.
- send_email_to_lead (the manual Email Queue / cadence email-touch
  path) - had ZERO compliance check of any kind. A lead who replied
  STOP via text (correctly marked DNC, correctly blocked from further
  texts) could still receive emails.
- confirm_email_send_batch (the other manual email send route) - same
  gap, zero compliance check at all.
- The daily cadence job's re-check - only ever looked at
  Lead.status == DNC directly, never the suppression list. A number
  sitting in the suppression list with a Lead.status that had drifted
  out of sync (the exact scenario the suppression list exists to
  catch) would have still been texted by this fully automated,
  no-human-in-the-loop job.
- SuppressionEntry itself is phone-only (phone column is
  nullable=False) - it structurally cannot represent an email-based
  opt-out at all.

Raised the real, specific question directly before designing anything:
should a STOP on one channel block every channel for that lead, or
should each channel need its own separate opt-out? Confirmed: every
channel, always - a real opt-out signal means "leave this person
alone," not "leave this specific channel alone."

---

## The actual fix - one real, shared gate

New: check_compliance_preflight(db, lead) in
app/services/compliance_service.py

New tests: 7 in tests/test_compliance_preflight.py

The single function every send path now calls. Checks Lead.status ==
DNC FIRST and unconditionally - this is the real, channel-agnostic
signal, and it's why an email-only DNC lead (no phone to suppress at
all) is still correctly blocked. Then, additionally, checks phone
suppression when a phone number exists - an independent guard for the
specific "suppression list says no, but Lead.status hasn't caught up
yet" drift scenario. A lead with no phone simply skips that second
check without erroring.

This was NOT built by adding a new table or new schema - the existing
Lead.status field and the existing is_phone_suppressed function were
already each individually correct; the fix was building the one real
function that calls both, in the right order, and making every send
path actually call it.

---

## Every real send path now wired to the same gate

send_sms, send_exact_sms (app/services/sms_service.py) - their
previous duplicated inline checks replaced with one call each to the
shared gate. 19/19 existing tests still pass unchanged, confirming
behavior preserved exactly.

send_batch (app/services/sms_service.py) - its pre-filter (used to
keep the batch summary's skipped list accurate) extended to also
check suppression, not just DNC status - previously a suppressed-but-
not-DNC lead in a batch would have ended up in the failed list via a
caught exception rather than cleanly skipped.

send_email_to_lead (app/services/email_service.py) - the actual, most
important fix in this whole session. 3 new tests, including the
specific scenario Mike described: a lead suppressed via phone (STOP
on text) must still be blocked from email, and an email-only DNC lead
with no phone at all must also be blocked.

confirm_email_send_batch (app/routers/email_router.py) - same fix for
the manual batch-send endpoint. A compliance block is now tracked as a
genuinely distinct outcome (blocked_count) from a missing-email skip
(skipped_count), since an advisor reviewing batch results deserves to
know WHY something didn't send, not just that it didn't. Confirmed
with a test that one blocked lead in a batch never stops the rest from
sending.

The daily cadence job (app/services/cadence_service.py) - the
real-world most consequential fix: replaced the status-only re-check
with the real shared gate, confirmed with a direct test that a
suppressed-but-not-DNC-status lead is correctly stopped, with Twilio's
client never even constructed.

---

## What was checked and correctly left untouched

Did a systematic search for every direct Twilio messages.create call
and every direct email-provider call in the codebase, to confirm
nothing was missed:

- send_plain_sms - sends to the ADVISOR's own phone for self-alerts,
  never to a lead. Compliance checks exist to protect leads from
  over-contact, not to gate an advisor messaging themselves -
  correctly excluded by design, confirmed by reading its own
  documented rationale, not just assumed.
- notification_service.py's reply-alert email - same reasoning, sends
  to the advisor's own notification email, not a lead.
- Campaign Builder (campaign_router.py) - confirmed it only does
  create/list/preview/apply (lead filtering/tagging), no actual
  message-sending logic exists there to fix.

---

## Suggested manual smoke test

1. Mark a real test lead as DNC (or have them reply STOP via text).
2. Try sending them an email through the Email Queue - confirm it's
   now blocked with a clear error, where before this session it would
   have gone through.
3. If that lead also has a phone, confirm texting them is still
   (and was already) blocked.
4. Manually add a lead's phone number to the Compliance Center's
   suppression list without changing their status - confirm both
   texting AND emailing them is now blocked.

---

## Still ahead

The industry-agnostic vocabulary layer, the Qualification gate
(designed for, not built), Auto-Send Queue Phase 2 (no-click sending -
deliberately not started yet, since Phase 1 needs real use first),
Campaign Builder overhaul, full Conversation Timeline, AI Objection
Library, the Twilio A2P resubmission, and the pre-existing
Compliance.css dead-CSS cleanup (still not part of any session's
scope). Rotating the Microsoft/Google client secrets remains the
person's own call, on his own timeline.
