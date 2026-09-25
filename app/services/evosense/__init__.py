"""EvoSense Acquisition Engine (Wholesale Phase 7).

Set the strategy. Let EvoSense hunt. Bring the human in when judgment matters.

Modules, in the order data flows through them:

    strategy     what to hunt for (validated, versioned, never deleted)
    providers    provider-neutral capabilities, adapters, health, routing
    ingest       one source record -> observation -> canonical property + owner graph
    identity     property identity resolution (EXACT / PROBABLE / AMBIGUOUS / NEW)
    signals      evidence, freshness, derivation, stacking
    scoring      the three deterministic scores + data confidence
    evaluate     re-score a property and put it in the right inbox bucket
    enrichment   cost-aware contact lookups (decision -> budget -> provider -> ledger)
    budget       atomic paid-data budget
    contacts     owner -> person -> contact point, Contact Confidence
    eligibility  may this contact be worked right now? (DNC / suppression / caps)
    outreach     start working an owner through the EXISTING cadence engine
    conversation read a seller reply: outcome, facts with provenance, intent
    handoff      NEEDS YOU
    economics    preliminary numbers through the EXISTING wholesale analyzer
    promotion    into the EXISTING Wholesale deal, human only
    hunt         one idempotent, bounded run of one strategy
    views        Command Center, Discovery Inbox, Property Intelligence
"""
