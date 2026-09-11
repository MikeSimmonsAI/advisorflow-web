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

Scripts that read it: `deploy.bat`, `deploy.ps1`, `render_deploy.bat`. Each
one degrades gracefully when it is unset and says so.

### Why the key is not in the repo any more

`deploy.bat` carried a Render API key in plaintext, committed. Anyone who
could read the repository could read the key.

**Removing a key from HEAD does not revoke it.** It remains in git history and
is readable by anyone who can clone. Any key that has ever been committed must
be **rotated in the Render dashboard** — deleting the old key is the only thing
that actually invalidates it. `deploy.ps1` carries the same warning about an
earlier key of its own; both must be rotated.

---

## 3. WHAT THE DEPLOY SCRIPTS MAY STAGE

**Deployment automation does not stage the working tree indiscriminately.**

Every script stages with `git add -u` — modifications and deletions of files
git **already tracks**. Untracked files are never swept in.

Why: `git add .` / `git add -A` staged *everything* present in the tree at the
moment somebody ran a deploy. In a repository with several live worktrees that
included other threads' work-in-progress, throwaway probe databases, scratch
output, and any file that happened to contain a credential. `.gitignore`
already carries a comment about `.probe_*.db` being committed by exactly this
mechanism.

A new file is therefore something you add **on purpose**:

```bash
git add path/to/new_file.py
```

`deploy.bat` and `deploy.ps1` both **stop** when untracked files exist, listing
them, rather than deploying without them. Shipping stale code while reporting
success is the failure `deploy.ps1`'s own header was written about; a new
router that never reaches production is not a smaller bug than a scratch file
that does. `deploy.ps1 -IncludeNew` stages them all deliberately when that is
genuinely what you want.

`frontend/dist` is the one exception: it is gitignored and force-added by
`deploy.ps1` (`git add -f frontend/dist`) because Render's static site serves
it.

---

## 4. THE CREDENTIAL GATE

`scripts/_secret_audit.py` scans **tracked** files for credential-shaped
strings and exits non-zero if any look live. `deploy.bat` and `deploy.ps1` run
it before staging anything, so "did we just commit a key" is never answered
with "yes, and it is already on GitHub".

```bash
python scripts/_secret_audit.py          # all tracked files
python scripts/_secret_audit.py path...  # just these
```

It reports **shape and location only** — prefix, length, file, line — and never
prints the value. An audit that echoes what it found copies the secret into a
terminal buffer, a scrollback, a log and a transcript.

Three verdicts: `LIVE-SHAPED` (blocks), `PLACEHOLDER` (filler text or fewer
than five distinct characters — `ACaaaa…` in the Twilio probe), `SHORT` (below
the vendor's real token length).

If it blocks you: move the value to an environment variable, remove it from the
file, **rotate it**, and run again.

---

## 5. THE SCRIPTS

| Script | Does | Stages |
|---|---|---|
| `deploy.bat` | audit → review → commit → push → optional nudge | `git add -u` |
| `deploy.ps1` | the full gated deploy: audit, auto-save, tests, frontend build, push, verify, deploy | `git add -u` + `-f frontend/dist` |
| `deploy_force.bat` | empty commit to force a rebuild of code already on `main` | **nothing** |
| `git_push.bat` | stage tracked changes, commit with a message you supply, push | `git add -u` |
| `render_deploy.bat` | API trigger only | **nothing** |

`deploy_force.bat` refuses to run with a dirty tree. Forcing a redeploy and
shipping your working tree used to be the same keystroke, which meant a
force-redeploy could carry anything in the tree into production while the
intent was "just redeploy".

`git_push.bat` requires a message argument and works on the repository you are
standing in. It previously `cd`'d to a hardcoded path, so running it from any
other worktree committed in a different one — with several worktrees live at
once, that is a way to push somebody else's half-finished branch.

---

## 6. OTHER DEPLOYMENT SECRETS

Everything else the services need is set as a Render environment variable on
the service, not in this repository: `DATABASE_URL`, `JWT_SECRET`,
`ENCRYPTION_KEY`, `OPENAI_API_KEY`, `RESEND_API_KEY`, Twilio credentials,
Stripe keys, Google/Microsoft OAuth client secrets.

`render.yaml` declares which variables a service needs; it does not contain
their values. Adding a new one is a change in the Render dashboard plus a
declaration in `render.yaml`, never a literal in a script.
