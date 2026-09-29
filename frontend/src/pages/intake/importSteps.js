// Step gating for the guided importer (ImportWizard.jsx), kept free of React
// and of api/ so it can be exercised directly: tests/frontend/importSteps.test.mjs.
//
// THE DEFECT THIS MODULE CLOSES (Step 6 -> Step 7):
//   * The Approve button was enabled only for statuses in a list the wizard
//     kept for itself. Any batch outside it - a commit that failed once, a
//     mapping changed after analysis - rendered Step 6 with a permanently
//     disabled button and no reason, and the server answered 409.
//   * The DEFAULT choice on Step 6 ("Stage everything") reloaded the batch and
//     stayed on Step 6: the primary action on the page never led to Step 7.
//   * Step 6 was reachable whenever an analysis had ever run, even when the
//     batch had since been re-mapped and could not be committed.
// The server now answers batch.commit_gate = { can_commit, reason }; this module prefers that answer and only falls back to the
// status list for an older server.

export const COMMITTABLE = ['ready_for_review', 'staged', 'partially_committed']
export const DONE = ['committing', 'committed', 'partially_committed', 'rolled_back',
                     'partially_rolled_back']

export function isAnalyzed(batch) {
  return !!(batch && (batch.workflow?.analyzed || batch.analysis?.status))
}

/** Where the server says this batch is (1-7). */
export function stepFor(batch) {
  if (!batch) return 1
  if (batch.workflow?.step) return batch.workflow.step
  const s = batch.status
  if (s === 'mapping' || (s === 'failed' && !batch.analysis?.status)) return 2
  if (s === 'processing') return 3
  if (DONE.includes(s)) return 7
  if (s === 'staged') return 6
  return 3
}

/** May Step 6 commit this batch? { ok, reason } - the server's answer first. */
export function commitGate(batch) {
  if (!batch) return { ok: false, reason: 'Loading…' }
  const g = batch.commit_gate || {}
  if (typeof g.can_commit === 'boolean') {
    return { ok: g.can_commit, reason: g.can_commit ? '' : (g.reason || `A '${batch.status}' batch cannot be imported.`) }
  }
  const ok = COMMITTABLE.includes(batch.status)
  return { ok, reason: ok ? '' : `A '${batch.status}' batch cannot be imported. Re-run the analysis first.` }
}

/** The furthest step a user may open for this batch. */
export function reachableStep(batch) {
  if (!batch) return 1
  if (DONE.includes(batch.status) || batch.status === 'staged') return 7
  if (commitGate(batch).ok) return 6
  // A failed / re-mapped batch can still show its last analysis and let the
  // user open Approve to read WHY it cannot be imported yet.
  return isAnalyzed(batch) ? 6 : 2
}

/** Which step to render, from the batch and the ?step= in the URL. */
export function resolveStep(batch, urlStep, hasBatchId) {
  const u = Number(urlStep) || 0
  if (!batch) return u || (hasBatchId ? 0 : 1)
  const server = stepFor(batch)
  // A partially committed batch still has staged rows: Approve stays open so
  // the rest can be imported; otherwise a finished batch shows its results.
  if (server === 7 && !(batch.status === 'partially_committed' && u === 6)) return 7
  if (u >= 1 && u <= reachableStep(batch)) return u
  return server || 1
}

/** Which Step 7 follows a successful POST /commit - including "Keep staged". */
export function stepAfterCommit(/* mode */) {
  return 7
}

/** Is the Approve button pressable right now? */
export function approveEnabled(batch, mode, typed, orgName, busy = false) {
  if (busy) return false
  if (!commitGate(batch).ok) return false
  if (mode === 'stage_only') return true
  const want = String(orgName || '').trim().toLowerCase()
  return !!want && String(typed || '').trim().toLowerCase() === want
}
