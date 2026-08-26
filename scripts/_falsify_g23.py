"""FALSIFIABILITY RUN for gate 23.

A gate that has never failed is a gate nobody has tested. This breaks the fix
five different ways and requires the gate to notice each one, then puts every
file back exactly as it was.

Not committed as part of the product surface - it is run by hand before a
security commit and its output is the evidence.
"""
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPS = os.path.join(REPO, "app", "deps.py")
ADMIN = os.path.join(REPO, "app", "routers", "admin_router.py")
GATE = os.path.join(REPO, "scripts", "probe_platform_boundary.py")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s):
    # OneDrive holds a lock on files in this tree at unpredictable moments and
    # a failed write here would silently mean the sabotage never applied - the
    # run would then "pass" while testing nothing at all.
    for attempt in range(20):
        try:
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(s)
            return
        except PermissionError:
            time.sleep(0.5)
    raise SystemExit("could not write %s - sabotage not applied, run aborted" % p)


SABOTAGES = [
    ("reset-password loads the target with no scoping at all",
     ADMIN,
     "    target = load_user_in_scope(db, current_user, user_id)",
     "    target = db.query(User).filter(User.id == user_id).first()\n"
     "    if not target:\n"
     "        raise HTTPException(status_code=404, detail='User not found')",
     "reset-password on the god_admin OWNER"),

    ("the god_admin / elevated-target refusal is removed",
     DEPS,
     "    if target.role in ELEVATED_ROLES:\n"
     "        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=\"User not found\")",
     "    pass",
     "PEER super_admin"),

    ("update_organization fetches the org straight from the URL again",
     ADMIN,
     "    org = load_org_in_scope(db, current_user, org_id)\n\n    if payload.name is not None:",
     "    org = db.query(Organization).filter(Organization.id == org_id).first()\n"
     "    if not org:\n"
     "        raise HTTPException(status_code=404, detail='Organization not found')\n\n"
     "    if payload.name is not None:",
     "PUT /admin/organizations/{other platform's org}"),

    ("set_org_platform goes back to require_super_admin",
     ADMIN,
     "    current_user: User = Depends(require_god),\n"
     "):\n"
     "    \"\"\"Assign or unassign a platform for an org.",
     "    current_user: User = Depends(require_super_admin),\n"
     "):\n"
     "    \"\"\"Assign or unassign a platform for an org.",
     "/platform is refused"),

    ("provision_client stops stamping the platform",
     ADMIN,
     "        platform_id=_resolved_platform_id,\n",
     "",
     "stamped with the CALLER'S platform"),

    ("wipe_demo_data fetches the org straight from the URL again",
     ADMIN,
     "    target_org = load_org_in_scope(db, current_user, org_id)\n\n    # Get all lead IDs for the org",
     "    target_org = db.query(Organization).filter(Organization.id == org_id).first()\n"
     "    if not target_org:\n"
     "        raise HTTPException(status_code=404, detail='Organization not found')\n\n"
     "    # Get all lead IDs for the org",
     "demo/wipe/{other platform's org}"),
]


def run_gate():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, GATE], cwd=REPO, env=env,
                       capture_output=True, text=True, errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    originals = {DEPS: read(DEPS), ADMIN: read(ADMIN)}

    print("=" * 78)
    print("BASELINE - the gate must pass on the real tree")
    print("=" * 78)
    rc, out = run_gate()
    print("  exit=%s  %s" % (rc, "PASS" if rc == 0 else "FAIL"))
    if rc != 0:
        print(out[-3000:])
        raise SystemExit("baseline is not green - fix that before falsifying")

    caught, missed = [], []
    try:
        for i, (name, path, old, new, expect) in enumerate(SABOTAGES, 1):
            print("\n" + "=" * 78)
            print("SABOTAGE %d: %s" % (i, name))
            print("=" * 78)
            src = originals[path]
            if src.count(old) != 1:
                print("  !! anchor matched %d times - sabotage NOT applied" % src.count(old))
                missed.append(name + "  [anchor did not match]")
                continue
            write(path, src.replace(old, new, 1))
            rc, out = run_gate()
            failed = rc != 0
            named = expect in out
            print("  gate exit=%s   expected finding present=%s" % (rc, named))
            if failed and named:
                for line in out.splitlines():
                    if line.strip().startswith(("LEAK", "BROKE")):
                        print("    " + line.strip())
                caught.append(name)
            else:
                if not failed:
                    print("  !! THE GATE PASSED A SABOTAGED TREE")
                elif not named:
                    print("  !! gate failed but for the wrong reason - expected %r" % expect)
                    for line in out.splitlines():
                        if line.strip().startswith(("LEAK", "BROKE")):
                            print("    " + line.strip())
                missed.append(name)
            write(path, src)
    finally:
        for p, s in originals.items():
            write(p, s)
        print("\n" + "=" * 78)
        print("tree restored")

    rc, out = run_gate()
    print("re-check after restore: exit=%s  %s" % (rc, "PASS" if rc == 0 else "FAIL"))

    print("\ncaught %d/%d" % (len(caught), len(SABOTAGES)))
    for m in missed:
        print("  MISSED: %s" % m)
    sys.exit(1 if (missed or rc != 0) else 0)


if __name__ == "__main__":
    main()
