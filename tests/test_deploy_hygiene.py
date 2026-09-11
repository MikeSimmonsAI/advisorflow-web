"""DEPLOYMENT HYGIENE — the two things that must never come back.

WHY THIS IS A TEST AND NOT A NOTE IN A README
----------------------------------------------
Both of these were fixed once already and both came back, because the only
thing stopping them was somebody remembering:

  1. A RENDER API KEY IN PLAINTEXT IN A TRACKED FILE. `deploy.ps1` carries a
     comment saying its own hardcoded key was removed and must be rotated —
     and `deploy.bat` still had one, in the same repository, for months after.
     One file was cleaned and the other was not, because nothing checked.

  2. `git add .` / `git add -A` IN DEPLOYMENT AUTOMATION. It stages the whole
     working tree at the moment somebody runs a deploy. With several worktrees
     live at once that means another thread's work-in-progress, throwaway
     probe databases, scratch output, and any file that happens to hold a
     credential. `.gitignore` already carries a comment about `.probe_*.db`
     being committed by exactly this mechanism — the tell that it had already
     happened.

So the rule is checked, on every run, against the real tracked tree. A comment
asking people to be careful is not a control.

THE THIRD TEST IS ABOUT THE CHECKER ITSELF. A secret scanner that cries wolf
gets switched off, so `verdict()` has to tell a fixture from a key — and the
first version of it reported 132 "live" Resend keys that were all the tail of
`pre_need_lock_price`.
"""

import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import _secret_audit  # noqa: E402


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


# ── 1. NO LIVE CREDENTIAL IN A TRACKED FILE ────────────────────────────────

def test_no_live_shaped_credential_is_tracked():
    """The audit is the gate the deploy scripts run. It must be green here too.

    If this fails, the message names the file and line and NOT the value —
    a failing test that prints a secret copies it into CI output, a terminal
    buffer and a transcript.
    """
    findings = []
    for path in _tracked():
        full = os.path.join(REPO, path)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                for lineno, line in enumerate(fh, 1):
                    for label, pattern, minimum in _secret_audit.PATTERNS:
                        for match in pattern.finditer(line):
                            if _secret_audit.verdict(
                                    label, match.group(0), minimum) == "LIVE-SHAPED":
                                findings.append("%s:%d (%s)" % (path, lineno, label))
        except OSError:
            continue

    assert not findings, (
        "A live-shaped credential is committed. Move it to an environment "
        "variable, remove it from the file, and ROTATE it — removing it from "
        "HEAD does not revoke it. Locations: %s" % ", ".join(findings))


def test_deploy_bat_takes_its_key_from_the_environment():
    """The specific regression: deploy.bat had the key inline."""
    source = _read("deploy.bat")
    assert "RENDER_API_KEY" in source, \
        "deploy.bat must read the Render credential from the environment"
    assert not re.search(r"rnd_[A-Za-z0-9]{20,}", source), \
        "deploy.bat contains a Render-key-shaped literal"
    assert "Bearer %RENDER_API_KEY%" in source, \
        "the Authorization header must interpolate the environment variable"


# ── 2. NO INDISCRIMINATE STAGING IN DEPLOYMENT AUTOMATION ──────────────────

DEPLOY_SCRIPTS = ("deploy.bat", "deploy.ps1", "deploy_force.bat",
                  "git_push.bat", "render_deploy.bat")

# Comments explaining what was removed are the point of the fix, not a
# violation of it. Only an executable line counts.
_COMMENT = re.compile(r"^\s*(#|REM\b|::|//)", re.IGNORECASE)
_BLIND_ADD = re.compile(r"\bgit\s+add\s+(\.|-A)(\s|$)")


def _executable_lines(source):
    return [(n, l) for n, l in enumerate(source.splitlines(), 1)
            if l.strip() and not _COMMENT.match(l)]


@pytest.mark.parametrize("script", DEPLOY_SCRIPTS)
def test_deploy_scripts_do_not_stage_the_whole_tree(script):
    offenders = [(n, l.strip()) for n, l in _executable_lines(_read(script))
                 if _BLIND_ADD.search(l)]
    assert not offenders, (
        "%s stages the working tree indiscriminately: %s. Use `git add -u` "
        "(tracked modifications) and require new files to be added on "
        "purpose." % (script, offenders))


def test_the_deploy_scripts_that_ship_changes_stage_tracked_files():
    """The counterpart assertion: removing `-A` must not have removed staging.

    A deploy script that stages NOTHING is not safer, it is broken — it would
    push whatever happened to be committed already and report success, which
    is the stale-deploy failure `deploy.ps1`'s own header was written about.
    """
    for script in ("deploy.bat", "deploy.ps1", "git_push.bat"):
        source = _read(script)
        assert re.search(r"\bgit\s+add\s+-u\b", source), \
            "%s no longer stages tracked changes at all" % script


def test_force_redeploy_ships_no_changes():
    """Forcing a rebuild and shipping your working tree must not be one
    keystroke. deploy_force.bat commits empty, and refuses a dirty tree."""
    source = _read("deploy_force.bat")
    assert "--allow-empty" in source
    assert "git diff --quiet" in source, \
        "deploy_force.bat must refuse to run with uncommitted changes"


# ── 3. THE CHECKER CAN TELL A FIXTURE FROM A KEY ───────────────────────────

def _verdict(label, token):
    minimum = dict((l, m) for l, _p, m in _secret_audit.PATTERNS)[label]
    return _secret_audit.verdict(label, token, minimum)


@pytest.mark.parametrize("token", [
    "AC" + "a" * 32,                        # one repeated character
    "AC" + "deadbeef" * 4,                  # a repeated unit
    "rnd_" + "test" * 8,                    # filler word
    "sk-" + "x" * 40,                       # filler word
])
def test_obvious_fixtures_are_not_reported_as_live(token):
    """A gate that blocks a deploy over `ACaaaa…` is a gate somebody disables."""
    label = "twilio-sid" if token.startswith("AC") else (
        "render" if token.startswith("rnd_") else "openai")
    assert _verdict(label, token) == "PLACEHOLDER"


def test_an_ordinary_identifier_is_not_a_resend_key():
    """`re_[A-Za-z0-9_-]{16,}` matched the tail of `pre_need_lock_price` and
    reported 132 live keys. Every pattern is left-anchored now."""
    haystack = "MessageTrack.PRE_NEED_LOCK_PRICE pre_need_lock_price_variant"
    label, pattern, _m = [p for p in _secret_audit.PATTERNS
                          if p[0] == "resend"][0]
    assert not pattern.search(haystack)


def test_a_real_looking_key_is_reported_as_live():
    """The scanner has to actually catch something, or it is decoration."""
    assert _verdict("render", "rnd_9Qx4Lm2ZbW7pKv3TjR8sHc1FyNdE") == "LIVE-SHAPED"
