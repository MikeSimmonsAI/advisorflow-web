# VOLT consumer leads (DEV-4/5/6): rollout, compliance flags, rollback

Design references read and used: `docs/volt/design/volt_leads_v3.html` (funnel) and
`volt_leads_backend_demo.html` (dashboard). Both are committed and readable.

## What shipped
- Tables (create_all, new only): `volt_lead_settings`, `volt_leads`, `volt_lead_events`, `volt_lead_grants`.
- Public: `GET /volt/public/volt-marketing/leads/config`, `POST .../leads` (10/min per address, validated, consent + version stored, dedup on org + phone/email, identical response for repeats). Page: `/volt/check-coverage`.
- Staff: `/volt/ops/{org}/leads*` (list/search, detail + history, status, assign, report, owner settings, owner grants). Tab: Consumer leads.
- Authorization (`app/services/volt/leads.py::scope`): owner and granted staff see all; other org staff see only assigned leads; viewers, ACA-applicant logins and other tenants get 403/404.

## Compliance-review flags (intake is OFF until cleared)
1. Consent wording `CONSENT_TEXT` / `CONSENT_VERSION` (TCPA, state rules, autodialer language).
2. `$0 premium` caveat `ELIGIBILITY_CAVEAT` and funnel copy; not a government site.
3. Privacy notice is not yet linked from the funnel.
4. Licensed-agent routing: assignment is manual by owner or granted staff; no auto-routing.
5. Turning intake on requires the owner, a confirmation and a recorded approver note (audited).

## Rollout
Merge -> deploy (create_all makes the tables) -> owner completes review -> owner turns intake on in Consumer leads.
## Rollback
Turn intake off in the tab (immediate). Code rollback is a revert; the volt_lead* tables are additive and can stay.
