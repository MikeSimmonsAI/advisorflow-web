# DEV-1: configurable applicant CSV exports and ACA-only staff access

**Status: not implemented. No code was changed. This needs a decision from the owner.**

## Why nothing was built
The request cannot be delivered safely using only the files a VOLT dev request may change
(`app/services/volt/`, `app/routers/volt_router.py`, `frontend/src/pages/volt/`,
`frontend/public/brands/volt/`, `tests/test_volt*.py`, `docs/volt/`).

1. **New storage is required, and it lives outside the allowed scope.**
   Saved export presets, owner-controlled per-field view/export authorizations and a per-person
   active/inactive toggle all need new tables or columns. VOLT's models are in
   `app/models/volt_models.py`, plus a migration. Neither is an allowed path.
   Squeezing this into existing columns (for example the free-text `VoltProgram` fields) would
   be fragile and unsafe for something that gates personal data.
2. **A new restricted role and project-level scoping touch the shared auth/permission layer.**
   Today `C.require_staff` (`app/services/volt/common.py`) and `Membership` roles
   (`org_admin`, `manager`, `viewer`) are organization-wide. "ACA Healthcare applicants only,
   and nothing else" must be enforced on every list, detail, file, export and alternate route.
   That includes the existing `/ops/{org}/export.csv`, `/applications`, `/applications/{id}`
   and the `talent` CSV. It needs a new role and a rule shared across those routes, which goes
   beyond the VOLT-only files. A partial version risks leaking other campaigns' data.
3. **Granting or inviting Jstewyyy5045@gmail.com is an account and membership action.**
   It creates a login or membership and may send an invitation. That is outside this scope and
   must not happen from a dev request. The owner should do it from VOLT People & Access once a
   restricted role exists.
4. **The requested pre-checks cannot be done from this runner.** It has no access to live GitHub
   state (current main, open VOLT PRs, issue #535 comments), deployment status,
   `VOLT-Marketing-Complete-Project-Handoff.md` or `volt-recruitment-demo.html`. I did not
   look for or invent their contents; they are not in this checkout.

## What I'd suggest
Split into an owner-approved change that includes the non-VOLT parts, in this order:
1. Add models and migration: `volt_export_preset`, `volt_field_grant` (user, field, can_view,
   can_export), and a restricted role or scope column (for example `project_scope = "aca"`)
   on the VOLT access record.
2. Add a server-side check in `require_staff` that denies by default for the restricted role
   and filters every query by project. Add a field-level filter on every response and export.
3. Build the export builder (ordering, filters, UTF-8 and CSV-formula-safe escaping, audit
   events on export and on access changes), reusing `reports.csv_text` and `C.audit`.
4. Tests for business isolation, denied fields, a disabled user, filtered exports and no data leakage.

Once the owner approves widening the scope (models, migration, shared permission code), resubmit
and this can be done in one PR. Nothing was merged, pushed, deployed or sent.
