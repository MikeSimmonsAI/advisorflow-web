# EvoSys Pro — Express Overhaul + Live Acceptance (2026-09-28 22:37 CT → 2026-09-29)

Rollback tag for this run: `pre-express-20260928` (= 7ab7481). Earlier tag: `pre-overnight-20260928` (= eb493fe).
Commits this run: 083c92a (BookaBoost Wholesale offer), 2430f2e (P0s + express streams), 4211564 (second pass:
brand bleed, bulk delete message), 054d77a (God Mode theme refresh).
Revert: `git revert 054d77a 4211564 2430f2e 083c92a` + push (Render auto-deploys). Schema: new tables only
(phone_numbers, telephony_user_settings, inbound_call_logs, voicemails, org_voicemail_drops,
skiptrace_provider_products, skiptrace_cost_estimates) + 8 additive voice_calls columns — harmless if left.

## Security
Render API keys: 2 distinct keys (rnd_Ow…S8A, rnd_9Q…NdE) found in PUBLIC git history of
MikeSimmonsAI/advisorflow-web (deploy scripts, Jul 10 – Sep 10; removed from files Aug 25 / Sep 10). Not in current
tree or frontend. Owner rotated/revoked keys 2026-09-28 ~22:49 CT. Status: CONTAINED. Optional: history rewrite.
New key belongs only in the RENDER_API_KEY user env var (deploy.ps1 reads it); auto-deploy does not need it.

## Root causes found
- BookaBoost Wholesale nav: the shell offered a product only if the brand had a commercial name
  (brand_config.PRODUCT_NAMES has only evosyspro) → entitled BookaBoost orgs could use Wholesale by URL but never
  saw it. Now /branding/org sends platform.offered.wholesale from the entitlement brand layer (+ org grant).
- "Unable to reach the server" on ANY 500: Starlette's Exception handler runs outside CORSMiddleware; the 500 had
  no CORS headers and the browser hid it. New ServerErrorInsideCORSMiddleware (app/main.py).
- Lead delete: db.delete relied on DB cascades; voice_calls / pipeline_conversations (NOT NULL, no cascade) and
  other refs raised IntegrityError. app/services/lead_deletion.py derives policy from the schema: nullable refs
  detached, NOT NULL lead-owned rows removed (recursively), wholesale-deal sellers refused (409), DNC/opt-out
  numbers written to suppression first, audit records what happened.
- Import step 6→7: step 6 had its own commit-status list (drifted from the server) and "Keep staged" never
  advanced. Server now returns commit_gate {can_commit, reason}; every step-6 choice goes to step 7; failed
  commits retry idempotently.
- EvoSense dead ends: no manual owner/contact path in the UI, no dismiss, promotion without a conversation
  created a deal with no seller. Now: per-property actions/readiness/summary, manual owner + contact, dismiss with
  reason, inbox next steps, promotion attaches the seller via wholesale_service.attach_seller.
- Dark slabs: shared .panel/.stat-card still painted navy glass; brand skin blocks forced dark cards.
- Brand bleed: applyBrandingCSS never cleared previous inline colours; God pages kept the last customer's theme.

## Telephony (provider-config dependent for real calls)
Number resolution location → org → brand → platform (+ legacy org/user columns). No hard-coded sender. Inbound
calls resolved by the CALLED number and scoped to that org. Inbound voicemail (record → store → task → thread;
audio via authenticated proxy). Voicemail drop only with an approved message + AMD. Human click-to-call bridge
(user phone verified by spoken code; DNC/suppression/lead-number refusal; 5/min, 100/day). Browser calling not
built (no Voice SDK token endpoint). To go live: assign a voice number (God > Org > Operations > Phone numbers),
store the org Twilio account, set API_BASE_URL, point the Twilio number's Voice webhook to
POST {API_BASE_URL}/voice/inbound, users verify callback phones, approve a voicemail message if drops are wanted.
NOTE: the old literal +14692241155 is no longer used; if an org relied on it for bulk voice, assign it as a
PhoneNumber record (voice is currently OFF platform-wide).

## Skip-trace
Current: Tracerfy instant lookup (5 credits × $0.02 = $0.10/hit, misses free), evaluation-only.
Lowest total at pilot volume: Tracerfy Normal batch (~$0.02/record; 250 ≈ $5, 500 ≈ $10, worst case all
charged), then DataSkip (~$0.04). Estimates + dedupe + typed approval gate built; no vendor calls. Batch sender
not built (needs Tracerfy confirmation of batch API + credentials). Derrick's ~$0.01 provider: config slot
SKIPTRACE_CUSTOM_PRODUCT (disabled).

## Workspace entitlements
Header X-Workspace-Location; single-location users implicit; multi-location = most restrictive unless selected;
enforced in require_feature, nav_features, /branding/org. Service-level org_has_feature callers without an acting
user remain org-level (listed in /tmp notes; see XD summary in final report).

## Live acceptance (production, sandbox/test records only)
- BookaBoost Fiber Cartel: Wholesale entry visible (god view), route + API 200; org-level disable → removed from
  customer allow-list (access diagnostic shows Dlo denied) → reset → restored, history recorded.
- Lead delete (ZZ Launch Verify A sandbox lead): deleted from Leads UI; gone from list/summary/search; contact kept.
- Import (ZZX-20260929-001): upload→…→step 7 (3 created, 1 merged, 0 leads) → ledger → rollback executed.
- EvoSense (ZZ A sandbox property 7700 ZZTEST Acceptance Ln): owner → contact (manual, NOT CONFIGURED lookup
  shown) → promote → deal 6ada9d83… with seller; consent false; calling blocked with reasons.
- Visual: Workforce Command, My AI Workforce, EVO Overview/Leads, BookaBoost Leads, Atlantis Replies light;
  Demo Sites no mojibake; telephony Operations section truthful; skip-trace panel live.

## Remaining / owner items
- Customer-user (non-god) live view of BookaBoost Wholesale hide/deny on app.bookaboost.live: needs a signed-in
  customer session in the browser pane (proven via entitlement payload + access diagnostic instead).
- "Powered by EvoSense" shows in every brand incl. BookaBoost — owner decision.
- Derrick user invite (real email).

## 2026-09-29 afternoon — system-wide delete
Owner: "I just can't delete any contact" / "fix that delete lead option as well, system wide".
- Contacts: `DELETE /intake/contacts/{id}` and `POST /intake/contacts/bulk-delete {ids}` (≤500), acting workspace
  only (foreign id → 404 / not_found), capability lead_import_manage (org admins by role), observation mode refused.
  app/services/contact_deletion.py reuses lead_deletion's schema-derived clearing: alt source ids removed, FK refs
  detached, non-FK refs (leads.org_contact_id, inbound_call_logs, voicemails) nulled; a linked lead is KEPT; DNC /
  opted-out / suppressed numbers written to suppression first; audit `contact.delete`. Import rollback afterwards
  treats the contact as "already gone".
- UI: Contacts page checkboxes + "Delete contacts" bar with a result line; "Delete contact" in the drawer.
  Energy/Atlantis Leads workspace: checkboxes + bulk delete + "Delete lead" in the drawer. Lead detail page: Delete.
  All lead screens share frontend/src/utils/deleteRecords.js (same confirm, same "Deleted X of Y… reason" message).
- Tests: tests/test_contact_delete.py (7), tests/frontend/deleteRecords.test.mjs.

## 2026-09-29 afternoon — AI drafts + bulk promote
- AI drafts (cba9860, 2253565): drafts used the SENDER's home org (Atlantis lead introduced as "EVO Integrated
  Solutions LLC"), an account label as a person, and no business context. app/services/draft_context.py now
  resolves the LEAD's org, industry, services, tagline, stage label, lead facts and the composer's booking type;
  non-person accounts sign "The <Business> Team"; funeral wording only for funeral orgs; no invented free
  offers/savings, no stock openers. Live check on the Guillermo Perez (Atlantis) draft: Atlantis Light & Power,
  Energy Rate Review, Rio Grande Treats. Nothing sent.
- Bulk promote (e437922): POST /intake/contacts/bulk-promote (200/request; same rules as single promote; skips DNC,
  already-a-lead, no phone + no working email, with counts), GET /intake/contacts/ids (select all matching, cap
  25,000). Contacts page: lead-stage picker, Promote to leads, "Select all N matching", chunked progress.
  Live: Atlantis select-all = 10,861 (2,799 with phone), matches KPIs; sandbox ZZTEST Keep Sandbox promoted via UI
  (lead fd40a2bd…, status new, consent untouched). Mike's real contacts NOT promoted — his call.

## 2026-09-29 late afternoon — email send hardening + P0 inbound reply sync
- Send with attachment crashed AFTER the provider accepted (NameError acting_advisor) → mail delivered, nothing
  recorded, booking button ignored (4db5441). Both one-off paths now share _send_custom_email; e1ebc1e added:
  15-min duplicate guard (409 duplicate_send; composer confirms), send_record.record_after_send (post-send save
  retried, never a 500; also send_sms/send_mms), M365 mailbox used when the lead's advisor connected it, text/plain
  part, truthful composer sender line, POST /email/record-sent (managers; sends nothing; audited). Joshua's 2:29 PM
  email recorded (send_source=recorded). Funeral "What they have" panel + hint only in funeral workspaces (50afd5e).
- Deliverability (evosyspro.live): SPF root -all + outlook; Resend return-path send.evosyspro.live (SES);
  DKIM resend._domainkey d=evosyspro.live; DMARC p=quarantine relaxed → aligned pass. No tracking/unsubscribe on
  one-off mail. Spam = content/brand mismatch (Atlantis brand from EvoSys support address, image flyer, HTML-only).
  Long-term: verify an Atlantis sending domain (DNS — owner), or connect M365 per advisor.
- Inbound (ec1b563 → 986f999): nothing read support@evosyspro.live; the advisor poller only read personal M365
  inboxes and reported Graph failures as "checked 0". New inbound_mailboxes + inbound_mailbox_messages; owner
  connected support@evosyspro.live (God → Email Diagnostics, Microsoft sign-in, delegated Mail.Read). Poll every
  cron run from a cursor, ALL folders except Sent/Drafts/Outbox/Deleted (mailbox rule files mail into "Careers /
  Indeed Applicants"), ImmutableId + internetMessageId dedupe. Routing: workspaces whose sending identity is the
  mailbox; sender → lead (most recently emailed if shared; else ambiguous, not attached). Reply + status replied +
  REPLY_RECEIVED notification; AI only if a conversation/pipeline already runs. Probe endpoint (read-only folders/
  newest). Joshua's reply attached automatically by the cron at 21:21 UTC.
- Controlled test: lead "ZZTEST Reply Loop" (Atlantis, c9c0eaa7…, simmonsmj242@gmail.com); email 2f5130c4… sent
  via provider; awaiting owner reply to confirm the loop. Delete the ZZTEST lead afterwards.
