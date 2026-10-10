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

## Addendum 08:55 (second pass, same night)
### SCI
- Correction: the merge is NOT schema-free. It adds one column, `lead_outcomes.attendance` (nullable, additive) automatically at startup through app/auto_migrate.py. Approving the merge approves that column on production. Rollback is still safe (older code ignores it). Readiness pack corrected.
- Second, independent visual pass at the exact requested widths 1600 / 1440 / 1280 / 768 / 390: 14 screens x 5 = 70/70 with no sideways scroll. Contact sheets and all 70 images: C:\Dev\af-consent\handoff\screens\overnight-2026-10-10\ (not committed).
- Small visual items (none block the merge):
  1. Launch Readiness checklist still quotes the old suite count "7,745 passed (2026-10-09)"; current candidate is 7,769 / 0.
  2. Phone/tablet width in God mode only: the menu button sits on top of the STAGING bar text and the clock is clipped at 390. Staff in a workspace do not see that bar.
  3. My Work has an empty gap between the header and Exceptions at desktop widths.
  4. Appointments: the small "Family Service Center" label sits tight under the workspace bar.
- Verdict: CONDITIONAL GO. Condition = Mike merges, accepting the one added column.

### AI Workforce
- 11 workforce test files on current main code: 266 passed, 0 failed. Branches feat/ai-workforce-* are already fully in main. Proof level: automated tests with an in-memory database and stubbed model/provider; no live provider run.

### Core platform (Priority 5)
- Not started. Nothing claimed.

### Truth in product
- SCI: built and tested; staging browser-verified; NOT in production; no real customer sends were made.
- Control Room: silence detection + desktop runner card are tested (relay suite) and on a branch; NOT on main, so the live Control Room does not show them yet. Stall detection on the PC IS live and caught 3 real stalls tonight (01:57-04:24, 05:52-07:41) plus the simulated one.
- Wholesale: all Wholesale tests pass (533 + 91); no staging environment, no live provider proof; NO-GO for real sellers until Mike's gates.
- AI Workforce: 266 tests pass; no live provider proof.

### Mike's decisions (complete list)
1. Merge release/sci-lead-workflow to main (includes the lead_outcomes.attendance column).
2. Merge relay/heartbeat-stall to main (Control Room silence detection + fix for the relay re-signalling an old run).
3. Wholesale: staging environment, skip-trace vendor, Twilio/A2P, GO before any seller message, close-requires-contract policy.
4. Close the stale branches fix/relay-stall-detector-v2, fix/relay-active-stall-20261007, fix/relay-watchdog-hardening (superseded).
5. Optional: a GitHub token on the PC if check-ins should also post to relay issue #1 (today they go to the overnight-status branch + chat).
