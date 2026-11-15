# VOLT Consumer Leads: rollout, compliance flags, timestamps, rollback

Consumer Leads is a campaign **inside VOLT Marketing**. It is separate from recruiting applicants and from HIP. It has its own tables, campaign card, consumer page, dashboard, settings, reporting and Development entry point.

## Released
- **DEV-6, PR #611** (tested commit `013782d`, merged as `1847242`, Nov 14, 2026, 5:18 PM CST):
  - Tables: `volt_lead_settings`, `volt_leads`, `volt_lead_events`, `volt_lead_grants`.
  - Public endpoints: `GET /volt/public/volt-marketing/leads/config` and `POST .../leads`.
  - Staff endpoints: `/volt/ops/{org}/leads*`.
  - Consumer page: `/volt/check-coverage`.
- **Completion PR (this change):**
  - Campaign card on VOLT Operations Overview.
  - Consumer page: the $0 message, plain explanations, required disclaimers, and a Medicare note for 65+.
  - New intake fields: state, income range, contact preference, best time.
  - Attribution: full UTM capture plus referrer, sorted into channels (Meta, Google, organic, direct, referral, other).
  - New lead events: call attempted, contact completed, appointment set/changed, enrollment recorded.
  - Reporting by channel and by agent.
  - Times shown in Central (CST/CDT).
  - A Development button that tags requests with the campaign.
  - VOLT's two named operators manage every lead.

## Who sees what (enforced in `app/services/volt/leads.py::scope`)
- **All leads:**
  - the owner (god_admin)
  - VOLT's named development operators (`VOLT_DEV_PRINCIPALS` plus active VOLT admin, re-checked every call; this is how D'Angelo manages the campaign; no identity is hard-coded)
  - VOLT staff the owner has granted full access
- **Managers (every lead):** people the OWNER adds as Consumer Leads managers.
- **Agents (only their assigned leads):** people the owner or a named operator adds as Consumer Leads agents. An agent only needs to be an active member of the VOLT organization; recruiting roles are not needed and give nothing here.
- **Nothing (403):** everyone else, including VOLT recruiting staff with no Consumer Leads role, viewers, ACA-applicant logins, other businesses, signed-out visitors.
- Since 2026-11-14 (#571) recruiting roles no longer open Consumer Leads. Production had no leads and no grants when this changed. Grants that existed before default to "manager", keeping their earlier meaning.

## Workspace (2026-11-14)
- **Own card:** Consumer Leads has its own Program Access card (`consumer_leads_contexts` in `/auth/my-contexts`), shown only to people `leads.access()` allows. It covers only the `volt-marketing` program, whose customer website is `/volt/check-coverage`.
- **Back office:** `/consumer-leads/<org>`. Sections: Overview, Leads, Lead Sources, Funnels & Campaigns, Assignments, Responses & Follow-Up, Reporting, Development, Settings. The VOLT Operations `leads` tab now redirects there.
- **Preview:** `/consumer-leads/<org>/preview` shows the customer website signed in. Submissions go to `POST /volt/ops/<org>/leads/preview` (managers, operators and the owner only) and are saved with `is_test = true`. Every query filters on `is_test`, so test rows never appear in real lists, reports or the card. "Remove all test leads" deletes test rows only.
- **Public website:** always visible. With intake off it shows a notice, turns off the last step's boxes and the send button, and the public POST still answers 503.

## Lead sources (`app/services/volt/lead_sources.py`)
- **Working now:** custom webhook (HMAC-SHA256 over `<timestamp>.<body>`, 5-minute window), Google Ads lead-form webhook (`google_key`), and CSV import (a consent statement is required).
- **Waiting for account connection (cannot be turned on):** Facebook and Instagram Lead Ads (an approved Meta app with `leads_retrieval`, plus Page authorization), and TikTok Instant Forms (an approved TikTok for Business app, plus the advertiser's authorization).
- **Real sources** accept leads only while public intake is on. **Test sources** write test data only.
- **Deliveries:** idempotent per (source, external id). The payload is cleared once processed. Bad data waits for a mapping fix and Retry. Temporary errors are retried by the VOLT watch loop with backoff (up to 5 tries).

## Responses & Follow-Up
- Response-time monitoring, the first-contact queue, follow-ups due, and upcoming appointments are live.
- Staff alerts are prepared but off.
- Customer texts, emails and calls are OFF until outreach rules and compliance are final.

## Automatic release policy (`devdispatch.auto_release_check`)
A relay job is released without a person only when all of these hold:
- tests passed at the exact commit;
- GitHub's required checks and rules pass (never bypassed);
- every file is `ConsumerLeadsWorkspace.jsx`, `VoltLeads.jsx`, a VOLT `.css`, a VOLT brand image, `docs/volt/`, or a VOLT test that only adds lines;
- the changed lines mention no intake, consent, settings/grants, secrets/tokens, outreach, test-partition or preview code.

Anything else waits for a person. The job timeline records `auto_release` (with every check passed) or `auto_release_skipped` (with the reason). Kill switch: `VOLT_DEV_AUTO_RELEASE=off`.

## Compliance review flags (public intake stays OFF until cleared)
1. Consent wording: `CONSENT_TEXT` / `CONSENT_VERSION` (TCPA, state rules, autodialer language).
2. Page copy: `PAGE` (headline, benefits, disclaimers, Medicare note) and `ELIGIBILITY_CAVEAT`.
3. A privacy notice link on the consumer page (not yet written).
4. Licensed-agent routing: assignment is manual by the owner or an operator; there is no auto-routing.
5. Turning intake on requires the owner, a confirmation, and a recorded approver note. All of it is audited.

## Timestamps
- **How they're stored:** every lead and event time is UTC. Columns are naive `DateTime`, written with `datetime.utcnow()` (that is how the rest of the platform stores times).
- **How the API sends them:** with an explicit `+00:00` (`leads._utc`), so browsers never read them as local time.
- **How screens show them:** Central time with CST/CDT (`fmtCT`).
- **Events are append-only:** `volt_lead_events` rows are never edited. Old and new values are kept on each change.
- **Plan for the naive columns** (not done here, because it touches shared platform conventions):
  1. Add `*_tz TIMESTAMPTZ` columns.
  2. Back-fill them with `col AT TIME ZONE 'UTC'`.
  3. Switch reads.
  4. Drop the old columns after a release cycle.

  Values never change meaning, because they are already UTC.

## Rollback
- **Intake:** turn it off in Consumer Leads → Settings. Takes effect immediately; real sources stop accepting too.
- **Code:** revert the merge commit. The `volt_lead*` tables and the added columns are additive and nullable, so they can stay.
- **Data:** no destructive data rollback is ever automatic.
