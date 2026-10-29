# Wholesale — developer notes

## Binding business rules
- **[Foreclosure Work-Priority Policy](FORECLOSURE_PRIORITY_POLICY.md)** (2026-10-26).
  - Verified imminent auctions are worked first, whatever the investment evidence score.
  - Enforced by `app/services/evosense/work_priority.py` and protected by the permanent suite
    `tests/test_foreclosure_priority_policy.py`.
  - Read it before changing scoring, Discovery Inbox ordering or buckets, the Foreclosure Command Center,
    lists / the Top 100, hand-offs or the property page.
