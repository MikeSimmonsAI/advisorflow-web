"""T8 - AI WORKFORCE BUILDER, DEPLOYMENT AND COMMERCIALISATION.

WHAT THIS PACKAGE IS. The layer that turns an engine into a product. T6 built
the workforce engine, T7 gave it controlled operational reach, and T8 is how a
white-label brand OFFERS an AI employee and a customer HIRES, CONFIGURES,
PROVISIONS, ACTIVATES, PAUSES, UPGRADES and REMOVES one.

WHAT THIS PACKAGE IS NOT, and every one of these was a real temptation:

    not a second AI engine          every authority question goes to T6.
    not a second communications     every outward action goes to T7.
    not a prompt builder            no module here composes, stores or reads a
                                    prompt. `constants.FORBIDDEN_CONFIG_*`
                                    refuses the word on the way in.
    not a second billing system     no price, no checkout, no Stripe call, no
                                    entitlement of its own. `commerce` asks T2
                                    and reports.
    not a second root control plane God Mode is root. There is no platform
                                    superadmin, no workforce owner, and no
                                    route here that is not `require_god` or
                                    scoped to the caller's own workspace.

THE MAP

    constants       the vocabulary: two state machines, readiness verdicts,
                    refusal codes, and what a customer may never configure.
    catalog         three views of T6's ONE job registry - platform, brand,
                    customer. No second registry.
    commerce        the T2 boundary. Asks; never writes a commercial row.
    capacity        package and capacity guards. What buying does NOT buy.
    configuration   the guided business interview, and the loop detector.
    readiness       deterministic checks. No model decides readiness.
    lifecycle       the deployment state machine, idempotent provisioning and
                    the conditional updates that survive two writers.
    activation      asks T6 to switch one on. Never opens a gate itself.
    deprovision     stopping without erasing, and the orphan sweep.
    views           the payloads the two routers render.
    simulation      three synthetic lifecycles, end to end, reaching nobody.
    evaluation      the adversarial harness.

THE SAFETY ARGUMENT, IN ONE PARAGRAPH. Nothing this package does can cause an
outward action. It creates AI employees that start at `draft` and `off`; it
asks T6 for a stage and T6 still resolves the minimum across four scopes, of
which the platform scope ships `off`; it reads entitlement rather than granting
it, and no AI employee carries a price, so nothing resolves to entitled for a
real customer in this deployment; and it adds no environment switch of its own,
so the dark-launch assertions T6 and T7 shipped still describe the whole
system.
"""

from app.services.ai_deployment import constants  # noqa: F401

__all__ = ["constants"]
