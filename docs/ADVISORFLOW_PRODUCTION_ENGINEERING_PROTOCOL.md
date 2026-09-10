# AdvisorFlow Production Engineering Protocol v1.0

This document records standing architectural rules that govern production code
changes to the AdvisorFlow platform. Rules here have been explicitly approved
and must not be altered without a new approval session.

---

## SECTION 1 — IDENTITY AND TERMINOLOGY

### 1.1  God Mode terminology is PRIVATE

"God Mode," "God Admin," "God Operations," and equivalent terms are
**internal-only** identifiers visible solely to the platform owner. These
terms must never appear in:
- User-facing copy or navigation labels
- Error messages or HTTP detail strings
- Help text, tooltips, or UI instructions
- Footers, changelogs, or any customer-visible surface

Use neutral operational language in all public-facing contexts.

---

## SECTION 2 — GOD MODE ROOT AUTHORITY

### 2.1  Canonical rule (APPROVED)

> God/platform owner is the highest authority in AdvisorFlow. Nothing outranks
> God Mode. God has unrestricted administrative authority across all AdvisorFlow
> core systems, white-label brands, Executive Suites, Back Office/Sales
> contexts, customer organizations and operational workspaces. God does not
> require ordinary brand/customer memberships to exercise this authority.
> Explicit context selection and tenant/brand scoping remain mandatory to
> prevent data mixing and preserve auditability. Non-God users continue to
> require explicit memberships, roles, capabilities and applicable resource
> authorization.

### 2.2  Implementation pattern — require_brand_executive

God root authority is implemented via a sentinel bypass in `app/deps.py`
`require_brand_executive`. When `user.role == "god_admin"`:

1. Read `user._selected_brand_id` (set from `X-Brand-Override` header by
   `get_current_user`).
2. If absent or falsy → HTTP 403 "Select a brand context to enter the
   Executive Suite." (explicit context selection is mandatory).
3. If present but resolves to no Platform row → HTTP 403 "Selected brand
   context not found."
4. If valid → load the Platform, construct a `types.SimpleNamespace` sentinel
   with `role=ROLE_BRAND_EXECUTIVE`, `scope_type=SCOPE_PLATFORM`,
   `scope_id=platform.id`, `created_at=None`, and return
   `(user, sentinel, platform)`.
5. The sentinel satisfies the router contract (`user, mem, platform = executive`)
   **without creating a real Membership row**.

### 2.3  Implementation constraints (PERMANENT — must not be changed)

- Do **not** create a real `brand_executive` Membership row for god.
- Do **not** duplicate Mike's identity or create a shadow account.
- Do **not** change Mike's base role (`users.role`).
- Do **not** weaken `require_brand_executive` for non-god users.
- Do **not** modify Michael Schlueter's memberships.
- Do **not** modify customer memberships.
- Do **not** change the database schema.

### 2.4  authorized_contexts — god branch

`app/services/workspace_access.py` `authorized_contexts()` populates
`executive_contexts` with **all platforms** (ordered by name) when the user is
`god_admin`. Non-god users continue to receive only their membership-granted
platforms.

### 2.5  ContextSwitcher — brand context must be set before navigation

`frontend/src/components/ContextSwitcher.jsx` `enterExecutive()` must call
`setBrandContext(platformId, platformName)` **before** calling
`navigate('/executive')`. This ensures the `X-Brand-Override` header is present
on the very first Executive Suite request.

### 2.6  Security rule

Server-side route guards (`require_brand_executive`) are the authoritative
access gate. The UI (ContextSwitcher visibility) is UX only. Typing
`/executive` directly with no valid brand context returns 403 regardless of
what the UI shows or hides.

---

## SECTION 3 — DIAGNOSTIC PROTOCOL CONSTRAINTS

The following actions are **permanently prohibited** during diagnostic sessions
unless a new explicit approval is granted:

- Reset password for any user
- Send an activation token to any user
- Alter any user's `organization_id`
- Alter any user's `platform_id`
- Alter any user's base role
- Alter any Membership row
- Change routing or context resolution logic
- Change login isolation
- Create additional user accounts
- Manually navigate to protected routes to force test passage

---

## SECTION 4 — CHANGE AND DEPLOYMENT RULES

### 4.1  Local-first, review-before-push

No changes are pushed to production without prior local review. The sequence is:

1. Implement changes in the cloud container.
2. Run the full test suite locally; all gates must be green.
3. Send changed files to Mike for review.
4. After explicit approval, build the frontend (`npm run build`).
5. Commit all changes (backend + frontend dist).
6. Push to GitHub from the Windows repo.
7. Verify Render auto-deploy.
8. Run production proof against `https://app.evosyspro.live`.

### 4.2  Credentials must not appear in transcripts or tool output

`DATABASE_URL`, passwords, API keys, and session tokens must never be visible
in conversation output, tool call results, or any committed file. Scripts that
require credentials must read them from environment variables only.

### 4.3  Retell configuration must not be changed

The Retell voice agent configuration is production-sensitive. No session may
alter Retell settings without an explicit, named approval from Mike.

### 4.4  Feature sequencing

Features are implemented and production-proved one at a time in the approved
order. A new feature does not begin until the current feature is production-proved
and the approval to proceed is granted.

---

## SECTION 5 — TEST IDENTITY

### 5.1  Authorized test accounts

| Purpose | Account |
|---|---|
| God / platform-owner proof | Mike's god account (simmonsmj242@gmail.com) |
| EvoSys Pro Brand Executive proof | Michael Schlueter (michaelpschlueter@gmail.com) |

God Mode and Brand Executive are **separate authorized contexts**. A god account
receiving 403 from `/executive/context` without an explicit brand selection is
**expected behavior**, not a bug. Michael Schlueter's account must not be given
god-level access; Mike's god account must not be used as a substitute for
Michael's Brand Executive context.

---

## SECTION 6 — OPEN ENGINEERING PUNCH LIST

Carried deliberately, with the reason. An item here is a decision to defer,
not a thing that was forgotten — each says what it costs to leave and what
would make it urgent.

### P2 — Consolidate migrations onto Alembic

**State:** Alembic exists with six revisions. The live schema is at 369
columns, carried there by `app/auto_migrate.py`. `alembic upgrade head` against
production today would do almost nothing.

**Why it is not done:** autogenerating one catch-up revision against a live
schema is a `drop_column` generator. Anything the models no longer mention —
`deal_value`, kept on purpose — becomes a drop nobody reviews carefully at
deploy time. Trading a startup hang for a data-loss risk is not an
improvement.

**What was done instead (2026-09-10):** the ORDER was fixed.
`python -m app.migrate` runs as the backend's `preDeployCommand`, so
migrations run and can fail the deploy BEFORE the app starts. The engine
underneath is still `auto_migrate`. Swapping it for Alembic is now a change
to one function, with the deploy gate already in place.

**Cost of leaving it:** no reviewable migration history, no down-migrations,
and schema drift between environments stays invisible until something breaks.

**What makes it urgent:** a second engineer, a second environment that must
match production exactly, or the first time a column needs to be REMOVED
rather than added.

### P3 — `booking_links` index warnings on every boot

**State:** every startup logs two failures —
`ix_booking_links_organization_id` and `ix_booking_links_slug` — because
`booking_links` has neither an `organization_id` nor a `slug` column.

**Why it is not done:** harmless today. The indexes are skipped, the table
works, nothing depends on them. Fixing it means deciding whether the columns
should exist (a schema question) or the index entries should go (a cleanup
question), and that is a real decision, not a typo.

**Cost of leaving it:** two red lines per boot in the same log where genuine
migration failures now appear. Noise next to signal is how the real one gets
skimmed past.

**What makes it urgent:** any work on booking links, or the first time
somebody misses a real failure because they had learned to ignore these.

### P3 — Blueprint sync must stay the source of truth for the deploy gate

**State:** `preDeployCommand` and `SKIP_STARTUP_MIGRATIONS` live in
`render.yaml` and reached production through a Blueprint sync.

**Risk:** editing the service in the Render UI instead of the file can drift
the gate away from the repo, and the failure is silent — migrations quietly
return to running during boot.

**Check:** `GET /god/ops/diagnostics/database` reports the last migration's
outcome and elapsed time; a deploy log without `==> Pre-deploy complete!`
means the gate did not run.

---

*Last updated: 2026-09-10 — added Section 6 (Open Engineering Punch List)
when the database hardening phase was closed.*
