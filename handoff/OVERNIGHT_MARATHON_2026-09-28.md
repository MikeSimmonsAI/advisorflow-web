# Overnight marathon — 2026-09-27 23:17 CT → 2026-09-28 morning

Scope: eliminate existing backlog/defects/debt. No SMS, no email, no Twilio, no Tracerfy, no real
Atlantis import, $0 spend. Commits on `main` (see bottom for the final list).

## 1. System Fixes SS1–SS10 (audited against code + production before fixing)

| ID | Verdict | What changed |
|---|---|---|
| SS1 lead limit | ALREADY FIXED (manual create refuses with a clear 402; external arrivals are held; universal intake holds) | Remaining "error" is configuration: a Custom-tier org with no recorded entitlements falls back to the entry tier's lead ceiling. Record entitlements via PUT /god/billing/customers/{id}/entitlements. Test leads no longer count toward plan usage. |
| SS2 custom plan | REPRODUCED (UI) + NEEDS MIKE (policy) | Plan-switch dropdown lists only purchasable plans; note explains Custom is configured by recording entitlements. Whether operators may switch an existing Stripe subscription to Custom (and at what price) is a business decision. |
| SS3 same-day activity | REPRODUCED → FIXED | Activity page "Today" summary from /activity/today. |
| SS4 AI-email reporting | REPRODUCED → FIXED (visibility) | /activity/sent returns send_source, send_source_label, ai_generated; "AI" badge. Few AI-sent emails exist because bulk AI email is gated off (OUTBOUND_EMAIL_BULK_AI) and background AI is disabled — by design. |
| SS5 full history | REPRODUCED → FIXED | "Load older activity" paging on Lead Detail; backend cursor no longer skips rows of a busy channel; /timeline scoping aligned with /history (never wider). |
| SS6 cadence history | REPRODUCED → FIXED | Lead Detail "Cadence" panel from /cadence/lead/{id}/history (per-touch outcomes, reasons, provider errors); fake "of 8" removed. |
| SS7 SMS/Voice queues | REPRODUCED → FIXED | My Work "Queues" tabs (SMS / Voice / Email / Follow-up) from the derived queues, with "show excluded + why". |
| SS8 AI Hub | PARTIAL + NEEDS MIKE | Truthful touch counts. The funnel redesign (lead → conversation → appointment view) and whether the Send Queue tab should move off the superseded auto_send_queue are product decisions. |
| SS9 vanity demo links | REPRODUCED → FIXED | Slug inherited on republish, cleared on retire, refused publish no longer revokes the live version; "Link name" field in the Demo Sites panel. |
| SS10 bulk AI compose | REPRODUCED → FIXED | Fallback/failed generations are never sent or drafted; non-email auto-send rejected with a clear message (nothing sent); gate refusals reported as "disabled"; drafts no longer claim to be "queued". Bulk AI SMS needs its own switch — Mike's decision. |
| PF1 Leads table | FIXED | Compact rows, nowrap/ellipsis, right-aligned actions, horizontal scroll instead of clipping, mobile sizing. CSS only. |
| PF2 / NEW product catalog | FIXED | Products per industry template + per-org override (GET/PUT/DELETE /settings/products); generic orgs never inherit insurance; all 23 legacy keys preserved and still render on existing case files. |

## 2. Reliability
- Cadence runner: one failing enrollment is rolled back, logged and counted; the others run; the pass then fails loudly (CadencePassErrors) so the ledger stays truthful.
- Claim-before-send (no double send on a failed commit / overlapping deploy): review-request SMS, AI conversation touches, sales appointment reminders.
- Per-item rollback isolation: email poller (per email, per advisor), support intelligence (per step/platform), EvoSense contactability backfill, post-appointment follow-ups, wholesale exception sweep (org lookup inside the try).
- Job monitor: every job has an expected interval; GET /god/job-runs/latest adds `stale`, `expected_every_minutes`, `stale_jobs`; System Health shows STALE (e.g. ai_conversation_cron has no Render service since 2026-09-19).
- Production: cadence_loop healthy since the 69d44c8 fix; 244 parameterless GET endpoints returned 0 × 5xx.

## 3. Security / tenant isolation (fixed, with tests)
- Tier definitions: super_admin could read/create/update/delete/seed/reset another brand's org tiers → scoped with load_org_in_scope.
- EvoSense admin actions used the account-global role, not the role in the selected workspace → workspace-aware.
- Setup links: super_admin could mint for any user; NULL-org match → scoped.
- Audit log, cadence templates, settings (appointment types/products) queried the home org while authorizing against the selected workspace → active workspace.
- Settings admin profile (PATCH/GET /settings/admin/profile/{id}): any super_admin could edit ANY user's email/role/Twilio creds on the box (account takeover) → load_user_in_scope; org_admin cannot edit elevated accounts.

- Launch experience + commercial onboarding: `_actor_platform_ids` counted `users.platform_id` for ANY role, but customer_activation stamps it on every customer org_admin/advisor/viewer → a customer's org_admin could preview other customers on the same brand, read their commercial onboarding/overrides, and rewrite brand-level and other customers' launch config. Now super_admin only (tests/test_org_path_scope.py, 26).
- Every other `{org_id}` path route audited: admin (load_org_in_scope), god routes (require_god), sales catalog (brand check), executive (portfolio check), CRM inbound (per-org key in secure mode) — SAFE. Orgs still in legacy CRM-inbound mode (`crm_inbound_secure_required=False`) accept unauthenticated posts by design; God can close per org.
- Proposal files (public capability URL used by the no-login portal): no longer served once the proposal is deleted; `private` cache + `nosniff`; filename can't inject into the header. Moving to signed/portal-token URLs is a design decision.
- Verified already fixed: Stripe webhook has signature verification + idempotency (billing_webhook). CORS localhost origins are low risk (bearer tokens only, no cookies) — left for Mike's local dev.

- Destructive routes (75 audited): BUG fixed — "block date range + cancel existing" cancelled the ADMIN's bookings instead of the target advisor's; advisor lookup now uses the same workspace as the admin check. Audit entries added for tier reset-defaults, demo wipe, CRM contact delete, import-batch delete, cadence touches replace, CRM connection delete, stage/appointment-type/product resets. Lead delete / duplicate bulk-delete now audit under the rows' org and use the workspace-aware admin check (is_manager_here); DELETE /leads/import-batches deletes in the active workspace and no longer silently rolls back half its work (savepoints). tests/test_destructive_route_audit.py (22).
- FOUND, NOT FIXED (Mike): availability cancellation texts never go out — `_cancel_bookings_in_range` imports a non-existent `send_raw_sms` and swallows the error. Fixing it would start sending real SMS.

## 4. Test records never contacted / never counted
- Guards: send_sms/send_mms/send_batch, compliance preflight (email + auto-send), start_cadence + enrolled-test stop, cadence start-all/start-batch, campaigns, AI conversation start + touch loop, auto-send proactive scan, pipeline launch, voice eligibility. A deliberate one-to-one MANUAL send by a person who sees the TEST badge is still allowed (test_records rule).
- Plan usage and capacity holds exclude test leads.
- Reports (/reports/*), activity reporting, executive portfolio/command center, EvoSense command center (real workspaces) exclude test records.

## 5. Universal Intake / identity
- DNC bypass closed: an explicit capture no longer creates a contactable Lead for a DNC contact.
- SMS consent mirrors onto a Lead only when the lead's phone IS the consenting number; a lead phone change clears consent (+evidence fields); consent is never transferred.
- EvoSense and /sell fallbacks link the created Lead to its contact (no orphan duplicates); DNC carried.
- CRM inbound / social / fiber dedupe on normalized phone + lowercased email; social webhook NameError (lead_capacity import) fixed.
- Audit result: 8 inbound paths still create Leads outside canonical intake (public_capture website forms, public booking, voice/scheduling, social, fiber, CRM push, scraper, manual create). Converging them is the "three contact stores" architecture decision (Decisions Needed) — deferred to Mike.
- /sell legacy direct-Lead fallback kept: it only runs on an intake exception and is pinned by tests; removing it needs a decision (retryable 5xx vs queue).

## 6. Performance
- Batched loads + limits: exceptions queue (limit/offset, subjects in one query per type), calendar events (SQL time window), pipeline flagged/conversations, auto-send queue/history, timeline senders, contacts (limit/offset), availability upcoming; wholesale deals `band` filter paging fixed; timeline email-sender crash fixed.

## 7. UX / responsive
- Context banner readable on phones. No page-level horizontal overflow on 16 audited routes at 375px; tables scroll inside their panels. No links to undeclared routes found.

## 8. Commits (main)
| Commit | Contents | Full suite (Windows) |
|---|---|---|
| 519fa02 | Batch 1: SS fixes, product catalog, Leads table, cadence isolation, job staleness, security scope, performance | 6165 passed / 0 failed |
| aa8bc28 | Batch 2: test-record guards, intake consent/DNC, KPI/report hygiene, admin-profile takeover fix, post-appointment claim, timeline cursor, phone banner | 6223 passed / 0 failed |
| e6ae362 | Batch 3: launch-experience/commercial scope, destructive-route audit + availability cancel fix, proposal-file hardening | 6275 passed / 0 failed (FINAL) |

No migrations beyond one additive column (`organizations.products` TEXT via auto_migrate). No production data changed. No SMS/email sent, no provider calls, $0.

## 9. Needs Mike
SS2 Custom-plan policy · SS8 AI Hub funnel + Send Queue source · bulk AI SMS switch · ai_conversation_cron service (stale since 9/19) · /sell fallback replacement · availability cancellation texts (broken import; fixing sends SMS) · proposal files signed URLs · legacy CRM-inbound orgs · CORS localhost removal · 8 non-canonical inbound Lead paths (three contact stores).
