# Session Log — v1.8: Full Email Queue Rebuild

**Version: v1.8** (previous: v1.7 — Mixed-Channel Cadence)

Continues from SESSION_LOG_V1.7_MIXED_CHANNEL_CADENCE.md. Mike named
the Email Queue directly as something he's "always been unsatisfied
with," and explained exactly why: it's been a thin SMS clone, never
built around what email is actually good for. Rebuilt as four real
pieces, all at once, per his explicit choice not to phase this.

All changes verified by actually running them, including a full
clean-state rebuild before calling this done:
- Backend: **597 passed, 8 skipped, 0 failed**
- Frontend: clean `npm install` + `npm run build` from a fully clean state

---

## One real environment variable to set before this is fully live

`TRACKING_BASE_URL` - defaults to
`https://advisorflow-backend.onrender.com` if not set, which is
correct for the current production backend. If that backend URL ever
changes, this needs to be updated too, or open/click tracking links
will point at the wrong place. No migration needed otherwise - new
columns get picked up automatically by the existing auto-migration
system.

---

## Part 1 — Broadened lead scope

**Changed:** `email_queue` (renamed from `email_only_queue`) in
`app/routers/email_router.py`

**New tests:** 3 in `tests/test_email_router.py`

Real, confirmed gap: the queue only ever showed
`Lead.contact_channel == "email_only"` - a lead with BOTH a phone and
an email was invisible here entirely. Now any lead with a real email
address shows up, regardless of contact_channel. Confirmed the
existing tests never actually caught this, since their helper
hardcoded every test lead to `email_only` regardless of phone presence
- added a dedicated test with a genuinely `contact_channel="sms"` lead
that also has an email, proving the real fix.

---

## Part 2 — Real rich content composer

**New:** `frontend/src/components/RichEmailComposer.jsx` + `.css`

**Changed:** `frontend/src/components/EmailReview.jsx` - replaced the
raw HTML textarea entirely

The old editor was a plain textarea where an advisor had to hand-type
HTML tags. Built a real, lightweight composer instead - bold/italic/
underline/bullet-list formatting plus actual image insertion, with
images embedded as base64 data URIs directly in the HTML rather than
uploaded to separate file storage. Deliberately NOT a third-party
rich-text library - no new dependency added, since contentEditable
plus a small toolbar covers the real requirement without the weight of
a full editor framework.

A real bug caught and fixed before it shipped: the first version used
`dangerouslySetInnerHTML` to set the editable content, which
re-applies on every React re-render - since contentEditable content is
mutated directly by the browser as the user types, this would have
fought the user's live edits/cursor position the moment `value`
changed via our own `onChange` callback. Fixed by initializing content
once via `useEffect` on mount, then letting the DOM own it directly
afterward.

Embedded-image approach means BOTH existing send paths (SendGrid,
Microsoft Graph) work with zero code changes, since they already just
take a plain `body_html` string.

---

## Part 3 — Open/click tracking

**New model fields:** `EmailMessage.opened_at`, `click_count`,
`last_clicked_at` in `app/models/models.py`

**New service:** `app/services/email_tracking_service.py`
(`inject_tracking`)

**New router:** `app/routers/email_tracking_router.py` - two
deliberately UNAUTHENTICATED endpoints (`/email-tracking/open/{id}`,
`/email-tracking/click/{id}`), since they're hit directly by a
recipient's email client/browser with no AdvisorFlow login at all

**Changed:** both real send paths - `send_email_to_lead`
(app/services/email_service.py) and `confirm_email_send_batch`
(app/routers/email_router.py) - found and fixed independently, since
they had separately duplicated send logic

**New tests:** 7 in `tests/test_email_tracking_service.py`, 10 in
`tests/test_email_tracking_router.py`, 2 end-to-end in
`tests/test_email_router.py`

How it works: every link in an outgoing email gets rewritten to route
through `/email-tracking/click/{id}` first (logs the click, then
302-redirects to the real original URL - recipient's experience is
unaffected), and a 1x1 transparent tracking pixel gets appended to the
body (most email clients auto-load images, which is what marks an
email opened). `opened_at` only ever gets set once (first open, not
most recent - email clients can re-fetch images on repeat views);
`click_count` and `last_clicked_at` update on every click, since
repeat clicks are still meaningful engagement.

Real sequencing fix needed in both send paths: tracking URLs are keyed
by `EmailMessage.id`, which doesn't exist until that row is inserted -
but the original code sent the email FIRST, then created the row.
Fixed in both places: create the row first (status="queued"), get its
real id, inject tracking into a separate copy of the HTML used only
for the actual provider call, while the ORIGINAL untracked content
stays in `body_html` - so the saved record stays clean and re-readable,
with tracking noise never baked in permanently.

The most important test in this whole feature is the end-to-end one in
`test_email_router.py`
(`test_sent_history_includes_open_and_click_tracking_fields`) - it
sends a real email through the actual endpoint, then calls the
tracking endpoints exactly as a real recipient's email client would,
then confirms the Sent tab's response reflects that real engagement.
Proves the entire pipeline works together, not just each piece in
isolation.

---

## Part 4 — Surfaced in the UI

**Changed:** `app/routers/email_router.py` (`email_sent_history` now
returns `opened_at`/`click_count`), `frontend/src/pages/EmailQueue.jsx`

New "Opened" and "Clicks" columns on the Sent tab - a green "Opened"
badge with the actual timestamp on hover, or "Not yet"; a real click
count, or "—" if zero. Page copy updated throughout (subtitle,
empty-state) to reflect the broadened scope from Part 1 - no longer
"Email-only leads," now "Anyone with an email on file."

Also cleaned up two CSS classes (`.email-review-body-textarea`,
`.email-review-html-note`) that became genuinely unused once the raw
textarea was replaced by RichEmailComposer in Part 2.

---

## Suggested manual smoke test

1. Email Queue page - confirm a lead who has BOTH a phone and an email
   now shows up here (previously would have been invisible).
2. Open the composer for any lead - use the formatting buttons, insert
   a real image - confirm it renders inline as you'd expect, not as
   raw HTML text.
3. Send a real test email to yourself or a test inbox - open it,
   confirm images render correctly, click any link in it - confirm you
   land on the right page (the redirect should be invisible/instant).
4. Back in AdvisorFlow's Email Queue - Sent tab - confirm that email
   now shows "Opened" and at least 1 click.
5. Confirm an email you've sent but haven't opened/clicked yet
   correctly shows "Not yet" and "—".

---

## Still ahead

The auto-send queue, the industry-agnostic vocabulary layer, the
Qualification gate (designed for, not built), Campaign Builder
overhaul (this session's image-embedding work in RichEmailComposer is
directly reusable there for flyers/promos), Compliance Preflight / full
Conversation Timeline, AI Objection Library, the Twilio A2P
resubmission, and rotating the Microsoft/Google client secrets shared
in chat during setup a few sessions back.
