# DEPLOYMENT — credentials, staging, and what actually ships

This describes the deployment *path*: the credential it needs, where that
credential lives, and what the scripts are and are not allowed to stage.
For what the product does, see `DEPLOY.md` at the repository root.

---

## 1. WHAT DEPLOYS PRODUCTION

**A push to `main`.** Both Render services are created from `render.yaml` and
auto-deploy on push:

| Service | Render id | Trigger |
|---|---|---|
| `advisorflow-backend` | `srv-d8rsm2kvikkc738v8470` | auto-deploy on push to `main` |
| `advisorflow-frontend` | `srv-d8rslocvikkc738v7ocg` | auto-deploy on push to `main` |

The backend runs `python -m app.migrate` as its **preDeployCommand**, which
creates new tables and applies column/enum/index migrations *before* the new
instance is promoted. If that command exits non-zero the deploy fails and the
healthy old instance keeps serving — so a successful deploy is itself evidence
that the migration ran.

Every API call in `deploy.bat` / `render_deploy.bat` is a **nudge**, not the
mechanism. With no API key set at all, a push still deploys. This matters: it
means a revoked key degrades the convenience, never the delivery.

---

## 2. THE CREDENTIAL: `RENDER_API_KEY`

**It is an environment variable. It is never written into a file in this
repository.**

Set it once per machine (PowerShell, persists across sessions):

```powershell
[Environment]::SetEnvironmentVariable("RENDER_API_KEY", "<key from Render>", "User")
```

or, from `cmd`:

```bat
setx RENDER_API_KEY "<key from Render>"
```

Then **open a new terminal** — neither form affects the shell you typed it in.

Get the key from the Render dashboard: *Account Settings → API Keys*. Create
one per machine rather than sharing one, so a single machine can be revoked
without breaking every other machine.

Scripts that read it: `deploy.bat`, `deploy.ps1`, `render_deploy.bat`.

Missing it fails *safely*, and the two behaviours differ on purpose:

- `deploy.bat` / `deploy.ps1` still deploy. The push is the mechanism; the API
  call is a nudge. They say the key is unset and exit **0** — nothing failed.
- `render_deploy.bat` does nothing *but* nudge, so without the key it has no
  work to do and exits **1**, saying so, rather than reporting a deploy that
  never happened.

### Why the key is not in the repo any more

`deploy.bat` carried a Render API key in plaintext, committed. Anyone who
could read the repository could read the key.

**Removing a key from HEAD does not revoke it.** It remains in git history and
is readable by anyone who can clone. Any key that has ever been committed must
be **rotated in the Render dashboard** — deleting the old key is the only thing
that actually invalidates it. `deploy.ps1` carries the same warning about an
earlier key of its own; both must be rotated.

---

## 3. YOU COMMIT. THE DEPLOY SCRIPT PUSHES.

**Deployment automation does not decide what goes into a commit.**

The production flow is:

```bash
git add app/services/support_sla.py tests/test_support_engine.py   # you choose
git commit -m "fix SLA pause arithmetic"                           # you describe
deploy.bat                                                         # it verifies and pushes
```

`deploy.bat` and `deploy.ps1` run **no `git add`** at all. They check the
working tree, run the credential audit, and push a commit that already exists.
If the tree is dirty they print the file **names** and stop — they will not
stage it, commit it, stash it, or throw it away.

### Why every form of bulk staging is banned

`git add .` and `git add -A` staged *everything* present in the tree at the
moment somebody ran a deploy. In a repository with several live worktrees that
meant other threads' work-in-progress, throwaway probe databases, scratch
output, and any file that happened to contain a credential. `.gitignore` still
carries a comment about `.probe_*.db` being committed by exactly this
mechanism — the tell that it had already happened.

`git add -u` was the first fix and is banned too. It only stages files git
already tracks, which is narrower, but it is the same mistake: the script still
chose the change set, and it still chose by wildcard. "Everything I happen to
have edited right now" is not a change set. A half-finished edit in another
file ships next to the fix, under the fix's message, and the history stops
describing the work.

So: **no `git add .`, no `-A`, no `-u`, no equivalent**, anywhere in deployment
tooling.

### Adding a new file to a deploy

New files are not special and there is no flag for them. Stage the path, commit
it, then deploy:

```bash
git add app/routers/new_router.py
git commit -m "add the new router"
deploy.bat
```

If you leave it untracked, the clean-tree check names it and stops. That is
deliberate — a new router that never reaches production is not a smaller bug
than a scratch file that does.

### The one staging exception

`deploy.ps1` runs exactly one `git add`, naming one path:

```powershell
git add -f frontend/dist
```

`frontend/dist` is gitignored and deliberately committed, because Render's
static site serves the committed bundle instead of building on Render. The
script **generated that output itself**, seconds earlier, from source that was
already committed and had already passed the gates. `-f` is needed to override
`.gitignore`; the path is explicit, so it cannot pick up anything else.

Its `-Message` names that `frontend/dist` commit and nothing else. Your own
commit keeps the message you gave it.

### Nothing discards work

No deployment script runs `git reset --hard`, `git checkout -- .`, `git clean`,
or deletes `.git/index.lock`. `deploy.ps1` once carried a `reset --hard` that
cost a full session of backend work; `_wt.bat` once deleted `index.lock`
blindly, which can corrupt the index of a worktree that is mid-write. Both are
gone. Where the old scripts reconciled by force they now fast-forward, and stop
if they cannot.

---

## 4. THE CREDENTIAL GATE

`scripts/_secret_audit.py` scans **tracked** files for credential-shaped
strings and exits non-zero if any look live. `deploy.bat` and `deploy.ps1` run
it **before the push**, so "did we just put a key on GitHub" is never answered
with "yes, and it is already public". An audit that runs after the push is a
report, not a gate.

```bash
python scripts/_secret_audit.py          # all tracked files
python scripts/_secret_audit.py path...  # just these
```

It reports **shape and location only** — label, verdict, length, `file:line` —
and never prints the value. Not truncated, not masked: `scan()` does not even
return the token, so nothing downstream can render it by accident. An audit
that echoes what it found has copied the secret into a terminal buffer, a
scrollback, a log and a transcript.

Four verdicts:

| Verdict | Meaning | Blocks? |
|---|---|---|
| `LIVE-SHAPED` | long and random enough to be a working credential | **yes** |
| `PLACEHOLDER` | filler text, ≤4 distinct characters, or a repeated unit — `ACaaaa…` and `ACdeadbeef…` in the Twilio probe | no |
| `FIXTURE` | the line carries an explicit `secret-audit: fixture` marker | no |
| `SHORT` | below the vendor's real token length | no |

The `FIXTURE` marker is the **only** explicit exclusion and it is deliberately
narrow: it must be typed on the **same physical line** as the value, it cannot
be set per file, per directory, by glob or by environment variable, and it
shows up in every diff that adds one. It exists so a test can hold a value the
scanner really does classify as live without the audit failing forever against
its own fixture — a permanently red gate is a gate somebody switches off.
Widening it to a file or a path would turn the one escape hatch into the way a
real key gets through.

The scanner is itself tested (`tests/test_deploy_hygiene.py`): exit 0 on the
current tree, exit 1 on a planted live-shaped key, no false positive on an
ordinary identifier like `pre_need_lock_price`, and no credential in its output.

If it blocks you: move the value to an environment variable, remove it from the
file, **rotate it**, and run again.

---

## 5. THE SCRIPTS

| Script | Does | Stages | Commits |
|---|---|---|---|
| `deploy.bat` | audit → clean-tree check → push → optional nudge | **nothing** | **nothing** |
| `deploy.ps1` | the full gated deploy: audit, clean-tree check, sync `main`, ~50 gates, frontend build, push, verify, trigger | `frontend/dist` only | the rebuilt bundle only |
| `deploy_force.bat` | empty commit to force a rebuild of code already on `main` | **nothing** | one empty commit |
| `git_push.bat` | stage the paths **you name**, commit with your message, push | only your paths | yes |
| `render_deploy.bat` | API trigger only | **nothing** | **nothing** |
| `_wt.bat` | add a worktree for a new thread | **nothing** | **nothing** |

**`deploy.bat` / `deploy.ps1`** require a clean working tree and an
already-prepared commit. A dirty tree is listed by name and stops the deploy
with a non-zero exit, so a wrapper or a CI step cannot mistake it for success.

**`deploy_force.bat`** refuses to run with a dirty tree and commits
`--allow-empty`. Forcing a redeploy and shipping your working tree used to be
the same keystroke, which meant a force-redeploy could carry anything in the
tree into production while the intent was "just redeploy". Use it when the code
on `main` is already correct and the running instance is not — a stuck build, a
cache to clear, a missed webhook.

**`git_push.bat`** is the one helper allowed to create an ordinary commit, and
it may stage **only the paths you pass it**:

```
git_push.bat "fix SLA pause arithmetic" app\services\support_sla.py tests\test_support_engine.py
```

It requires both a message and at least one path — there is no flag to stage
everything, because that flag is the bug. It lists what it left alone, and it
works on the repository you are standing in. It previously `cd`'d to a
hardcoded path, so running it from any other worktree committed in a different
one — with several worktrees live at once, a way to push somebody else's
half-finished branch. `scripts/_dep.ps1` carried the same hardcoded path and
now resolves the repo from its own location.

**`_wt.bat`** takes the worktree path, branch and start point as arguments. It
no longer deletes `.git/index.lock`: that lock exists because a git process is
holding the index or died holding it, deleting it blind cannot tell those
apart, and doing it while another worktree is mid-write corrupts the index.

`scripts/commit_god07.bat` is a one-off from an earlier thread. It stages two
explicit paths, which is compliant, and is left as it is.

---

## 6. OTHER DEPLOYMENT SECRETS

Everything else the services need is set as a Render environment variable on
the service, not in this repository: `DATABASE_URL`, `JWT_SECRET`,
`ENCRYPTION_KEY`, `OPENAI_API_KEY`, `RESEND_API_KEY`, Twilio credentials,
Stripe keys, Google/Microsoft OAuth client secrets.

`render.yaml` declares which variables a service needs; it does not contain
their values. Adding a new one is a change in the Render dashboard plus a
declaration in `render.yaml`, never a literal in a script.

A presence check is not a health check. `OPENAI_API_KEY` being *set* tells you
a variable exists, not that the provider answers, that the key is valid, or
that the account has quota. Read it as configuration, never as a green light.

---

## 7. KNOWN DEFERRED ITEMS

Recorded so they are not rediscovered as bugs. These are accepted gaps, not
open defects, and each is a deliberate scope decision:

| Item | State |
|---|---|
| Email-to-ticket intake | deferred — tickets are created in-app and by agents |
| SMS support channel | deferred |
| Mobile support UI | deferred — the support console is desktop-first |
| Customer attachment download UX | limited — attachments upload and are listed; the customer-side download flow is minimal |
| Support / service pricing | handled in the commercial catalogue workstream, not in the support code |
| `OPENAI_API_KEY` presence check | configuration evidence only, never proof of provider health |

Rotation of any Render API key that has ever been committed is still
outstanding until it is done in the dashboard — see §2. The key this repository
used to carry already returns `Unauthorized`, so it is revoked rather than
merely removed.
