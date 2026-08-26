"""Demo scenario state and its audit trail.

THESE TABLES ONLY EVER EXIST IN THE DEMO DATABASE. They register on the same
`Base` as everything else, so `create_all()` will create them in production too
— which is harmless, because nothing in production ever writes to them and
every route that reads them 404s outside the demo environment. Keeping them on
one Base is what lets the demo service run the SAME image as production rather
than a fork that could drift.

WHY THERE IS STATE AT ALL. A demo is a performance with a running order. The
operator needs to know which scenario is loaded, which step they are on, and
what the next step is — mid-presentation, on a phone, without reading code.
That is what `DemoScenarioState` holds: one row per scenario, not a pile of
flags spread across the records being demonstrated.

WHY THE AUDIT IS SEPARATE. `DemoEvent` records what the operator did and what
the simulated providers were asked to do. It is deliberately NOT mixed into the
customer-facing activity tables: a simulated SMS appears in the lead's timeline
because a real `Message` row was written, and the fact that a demo operator
caused it belongs here instead. Real usage analytics must never have to filter
demo rows out of their numbers — see claude/EVOSYS_DEMO_MODE.md.
"""

from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean, Index
from datetime import datetime
import uuid

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


# Every demo-owned record's id begins with this. Reset deletes by this prefix,
# which is what makes "remove the demo data" a precise operation rather than a
# hopeful one — see demo_reset.py.
DEMO_ID_PREFIX = "demo-"

# Scenario lifecycle.
SCENARIO_EMPTY = "empty"          # never seeded, or reset
SCENARIO_READY = "ready"          # seeded, at step 0
SCENARIO_RUNNING = "running"      # at least one step advanced
SCENARIO_COMPLETE = "complete"    # final step reached


class DemoScenarioState(Base):
    """One row per scenario key. The operator's place in the performance."""
    __tablename__ = "demo_scenario_state"

    id = Column(String, primary_key=True, default=gen_uuid)
    scenario_key = Column(String, nullable=False, unique=True, index=True)
    # customer | brand — which of the two product domains this demonstrates.
    domain = Column(String, nullable=False)

    status = Column(String, default=SCENARIO_EMPTY, nullable=False)
    current_step = Column(Integer, default=0, nullable=False)
    total_steps = Column(Integer, default=0, nullable=False)

    seeded_at = Column(DateTime, nullable=True)
    last_advanced_at = Column(DateTime, nullable=True)
    last_reset_at = Column(DateTime, nullable=True)
    # Who is running the demo. A user id, for the audit trail.
    operator_user_id = Column(String, nullable=True)

    def is_seeded(self) -> bool:
        return self.status in (SCENARIO_READY, SCENARIO_RUNNING, SCENARIO_COMPLETE)


class DemoEvent(Base):
    """Append-only record of demo operator actions and simulated provider calls.

    Answers, after a presentation went sideways: which scenario, which step,
    who, when, what was simulated, and what came back.
    """
    __tablename__ = "demo_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    scenario_key = Column(String, nullable=True, index=True)
    # seed | advance | reset | simulate | enter | error
    action = Column(String, nullable=False)
    step_index = Column(Integer, nullable=True)
    step_label = Column(String, nullable=True)

    operator_user_id = Column(String, nullable=True)
    operator_email = Column(String, nullable=True)

    # Which fake answered, when one did: sms | email | voice | calendar |
    # meeting | portal. NULL for pure operator actions.
    simulated_provider = Column(String, nullable=True)
    success = Column(Boolean, default=True, nullable=False)
    detail = Column(Text, nullable=True)

    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_demo_events_scenario_time", "scenario_key", "occurred_at"),
    )
