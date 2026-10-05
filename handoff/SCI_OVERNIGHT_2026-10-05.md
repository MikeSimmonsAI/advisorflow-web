# SCI on EvoSys Pro — overnight build (2026-10-05)

Mission: configure **Service Corporation International (SCI)** as a company
inside the existing EvoSys Pro platform — not a new CRM — so every family hears
from **Kerry Allan at their own local SCI location**
("Kerry Allan | Eastern Gate Memorial Gardens").

Start 00:07 CT. Nothing was sent to anyone. Nothing was imported into
production. No production data was changed.

---

## 1. What was built (and where)

SCI is **data**, not code. The feature is a generic *location outreach program*
any multi-location customer can use; SCI is one program row plus its locations.
Every table is **new** (additive); no existing table was altered. An
organization without a program behaves exactly as before on every path below
(one indexed lookup that returns nothing).

| Piece | Where | What it does |
|---|---|---|
| Program settings | `outreach_programs` | Program name, primary contact (Kerry Allan, Head of Sales), alert email/phone (**blank until supplied**), management recipients (configurable list, **blank**), HOT SLA (15 min, configurable 1–1440), reply instructions ("Reply to this text…", "Just reply to this email…"), staff text/email alerts **off**. |
| Location profiles | `location_profiles` (+ existing `locations`) | One per SCI location (39) + **Unassigned / Location Review**. Official name, logo, facility image, address, facility phone, website, manager, advisors, email sender name, SMS sign-off, appointment link, brand settings. Only the NAME came from the source; nothing else was invented. |
| Source records | `program_source_records` | One row per SCI Lead ID — the original row kept verbatim (`raw_json`) plus decisions: location, campaign family, contact master, link status, duplicate-review reason, data-note flags. Originals are written once and never overwritten. |
| Import runs | `program_import_runs` | Audit of every dry run / staging run with its summary. |
| Responses | `program_responses` | Every meaningful inbound reply: contact, Lead ID, location, campaign, class, summary, recommended action, NEW → OPENED → RESPONDED → ACTIVE → CLOSED timestamps, SLA due time, re-alert count. |
| Alerts | `program_alerts` | Every alert decision — in-app delivered, staff SMS/email recorded with the reason it was not sent (switched off / no recipient). |
| Campaign families | `program_campaign_families` | Veteran Planning Guide, Veteran Official, Veteran – Spanish callouts, General Survey, Life Story, Cemetery X-Sell, Cremation, Re-engagement. One template each, rendered per location. **All OFF.** Email mode per family: first touch *hosted flyer*, follow-up *attached PDF* (each: none / hosted / attached). |
| Assets / flyers | `program_assets` | Logos, facility images, approved flyers (PDF/PNG/JPEG/WebP/SVG ≤ 20 MB). Versioned, one active version per slot, preview, hosted public link (active versions only), location-specific or every-location, flyer categories (Veteran Planning Guide, General Pre-Planning, Cemetery Planning, Cremation Information, Re-engagement). Dynamic-field list recorded for the creative. |
| Campaign email touches | `program_email_touches` | One row per automated campaign email (touch 1 = first touch, touch 2 = follow-up after 4 days): sent / failed / blocked with the reason. Unique per contact + touch and claimed before the provider is called, so a family can never get the same touch twice. |
| Verification | `contact_verifications` | Verified phone / line type / ownership confidence / alternate phone, verified email / confidence, verified & current address, identity confidence, provider, date — **beside** the SCI originals. `outreach_eligible` is always false: verification is evidence, not permission. |

Code: `app/models/program_models.py`, `app/services/programs/`
(`normalize`, `setup`, `importer`, `identity`, `responses`, `promote`,
`unsubscribe`, `email_touches`), `app/routers/program_router.py`,
`app/routers/email_events_router.py`, `scripts/program_setup.py`,
`frontend/src/pages/program/ProgramCenter.jsx` (+ `.css`).

### Hooks into existing engines (reuse, not rebuild)
* **SMS send** (`sms_service.send_sms`, `send_mms`): refuses a program contact
  with no resolved location, an open Data Review, or an open Duplicate Review;
  refuses **automated** sends while the contact's campaign is OFF (a person can
  always reply by hand). `compose_body` adds the sign-off
  "- Kerry Allan, <Location>" before "Reply STOP to opt out." — preview and send
  show the same text.
* **Email send** (`email_service.send_email_to_lead`): same refusals; From
  display name "Kerry Allan | <Location>" on the existing verified EvoSys
  sender; one-click unsubscribe footer + `List-Unsubscribe` /
  `List-Unsubscribe-Post` headers.
* **Inbound SMS** (`/sms/webhook/inbound`), **inbound email** (M365 poller and
  the shared-mailbox reader): after the platform's own handling (Reply row,
  STOP/DNC, suppression), the program step attaches location/campaign, pauses
  the cadence, classifies, summarises, alerts and starts the HOT SLA. Wrapped:
  it can never lose the inbound message or fail the webhook.
* **Cadence engine** (`render_cadence_message`): a program contact's touch 1
  uses their campaign family, later touches the Re-engagement family, rendered
  for their location.
* **Background loop** `program_sla_loop` (every 2 min, one instance): re-alerts
  HOT responses untouched past the SLA — "HOT RESPONSE — NOT YET HANDLED".
* **Campaign email runner** (`programs/email_touches.py`, same loop): the SMS
  cadence engine never sent email, so this sends a program's campaign emails —
  first touch in the family's first-touch mode (no flyer / hosted link /
  attached PDF), follow-up in its follow-up mode. **Off twice over**:
  `PROGRAM_EMAIL_TOUCHES_SENDING` (deployment, default off) AND the campaign
  family switch (default off). Holds a contact who replied on any channel, has
  an open review, opted out, or whose campaign needs a flyer that is not
  uploaded yet; sends only 9:00–18:00 in the org's timezone; at most 25 per
  pass (`PROGRAM_EMAIL_TOUCH_BATCH`); goes through `send_email_to_lead`, so
  DNC / opt-out / bad address / demo / AI-paused refusals all apply. The
  Campaigns tab shows what *would* go out right now (dry run).

### Screens — "Family Service Center" (`/program`, admins only)
Dashboard (hero, location selector, Leads / Contacts / Locations / Qualified /
New Responses / Hot Responses, Work Leads / Open Responses / Hot Leads /
Appointments, Needs My Attention, Lead Pipeline, Outbound Identity, Location
Performance, Recent Conversations, Campaign Performance, Automation Health,
Delivery & Response Reporting) · Responses queue · Review queues (Data /
Location / Duplicate / Linked) · Locations · Campaigns (per-location preview,
email modes, switch on/off with typed confirmation) · Assets & Flyers ·
Settings (readiness, program settings, staging import, alert log).
Campaigns also shows "Campaign email": on/off, would-go-out-now list, sent /
blocked / failed counts.
Desktop and phone layouts. Advisors do not see it (every family's details are
on these screens).

---

## 2. Results (local, full copy of the real SCI file)

| Item | Result |
|---|---|
| Source rows / Lead IDs | **551 / 551 kept** (no blanks, no repeats) |
| Email / phone present | 551 / 551 |
| Location mapped | **549** to the 39 named locations |
| Location Review | **2** (blank location in the source) |
| Contact masters | **544** |
| Auto-linked | **7** rows (same normalised name + same phone or email) — linked, not deleted |
| Duplicate Review | **2** (same email, different names — a shared household email) |
| Source data notes | **4** flagged "SOURCE DATA NOTE DETECTED — REVIEW" (DISQUALIFIED, NOT INTERESTED, FULLY PREPLANNED); status not changed |
| Status | Qualified 294 · Open 256 · Unworked 1 |
| Campaign families | Veteran Planning Guide 306 · General Survey 180 · Veteran Official 40 · Cemetery X-Sell 21 · Spanish callouts 2 · Life Story 1 · Cremation 1 |

Dedup rule (your correction, implemented exactly): auto-link only when the
**normalised person name** matches AND the phone or the email matches.
Shared phone/email with a different name → Duplicate Review. Generational
suffixes (Jr, Sr, III) are part of the name, so father and son stay two people.

Simulated replies through the real inbound code (local, test leads only):
"Yes please, I'd like to set up a time to visit and go over prices" → **HOT**,
cadence paused, in-app alert, 15-minute SLA; past SLA it re-alerted.
Email "What does the veteran guide include…" → HOT (wants information).
"ok thanks" → LOW.

Full pipeline on a scratch copy of the local database with the real file
(`program_setup.py --create … --apply --stage --promote`): 551 staged, **544
leads + 544 contacts** created, 0 with SMS consent, 0 enrolled, 0 messages.
Email runner dry run with every campaign hypothetically on: **535 would get a
first touch across all 39 locations; 9 held** (4 Data Review, 3 Location
Review, 2 Duplicate Review). Nothing was sent.

---

## 3. Email deliverability audit (live DNS, 00:20 CT)

| Check | Status |
|---|---|
| Sending domain | `evosyspro.live` via **Resend**; Return-Path / bounce domain `send.evosyspro.live` (Amazon SES feedback MX) |
| SPF | **PASS (aligned, relaxed)** — `send.evosyspro.live: v=spf1 include:…_spfm.send.evosyspro.live ~all`. Root `evosyspro.live` SPF is `include:spf.protection.outlook.com -all` (M365 only) — fine, Resend mail uses the subdomain. |
| DKIM | **PASS** — `resend._domainkey.evosyspro.live` public key published |
| DMARC | **PRESENT** — `p=quarantine; adkim=r; aspf=r; rua=mailto:dmarc_rua@onsecureserver.net` |
| From identity | Resolved sender for EvoSys Pro customers: `support@evosyspro.live`; display name now "Kerry Allan \| <Location>" for SCI |
| Reply-To | **Not set** for SCI → replies go to `support@evosyspro.live` (MX = Microsoft 365) |
| Inbound reply mapping | Replies to that mailbox are read by the shared-mailbox reader **if the mailbox is connected** (owner connects it once in the app); the Settings tab's readiness line shows connected / not connected |
| Bounce handling | **Built tonight, OFF until configured**: signed Resend webhook `POST /email/events/resend` — delivered → delivered; hard bounce → address flagged `bad_email` (every send path then refuses it); complaint → email opt-out of record |
| Complaint handling | Same webhook |
| Suppression | Existing: DNC, `allow_email=False`, `bad_email`; SMS STOP list |
| Unsubscribe | **Built tonight**: one-click link + RFC 8058 headers on SCI email (a GET shows a button; only the POST unsubscribes, so link scanners can't) |
| Delivery tracking | Sent / failed / opened tracked before; delivered / bounced / complained once the webhook is configured. Sent is never reported as delivered. |

**EMAIL: production-capable on the existing domain, with three steps left**
(webhook secret, Reply-To / mailbox connection, one live test — see §6).

---

## 4. SMS / Twilio audit

* No SCI phone number is hard-coded anywhere. SCI sends from the organization's
  configured Twilio number (`org_twilio_phone_number`).
* The platform's toll-free number in the codebase is **+1 844-917-2171**. The
  code comment says Toll-Free Verification was approved; ROADMAP.md says it was
  submitted Aug 20 and awaiting approval. **I could not confirm the current TFV
  status** — that needs the Twilio console (no Twilio calls were made tonight).
* **Important — reply routing:** inbound texts are routed to a workspace **by
  the number they were sent to**. If SCI shares +1 844-917-2171 with another
  workspace (or a user's own number), SCI's replies land in whichever matches
  first. The Settings readiness check "sms reply routing" says which is true.
  SCI needs that number used by SCI alone, or its own number.

**PRODUCTION SMS: BLOCKED** until (a) TFV approval is confirmed in Twilio, and
(b) the number is assigned to the SCI workspace and not shared for inbound.

---

## 5. Tests

* `tests/test_outreach_program.py` (52) and `tests/test_email_events.py` (5):
  staging and the dedup rule, idempotent setup, nothing invented, identity and
  sign-off, send refusals (no location / data review / duplicate review /
  campaign off), email From name, unsubscribe, non-program orgs unchanged,
  classification (including "Can I stop by Tuesday?" = HOT and bereavement =
  HOT), HOT webhook end-to-end with no provider call, SLA re-alert once per
  window, opt-out and bad data, dashboard and location filter, advisor and
  cross-tenant isolation, settings validation, assets versioning and hosting,
  campaign preview with flyer modes, verification beside originals, location
  review, promotion, review holds, re-import keeps people's decisions, signed
  webhook (bad / stale signatures refused), bounce / complaint; email runner:
off by default, needs the campaign on, hosted-then-attached never twice,
a reply ends the sequence, reviews / opt-out hold, compliance refusal recorded
as blocked, sending hours, no-flyer hold, emergency stop claims nothing,
plan endpoint sends nothing.
* An independent review found 7 issues (linked-row review bypass, reply
  misclassification, re-import erasing review state, manual MMS blocked,
  advisor visibility, SLA double-alert path, small items). **All fixed** with
  regression tests.
* Full suite: see the final report.

---

## 6. Exactly what remains before SCI production outreach can be turned on

1. **Deploy this code** (it is on branch `sci-program`, not on `main`):
   merge to `main`; Render deploys; the new tables are created on boot
   (additive, `create_all`). Nothing changes for any existing customer.
2. **Create SCI in production** (owner account) — either:
   * App: owner console → create customer "Service Corporation International"
     (industry Funeral home / cemetery, brand EvoSys Pro), then
     `POST /god/programs/setup` with the 39 location names; or
   * Render shell: `python scripts/program_setup.py --create --platform-slug evosyspro --actor-email <owner> --source SCI_Filtered_551_Leads.csv --logo SCI_Logo.png`
     (dry run), then the same with `--apply --stage`.
3. **Review queues** in Family Service Center: 2 Location Review, 2 Duplicate
   Review, 4 Data Review — decide each.
4. **Location profiles**: addresses, websites, managers, facility images,
   location logos (we invented none).
5. **Kerry Allan**: her email (to create her account — no invite is sent until
   you choose) and alert phone/email; management recipients. Then switch staff
   alerts on.
6. **Email**: set `RESEND_WEBHOOK_SECRET` and add the webhook in Resend
   (`/email/events/resend`, events delivered / bounced / complained / opened);
   set SCI's Reply-To or connect the `support@evosyspro.live` mailbox for reply
   reading; set `PROGRAM_ASSET_BASE_URL` so hosted flyer and unsubscribe links
   use your domain.
7. **SMS**: confirm TFV approval for +1 844-917-2171; assign a number used by
   SCI alone for inbound routing.
8. **Approved flyers**: upload in Assets & Flyers and mark active; review the
   campaign copy (it is a starting point, not approved creative).
9. **Promote staged rows to live contacts/leads** (one per contact master,
   every Lead ID traceable, no consent inferred, nothing enrolled or sent):
   `python scripts/program_setup.py --org-id <SCI id> --source SCI_Filtered_551_Leads.csv --apply --stage --promote`
   — after the review queues are decided (held rows are promoted but every
   send path refuses them until cleared).
10. **One live end-to-end test** to your own phone/email: send, reply by text
    and by email, confirm the reply lands in Responses, the cadence pauses,
    and alerts fire.
11. **Email campaigns**: when ready, set `PROGRAM_EMAIL_TOUCHES_SENDING=on`
    on Render (optionally `PROGRAM_EMAIL_TOUCH_BATCH`, default 25 per pass,
    and `PROGRAM_EMAIL_FOLLOWUP_DAYS`, default 4). Nothing goes out until a
    campaign family is also switched on. Check Campaigns → "Campaign email"
    first: it lists exactly who would get what.
12. Switch on one campaign family (typed confirmation), enrol a small batch,
    watch the dashboard.

## 7. Known issues
* The supplied SCI logo PNG is cropped on the right ("Corporatio"); used as
  supplied. A full-width logo file would fix it.
* The mockup's facility photo is illustrative; no facility images were
  invented — the Outbound Identity card says "No facility image yet".
* Workspace switching: the "Family Service Center" nav entry is decided on
  page load; after switching workspace, reload to update it.
* Staff SMS/email alerts (when switched on) are sent inside the inbound
  webhook; with many recipients consider moving them to the background loop.
* The platform's top-bar Location selector and the Family Service Center
  location selector are separate controls.
