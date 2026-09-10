"""THE DEMO SUITE — a brand's demonstration environment, inside the real product.

═══════════════════════════════════════════════════════════════════════════
THIS IS NOT `demo_models.py`, AND THE DIFFERENCE IS THE WHOLE DESIGN
═══════════════════════════════════════════════════════════════════════════

`app/models/demo_models.py` belongs to the APP_ENV=demo DEPLOYMENT: a separate
process, pointed at a separate database, with an egress firewall installed and
every `/demo/*` route returning 404 anywhere else. It is a demo BOX. It is
excellent and nothing here touches it.

This file belongs to the DEMO SUITE: a demonstration environment that lives
INSIDE the production platform, so that a salesperson who has never been given
a second set of credentials can open the product they sell, sign in as
themselves, and present it. The two exist for different reasons and neither
replaces the other — the box is where the platform is demonstrated to Mike; the
Suite is where the product is demonstrated to a prospect by somebody who is not
Mike, which is the entire point of building it.

═══════════════════════════════════════════════════════════════════════════
WHERE THE DEMO DATA LIVES, AND WHY IT IS SAFE
═══════════════════════════════════════════════════════════════════════════

A demo environment is a REAL tenant that is flagged as a demonstration:

    Platform  (EvoSys Pro)
      ├── Organization      is_demo = True    the demo CUSTOMER workspace
      └── BrandSalesOrg     is_demo = True    the demo SALES organization

Nothing new was invented for the isolation. Leads, opportunities, messages and
appointments are scoped by `organization_id` / `brand_sales_org_id` by every
query in the product already, so a demo record is invisible to a real customer
for exactly the reason one customer's records are invisible to another. Reusing
that boundary is worth far more than a parallel set of `demo_*` tables would
be: a parallel table is a second thing to keep correct, and a demo that runs on
tables nothing else reads is a demo of software that does not ship.

WHAT THE FLAG ADDS on top of ordinary tenancy is a REFUSAL. `demo_guard`
asserts `is_demo` before any Demo Suite write, and the outbound send paths
refuse a demo organization outright, so:

  * a demo action can only ever land on a demo-flagged tenant, and
  * a demo tenant can never reach Twilio, Resend or Stripe,

which are two independent failures away from a demo touching anything real.

WHY THE ENVIRONMENT IS A ROW AND NOT A CONFIG CONSTANT. Because "which
organization is the EvoSys Pro demo" has to be answerable from a database, by a
reset that must delete precisely the right rows, in a process that has never
seen the seeding code run. A constant in a Python file cannot be that.
"""

from datetime import datetime
import uuid

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


# Every record the Demo Suite seeds carries an id beginning with this, and the
# environment's own id is interpolated into it. Reset therefore has a provable
# extent: it deletes rows whose id starts with this prefix AND whose tenant is
# the demo tenant. Either condition alone would be a sweep; both together are a
# precise operation. See `demo_environment.canonical_id`.
DEMO_SUITE_ID_PREFIX = "ds-"

# Environment lifecycle.
ENV_EMPTY = "empty"          # provisioned but never seeded
ENV_BUILDING = "building"    # a seed or reset is in flight
ENV_READY = "ready"          # seeded and presentable
ENV_ERROR = "error"          # the last build failed; detail on the row
ENV_STATUSES = (ENV_EMPTY, ENV_BUILDING, ENV_READY, ENV_ERROR)

# Presenter session lifecycle, per scenario.
SESSION_READY = "ready"
SESSION_RUNNING = "running"
SESSION_COMPLETE = "complete"
SESSION_STATUSES = (SESSION_READY, SESSION_RUNNING, SESSION_COMPLETE)


class DemoEnvironment(Base):
    """One demonstration environment per brand. The thing reset resets.

    `platform_id` is unique: a brand has one demo world, not one per
    salesperson. Presenters share the environment and hold their own PLACE in
    it (`DemoSession`), which is what makes a demo repeatable without giving
    every rep their own tenant to keep seeded.
    """
    __tablename__ = "demo_environments"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=False, unique=True, index=True)

    # The two tenants this environment owns. Nullable only between the row
    # being created and the first build completing; `demo_guard` refuses to
    # act on an environment whose tenants are not both resolved.
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=True)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id"), nullable=True)

    status = Column(String, default=ENV_EMPTY, nullable=False)
    # Bumped whenever the seed CONTENT changes in code. An environment seeded
    # at an older version is offered a rebuild rather than silently presenting
    # a story the guided scenarios no longer describe.
    seed_version = Column(Integer, default=0, nullable=False)

    seeded_at = Column(DateTime, nullable=True)
    last_reset_at = Column(DateTime, nullable=True)
    last_reset_by = Column(String, ForeignKey("users.id"), nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def is_ready(self) -> bool:
        return (self.status == ENV_READY
                and bool(self.organization_id)
                and bool(self.brand_sales_org_id))


class DemoSession(Base):
    """One presenter's place in one scenario, in one brand's environment.

    THE STATE IS PER PERSON, THE WORLD IS PER BRAND. Two reps demonstrating the
    same brand on the same afternoon share the seeded records and each keep
    their own step counter, so neither is dragged forward by the other. A rep
    who resets their SESSION goes back to step one; rebuilding the shared world
    is `demo_admin` and a different operation entirely.
    """
    __tablename__ = "demo_sessions"

    id = Column(String, primary_key=True, default=gen_uuid)
    environment_id = Column(String,
                            ForeignKey("demo_environments.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    scenario_key = Column(String, nullable=False)

    status = Column(String, default=SESSION_READY, nullable=False)
    current_step = Column(Integer, default=0, nullable=False)
    total_steps = Column(Integer, default=0, nullable=False)
    # The keys of the steps that have actually been run, newline-separated.
    # A list rather than only a counter because a presenter may legitimately
    # jump back and re-run a step in front of a prospect who asked to see it
    # again, and a bare counter cannot represent that.
    completed_steps = Column(Text, nullable=True)

    started_at = Column(DateTime, nullable=True)
    last_advanced_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("environment_id", "user_id", "scenario_key",
                         name="uq_demo_session_env_user_scenario"),
        Index("ix_demo_sessions_user", "user_id", "scenario_key"),
    )


class DemoActionEvent(Base):
    """Append-only record of what a presenter made the demo do.

    Deliberately NOT written into `audit_log_entries`. A demo action is not a
    control-plane action and mixing them would mean every genuine audit query
    had to learn to filter demo noise out of itself — the same reasoning the
    APP_ENV=demo box already applies to `demo_events`, arrived at
    independently and for the same reason.

    It is worth keeping at all because "the demo did something odd in front of
    Brightwater on Tuesday" is otherwise unanswerable, and because a simulated
    provider call should be as traceable as a real one.
    """
    __tablename__ = "demo_action_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    environment_id = Column(String, ForeignKey("demo_environments.id",
                                               ondelete="CASCADE"),
                            nullable=True, index=True)
    platform_id = Column(String, nullable=True, index=True)

    user_id = Column(String, nullable=True)
    user_email = Column(String, nullable=True)

    scenario_key = Column(String, nullable=True)
    step_key = Column(String, nullable=True)
    # move_stage | complete_task | book_appointment | send_sms | ai_follow_up |
    # qualify_lead | reset_session | rebuild_environment | enter
    action = Column(String, nullable=False)
    target_type = Column(String, nullable=True)
    target_id = Column(String, nullable=True)

    # Which provider WOULD have been called, when the action simulates one:
    # sms | email | voice | calendar | ai | billing. NULL for pure state moves.
    simulated_provider = Column(String, nullable=True)
    success = Column(Boolean, default=True, nullable=False)
    detail = Column(Text, nullable=True)

    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False,
                         index=True)

    __table_args__ = (
        Index("ix_demo_action_events_env_time", "environment_id", "occurred_at"),
    )
