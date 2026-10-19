# Atlantis Claude Development Lane

**Branch:** `workstream/atlantis`
**Local worktree:** `C:\Dev\af-atlantis`
**Mission:** Develop Atlantis Light & Power independently of SCI and Wholesale.

## Work to own
- Atlantis public site, customer-facing energy shopping flows, Compare Power integration, Move Concierge, Atlantis-specific branding, lead intake/CRM bridge and relevant tests.
- Protect organization and brand separation. Keep Atlantis messaging and workflows scoped to its own customer context.
- Before reviving older code, inspect `atlantis-launch` and any Atlantis-specific PRs against the current code; selectively port only the changes that are still needed.

## Start here
1. Verify `workstream/atlantis` in an independent Atlantis worktree.
2. Audit Atlantis routes, UI, configuration, tests, relevant outstanding changes, and dependencies.
3. Prepare a short prioritized Atlantis-specific task list.
4. Implement tested features in small PRs without editing Wholesale or SCI feature behavior.

## Collaboration rules
- No Render changes, domain moves, API-key changes, migrations or production deployments as part of lane setup.
- Keep customer/account secrets out of commits.
- Explicitly flag edits to cross-brand routing, auth, shared CRM, templates or config for integration review.
- Leave other developers' worktrees and uncommitted code untouched.

## Handoff format
Report branch, commit, Atlantis changes, tests, shared changes, old branch reconciliation, next step.
