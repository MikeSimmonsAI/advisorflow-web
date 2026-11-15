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
- **Only their assigned leads:** other VOLT staff.
- **Nothing (403):** viewers, ACA-applicant logins, other businesses, signed-out visitors.
- **Who can be an agent:** today, agents must be VOLT staff (org admin or manager), and VOLT staff also see the recruiting screens. A lead-only agent role is a known follow-up.

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
- **Intake:** turn it off on the Consumer Leads tab. Takes effect immediately.
- **Code:** revert the merge commit. The `volt_lead*` tables and the added columns are additive and nullable, so they can stay.
- **Data:** no destructive data rollback is ever automatic.
