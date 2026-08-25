"""
Guard rail for the local admin helper scripts in this folder.

These scripts create or reset a god_admin account. They read DATABASE_URL like
every other part of the app, which means that with a production DATABASE_URL in
the environment they will happily rewrite the live god_admin password. That is
exactly what we do not want, so every such script must call require_local_db()
before touching anything.

Set ALLOW_REMOTE_DB=1 to deliberately override, e.g. from the Render shell.
"""
import os
import sys


def require_local_db():
    url = os.environ.get("DATABASE_URL", "")
    if os.environ.get("ALLOW_REMOTE_DB") == "1":
        print("WARNING: ALLOW_REMOTE_DB=1 - running against %s" % _redact(url))
        return
    local = (
        not url
        or url.startswith("sqlite")
        or "localhost" in url
        or "127.0.0.1" in url
    )
    if not local:
        sys.exit(
            "REFUSING TO RUN: DATABASE_URL points at a non-local database (%s).\n"
            "This script rewrites god_admin credentials. If you really mean to do\n"
            "this, re-run with ALLOW_REMOTE_DB=1." % _redact(url)
        )


def require_init_password():
    pw = os.environ.get("GOD_ADMIN_INIT_PW", "")
    if not pw:
        sys.exit(
            "REFUSING TO RUN: GOD_ADMIN_INIT_PW is not set.\n"
            "There is no default password. Set it for this shell only, e.g.\n"
            '  PowerShell:  $env:GOD_ADMIN_INIT_PW = "<a strong password>"'
        )
    if len(pw) < 12:
        sys.exit("REFUSING TO RUN: GOD_ADMIN_INIT_PW must be at least 12 characters.")
    return pw


def _redact(url):
    if "@" in url:
        return url.split("@")[0].split("//")[0] + "//***@" + url.split("@", 1)[1]
    return url or "(unset)"
