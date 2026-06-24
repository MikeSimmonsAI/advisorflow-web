# Session Log — Account Management Fixes + Import Access Control

This session worked through Mike's account/password complaints plus the
admin-only Excel import request, both confirmed concretely before any
code was written.

All changes verified by actually running them:
- Backend: **444 passed, 8 skipped, 0 failed**
- Frontend: clean `npm install` + `npm run build` from a fully clean state

---

## ⚠️ Migration required before this is live

**New column added:** `users.can_import_leads` (BOOLEAN DEFAULT FALSE)

Already added to `app/migrate_add_missing_columns.py`'s column list. Run
it once on the live database after this deploys, same as every other
session:
```
python -m app.migrate_add_missing_columns
```
Skipping this will make every page that loads a `User` row fail outright
- this is the exact same failure mode that took the whole site down
earlier this session (the `notification_phone` column was missing). Do
not skip this step.

---

## 1. ChangePassword screen — real bug found and fixed

**Changed:** `frontend/src/pages/ChangePassword.jsx` + `frontend/src/pages/Login.css`

Mike's complaint, confirmed by reading the actual component: this one
screen serves two different scenarios - a logged-in advisor voluntarily
changing their password, and a brand-new advisor forced to set a real
password after their first login with a temp password. Both scenarios
shared the same generic "Current password" label with zero context. For
a brand-new user who's never logged in before, "current password" gave
no indication that their temp/welcome password is what goes there - the
exact confusing dead-end Mike described.

Fixed: when `forced=true` (the first-login case), the field now reads
"Temporary password" with a placeholder explaining what to enter, plus a
help line above the form explaining the two-step process. Also added an
explicit password-rule line ("Must be at least 8 characters. No other
requirements.") so the complexity rule is stated, not guessed at -
matching Mike's direct request that there shouldn't be guessing about
this. Backend rule unchanged (8 char minimum, confirmed earlier this
project as deliberately simple, not tightened).

---

## 2. Admin-specified passwords on create + reset - both options, as requested

**Changed:** `app/routers/admin_router.py` (`CreateUserRequest`,
`create_user`, new `ResetPasswordRequest`, `reset_user_password`),
`frontend/src/pages/Users.jsx` + `.css`
**New tests:** 6 appended to `tests/test_user_management.py`

Mike's explicit ask: both a generated password (existing behavior) AND
the ability to type a specific password himself, on both account
creation and password reset. Both endpoints now accept an optional
`password` field - if given (8+ chars enforced), used directly; if
omitted, falls back to the existing auto-generated temp password
exactly as before. Fully backward compatible: every existing caller
that sends no password (or no body at all, for reset) behaves
unchanged - confirmed directly with dedicated regression tests, not
just assumed.

Either path still forces `must_change_password=True` - an admin-chosen
password is never the account's permanent one without the advisor
explicitly confirming/changing it themselves on first login.

Frontend: both the create-user form and a new inline reset-password
panel (replacing the old single `confirm()` dialog) now offer a real
choice - "Generate a temporary password for me" vs. "Set their password
myself" - with the specify field only shown when chosen.

---

## 3. Excel import — admin-only by default, with a per-advisor override

**New column:** `users.can_import_leads` (see migration note above)
**Changed:** `app/models/models.py`, `app/routers/leads_router.py` (new
`_require_import_access` helper, applied to both upload endpoints),
`app/routers/admin_router.py` (`UpdateUserRequest`, `UserResponse`,
`update_user`, `list_users` now include `can_import_leads`),
`frontend/src/pages/Users.jsx` + `.css`
**New/updated tests:** 5 new access-control tests in
`tests/test_upload_endpoints.py` (plus 5 existing tests switched from
`auth_headers` to `admin_auth_headers`, since imports are no longer
advisor-default), 4 new tests in `tests/test_user_management.py`

Mike's explicit answer when this was raised earlier: admin-only, but
with the ability to grant specific advisors that right individually -
not all-or-nothing. `_require_import_access` checks this before any
file is even written to a temp file, so a rejected request doesn't
waste effort on disk I/O. org_admin and super_admin are always allowed
regardless of the flag; a plain advisor needs `can_import_leads=True`
explicitly granted.

Frontend: new "Import access" column on the Users table - shows "Always
on" for admins, "Allowed"/"Admin only" badge for advisors, with a toggle
checkbox available in the same inline edit mode used for name/email/role.

---

## Suggested manual smoke test

1. Run the migration on Render (see top of this doc) before testing
   anything else.
2. Users page → Create account → try both "Generate" and "Set myself"
   password options → confirm the resulting temp password actually logs
   in and forces a password change.
3. Reset an existing advisor's password the same way, both options.
4. Log in as a brand-new account with its temp/welcome password →
   confirm the forced change-password screen now clearly says
   "Temporary password" with helpful context, not a bare "Current
   password" field.
5. As a plain advisor (no `can_import_leads`), try uploading an Excel
   file on the Leads page → confirm a clear "you don't have permission"
   message, not a confusing failure.
6. Grant that advisor `can_import_leads` via Users → Edit → confirm the
   same upload now succeeds.
