# DEV-4: Consumer ACA lead funnel and agent operations dashboard - no changes made

Status: **not implemented. Needs scoping and separate approval.**

The VOLT dev relay may only change `app/services/volt/`, `app/routers/volt_router.py`,
`frontend/src/pages/volt/`, `frontend/public/brands/volt/`, `tests/test_volt*.py` and `docs/volt/`.
This request cannot be built inside that scope.

## Why it is out of scope
- **Database and migrations.** Encrypted durable lead storage, audit trail, consent records and
  retention/deletion need new models (`app/models/`) and Alembic migrations (`alembic/versions/`).
  Both are outside the allowed paths, and the request also asks for a migration and rollback plan.
- **Auth and roles.** Owner/admin versus agent permissions, and agents seeing only assigned leads, touch
  `app/deps.py`, auth and workspace access. These are explicitly off-limits.
- **Public consumer site.** A separate, login-free consumer website needs its own public routes, app
  registration (`app/main.py`), a public site or deployment target, and rate limiting. None of these
  is under the VOLT paths.
- **Notifications, rate limiting, export and retention controls.** These need shared infrastructure
  or configuration outside the allowed paths.
- **Deployment.** The request asks for a deployment plan and a tested commit. The relay does not
  deploy, and the request itself says not to merge or deploy without separate approval.

## Other blockers
- **Design references.** The demos (`volt_leads_v3.html`, `volt_leads_backend_demo.html`, and the two
  earlier demos) are in a Google Drive folder. This session has no Drive access, so none of them
  were read. They need to be supplied through an authorized channel, for example committed under
  `docs/volt/`. Their contents should not be guessed.
- **Compliance.** ACA marketing copy ($0 premium claims, Marketplace vs Medicare, consent wording)
  needs counsel/compliance review before real traffic. Copy should not be finalized without that review.
- **Size.** This is a multi-PR project, not one relay request.

## Suggested path
1. An authorized person with the right scope approves a project that covers models, migrations and
   RBAC, outside the VOLT-only relay. The recruiting applicants data stays segregated.
2. Supply the design demos in the repo.
3. Split the work into PRs:
   1. data model, migrations and encryption;
   2. public submission API with validation, consent, dedup and rate limiting;
   3. agent and owner API with RBAC and audit;
   4. consumer funnel UI (WCAG 2.2 AA);
   5. dashboard UI under `frontend/src/pages/volt/`.
4. Each PR gets tests, a rollback plan, and explicit approval of the tested commit before any merge or deploy.

Parts 5 (the dashboard UI) and some service logic could later be built inside the VOLT paths once
the backend foundation exists.
