"""
═══════════════════════════════════════════════════════════════════════════
T6 UNDER T7 — THE BOUNDARY IS LOADED, NOT DECLARED
═══════════════════════════════════════════════════════════════════════════

T7 (`app/services/ai_operations/`) is the layer that turns "send this person a
message" into a provider call. It was written and shipped BEFORE T6 was on
main, so `contracts.py` resolves every workforce import through `_try()` and
falls back to a DECLARED context when T6 is absent — a context that can never
resolve a live adapter, which is what made shipping it early safe.

T6 is now present. That changes T7's behaviour: the fallback stops being the
path, and the real `policy`, `activation`, `eligibility` and `queue` answer
instead. That is the arrangement T7 was designed for, but "T7 is consuming T6"
is exactly the kind of claim that is asserted in a comment and quietly false
in the code — so T7 exposed `contracts.availability()` to make it checkable,
and this file checks it.

WHY IT MATTERS THAT THIS IS A TEST AND NOT A COMMENT

If `app/services/workforce/` were renamed, moved, or made to raise on import,
every `_try()` would return None and T7 would silently revert to the declared
fallback. Nothing would fail. T7's own tests would still pass, because they
pass with the fallback — that is what it is for. The product would go on
looking correct while the authority layer it claims to consult had stopped
being consulted at all.
"""

import pytest

from app.services.ai_operations import contracts


def test_every_t6_contract_t7_asks_for_is_actually_loaded():
    """Not one of them may be answered by the fallback."""
    missing = sorted(k for k, present in contracts.availability().items()
                     if not present)
    assert not missing, (
        "T7 could not import these T6 modules, so it is running on its "
        "declared fallback instead of the real authority layer: %s. Nothing "
        "fails when this happens, which is why it is asserted here."
        % ", ".join(missing))


def test_t6_is_reported_present():
    assert contracts.T6_PRESENT is True


def test_the_operation_tool_map_resolves_against_the_real_tool_registry():
    """T7 maps an operation to a T6 TOOL, and holding the tool is the
    authority. A map pointing at tool keys the registry does not define would
    refuse everything, or — worse, if it were ever made permissive — authorize
    against a name nothing checks."""
    from app.services.workforce import registry as wf_registry

    known = set(wf_registry.TOOLS)
    assert known, "the T6 tool registry is empty"

    operations = sorted(contracts.TOOL_FOR_OPERATION)
    assert operations, "T7's operation -> tool map is empty"

    checked = 0
    for operation in operations:
        for channel in (None, "sms", "email", "voice"):
            tool = contracts.tool_for(operation, channel=channel)
            if not tool:
                continue
            checked += 1
            assert tool in known, (
                "T7 maps operation %r on %s to tool %r, which the T6 registry "
                "does not define. The employee could never hold it, so the "
                "operation is unreachable."
                % (operation, channel or "any channel", tool))

    assert checked, "no operation resolved to a tool key at all"


def test_the_declared_fallback_still_cannot_reach_anybody():
    """T6 being present must not have retired the safety property.

    The fallback exists for the synthetic profiles and the evaluation
    harness, and its whole guarantee is that `source == "declared"` forces the
    simulated adapter. If T6's arrival changed that, every synthetic run would
    become capable of resolving a real provider.
    """
    ctx = contracts.declare_employee_context(
        organization_id="org-boundary-check",
        employee_id="emp-boundary-check")
    assert ctx.source == "declared"
    # A property, not a method — `if ctx.is_declared()` would raise rather
    # than answer, so the shape is asserted as well as the value.
    assert ctx.is_declared is True
