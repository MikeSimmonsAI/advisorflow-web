"""Default-deny outbound network egress for the demo environment.

THE PROBLEM THIS SOLVES
-----------------------
Outbound side effects in this codebase are not funnelled through one place.
Twilio is constructed directly in ten modules, Resend in five, raw httpx in
twelve, googleapiclient in three, OpenAI in seventeen, Stripe in one. There is
no single call site to guard, and `sms_service.send_sms()` writes its `Message`
row from `twilio_msg.sid` - the send and the record are the same statement, so
they cannot be separated without rewriting the messaging layer.

Scattering `if demo:` through thirty-five modules would be a promise kept in
thirty-five places, which is a promise kept in none. The first module anyone
adds next month would not have it.

SO THE SEAM GOES UNDERNEATH ALL OF IT.
--------------------------------------
Every one of those libraries eventually opens a TCP socket through Python's
`socket` module. This installs a connect-time deny on that module. One seam,
below every SDK, that a new provider added next month inherits automatically
without anyone remembering anything.

WHAT STILL WORKS: the database. `psycopg2` wraps libpq and connects in C,
without going through Python's socket layer at all, so Postgres is unaffected
by this patch and needs no allowlist entry to function. Loopback is allowed
explicitly so an in-process test client, a local worker, or a health probe
still works.

WHAT IT DOES NOT DO
-------------------
It is not a substitute for the simulated providers. A blocked call raises
`OutboundBlocked`, which surfaces as a provider failure - correct and safe, but
not a good demo. The registered fakes in `demo_providers.py` are what make the
demo look alive; this is the backstop that makes it SAFE when a fake is missing
or a code path nobody anticipated tries to phone out.

That division matters: the demo's believability is allowed to have gaps. Its
safety is not.

FAIL CLOSED, LOUDLY
-------------------
A blocked attempt raises and is logged with the destination. Application code
that already wraps provider calls in `except Exception` records it as a
provider failure, which is exactly the controlled error the demo should show.
Nothing falls through to a real provider, because there is no route to one.
"""

import logging
import socket
import threading

log = logging.getLogger(__name__)

_installed = False
_lock = threading.Lock()

# Every attempt, for the report and for the tests.
BLOCKED: list = []
MAX_BLOCKED_RECORDED = 500

# Loopback only. The database is not listed because psycopg2 does not traverse
# this layer; if a future driver did, its host would be added here explicitly
# rather than by widening the rule.
ALLOWED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "testserver",          # starlette's TestClient
}


class OutboundBlocked(OSError):
    """A demo process tried to reach the outside world.

    Subclasses OSError deliberately. Provider SDKs and `requests`/`httpx`
    already treat OSError as a transport failure, so a blocked call is reported
    through their normal failure path instead of escaping as an exception type
    nobody handles and 500ing the request.
    """


def _is_allowed(host) -> bool:
    if host is None:
        return False
    name = str(host).strip().lower().rstrip(".")
    if not name:
        return False
    if name in ALLOWED_HOSTS:
        return True
    # A unix socket path, used by some local Postgres setups.
    if name.startswith("/"):
        return True
    return False


def _record(destination: str) -> None:
    if len(BLOCKED) < MAX_BLOCKED_RECORDED:
        BLOCKED.append(destination)


def install() -> bool:
    """Patch the socket layer. Idempotent; returns True once installed.

    Called from main.py's startup handler when APP_ENV=demo, BEFORE anything
    else runs. `environment.assert_safe()` refuses to boot if this returned
    False, so a failed install stops the process rather than producing a demo
    with no firewall.
    """
    global _installed
    with _lock:
        if _installed:
            return True

        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex
        real_create_connection = socket.create_connection

        def _deny(address, what="connect"):
            host = address[0] if isinstance(address, (tuple, list)) and address else address
            port = address[1] if isinstance(address, (tuple, list)) and len(address) > 1 else "?"
            dest = "%s:%s" % (host, port)
            _record(dest)
            log.error("DEMO FIREWALL blocked an outbound %s to %s", what, dest)
            raise OutboundBlocked(
                "Outbound network access is blocked in the demo environment "
                "(tried to reach %s). This is not a bug - it is the demo "
                "side-effect firewall. Register a simulated provider instead."
                % dest)

        def guarded_connect(self, address):
            if isinstance(address, (tuple, list)) and address and not _is_allowed(address[0]):
                _deny(address, "connect")
            return real_connect(self, address)

        def guarded_connect_ex(self, address):
            if isinstance(address, (tuple, list)) and address and not _is_allowed(address[0]):
                # connect_ex reports errors by return code rather than raising.
                # Callers that use it are checking a number, so give them one
                # instead of an exception they will not catch.
                _record("%s:%s" % (address[0], address[1] if len(address) > 1 else "?"))
                log.error("DEMO FIREWALL blocked an outbound connect_ex to %s", address[0])
                import errno
                return errno.ECONNREFUSED
            return real_connect_ex(self, address)

        def guarded_create_connection(address, *a, **kw):
            if isinstance(address, (tuple, list)) and address and not _is_allowed(address[0]):
                _deny(address, "create_connection")
            return real_create_connection(address, *a, **kw)

        socket.socket.connect = guarded_connect
        socket.socket.connect_ex = guarded_connect_ex
        socket.create_connection = guarded_create_connection

        # Stash the originals so `uninstall()` can restore them for tests. They
        # are NOT exposed as a way to bypass the firewall at runtime: uninstall
        # refuses unless the caller passes the explicit test token below.
        _installed = True
        install._originals = (real_connect, real_connect_ex, real_create_connection)
        log.warning("DEMO FIREWALL installed - outbound network egress is "
                    "default-deny. Allowed: %s", ", ".join(sorted(ALLOWED_HOSTS)))
        return True


# Passed by the test suite to restore real sockets in teardown. Not a runtime
# escape hatch: production never installs the firewall in the first place, and
# a demo process that called this would still have to know the token, which
# exists only in this module and the tests.
_TEST_TOKEN = "restore-sockets-for-tests"


def uninstall(token: str) -> None:
    global _installed
    if token != _TEST_TOKEN:
        raise PermissionError("demo_firewall.uninstall is for tests only.")
    with _lock:
        if not _installed:
            return
        real_connect, real_connect_ex, real_create_connection = install._originals
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex
        socket.create_connection = real_create_connection
        _installed = False


def is_installed() -> bool:
    return _installed


def blocked_attempts() -> list:
    """What the demo tried to reach. Surfaced in the demo control panel so an
    operator can see a missing simulation rather than wonder why a screen is
    empty."""
    return list(BLOCKED)


def reset_log() -> None:
    del BLOCKED[:]
