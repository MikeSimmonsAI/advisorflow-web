"""READ-ONLY: what brand-sales access two named people actually have.

Answers, per person, the four questions that decide whether they can log in to
the Sales Workspace at all:

  1. Is there a real users/auth identity?
  2. Is there an ACTIVE brand_sales membership for EvoSys Pro?
  3. Is the sales role right?
  4. Is there a usable login method - or has this person never signed in?

WHY THIS EXISTS RATHER THAN READING THE SALES OPERATIONS SCREEN. A name shown
in God Mode comes from an Opportunity's owner, a Membership, or a User row, and
those are three different things. A person can appear on that screen and have no
way to log in. This reads the rows.

SELECTs only. No INSERT, UPDATE, DELETE or DDL, and the session opens a READ
ONLY transaction so the database itself would refuse one. It prints no password
hash and no token - only whether a hash exists.

Run it in the Render shell for advisorflow-backend, where DATABASE_URL is
already set:

    python scripts/inspect_sales_users.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_url = os.environ.get("DATABASE_URL", "")
if not _url:
    raise SystemExit("DATABASE_URL is not set. Run this in the Render shell.")
if _url.startswith("sqlite"):
    # Refusing rather than running is the whole point. The local .env points at
    # a dev SQLite file that is essentially empty, so this script would happily
    # report "NONE FOUND / nobody has brand-sales access" - a true statement
    # about the dev database that reads exactly like a devastating finding about
    # production. An inspection tool that can quietly answer the wrong question
    # is worse than no tool.
    raise SystemExit(
        "DATABASE_URL points at SQLite, so this is a local dev database.\n"
        "Run this in the Render shell for advisorflow-backend, where the real\n"
        "DATABASE_URL is already set. Reporting 'no sales users' from a dev\n"
        "database would be true and completely misleading.")

from sqlalchemy import create_engine, text                          # noqa: E402

engine = create_engine(os.environ["DATABASE_URL"])

# (display name, the membership role this person is supposed to hold)
WANTED = [("Michael Schlueter", "sales_manager"),
          ("Blake Rehani", "sales_rep")]


def rows(conn, sql, **params):
    """All matching rows as dicts. `conn` is deliberately not named `c` - a bind
    parameter called :c would then collide with it, which is exactly the bug
    that broke verify_cp6_production.py on its first real run."""
    return conn.execute(text(sql), params).mappings().all()


with engine.connect() as conn:
    try:
        conn.execute(text("SET TRANSACTION READ ONLY"))
    except Exception:
        pass

    print("\n=== brand sales organisations ===")
    for b in rows(conn,
                  "SELECT b.id, b.name, b.slug, b.is_active, p.name AS platform "
                  "FROM brand_sales_orgs b "
                  "LEFT JOIN platforms p ON p.id = b.platform_id "
                  "ORDER BY b.name"):
        print("  %-38s %-22s platform=%s active=%s"
              % (b["id"], b["slug"], b["platform"], b["is_active"]))

    for name, want_role in WANTED:
        first, last = name.split()[0], name.split()[-1]
        print("\n" + "=" * 72)
        print("%s   (needs: %s)" % (name, want_role))
        print("=" * 72)

        # Matched on NAME, not on a guessed email - the point is to find out
        # what is actually there, including a row under an address nobody
        # expected, and including duplicates.
        found = rows(conn,
            "SELECT id, email, full_name, role, is_active, organization_id, "
            "       must_change_password, last_login_at, created_at, "
            "       (password_hash IS NOT NULL AND password_hash <> '') AS has_pw, "
            "       failed_login_attempts, lockout_until "
            "FROM users "
            "WHERE lower(full_name) LIKE :n OR lower(full_name) LIKE :l "
            "ORDER BY created_at",
            n="%" + first.lower() + "%", l="%" + last.lower() + "%")

        if not found:
            print("  [1] users identity          : NONE FOUND")
            print("  [2] brand sales membership  : n/a")
            print("  [3] sales role              : n/a")
            print("  [4] usable login            : n/a")
            continue

        if len(found) > 1:
            print("  !! %d user rows match this name - possible duplicate identities"
                  % len(found))

        for u in found:
            org_name = None
            if u["organization_id"]:
                r = rows(conn, "SELECT name FROM organizations WHERE id = :i",
                         i=u["organization_id"])
                org_name = r[0]["name"] if r else "(organization row missing)"

            print("\n  [1] users identity          : YES")
            print("      id                      : %s" % u["id"])
            print("      email                   : %s" % u["email"])
            print("      full_name               : %s" % u["full_name"])
            print("      users.role              : %s" % u["role"])
            print("      is_active               : %s" % u["is_active"])
            print("      organization_id         : %s" % (
                ("%s   <-- INSIDE A CUSTOMER TENANT: %s"
                 % (u["organization_id"], org_name))
                if u["organization_id"] else "NULL   (correct for brand sales)"))

            mem = rows(conn,
                "SELECT m.id, m.role, m.is_active, m.scope_id, m.created_at, "
                "       b.name AS org_name, b.slug AS org_slug "
                "FROM memberships m "
                "LEFT JOIN brand_sales_orgs b ON b.id = m.scope_id "
                "WHERE m.user_id = :u AND m.scope_type = 'brand_sales_org' "
                "ORDER BY m.created_at", u=u["id"])

            if not mem:
                print("  [2] brand sales membership  : NONE"
                      "   <-- cannot enter the Sales Workspace")
                print("  [3] sales role              : NONE")
            else:
                for m in mem:
                    print("  [2] brand sales membership  : %s" % (m["org_slug"] or m["scope_id"]))
                    print("      membership role         : %s%s" % (
                        m["role"],
                        "" if m["role"] == want_role else "   <-- EXPECTED %s" % want_role))
                    print("      membership is_active    : %s%s" % (
                        m["is_active"],
                        "" if m["is_active"] else "   <-- INACTIVE, so access is denied"))
                print("  [3] sales role              : %s"
                      % ", ".join(m["role"] for m in mem))

            print("  [4] usable login            : password hash present=%s  "
                  "must_change_password=%s" % (u["has_pw"], u["must_change_password"]))
            print("      last_login_at           : %s"
                  % (u["last_login_at"] or "NEVER SIGNED IN"))
            print("      failed attempts/lockout : %s / %s"
                  % (u["failed_login_attempts"], u["lockout_until"] or "none"))
            if u["last_login_at"] is None:
                print("      -> never signed in. Any password on this row is one")
                print("         nobody has told them, so it is not a usable method.")

    print("\n=== every brand-sales membership in production ===")
    everyone = rows(conn,
        "SELECT u.full_name, u.email, u.is_active AS user_active, u.organization_id, "
        "       m.role, m.is_active AS mem_active, b.slug "
        "FROM memberships m "
        "JOIN users u ON u.id = m.user_id "
        "LEFT JOIN brand_sales_orgs b ON b.id = m.scope_id "
        "WHERE m.scope_type = 'brand_sales_org' "
        "ORDER BY b.slug, m.role, u.full_name")
    if not everyone:
        print("  (none - nobody has brand-sales access)")
    for m in everyone:
        print("  %-22s %-32s %-14s %-18s mem_active=%-5s user_active=%-5s org_id=%s"
              % (m["full_name"], m["email"], m["role"], m["slug"],
                 m["mem_active"], m["user_active"], m["organization_id"] or "NULL"))

print("\nread-only: nothing was written\n")
