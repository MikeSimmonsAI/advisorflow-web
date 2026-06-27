# Session Log — v2.1: Auto-Send Queue, Phase 1 Complete

**Version: v2.1** (previous: v2.0 — Dashboard Redesign)

Continues from SESSION_LOG_V2.0_DASHBOARD_REDESIGN.md. This is the
biggest build left on the list - the auto-send queue - built across
two sessions with the same careful, staged approach used for every
other safety-relevant feature this project has built: schema first,
then the highest-stakes logic (tested exhaustively before anything
else touches it), then the wiring, then the human-facing surface.

All changes verified by actually running them, including a full
clean-state rebuild before calling this done:
- Backend: **673 passed, 8 skipped, 0 failed**
- Frontend: clean npm install + npm run build from a fully clean state

No migration needed for new tables - auto_send_candidates and
auto_sent_log are brand-new tables, picked up automatically by the
existing create_all() on next deploy. One real migration:
users.auto_send_phase, added to the auto-migration list.

---

## The actual design, exactly as agreed

A reply only ever becomes an auto-send candidate if it passes a real,
hard gate, checked in order:
1. The reply's general classification must be "question" - every other
   classification (interested, callback, dnc, not_interested,
   wrong_number, neutral) is hard-excluded, no exceptions, checked in
   plain Python before any AI call is even made.
2. A DEDICATED eligibility classifier - separate from the general
   reply classifier, built specifically for this one question -
   independently confirms the question is genuinely simple/logistical,
   not an emotionally loaded or ambiguous question that happens to end
   in a question mark.
3. The reply must NOT be the lead's first-ever reply - real, prior
   context must already exist.
4. Confidence must be HIGH, not medium or low - the one hard gate in
   this whole app where "probably fine" is not a permitted basis for
   an unsupervised send.

Two phases: "candidate" (Phase 1, this session - AI drafts, advisor
explicitly confirms/edits/declines, nothing sends without a click) and
"auto" (Phase 2, not yet built - the same gate, but a qualifying reply
sends with no click at all, logged permanently). An advisor's phase is
admin-controlled only - the same require_super_admin pattern already
used for can_import_leads - since this is the single highest-stakes
permission in the entire app.

---

## What got built this session

### Webhook wiring
New: app/services/auto_send_candidate_service.py
Changed: app/routers/sms_router.py (inbound webhook)
New tests: 12 in tests/test_auto_send_candidate_service.py, 3
end-to-end in tests/test_sms_router.py

The actual gate deciding whether the eligibility brain is even
consulted - an advisor on the default "off" phase (every advisor,
until explicitly opted in) gets zero candidate rows, zero API calls,
proven by a real end-to-end test hitting the actual webhook endpoint.

A real test bug was found and properly investigated during this
build, not papered over: Reply.classification has a column-level
default (NEUTRAL) that silently overrides an explicit None passed to
the constructor, once a real commit happens - confirmed by direct
investigation against a real in-memory SQLite database, not assumed.
The affected test was rewritten to use a genuinely uncommitted,
in-memory object, the only real way that code path is ever reached in
production.

### AI drafting
Changed: app/services/auto_send_candidate_service.py

Reuses the existing, already-proven draft_reply_service.draft_reply
directly - same conversation-history building, booking-link handling,
and AI-failure fallback already used for Lead Detail's one-on-one
drafting. No duplicate drafting logic was written. A failure in
drafting never blocks candidate creation - the candidate is still
created with an empty draft, and the advisor can write the reply
themselves in the review queue.

### A new, dedicated send function
New: send_exact_sms in app/services/sms_service.py
New tests: 4 in tests/test_sms_service.py

A real gap, not previously covered: send_sms runs every body through
render_template's placeholder substitution, which is unnecessary (and
a small correctness risk) for an already-fully-rendered AI draft;
send_plain_sms sends bare text but creates no Message record at all,
which the certification pipeline and conversation timeline both need.
Built send_exact_sms to fill this gap - sends a final body VERBATIM,
reusing the exact same DNC and suppression-list safety checks as
send_sms. Confirmed directly with a test that a literal
{first_name}-looking string in the body is never touched.

### Phase 1 review queue - the full backend API
New: app/routers/auto_send_router.py (list, counts, confirm,
edit-and-send, override, history)
New tests: 12 in tests/test_auto_send_router.py

Every send action goes through send_exact_sms, confirmed by a direct
test that the suppression-list check genuinely blocks a send attempt
through this queue (this is not a way around existing safety checks).
Override sends nothing at all, confirmed by asserting the Twilio
client is never even constructed. Every endpoint scoped to the
calling advisor's own candidates only.

### Phase 1 review queue - the frontend
New: frontend/src/pages/AutoSendQueue.jsx + .css
Changed: frontend/src/App.jsx, frontend/src/components/Layout.jsx

A real page, not a stub - shows each pending candidate as its own
card with the AI draft, the eligibility reasoning, and three real
actions (send as drafted, edit and send, decline). No bulk-confirm or
"send all" action anywhere on this page, on purpose - every candidate
gets an individual look, matching the design's actual safety intent.

### The admin-controlled toggle
Changed: app/routers/admin_router.py (UpdateUserRequest, UserResponse,
update_user, list_users, create_user)
Changed: frontend/src/pages/Users.jsx + .css
New tests: 8 in tests/test_user_management.py

A new "Auto-send" column in the Users table, editable only by
super_admin, with three real states (Off / Review queue / Full
auto-send), validated server-side, fully audit-logged with real
before/after values. Confirmed with a direct test that a regular
advisor is rejected outright by the role check (403) and that their
auto_send_phase is never touched by a rejected request.

### Real CSS consolidation, continuing the established discipline
A third instance of the same pattern from earlier sessions was found
and fixed: .tab/.tab--active were defined identically in both
Admin.css and Leads.css (confirmed byte-for-byte identical before
consolidating) and would have needed a THIRD identical copy for the
new Auto-Send Queue page. Moved to shared.css once, properly, with a
systematic comm-based check confirming zero duplicates remain across
the whole frontend afterward - not just trusted on a first pass.

---

## Suggested manual smoke test

1. As a super_admin, go to Users, edit an advisor, set Auto-send to
   "Review queue" - confirm it saves and shows the right badge.
2. Have that advisor's lead reply with a real, simple scheduling
   question (e.g. "what time works tomorrow") on a lead they've
   already had at least one prior reply from.
3. Confirm a new candidate shows up in that advisor's Auto-Send Queue
   page, with a real AI-drafted reply.
4. Try all three actions: send as drafted, edit and send, and decline
   - confirm each behaves correctly and the candidate moves to History.
5. Confirm a reply that's clearly hot/interested, or a DNC/STOP, or a
   lead's very first-ever reply never produces a candidate at all.

---

## Still ahead

Phase 2 - the actual no-click auto-send path and its permanent
AutoSentLog audit trail (the table already exists, unused until Phase
2 is built). The industry-agnostic vocabulary layer, the Qualification
gate (designed for, not built), Campaign Builder overhaul, Compliance
Preflight / full Conversation Timeline, AI Objection Library, the
Twilio A2P resubmission, rotating the Microsoft/Google client secrets
shared in chat during setup a few sessions back, and the pre-existing
Compliance.css dead-CSS cleanup flagged in the last session (still not
part of any session's scope).
