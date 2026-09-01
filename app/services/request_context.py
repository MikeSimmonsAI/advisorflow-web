"""THE CURRENT REQUEST, available to code that was not handed one.

WHY THIS EXISTS RATHER THAN A PARAMETER

P0 routed every lead-touching query through one function, `authorized_lead_query`,
and that was the whole point: one place to change, and forty-five call sites that
inherit the change for free. The context switcher then needed one more input -
WHICH WORKSPACE this request selected - and that input arrives in a header.

Threading `request` through forty-five call sites would have undone the property
P0 was bought for. Every one of them would need a signature change, every future
call site would need to remember, and the one that forgot would silently resolve
to the caller's legacy column instead of the workspace they are standing in -
failing quietly, which is the failure mode this codebase keeps paying for.

So the request is put here once, by middleware, and `lead_scope` reads it when
its caller did not pass one. An explicit `request=` argument always wins; this is
the fallback, not the interface.

WHAT THIS IS NOT

It is not authorization and it carries none. The header it makes reachable is a
REQUEST for a workspace, and `workspace_access.selected_workspace_id` throws it
away unless an active membership backs it. Nothing here grants anything, and a
request that never made it into the var simply falls back to the behaviour that
existed before the switcher.

CONCURRENCY

`ContextVar` is per-task, not per-process: FastAPI runs each request in its own
asyncio task, and a sync endpoint runs in a worker thread that inherits a COPY of
the context. Two simultaneous requests cannot see each other's value. The token
is reset in a finally block so a task never leaves a stale request behind for the
next one to pick up.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from starlette.requests import Request

_current_request: ContextVar[Optional[Request]] = ContextVar(
    "advisorflow_current_request", default=None)


def set_current_request(request: Optional[Request]):
    """Store the request for this task. Returns the token to reset with."""
    return _current_request.set(request)


def reset_current_request(token) -> None:
    try:
        _current_request.reset(token)
    except Exception:
        # A token from another context is not worth failing a response over.
        pass


def get_current_request() -> Optional[Request]:
    """The request being served, or None outside a request (jobs, scripts, tests)."""
    try:
        return _current_request.get()
    except Exception:
        return None
