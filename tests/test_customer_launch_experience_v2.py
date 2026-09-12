"""THE V2 CUSTOMER LAUNCH EXPERIENCE — the two rules that can silently break.

The V2 work is a VISUAL redesign, and almost none of it is testable by
assertion: whether a hero feels premium is a judgement Mike makes by looking at
it. Two things in it are not judgements, and both fail silently if they regress,
which is exactly why they are here rather than in a reviewer's head.

RULE ONE — NO HARD-CODED RESPONSE-TIME PROMISE
----------------------------------------------
"Replies the same business day" is a service level. Typed into a component it
becomes a commitment every brand makes to every customer, sold by nobody,
enforceable by no one, and wrong the first day somebody is on leave. So the
promise lives in the resolved launch-experience configuration, defaults to
None, and the shell reaches it through exactly one helper.

The test that matters is the NEGATIVE one: no file on the customer-facing
launch surface may contain a timeframe. That is a grep, and a grep is the only
thing that keeps holding after somebody adds a tenth component.

RULE TWO — PREVIEW CHROME CANNOT REACH A CUSTOMER
--------------------------------------------------
The internal preview is a staff route carrying an organization id. The
customer's own route has no id. The ribbon renders on the first and not the
second, and the difference is structural — a route, not a flag, not a setting,
not a query string — because a "preview mode" that is a setting is a setting
somebody eventually turns on for a real customer mid-onboarding.

These assert the structure, not the styling: that PreviewBanner is imported and
rendered in ONE place, guarded by the preview flag, that it refuses to render
without a preview context, and that no other component on the surface can
summon it.

Nothing here overrides an auth dependency or stubs a resolver. The
configuration tests run the real resolve/compose path.
"""
import itertools
import pathlib
import re

import pytest

from app.models.implementation_models import Implementation
from app.models.launch_experience_models import (
    LaunchExperienceConfig, SCOPE_BRAND, SCOPE_ORGANIZATION,
)
from app.models.models import Organization, Platform
from app.services import launch_experience

_SEQ = itertools.count(1)

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCH_DIR = ROOT / "frontend" / "src" / "pages" / "launch"


def _launch_sources():
    """Every customer-facing launch source file, steps included."""
    return sorted(p for p in LAUNCH_DIR.rglob("*.js*") if p.is_file())


def _read(path):
    return path.read_text(encoding="utf-8")


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def db(db_session):
    """The suite's session under the name this module's helpers read."""
    return db_session


def _platform(db, name):
    p = Platform(name=name, slug="v2p-%d" % next(_SEQ), short_name=name[:2],
                 tagline="t", support_email="s@example.test")
    db.add(p)
    db.commit()
    return p


def _org(db, plat, name, industry=None):
    kwargs = {} if industry is None else {"industry": industry}
    o = Organization(name=name, slug="v2o-%d" % next(_SEQ),
                     platform_id=plat.id, plan="standard", is_active=True,
                     **kwargs)
    db.add(o)
    db.commit()
    return o


def _impl(db, org, plat, status="not_started"):
    im = Implementation(organization_id=org.id, platform_id=plat.id,
                        opportunity_id="v2opp-%d" % next(_SEQ), status=status)
    db.add(im)
    db.commit()
    return im


# ════════════════════════════════════════════════════════════════════════════
# RULE ONE — the support promise is configuration, and defaults to none
# ════════════════════════════════════════════════════════════════════════════

def test_the_platform_default_promises_no_response_time():
    """A fresh install commits the brand to helping, and to no clock."""
    support = launch_experience.DEFAULT_PRESENTATION["support"]
    assert support["response_promise"] is None, (
        "The platform default must not ship a response-time promise. "
        "Nobody sold it and nobody is accountable for it."
    )
    assert "{brand}" in support["body"], (
        "The neutral line names the brand, not the platform."
    )


def test_the_neutral_line_states_no_timeframe():
    body = launch_experience.DEFAULT_PRESENTATION["support"]["body"].lower()
    for timeframe in ("hour", "day", "minute", "business day", "24", "48"):
        assert timeframe not in body, (
            "The neutral support line must not imply a timeframe: %r" % body
        )


def test_a_brand_can_configure_a_promise_and_a_customer_can_override_it(db):
    """The layered configuration is what carries an entitlement."""
    plat = _platform(db, "Provider V2")
    org = _org(db, plat, "Customer V2")

    db.add(LaunchExperienceConfig(
        scope_type=SCOPE_BRAND, scope_id=plat.id, name="brand support",
        is_active=True,
        presentation={"support": {"response_promise": "We reply within one "
                                                      "business day."}}))
    db.commit()

    resolved = launch_experience.resolve(
        db, platform_id=plat.id, organization_id=org.id)
    assert (resolved["presentation"]["support"]["response_promise"]
            == "We reply within one business day.")
    # The rest of the block survives the merge rather than being replaced.
    assert resolved["presentation"]["support"]["title"]

    db.add(LaunchExperienceConfig(
        scope_type=SCOPE_ORGANIZATION, scope_id=org.id, name="customer support",
        is_active=True,
        presentation={"support": {"response_promise": "Named engineer, "
                                                      "same hour."}}))
    db.commit()

    resolved = launch_experience.resolve(
        db, platform_id=plat.id, organization_id=org.id)
    assert (resolved["presentation"]["support"]["response_promise"]
            == "Named engineer, same hour.")


def test_the_composed_experience_carries_the_support_block(db):
    plat = _platform(db, "Compose V2")
    org = _org(db, plat, "Compose Customer")
    impl = _impl(db, org, plat)

    payload = launch_experience.compose(db, impl, org)
    support = payload["presentation"]["support"]
    assert support["response_promise"] is None
    # {brand} is substituted at compose time, so the customer reads a name.
    assert "Compose V2" in support["body"]
    assert "{brand}" not in support["body"]


# ════════════════════════════════════════════════════════════════════════════
# the journey owner — configured, never guessed
# ════════════════════════════════════════════════════════════════════════════

def test_every_default_stage_names_who_it_waits_on():
    owners = [s.get("owner") for s in launch_experience.DEFAULT_JOURNEY]
    assert all(o in ("customer", "provider", "both") for o in owners), owners
    assert len(launch_experience.DEFAULT_JOURNEY) == 7, (
        "The seven-stage journey is preserved by V2, not redesigned."
    )


def test_a_journey_that_does_not_say_who_owns_a_stage_asserts_nothing(db):
    """A configured journey with no owner must not have one invented."""
    plat = _platform(db, "Owner V2")
    org = _org(db, plat, "Owner Customer")
    impl = _impl(db, org, plat)

    db.add(LaunchExperienceConfig(
        scope_type=SCOPE_BRAND, scope_id=plat.id, name="short journey",
        is_active=True,
        journey=[{"key": "intake", "label": "Intake"},
                 {"key": "golive", "label": "Go Live", "owner": "nonsense"}]))
    db.commit()

    payload = launch_experience.compose(db, impl, org)
    assert [s["owner"] for s in payload["journey"]] == [None, None], (
        "An unconfigured or invalid owner renders as no owner at all — "
        "telling a customer their launch waits on them when it does not is "
        "worse than telling them nothing."
    )


# ════════════════════════════════════════════════════════════════════════════
# RULE ONE, enforced against the source: no timeframe anywhere on the surface
# ════════════════════════════════════════════════════════════════════════════

# Phrases that would read to a customer as a commitment about response time.
PROMISE_PATTERNS = [
    r"same\s+business\s+day",
    r"same[- ]day",
    r"within\s+\d+\s*(?:business\s+)?(?:hour|day|minute)",
    r"\b\d+\s*(?:hour|hr)s?\s+(?:response|reply|turnaround)",
    r"respond(?:s)?\s+within",
    r"repl(?:y|ies)\s+within",
    r"24[/-]7",
]


# AN ANSWER IS NOT A PROMISE. The intake asks customers how fast THEY follow
# up on their own enquiries, and one of the options is "Same business day".
# That is the customer describing their business, in a select the customer
# fills in — the opposite direction of travel from a commitment the brand makes
# to them. Option rows are skipped by shape (a `value:` and a `label:` on one
# line), which is narrow enough that prose anywhere on the surface, including
# inside a step component, is still caught.
_OPTION_ROW = re.compile(r"\bvalue:\s*'[^']*',\s*label:", re.IGNORECASE)


@pytest.mark.parametrize("pattern", PROMISE_PATTERNS)
def test_no_launch_source_hard_codes_a_response_time(pattern):
    rx = re.compile(pattern, re.IGNORECASE)
    offenders = []
    for path in _launch_sources():
        for n, line in enumerate(_read(path).splitlines(), 1):
            if _OPTION_ROW.search(line):
                continue
            if rx.search(line):
                offenders.append("%s:%d: %s" % (path.name, n, line.strip()))
    assert not offenders, (
        "A response-time promise is an entitlement, not copy. It belongs in "
        "presentation.support.response_promise, reached through supportLine() "
        "in present.js.\n" + "\n".join(offenders)
    )


def test_the_three_support_touchpoints_all_read_the_configuration():
    """Rail, progress rail and footer — one helper, no local copy."""
    for name in ("LaunchSidebar.jsx", "OnboardingProgressPanel.jsx",
                 "LaunchFooter.jsx"):
        src = _read(LAUNCH_DIR / name)
        assert "supportLine" in src, (
            "%s must take its support copy from supportLine() so a configured "
            "promise reaches every touchpoint at once." % name
        )


def test_support_appears_in_three_places_and_not_more():
    """§11 of the direction: findable, not duplicated into giant help cards."""
    users = [p.name for p in _launch_sources() if "supportLine(" in _read(p)]
    # present.js defines it; the three touchpoints consume it.
    assert sorted(users) == ["LaunchFooter.jsx", "LaunchSidebar.jsx",
                             "OnboardingProgressPanel.jsx", "present.js"], users


# ════════════════════════════════════════════════════════════════════════════
# RULE TWO — preview chrome is structurally unreachable from a customer page
# ════════════════════════════════════════════════════════════════════════════

def test_the_ribbon_is_rendered_in_one_place_and_only_under_preview():
    pad = _read(LAUNCH_DIR / "LaunchPad.jsx")
    renders = re.findall(r"<PreviewBanner\b", pad)
    assert len(renders) == 1, (
        "The preview ribbon must have exactly one render site: %d found."
        % len(renders)
    )
    assert re.search(r"\{\s*preview\s*\?\s*<PreviewBanner", pad), (
        "The ribbon must be guarded by the preview flag directly — not by a "
        "variable that something else could set true on a customer's page."
    )
    # The flag itself comes from the route parameter and from nothing else.
    assert re.search(r"const\s+preview\s*=\s*!!\s*organizationId", pad), (
        "`preview` must be derived from the route's organization id. A flag "
        "from state, storage or a query string is a flag that can be set on "
        "a real customer's session."
    )


def test_nothing_else_on_the_surface_can_render_the_ribbon():
    """Importing or rendering it — a mention in a comment is not either."""
    use = re.compile(r"(?:from\s+'\./PreviewBanner'|<PreviewBanner\b)")
    importers = [p.name for p in _launch_sources()
                 if use.search(_read(p)) and p.name != "PreviewBanner.jsx"]
    assert importers == ["LaunchPad.jsx"], importers


def test_the_ribbon_refuses_to_render_without_a_preview_context():
    src = _read(LAUNCH_DIR / "PreviewBanner.jsx")
    assert re.search(r"if\s*\(!context\)\s*return\s+null", src), (
        "Without a preview context there is no preview, and the component "
        "must render nothing rather than an unexplained strip."
    )


def test_the_ribbon_states_read_only_and_that_nothing_was_sent():
    src = _read(LAUNCH_DIR / "PreviewBanner.jsx")
    assert "Preview — Read Only" in src
    assert "saved or sent" in src


def test_the_customer_route_carries_no_organization_id():
    """The structural half of rule two, asserted where it is decided."""
    app_src = _read(ROOT / "frontend" / "src" / "App.jsx")
    assert 'path="/launch/preview/:organizationId"' in app_src
    assert 'path="/launch/:orgId"' not in app_src
    assert 'path="/launch/:organizationId"' not in app_src


# ════════════════════════════════════════════════════════════════════════════
# white-label: no customer, no brand, no platform name on the surface
# ════════════════════════════════════════════════════════════════════════════

def test_the_launch_surface_never_names_the_platform():
    """§13: the platform is infrastructure, not the customer's relationship."""
    offenders = []
    for path in _launch_sources():
        for n, line in enumerate(_read(path).splitlines(), 1):
            if re.search(r"advisorflow", line, re.IGNORECASE):
                offenders.append("%s:%d" % (path.name, n))
    assert not offenders, (
        "The customer-facing launch surface must not expose the platform "
        "name: " + ", ".join(offenders)
    )


def test_the_launch_surface_names_no_customer_and_no_brand():
    banned = ("atlantis", "comparepower", "evosys", "bookaboost")
    offenders = []
    for path in _launch_sources():
        for n, line in enumerate(_read(path).splitlines(), 1):
            low = line.lower()
            for word in banned:
                if word in low:
                    offenders.append("%s:%d (%s)" % (path.name, n, word))
    assert not offenders, (
        "V2 is reusable or it is not white-label: " + ", ".join(offenders)
    )


# ════════════════════════════════════════════════════════════════════════════
# the nine deliverables survive the V2 grouping
# ════════════════════════════════════════════════════════════════════════════

def test_the_plan_groups_cover_all_nine_deliverables_exactly_once():
    src = _read(LAUNCH_DIR / "launchConfig.js")
    deliverables = re.findall(r"\{\s*t:\s*'", src)
    assert len(deliverables) == 9, (
        "The nine deliverables are preserved by V2: %d found."
        % len(deliverables)
    )
    groups = re.findall(r"items:\s*\[([^\]]*)\]", src)
    assert len(groups) == 3, "Three parts: %d found." % len(groups)
    covered = []
    for group in groups:
        covered.extend(int(x) for x in re.findall(r"\d+", group))
    assert sorted(covered) == list(range(9)), (
        "Every deliverable belongs to exactly one part, and none is dropped "
        "by the regrouping: %r" % sorted(covered)
    )


# ════════════════════════════════════════════════════════════════════════════
# the V2 stylesheet: responsive rules last, and no fixed content widths
# ════════════════════════════════════════════════════════════════════════════

def test_the_responsive_rules_come_last_in_the_v2_layer():
    """An override after a media query wins at every width.

    This is the bug a retune layer reintroduces if it is appended carelessly:
    the desktop rule is written after the narrow-screen rule, so the narrow
    screen never gets it back. The V2 block therefore ends with its own media
    queries, and this asserts the ordering rather than trusting it.
    """
    src = _read(LAUNCH_DIR / "LaunchStyles.jsx")
    v2_at = src.index("V2 — THE APPROVED VISUAL DIRECTION")
    responsive_at = src.index("RESPONSIVE — LAST, SO NOTHING ABOVE OUTRANKS IT")
    assert v2_at < responsive_at

    tail = src[responsive_at:]
    # Nothing but media queries, their contents and the closing of the literal
    # may follow. A bare selector at column zero after this point is a rule
    # that outranks every breakpoint.
    stray = [line for line in tail.splitlines()
             if line.startswith(".lp-") or line.startswith("[data-surface")]
    assert not stray, (
        "These rules sit after the breakpoints and would win at every width: "
        + "; ".join(stray)
    )


def test_the_shell_has_no_fixed_content_width():
    """§14: desktop first, and never by way of a width that cannot shrink."""
    src = _read(LAUNCH_DIR / "LaunchStyles.jsx")
    v2 = src[src.index("V2 — THE APPROVED VISUAL DIRECTION"):]
    # Every grid track that holds content must be able to shrink. A bare
    # `1fr` refuses to go below its content and is what produced the
    # horizontal scrollbar the first time.
    for rule in re.findall(r"grid-template-columns:[^;}]+", v2):
        if "repeat(" in rule or "minmax(0" in rule:
            continue
        assert "1fr" not in rule.replace("minmax(0,1fr)", ""), rule


# ════════════════════════════════════════════════════════════════════════════
# the hero leads with the customer, not with the provider's ecosystem
# ════════════════════════════════════════════════════════════════════════════

def test_the_hero_eyebrow_names_the_work_not_the_providers_ecosystem():
    """§1: the provider is present, and does not overpower.

    This is the line that regressed the whole page. "Welcome to the {brand}
    Ecosystem" sat in the largest small-caps type on the customer's own portal
    and made it read as the provider's software with a customer dropped into
    it. The eyebrow names the WORK; the title is the customer; the brand is
    credited after the subtitle.
    """
    p = launch_experience.DEFAULT_PRESENTATION
    assert "{brand}" not in p["eyebrow"], (
        "The first line of the customer's hero must not be about the provider."
    )
    assert "ecosystem" not in p["eyebrow"].lower()
    assert "welcome" not in p["eyebrow"].lower()
    assert p["title"] == "{customer}", (
        "The largest line on the page is the customer's own name."
    )


def test_the_hero_intro_says_what_the_answers_are_for(db):
    """The paragraph before the first question earns the twenty fields."""
    plat = _platform(db, "Intro V2")
    org = _org(db, plat, "Intro Customer")
    impl = _impl(db, org, plat)

    intro = launch_experience.compose(db, impl, org)["presentation"]["intro"]
    assert "Intro V2" in intro, "The provider is named as the one building it."
    assert "{brand}" not in intro
    # It has to say what happens to the answers, not merely that a form exists.
    assert "build" in intro.lower()
    assert "submit" in intro.lower(), (
        "A customer part-way through a long form needs to know nothing is "
        "final until they say so."
    )
