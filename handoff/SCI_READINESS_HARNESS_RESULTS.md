# SCI readiness harness: executed results

Executed 129 scenarios: **129 PASS, 0 FAIL**. Stdlib only; real decision modules; does not replace dependency-backed pytest or DB integration suites.

| # | Group | Scenario | Result |
|---|---|---|---|
| 1 | pools | pool 205 resolves and has entities | PASS |
| 2 | pools | pool 205 number label round-trips (no location in the number) | PASS |
| 3 | pools | pool 334 resolves and has entities | PASS |
| 4 | pools | pool 334 number label round-trips (no location in the number) | PASS |
| 5 | pools | pool 850 resolves and has entities | PASS |
| 6 | pools | pool 850 number label round-trips (no location in the number) | PASS |
| 7 | pools | pool 251 resolves and has entities | PASS |
| 8 | pools | pool 251 number label round-trips (no location in the number) | PASS |
| 9 | pools | pool 706 resolves and has entities | PASS |
| 10 | pools | pool 706 number label round-trips (no location in the number) | PASS |
| 11 | pools | pool 318 resolves and has entities | PASS |
| 12 | pools | pool 318 number label round-trips (no location in the number) | PASS |
| 13 | pools | exactly six pools, unique ids | PASS |
| 14 | pools | every pooled entity belongs to exactly one pool | PASS |
| 15 | pools | foreign/missing pool label is not a pool | PASS |
| 16 | pools | Pine Crest Cemetery West is verified and in the 251 (Mobile) pool | PASS |
| 17 | pools | Oaklawn is unresolved: no area code, no pool, no sender | PASS |
| 18 | pools | 844 toll-free is overflow only: never a pool, never a sender | PASS |
| 19 | pools | unverified/foreign area code None has no sender (844 not a silent default) | PASS |
| 20 | pools | unverified/foreign area code '' has no sender (844 not a silent default) | PASS |
| 21 | pools | unverified/foreign area code '   ' has no sender (844 not a silent default) | PASS |
| 22 | pools | unverified/foreign area code '999' has no sender (844 not a silent default) | PASS |
| 23 | pools | unverified/foreign area code '214' has no sender (844 not a silent default) | PASS |
| 24 | entities | 30 campuses / 39 entities locked | PASS |
| 25 | entities | entity names are unique (no merging) | PASS |
| 26 | entities | alias slugs are unique and non-empty for all 39 entities | PASS |
| 27 | entities | alias slugs are valid local parts and not reserved | PASS |
| 28 | entities | shared-campus twins keep distinct entity names AND distinct aliases | PASS |
| 29 | entities | Pine Crest Cemetery / Funeral Home share a campus, Cemetery West is its own | PASS |
| 30 | entities | alias casing/apostrophes normalised without changing the entity name | PASS |
| 31 | entities | alias domain extraction | PASS |
| 32 | routing | pool 205: known contact routes to own exact entity | PASS |
| 33 | routing | pool 205: unknown sender/caller goes to regional review, no location guessed | PASS |
| 34 | routing | pool 334: known contact routes to own exact entity | PASS |
| 35 | routing | pool 334: unknown sender/caller goes to regional review, no location guessed | PASS |
| 36 | routing | pool 850: known contact routes to own exact entity | PASS |
| 37 | routing | pool 850: unknown sender/caller goes to regional review, no location guessed | PASS |
| 38 | routing | pool 251: known contact routes to own exact entity | PASS |
| 39 | routing | pool 251: unknown sender/caller goes to regional review, no location guessed | PASS |
| 40 | routing | pool 706: known contact routes to own exact entity | PASS |
| 41 | routing | pool 706: unknown sender/caller goes to regional review, no location guessed | PASS |
| 42 | routing | pool 318: known contact routes to own exact entity | PASS |
| 43 | routing | pool 318: unknown sender/caller goes to regional review, no location guessed | PASS |
| 44 | routing | same-campus twin: funeral-home contact is NOT re-homed to the cemetery | PASS |
| 45 | routing | a pool number never supplies identity (empty-string sender is unknown) | PASS |
| 46 | opt-out | clear opt-out suppresses: 'STOP' | PASS |
| 47 | opt-out | clear opt-out suppresses: 'stop' | PASS |
| 48 | opt-out | clear opt-out suppresses: 'Stop.' | PASS |
| 49 | opt-out | clear opt-out suppresses: 'unsubscribe' | PASS |
| 50 | opt-out | clear opt-out suppresses: 'Please STOP' | PASS |
| 51 | opt-out | clear opt-out suppresses: 'QUIT' | PASS |
| 52 | opt-out | clear opt-out suppresses: 'cancel' | PASS |
| 53 | opt-out | clear opt-out suppresses: 'stopall' | PASS |
| 54 | opt-out | clear opt-out suppresses: 'END' | PASS |
| 55 | opt-out | clear opt-out suppresses: 'OPT OUT' | PASS |
| 56 | opt-out | explicit opt-out phrase suppresses: 'remove me from your list' | PASS |
| 57 | opt-out | explicit opt-out phrase suppresses: 'Please take me off this list' | PASS |
| 58 | opt-out | explicit opt-out phrase suppresses: 'stop texting me' | PASS |
| 59 | opt-out | explicit opt-out phrase suppresses: 'Stop calling me' | PASS |
| 60 | opt-out | explicit opt-out phrase suppresses: 'stop sending these messages' | PASS |
| 61 | opt-out | explicit opt-out phrase suppresses: 'unsubscribe me please' | PASS |
| 62 | opt-out | scheduling/ordinary language does NOT suppress: 'Can I stop by Friday?' | PASS |
| 63 | opt-out | scheduling/ordinary language does NOT suppress: "I'll stop by the office tomorrow" | PASS |
| 64 | opt-out | scheduling/ordinary language does NOT suppress: 'Stop by anytime after 3, we can talk then' | PASS |
| 65 | opt-out | scheduling/ordinary language does NOT suppress: "we can't stop by" | PASS |
| 66 | opt-out | scheduling/ordinary language does NOT suppress: 'I need to cancel my appointment' | PASS |
| 67 | opt-out | scheduling/ordinary language does NOT suppress: 'see you this weekend' | PASS |
| 68 | opt-out | scheduling/ordinary language does NOT suppress: 'Please remove my old address' | PASS |
| 69 | opt-out | empty / None body is not an opt-out | PASS |
| 70 | opt-out | program classifier: 'Can I stop by Tuesday?' is HOT, not OPT-OUT | PASS |
| 71 | opt-out | program classifier: 'STOP' is OPT-OUT, closed, no alert, no draft | PASS |
| 72 | opt-out | program classifier: 'unsubscribe' is OPT-OUT, closed, no alert, no draft | PASS |
| 73 | opt-out | program classifier: 'Stop!' is OPT-OUT, closed, no alert, no draft | PASS |
| 74 | opt-out | program classifier: 'do not text me' is OPT-OUT | PASS |
| 75 | classify | "Yes please, I'm interested" -> hot | PASS |
| 76 | classify | 'How much does a plan cost?' -> hot | PASS |
| 77 | classify | 'Please call me tomorrow' -> hot | PASS |
| 78 | classify | 'Can we schedule a visit?' -> hot | PASS |
| 79 | classify | 'What does the package include?' -> active | PASS |
| 80 | classify | 'Can you send me a brochure' -> active | PASS |
| 81 | classify | 'ok' -> low | PASS |
| 82 | classify | 'Thanks!' -> low | PASS |
| 83 | classify | '' -> low | PASS |
| 84 | classify | bereavement reply is HOT and routed to a personal reply | PASS |
| 85 | classify | upstream 'interested' classification promotes a neutral-looking reply to HOT | PASS |
| 86 | classify | urgency mapping: HOT/ACTIVE high, terminal lanes LOW (never an emergency page) | PASS |
| 87 | classify | intents: pricing + appointment detected, ack for 'ok' | PASS |
| 88 | data | 'This is not him, wrong person' -> wrong_person (held for Data Review, not sold to) | PASS |
| 89 | data | 'you have the wrong number' -> bad_data (held for Data Review, not sold to) | PASS |
| 90 | data | 'who is this?' -> bad_data (held for Data Review, not sold to) | PASS |
| 91 | data | "she doesn't live here" -> wrong_person (held for Data Review, not sold to) | PASS |
| 92 | data | wrong person/bad data raise an in-app alert only (no external) | PASS |
| 93 | data | wrong-person draft acknowledges and promises no further contact | PASS |
| 94 | cadence | any reply pauses an ACTIVE cadence | PASS |
| 95 | cadence | HOT reply pauses an ACTIVE cadence | PASS |
| 96 | cadence | opt-out ENDS an active cadence (stopped_dnc), not just pauses | PASS |
| 97 | cadence | opt-out ENDS an already-paused cadence | PASS |
| 98 | cadence | no cadence: nothing to pause | PASS |
| 99 | cadence | a stopped cadence is never resumed by a reply | PASS |
| 100 | idempotency | retried MessageSid with stored reply -> duplicate, no second pipeline | PASS |
| 101 | idempotency | new MessageSid -> processed | PASS |
| 102 | idempotency | no MessageSid cannot be deduped (processed) | PASS |
| 103 | idempotency | same input classifies identically twice (deterministic) | PASS |
| 104 | alerts | HOT: management + external alert, kind hot | PASS |
| 105 | alerts | ACTIVE: external, no management | PASS |
| 106 | alerts | LOW: in-app only | PASS |
| 107 | alerts | staff SMS/email is OFF by default: attempt refused with reason | PASS |
| 108 | alerts | missing recipient is recorded, never guessed | PASS |
| 109 | alerts | enabled + recipient configured -> allowed | PASS |
| 110 | no-auto-send | suggested reply is a DRAFT string; terminal lanes get none | PASS |
| 111 | no-auto-send | draft never asks the family to call | PASS |
| 112 | console | console check: Synthetic HOT appointment reply is classified HOT | PASS |
| 113 | console | console check: Synthetic ACTIVE information reply is classified ACTIVE | PASS |
| 114 | console | console check: "STOP" is an opt-out and ends the cadence | PASS |
| 115 | console | console check: "Can I stop by Friday?" is NOT an opt-out | PASS |
| 116 | console | console check: Known contact routes to its own location | PASS |
| 117 | console | console check: Unknown sender/caller goes to regional review (all six pools) | PASS |
| 118 | console | console check: Oaklawn refuses/holds: no area code, no pool, no sender | PASS |
| 119 | console | console check: 844 is overflow-only: never a pool or a sender | PASS |
| 120 | console | console check: No AI auto-send: drafts only, decision modules have no send path | PASS |
| 121 | console | console check: Meaningful reply pauses cadence (never resumes a stopped one) | PASS |
| 122 | console | console run_all reports 10 PASS / 0 FAIL, nothing sent, no failed gate | PASS |
| 123 | console | console reports the exact failed gate when a check breaks | PASS |
| 124 | console | verdict: not run = BLOCKED; all pass with external blockers = CONDITIONAL (never READY) | PASS |
| 125 | no-auto-send | decision module has no send/network/DB imports: app/services/programs/reply_rules.py | PASS |
| 126 | no-auto-send | decision module has no send/network/DB imports: app/services/optout_parser.py | PASS |
| 127 | no-auto-send | decision module has no send/network/DB imports: app/services/programs/regional_pools.py | PASS |
| 128 | no-auto-send | decision module has no send/network/DB imports: app/services/programs/alias_rules.py | PASS |
| 129 | no-auto-send | responses.py reply path never calls a customer-send function | PASS |
