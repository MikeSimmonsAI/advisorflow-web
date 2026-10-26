# Foreclosure Work-Priority Policy — permanent decision record

**DATE:** October 26, 2026
**Status:** In force. Enforced in code by `app/services/evosense/work_priority.py` and protected by
`tests/test_foreclosure_priority_policy.py` (permanent; do not remove or weaken).

## Owner requirement

All verified imminent foreclosure auctions must be put first in the work order, regardless of incomplete
financial, owner or contact information.

- **AUCTION URGENCY IS NOT INVESTMENT PROFITABILITY.**
- **UNKNOWN FINANCIAL VALUE IS NOT A BAD DEAL.**
- Missing data is not a bad opportunity.
- Low evidence confidence is not low work priority.
- High auction urgency is not guaranteed profit. Investment scores are never raised to make an auction look important.

## Prior incident

The owner reported more than once that imminent November 3 foreclosures appeared LOW because their
investment evidence scores were low (38, 41, 55).

## Previous attempt

Releases #400–#402 separated auction urgency, deal potential and data confidence, but only on the Foreclosure
Command Center and the property page. The owner still saw the wrong overall workflow.

## Root cause (found in production, 2026-10-26)

1. **The Discovery Inbox filed every property by its investment score.**
   - `evaluate.status_for` put any property scoring under its strategy's threshold into the
     `low_opportunity` bucket (shown as "Below Threshold"). The only way out was an open person hand-off.
   - Hand-offs were opened only for a *verified mortgage* notice within 14 days (`opportunity.escalate`).
2. **Tax-foreclosure sales were outside the deadline rule.**
   - 906 properties come from the county tax attorneys' (Linebarger) sale listings. 755 of them sat in
     "Below Threshold".
   - 131 of them have a tax sale posted for **November 3, 2026**: 102 sat in "Below Threshold", 21 in
     "Waiting for data", 5 in "Needs review" and 3 in "High investment evidence". None was escalated.
   - `opportunity.measures` read only mortgage notices.
3. **The default orders used the investment score:**
   - The Inbox sorted by an evidence blend (45% investment score).
   - The Top 100 list sorted by the same blend, and showed an A/B/C letter that put any property without a
     verified owner in "Review".
   - The Foreclosure Command Center ranked by the list rank.
4. **The clock was UTC.** `cohort.foreclosure_status` used the server's UTC date, so after 7 PM Central the
   days-to-sale count was a day short.
5. **The cache wasn't refreshed often enough.** Days-to-sale moves every day, but properties with a sale were
   re-assessed only by the general 500-per-pass backfill.

## Resolution

One shared policy, `work_priority.py`, and every surface reads it:

| Measure | Values |
|---|---|
| Auction urgency | `CRITICAL` (verified sale ≤ 14 days, Central Time) · `UPCOMING` · `URGENT_VERIFY` (claimed / conflicting / not-found date ≤ 14 days) · `VERIFY` · `POSTPONED` / `CANCELED` (recorded with evidence) · `OUTCOME_UNVERIFIED` · `COMPLETED` · `NONE` |
| Deal potential | HIGH · MEDIUM · LOW · UNKNOWN — RESEARCH REQUIRED · INSUFFICIENTLY ASSESSED (no validated model for the type). Supported numbers only. |
| Data confidence | HIGH · MEDIUM · LOW |
| **Work priority** (the primary order) | `CRITICAL — ACT NOW` · `REVIEW REQUIRED — URGENT` · `HIGH — WORK NEXT` · `NORMAL` · `REVIEW REQUIRED` · `CLOSED / INACTIVE` |

**Rules:**
- A verified sale within 14 days is **CRITICAL — ACT NOW**. The only exceptions are an owner who said no or
  asked not to be contacted, and a closed deal.
- An imminent notice that isn't verified (or conflicts) is **REVIEW REQUIRED — URGENT**: a person verifies it.
- The investment evidence score is shown as "Investment evidence score". It **never lowers** a work priority;
  it only breaks ties inside the same priority and sale date.
- Mortgage foreclosures (trustee / HOA / judicial notices — the first-Tuesday trustee sales) and tax sales (a
  delinquent-tax sale posted by the county's tax attorney) are separate auction kinds. Each is labelled.
- **Default order everywhere:** work priority → days to sale → seller already engaged / contact ready / needs
  contact research → investment evidence score → id (deterministic).

**Where it is enforced:**
- **Inbox:** bucket (a CRITICAL or REVIEW_URGENT property is `needs_you`, never "Below threshold"), default
  sort, work-priority filter, Work priority column.
- **Foreclosure Command Center:** default sort, "Critical — act now" quick filter, work-priority filter, column.
- **Top 100 and other lists:** default sort, work-priority filter and column, work queue.
- **Property page:** Work priority tile first.
- **Acquisition Command:** `/wholesale/evosense/command-center` Work first cards, Next opportunities to work table, and Needs You hero all use the authoritative deadline-first work priority. Numerical investment evidence scores remain separate and cannot reorder the urgent work list.
- **Person / VA hand-off queue:** CRITICAL and REVIEW_URGENT properties open one hand-off per sale date; no
  outreach is ever sent.
- **API:** `work` row on inbox, list and foreclosure items, plus `measures.work_priority`.

**Refresh:**
- Every property with a sale on record (mortgage notice, posted tax sale, auction-calendar event) is
  re-assessed at least once per Central Time day, and whenever the policy version changes
  (`opportunity.refresh_auction_properties`, run on every scheduler pass).
- Source evidence, scores and history are never rewritten; only the cached work-priority columns change.

## Acceptance

- **Tests:** permanent regression suite `tests/test_foreclosure_priority_policy.py`, scenarios A–O (plus tax
  sales). Must pass on every release.
- **Production verification:** recorded on GitHub issue #236 (aggregate counts only, no homeowner data).

## Future development rule

No new Wholesale feature, scoring revision, screen redesign or workflow change may violate this policy. If a
change needs `tests/test_foreclosure_priority_policy.py` changed, the change needs the owner's decision first.

## References

- Policy code: `app/services/evosense/work_priority.py`
- Consumers: `opportunity.py` (`measures`, `escalate`, `refresh_auction_properties`), `evaluate.py`
  (`status_for`), `views.py` (inbox sort / filter / `work` row), `cohort.py` (`rows_for` sort / `work`),
  `foreclosure_cycles.py` (sorts, quick filters, work queue)
- Screens: `EvoInbox.jsx`, `EvoCommand.jsx`, `ForeclosureCenter.jsx`, `EvoCohort.jsx`, `OpportunityCard.jsx`, `Measures.jsx`
  (`WorkCell`)
- Tests: `tests/test_foreclosure_priority_policy.py`
