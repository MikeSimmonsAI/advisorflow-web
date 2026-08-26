"""The side-effect firewall, proven rather than promised.

THIS IS THE SUITE THAT MATTERS MOST IN CHECKPOINT 5.5. Every other demo
guarantee rests on the claim that a demo process cannot reach a real provider.
A claim like that is worth exactly what its tests are worth, so this file
attacks it from every direction it can:

  * the SDKs the codebase actually uses - twilio, resend, httpx, requests,
    googleapiclient, openai, stripe - each tried for real and each expected to
    fail rather than connect
  * a raw socket, in case a future library skips all of them
  * a provider the firewall has never heard of, to prove the rule is
    default-DENY and not a blocklist of known hosts
  * the environment guards, including that a demo pointed at a production-
    looking database refuses to boot

NOTHING HERE REACHES THE NETWORK. Every outbound attempt is expected to be
blocked; a test that accidentally succeeded in connecting would be reported as
a FAILURE, which is the only way this suite can be honest about its own claim.

    python scripts/smoke_demo_firewall.py
"""
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:300]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def blocked(fn, *a, **kw):
    """Run something that tries to reach the network.

    Returns (was_blocked, detail). A call is 'blocked' if it raised anything at
    all that traces back to the firewall - the SDKs wrap OSError in their own
    transport exceptions, so the exception TYPE is unreliable and the message
    chain is what settles it.
    """
    from app.services.demo_firewall import OutboundBlocked
    try:
        fn(*a, **kw)
    except BaseException as e:                                  # noqa: BLE001
        chain, cur, depth = [], e, 0
        while cur is not None and depth < 12:
            chain.append("%s: %s" % (type(cur).__name__, cur))
            if isinstance(cur, OutboundBlocked):
                return True, "OutboundBlocked"
            cur = cur.__cause__ or cur.__context__
            depth += 1
        joined = " | ".join(chain)
        if "demo environment" in joined or "OutboundBlocked" in joined:
            return True, joined[:200]
        return False, "raised, but NOT by the firewall: " + joined[:250]
    return False, "THE CALL SUCCEEDED - it reached the network"


# ── 1. the environment module ───────────────────────────────────────────────

def s1_environment():
    print("\n[1] Environment identity")
    from app.services import environment as env

    os.environ.pop("APP_ENV", None)
    check("APP_ENV unset means PRODUCTION, never demo",
          env.current() == "production" and not env.is_demo(), env.current())

    os.environ["APP_ENV"] = "Demo1"
    check("A TYPO DOES NOT GRANT DEMO POWERS",
          env.current() == "production", env.current())

    os.environ["APP_ENV"] = "DEMO"
    check("the value is case-insensitive", env.is_demo(), env.current())

    os.environ["APP_ENV"] = "demo"
    check("demo is demo", env.is_demo())

    # Host parsing must never return a credential.
    url = "postgresql://someuser:s3cr3t-p4ss@advisorflow-db.frankfurt.render.com:5432/af"
    host = env.database_host(url)
    check("the database host is extracted without the password",
          host == "advisorflow-db.frankfurt.render.com", host)
    check("NO CREDENTIAL SURVIVES THE PARSE",
          "s3cr3t" not in host and "someuser" not in host, host)

    check("a production-looking database is recognised",
          env.looks_like_production_db(url), url)
    check("a demo database is not",
          not env.looks_like_production_db(
              "postgresql://u:p@advisorflow-demo-db.render.com:5432/demo"))
    check("sqlite is not mistaken for production",
          not env.looks_like_production_db("sqlite:///./demo_local.db"))

    payload = env.banner_payload()
    check("the banner payload announces demo mode",
          payload["demo_mode"] is True and "DEMO MODE" in (payload["banner"] or ""),
          payload)
    os.environ["APP_ENV"] = "production"
    check("production shows no banner",
          env.banner_payload()["banner"] is None)
    os.environ["APP_ENV"] = "demo"


def s2_boot_refusal():
    print("\n[2] A misconfigured demo refuses to BOOT, not warn")
    from app.services import environment as env
    from app.services.environment import UnsafeEnvironment

    saved = os.environ.get("DATABASE_URL")

    os.environ["DATABASE_URL"] = "postgresql://u:p@advisorflow-db.render.com:5432/af"
    try:
        env.assert_safe(firewall_installed=True)
        check("A DEMO POINTED AT PRODUCTION REFUSES TO BOOT", False,
              "assert_safe returned instead of raising")
    except UnsafeEnvironment as e:
        check("A DEMO POINTED AT PRODUCTION REFUSES TO BOOT", True)
        check("and the refusal names no credential",
              "p@" not in str(e) and ":p" not in str(e), str(e)[:200])

    os.environ["DATABASE_URL"] = "postgresql://u:p@advisorflow-demo-db.render.com:5432/demo"
    try:
        env.assert_safe(firewall_installed=False)
        check("A DEMO WITH NO FIREWALL REFUSES TO BOOT", False,
              "assert_safe returned instead of raising")
    except UnsafeEnvironment:
        check("A DEMO WITH NO FIREWALL REFUSES TO BOOT", True)

    try:
        env.assert_safe(firewall_installed=True)
        check("a correctly configured demo boots", True)
    except UnsafeEnvironment as e:
        check("a correctly configured demo boots", False, e)

    os.environ["APP_ENV"] = "production"
    os.environ["DATABASE_URL"] = "postgresql://u:p@advisorflow-db.render.com:5432/af"
    try:
        env.assert_safe(firewall_installed=False)
        check("PRODUCTION IS UNAFFECTED - no firewall, no refusal", True)
    except UnsafeEnvironment as e:
        check("PRODUCTION IS UNAFFECTED - no firewall, no refusal", False, e)

    os.environ["APP_ENV"] = "demo"
    if saved is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = saved


def s3_require_demo():
    print("\n[3] Demo-only operations refuse to run elsewhere")
    from app.services import environment as env
    from app.services.environment import UnsafeEnvironment

    for bad in ("production", "staging"):
        os.environ["APP_ENV"] = bad
        try:
            env.require_demo()
            check("require_demo refuses under APP_ENV=%s" % bad, False, "did not raise")
        except UnsafeEnvironment:
            check("require_demo refuses under APP_ENV=%s" % bad, True)

    os.environ["APP_ENV"] = "demo"
    try:
        env.require_demo()
        check("require_demo permits the demo environment", True)
    except UnsafeEnvironment as e:
        check("require_demo permits the demo environment", False, e)


# ── 4. the firewall against every provider the codebase uses ────────────────

def s4_providers():
    print("\n[4] Every real provider fails closed")
    from app.services import demo_firewall as fw
    fw.reset_log()
    installed = fw.install()
    check("the firewall installs", installed and fw.is_installed())

    # A raw socket first: if this got out, nothing below would mean anything.
    def raw():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(("api.twilio.com", 443))

    ok, detail = blocked(raw)
    check("A RAW SOCKET CANNOT REACH THE INTERNET", ok, detail)

    def raw_create():
        socket.create_connection(("api.twilio.com", 443), timeout=5)

    ok, detail = blocked(raw_create)
    check("neither can socket.create_connection", ok, detail)

    # A host the firewall has never heard of, to prove default-DENY.
    def unknown_vendor():
        socket.create_connection(("some-vendor-nobody-listed.example.com", 443), timeout=5)

    ok, detail = blocked(unknown_vendor)
    check("A PROVIDER NOBODY LISTED IS ALSO BLOCKED - it is default-deny, "
          "not a blocklist", ok, detail)

    # httpx - used directly in twelve modules, and by the openai SDK.
    try:
        import httpx

        ok, detail = blocked(
            lambda: httpx.post("https://graph.microsoft.com/v1.0/me/events",
                               json={}, timeout=5))
        check("httpx to Microsoft Graph is blocked", ok, detail)

        ok, detail = blocked(
            lambda: httpx.get("https://api.openai.com/v1/models", timeout=5))
        check("httpx to OpenAI is blocked", ok, detail)
    except ImportError:
        check("httpx is installed to be tested", False, "httpx missing")

    # requests - twilio, resend and stripe all sit on it.
    try:
        import requests

        ok, detail = blocked(
            lambda: requests.post("https://api.twilio.com/2010-04-01/Accounts",
                                  timeout=5))
        check("requests to Twilio is blocked", ok, detail)
    except ImportError:
        print("       (requests not installed - skipped)")

    # The twilio SDK itself, constructed the way the app constructs it.
    try:
        from twilio.rest import Client

        def twilio_send():
            Client("ACfake00000000000000000000000000", "faketoken").messages.create(
                body="this must never leave the process",
                from_="+15555550100", to="+15555550101")

        ok, detail = blocked(twilio_send)
        check("THE TWILIO SDK CANNOT SEND AN SMS", ok, detail)
    except ImportError:
        print("       (twilio not installed - skipped)")

    # Resend, as email_service uses it.
    try:
        import resend

        def resend_send():
            resend.api_key = "re_fake_key_for_this_test"
            resend.Emails.send({"from": "demo@example.com", "to": ["nobody@example.com"],
                                "subject": "must not send", "html": "<p>no</p>"})

        ok, detail = blocked(resend_send)
        check("THE RESEND SDK CANNOT SEND AN EMAIL", ok, detail)
    except ImportError:
        print("       (resend not installed - skipped)")

    # googleapiclient, as the Google calendar provider uses it.
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        def gcal_write():
            creds = Credentials(token=None, refresh_token="fake",
                                token_uri="https://oauth2.googleapis.com/token",
                                client_id="fake", client_secret="fake")
            build("calendar", "v3", credentials=creds,
                  cache_discovery=False).events().insert(
                calendarId="primary", body={}).execute()

        ok, detail = blocked(gcal_write)
        check("GOOGLE CALENDAR CANNOT BE WRITTEN TO", ok, detail)
    except ImportError:
        print("       (google client libraries not installed - skipped)")

    # Stripe.
    try:
        import stripe as _stripe

        def charge():
            _stripe.api_key = "sk_test_fake"
            _stripe.Customer.list(limit=1)

        ok, detail = blocked(charge)
        check("STRIPE CANNOT BE CHARGED", ok, detail)
    except ImportError:
        print("       (stripe not installed - skipped)")

    # The Retell bridge is inbound-only, but the demo must not call OUT to it
    # either if anything ever does.
    ok, detail = blocked(
        lambda: socket.create_connection(("api.retellai.com", 443), timeout=5))
    check("RETELL CANNOT BE CALLED", ok, detail)

    attempts = fw.blocked_attempts()
    check("every blocked attempt is recorded for the operator",
          len(attempts) >= 6, len(attempts))
    check("the record names the destination",
          any("twilio" in a for a in attempts), attempts[:5])


def s5_loopback():
    print("\n[5] What must keep working, keeps working")
    from app.services import demo_firewall as fw
    check("the firewall is still installed", fw.is_installed())

    # A local listener - the in-process test client and any local worker.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        c = socket.create_connection(("127.0.0.1", port), timeout=5)
        c.close()
        check("LOOPBACK STILL CONNECTS - local calls are not collateral damage", True)
    except Exception as e:
        check("LOOPBACK STILL CONNECTS - local calls are not collateral damage",
              False, e)
    finally:
        srv.close()

    # The database. SQLite needs no socket; the point of the assertion is that
    # a session opens and queries under an installed firewall.
    from app.deps import SessionLocal
    from sqlalchemy import text
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        check("THE DATABASE STILL WORKS UNDER THE FIREWALL", True)
    except Exception as e:
        check("THE DATABASE STILL WORKS UNDER THE FIREWALL", False, e)


def s6_uninstall():
    print("\n[6] The firewall cannot be turned off from application code")
    from app.services import demo_firewall as fw

    try:
        fw.uninstall("please")
        check("UNINSTALL REFUSES AN ARBITRARY CALLER", False, "it uninstalled")
    except PermissionError:
        check("UNINSTALL REFUSES AN ARBITRARY CALLER", True)
    check("and the firewall is still up", fw.is_installed())

    fw.uninstall(fw._TEST_TOKEN)
    check("the test suite can restore real sockets for teardown",
          not fw.is_installed())


def s7_static():
    print("\n[7] Guarantees that must hold in the source")
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

    def read(*p):
        return open(os.path.join(root, *p), encoding="utf-8").read()

    main = read("app", "main.py")
    # Scoped to the startup handler. A module-level comment near the top of
    # main.py mentions create_all() by name, and matching that instead of the
    # call would have made this assertion pass or fail for the wrong reason.
    startup = main[main.index("async def on_startup"):]
    idx_env = startup.find("environment as _env")
    idx_create = startup.find("Base.metadata.create_all(")
    check("THE BOUNDARY IS CHECKED BEFORE THE DATABASE IS TOUCHED",
          0 <= idx_env < idx_create, (idx_env, idx_create))
    check("and before the migrations run",
          0 <= idx_env < startup.find("run_auto_migrations("),
          (idx_env, startup.find("run_auto_migrations(")))
    check("startup installs the firewall for demo", "demo_firewall" in main)
    check("startup calls assert_safe", "assert_safe(" in main)

    envsrc = read("app", "services", "environment.py")
    check("an unknown APP_ENV falls back to production, not demo",
          "return ENV_PRODUCTION" in envsrc)
    check("THERE IS NO OVERRIDE FLAG THAT SKIPS THE SAFETY CHECK",
          "FORCE" not in envsrc and "SKIP_SAFETY" not in envsrc
          and "ALLOW_UNSAFE" not in envsrc)

    fwsrc = read("app", "services", "demo_firewall.py")
    check("the firewall records what it blocked", "BLOCKED" in fwsrc)
    check("uninstall is token-guarded", "PermissionError" in fwsrc)


def main():
    os.environ["APP_ENV"] = "demo"
    os.environ.setdefault("DATABASE_URL", "sqlite:///./demo_firewall_test.db")
    os.environ.setdefault("JWT_SECRET", "smoke" + "0" * 59)
    os.environ.setdefault("SECRET_KEY", "smoke" + "0" * 59)

    try:
        s1_environment()
        s2_boot_refusal()
        s3_require_demo()
        s4_providers()
        s5_loopback()
        s6_uninstall()
        s7_static()
    except Exception:
        import traceback
        print(traceback.format_exc().encode("ascii", "replace").decode("ascii"))
        FAILURES.append("UNHANDLED EXCEPTION")
    finally:
        try:
            from app.services import demo_firewall as fw
            fw.uninstall(fw._TEST_TOKEN)
        except Exception:
            pass
        for leftover in ("demo_firewall_test.db",):
            try:
                os.remove(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "..", leftover))
            except OSError:
                pass

    print()
    if FAILURES:
        print("  %d FAILURE(S): %s" % (len(FAILURES), ", ".join(FAILURES[:8])))
        sys.exit(1)
    print("  ALL DEMO FIREWALL CHECKS PASSED")


if __name__ == "__main__":
    main()
