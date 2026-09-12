"""Wait for a local service to come up - or assert that nothing is there yet.

    m2_wait_for.py <base> [seconds]      wait until it answers
    m2_wait_for.py <base> --expect-down  fail if anything already answers

The second form exists because this machine runs many worktrees at once. A
port that is already occupied by somebody else's server does not announce
itself: every request simply goes to the wrong application and comes back
looking like a bug in this one.
"""
import sys
import time
import urllib.error
import urllib.request

args = sys.argv[1:]
base = (args[0] if args else "http://127.0.0.1:8137").rstrip("/")
expect_down = "--expect-down" in args
rest = [a for a in args[1:] if not a.startswith("--")]
timeout = float(rest[0]) if rest else 120.0


def answered():
    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as r:
            return r.status in (200, 204)
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


if expect_down:
    if answered():
        print("REFUSING TO START: something is already answering on %s.\n"
              "    That is another process, and every request in this run "
              "would go to it.\n"
              "    Pick a different port or stop that service first." % base)
        sys.exit(1)
    print("free: %s" % base)
    sys.exit(0)

deadline = time.time() + timeout
while time.time() < deadline:
    if answered():
        print("up: %s" % base)
        sys.exit(0)
    time.sleep(1.5)

print("TIMED OUT waiting for %s" % base)
sys.exit(1)
