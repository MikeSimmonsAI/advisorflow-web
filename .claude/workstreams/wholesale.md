# Wholesale Claude Development Lane

**Branch:** `workstream/wholesale`
**Local worktree:** `C:\Dev\af-wholesale`
**Mission:** Build EvoSys Pro Wholesale independently while leaving the current EvoSys production environment intact.

## Work to own
- Wholesale properties and import list, owner research, skip-trace enrichment queue, buyer/investor relationships, EvoSense, seller pipelines, offers, deal workflow, Wholesale pages/APIs, and tests.
- The owner-facing marketing alias `wholesale.evosyspro.live` remains unchanged; do not move hosting, databases, or domains in this task.
- Look for outstanding functionality in older `feature/wholesale-evosense-p7`, `feat/opportunity-seller-ux`, and EvoSense branches. Compare carefully with current `main` first; many commits may already have been superseded.

## Start here
1. Verify `workstream/wholesale` and a dedicated Wholesale worktree.
2. Inventory Wholesale endpoints, UI, property import, contacts, workers and dependencies; identify feature gaps and tests.
3. Compare old work with present implementations using `git log`/`git diff` and selectively bring forward *only* missing, appropriate changes.
4. Implement tasks in small scoped commits; open PRs with test evidence and data-migration notes if relevant.

## Collaboration rules
- No automatic import, outbound lead messaging, production data change, database migration or deployment by merely starting this lane.
- Never merge the stale Wholesale branch wholesale into current `main`.
- When changing common auth, app routing, CRM models, or platform configuration, explicitly document coordination needs with SCI/Atlantis.
- Preserve property and lead data exactly unless an actual future task authorizes an explicit operation.

## Handoff format
Report branch, commit, Wholesale changes, tests, old functionality reconciled, shared-file risks, next task.
