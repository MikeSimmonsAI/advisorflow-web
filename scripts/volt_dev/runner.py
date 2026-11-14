#!/usr/bin/env python3
"""VOLT development relay - the GitHub Actions side (#571).

Run by .github/workflows/volt-dev.yml from main. Every call to the VOLT API
carries a FRESH GitHub OIDC identity token (audience volt-dev-relay); the API
verifies the signature, this repository, this workflow on main, and refuses a
token it has seen before. No shared secret exists.

    runner.py next      ask for work; handle merges, rollback prep and deploy
                        proof here; for a code job, prepare the branch and leave
                        the job for the Claude step. Writes step outputs.
    runner.py finish    after Claude (or a rollback): scope check, tests, build,
                        push the branch, open the PR, report the result.
    runner.py fail MSG  report a failure for the leased job.

Standard library only. Uses `git` and the Actions GITHUB_TOKEN (gh api) for
branches and PRs. Never prints a token.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("VOLT_API", "https://api.voltmarketing.live").rstrip("/")
FRONTEND = os.environ.get("VOLT_FRONTEND", "https://voltmarketing.live").rstrip("/")
REPO = os.environ.get("GITHUB_REPOSITORY", "MikeSimmonsAI/advisorflow-web")
TMP = os.environ.get("RUNNER_TEMP", "/tmp")
STATE = os.path.join(TMP, "volt-dev-job.json")
SCOPE = (r"^app/services/volt/(?!dev(?:relay|dispatch|oauth)\.py\Z)", r"^app/routers/volt_(?:router|leads_router)\.py\Z",
         r"^app/models/volt_leads_models\.py\Z", r"^alembic/versions/[0-9a-f]+_volt_leads_[a-z0-9_]+\.py\Z",
         r"^frontend/src/pages/volt/",
         r"^frontend/public/brands/volt/", r"^tests/test_volt[^/]*\.py\Z", r"^docs/volt/")


def sh(*cmd, check=True, capture=True, env=None):
    r = subprocess.run(list(cmd), capture_output=capture, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(cmd[:3]), (r.stderr or r.stdout or "")[-800:]))
    return (r.stdout or "").strip()


def oidc() -> str:
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=volt-dev-relay"
    req = urllib.request.Request(url, headers={"Authorization": "bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["value"]


def api(path, body=None):
    for attempt in range(3):
        req = urllib.request.Request(API + path, method="POST", data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + oidc()})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:500]
            if e.code >= 500 and attempt < 2:
                time.sleep(10)
                continue
            raise RuntimeError("VOLT API %s -> %s %s" % (path, e.code, msg))
        except urllib.error.URLError:
            if attempt < 2:
                time.sleep(10)
                continue
            raise


def report(job_id, **body):
    out = api("/volt/dev/runner/jobs/%s/report" % job_id, body)
    print("reported %s -> %s" % (body.get("status"), out.get("status")))
    return out


def output(**kv):
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as f:
        for k, v in kv.items():
            f.write("%s=%s\n" % (k, v))


def gh(method, path, body=None):
    args = ["gh", "api", "-X", method, path]
    if body is not None:
        args += ["--input", "-"]
    r = subprocess.run(args, input=json.dumps(body) if body is not None else None, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("gh api %s %s failed: %s" % (method, path, (r.stderr or r.stdout)[-500:]))
    return json.loads(r.stdout or "null")


def fetch_json(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Cache-Control": "no-cache"}), timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        print("could not read %s: %s" % (url, type(e).__name__))
        return {}


def contains(running, sha):
    if not running or running == "unknown":
        return False
    sh("git", "fetch", "-q", "origin", "main", check=False)
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, running]).returncode == 0


def deploy_proof(watch):
    """Confirm merged jobs against what is ACTUALLY running."""
    if not watch:
        return
    backend = fetch_json(API + "/version").get("commit")
    frontend = fetch_json(FRONTEND + "/build-info.json?t=%d" % int(time.time())).get("commit")
    for w in watch:
        need = [t for t in (w.get("targets") or "").split(",") if t]
        running = {"backend": backend, "frontend": frontend}
        ok = all(contains(running.get(t), w["merge_sha"]) for t in need)
        print("%s deploy check: %s" % (w["ref"], {t: (running.get(t) or "?")[:7] for t in need}))
        if ok:
            detail = "Running in production: " + ", ".join("%s %s" % (t, running[t][:7]) for t in need)
            report(w["id"], status="deployed", deploy_detail=detail, detail=detail)


def changed_files():
    sh("git", "fetch", "-q", "origin", "main")
    out = sh("git", "diff", "--no-renames", "--name-only", "-z", "origin/main...HEAD")
    return [p for p in out.split("\0") if p]


def out_of_scope(paths):
    return [p for p in paths if not any(re.match(rx, p) for rx in SCOPE)]


def cmd_next():
    # Presence only (the workflow computes true/false; values never reach here).
    work = api("/volt/dev/runner/next", {"claude_credentials": os.environ.get("HAS_CLAUDE", "") == "true",
                                         "reason": (os.environ.get("START_REASON") or "")[:200]})
    try:
        deploy_proof(work.get("deploy_watch") or [])
    except Exception as e:  # noqa: BLE001 - a deploy check never blocks other work
        print("deploy check skipped: %s" % e)
    action = work.get("action")
    job = work.get("job") or {}
    output(more=int(work.get("more") or 0))
    print("action: %s %s (more waiting: %s)" % (action, job.get("ref", ""), work.get("more") or 0))
    if action == "merge":
        return merge(job)
    if action in ("code", "revert"):
        with open(STATE, "w") as f:
            json.dump(job, f)
        try:
            sh("git", "config", "user.name", "VOLT development relay")
            sh("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
            sh("git", "checkout", "-q", "-B", job["branch"], "origin/main")
            if action == "revert":
                sh("git", "revert", "--no-edit", job["revert_sha"])
            request = "%s\n\n%s\n" % (job["title"], job["request"])
            with open(os.path.join(TMP, "volt-dev-request.md"), "w") as f:
                f.write(request)
        except Exception as e:  # noqa: BLE001
            report(job["id"], status="failed", detail="Could not prepare the branch: %s" % e)
            output(action="none")
            return
        output(action=action, job_ref=job["ref"], branch=job["branch"])
        return
    output(action="none")


def merge(job):
    pr = gh("GET", "repos/%s/pulls/%d" % (REPO, job["pr_number"]))
    try:
        if pr.get("state") != "open":
            raise RuntimeError("PR #%d is %s." % (job["pr_number"], pr.get("state")))
        if pr["head"]["sha"] != job["approved_sha"]:
            raise RuntimeError("The PR changed after approval (now %s, approved %s). Retry to test it again."
                               % (pr["head"]["sha"][:7], job["approved_sha"][:7]))
        file_records = gh("GET", "repos/%s/pulls/%d/files?per_page=100" % (REPO, job["pr_number"]))
        if len(file_records) != pr["changed_files"]:
            raise RuntimeError("Cannot verify scope for every changed file. Split the pull request.")
        files = [path for f in file_records for path in (f["filename"], f.get("previous_filename")) if path]
        bad = out_of_scope(files)
        if bad:
            raise RuntimeError("Changes outside VOLT scope: %s" % ", ".join(bad[:5]))
        res = gh("PUT", "repos/%s/pulls/%d/merge" % (REPO, job["pr_number"]),
                 {"merge_method": "squash", "sha": job["approved_sha"],
                  "commit_title": "%s: %s (#%d)" % (job["ref"], job["title"], job["pr_number"])})
    except Exception as e:  # noqa: BLE001
        report(job["id"], status="failed", detail="Merge refused: %s" % e)
        output(action="none")
        return
    report(job["id"], status="merged", merge_sha=res["sha"], merged_head_sha=job["approved_sha"],
           detail="Merged as %s. Render is deploying main." % res["sha"][:7])
    output(action="none")


def cmd_finish(claude_outcome):
    with open(STATE) as f:
        job = json.load(f)
    jid = job["id"]
    try:
        if sh("git", "status", "--porcelain"):
            sh("git", "add", "-A")
            sh("git", "commit", "-q", "-m", "%s: %s" % (job["ref"], job["title"]))
        files = changed_files()
        if not files:
            run = "%s/%s/actions/runs/%s" % (os.environ.get("GITHUB_SERVER_URL", "https://github.com"), REPO,
                                             os.environ.get("GITHUB_RUN_ID", ""))
            if claude_outcome == "failure":
                detail = ("Claude's step failed before making a change (credential, usage limit or timeout). "
                          "Run log: %s" % run)
            else:
                detail = "Claude finished without changing any files (%s). Add detail and retry." % claude_outcome
            report(jid, status="failed", detail=detail)
            return
        blocker_doc = "docs/volt/dev-relay/%s.md" % job["ref"]
        if claude_outcome != "rollback" and blocker_doc in files and all(p.startswith("docs/volt/") for p in files):
            with open(blocker_doc, encoding="utf-8") as f:
                _, marker, reason = f.read().partition("VOLT_IMPLEMENTATION_BLOCKED:")
            if marker:
                report(jid, status="failed", detail="Implementation blocked: " + reason.strip()[:350])
                return
        bad = out_of_scope(files)
        if bad:
            report(jid, status="failed", changed_files=files,
                   detail="Refused: the change touched files outside VOLT Marketing (%s). Nothing was pushed." % ", ".join(bad[:5]))
            return
        report(jid, status="coding_completed", changed_files=files, branch=job["branch"],
               detail="Claude finished (%s). Running tests and the build." % claude_outcome)
        env = dict(os.environ, JWT_SECRET="ci-only-" + "x" * 32, APP_ENV="test", PYTHONDONTWRITEBYTECODE="1")
        t = subprocess.run(["python", "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:warnings"]
                           + sorted(set(["tests/test_volt_recruiting.py", "tests/test_volt_mail.py",
                                         "tests/test_volt_access_booking.py", "tests/test_volt_dev_relay.py"]
                                        + [p for p in files if p.startswith("tests/test_volt")])),
                           capture_output=True, text=True, env=env)
        tests_line = ((t.stdout or "").strip().splitlines() or ["(no output)"])[-1]
        b = subprocess.run(["bash", "-lc", "cd frontend && npm ci --no-audit --no-fund >/dev/null && npm run build"],
                           capture_output=True, text=True)
        build_line = "build ok" if b.returncode == 0 else "build FAILED: " + (b.stderr or b.stdout)[-300:]
        passed = t.returncode == 0 and b.returncode == 0
        summary = "pytest: %s; frontend %s" % (tests_line, build_line)
        sh("git", "push", "-q", "-f", "origin", "HEAD:refs/heads/%s" % job["branch"])
        head = sh("git", "rev-parse", "HEAD")
        body = ("VOLT development relay **%s** (attempt %s), requested through VOLT Operations.\n\n"
                "**Request**\n\n%s\n\n**Changed files**\n\n%s\n\n**Runner tests**: %s\n\n"
                "Merge happens only after one of the two authorized principals approves this exact commit "
                "(%s) in VOLT Operations → Development.\n\nRefs #571"
                % (job["ref"], job.get("attempt", 1), job["request"][:3000],
                   "\n".join("- `%s`" % p for p in files[:60]), summary, head[:7]))
        prs = gh("GET", "repos/%s/pulls?state=open&head=%s:%s" % (REPO, REPO.split("/")[0], job["branch"]))
        if prs:
            pr = prs[0]
        else:
            pr = gh("POST", "repos/%s/pulls" % REPO, {"title": "%s: %s" % (job["ref"], job["title"])[:240],
                                                      "head": job["branch"], "base": "main", "body": body})
        if passed:
            try:
                gh("POST", "repos/%s/actions/workflows/redesign-approval.yml/dispatches" % REPO,
                   {"ref": "main", "inputs": {"pull_number": str(pr["number"])}})
            except RuntimeError as exc:
                print("Could not dispatch required PR check: %s" % exc)
            report(jid, status="ready_to_test", tests_passed=True, tests_summary=summary, pr_number=pr["number"],
                   pr_url=pr["html_url"], head_sha=head, branch=job["branch"], changed_files=files,
                   detail="Tests passed. Ready to test and approve.")
        else:
            report(jid, status="tests_failed", tests_passed=False, tests_summary=summary, pr_number=pr["number"],
                   pr_url=pr["html_url"], head_sha=head, changed_files=files, detail="Tests or build failed. Retry after a fix.")
    except Exception as e:  # noqa: BLE001
        try:
            report(jid, status="failed", detail="Runner error: %s" % e)
        finally:
            raise


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "next"
    if cmd == "next":
        cmd_next()
    elif cmd == "finish":
        cmd_finish(sys.argv[2] if len(sys.argv) > 2 else "unknown")
    elif cmd == "fail":
        with open(STATE) as f:
            report(json.load(f)["id"], status="failed", detail=" ".join(sys.argv[2:])[:500] or "Runner failed")
    else:
        sys.exit("usage: runner.py next|finish|fail")
