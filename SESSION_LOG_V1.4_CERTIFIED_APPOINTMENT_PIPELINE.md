# Session Log — v1.4: Certified Appointment Pipeline

**Version: v1.4** (previous: v1.3 — Google Contacts Sync + Referral Leads)

Continues from SESSION_LOG_V1.3_GOOGLE_CONTACTS_REFERRALS.md. Started
from a strategic conversation, not a feature request - Mike wanted to
think through what AdvisorFlow is actually FOR, and that conversation
turned into something real and buildable tonight.

All changes verified by actually running them, including a full
clean-state rebuild before calling this done:
- Backend: **552 passed, 8 skipped, 0 failed**
- Frontend: clean `npm install` + `npm run build` from a fully clean state

**No manual migration needed.** One new column
(`booking_links.confirmed_at`) gets picked up automatically by the
existing auto-migration system on next deploy.

---

## The strategic conversation, worth preserving in full

Mike asked what real products AdvisorFlow should be compared against,
then pushed further: he's not trying to build a better lead-tracking
CRM - he wants the end product to be **certified appointments**, not
just hot leads. Separately, he wants the whole platform to eventually
be a reusable template he can strip funeral-specific vocabulary out of
for other industries (roofing, land sales, etc.).

The key insight, surfaced by connecting those two threads rather than
treating them as separate asks: a real, auditable "certified
appointment" standard is inherently industry-agnostic, by its own
nature - confirming a real need, a real decision-maker, and a booked,
confirmed next step doesn't care whether the business is funeral homes
or roofing. That makes the certification pipeline the right thing to
build FIRST, before the industry-vocabulary abstraction work - it's
the universal core other industry-specific features can sit on top of,
rather than something that would need rework once a second industry
shows up.

## What "certified" actually means - Mike's own definition, verbatim

Asked directly what certified means in practice, not in the abstract.
His answer: "certified means that we've already solicited. We had to
contact them. They booked the appointment. We confirmed. Now we're
just waiting for them to come in." A real, ordered SEQUENCE of events,
not a score or an AI judgment call - "did this actually happen," not
"does this seem promising."

This is meaningfully simpler and more useful than an earlier framing
floated this session (need/decision-maker/timeframe/booked as four
separate attributes to confirm) - Mike's version is auditable: each
step is a literal yes/no fact, checkable against real data, not a
judgment call about lead quality.

## Qualification - the real industry-dependent exception

Mike flagged directly that some industries (land sales specifically)
need an extra verification/qualification step the base pipeline
doesn't require - funeral homes and "simple" businesses (his example:
a tire shop) don't need it; a buyer's qualification might matter for
land. Asked him directly whether to build that toggle now or design
for it later: building it now would mean real, untested code with zero
current users (no qualification-requiring client exists yet);
designing for it costs almost nothing today. Landed on designing for
it - `get_certification_status` already accepts an
`is_qualification_required` parameter that does nothing yet, a
deliberate, clearly-marked seam for when a real qualification feature
is actually needed.

---

## What got built

**New model:** `BookingLink.confirmed_at` in `app/models/models.py`
**New service:** `app/services/certification_service.py`
**New backend:** `GET /{lead_id}/certification`,
`POST /{lead_id}/certification/confirm` in `app/routers/leads_router.py`
**New frontend:** `frontend/src/components/CertificationPanel.jsx` +
`.css`, wired into `LeadDetail.jsx` as the headline status, above
Outcome Tracker
**New tests:** 10 in `tests/test_certification_service.py`, 5 in
`tests/test_leads_router.py`

**Real architectural decision worth preserving:** `LeadStatus` already
has a `BOOKED` value with real, existing logic depending on it
specifically (`cadence_service.py` stops cadence on `BOOKED`,
`engagement_service.py` scores it specially, `workqueue_router.py`
filters on it). Checked this directly before building anything -
adding a new `CONFIRMED` LeadStatus value to replace `BOOKED` once
confirmed would have silently broken every one of those checks. Built
`confirmed_at` as an ADDITIVE field on `BookingLink` instead -
"Confirmed" layers on top of the existing booking record rather than
replacing any status value, so nothing that already depends on
`LeadStatus.BOOKED` changes at all.

The five steps - Solicited, Contacted, Booked, Confirmed, Waiting - are
each checked against a real, underlying fact, not inferred from a
status field:
- **Solicited**: a `Message` or `EmailMessage` row exists for the lead
- **Contacted**: a `Reply` row exists
- **Booked**: a `BookingLink` with `status == "booked"` exists (a
  "pending" link - sent but not yet acted on - deliberately does NOT
  count, confirmed with a direct test)
- **Confirmed**: that booking's `confirmed_at` is set - a deliberate,
  separate action ("we confirm: if they say yes, I'm still good,
  that's confirmed"), never inferred automatically from booking alone
- **Waiting**: all four prior steps true - the certified state itself

Steps are earned strictly in order - `current_step` reflects the
earliest gap, not the latest individually-true fact, since
certification is about an unbroken real sequence.

`confirm_appointment` is idempotent - confirming an already-confirmed
booking preserves the original confirmation timestamp rather than
overwriting it, confirmed with a direct test.

The frontend shows a clean 5-step visual pipeline on Lead Detail, with
a "Mark confirmed" button that only appears once a lead has reached
the Booked step.

---

## Suggested manual smoke test

1. Open any lead with no messages sent → Certification panel should
   show step 1 (Solicited) not yet reached.
2. Send a message → step 1 lights up.
3. Get a reply on that lead (or simulate via the webhook) → step 2
   lights up.
4. Book an appointment through the real booking flow → step 3 lights
   up, "Mark confirmed" button appears.
5. Click "Mark confirmed" → step 4 lights up, panel shows "Certified —
   Waiting."

---

## Still ahead

The industry-agnostic vocabulary layer (Pre-Need → configurable per
org), the Qualification gate (designed for, not built - see above),
wiring certification status into Smart Lead Routing / the Lead
Intelligence Score / the auto-send queue's eligibility logic (all
benefit from a real "is this certified" check now that one exists),
Campaign Builder overhaul, Compliance Preflight / full Conversation
Timeline, AI Objection Library, the Twilio A2P resubmission, and
rotating the Microsoft/Google client secrets shared in chat during
setup a few sessions back.
