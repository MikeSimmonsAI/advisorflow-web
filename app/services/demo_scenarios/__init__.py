"""The scenario registry.

ADDING A SCENARIO IS ADDING A FILE AND ONE LINE HERE. Nothing else in the
system needs to know it exists - the control panel, the reset and the guided
checklist all read from this registry rather than from a hardcoded list.

ADDING AN INDUSTRY is the same operation. A roofing reactivation scenario is a
subclass of `Scenario` with different strings, registered below. No service
changes, no schema changes, no branch anywhere on `industry`.
"""

from typing import Dict, List, Optional

from app.services.demo_scenarios.base import (
    Scenario, Step, DOMAIN_CUSTOMER, DOMAIN_BRAND,
    demo_id, demo_phone, demo_email, assert_safe_contact,
)
from app.services.demo_scenarios.customer_reactivation import (
    CustomerReactivation, DEMO_PASSWORD,
)
from app.services.demo_scenarios.speed_to_lead import SpeedToLead
from app.services.demo_scenarios.brand_sales import BrandSalesCycle

_REGISTRY: Dict[str, Scenario] = {}


def register(scenario: Scenario) -> None:
    if scenario.key in _REGISTRY:
        raise ValueError("Duplicate scenario key %r" % scenario.key)
    _REGISTRY[scenario.key] = scenario


register(CustomerReactivation())
register(SpeedToLead())
register(BrandSalesCycle())


def get(key: str) -> Optional[Scenario]:
    return _REGISTRY.get((key or "").strip())


def all_scenarios() -> List[Scenario]:
    return list(_REGISTRY.values())


def catalogue() -> List[dict]:
    """What the control panel offers, grouped by product domain so an operator
    is never in doubt which of the two systems they are about to demonstrate."""
    return [s.describe() for s in _REGISTRY.values()]


__all__ = [
    "Scenario", "Step", "DOMAIN_CUSTOMER", "DOMAIN_BRAND",
    "demo_id", "demo_phone", "demo_email", "assert_safe_contact",
    "DEMO_PASSWORD", "register", "get", "all_scenarios", "catalogue",
]
