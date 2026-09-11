"""
═══════════════════════════════════════════════════════════════════════════
T6 — THE ENGINE NAMES NOBODY
═══════════════════════════════════════════════════════════════════════════

The architecture is one engine at the platform layer, configured per brand and
per customer. That claim is only true while the engine contains no brand, no
customer and no person — and it stops being true one string at a time, usually
in a docstring, where nothing fails and nobody looks.

This file caught exactly that once already: an illustrative brand name in the
`AIBrandOffering` docstring, put there to explain white-labelling and doing the
opposite. A comment is where the first hard-coded brand gets written, because
a comment is the one place a name feels harmless.

WHAT IS FORBIDDEN, AND WHY EACH ONE

  * Named brands and customers. If the engine knows one, it is not a
    white-label engine; it is one customer's engine with a settings screen.
  * A named person or their email. Root authority is a ROLE. An identity
    written into source is an authority nobody can revoke without a deploy.
  * Product names that do not exist. A name invented in passing gets
    implemented later by somebody who assumes it was real.

WHAT IS DELIBERATELY ALLOWED

  * `AdvisorFlow`, the platform itself. The engine is allowed to know what it
    is; §53 says the engine is AdvisorFlow and the NAMES are white-label.
  * Anything a brand or customer types at runtime. `display_name` on a brand
    offering is the one place a brand's own wording reaches a customer, and it
    is a column, not a literal.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
FE = os.path.join(ROOT, "frontend", "src")

# Every file that makes up the engine and its surfaces. Listed by location
# rather than by name so a file added tomorrow is covered the day it lands.
ENGINE_DIRS = [os.path.join(APP, "services", "workforce")]
ENGINE_FILES = [
    os.path.join(APP, "models", "workforce_models.py"),
    os.path.join(APP, "routers", "workforce_router.py"),
    os.path.join(APP, "routers", "god_workforce_router.py"),
    os.path.join(FE, "pages", "AITeam.jsx"),
    os.path.join(FE, "pages", "AIEmployeeDetail.jsx"),
    os.path.join(FE, "pages", "god", "GodWorkforce.jsx"),
]

# Each entry states what it is, because a bare blocklist invites somebody to
# delete the line they happen to trip over.
FORBIDDEN = {
    "restland": "a real customer",
    "atlantis": "a real customer",
    "evosys": "a real brand",
    "lakemont": "the Demo Suite's fictional tenant — still a named tenant",
    "leasebreaker": "a product that does not exist and must never be invented",
    "mike simmons": "a person; root authority is a role, never an identity",
    "simmonsmj242": "a person's email address",
}

# A second root control plane by any other name. §1: God Mode is root, full
# stop, and the way a second one arrives is as a word before it is as a table.
FORBIDDEN_ROLES = [
    "platform superadmin", "platform super admin", "super god", "supergod",
    "root admin", "master admin", "system owner",
]


def _engine_sources():
    for d in ENGINE_DIRS:
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".py") and fn != "__pycache__":
                yield os.path.join(d, fn)
    for p in ENGINE_FILES:
        yield p


def _rel(p):
    return os.path.relpath(p, ROOT).replace(os.sep, "/")


def test_the_engine_names_no_brand_customer_or_person():
    """Comments count. A name in a docstring is a name in the engine."""
    found = []
    for path in _engine_sources():
        low = open(path, encoding="utf-8").read().lower()
        for word, what in FORBIDDEN.items():
            if word in low:
                found.append("%s contains %r (%s)" % (_rel(path), word, what))
    assert not found, (
        "The AI Workforce engine is supposed to be configurable per brand and "
        "per customer, which is only true while it names none of them:\n  "
        + "\n  ".join(sorted(found)))


def test_the_engine_introduces_no_second_root_role():
    """God Mode is root authority. A rival root arrives as a word first."""
    found = []
    for path in _engine_sources():
        low = open(path, encoding="utf-8").read().lower()
        for phrase in FORBIDDEN_ROLES:
            # Allow a comment that says such a role must NOT exist.
            for line in low.splitlines():
                if phrase in line and not re.search(
                        r"\b(no|not|never|without|forbidden|do not|refuse)\b",
                        line):
                    found.append("%s: %r" % (_rel(path), line.strip()[:90]))
    assert not found, (
        "These lines introduce a root control plane beside God Mode:\n  "
        + "\n  ".join(sorted(set(found))))


def test_the_engine_may_still_call_itself_advisorflow():
    """The guard above must not have banned the platform's own name.

    A blocklist that forbids everything is not a policy, it is a way of making
    the next person delete the test. §53 is specific: the ENGINE is
    AdvisorFlow; the NAMES customers read are white-label.
    """
    assert "advisorflow" not in {k.lower() for k in FORBIDDEN}


def test_no_engine_file_hard_codes_an_email_address():
    """Except the two reserved domains, which cannot resolve by design."""
    pattern = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
    allowed = ("example.invalid", "example.com", "example.org", "@%s", "@{",
               "advisorflow.com")
    found = []
    for path in _engine_sources():
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            for m in pattern.finditer(line):
                addr = m.group(0)
                if any(a in addr for a in allowed):
                    continue
                found.append("%s:%d %s" % (_rel(path), i, addr))
    assert not found, (
        "A real-looking address in engine source is either somebody's inbox "
        "or a default nobody can change without a deploy:\n  "
        + "\n  ".join(found))
