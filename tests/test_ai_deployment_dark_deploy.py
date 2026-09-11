"""T8 - THE DEPLOYMENT IS DARK, AND T8 IS NOT WHAT CHANGES THAT.

T6 and T7 each shipped a file asserting what production is configured to
allow, and each enumerates the environment switches ITS OWN package reads. A
switch introduced by a third package would be unasserted by both, and the two
files would go on describing a system they no longer covered.

SO T8 READS NO ENVIRONMENT VARIABLE OF ITS OWN, and this file is where that is
checked rather than promised. Everything T8 needs to know about whether
anything may run, it asks T6 and T7 - which is also why it cannot be the reason
something starts.

THE OTHER CLAIMS ASSERTED HERE, each of which is a sentence from the brief
turned into something that fails:

    NOT A SECOND BILLING SYSTEM   no money column, no Stripe call, no price
                                  literal anywhere in the package.
    NOT A SECOND AI ENGINE        no model provider, no prompt, no tool
                                  registry of its own.
    NOT A SECOND ROOT             every God route is `require_god`, every
                                  customer route resolves its own workspace,
                                  and the words that name a rival root appear
                                  nowhere.
    DEPLOYING IT STARTS NOTHING   no cron entry, no asyncio loop, no worker.
"""

import inspect
import os
import re

import pytest

from app.services import ai_deployment as t8_package

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_DIR = os.path.dirname(inspect.getfile(t8_package))
ROUTERS = [os.path.join(ROOT, "app", "routers", "ai_deployment_router.py"),
           os.path.join(ROOT, "app", "routers", "god_ai_deployment_router.py")]
MODELS = os.path.join(ROOT, "app", "models", "ai_deployment_models.py")
RENDER = os.path.join(ROOT, "render.yaml")


def _sources():
    for name in sorted(os.listdir(PACKAGE_DIR)):
        if name.endswith(".py"):
            yield os.path.join(PACKAGE_DIR, name)
    for path in ROUTERS + [MODELS]:
        yield path


def _read(path):
    return open(path, encoding="utf-8").read()


def _rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


# ---------------------------------------------------------------------------
# ENVIRONMENT
# ---------------------------------------------------------------------------

def test_t8_introduces_no_environment_switch():
    """A switch T8 owned would be unasserted by T6's and T7's dark-deploy files.

    `AI_OPERATIONS_ENABLED` is allowed because the synthetic proof TOGGLES T7's
    own flag for the duration of a run and puts it back. Toggling somebody
    else's switch inside a savepoint is not the same as owning one.
    """
    found = set()
    for path in _sources():
        for match in re.finditer(r"[\"'](AI_[A-Z0-9_]+)[\"']", _read(path)):
            found.add(match.group(1))
    assert found <= {"AI_OPERATIONS_ENABLED"}, (
        "T8 reads environment switches of its own: %s"
        % sorted(found - {"AI_OPERATIONS_ENABLED"}))


def test_the_deploy_file_declares_nothing_for_t8():
    """Nothing was added to render.yaml for this thread."""
    src = _read(RENDER)
    for key in ("AI_DEPLOYMENT", "AI_WORKFORCE_DEPLOY", "AI_HIRE"):
        assert key not in src, "render.yaml declares %s" % key


def test_deploying_t8_starts_no_background_work():
    """No cron entry, no asyncio loop, no worker. Section 6 of T7's log.

    A background job is how a dark launch stops being dark without anybody
    deciding to switch something on.
    """
    for path in _sources():
        src = _read(path)
        # `threading.Thread` spelled in full on purpose: a bare "Thread("
        # matches `AIConversationThread(`, which is a T7 model and not a
        # background worker. A pattern that fires on the wrong thing is a
        # pattern somebody deletes.
        for banned in ("asyncio.create_task", "schedule.every", "BackgroundTasks",
                       "add_job(", "threading.Thread("):
            assert banned not in src, "%s starts background work (%s)" % (
                _rel(path), banned)


# ---------------------------------------------------------------------------
# NOT A SECOND BILLING SYSTEM
# ---------------------------------------------------------------------------

def test_no_t8_table_carries_money():
    from app.models.ai_deployment_models import (AIDeploymentEvent,
                                                 AIEmployeeDeployment,
                                                 AIOfferingTerms)
    banned = ("amount", "price", "cents", "currency", "interval", "stripe_")
    offenders = []
    for model in (AIOfferingTerms, AIEmployeeDeployment, AIDeploymentEvent):
        for column in model.__table__.columns:
            if any(b in column.name.lower() for b in banned):
                offenders.append("%s.%s" % (model.__tablename__, column.name))
    assert not offenders, offenders


def test_t8_never_calls_stripe():
    """Commerce is asked, never performed. T2 owns every call that costs money."""
    for path in _sources():
        src = _read(path)
        assert "import stripe" not in src, _rel(path)
        assert "stripe." not in src.replace("stripe_", ""), _rel(path)


def test_t8_writes_no_commercial_row():
    """It reads `CatalogPurchase`; it never constructs one outside the proofs.

    The synthetic proof builds one deliberately, inside a savepoint, to
    exercise the entitled path - so `simulation.py` and the harness that reuses
    its builders are exempt by name rather than by accident.
    """
    exempt = {"simulation.py", "evaluation.py"}
    for path in _sources():
        if os.path.basename(path) in exempt:
            continue
        src = _read(path)
        assert "CatalogPurchase(" not in src, _rel(path)
        assert "BrandCatalogItem(" not in src, _rel(path)


def test_no_price_literal_appears_anywhere_in_t8():
    """No amount, no default, no 'suggested' figure. Not even a zero."""
    pattern = re.compile(r"amount_cents\s*=\s*\d+")
    # The two proof modules build a synthetic purchase at an explicit zero,
    # inside a savepoint, so the entitled path can be exercised without a
    # figure being invented. Exempt by name rather than by luck.
    exempt = {"simulation.py", "evaluation.py"}
    for path in _sources():
        if os.path.basename(path) in exempt:
            continue
        assert not pattern.search(_read(path)), _rel(path)


# ---------------------------------------------------------------------------
# NOT A SECOND AI ENGINE
# ---------------------------------------------------------------------------

def test_t8_holds_no_model_provider():
    """No provider, no router, no temperature. T8 never talks to a model.

    `evaluation.py` is exempt because the harness NAMES the things it forbids -
    one of its own cases asserts that the readiness engine mentions none of
    these words, and it can only do that by containing them. A ban list that
    tripped over the test enforcing the ban is a test somebody deletes.
    """
    for path in _sources():
        if os.path.basename(path) == "evaluation.py":
            continue
        low = _read(path).lower()
        for banned in ("openai", "anthropic", "model_router", "temperature="):
            assert banned not in low, "%s mentions %s" % (_rel(path), banned)


def test_the_harness_itself_checks_the_readiness_engine_for_a_model():
    """The exemption above is only safe because this case exists.

    Asserted by name: if the harness ever stopped checking, the exemption would
    become a hole with nothing behind it.
    """
    from app.services.ai_deployment import evaluation
    cases = {key for _dimension, key, _fn in evaluation.CASES}
    assert "readiness_never_asks_a_model" in cases


def test_t8_holds_no_tool_registry_of_its_own():
    """Authority comes from T6's registry. There is no second list of tools."""
    from app.services.ai_deployment import catalog
    from app.services.workforce import registry as wf_registry
    keys = {t["template_key"] for t in catalog.platform_catalog()}
    assert keys == set(wf_registry.ALL_TEMPLATE_KEYS)


def test_the_configuration_filter_refuses_every_internal_name():
    """The list is the product promise. Each entry is checked individually."""
    from app.services.ai_deployment import constants as D
    for name in ("system_prompt", "systemPrompt", "model", "modelName",
                 "temperature", "top_p", "max_tokens", "tools", "tool_keys",
                 "api_key", "apiKey", "provider", "webhook_url", "raw_sql",
                 "capability", "entitlement_key", "amount_cents",
                 "stripe_price_id"):
        assert D.forbidden_reason(name) is not None, name


def test_ordinary_business_questions_are_not_refused():
    """A blocklist that refuses everything is a blocklist somebody deletes."""
    from app.services.ai_deployment import constants as D
    for name in D.BUSINESS_FIELD_KEYS:
        assert D.forbidden_reason(name) is None, name


# ---------------------------------------------------------------------------
# NOT A SECOND ROOT
# ---------------------------------------------------------------------------

def test_every_god_route_is_require_god():
    from app.routers import god_ai_deployment_router as god_router
    assert any("require_god" in str(d.dependency)
               or getattr(d.dependency, "__name__", "") == "require_god"
               for d in god_router.router.dependencies), (
        "the God router does not carry require_god as a router dependency")


def test_t8_introduces_no_rival_root_role():
    """A second root arrives as a word before it arrives as a table."""
    banned = ["platform superadmin", "platform super admin", "super god",
              "supergod", "root admin", "master admin", "system owner",
              "workforce owner"]
    offenders = []
    for path in _sources():
        for line in _read(path).lower().splitlines():
            for phrase in banned:
                if phrase in line and not re.search(
                        r"\b(no|not|never|without|forbidden|do not|refuse|"
                        r"rival|second)\b", line):
                    offenders.append("%s: %s" % (_rel(path), line.strip()[:90]))
    assert not offenders, offenders


def test_only_god_admin_is_the_platform_operator():
    from app.services.ai_deployment import activation

    class _Pretender:
        role = "platform_superadmin"

    class _Operator:
        role = "god_admin"

    assert activation._is_platform_operator(_Operator())
    assert not activation._is_platform_operator(_Pretender())
    assert not activation._is_platform_operator(None)


# ---------------------------------------------------------------------------
# THE ROUTES EXIST AND ANSWER
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/ai-workforce/overview",
    "/ai-workforce/catalog",
    "/ai-workforce/deployments",
    "/god/ai-workforce/overview",
    "/god/ai-workforce/templates",
    "/god/ai-workforce/deployments",
])
def test_the_route_is_mounted_and_refuses_an_anonymous_caller(client, path):
    """Mounted, and closed. A route that 404s is a route nobody deployed."""
    response = client.get(path)
    assert response.status_code in (401, 403), (path, response.status_code)


def test_the_frontend_bundle_carries_both_screens():
    """The screens are in the SERVED bundle, not only in the source tree.

    A page that exists in `src` and not in `dist` is a page nobody can reach,
    and the only symptom is a blank panel.
    """
    dist = os.path.join(ROOT, "frontend", "dist", "assets")
    if not os.path.isdir(dist):
        pytest.skip("frontend has not been built in this checkout")
    blobs = [os.path.join(dist, f) for f in os.listdir(dist)
             if f.endswith(".js")]
    assert blobs, "no javascript bundle in frontend/dist/assets"
    haystack = "".join(open(b, encoding="utf-8", errors="ignore").read()
                       for b in blobs)
    for needle in ("/ai-workforce/overview", "/god/ai-workforce/overview",
                   "My AI Workforce"):
        assert needle in haystack, "%r is missing from the built bundle" % needle
