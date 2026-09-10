"""THE TWO REFUSALS. Small on purpose, and imported by the send paths.

This module exists so that `sms_service` and `email_service` can ask one
question — "is this a demonstration tenant?" — without importing the Demo Suite,
its models, its seeder or its content. A guard that drags a feature in behind it
is a guard people are tempted to remove.

    assert_demo_org / assert_demo_brand    a Demo Suite write may only land on
                                           a tenant flagged as a demo.

    block_if_demo                          an outbound provider call may never
                                           be made for a tenant flagged as a
                                           demo.

THEY POINT IN OPPOSITE DIRECTIONS, WHICH IS THE POINT. One refuses to write
demo data anywhere real; the other refuses to let anything real happen inside
the demo. A single check could not be both, and a demo protected by only one of
them fails in exactly the way that matters: either the seed lands in a
customer's workspace, or the customer's phone rings during a sales meeting.

WHY THIS IS NOT `demo_firewall`. `demo_firewall` blocks SOCKETS, and it is only
installed in an APP_ENV=demo process. Production must not have its sockets
blocked, so the Demo Suite cannot rely on it. This is the production-side
equivalent, enforced at the one place a provider is actually chosen rather than
at the network layer.

NULL READS AS FALSE. `is_demo` is NOT NULL in the schema, but a database that
has not yet run the migration, or an object constructed in a test without the
column, would answer None. None means "not a demo", which is the safe direction
for the first guard (refuse to seed) and — deliberately — also the safe
direction for the second (allow a real send for a real tenant). A demo tenant
always has the column, because the seeder writes it.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, status


class DemoBoundaryViolation(RuntimeError):
    """Raised when a send path is asked to act for a demonstration tenant.

    A RuntimeError rather than an HTTPException because the send paths are
    called from cron loops and background jobs as well as from routes, and a
    404 raised inside a cadence worker is not an answer to anybody. Route-level
    callers translate it; `send_sms` already raises `ValueError` for its other
    refusals, and this sits beside them.
    """


def is_demo_tenant(obj: Optional[Any]) -> bool:
    """True only for an object that positively says it is a demo tenant."""
    if obj is None:
        return False
    return bool(getattr(obj, "is_demo", False))


def assert_demo_org(org: Optional[Any]) -> Any:
    """A Demo Suite write may only touch a demo-flagged organization.

    404, not 403. A caller who has reached this point holds the demo
    entitlement; the answer to "may I write to Restland?" is that Restland is
    not a thing the Demo Suite can address at all, and a 403 would confirm the
    id names a real customer.
    """
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No demonstration environment for that brand.")
    if not is_demo_tenant(org):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No demonstration environment for that brand.")
    return org


def assert_demo_brand(brand_sales_org: Optional[Any]) -> Any:
    """The sales-side half of the same rule."""
    return assert_demo_org(brand_sales_org)


def block_if_demo(org: Optional[Any], channel: str) -> None:
    """Refuse an outbound provider call for a demonstration tenant.

    Called from the real send paths, before the provider client is
    constructed. The message names the channel because the person who sees it
    is a developer reading a log during a presentation, and "blocked" without
    "which one" costs them the next ten minutes.
    """
    if is_demo_tenant(org):
        raise DemoBoundaryViolation(
            "Refusing to send %s for a demonstration organization. The Demo "
            "Suite writes a simulated %s record instead of calling a provider "
            "— a demo must never make a real phone ring or land in a real "
            "inbox." % (channel, channel))
