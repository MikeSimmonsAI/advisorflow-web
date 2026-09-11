"""DEPLOYMENT HYGIENE - the things that must never come back.

WHY THIS IS A TEST AND NOT A NOTE IN A README
----------------------------------------------
Each of these was fixed once already and came back, because the only thing
stopping it was somebody remembering:

  1. A RENDER API KEY IN PLAINTEXT IN A TRACKED FILE. `deploy.ps1` carries a
     comment saying its own hardcoded key was removed and must be rotated -
     and `deploy.bat` still had one, in the same repository, for months after.
     One file was cleaned and the other was not, because nothing checked.

  2. WILDCARD STAGING IN DEPLOYMENT AUTOMATION. `git add .` and `git add -A`
     stage the whole working tree at the moment somebody runs a deploy. With
     several worktrees live at once that means another thread's
     work-in-progress, throwaway probe databases, scratch output, and any file
     that happens to hold a credential. `.gitignore` still carries a comment
     about `.probe_*.db` being committed by exactly this mechanism - the tell
     that it had already happened.

     `git add -u` was the first attempt at a fix and is banned here too. It is
     narrower and it is the same mistake: the deploy script still chooses the
     contents of the commit, and it still chooses by wildcard. A deploy ships
     what a person decided to ship.

  3. A DEPLOY SCRIPT THAT WRITES TO THE REPOSITORY. Staging, committing,
     `reset --hard`, discarding a dirty tree - all of it is how work gets lost
     or silently shipped. Deployment reads the repository state and refuses;
     it does not fix it.

THE LAST SECTION IS ABOUT THE CHECKER ITSELF. A secret scanner that cries wolf
gets switched off, so `verdict()` has to tell a fixture from a key - the first
version reported 132 "live" Resend keys that were all the tail of
`pre_need_lock_price`. A scanner that never fires is equally useless, so the
opposite is asserted too.
"""

import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import _secret_audit  # noqa: E402

AUDIT = os.path.join(REPO, "scripts", "_secret_audit.py")

# FABRICATED KEYS, ASSEMBLED AT RUNTIME SO THIS FILE CONTAINS NONE.
#
# These tests need values the scanner classifies as LIVE-SHAPED - a scanner
# that only ever sees placeholders proves nothing. Writing them as literals
# would make this file itself fail `test_no_live_shaped_credential_is_tracked`
# forever, so the prefix is concatenated on: the scanner's patterns require the
# vendor prefix to be immediately followed by the body, and a `+` between them
# means no physical line here matches. Neither value is or ever was real.
_FAKE_A = "rnd" + "_" + "9Qx4Lm2ZbW7pKv3TjR8sHc1FyNdE"
_FAKE_B = "rnd" + "_" + "7Hj2Wq5ZnB8xKt4RvM1cYd3PfLsE"


def _tracked():
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                         text=True)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [p for p in out.stdout.splitlines() if p.strip()]


def _read(relpath):
    with open(os.path.join(REPO, relpath), "r", encoding="utf-8",
              errors="ignore") as fh:
        return fh.read()


# Comments explaining what was removed are the point of the fix, not a
# violation of it. Only an executable line counts.
_COMMENT = re.compile(r"^\s*(#|REM\b|::|//)", re.IGNORECASE)

# A line that PRINTS `git add <path>` is telling the operator what to do next.
# That is the whole replacement for the behaviour being removed, so it must not
# read as the behaviour itself.
_PRINTS = re.compile(r"^\s*(echo\b|Write-Host\b|Write-Output\b)", re.IGNORECASE)


def _executable_lines(source):
    """Lines that DO something: not comments, not console output."""
    return [(n, l) for n, l in enumerate(source.splitlines(), 1)
            if l.strip() and not _COMMENT.match(l) and not _PRINTS.match(l)]


# -- 1. NO LIVE CREDENTIAL IN A TRACKED FILE --------------------------------

def test_no_live_shaped_credential_is_tracked():
    """The audit is the gate the deploy scripts run. It must be green here too.

    If this fails, the message names the file and line and NOT the value - a
    failing test that prints a secret copies it into CI output, a terminal
    buffer and a transcript. `scan()` never returns the token at all, which is
    what makes that guarantee structural rather than a promise.
    """
    paths = [os.path.join(REPO, p) for p in _tracked()]
    findings = _secret_audit.scan([p for p in paths if os.path.isfile(p)])
    live = ["%s:%d (%s)" % (os.path.relpath(f[0], REPO), f[1], f[2])
            for f in findings if f[4] == "LIVE-SHAPED"]

    assert not live, (
        "A live-shaped credential is committed. Move it to an environment "
        "variable, remove it from the file, and ROTATE it - removing it from "
        "HEAD does not revoke it. Locations: %s" % ", ".join(live))


def test_deploy_bat_takes_its_key_from_the_environment():
    """The specific regression: deploy.bat had the key inline."""
    source = _read("deploy.bat")
    assert "RENDER_API_KEY" in source, \
        "deploy.bat must read the Render credential from the environment"
    assert not re.search(r"rnd_[A-Za-z0-9]{20,}", source), \
        "deploy.bat contains a Render-key-shaped literal"
    assert "Bearer %RENDER_API_KEY%" in source, \
        "the Authorization header must interpolate the environment variable"


@pytest.mark.parametrize("script", ["deploy.bat", "deploy.ps1",
                                    "render_deploy.bat"])
def test_no_deploy_script_embeds_a_credential(script):
    """Not just deploy.bat. The last one was missed because only one was
    checked."""
    findings = _secret_audit.scan([os.path.join(REPO, script)])
    live = [f for f in findings if f[4] == "LIVE-SHAPED"]
    assert not live, "%s embeds a live-shaped credential at line(s) %s" % (
        script, [f[1] for f in live])


def test_a_missing_render_key_fails_safely_where_it_is_actually_needed():
    """RENDER_API_KEY is only needed for the API nudge, and the nudge is not
    the deploy - the push is. So a missing key must NOT abort the deploy, and
    must not be silently ignored either: it says so and carries on."""
    bat = _read("deploy.bat")
    assert '"%RENDER_API_KEY%"==""' in bat, \
        "deploy.bat must check for the key before using it"
    # The guard exits 0 after the push, not 1: nothing failed.
    tail = bat.split('"%RENDER_API_KEY%"==""', 1)[1]
    assert "exit /b 0" in tail.split("curl", 1)[0], \
        "a missing nudge credential must not be reported as a failed deploy"

    rd = _read("render_deploy.bat")
    # render_deploy.bat does NOTHING but nudge, so there the key is required.
    assert '"%RENDER_API_KEY%"==""' in rd and "exit /b 1" in rd, \
        "render_deploy.bat has nothing to do without the key and must say so"


# -- 2. NO WILDCARD STAGING, AND NO STAGING AT ALL IN A DEPLOY --------------

DEPLOY_SCRIPTS = ("deploy.bat", "deploy.ps1", "deploy_force.bat",
                  "git_push.bat", "render_deploy.bat", "_wt.bat",
                  "scripts/_dep.ps1", "scripts/commit_god07.bat")

_BLIND_ADD = re.compile(r"\bgit\s+add\s+(-A\b|--all\b|-u\b|--update\b|\.(\s|$))")


@pytest.mark.parametrize("script", DEPLOY_SCRIPTS)
def test_no_script_stages_by_wildcard(script):
    """`.`, `-A` and `-u` are all banned. A deploy helper may stage a path
    somebody named; it may never stage 'whatever is different right now'."""
    offenders = [(n, l.strip()) for n, l in _executable_lines(_read(script))
                 if _BLIND_ADD.search(l)]
    assert not offenders, (
        "%s stages by wildcard: %s. Deployment automation must not decide "
        "which source changes belong in a commit - stage explicit paths."
        % (script, offenders))


# The production deploy path. These ship to main; they must not write to the
# repository at all beyond pushing what is already committed.
PRODUCTION_DEPLOY = ("deploy.bat", "deploy_force.bat", "render_deploy.bat")


@pytest.mark.parametrize("script", PRODUCTION_DEPLOY)
def test_production_deploy_scripts_never_stage_anything(script):
    """Not narrower staging - none. The flow is: a person stages and commits,
    then the deploy script verifies and pushes."""
    offenders = [(n, l.strip()) for n, l in _executable_lines(_read(script))
                 if re.search(r"\bgit\s+(-c\s+\S+\s+)?add\b", l)]
    assert not offenders, (
        "%s runs `git add`. A production deploy script ships an "
        "already-prepared commit: %s" % (script, offenders))


def test_deploy_ps1_stages_only_the_bundle_it_built_itself():
    """deploy.ps1 is the exception and the exception is one explicit path.

    It builds frontend/dist in step 4 and that output is deliberately
    committed - the static site serves the bundle rather than building on
    Render - so it stages exactly `frontend/dist` by name. Anything else is
    the old behaviour coming back.
    """
    adds = [l.strip() for _n, l in _executable_lines(_read("deploy.ps1"))
            if re.search(r"\bgit\s+add\b", l)]
    assert adds == ["git add -f frontend/dist"], \
        "deploy.ps1 should stage only frontend/dist, found: %s" % adds


@pytest.mark.parametrize("script", ("deploy.bat", "deploy.ps1",
                                    "deploy_force.bat"))
def test_a_dirty_worktree_stops_the_deploy(script):
    """A dirty tree is the operator's decision to finish, not the script's to
    resolve. It must stop, and the exit must be a failure so a wrapper or a
    CI step cannot read it as a successful deploy."""
    source = _read(script)
    assert re.search(r"git (status --porcelain|diff --quiet)", source), \
        "%s does not inspect the working tree" % script
    assert re.search(r"(exit /b 1|exit 1)", source), \
        "%s never refuses" % script
    assert re.search(r"(?i)(refus|not clean|uncommitted|does not ship)", source), \
        "%s does not say why it stopped" % script


@pytest.mark.parametrize("script", ("deploy.bat", "deploy.ps1",
                                    "deploy_force.bat"))
def test_the_operator_is_shown_what_is_in_the_way(script):
    """Stopping without naming the files just moves the guessing to the human.
    Names only - never contents, never a diff of a file that might hold a
    credential."""
    source = _read(script)
    assert re.search(r"git status (--short|--porcelain)", source), \
        "%s must list the files that blocked it" % script


@pytest.mark.parametrize("script", ("deploy.bat", "deploy.ps1",
                                    "deploy_force.bat", "git_push.bat",
                                    "_wt.bat"))
def test_no_script_discards_work(script):
    """`reset --hard` in deploy.ps1 once ate a full session of backend work.
    `del .git\\index.lock` in _wt.bat could corrupt an index another worktree
    was mid-write on. Neither belongs in automation."""
    source = _read(script)
    for forbidden in (r"reset\s+--hard", r"checkout\s+--\s+\.",
                      r"clean\s+-[a-z]*f", r"del\s+.*index\.lock",
                      r"Remove-Item.*index\.lock"):
        offenders = [(n, l.strip()) for n, l in _executable_lines(source)
                     if re.search(forbidden, l, re.IGNORECASE)]
        assert not offenders, "%s can destroy work: %s" % (script, offenders)


def test_the_dev_helper_stages_only_paths_the_caller_names():
    """git_push.bat is the one helper allowed to create a commit. It may stage
    only what it is handed, and it must refuse with no paths rather than
    falling back to 'everything'."""
    source = _read("git_push.bat")
    assert 'git add -- "%~1"' in source, \
        "git_push.bat must stage the caller's explicit paths"
    assert 'if "%~2"=="" goto :usage' in source, \
        "git_push.bat must require at least one path, not default to the tree"


def test_force_redeploy_ships_no_changes():
    """Forcing a rebuild and shipping your working tree must not be one
    keystroke. deploy_force.bat commits empty, and refuses a dirty tree."""
    source = _read("deploy_force.bat")
    assert "--allow-empty" in source
    assert "git diff --quiet" in source, \
        "deploy_force.bat must refuse to run with uncommitted changes"


# -- 3. THE DEPLOY PATH ITSELF STILL WORKS ----------------------------------

def test_the_credential_audit_runs_before_the_push():
    """Order matters. An audit that runs after the push is a report, not a
    gate - the key is already on GitHub by then."""
    for script in ("deploy.bat", "deploy.ps1"):
        source = _read(script)
        audit_at = source.find("_secret_audit.py")
        push_at = source.find("git push")
        assert audit_at != -1, "%s does not run the credential audit" % script
        assert push_at != -1, "%s does not push" % script
        assert audit_at < push_at, \
            "%s pushes before auditing" % script


def test_auto_deploy_by_push_to_main_is_preserved():
    """This is what actually deploys production - the API call is a nudge. The
    cleanup must not have turned the mechanism off in render.yaml."""
    blueprint = _read("render.yaml")
    # Render auto-deploys by default, so the invariant is the ABSENCE of an
    # opt-out. Asserting `autoDeploy: true` would pass only by accident of
    # somebody writing a redundant key.
    assert not re.search(r"autoDeploy:\s*(false|no|off)", blueprint, re.I), \
        "render.yaml turns off auto-deploy - the push would stop deploying"
    assert "preDeployCommand" in blueprint, \
        "the migration gate before promotion is missing"
    assert "git push" in _read("deploy.bat"), \
        "deploy.bat must still push - the push is the deploy"


# -- 4. THE CHECKER CAN TELL A FIXTURE FROM A KEY ---------------------------

def _verdict(label, token):
    minimum = dict((l, m) for l, _p, m in _secret_audit.PATTERNS)[label]
    return _secret_audit.verdict(token, minimum)


@pytest.mark.parametrize("token,label", [
    ("AC" + "a" * 32, "twilio-sid"),           # one repeated character
    ("AC" + "deadbeef" * 4, "twilio-sid"),     # a repeated unit
    ("rnd_" + "test" * 8, "render"),           # filler word
    ("sk-" + "x" * 40, "openai"),              # filler word
])
def test_obvious_fixtures_are_not_reported_as_live(token, label):
    """A gate that blocks a deploy over `ACaaaa...` is a gate somebody
    disables inside a week."""
    assert _verdict(label, token) == "PLACEHOLDER"


def test_an_ordinary_identifier_is_not_a_resend_key():
    """`re_[A-Za-z0-9_-]{16,}` matched the tail of `pre_need_lock_price` and
    reported 132 live keys. Every pattern is left-anchored now."""
    haystack = "MessageTrack.PRE_NEED_LOCK_PRICE pre_need_lock_price_variant"
    _label, pattern, _m = [p for p in _secret_audit.PATTERNS
                           if p[0] == "resend"][0]
    assert not pattern.search(haystack)


def test_a_real_looking_key_is_reported_as_live():
    """The scanner has to actually catch something, or it is decoration."""
    assert _verdict("render", _FAKE_A) == "LIVE-SHAPED"


def test_the_fixture_marker_suppresses_only_the_line_it_is_on(tmp_path):
    """The one explicit exclusion. It has to be narrow enough that it cannot
    become the way a real key gets through: the marked line is suppressed and
    the very next line, with an equally live-shaped value, is not."""
    sample = tmp_path / "sample.txt"
    sample.write_text(
        "key = '%s'  # secret-audit: fixture\n"
        "other = '%s'\n" % (_FAKE_A, _FAKE_B),
        encoding="utf-8")
    findings = _secret_audit.scan([str(sample)])
    verdicts = [f[4] for f in sorted(findings, key=lambda f: f[1])]
    assert verdicts == ["FIXTURE", "LIVE-SHAPED"], \
        "the marker must cover its own line and no other"


def test_the_audit_exits_zero_on_the_current_tree():
    """The gate the deploy scripts actually call. If this is red, every deploy
    is blocked - and a permanently blocked gate is a gate that gets bypassed."""
    out = subprocess.run([sys.executable, AUDIT], cwd=REPO,
                         capture_output=True, text=True)
    assert out.returncode == 0, \
        "the credential audit fails against the current tree:\n%s" % out.stdout


def test_the_audit_exits_nonzero_when_a_live_key_is_introduced(tmp_path):
    """The other half. An audit that can only pass proves nothing, so a
    controlled file with a fabricated live-shaped key must fail it."""
    planted = tmp_path / "planted.env"
    planted.write_text("RENDER_API_KEY=%s\n" % _FAKE_B, encoding="utf-8")
    out = subprocess.run([sys.executable, AUDIT, str(planted)], cwd=REPO,
                         capture_output=True, text=True)
    assert out.returncode == 1, \
        "a planted live-shaped credential did not fail the audit"
    assert "LIVE-SHAPED" in out.stdout


def test_the_audit_never_prints_the_credential(tmp_path):
    """The whole point of reporting shape instead of value. A scanner that
    echoes what it found has copied the secret into a log and a transcript."""
    secret = _FAKE_B
    planted = tmp_path / "planted.env"
    planted.write_text("RENDER_API_KEY=%s\n" % secret, encoding="utf-8")
    out = subprocess.run([sys.executable, AUDIT, str(planted)], cwd=REPO,
                         capture_output=True, text=True)
    combined = out.stdout + out.stderr
    assert secret not in combined, "the audit printed the credential"
    # Not even a recognisable chunk of it.
    assert secret[4:16] not in combined, "the audit printed part of the credential"
    assert "LIVE-SHAPED" in combined and "planted.env" in combined, \
        "it must still say what it found and where"
