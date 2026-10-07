# Automatic Agent Relay Setup

## Contract

This file exists so relay workers have a local, branch-readable execution contract.

### Worker contract
- Execute exactly the authorized [RELAY:DIRECTIVE] for the guarded working branch.
- The directive text is authoritative for project scope. If the directive explicitly embeds or supersedes an issue scope, do not require separate issue access.
- Continue through ordinary engineering, test, dependency, and environment problems. A blocked item is recorded and skipped when other safe work remains.
- Stop only for a true approval gate: production launch/promotion; real customer messaging without Mike's GO; unapproved spending; destructive or irreversible production/data/infrastructure changes; legal/carrier attestations; or credentials/secrets Mike must personally enter.
- Never push to main/master. Commit and push only to the guarded working branch.
- Never touch Restland unless the directive explicitly authorizes it.
- Never use production customer data as test fixtures.
- Never print, log, commit, or post secrets.
- Prefer synthetic/test data and local/test databases when deployed staging is unavailable.
- A claim is not complete without evidence. Classify proof honestly as synthetic logic, database integration, deployed authenticated, or live provider.
- Do not call a unit/integration test live-provider proof.
- Commit valuable work at safe checkpoints; do not leave finished work only in an ephemeral runner.
- Before terminal status, report exact commits, tests/counts, proof level, blockers, approval gates, production impact, and the next safe executable action.
- If safe work remains, recommend/continue another executable task rather than 'wait for Mike'.

### Do-not-touch defaults
Unless a directive explicitly crosses an approved gate, do not:
- merge/push main or master;
- merge relay-signal PR #3;
- mutate production records;
- message real customers;
- purchase/provision paid provider resources;
- change live Twilio/A2P/carrier configuration;
- submit legal/carrier attestations;
- alter Restland;
- expose secrets;
- delete audit history.

### Evidence standard
For each important capability, distinguish:
- SYNTHETIC LOGIC
- DATABASE INTEGRATION
- DEPLOYED AUTHENTICATED
- LIVE PROVIDER

No stronger claim may be made than the evidence supports.
