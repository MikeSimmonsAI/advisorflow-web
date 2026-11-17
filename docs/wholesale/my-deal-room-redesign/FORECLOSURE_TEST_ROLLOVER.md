# `test_an_imported_sale_date_is_a_claim_with_provenance`: Central/UTC rollover reproduction

The failure seen on PR #683's CI run at 2026-11-17 04:47 UTC is **not caused by this branch**. It reproduces identically on `main` (2851b07c) and on the PR, depending only on the wall-clock time and the process time zone.

## Why it fails

- The test's expected sale date is computed once, at import time, from the **process-local** date: `SOON = date.today() + timedelta(days=11)`.
- `cohort.foreclosure_status()` counts days from **Central Time** (`work_priority.today_ct()`).
- GitHub runners use UTC. From 6 PM to midnight Central (00:00–06:00 UTC, standard time), the UTC date is one day ahead of the Central date. The sale is then 12 Central days away instead of 11, so `fc["days"] == 11` fails.

## Controlled runs

The clock was pinned with `time-machine` (a pytest plugin started before collection). The test was run on its own: `pytest tests/test_evosense_foreclosure_claims.py -k imported_sale_date_is_a_claim`.

| Instant (UTC) | Central | Process TZ | main 2851b07c | PR #683 |
|---|---|---|---|---|
| 2026-11-16 23:30 | Nov 16 5:30 PM | UTC | pass | pass |
| 2026-11-16 23:30 | Nov 16 5:30 PM | America/Chicago | pass | pass |
| 2026-11-17 00:30 | Nov 16 6:30 PM | UTC | **fail** | **fail** |
| 2026-11-17 00:30 | Nov 16 6:30 PM | America/Chicago | pass | pass |
| 2026-11-17 05:55 | Nov 16 11:55 PM | UTC | **fail** | **fail** |
| 2026-11-17 05:55 | Nov 16 11:55 PM | America/Chicago | pass | pass |
| 2026-11-17 06:05 | Nov 17 12:05 AM | UTC | pass | pass |
| 2026-11-17 06:05 | Nov 17 12:05 AM | America/Chicago | pass | pass |

**Conclusion:** the test is clock-dependent. It fails on UTC machines for the six hours a day when the UTC and Central calendars differ, on `main` and on this branch alike. The fix belongs in the test: compute `SOON` from `work_priority.today_ct()`. That is the same class of fix as the open `audit/650-clock-independent-tests` branch. This PR does not change it.
