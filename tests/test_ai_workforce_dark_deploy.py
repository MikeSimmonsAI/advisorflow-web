"""
═══════════════════════════════════════════════════════════════════════════
THE DEPLOYMENT IS DARK, AND STAYS DARK BY ACCIDENT OF NOTHING
═══════════════════════════════════════════════════════════════════════════

Everything else in this suite proves the ENGINE refuses. This file is about
the deployment: what production is actually configured to allow.

The engine reads exactly three environment variables, and every one of them
is absent from `render.yaml`:

  AI_WORKFORCE_LLM_ENABLED   the OpenAI provider will not resolve without it,
                             so planning runs on the credential-free
                             deterministic provider and no model call leaves
                             the process.
  AI_WORKFORCE_LIVE_VOICE    live AI voice. Out of scope for this launch; the
                             real voice adapter raises unconditionally, and
                             this is the second lock on the same door.
  AI_WORKFORCE_KILL          the platform-wide brake. Absent means NOT
                             engaged — which is deliberately the safe reading
                             here only because activation already resolves to
                             `off`; the kill switch is an override, never the
                             thing that makes the system safe.

WHY ASSERT THE ABSENCE RATHER THAN TRUST IT. An environment variable added to
`render.yaml` changes production behaviour with no code review of the code it
changes, and "someone added a key to the deploy file" is not a diff anybody
reads as a behaviour change. Adding one to enable a capability should have to
delete a test that says, in words, that this was meant to stay off.

THIS FILE ASSERTS NOTHING ABOUT RENDER'S DASHBOARD. A variable set there is
invisible from the repository, which is exactly why the engine does not rely
on any of these being absent: activation resolves to `off` from the database,
no AI employee is purchasable so none holds an entitlement, and the tool
gateway refuses reach-shaped tools below the controlled stage regardless of
every variable above. This is a fourth lock, not the first.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDER = os.path.join(ROOT, "render.yaml")

# Every variable the engine reads, and what setting it would turn on.
ENGINE_SWITCHES = {
    "AI_WORKFORCE_LLM_ENABLED": "outbound model calls",
    "AI_WORKFORCE_LIVE_VOICE": "live AI voice",
    "AI_WORKFORCE_OPENAI_MODEL": "a specific model for those calls",
}

TRUTHY = ("true", "1", "yes", "on")


def _render():
    return open(RENDER, encoding="utf-8").read()


def test_no_workforce_switch_is_declared_in_the_deploy_file():
    declared = []
    src = _render()
    for key, what in ENGINE_SWITCHES.items():
        if re.search(r"^\s*-?\s*key:\s*%s\s*$" % re.escape(key), src, re.M):
            declared.append("%s (%s)" % (key, what))
    assert not declared, (
        "render.yaml declares AI Workforce switches that this launch is "
        "supposed to ship without: %s. If one of these is genuinely being "
        "turned on, that is a decision with a blast radius, and deleting this "
        "assertion is the right way to record it." % ", ".join(declared))


def test_the_kill_switch_is_not_pinned_off_in_the_deploy_file():
    """The brake must stay operable.

    AI_WORKFORCE_KILL absent means "not engaged", which is correct. What must
    never appear is the variable pinned to a falsey literal in the deploy
    file, because that reads as a considered decision that the brake is off
    and makes engaging it a deploy rather than an environment change.
    """
    src = _render()
    m = re.search(r"key:\s*AI_WORKFORCE_KILL\s*\n\s*value:\s*['\"]?(\w+)",
                  src)
    assert m is None, (
        "render.yaml pins AI_WORKFORCE_KILL to %r. Leave it undeclared so the "
        "platform brake can be engaged from the environment without shipping "
        "a commit." % m.group(1))


def test_every_environment_switch_the_engine_reads_is_listed_here():
    """A new switch must not slip past this file unnoticed.

    This test exists because the assertions above are only as good as the
    list they iterate. A fourth variable added to the engine and not added
    here would be unasserted, and the file would still pass and still claim
    to cover the deployment.
    """
    found = set()
    for sub in ("services/workforce",):
        base = os.path.join(ROOT, "app", *sub.split("/"))
        for fn in sorted(os.listdir(base)):
            if not fn.endswith(".py"):
                continue
            src = open(os.path.join(base, fn), encoding="utf-8").read()
            # Any AI_WORKFORCE_* STRING LITERAL, not only a direct
            # `os.environ.get(...)`. activation.py reads both of its switches
            # through a local `_env_flag(name, default)` helper, which a
            # call-shaped pattern misses entirely — and missing one is the
            # exact failure this test is here to prevent. The lowercase table
            # name `ai_workforce_activations` does not match, because the
            # pattern requires uppercase.
            for m in re.finditer(r"[\"'](AI_WORKFORCE_[A-Z0-9_]+)[\"']", src):
                found.add(m.group(1))

    known = set(ENGINE_SWITCHES) | {"AI_WORKFORCE_KILL"}
    unlisted = sorted(found - known)
    assert not unlisted, (
        "The engine reads these environment variables, and this file says "
        "nothing about whether production sets them: %s. Add each to "
        "ENGINE_SWITCHES with what it turns on." % ", ".join(unlisted))

    # And the reverse: a name listed here that nothing reads is a stale
    # assurance, the same defect test_exempt_list_has_no_stale_entries exists
    # for in the plan-limits gate.
    stale = sorted(known - found)
    assert not stale, (
        "These are asserted about but nothing in the engine reads them any "
        "more: %s." % ", ".join(stale))
