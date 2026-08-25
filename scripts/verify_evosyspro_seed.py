"""
Post-seed verification for the EvoSys Pro brand-sales seed. READ ONLY.

The ten checks Mike required, run against whatever DATABASE_URL points at:

   1  brand sales org exists under the EvoSys Pro platform
   2  the brand sales org is NOT a customer `organizations` row
   3  four sales packages exist, priced as approved, none linked to Stripe
   4  the three sales users exist, exactly once each
   5  Michael Schlueter and Mike Simmons are separate rows
   6  the two new sales users carry organization_id = NULL
   7  both new users are inactive-until-password-change (must_change_password)
   8  memberships are scoped to brand_sales_org with the right roles
   9  users.role was not repurposed by the membership layer
  10  test records are flagged, carry a note, and are excluded from outreach

Prints PASS/FAIL per check and exits non-zero if any fail.

    python scripts/verify_evosyspro_seed.py
"""
import os
import sys

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL not set in this shell.")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy import create_engine, text   # noqa: E402

eng = create_engine(os.environ["DATABASE_URL"])
FAILURES = []

SALES_EMAILS = ("michaelpschlueter@gmail.com", "blakerehani@gmail.com")
MIKE = "mike@simmonsstrong.com"


def check(n, label, ok, detail=""):
    print("  %-4s %2d. %s%s" % ("PASS" if ok else "FAIL", n, label,
                                ("\n           -> " + str(detail)) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def one(c, sql, **p):
    return c.execute(text(sql), p).scalar()


def rows(c, sql, **p):
    return c.execute(text(sql), p).fetchall()


with eng.connect() as c:
    print("POST-SEED VERIFICATION\n")

    # 1
    org = rows(c, "SELECT id, name, slug, platform_id, is_active FROM brand_sales_orgs")
    check(1, "brand sales org exists under EvoSys Pro",
          len(org) == 1 and org[0][3] == "plt-evosyspro" and org[0][4], org)
    sales_org_id = org[0][0] if org else None
    if sales_org_id:
        print("           %s  %s" % (org[0][1], sales_org_id))

    # 2
    clash = one(c, "SELECT COUNT(*) FROM organizations WHERE slug = 'evosyspro-sales'")
    check(2, "brand sales org is NOT a customer organizations row", clash == 0,
          "%d customer org(s) share the slug" % clash)

    # 3
    pkgs = rows(c, "SELECT key, name, price, is_custom, billing_plan_key, is_active "
                   "FROM brand_packages WHERE platform_id='plt-evosyspro' ORDER BY sort_order")
    expected = {"starter": 1497.00, "growth": 2495.00,
                "professional": 4995.00, "multi_tenant": None}
    priced_ok = len(pkgs) == 4 and all(
        (p[2] is None if expected[p[0]] is None else float(p[2]) == expected[p[0]])
        for p in pkgs if p[0] in expected)
    check(3, "four packages, approved prices, none linked to a Stripe plan",
          priced_ok and all(p[4] is None for p in pkgs), pkgs)
    for p in pkgs:
        print("           %-22s %-12s %s" % (p[1], p[2] if p[2] is not None else "custom", p[0]))

    # 4
    dupes = rows(c, "SELECT email, COUNT(*) FROM users WHERE email IN "
                    "(:a,:b,:m) GROUP BY email",
                 a=SALES_EMAILS[0], b=SALES_EMAILS[1], m=MIKE)
    check(4, "all three sales users exist exactly once",
          len(dupes) == 3 and all(d[1] == 1 for d in dupes), dupes)

    # 5
    ids = dict(rows(c, "SELECT email, id FROM users WHERE email IN (:a,:b)",
                    a=SALES_EMAILS[0], b=MIKE))
    check(5, "Michael Schlueter and Mike Simmons are separate user rows",
          len(ids) == 2 and ids[SALES_EMAILS[0]] != ids[MIKE], ids)

    # 6
    orgs = rows(c, "SELECT email, organization_id FROM users WHERE email IN (:a,:b)",
                a=SALES_EMAILS[0], b=SALES_EMAILS[1])
    check(6, "new sales users carry organization_id = NULL",
          len(orgs) == 2 and all(o[1] is None for o in orgs), orgs)

    # 6b — nothing else in the DB accidentally lost its tenancy
    stray = one(c, "SELECT COUNT(*) FROM users WHERE organization_id IS NULL "
                   "AND email NOT IN (:a,:b)", a=SALES_EMAILS[0], b=SALES_EMAILS[1])
    check(6, "no OTHER user lost its organization", stray == 0,
          "%d unexpected NULL-org users" % stray)

    # 7
    st = rows(c, "SELECT email, must_change_password, is_active, role "
                 "FROM users WHERE email IN (:a,:b)",
              a=SALES_EMAILS[0], b=SALES_EMAILS[1])
    check(7, "new users are active and must change password on first login",
          all(r[1] and r[2] for r in st), st)

    # 8
    mem = rows(c, """SELECT u.email, m.role, m.scope_type, m.scope_id, m.is_active
                     FROM memberships m JOIN users u ON u.id = m.user_id
                     ORDER BY u.email""")
    by = {r[0]: r for r in mem}
    check(8, "three brand_sales_org memberships with the approved roles",
          len(mem) == 3
          and all(r[2] == "brand_sales_org" and r[3] == sales_org_id and r[4] for r in mem)
          and by.get(SALES_EMAILS[0], [None, None])[1] == "sales_manager"
          and by.get(SALES_EMAILS[1], [None, None])[1] == "sales_rep"
          and by.get(MIKE, [None, None])[1] == "sales_manager", mem)
    for r in mem:
        print("           %-30s %s" % (r[0], r[1]))

    # 9
    bad_role = rows(c, "SELECT email, role FROM users WHERE role IN "
                       "('sales_manager','sales_rep')")
    check(9, "users.role was NOT repurposed by the membership layer",
          len(bad_role) == 0, bad_role)
    mike_role = one(c, "SELECT role FROM users WHERE email = :e", e=MIKE)
    check(9, "Mike Simmons is still god_admin", mike_role == "god_admin", mike_role)

    # 10
    flagged = rows(c, "SELECT id, first_name, last_name, phone, email, is_test, "
                      "test_note FROM leads WHERE is_test IS TRUE")
    check(10, "exactly the two evidenced QA records are flagged", len(flagged) == 2, flagged)
    check(10, "every flagged record carries an explanatory note",
          all(f[6] for f in flagged), flagged)
    for f in flagged:
        print("           %s  %s %s  %s" % (f[0], f[1], f[2], f[3]))

    # 10b — Blake was NOT invented a lead record
    blake = one(c, "SELECT COUNT(*) FROM leads WHERE lower(email)=:e OR phone LIKE :p "
                   "OR phone_raw LIKE :p", e=SALES_EMAILS[1], p="%4805501982")
    check(10, "Blake Rehani has no lead record and none was invented", blake == 0, blake)

    # 10c — the outreach query actually excludes them
    excluded = one(c, "SELECT COUNT(*) FROM leads WHERE is_test IS NOT TRUE "
                      "AND id IN ('8bafdfa9-4aea-428a-bbbf-a4adff7ef586',"
                      "'31404722-25d3-4e1f-b9cc-4f7f559e14b0')")
    check(10, "flagged records fall OUT of the outreach-eligible query",
          excluded == 0, "%d still eligible" % excluded)

    # regression: the rest of the data is untouched
    print("\n  data integrity:")
    for label, sql in [("leads total", "SELECT COUNT(*) FROM leads"),
                       ("users total", "SELECT COUNT(*) FROM users"),
                       ("organizations", "SELECT COUNT(*) FROM organizations"),
                       ("platforms", "SELECT COUNT(*) FROM platforms"),
                       ("opportunities", "SELECT COUNT(*) FROM opportunities")]:
        print("           %-16s %s" % (label, one(c, sql)))

print("\n" + "=" * 66)
if FAILURES:
    print("FAILED (%d): %s" % (len(FAILURES), "; ".join(FAILURES)))
    sys.exit(1)
print("ALL POST-SEED CHECKS PASSED")
