"""Which environment this process is, and what that permits.

ONE PLACE ANSWERS "AM I THE DEMO?". Every guard in the codebase asks here
rather than reading the env var itself, so there is exactly one definition of
demo and no chance of two modules disagreeing about it mid-request.

THREE ENVIRONMENTS
------------------
    production   real customers, real providers, real side effects.
    staging      real behaviour, test provider accounts.
    demo         isolated database, every outbound call blocked, resettable.

Set by `APP_ENV`. Absent means PRODUCTION — the safe default, because a
misconfigured process must never silently gain demo powers over real data. The
cost of that default is that a demo box with a missing variable simply refuses
to be a demo; the cost of the opposite default is a reset button next to a real
customer's pipeline.

THE BOOT CHECK IS THE POINT
---------------------------
`assert_safe()` runs at startup and REFUSES TO BOOT rather than warn:

  * `APP_ENV=demo` pointed at a database that looks like production
  * `APP_ENV=demo` where the egress firewall did not install

A demo that quietly came up attached to the production database, or with its
firewall silently missing, is far more dangerous than one that will not start.
There is no override flag here on purpose. If you need one, you are configuring
something wrong.
"""

import logging
import os
import re
from typing import Optional

log = logging.getLogger(__name__)

ENV_PRODUCTION = "production"
ENV_STAGING = "staging"
ENV_DEMO = "demo"
VALID_ENVS = (ENV_PRODUCTION, ENV_STAGING, ENV_DEMO)


class UnsafeEnvironment(RuntimeError):
    """Refusing to start. Never caught anywhere — that is deliberate."""


def current() -> str:
    raw = (os.environ.get("APP_ENV") or "").strip().lower()
    if raw in VALID_ENVS:
        return raw
    if raw:
        # A typo like APP_ENV=Demo1 must not fall through to demo powers, and
        # must not be silent either.
        log.error("APP_ENV=%r is not one of %s - treating this process as "
                  "PRODUCTION.", raw, ", ".join(VALID_ENVS))
    return ENV_PRODUCTION


def is_demo() -> bool:
    return current() == ENV_DEMO


def is_production() -> bool:
    return current() == ENV_PRODUCTION


# ── database identity ───────────────────────────────────────────────────────
#
# Hosts that must never be reachable from a demo process. Matched against the
# host portion of DATABASE_URL. Append here, never remove.
PRODUCTION_DB_MARKERS = (
    "advisorflow-db",
    "advisorflow_db",
    "advisorflow-backend",
)


def database_host(url: Optional[str] = None) -> str:
    """The host from a SQLAlchemy URL, with no credentials in it.

    Parsed with a regex rather than urlparse because a libpq URL can carry a
    password containing characters urlparse mis-splits, and the only thing this
    function may ever return is the host.
    """
    raw = url if url is not None else (os.environ.get("DATABASE_URL") or "")
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("sqlite"):
        return "sqlite"
    m = re.match(r"^[a-z+]+://(?:[^@/]*@)?([^/:?]+)", raw, re.I)
    return (m.group(1) if m else "").lower()


def looks_like_production_db(url: Optional[str] = None) -> bool:
    host = database_host(url)
    if not host or host == "sqlite":
        return False
    return any(marker in host for marker in PRODUCTION_DB_MARKERS)


# Environment variables that redirect outbound traffic through a local proxy.
# These are the demo firewall's blind spot, discovered in Checkpoint 6: the
# firewall matches on the SOCKET's destination, and a proxied client's socket
# destination is the proxy itself. When the proxy listens on loopback - which
# every sidecar and every corporate agent does - the destination is `localhost`,
# which the firewall must allow so the app can reach its own database and health
# checks. The connection is therefore permitted, and the proxy then forwards it
# to Twilio.
#
# There is no way to distinguish "loopback because it is the database" from
# "loopback because it is a proxy to the whole internet" at the socket layer.
# So the demo refuses to boot when a proxy is configured, exactly as it refuses
# to boot when the firewall failed to install. Render sets none of these, so
# this changes nothing about how the demo actually runs.
PROXY_ENV_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY", "GRPC_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "grpc_proxy",
)


def configured_proxies() -> list:
    """Names of proxy variables that are set to a non-empty value."""
    return [n for n in PROXY_ENV_VARS if (os.environ.get(n) or "").strip()]


def assert_safe(firewall_installed: bool = False) -> None:
    """Called once at startup. Raises rather than logs."""
    env = current()
    if env != ENV_DEMO:
        return

    if looks_like_production_db():
        raise UnsafeEnvironment(
            "APP_ENV=demo but DATABASE_URL points at %r, which looks like the "
            "production database. Refusing to start. A demo environment needs "
            "its own database - see claude/EVOSYS_DEMO_MODE.md."
            % database_host())

    if not firewall_installed:
        raise UnsafeEnvironment(
            "APP_ENV=demo but the outbound firewall did not install. Refusing "
            "to start rather than run a demo that can reach real providers.")

    proxies = configured_proxies()
    if proxies:
        # Names only. A proxy URL routinely carries credentials, and a refusal
        # message that prints them writes them into the log it was trying to
        # protect.
        raise UnsafeEnvironment(
            "APP_ENV=demo but an outbound proxy is configured (%s). A proxy "
            "makes the socket-level firewall ineffective, because every "
            "outbound call reaches the proxy on loopback and the proxy - not "
            "this process - decides where it goes. Refusing to start. Unset "
            "these before running the demo." % ", ".join(proxies))

    log.warning("APP_ENV=demo - outbound network calls are BLOCKED and demo "
                "routes are ENABLED. Database host: %s", database_host())


def require_demo() -> None:
    """Guard for anything that may only ever run in the demo environment.

    Used by the seeders and the reset. Raising here is what stops
    `python scripts/demo_seed.py` from being pointed at production by someone
    with the wrong shell open.
    """
    if not is_demo():
        raise UnsafeEnvironment(
            "This operation is only permitted when APP_ENV=demo. This process "
            "is %r." % current())


def banner_payload() -> dict:
    """What the frontend shows. Safe to expose unauthenticated - it reveals
    only which environment answered, which the URL already tells you."""
    env = current()
    return {
        "environment": env,
        "demo_mode": env == ENV_DEMO,
        "banner": ("DEMO MODE - Simulated Environment. No real messages, "
                   "calls, calendar events, or charges will occur."
                   if env == ENV_DEMO else None),
    }
