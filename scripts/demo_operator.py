"""Create the demo operator accounts and load both scenarios.

REFUSES TO RUN OUTSIDE THE DEMO ENVIRONMENT. `environment.require_demo()` is
the first thing it does after import, so an operator with the wrong shell
exported cannot point this at anything real. The passwords below exist only in
a demo database that holds no real person's data, and they are documented
openly in claude/EVOSYS_DEMO_MODE.md so nobody has to ask for them.

    scripts\\demo_local.bat seed
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if (os.environ.get("APP_ENV") or "").lower() != "demo":
    print("APP_ENV is not 'demo'. Refusing to run.")
    print("Use scripts\\demo_local.bat seed, which sets it.")
    sys.exit(2)

from sqlalchemy import inspect                                     # noqa: E402
from app.deps import SessionLocal, engine                          # noqa: E402
from app.models.models import Base, User, Platform                 # noqa: E402
import app.models.sales_models                                     # noqa: E402,F401
import app.models.scheduling_models                                # noqa: E402,F401
import app.models.calendar_models                                  # noqa: E402,F401
import app.models.meeting_models                                   # noqa: E402,F401
import app.models.integration_models                               # noqa: E402,F401
import app.models.demo_models                                      # noqa: E402,F401
from app.services import environment as env                        # noqa: E402
from app.services import demo_firewall as fw                       # noqa: E402
from app.services import demo_runner as runner                     # noqa: E402
from app.services import demo_scenarios as registry                # noqa: E402
from app.services.auth_service import hash_password                # noqa: E402
from app.services.demo_scenarios import DEMO_PASSWORD              # noqa: E402

env.require_demo()

# The seeder writes nothing outbound, but installing the firewall here means a
# stray provider call during seeding fails loudly instead of quietly leaving.
fw.install()

# NOT `demo-` prefixed, deliberately.
#
# An earlier version used `demo-operator-owner`, and pressing "Reset
# everything" then deleted the operator's own account and logged them out
# mid-presentation - the reset sweeps every id starting `demo-`, and it was
# right to. The operator is infrastructure, not scenario data: it must survive
# every reset, which means it must fall outside the prefix that reset owns.
#
# The platform row below is the same case for the same reason.
OPERATOR_ID = "operator-demo-owner"
OPERATOR_PLATFORM_ID = "operator-demo-platform"
OPERATOR_EMAIL = "owner@example.com"


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    op = db.query(User).filter(User.id == OPERATOR_ID).first()
    if op is None:
        if db.query(Platform).filter(Platform.id == OPERATOR_PLATFORM_ID).first() is None:
            db.add(Platform(id=OPERATOR_PLATFORM_ID, name="EvoSys Pro",
                            slug="operator-demo-root"))
            db.flush()
        op = User(id=OPERATOR_ID, organization_id=None,
                  email=OPERATOR_EMAIL, full_name="Demo Operator",
                  password_hash=hash_password(DEMO_PASSWORD),
                  role="god_admin", must_change_password=False, is_active=True)
        db.add(op)
        db.commit()
        print("Created demo operator: %s" % OPERATOR_EMAIL)
    else:
        print("Demo operator already exists: %s" % OPERATOR_EMAIL)

    for key in ("customer_reactivation", "brand_sales"):
        out = runner.seed_scenario(db, key, operator=op)
        print("  seed %-24s %s" % (key, "OK" if out.get("ok") else out.get("error")))

    print()
    print("  Demo environment ready.")
    print("  Sign in as %s / %s" % (OPERATOR_EMAIL, DEMO_PASSWORD))
    print("  Scenario logins are listed in claude/EVOSYS_DEMO_MODE.md")
    db.close()


if __name__ == "__main__":
    main()
