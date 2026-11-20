# DEV-1: configurable applicant CSV exports and ACA-only staff access

**Result: no code changes made.** The request is already implemented on `main`
by commit 86d98658 (#594, "Implement VOLT applicant export presets and ACA-only
staff access"). Making a second implementation would duplicate it and risk
broadening permissions.

## What #594 already covers
- Owner-managed ACA-only grant (`PUT/GET /api/volt/ops/{org}/aca-access`) with
  an active/inactive toggle and separate view-field and export-field lists
  (export fields must be a subset of view fields). Changes are audited.
- Default deny: a login with no grant gets nothing; an inactive grant returns no
  staff access, so the effect on the next request is immediate.
- Restricted logins are locked to the ACA project, can only read the flat
  applicant view and the CSV export, and cannot use write or admin routes.
- `export.csv` accepts an ordered `column` list, checks it against the
  authorized fields, and writes an audit entry per export.
- Saved presets per user (`export-options`, `export-options/presets`).
- Tests in `tests/test_volt_recruiting.py` (37 pass).

## Not verified from this sandbox
- I could not reach GitHub, issue #535, open pull requests or deployment
  status, so these were not checked.
- `VOLT-Marketing-Complete-Project-Handoff.md` and `volt-recruitment-demo.html`
  are not in the repository. Their contents were not used or invented.
- No grant for Jstewyyy5045@gmail.com is created by code. The login must exist
  first, then the owner provisions it through the aca-access endpoint or UI.
  Nothing was provisioned or sent.

## Possible follow-ups, which need a separate, more specific request
- Detail and document endpoints for restricted logins: they are blocked by the
  `applicant_read` default, but add an explicit test for this.
- Explicit session revocation on deactivation. Today access is re-checked on
  every request.
- Fields such as DOB, address and interview notes are not stored in the VOLT
  models, so they are not exportable.
