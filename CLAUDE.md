# Claude Code — Parallel EvoSys Development Lanes

This repository supports **three concurrent, independently developed workstreams**: SCI, Wholesale, and Atlantis. Each workstream has its own Git branch, Git worktree and Claude session, all based initially on the same current `main` snapshot.

## Select your lane
Read the instructions matching your checked-out branch:
- `workstream/sci` → `.claude/workstreams/sci.md`
- `workstream/wholesale` → `.claude/workstreams/wholesale.md`
- `workstream/atlantis` → `.claude/workstreams/atlantis.md`

Before editing, run `git status --short --branch` and `git branch --show-current`. Stop and report a wrong branch or uncommitted changes. Do not silently switch branches in somebody else's worktree.

## Required parallel-work rules
1. Change code only for your assigned business workstream. Shared routes/config (including `app/main.py`, `frontend/src/App.jsx`, auth, CRM models, shared migrations, deployment config) require clearly isolated edits, related tests, and an explicit handoff note; do not overwrite another lane's changes.
2. Never run a production database migration, reset, seeding task, outbound messaging job, import, or deployment as part of lane setup. No changes to Render or production databases are authorized by these instructions.
3. Use separate local working directories, ports, environment files, temp files, caches and test databases where needed. Never point tests at production.
4. Reconcile old and new work **without losing either**. These new workstream branches start at current `main`; older branches can hold unique unmerged commits. Inspect `git log`, `git diff`, and merge-base / compare results before selectively cherry-picking, reimplementing, or rebasing intended functionality. Do not blindly merge stale branches or force-push to recover them.
5. For each feature: make small commits, run relevant tests, list touched shared files, document any outstanding merge risk, and submit a scoped PR for review. Do not auto-merge workstreams into each other, `main` or production.
6. The customer database's age/freshness and Git code history are separate issues. Preserve current data, schema, records and audit history unless a separately authorized data change explicitly names the desired outcome.

## Handoff on every Claude session
Summarize: current branch and HEAD; features done; files touched; tests passed/failed; old-branch work still to reconcile; shared-file conflicts; and exact next step. Keep SCI, Wholesale and Atlantis work moving concurrently instead of serializing unrelated tasks.
