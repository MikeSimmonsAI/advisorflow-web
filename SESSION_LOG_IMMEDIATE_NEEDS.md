# Session Log — Immediate-Need Enhancements

Built on top of the reviewed/fixed branch (see CLAUDE_FIX_LOG.md for the
cadence health-summary bug fix from the prior review pass). All three items
below come from Mike's "lock it in" feedback pass: AI template writer,
bulk lead assignment (Leads page + Lead Cleanup page).

All changes verified by actually running them:
- Backend: 271 passed, 8 skipped, 0 failed
- Frontend: `npm install` + `npm run build` both pass clean

---

## 1. AI Template Writer

**New file:** `app/services/template_ai_service.py`
**Changed file:** `app/routers/templates_router.py` (two new endpoints)
**Changed file:** `frontend/src/pages/Templates.jsx` + `Templates.css`
**New test file:** `tests/test_template_ai_service.py` (12 tests)

### What it does
On the Templates page, when editing a track+channel:
- **"Generate with AI"** — writes a fresh draft from scratch, using
  situational context per track (Pre-Need vs At-Need vs Imminent vs Upsell
  vs Email-only nurture all get different tone guidance) so it doesn't write
  a salesy message for a recent-loss situation or a somber one for a
  pre-need price-lock pitch.
- **Free-text instruction box** — type something like "make this warmer,"
  "shorter," or "add urgency" and click either button:
  - "Generate with AI" treats the instruction as extra guidance on a
    from-scratch draft.
  - "Rewrite with AI" applies the instruction to whatever's currently in
    the editor (including anything you already typed/edited manually).
- For email templates, the AI writes both the subject and the body.
- Nothing is saved automatically — same as the existing manual edit flow,
  the AI just fills the editor box. You still review and click Save.

### Important design choice: no silent fallback
`draft_reply_service.py` (the Lead Detail "Suggest Reply" feature) always
returns *something* on AI failure, because an advisor mid-conversation
needs a usable draft right now. Template generation is different — there's
no safe generic substitute to silently return, and doing so would look
like a successful generation that changed nothing. So this raises a clear
error instead (`TemplateAIError` → HTTP 502 with a readable message) rather
than mimicking the old "never fails" pattern. If your OpenAI key hits the
same 429 rate limit you've seen elsewhere, you'll see that error message
directly in the Templates page instead of a silent no-op.

### Placeholders
The AI is constrained to only use the placeholders the substitution system
actually supports — `{first_name}`, `{advisor_name}`, `{tone_phrase}`,
`{booking_link}`, `{advisor_cell}` for SMS; the same minus `{tone_phrase}`
for email (email isn't touch-cadence-rotated the same way SMS is). It's
told not to invent new ones.

---

## 2. Bulk Lead Assignment — Main Leads Page

**Changed file:** `frontend/src/pages/Leads.jsx` + `Leads.css`
**No backend changes needed** — `POST /admin/leads/reassign` already
accepted a `lead_ids` array and was already fully tested for multi-lead
bulk reassignment, cross-org rejection, and unassign-to-pool. The only gap
was that the frontend had no UI wired to call it in bulk; Task 9 only
wired up the single-lead control on Lead Detail.

### What changed
- Checkbox selection is no longer limited to SMS-sendable leads (it used to
  hide the checkbox entirely for DNC/no-phone/duplicate leads, since the
  only bulk action was SMS send). Now every lead row has a checkbox, since
  bulk-assign has no such restriction.
- The bulk action bar (appears once you've selected ≥1 lead) now has an
  **"Assign to…"** button next to the existing "Send to selected," visible
  only to org_admin/super_admin (matches the role gate already used on Lead
  Detail's single-lead control).
- Clicking it opens a small panel with a dropdown of active advisors/admins
  (same `GET /admin/users` list Lead Detail already loads) and an Assign
  button.
- "Send to selected" now correctly only acts on the SMS-eligible subset of
  your selection if you've also selected some DNC/no-phone leads for
  assignment purposes — it tells you how many will be skipped before you
  send, rather than silently dropping them.

---

## 3. Bulk Lead Assignment — Lead Cleanup Page

**Changed file:** `frontend/src/pages/LeadCleanup.jsx` + `LeadCleanup.css`
**No backend changes needed** — same `/admin/leads/reassign` endpoint.

### What changed
Each potential-duplicate group card (the same cards used for merge) now
also has an advisor dropdown + **"Assign group"** button next to "Merge
Selected," admin-only. This lets you route an entire duplicate cluster to
one advisor in one click — useful since these leads are often the same
family/situation scattered across import batches, and you may want to
hand the whole cluster to one person before deciding whether to merge.

This is independent of merge — you can assign a group, decide later
whether to merge it, or merge first and assign the survivor through the
main Leads page. Assign result feedback shows inline on the card itself
(reassigned count + skipped count), separate from the merge success/error
banner at the top of the page.

---

## Suggested manual smoke test

1. Templates page → edit any track/channel → click "Generate with AI" with
   no instruction → confirm a draft appears in the editor → type an
   instruction like "shorter" → click "Rewrite with AI" → confirm it
   changes the draft accordingly → Save.
2. Leads page → select 3+ leads of mixed status (include at least one DNC
   or no-phone lead) → click "Assign to…" → pick an advisor → confirm the
   result shows the right reassigned count.
3. Lead Cleanup page → pick a duplicate group → use the group's assign
   dropdown → Assign group → confirm the inline success message appears on
   that card.
