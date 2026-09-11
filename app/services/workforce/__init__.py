"""THE ADVISORFLOW AI WORKFORCE ENGINE.

ONE ENGINE. Every AI employee — receptionist, qualifier, appointment setter,
reactivation specialist, sales assistant, follow-up, scheduling coordinator,
customer care, support, manager, prospector — is the SAME code running a
different template. There is no per-role agent module here and there must
never be one: eleven agents is eleven places for an authority check to be
forgotten, and the eleventh is the one that leaks.

READ THESE IN THIS ORDER if you are new to it:

    constants.py    the vocabulary: states, stages, channels, refusal codes
    registry.py     the job library and the TOOL REGISTRY, both in code
    activation.py   how far anything is allowed to go, and the kill switch
    policy.py       which tools and channels THIS employee holds
    eligibility.py  may this person be contacted, on this channel, right now
    queue.py        the work-item state machine, claiming and idempotency
    tools.py        THE GATEWAY. Nothing reaches a record except through here.
    tool_impls.py   the registered tools themselves
    model_router.py provider-neutral capability routing + the fake providers
    runtime.py      the bounded run loop
    memory.py       scoped context assembly and untrusted-content isolation
    handoff.py      giving the work to a person, with everything they need
    performance.py  the outcome ledger
    supervisor.py   what an operator needs to see
    profiles.py     the two synthetic proof configurations
    simulator.py    scenarios driven through the real engine
    evaluation.py   the repeatable harness and its measurable results

THE SECURITY ARGUMENT, IN ONE PARAGRAPH. A language model in this system is a
SUGGESTION ENGINE and nothing else. It proposes a tool and arguments; every
authority question is then answered from the database by `tools.authorize`,
which re-checks tenancy, employee status, pause and kill state, activation
stage, brand policy, customer entitlement, channel policy, contact
eligibility, argument schema, record ownership, rate and cost budgets, and
idempotency — in that order, with the cheapest and most absolute first. A
prompt cannot grant a tool. A lead's reply cannot grant a tool. A knowledge
article cannot grant a tool. The model asking nicely is not authorization.
"""
