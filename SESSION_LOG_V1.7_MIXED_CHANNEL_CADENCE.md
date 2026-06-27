# Session Log — v1.7: Mixed-Channel Cadence (Text + Email, Never Both At Once)

**Version: v1.7** (previous: v1.6 — Email Timeline Fix)

Continues from SESSION_LOG_V1.6_EMAIL_TIMELINE_FIX.md. Started from
Mike checking the email-timeline fix and immediately spotting a real,
deeper problem: if a lead has both a phone and an email, the cadence
needs to coordinate which channel gets used for which touch, not just
blast both.

All changes verified by actually running them, including a full
clean-state rebuild before calling this done:
- Backend: **575 passed, 8 skipped, 0 failed**
- Frontend: no changes this session (clean rebuild confirms nothing
  else regressed)

**No migration needed.** No new database columns or tables - this is
entirely new sending logic inside the existing cadence engine.

---

## The actual design conversation, worth preserving in full

Mike's first framing sounded like it wanted two independent parallel
tracks - SMS cadence and email cadence both running for the same lead.
Checked the schema before agreeing to that: `CadenceState.lead_id` has
a database-level `unique=True` constraint - a lead can have exactly
ONE cadence row, ever. Building real parallel tracks would have meant
genuine schema surgery touching 47 references across the codebase.
Flagged that size honestly and asked whether to plan it properly
first - Mike's answer, correctly, was "we're not live yet, fix it now"
- a fair call given there's no production data to migrate carefully
around.

But then, talking it through further, Mike corrected his own framing
directly: NOT two tracks running at once (he was clear that's the
wrong approach), and not one fixed channel for the whole sequence
either. What he actually wanted: one single sequence, same 9 touches,
same schedule - but each touch's CHANNEL chosen deliberately, mixing
text and email for a lead who has both, never the same touch landing
on both channels at once.

That's a fundamentally simpler, safer design than what the schema
question implied - no schema change needed at all, since it's still
one `CadenceState` row per lead. The real conversation that mattered
here was catching the actual shape of what Mike wanted before building
the wrong (bigger, riskier) thing.

Mike's own reasoning for why text and email need to coexist mattered
too, not just stylistic preference: some contacts are landline-only
households that never see a text but read email; promotional content
with real visuals performs better by email than a one-line text. This
is also why the Email Queue currently feeling underbuilt to him makes
sense - it's been treated as a thin SMS clone, not built around what
email is actually good for.

---

## What got built

**Changed:** `app/services/cadence_service.py` - new
`MIXED_CHANNEL_PATTERN` constant, new `_channel_for_touch()` function,
`run_due_cadences()` rewritten to branch between SMS and email sending
per-touch instead of always sending SMS.

**New tests:** 12 in `tests/test_cadence_service.py`.

The actual 9-touch pattern, with real reasoning behind each choice,
not arbitrary: text carries the fast, early touches; email lands on
touches that have had time to build something worth saying. Final
pattern, after fixing a real bug caught by testing the pattern's own
rule (see below):

Touch 1 (Day 1) sms, Touch 2 (Day 3) sms, Touch 3 (Day 7) email,
Touch 4 (Day 10) sms, Touch 5 (Day 14) sms, Touch 6 (Day 21) email,
Touch 7 (Day 30) sms, Touch 8 (Day 45) sms, Touch 9 (Day 60) email.

6 text / 3 email overall, but the real rule that matters is the
sequencing: email never appears twice in a row, and the LAST touch is
deliberately email - a real, substantial final attempt rather than a
throwaway text, per Mike's own promo/visual-content reasoning.

Only applies to leads with BOTH a phone and an email - a lead with
only one contact method is completely unaffected, always uses that one
method for every touch, exactly as before this change.

### A real bug caught by the tests, not just trusted from the comment
The first version of `MIXED_CHANNEL_PATTERN` had Touch 6 (Day 21) and
Touch 7 (Day 30) both set to "email" - directly violating the "never
two emails in a row" rule that was simultaneously documented in a
comment right above the array. Caught immediately by writing
`test_mixed_pattern_never_has_two_emails_in_a_row` as a real,
structural test against the pattern itself - not because the comment
was wrong, but because the array didn't actually match what the
comment claimed. Fixed by moving Touch 7 to sms. This is exactly why a
stated design rule needs a test enforcing it, not just a comment
describing it.

### A second real bug fixed alongside this feature
`run_due_cadences` previously required `advisor.twilio_phone_number`
unconditionally for every touch, regardless of which channel that
specific touch was actually going to use. A lead with both phone and
email, on a touch that the mixed pattern assigns to email, would have
failed with "advisor has no Twilio number configured" even though
that touch was never going to use Twilio at all. Fixed: the Twilio
check now only runs when channel == "sms". Confirmed directly with a
test where the advisor has zero Twilio configuration at all, and an
email-channel touch still sends successfully.

`send_email_to_lead` (already built, used by the manual Email Queue)
is reused directly for the email-channel sending path - no duplicate
email-sending logic was written for cadence.

---

## Suggested manual smoke test

1. Create or find a lead with BOTH a phone and an email, start their
   cadence (or use a test/sample lead).
2. Manually trigger or wait for the cadence job to process touch 1 -
   confirm it sends as a text, not an email.
3. Advance to touch 3 (Day 7) - confirm it sends as an email, with the
   email actually showing up in the Conversation timeline tagged
   "Email" (the previous session's timeline fix and this session's
   mixed-channel sending work together here).
4. Confirm no single touch ever produces both an SMS and an email at
   once.
5. Confirm a lead with ONLY a phone number (no email) behaves exactly
   as before - every touch is SMS, unaffected by this change.

---

## Still ahead

The auto-send queue, the industry-agnostic vocabulary layer, the
Qualification gate (designed for, not built), a real rebuild of the
Email Queue itself around what email is actually good for (Mike's own
direct complaint - promos, visuals, real campaigns, not a thin SMS
clone), Campaign Builder overhaul, Compliance Preflight / full
Conversation Timeline, AI Objection Library, the Twilio A2P
resubmission, and rotating the Microsoft/Google client secrets shared
in chat during setup a few sessions back.
