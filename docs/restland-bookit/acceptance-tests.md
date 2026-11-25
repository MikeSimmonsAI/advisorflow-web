# Acceptance tests

Automated (`tests/test_restland_bookit.py`, synthetic data):
* **A** each advisor sees only their own (404 on others, owner-only preview, suspension).
* **D** Evo 6 Block: one parent per review, six independent contacts, duplicate detection without
  revealing whose, independent eligibility/enrollment, origin kept.
* **F** ADD Lead: idempotent intake, auto-assignment, notifications, private to the assignee,
  resolution required, ADD network attribution.
* **G** 2 × $4,500 + $1,800 − $500 → gross $10,800, net $10,300 (exact cents).
* **H** repeated submission → one sale. **I** pending isn't revenue, closes later with the
  original appointment. **J** correction → dashboards + audit (old/new/reason).
* Close Ticket rules; service requests need a resolution.
* Board → sheet → app road: `tests/test_restland_team_board.py`.

Manual / in production (owner preview): B (existing booking visible), C (follow-up → appointment),
E (campaign transitions — blocked until a template is approved), K (Sheets outage), L (responsive
screens), M (existing campaign regression — no changes to campaign code).
