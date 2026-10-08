# SCI Toll-Free POC — Independent QA & Launch Checklist

Owner: ChatGPT QA workstream. Branch: chatgpt/sci-qa-readiness. Claude owns implementation on his branch. No production changes from this branch.

## Architecture contract
- Twilio toll-free (844) 917-2171 is the sole SCI SMS and inbound voice number for this POC. Do not purchase additional numbers.
- Each cemetery has its existing local callback number, optional booking URL, planning guide URL, custom voicemail greeting, and notification destination.
- Outbound SMS uses the toll-free sender. Replies map by recipient and program/cemetery to a conversation; STOP suppression always takes precedence.
- Incoming voice never connects to a live person or AI. Known unique contact-to-cemetery mapping -> cemetery-specific greeting and voicemail. Unknown, missing, or ambiguous mapping -> neutral greeting and voicemail; never infer solely from area code.
- Persist voicemail metadata, recording reference, contact/cemetery association where known, and callback task. Protect access to recordings and retention.
- Keep toll-free verification rules separate from 10DLC A2P campaign rules. Confirm actual toll-free approval scope for promotional content, embedded URLs and local phone numbers before live sends.
- Existing SCI list: do not mark all contacts ineligible merely because their consent originated on a general form. Reconcile available evidence and preserve opt-outs; no silent bypass of applicable safeguards.

## Acceptance scenarios
1. Known Alabama contact calls toll-free: correct Alabama cemetery greeting, voicemail, callback notification and contact association.
2. Known Pensacola contact: distinct greeting and correct cemetery association.
3. Unknown caller: neutral greeting, recording, unassigned callback task.
4. One phone associated with two cemeteries: neutral fallback; no incorrect attribution.
5. Caller hangs up before recording: accurate call event, no phantom voicemail.
6. Voicemail storage/notification failure: actionable error and retry/alert; do not lose call silently.
7. Inbound SMS reply: routed to correct SCI conversation, not Wholesale or general.
8. STOP: suppress further messages and record status. HELP: configured response path.
9. Template with cemetery's local number, booking URL and guide URL: verify approval flags before Twilio send.
10. Verify voice webhook signature, idempotency, access controls and safe handling of personally identifiable data.
11. Real-phone test: toll-free voice webhook changed only after tested handler deployed; never point at nonexistent endpoint.
12. Confirm no purchases, production deploys or live customer messages without Mike's GO.

## Handoff criteria
- Report actual branch and commit, automated test results, one real-phone inbound voicemail test per sample location, SMS reply test, and blockers.
- Explicitly distinguish built/tested from deployed/live.
- Do not claim that a passed unit test establishes Twilio live functionality.
