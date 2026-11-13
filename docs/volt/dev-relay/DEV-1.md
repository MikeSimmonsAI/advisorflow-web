# DEV-1: configurable applicant CSV exports and ACA-only staff access

**Status: no code changes made. This request needs work outside the VOLT-only scope, so it is held for the owner.**

## What I checked
- Repository code on branch `volt-dev/dev-1-a2`, based on `main` at `cfc73089`: `app/services/volt/{access,common,hiring,reports,talent}.py`, `app/routers/volt_router.py` and `app/models/volt_models.py`.
- The existing exports are `GET /{org_id}/export.csv` (`reports.csv_text`) and `GET /{org_id}/talent-pool.csv`.
- Not checked, because the workflow session has no GitHub or deployment access: open VOLT pull requests, issue #535 discussion, deployment status. `VOLT-Marketing-Complete-Project-Handoff.md` and `volt-recruitment-demo.html` are not in the repository, so I did not invent their contents.

## Why this cannot be done inside the allowed scope
1. **New persistent storage is required, and models are out of scope.** Owner-controlled per-field export/view authorizations, saved export presets, and the per-user ACA-only scope plus active/inactive toggle each need new tables or columns. Those live in `app/models/volt_models.py` and need a database migration. Neither is in the allowed paths. Storing this in `VoltAudit` rows or other overloaded fields would be fragile and not a safe place for access-control state.
2. **The restricted ACA-only role touches platform authorization.** Today `common.staff_access` maps a workspace membership role (`org_admin`, `manager`, `viewer`, ...) to access across the whole VOLT organization, and that role comes from `workspace_access` and the platform Membership flow. A role limited to one project (ACA) must be enforced consistently in `workspace_access`, auth, and session handling, which are out of scope. Adding it only in the VOLT router would leave alternate routes open, which the request forbids.
3. **Inviting or granting `Jstewyyy5045@gmail.com` creates or changes a platform login.** That is the God -> Manage Access / auth flow, not VOLT Marketing files. A deployment setting or a migration would also be needed, and I must not send messages or touch production.
4. **Sensitive-data policy decisions need the owner.** Date of birth and ACA health-adjacent answers need explicit entitlements. The owner should confirm which fields are lawfully collected and can be exported (stored fields today: name, email, phone, city/state/ZIP, DOB, application status/dates, recruiter, project, session/interview times; there is no stored street address or free-text notes field on the applicant).

## What is needed to proceed (suggested split)
- **Step A (outside VOLT scope, owner-approved):** add models/migration for `volt_export_grants` (user, org, field, can_view, can_export), `volt_export_presets` (owner, org, name, fields JSON, order, filters), and a per-user VOLT scope (allowed project keys + active flag); extend `workspace_access`/auth so a scoped role cannot read other projects; add the grant/invite for the named email.
- **Step B (in VOLT scope, after A):** an export-field registry built only from stored columns, a `/export-builder` endpoint family in `volt_router.py` with server-side project and field checks on lists, details, downloads and presets, CSV escaping with formula-injection guarding and UTF-8 BOM, audit events for every export and access change, and tests for business isolation, denied fields, disabled user, filtered exports and no leakage.

Please reply with approval to widen the scope for Step A, or tell me which part to do inside the current scope instead (for example Step B with owner-only field grants kept in a clearly designed storage option you approve).

Nothing was merged, deployed, pushed or sent.
