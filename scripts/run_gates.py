"""Run every gate the deploy runs, without deploying.

deploy.ps1 runs the suite and then ships. During a build you need the first half
on its own, many times, and you need it to say which gate failed rather than
stopping at the first one - so this runs them all and reports at the end.

Order matches deploy.ps1. Add new gates in both places.
"""
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")

GATES = [
    "smoke_import.py", "smoke_requests.py",
    "smoke_sales_models.py", "smoke_tenancy.py", "smoke_sales_login.py",
    "smoke_sales_workspace.py", "smoke_scheduling.py", "smoke_calendar_sync.py",
    "smoke_sales_execution.py", "smoke_manager_workspace.py",
    "smoke_retell_bridge.py", "smoke_tenant_bridge.py",
    "smoke_integration_migration.py", "smoke_demo_firewall.py",
    "smoke_demo_mode.py", "smoke_demo_frontend.py",
    "smoke_checkpoint6.py", "smoke_checkpoint6_frontend.py",
    "smoke_legacy_hardening.py", "smoke_staff_activation.py",
    "smoke_sales_workspace_complete.py", "smoke_sales_staff.py",
    "probe_platform_boundary.py", "probe_brand_owner_boundary.py",
    "probe_platform_owner.py", "probe_customer_provisioning.py",
    "probe_data_cleanup.py", "probe_tenant_isolation.py",
    "smoke_platform_frontend.py",
]


def main():
    only = sys.argv[1:] or None
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    failed, missing, ok = [], [], []
    t0 = time.time()
    for g in GATES:
        if only and not any(o in g for o in only):
            continue
        path = os.path.join(SCRIPTS, g)
        if not os.path.exists(path):
            missing.append(g)
            print("  ????  %-42s (not found)" % g)
            continue
        started = time.time()
        p = subprocess.run([sys.executable, path], cwd=REPO, env=env,
                           capture_output=True, text=True, errors="replace")
        secs = time.time() - started
        if p.returncode == 0:
            ok.append(g)
            print("  pass  %-42s %5.1fs" % (g, secs))
        else:
            failed.append((g, (p.stdout or "") + (p.stderr or "")))
            print("  FAIL  %-42s %5.1fs" % (g, secs))

    print("\n" + "=" * 78)
    print("%d passed, %d failed, %d missing   (%.0fs)"
          % (len(ok), len(failed), len(missing), time.time() - t0))
    for g, out in failed:
        print("\n--- %s " % g + "-" * max(0, 66 - len(g)))
        tail = [l for l in out.splitlines()
                if l.strip().startswith(("FAIL", "LEAK", "BROKE", "Traceback",
                                         "  File", "AssertionError", "Error",
                                         "NameError", "AttributeError", "TypeError"))]
        for l in (tail[-25:] if tail else out.splitlines()[-25:]):
            print("   " + l)
    print("=" * 78)
    sys.exit(1 if (failed or missing) else 0)


if __name__ == "__main__":
    main()
