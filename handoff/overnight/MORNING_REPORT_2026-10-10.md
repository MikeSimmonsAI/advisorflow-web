# Overnight morning report - Oct 10, 2026

## Stall (05:31-07:45)
- Root cause: the worker is a Claude cloud chat session, not a local process. It went silent after the 05:31 check-in while retrying Chrome-based screen checks. Chrome looked "frozen" for two reasons found this morning: the staging backend on Render cold-starts (36 s on the first request), leaving pages on "Loading…"; and a blocking browser dialog (window.confirm) freezes the page for the Chrome extension with 0% CPU until it is dismissed.
- Watchdog exit code 1 was the watchdog's own STALE signal, not a worker crash.
- Nothing was lost: every branch was clean and pushed (sci-program 1345e69f, release/sci-lead-workflow 4557eab3, release/wholesale-nightly 9ea6843a). The merge audit was not repeated.

## Done after recovery
- SCI visual acceptance on staging: 15 screens x 5 widths (1920/1600/1280/768/390), 75/75 with no sideways scroll, no error states and no stuck loading. Test-record send controls confirmed disabled.
- SCI production-readiness pack: SCI_PRODUCTION_READINESS_2026-10-10.md (fast-forward candidate, no migrations, rollback = redeploy 90485585).
- Wholesale on 9ea6843a: E2E workflow, journey, cross-tenant, guards and seller-SMS suites 91/91 passed (on top of 533/533 from the trial merge). Still blocked only by Mike items: dedicated Wholesale staging, skip-trace vendor, Twilio/A2P, GO before any real seller message.
- GitHub relay handoff watchdog: it had re-signalled the finished Oct 8 run relay-restart-20261008-0858 nine times over two days. Fixed on relay/heartbeat-stall b9007659 (6 h max age, 2 retries per run, skip once a newer run exists; 41 relay tests pass). The workflow runs from main, so the fix takes effect only when main gets it.
- Stall handling: watchdog now writes recovery.json (resume point), releases a dead lease, and the three watchdog routines (:03, :23, :43 each hour) perform recovery instead of only reporting. Isolated stall-recovery test: 10/10 checks passed; the live watchdog printed RECOVERED after recovery.

## Needs Mike (approval gates)
1. Merge release/sci-lead-workflow into main (production deploy).
2. Merge relay/heartbeat-stall (or just scripts/relay/handoff_watchdog.py + the workflow file) into main to stop the obsolete relay re-signals.
3. Wholesale gates listed above.
