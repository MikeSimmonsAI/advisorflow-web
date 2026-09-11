"""WHAT THIS PARTICULAR EMPLOYEE MAY DO — the intersection, computed once.

THREE LISTS, INTERSECTED, PLUS AN EXPLICIT GRANT.

    platform template   the outer bound. A job that cannot book cannot be
                        given booking by anybody.
    brand offering      may narrow. Never widens: registry.normalize_tool_keys
                        takes `bound` and cannot return a key outside it.
    employee authority  an explicit row per tool, default DENY. A tool inside
                        both lists above is STILL refused without a grant.

The fourth gate — is the CUSTOMER entitled to this employee at all — lives in
entitlement.py, because that is a commercial question and T2 owns commerce.

WHY DEFAULT-DENY AT THE EMPLOYEE LEVEL when the template already bounds it.
Because "this employee may text people" should be a decision somebody made,
with a name and a timestamp on it, not a consequence of which template was
picked. It also makes revocation real: removing one grant row stops one action,
where editing a JSON list is an edit nobody can later attribute.

`resolve()` reads the database ONCE per authorization and returns a plain
object. It is called per tool call — see the execution-time argument in
activation.py — so it stays cheap: three small reads, no joins across leads.
"""

import json
import logging
from datetime import datetime, time
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.models.workforce_models import (AIBrandOffering, AIEmployee,
                                         AIEmployeeAuthority,
                                         AIEmployeeTemplate)
from app.services.workforce import constants as C
from app.services.workforce import registry

_log = logging.getLogger(__name__)


def json_list(raw, default=None) -> Optional[List]:
    """Parse a JSON list column. A CORRUPT VALUE IS NOT A LICENCE.

    Returns `default` (normally None, meaning "nothing stated") rather than
    everything, which is the same reading entitlements.enabled_for applies to
    a corrupt allow-list and for the same reason.
    """
    if raw is None:
        return default
    if isinstance(raw, (list, tuple)):
        return list(raw)
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return default
    return list(val) if isinstance(val, list) else default


def json_obj(raw, default=None) -> Dict:
    if raw is None:
        return dict(default or {})
    if isinstance(raw, dict):
        return dict(raw)
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return dict(default or {})
    return dict(val) if isinstance(val, dict) else dict(default or {})


class EffectivePolicy:
    """Everything the gateway needs to answer "may you", in one object."""

    __slots__ = ("employee", "template", "offering", "tool_keys", "channels",
                 "max_iterations", "max_tool_calls", "max_touches",
                 "operating_hours", "timezone", "required_feature",
                 "entitlement_key", "escalations", "constraints",
                 "template_key", "job_role")

    def __init__(self, employee, template, offering, tool_keys, channels,
                 max_iterations, max_tool_calls, max_touches, operating_hours,
                 timezone, required_feature, entitlement_key, escalations,
                 constraints, template_key, job_role):
        self.employee = employee
        self.template = template
        self.offering = offering
        self.tool_keys: Set[str] = set(tool_keys)
        self.channels: Set[str] = set(channels)
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.max_touches = max_touches
        self.operating_hours = operating_hours
        self.timezone = timezone
        self.required_feature = required_feature
        self.entitlement_key = entitlement_key
        self.escalations = escalations
        self.constraints = constraints
        self.template_key = template_key
        self.job_role = job_role

    def as_dict(self) -> Dict:
        return {
            "template_key": self.template_key,
            "job_role": self.job_role,
            "tool_keys": sorted(self.tool_keys),
            "channels": sorted(self.channels),
            "max_iterations": self.max_iterations,
            "max_tool_calls": self.max_tool_calls,
            "max_touches": self.max_touches,
            "operating_hours": self.operating_hours,
            "timezone": self.timezone,
            "required_feature": self.required_feature,
            "entitlement_key": self.entitlement_key,
            "always_escalate": list(self.escalations),
        }


def _clamp(value: Optional[int], default: int, ceiling: int) -> int:
    """A configured limit may LOWER a default and may never raise past the
    ceiling. The ceiling is the runaway protection; configuration is not
    allowed to negotiate with it."""
    try:
        v = int(value) if value is not None else default
    except (TypeError, ValueError):
        v = default
    if v < 1:
        v = 1
    return min(v, ceiling)


def resolve(db: Session, employee: AIEmployee) -> EffectivePolicy:
    """The employee's effective authority. Never raises on missing rows."""
    tpl_row = (db.query(AIEmployeeTemplate)
               .filter(AIEmployeeTemplate.id == employee.template_id).first())

    # THE CODE REGISTRY IS THE SOURCE; the table is its mirror. If the two
    # disagree — a mirror row that was seeded before a template's tool list was
    # tightened — the INTERSECTION is used, so the narrower of the two wins.
    spec = registry.template(getattr(tpl_row, "key", "") or "")

    tpl_tools = json_list(getattr(tpl_row, "allowed_tool_keys", None))
    tpl_channels = json_list(getattr(tpl_row, "allowed_channels", None))
    if spec is not None:
        tpl_tools = (registry.normalize_tool_keys(tpl_tools, bound=spec.tool_keys)
                     if tpl_tools is not None else list(spec.tool_keys))
        tpl_channels = (registry.normalize_channels(tpl_channels,
                                                    bound=spec.channels)
                        if tpl_channels is not None else list(spec.channels))
    else:
        tpl_tools = registry.normalize_tool_keys(tpl_tools or [])
        tpl_channels = registry.normalize_channels(tpl_channels or [])

    # ── BRAND NARROWING ─────────────────────────────────────────────────────
    offering = None
    if employee.offering_id:
        offering = (db.query(AIBrandOffering)
                    .filter(AIBrandOffering.id == employee.offering_id).first())
        # A BRAND OFFERING THAT HAS BEEN SWITCHED OFF REMOVES THE AUTHORITY.
        # Not "the employee keeps working until somebody notices": the brand
        # withdrawing the product is a decision, and it takes effect on the
        # next tool call.
        if offering is not None and not offering.is_enabled:
            tpl_tools, tpl_channels = [], []
    if offering is not None:
        brand_tools = json_list(offering.allowed_tool_keys)
        if brand_tools is not None:
            tpl_tools = registry.normalize_tool_keys(brand_tools, bound=tpl_tools)
        brand_channels = json_list(offering.allowed_channels)
        if brand_channels is not None:
            tpl_channels = registry.normalize_channels(brand_channels,
                                                       bound=tpl_channels)

    # ── CUSTOMER NARROWING ──────────────────────────────────────────────────
    emp_channels = json_list(employee.allowed_channels)
    channels = (registry.normalize_channels(emp_channels, bound=tpl_channels)
                if emp_channels is not None else list(tpl_channels))

    # ── EXPLICIT GRANTS. DEFAULT DENY. ──────────────────────────────────────
    grants = (db.query(AIEmployeeAuthority)
              .filter(AIEmployeeAuthority.employee_id == employee.id,
                      AIEmployeeAuthority.organization_id == employee.organization_id)
              .all())
    granted = {g.tool_key for g in grants if g.is_allowed}
    constraints = {g.tool_key: json_obj(g.constraints) for g in grants}
    tool_keys = sorted(set(tpl_tools) & granted)

    policy_blob = {}
    if spec is not None:
        policy_blob.update(spec.policy)
    policy_blob.update(json_obj(getattr(tpl_row, "default_policy", None)))
    if offering is not None:
        policy_blob.update(json_obj(offering.brand_policy))
    cfg = json_obj(employee.config)

    escalations = []
    for src in (policy_blob.get("always_escalate"), cfg.get("always_escalate")):
        if isinstance(src, list):
            for item in src:
                if isinstance(item, str) and item not in escalations:
                    escalations.append(item)

    return EffectivePolicy(
        employee=employee,
        template=tpl_row,
        offering=offering,
        tool_keys=tool_keys,
        channels=channels,
        max_iterations=_clamp(employee.max_iterations,
                              int(policy_blob.get("max_iterations",
                                                  C.DEFAULT_MAX_ITERATIONS)),
                              C.MAX_ITERATIONS_CEILING),
        max_tool_calls=_clamp(employee.max_tool_calls,
                              int(policy_blob.get("max_tool_calls",
                                                  C.DEFAULT_MAX_TOOL_CALLS)),
                              C.MAX_TOOL_CALLS_CEILING),
        max_touches=_clamp(employee.max_touches,
                           int(policy_blob.get("max_touches",
                                               C.DEFAULT_MAX_TOUCHES)),
                           50),
        operating_hours=json_obj(employee.operating_hours),
        timezone=employee.timezone or "America/Chicago",
        required_feature=(getattr(tpl_row, "required_feature", None)
                          or (spec.required_feature if spec else None)),
        entitlement_key=(getattr(offering, "entitlement_key", None)
                         or getattr(tpl_row, "entitlement_key", None)
                         or (spec.entitlement_key if spec else None)),
        escalations=escalations,
        constraints=constraints,
        template_key=getattr(tpl_row, "key", None) or (spec.key if spec else ""),
        job_role=employee.job_role or getattr(tpl_row, "job_role", "") or "",
    )


def may_use_tool(pol: EffectivePolicy, tool_key: str
                 ) -> Tuple[bool, Optional[str], Optional[str]]:
    """(allowed, denial_code, human reason). NEVER raises.

    The three refusals are distinguished because they have three different
    fixes: the job does not do this, the brand does not sell this, or nobody
    granted it. An operator told only "denied" would have to guess which.
    """
    spec = registry.tool(tool_key)
    if spec is None:
        return False, C.DENY_UNKNOWN_TOOL, (
            "'%s' is not a registered platform tool." % tool_key)

    tpl_spec = registry.template(pol.template_key or "")
    if tpl_spec is not None and tool_key not in tpl_spec.tool_keys:
        return False, C.DENY_NOT_IN_TEMPLATE, (
            "The %s job does not include '%s'." % (tpl_spec.name, spec.label))

    if pol.offering is not None and not pol.offering.is_enabled:
        return False, C.DENY_NOT_IN_BRAND_OFFERING, (
            "This employee is not currently offered by the brand.")

    if tool_key not in pol.tool_keys:
        return False, C.DENY_NOT_GRANTED, (
            "'%s' has not been granted to this employee." % spec.label)

    return True, None, None


def channel_enabled(pol: EffectivePolicy, channel: Optional[str]) -> bool:
    if not channel:
        return True
    return channel in pol.channels


def _parse_hhmm(raw, fallback: time) -> time:
    try:
        h, m = str(raw).split(":")[:2]
        return time(int(h) % 24, int(m) % 60)
    except Exception:                                       # noqa: BLE001
        return fallback


def within_operating_hours(pol: EffectivePolicy,
                           now: Optional[datetime] = None) -> Tuple[bool, str]:
    """Is the employee inside the window the customer configured?

    UNCONFIGURED MEANS NOT PERMITTED TO REACH ANYONE, not "any time".
    `{}` here is a customer who never answered the "when may it work" question,
    and 3am texting is exactly the harm a defaulted-open window produces. The
    hours question is marked required in the template's config questions, so an
    employee reaching this branch was created by something that skipped it.

    The comparison is made in the employee's configured timezone when
    `zoneinfo` can resolve it, and in UTC otherwise — never silently in the
    server's local time, which on Render is UTC and in a developer's terminal
    is not.
    """
    hours = pol.operating_hours or {}
    if not hours:
        return False, "No operating hours configured for this employee."

    now = now or datetime.utcnow()
    tzname = pol.timezone or "America/Chicago"
    local = now
    try:
        from zoneinfo import ZoneInfo
        aware = now
        if aware.tzinfo is None:
            from datetime import timezone as _tz
            aware = aware.replace(tzinfo=_tz.utc)
        local = aware.astimezone(ZoneInfo(tzname))
    except Exception:                                       # noqa: BLE001
        _log.debug("workforce: could not resolve timezone %r; comparing in UTC",
                   tzname)

    days = hours.get("days")
    if isinstance(days, list) and days:
        try:
            if int(local.weekday()) not in [int(d) for d in days]:
                return False, ("%s is outside this employee's working days."
                               % local.strftime("%A"))
        except (TypeError, ValueError):
            return False, "Operating-hours days are not readable."

    start = _parse_hhmm(hours.get("start", "09:00"), time(9, 0))
    end = _parse_hhmm(hours.get("end", "17:00"), time(17, 0))
    cur = local.time()
    if start <= end:
        ok = start <= cur <= end
    else:
        # A window that wraps midnight. Rare, legitimate for a receptionist.
        ok = cur >= start or cur <= end
    if not ok:
        return False, ("%s is outside this employee's hours (%s-%s %s)."
                       % (cur.strftime("%H:%M"), start.strftime("%H:%M"),
                          end.strftime("%H:%M"), tzname))
    return True, ""
