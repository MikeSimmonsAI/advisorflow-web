# Notes for assistant sessions working in this repository

## Never push a branch that does not contain the app
Every branch pushed to GitHub is built by the Vercel project `advisorflow-booking`
(Root Directory `booking-app`). A branch without that folder fails with
"The specified Root Directory 'booking-app' does not exist" and posts a failed
Vercel status on the commit. This happened on the evidence-only orphan branches
`evidence/god-mode-redesign`, `qa/439-dashboards` and `qa/439-screenshots`.

- Do not create orphan or evidence-only branches (`git checkout --orphan`,
  screenshot-only branches).
- Put screenshots and QA evidence on the work's own branch, under
  `docs/<area>/<topic>/`, and link them from the PR. Alternatively, attach them
  to a PR comment.
- Do not change the Vercel Root Directory to make such a branch pass:
  `booking-app` is correct, and `main` deploys successfully with it.
