# VOLT consumer leads implementation

Continue DEV-4 as one implementation covering the consumer eligibility funnel and agent dashboard. DEV-5 supplied the approved designs. PRs #603 and #604 contain blocker notes only.

- [Consumer funnel design](volt_leads_v3.html)
- [Agent dashboard design](volt_leads_backend_demo.html)

Use these files as interaction and visual references. The sample dashboard records are fictional. Keep both files under docs and out of the public application build.

Implement the public funnel under the existing VOLT public route and the agent dashboard under the protected VOLT Operations route. Preserve the existing recruiting paths and records. Define new tables (named `volt_lead*`) in `app/models/volt_leads_models.py`; it is already registered, so deploy creates them with `create_all`. Mount any new `app/routers/volt_leads_router.py` from `app/routers/volt_router.py`. No Alembic revisions and no new columns on existing tables. Test database startup on a clean schema and on a schema with existing recruiting data. Keep lead authorization in the VOLT service layer and use the existing platform identity for authenticated staff.

Validate and rate-limit intake. Capture consent and its version before any contact. Keep personal data out of logs, demo fixtures, and public responses. Enforce organization and assigned-agent scope for every lead read, write, search, and report. Require an explicit owner grant for broader access. Keep intake disabled for real traffic until privacy copy, eligibility claims, consent, and agent routing are reviewed.

Test submission, retries, role isolation, assignment, lead history, database initialization, accessibility, and frontend build. Submit one implementation PR with its tested commit and rollout plan. Release requires explicit owner approval of that exact commit.
