"""AI WORKFORCE OPERATIONS — the controlled reach of the workforce.

WHAT THIS PACKAGE IS. T6 (app/services/workforce) decides WHO an AI employee
is, WHAT it is allowed to do and WHETHER it may run at all. This package is
how an authorized employee actually REACHES the world: it turns "send this
person a message" into a provider call, a state transition, an audit row and
a scheduled next action — and refuses to, loudly and explainably, whenever
any gate says no.

WHAT THIS PACKAGE IS NOT. Not a chatbot. Not a Twilio feature. Not a campaign
sender. Not a second AI Workforce engine. There is exactly one workforce
engine and it is T6's; everything here CONSUMES its contracts through
`contracts.py` and adds nothing that competes with them.

THE ONE-SENTENCE SHAPE. An operation is requested by name, the gateway says
allowed or denied, eligibility says ALLOW / DENY / REQUIRES_REVIEW, the state
machine says this transition is legal, idempotency says this has not already
happened, the budget says there is room, an ADAPTER executes it, and the audit
records every one of those answers whether the action happened or not.

READ THESE IN THIS ORDER:

    constants.py      the operational vocabulary — comm states, outcomes,
                      stop codes, refusal codes, correlation kinds
    flags.py          the dark-launch switches. Everything defaults OFF.
    contracts.py      THE T6 BOUNDARY. Every workforce concept enters here
                      and nowhere else.
    app/models/ai_operations_models.py
                      the durable objects: threads, communications, inbound
                      events, scheduled actions, ownership, actions, audit,
                      counters
    eligibility.py    may this person be contacted, on this channel, now
    comm_state.py     the communication state machine
    idempotency.py    the same thing must not happen twice
    budget.py         cost and runaway ceilings
    audit.py          who did what, under which authority, and what happened
    channels/         provider-neutral adapters. SMS, email, voice, simulated.
    continuity.py     one objective, one history, across channels
    orchestrator.py   THE OPERATIONS GATEWAY. Nothing reaches a provider
                      except through here.
    inbound.py        routing an incoming message to the right owner, or
                      failing safe
    handoff.py        giving the work to a person, with everything they need
    appointments.py   booking through the platform's own calendar authority
    followup.py       the next action, re-evaluated at execution time
    stop.py           every reason to stop, and the controls that do it
    supervisor_feed.py what an operator (and later T9) needs to see
    profiles.py       synthetic proving configurations — no real customers
    simulator.py      whole lifecycles driven through the real code
    evaluation.py     the adversarial harness and its measurable results

THE SAFETY ARGUMENT, IN ONE PARAGRAPH. No outbound communication happens
because a model asked for it. Every consequential operation is a registered
capability with an authority profile; the orchestrator answers authority from
the database, eligibility from the platform's own compliance authority, and
executability from the activation stage — in that order, cheapest and most
absolute first. A prompt is not a permission. A lead's reply is not a
permission. A knowledge article is not a permission. In this build the
executing adapters are not even reachable: `flags.py` resolves every
organization to the simulated provider unless an operator has explicitly
enabled live execution, and live autonomous outbound voice has no enabling
path at all.
"""
