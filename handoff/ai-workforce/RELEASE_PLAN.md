# AI Workforce — Release Plan (safe test mode)

Branch `feature/ai-workforce-launch` · tracking #5 · shared-file notes on #239
Written 2026-10-21 (America/Chicago). Nothing in this plan has been done in production yet.

## The three states — never mix them up

| State | What it means | How it is turned on | Who decides |
|---|---|---|---|
| **1. Customer screen deployed** | The new single **AI Workforce** page (Set Up / Working / Needs You / Results) is live. With nothing else switched on, every job card says honestly why it can't be set up, and Run test says "Test mode is not turned on for your account yet." | Merge the PR → Render auto-deploys backend + frontend. | Owner approves the PR. |
| **2. Safe test mode available** | Chosen brands/accounts can set up an AI worker, press **Run test**, and **Start working** on pretend leads. Nothing is ever sent to a real person; real leads are refused in test mode. | (a) worker cron created, (b) brand offers the job + the account has it on its plan, (c) platform / brand / account scopes set to `simulation` for exactly those tenants. | Owner approves the worker cost and names the tenants. |
| **3. Real customer messages** | The AI worker texts / emails real leads. | Separate, later decision: CONTROLLED stage by an operator, `AI_OPERATIONS_LIVE_SEND`, carrier/A2P, per-tenant cohort and caps. | **Not part of this release.** Requires its own explicit approval. |

## Step-by-step

### State 1 — UI deployed (no cost, no behaviour change)
1. Merge the PR from `feature/ai-workforce-launch` into `main` (after review + green CI).
2. Render deploys `advisorflow-backend` and `advisorflow-frontend` automatically.
3. Verify live:
   - Sidebar shows one **AI Workforce** entry; `/ai-team` and `/ai-workforce-command` land on the matching tab.
   - `GET /ai-workforce/home` as a normal customer returns the four cards; Lead Qualifier shows "Not available" / "Not on your plan" (no tenant is offered or allowed yet).
   - God → Workforce → Health shows **QUEUE WORKER: NOT RUNNING** (true: there is no worker yet).
   - No schema migration is needed: no new tables or columns. Practice appointments use the existing `booking_links.status` text column with the new value `practice`.

### State 2 — safe test mode for chosen tenants
1. **Create the queue worker cron** (owner approval — it costs money; see cost below). Add to `render.yaml` (shared file → post on #239 first) or create in the Render dashboard:
   ```yaml
   - type: cron
     name: advisorflow-ai-workforce-worker
     runtime: python
     schedule: "*/5 * * * *"
     buildCommand: "pip install -r requirements.txt"
     startCommand: "python app/jobs/run_ai_workforce_worker.py"
     envVars:
       - key: SERVICE_ROLE
         value: job
       - key: AI_OPERATIONS_ENABLED
         value: "1"
       - key: DATABASE_URL
         fromDatabase: { name: advisorflow-db, property: connectionString }
       - key: JWT_SECRET
         sync: false   # same value as advisorflow-backend
       - key: ENCRYPTION_KEY
         sync: false   # same value as advisorflow-backend
   ```
   Do **not** set `AI_OPERATIONS_LIVE_SEND`. Without it every channel resolves to the simulated adapter.
2. **Health check:** within 10 minutes God → Workforce → Health shows **QUEUE WORKER: RUNNING** with a recent "last good pass". (It reads the worker's own `job_runs` rows under `ai_workforce_worker`.)
3. **Offer the job to a brand** (God → AI Deployment): enable Lead Qualifier (and Appointment Setter) for the brand and set its terms.
4. **Put it on the account's plan** (catalogue item / add-on, existing billing flow).
5. **Allow test mode** for exactly the chosen brand and account (God → Workforce → Activation, or `PUT /god/workforce/activation`, a reason is required):
   - platform scope → `simulation` (reaches nobody; every tenant without its own row stays `off`)
   - brand scope → `simulation`
   - customer (account) scope → `simulation`
6. **Live smoke test** with a correctly entitled, non-God customer admin of that account: AI Workforce → Lead Qualifier → Set this up → answer the questions → **Run test** shows PASS → **Start working** → Working tab shows the practice lead → Pause / Resume / Stop. Then repeat for Appointment Setter. Capture screenshots. Only after this passes is state 2 "verified live".

### State 3 — not in this release
Live sending needs, at minimum: an operator moving the deployment to CONTROLLED with a cohort cap; `AI_OPERATIONS_LIVE_SEND`; working texting numbers and A2P registration; the inbound reply router waking AI work items (see "Known gaps"). Each is a separate owner decision.

## Operating cost (worker cron, from Render's published prices on 2026-10-21)
Render cron jobs: **$1/month per cron service** plus run time billed per second.
Every 5 minutes = 8,640 runs/month. At roughly one billed minute per run (start-up + pass):

| Instance | Price | Estimated monthly |
|---|---|---|
| Starter (512 MB) | $0.00016/min | ≈ $1.38 + $1 = **≈ $2.40/month** |
| Standard (2 GB) | $0.00058/min | ≈ $5.01 + $1 = **≈ $6/month** |

AI model cost: none by default. The engine uses its built-in rules planner unless BOTH `AI_WORKFORCE_LLM_ENABLED` and `OPENAI_API_KEY` are set on the service; then each run records tokens and estimated cost (`ai_employee_runs.estimated_cost_usd`).

## Emergency stop
- **Everything, instantly, no database needed:** set `AI_WORKFORCE_KILL=1` on the worker cron (and backend). Every activation resolves to off; the worker exits; Run test shows "All AI work is stopped for safety right now."
- **One brand or account:** God → Workforce → Activation → kill switch at that scope (also pauses its queued work).
- **One AI worker:** the customer's own Pause or Stop buttons.

## Rollback
1. Suspend or delete the `advisorflow-ai-workforce-worker` cron (no more passes).
2. Set the platform activation scope back to `off` (every tenant returns to off).
3. If the UI must go back: revert the merge commit on `main` and redeploy. No data migration to undo; practice records are `is_test` leads with `source = ai_workforce_practice` and can be left (they are hidden from every CRM list and report) or removed later by an approved cleanup.

## Verified so far (local, synthetic data only)
- `python scripts/ai_workforce_local_acceptance.py --fresh` → all steps pass (one command; refuses any non-local database).
- `pytest tests/test_ai_workforce_test_mode.py tests/test_ai_workforce_test_mode_api.py tests/test_ai_workforce_worker.py` and the full `tests/test_ai_*.py` suite.
- Headless-browser walkthrough at 1366px and 390px: owner completes the journey with PASS; an advisor sees "Ask your administrator to set this up".

## Known gaps (not blockers for state 2)
- **Real replies don't wake waiting AI work early.** The inbound reply path does not yet set a waiting item due; a waiting item is picked up at its next follow-up time (default 48 hours). Practice runs simulate the wake-up. Must be fixed before state 3.
- Follow-Up Specialist and Customer Care show "Coming soon" until each has its own end-to-end acceptance test on the real worker.
