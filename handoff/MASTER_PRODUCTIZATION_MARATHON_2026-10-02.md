# EvoSysPro — 16-Hour Master Productization Marathon (2026-10-02)

Start 08:12 CT · earliest normal completion 00:12 CT Oct 3.
Baseline: 981cfbc code / f78b46d HEAD, 6,992 passed / 27 skipped / 0 failed.

This file is updated through the day. Final suite, deploy, smoke and the
acceptance matrix are added at closeout.

---

## What shipped (commit → what it means for Mike)

| Commit | Area | Plain-English result |
|---|---|---|
| b690b2a | P0 Book a Demo | Phones see "Book a Demo" (mobile CSS hid it); every CTA tagged (`?cta=`), UTM kept; "send my information instead" requests now reach the sales pipeline with a next action; duplicate submits are refused at the database. Public-site files are in a package for Mike to upload. |
| 8c65468 | P0 Conversation Intelligence | Per-lead memory (facts vs inferences vs unknowns, with the quote and source), corrections supersede, open questions, follow-up timing, human takeover modes, next best action, quality gate on AI replies, Smart Composer, manager queue/search/insights. Deterministic — no model calls. |
| 1cdbcff | P0 Timezone · multi-instance · mobile | Workspace IANA timezone + one resolver (DST-correct day/month bounds) across every "today/overdue" screen; DB-backed duplicate guards (email send lease, login throttle, unique Twilio SID, idempotent enroll); phone prospect screen + Brain strip. |
| 62f0ae4 | Readiness · downloads | `/health/ready` (public) and owner `/god/system-health`; uploaded files with non-Latin names no longer 500 on download (7 routes). |
| de7556a | Security | Open redirect closed (email click tracker); 3 cross-tenant gaps closed (rep detail, foreign diagnostic run on a ticket, deal owner outside the brand); provisioning owner checked. |
| 32993b4 | Notifications · crash sweep | Mark all read (bounded by what you saw), keyboard-reachable bell; permanent sweep of 426 list + 218 detail GET routes × 3 roles × 3 verticals — no 5xx; "not configured" integrations now 503 not 500. |
| 05e35f0 | Screen walk fixes | Lead page gave "No phone or email" for leads that have both (now the real reason); Brain: ask-back ≠ answer, call request ≠ open question, readable unknowns; phone thread no longer hidden by the Brain strip. |
| 98fff42 | Owner panel | System Health → Platform readiness (DB latency, last run of each background job, provider configuration). Fixed: failing jobs would have read healthy. |
| 5604e18 | Accessibility | axe WCAG A/AA: labels on checkboxes/selects, muted-text contrast token, focusable scroll region. |
| 41d3ecc | Session UX | Expired session says why and returns to the same page/workspace (desktop + phone). |
| 4d34ad4 | Kill switch | `OUTBOUND_EMERGENCY_STOP=1` stops every SMS, call and email at the transport (Twilio, Resend, Graph, Retell); reads keep working. |
| 80fb6a1 | DB integrity | Owner "Data integrity — Run checks": 11 read-only checks, cross-tenant first. |
| f94b1a7 | Export security | CSV exports quote every cell and neutralise spreadsheet formulas. |
| b5465b0 | Failure handling | Twilio requests now time out (20 s default; the SDK waited forever); Google Contacts timeouts. |
| 7485439 | Multi-instance | Each background loop runs one pass per interval across all instances (DB lease; fails open). |
| 1814ed3 | Plan capacity | Reactivating a user / re-granting a revoked membership counts as a seat. |
| cfcad5c | Timezone tail | Owner "stalled or overdue" list agrees with the overdue badge for date-only dues. |
| 56e7b6d | Timezone tail | Agency screens: timestamps in the viewer's day, calendar dates as stored. |
| 88cdb3d | Mobile | Wholesale phone home: "Needs you" (offer requests, prices, approvals) + callbacks. |
| f632d72 | Observability | Every 500 carries a reference that is on the log line. |
| f2ab74c | Accessibility | Phone screens: zero axe violations. |
| 26996de | Conversation Brain | Bereavement handled by a person (not recorded as "spouse"); busy/driving = not now; seller's asking price recorded and routed to a person. |
| 7475dbc | **Regression fix** | From ~09:30 the Brain gate held every AI *text* reply for leads without an SMS-consent flag (most imported leads). Nothing wrong was sent; replies were held/flagged. Answering a text is allowed again; email never turns into texts. Found by the early full suite. |
| 9edf97a | Accessibility | Theme tokens (energy, cleaning, gold) and success green to AA; ~170 → 11 flagged elements. |
| 3a0c1fd | Conversation Brain | 12-message pipeline matrix (everyday replies answered, sensitive ones to a person); clause-aware "did we answer". |
| c7e5296 | Book a Demo | Deal owner gets an in-app notification that opens the deal (enum `DEMO_REQUEST`, additive). |
| 565fe90 · 6e23010 | Error states | Failure injection on 50+ screens: Org Settings no longer renders defaults that Save would write over real settings; Reports no longer shows fake zeros; Settings no longer hangs; Cadence/Templates/Auto-Send/CRM no longer say "empty" on a failed read; Booking Settings can't save defaults. |
| (oct2cc) | Broken in production | God Mode Opportunities/Meetings/Proposals and Org Settings CRM stages/custom fields used relative `fetch()` → read the frontend's HTML in production. Now via the API client; a guard test prevents relative API fetches. |
| ed33285 | API contract | Permanent test: every one of 1,133 `api.*` calls in the frontend matches a backend route. |
| a495f85 | Security / rate limits | Behind Cloudflare every request looked like it came from the same few edge IPs, so per-IP limits (login throttle) were shared by everyone. The real client IP (`CF-Connecting-IP`, validated) is now used. |
| 5d09089 | Observability | A background job "running" for over 6 h is shown as **stuck** (red) and degrades readiness; requests slower than 2 s are logged (path only, no query string). |
| eec4890 | Conversation Brain | Smart Composer: call requests answered "Happy to call you…", warmer / shorter / more direct styles, spouse wording. |
| 97ebc74 | Conversation Intelligence | **Conversation Queue** page (`/conversation-queue`, linked from Replies): insights, conversations by priority, search of what customers told us. |
| 2a4f1fe · b8e5026 | Duplicate sends | A double-tap no longer texts a family twice: database lease on lead + exact text (30 s) on every one-message SMS door (lead page, inbox/phone, MMS, AI approve-and-send, drafted batch, Wholesale owner text). |
| d6503b2 | Duplicate sends | Auto-send approve / approve-all now claim the row atomically (two clicks could both send); campaign sends refuse an identical repeat within 2 min. Corrects b8e5026's message, which said these were already guarded. |
| 45f81f1 · 17a7561 · fcf10a4 | Duplicate sends | Same protection on the lead page's AI voice call (two clicks rang the person twice), SMS send-batch, and both bulk email sends ("Send reviewed" and the email-only batch). Released on failure so a retry works. |
| e0442f6 | Dead code | Unreachable duplicate `/sales/video/status` removed; route-reachability test added. |
| ece4e13 · fed94db | Honest dead ends | `/onboarding` no longer shows a three-step signup form that ends in "retired" — it says accounts are set up by the team. Campaign Builder Send explains it is not switched on yet (Decision 8) instead of "Admin access required". |
| 8692f82 | Performance | The app was one 3.5 MB script (905 KB gzipped) that every screen downloaded first — Login and phones included. Owner console, Sales, Executive and Wholesale pages now load on first visit: first load 2.0 MB (520 KB gzipped, −43%). A tab left open across a deploy reloads once to pick up the new files instead of showing a blank page. |
| a978b71 · 88f0bd5 · 3fcea2b · (oct2ww) | Buttons that end in "Admin access required" | Two sweeps of every page a person can reach, against the role each route requires. Admin-only actions are now disabled or replaced with who can do them: AI Team add; AI employee pause/resume/stage; Wholesale pilot controls and paid skip-trace approval; EvoSense providers, resume, scoring, budgets (workspace and per-strategy), evaluations; Sales "release holdbacks", team-pipeline person filter, deal-value override. Admin buttons on Cadence, Lead, Leads, Compliance and Settings now follow the role in the current workspace (they used the account's role). |

## Checked and clean (no change needed)

- Reports/KPIs exclude test records (24 report/dashboard endpoints compared with and without a test lead).
- Role matrix: advisors reach no admin data or secrets through any read-only route (426 routes × advisor/admin).
- No route's query count grows with workspace size (247 routes, 10 vs 120 leads).
- Login errors don't reveal whether an email exists.
- Public booking: concurrent bookings of one slot are refused by the database (exclusion constraint) and a repeated form submit returns the first booking.
- Webhooks: Stripe events deduplicated by event id; Retell call events are applied idempotently (assignments, not increments) after signature check.
- Background sends: AI touches are claimed per conversation before the provider call; cadences use `SKIP LOCKED`.
- Write routes: every parameterless POST/PUT/PATCH/DELETE (261) with an empty body as admin and advisor — no 5xx (permanent test, outbound braked). One legacy exception: `POST /crm/contacts` (old `contacts_router`, raw SQL for a `crm_contacts` shape only some deployments have; the app uses `/crm-native/contacts`). Candidate to retire.
- Route reachability: across every registered route, only two literal paths are shadowed by an earlier route (Campaign Builder send — Decision 8; a duplicate `/sales/video/status`, dead copy). A permanent test now fails on any new one.

## Decisions only Mike can make (found today, not changed)

1. **Feature gates on send routes.** `/sms/send`, `/sms/send-batch`, email send, voice call and auto-send approve routes check no feature entitlement (`sms`, `email`, `voice` are defined but never enforced). Turning the gates on could stop a customer whose `enabled_features` list omits them (e.g. an agency workspace listing only `leads, insurance_agency`). Needs a check of each production workspace's features first.
2. **Admin dashboard / leads / imports / cleanup gates.** Same pattern: `master_dashboard`, `imports`, `lead_cleanup`, `ai_assist`, `compliance`, `calendar`, `availability`, `crm_connectors` are defined but not enforced on their routes.
3. **Monthly SMS / email allowances** appear on plan cards but are informational (`enforced: False`). Enforce, or stop showing them as limits.
4. **Brand-executive assignment** creates a membership that counts as a customer seat without a seat check — should an executive consume a customer seat?
5. **Gold primary/login button** white text is 2.6:1 contrast (brand colour). Darken the gold or use dark text.
6. **Self-service password reset** does not exist (admins reset passwords). Worth adding; it sends email, so it needs a decision on sender per brand.
7. **Wholesale skin** on phones depends on the workspace's industry/feature list; a wholesale workspace with no explicit feature list gets the generic phone home.
8. **Campaign Builder "Send" has never worked.** `POST /campaigns/builder/send` is shadowed by `POST /campaigns/{campaign_id}/send` (declared first), so advisors get "Admin access required" and admins get a validation error. Fixing the route order is one line, but it switches on a bulk SMS/email sender that has never run in production for real customers (Atlantis). Decide whether to turn it on, and for whom. A duplicate guard is already in place; a test pins today's behaviour. Until then the page tells the person "not switched on yet, nothing was sent" instead of "Admin access required" (remove that message in `CampaignBuilder.jsx` when switching it on).

9. **Rate limits are per server instance** (slowapi in-memory). With one Render instance that is exact; with more, each public form/login limit multiplies by the instance count. Sharing them needs Redis (a paid add-on) — only matters if the service is scaled out.

## Recommended next (not done today — explained)

- Hashed `/assets/*` are served `max-age=0` (each visit revalidates; 304s, not re-downloads). An `immutable` header in `render.yaml` is the standard fix; left alone because the Blueprint is live-synced and this is not worth a config sync during a marathon.

## Mike-only actions (unchanged)

- Upload the Book a Demo site package via cPanel (`evosyspro-site-book-a-demo-2026-10-02.zip`, 13 files + UPLOAD_STEPS.txt), then one TEST submission (sends real email to support@ and the address entered).
- Max Life production demo; same-phone policy; Max Life domain/hosting/legal.
- VAPID keys; Twilio numbers/campaign; Tracerfy (planned); Atlantis sender DNS / M365; Wholesale calendar OAuth.
