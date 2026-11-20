# DEV-5: Consumer lead funnel and agent operations dashboard - no changes made

Status: **not implemented. Access blocker plus scope blocker.**

## Access blocker: design references unreadable
The runner tried the Consumer V3 Drive link
(`1N8CozezEYuvOCs22RdjyT8z9coJXBhkI`). Drive returned **HTTP 401 Unauthorized**. The runner has no
Drive credentials, so it cannot read either demo (Consumer V3 or the agent backend dashboard).
The second link was not fetched separately because it sits in the same private folder.
Nothing about their content has been guessed. To unblock, commit the two demo files under
`docs/volt/` (for example `docs/volt/design/`) or share them through an authorized channel.

## Scope blocker (same as DEV-4)
This request supplements DEV-4, which was already judged out of relay scope (see DEV-4 notes).
The relay may only change `app/services/volt/`, `app/routers/volt_router.py`,
`frontend/src/pages/volt/`, `frontend/public/brands/volt/`, `tests/test_volt*.py` and `docs/volt/`.
A lead funnel needs durable storage (models and migrations), RBAC (agents see only assigned
leads), a public login-free consumer site and rate limiting. All of these sit outside those paths.

## Wording and compliance
The request says "insurance lead funnel". VOLT's ACA work is healthcare outreach, not insurance
sales, and the existing wording rules apply. Consumer copy needs compliance review before use.

## Release
No code was written, so there is no tested commit. Nothing should be released or deployed.

## Next steps
1. Supply the two demos in the repo.
2. Approve the backend foundation (models, migrations, RBAC) outside the VOLT-only relay.
3. Once that exists, the dashboard UI under `frontend/src/pages/volt/` can be built as a
   relay request.
