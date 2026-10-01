"""
PIPELINE STAGE CLOCK — records WHEN a lead entered its current stage.

The sales pipeline's stages are the organization's configurable lead tiers
(industry_templates.org_lead_tiers), stored on `Lead.tier`. Nothing recorded
when that value changed, so "age in stage" could not be told truthfully.

This module attaches ONE SQLAlchemy attribute listener to `Lead.tier`: every
ORM write that changes the tier (board move, rate-request status change,
enrollment, lead edit, intake) stamps `Lead.stage_entered_at` with the current
UTC time. It does not fire for bulk `query.update()` statements, which bypass
the ORM; those leave the previous stamp (or NULL), and the board then reports
the honest fallback ("since <created>") rather than a guessed age.

Leads that existed before the column shipped have NULL here. Nothing backfills
them: a backfill would have to invent the date.

Importing this module is what installs the listener (idempotent).
"""
from datetime import datetime

from sqlalchemy import event
from sqlalchemy.orm.attributes import NO_VALUE

from app.models.models import Lead

_INSTALLED = False


def _on_tier_set(target, value, oldvalue, initiator):
    if value == oldvalue:
        return value
    if oldvalue is NO_VALUE and value is None:
        return value
    target.stage_entered_at = datetime.utcnow()
    return value


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Lead.tier, "set", _on_tier_set, retval=True, active_history=True)
    _INSTALLED = True


install()
