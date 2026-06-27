# Session Log — v1.2: Action Center, Feature Toggles, Role Docs, Email Tone

**Version: v1.2** (previous: v1.1 — Manual Lead Entry, Editable Details, Mandatory Outcomes)

Continues from SESSION_LOG_V1.1_MANUAL_LEAD_EDIT_OUTCOMES.md. Completes
items 4-7 of the 7-item priority list - the full list is now done.

All changes verified by actually running them, including a full
clean-state rebuild (fresh `npm install`, fresh test run) before
calling this done, not just the in-progress checks along the way:
- Backend: **518 passed, 8 skipped, 0 failed**
- Frontend: clean `npm install` + `npm run build` from a fully clean state

**No manual migration needed.** Two new columns
(`users.feature_flags`) get picked up automatically by the
auto-migration system on next deploy.

---

## Item 4 — Replies as an action center

**New backend:** `GET /sms/replies/counts`, `bucket=` filter param on
`GET /sms/replies`, both in `app/routers/sms_router.py`
**Changed frontend:** `frontend/src/pages/Replies.jsx` + `.css` -
rebuilt with clickable scorecards
**New tests:** 11 in `tests/test_replies_action_center.py`

Mike's exact words: "it should not just send me back to the lead
sheet... it should feel like an action center, not just a message
list." Confirmed: Replies was a single flat feed with one binary
filter checkbox, no at-a-glance sense of what matters.

**Important scoping conversation, worth preserving:** Mike's original
notes named buckets like "Appointment interest" and "Objections" that
don't exist as real classifications anywhere in the system today.
Talked through productivity vs. risk directly - building the action
center now with the 5 buckets that have real, already-tracked data
(Hot, Callback, Question, Not Interested/Wrong Number/DNC, Needs
Follow-up, Reviewed) gets something genuinely useful in Mike's hands
today; inventing Objections/Appointment Interest as real
classifications first would mean touching the carefully-tuned AI
classifier before anything visible exists. Landed on building the real
buckets now, logging the two missing ones as a real future
classification project rather than faking them.

`reply_counts` is a SEPARATE endpoint from the existing list, not
derived from it - the list caps at 200 rows, and the scorecards need
true totals regardless of how many replies actually exist.

---

## Item 5 — Per-advisor feature toggle system

**New model field:** `User.feature_flags` (comma-separated string) in
`app/models/models.py`
**New service:** `app/services/feature_flags_service.py`
**Changed backend:** `app/routers/admin_router.py` - new
`GET /admin/feature-flags/available`, `feature_flags` wired into
`update_user`/`UserResponse`/`list_users`
**Changed frontend:** `frontend/src/pages/Users.jsx` + `.css` - renders
toggle checkboxes dynamically from the live registry
**New tests:** 9 in `tests/test_feature_flags_service.py`, 6 in
`tests/test_user_management.py`

Mike asked directly which approach is more "bulletproof": named
toggles tied to real code, or a freeform system where an admin types
in arbitrary permission names. Answered directly and built accordingly:
named toggles are bulletproof because every flag is validated against
`KNOWN_FEATURE_FLAGS` server-side - a typo or made-up flag name is
rejected immediately with a clear error, never silently doing nothing.
Proved this with a direct test: a flag present in stored data but no
longer in the live registry is treated as disabled, not trusted blindly.

**Real architectural decision, worth preserving:** `can_import_leads`
(built two sessions ago) was deliberately NOT migrated into this new
generic system, even though that would have been more "consistent."
Reasoning given directly to Mike: that flag controls something with
real weight (bulk lead import), already has its own dedicated,
tested, directly-queryable column, and folding it into a generic
string list would only make it harder to reason about for zero
benefit. The right long-term architecture is both coexisting on
purpose - dedicated columns for permissions with real teeth, this
generic system for lighter-weight feature gates added over time.

`KNOWN_FEATURE_FLAGS` is intentionally empty right now - no
placeholder/invented flags were added just to have something to show;
the registry is ready for whenever a real lightweight feature
actually needs gating.

---

## Item 6 — Role descriptions in the Users panel

**Changed frontend only:** `frontend/src/pages/Users.jsx` + `.css`

Confirmed zero descriptions existed anywhere - no tooltips, no help
text, nothing explaining what Advisor/Org Admin/Super Admin actually
means. Built `ROLE_INFO`, a single source of truth cross-checked
directly against `app/deps.py`'s real `require_admin`/
`require_super_admin` logic (not written from assumption), used by: a
collapsible "What do the roles mean?" reference panel comparing all
three roles side by side, live help text under the create-user role
dropdown, and a hover tooltip on the inline edit dropdown.

---

## Item 7 — Email tone/strength control

**Changed backend:** `app/services/template_ai_service.py` (new
`TONE_GUIDANCE`, `VALID_TONES`, `tone` param on `generate_template`/
`rewrite_template`), `app/routers/templates_router.py` (`tone` field
on both request models, validated in both route handlers)
**Changed frontend:** `frontend/src/pages/Templates.jsx` + `.css` - new
tone dropdown in the AI writer bar
**New tests:** 6 in `tests/test_template_ai_service.py`

**Real finding worth preserving:** Mike's request matched the SMS tone
selector built last session in spirit, but the actual email send path
turned out to be structurally different - outbound email is
template-based (fixed `EMAIL_TEMPLATES`, no AI call at send time), not
AI-drafted per-message the way SMS's "Suggest Reply" is. There's no
equivalent of an AI draft step on the actual send path to attach a
tone to.

The real, correct hook turned out to be the existing AI Template
Writer (`POST /templates/ai/generate` and `/ai/rewrite`, built a couple
sessions ago for admins editing template wording) - that's the genuine
AI-generation point in the email system, and it already accepted
free-text instructions like "make this warmer." Added a structured
`tone` parameter (soft/standard/urgent/direct, the exact same 4 tones
as the SMS selector) alongside the existing free-text instruction
field, so an admin gets both a quick structured choice and the option
to type something more specific. Reuses the identical tone-guidance
phrasing pattern from `draft_reply_service.py` for consistency, kept
as a separate constant rather than a shared import since template
generation and one-off reply drafting are different enough in framing
to warrant their own wording.

Removed a blanket "never pushy" rule from the generate prompt that
would have directly conflicted with the new urgent/direct tone options.

---

## Suggested manual smoke test

1. Replies page → confirm 7 scorecards show real numbers → click "Hot
   replies" → confirm the list below filters to just hot replies →
   click "Clear filter" → confirm everything shows again.
2. Users page → click "What do the roles mean?" → confirm all three
   roles show accurate descriptions.
3. Users page → edit any advisor → confirm there's no "Features"
   section visible yet (expected - the registry is empty until a real
   flag exists).
4. Templates page → edit any template → use "Generate with AI" with
   different tone selections → confirm the generated copy genuinely
   reads differently between Soft and Direct, not just the same text.

---

## All 7 items from the priority list are now complete

1. ✅ Manual single-lead entry (v1.1)
2. ✅ Lead Detail editability (v1.1)
3. ✅ Mandatory outcome selections (v1.1)
4. ✅ Replies action center (v1.2)
5. ✅ Per-advisor feature toggle system (v1.2)
6. ✅ Role descriptions (v1.2)
7. ✅ Email tone control (v1.2)

## Still ahead, not part of the 7-item list

Next of Kin / family contact tree, Google Contacts sync, Campaign
Builder overhaul (garden filters, flyer attachments, tracking), the
big auto-send queue plan (training-wheels phase, dedicated AI
eligibility classifier, auto-sent log), Compliance Preflight / full
Conversation Timeline, the Twilio A2P resubmission (tabled by Mike
until he says go), and the genuinely bigger AI Objection Library /
Appointment Interest classification work flagged in item 4 above.
