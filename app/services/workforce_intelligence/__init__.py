"""T9 - AI WORKFORCE SUPERVISOR, INTELLIGENCE & OPTIMIZATION.

The management, quality, exception and optimization layer ABOVE the AI
Workforce. It answers the eleven questions a person managing AI employees
actually has - what are they doing, what are they producing, what is waiting,
what is failing, where do humans need to intervene, what is costing money,
which employees are performing, which need attention, what changed, why it
matters, and what to do next.

WHAT THIS PACKAGE IS NOT. Not another engine (T6 owns that). Not another
communications layer (T7). Not another deployment or billing system (T8, T2).
Not the Executive Command Center (T10) - it exposes the CONTRACT T10 will read
and builds none of T10's screens. And not a second root: God Mode is the only
root authority in this platform, and nothing here creates another.

THE ONE RULE THAT SHAPES EVERY MODULE. T9 consumes authoritative records and
produces authorized intelligence. It may explain evidence. It may never create
evidence, and it may never act on its own authority - every consequential
change goes back through T6, T7 or T8's existing control path, with that
path's own checks and that path's own audit.

MODULE MAP

    constants       the management vocabulary, defined once
    scope           the one place T9 decides what a caller may see
    collect         authoritative reads from T6/T7/T8, scoped, never widened
    metrics         defensible counts and rates; UNKNOWN is never ZERO
    scorecards      explainable per-employee performance, like against like
    stalled         deterministic detection of work that is not progressing
    quality         outcome-based evaluation; never grades tone
    cost            usage, runaway conditions, and honest unknown cost
    attention       the Needs Your Attention queue, deduplicated to roots
    findings        Supervisor Intelligence - evidence-backed observations
    coaching        recommendations, which are never executions
    incidents       the exception view, and the handoff to T5 Support
    reconciliation  contradictions between systems, surfaced not repaired
    review          the human review experience and its audited decisions
    actions         management actions, each delegated to its owning system
    readmodel       materialisation, freshness, and tenant-keyed caching
    command         the Workforce Command Center payload
    executive       the stable read contract T10 consumes
    observability   whether T9 itself ran
    proof           synthetic proof of the whole management lifecycle
"""
