# Automatic ChatGPT ↔ GitHub ↔ Claude relay

Issue #1, "MIKE COMMAND RELAY — ChatGPT ↔ Claude", stays the permanent command log. This setup lets the two agents hand work to each other through it without Mike carrying messages. Mike is pulled in only at a true **approval gate**.

```
Mike gives an objective once
  → ChatGPT posts a [RELAY:DIRECTIVE] comment on issue #1
  → GitHub Actions wakes Claude          (.github/workflows/claude-relay.yml)
  → Claude posts [RELAY:ACK], works on the named branch (never main)
  → Claude posts [RELAY:CLAUDE_STATUS]   (COMPLETED | BLOCKED | APPROVAL_REQUIRED | WORKING)
  → GitHub labels issue #1 "relay:needs-chatgpt-review"   (.github/workflows/relay-status-event.yml)
  → ChatGPT reviews and, if work can continue, posts the next [RELAY:DIRECTIVE]
  → repeat
```

## Files

| File | Role |
|---|---|
| `.github/workflows/claude-relay.yml` | Wakes Claude for a new, authorized directive. Serialized per issue, at most 120 minutes per run. |
| `.github/workflows/relay-status-event.yml` | Turns a terminal Claude status into a label event for the ChatGPT side. It never wakes Claude. |
| `scripts/relay/relay_guard.py` | Checks the author, the marker, the run ID and the branch, then writes the ACK. Standard library only. |
| `tests/test_relay_guard.py` | 9 tests, covering the guard's decisions and a dry run of the workflow step. |

## Contract

### ChatGPT → Claude

The first line must be the marker. `relay_run_id` must be unique per directive.

```
[RELAY:DIRECTIVE]
relay_run_id: <unique, e.g. sci-2026-10-06-14>
parent_run_id: <run this continues, or ->
PROJECT:
PRIORITY:
OBJECTIVE:
CURRENT STATE:
TASKS:
ENVIRONMENT: sci-program / staging
BRANCH: sci-program
DO NOT TOUCH:
APPROVAL BOUNDARIES:
EXPECTED OUTPUT:
```

### Claude → ChatGPT

Claude posts exactly one of these per run:

```
[RELAY:CLAUDE_STATUS]
relay_run_id: <same id>
STATUS: WORKING | COMPLETED | BLOCKED | APPROVAL_REQUIRED
PROJECT:
BRANCH:
START TIME CT:
CURRENT TIME CT:
ELAPSED:
COMPLETED:
COMMITS:
TESTS:
LIVE VERIFICATION:
BLOCKERS:
APPROVAL REQUIRED:
NEXT RECOMMENDED ACTION:
PRODUCTION IMPACT:
DO NOT TOUCH CONFIRMATION:
```

### Other markers

- `[RELAY:ACK]`: written by the guard before Claude starts. It carries `relay_run_id`, `parent_run_id`, project, branch, the timestamp in CT, the actor, and the status ACCEPTED.
- `[RELAY:CHATGPT_REVIEW]`: an optional ChatGPT note that is not a new directive.
- `[RELAY:APPROVAL_REQUIRED]`: needs Mike.

**Only `[RELAY:DIRECTIVE]` wakes Claude.**

### Blocker vs approval gate

- **APPROVAL GATE** (stop, `STATUS: APPROVAL_REQUIRED`):
  - production launch;
  - messaging real customers without Mike's GO;
  - spending beyond prior authorization;
  - destructive data changes;
  - irreversible infrastructure changes;
  - legal or carrier attestations;
  - credentials only Mike can enter.
- **Everything else continues.** That includes a failing test, an unavailable site, one blocked subtask, a browser permission, research, documentation, or a normal defect. Claude works around these and reports them.
- **Standing rule:** API, connector, CLI or repo first; the browser second. When the browser is unavoidable, work is batched per domain in one approved session.

## Safety and loop prevention

- **Authorized actors only:** Claude wakes only when *all* of these hold:
  - the author is `MikeSimmonsAI` (repo variable `RELAY_AUTHORIZED_ACTORS` can extend the list);
  - the author is the repository OWNER, a MEMBER or a COLLABORATOR;
  - the comment is on issue #1 and is newly created.
- **Comments from strangers are never executed.** Edits and pull-request comments are ignored too.
- **No self-trigger:** Claude's own `[RELAY:CLAUDE_STATUS]` and `[RELAY:ACK]` comments are never directives. A comment that contains both markers is rejected.
- **Deduplication:**
  - the guard posts `[RELAY:ACK] relay_run_id: …` *before* Claude starts;
  - any later event carrying a run ID that already has an ACK or a STATUS is skipped;
  - runs are also serialized with `concurrency: claude-relay-issue-1`.
- **Branches:** only those in `RELAY_ALLOWED_BRANCHES` (default `sci-program`). `main`/`master` are refused even if listed.
- **Secrets:** they live only in repository secrets and are never printed. Claude is told never to commit or post them.

## One-time actions (Mike)

1. **Approval gate: put the relay bootstrap on `main`.**
   - GitHub runs `issue_comment` workflows only from the default branch, `main`, so the relay cannot wake anything until these three files are on main: the two workflows and `scripts/relay/relay_guard.py`. No application code goes to main.
   - They are ready on branch `relay-bootstrap`, which is cut from main.
   - Merge it yourself, or tell Claude "approve relay bootstrap merge".
2. **Credential: give the Action access to Claude.**
   - In Claude Code on your computer, run `/install-github-app` and pick `MikeSimmonsAI/advisorflow-web`.
   - That installs the Claude GitHub App and stores the `CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`) repository secret.

**Recommended:** turn on branch protection for `main` (require a PR) so nothing automated can push to it.

## ChatGPT side: the one instruction to paste once into ChatGPT Work

Configure the trigger, if ChatGPT Work offers GitHub triggers:

- **Repository:** `MikeSimmonsAI/advisorflow-web`
- **Event:** issue **labeled** on issue **#1** with label `relay:needs-chatgpt-review`. If label triggers are not available, use: an issue comment created on issue #1 whose body contains `[RELAY:CLAUDE_STATUS]`.

This repository cannot set anything on the ChatGPT account side. If ChatGPT Work has no GitHub event trigger, the same instruction still works when Mike says "check the relay".

Paste this:

> You are the ChatGPT half of Mike Simmons' automatic relay in GitHub issue #1 of MikeSimmonsAI/advisorflow-web. When woken, read the newest comment on issue #1 that starts with [RELAY:CLAUDE_STATUS]. Compare it with the active objective (the newest [RELAY:DIRECTIVE] from MikeSimmonsAI and anything Mike told you).
>
> - If STATUS is COMPLETED and the objective has a next step that needs no approval, post ONE new directive comment that starts with the line [RELAY:DIRECTIVE], followed by the relay contract fields in handoff/AUTOMATIC_AGENT_RELAY_SETUP.md (relay_run_id must be new and unique; parent_run_id = the status's relay_run_id).
> - If STATUS is BLOCKED, post a directive that works around the blocker if a safe path exists; otherwise post a [RELAY:CHATGPT_REVIEW] summary and stop.
> - If STATUS is APPROVAL_REQUIRED, do NOT post a directive. Post a [RELAY:CHATGPT_REVIEW] comment that states the exact decision Mike must make, and notify Mike.
>
> Never post a directive that targets main, touches production, messages real customers, spends money or makes a legal/carrier attestation without Mike's explicit approval in this conversation. Never include secrets. Never post more than one directive per Claude status. Post as MikeSimmonsAI so the relay accepts it.

## Dry run (once the two one-time actions are done)

Post on issue #1:

```
[RELAY:DIRECTIVE]
relay_run_id: relay-test-001
PROJECT: Relay
PRIORITY: P0
OBJECTIVE: Return RELAY TEST PASS with current CT timestamp and branch name. Make no code or infrastructure changes.
ENVIRONMENT: sci-program
DO NOT TOUCH: main, production, everything else
EXPECTED OUTPUT: one [RELAY:CLAUDE_STATUS] with STATUS: COMPLETED
```

**Expected:**
1. A `[RELAY:ACK] relay_run_id: relay-test-001` comment.
2. A `[RELAY:CLAUDE_STATUS]` comment with `STATUS: COMPLETED` and "RELAY TEST PASS".
3. The label `relay:needs-chatgpt-review`.
4. Re-running the workflow, or posting the same text again, produces "duplicate suppressed" and no second run.
