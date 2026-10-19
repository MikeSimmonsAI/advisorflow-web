# SCI Claude Development Lane

**Branch:** `workstream/sci`
**Local worktree:** `C:\Dev\af-sci`
**Mission:** Move the SCI funeral/cemetery suite forward independently of Wholesale and Atlantis.

## Work to own
- SCI advisor workflows, contact and family-service journeys, SCI-specific email/SMS presentation, appointment flows, templates, location-aware advisor experience, and SCI-specific UI/API/tests.
- Preserve integrations used by EvoSys Core without changing Wholesale or Atlantis behavior.
- Review relevant outstanding SCI branches (e.g. `chatgpt/sci-qa-readiness`, `feature/sci-command-center-redesign`, `feat/sci-touch-text`, `feat/sci-send-via-m365`) before porting older work. These are candidates for inspection, not automatic merges.

## Start here
1. Verify you are on `workstream/sci` in a dedicated SCI worktree and check for local changes.
2. Audit current SCI entry points, tests, and outstanding PRs. Record a baseline.
3. Produce a prioritized SCI task board from current code and known unfinished changes.
4. Implement selected tasks in small commits with scoped tests and a reviewable PR.

## Collaboration rules
- Do not edit Wholesale or Atlantis feature code unless a shared interface fix is unavoidable. In that case call it out in the PR.
- Do not overwrite customer data or migrate the production database. Keep production deployments manual and separately authorized.
- SCI work must not block the other two Claude sessions.
- Preserve any uncommitted work in original developer folders; never clean/reset those folders.

## Handoff format
Report branch, commit, SCI features changed, tests, shared modules touched, old SCI changes reconciled, and next action.
