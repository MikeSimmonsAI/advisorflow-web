// Redesign owner-approval check (UI-REDESIGN lane, docs/redesign/RULES.md).
//
// Every PR gets this check. A PR that is NOT a redesign passes immediately, so
// SCI / Atlantis / Wholesale / Roofing / Insurance work is never held up.
// A redesign PR (label `lane:ui-redesign`, or a `redesign/*` branch) passes only
// when it carries the evidence sections AND the `owner-approved` label - and,
// if the repo variable REDESIGN_APPROVER names a separate GitHub account, an
// APPROVED review from that account on the current head commit.
//
// LIMITATION (documented in RULES.md): the label can be applied by anyone with
// write access, including an assistant session that works through the owner's
// own GitHub identity. Only the REDESIGN_APPROVER review is an independently
// authenticated approval.

const REDESIGN_LABEL = 'lane:ui-redesign'
const APPROVED_LABEL = 'owner-approved'
const CHECK_NAME = 'Redesign owner approval'

const SECTIONS = [
  ['Affected screens', /^#{1,4}\s*affected screens?\b/im],
  ['Screenshots', /^#{1,4}\s*(browser\s+)?screenshots?\b/im],
  ['Tests', /^#{1,4}\s*tests?\b/im],
  ['Build', /^#{1,4}\s*build\b/im],
]

function labelsOf(pr) {
  return (pr.labels || []).map(l => (typeof l === 'string' ? l : l.name))
}

function isRedesign(pr) {
  const ref = (pr.head && pr.head.ref) || ''
  return labelsOf(pr).includes(REDESIGN_LABEL) || ref.startsWith('redesign/')
}

/**
 * Pure decision. `reviews` = GitHub review objects; `approver` = login or ''.
 * Returns { ok, title, summary, missing[] }.
 */
function evaluate(pr, reviews = [], approver = '', files = []) {
  if (!isRedesign(pr)) {
    return { ok: true, missing: [], title: 'Not a redesign PR', summary: 'No owner visual approval needed.' }
  }
  const n = pr.number
  const missing = []
  const body = pr.body || ''
  for (const [name, re] of SECTIONS) if (!re.test(body)) missing.push('PR description section: "' + name + '"')
  if (!labelsOf(pr).includes(APPROVED_LABEL)) {
    missing.push('Label `' + APPROVED_LABEL + '` - added only after the owner writes "APPROVE REDESIGN PR #' + n + ' FOR MERGE"')
  }
  if (approver) {
    const headSha = pr.head && pr.head.sha
    const mine = reviews.filter(r => r.user && r.user.login && r.user.login.toLowerCase() === approver.toLowerCase())
    const last = mine[mine.length - 1]
    if (!last || last.state !== 'APPROVED' || (headSha && last.commit_id !== headSha)) {
      missing.push('An APPROVED review from @' + approver + ' on the current head commit')
    }
  }
  // A redesign PR may not edit the gate that judges it.
  const gateFiles = files.filter(f => f.startsWith('.github/'))
  if (gateFiles.length) missing.push('Redesign PRs may not change .github/ (found: ' + gateFiles.join(', ') + ')')
  if (pr.draft) missing.push('PR is still a draft (mark ready only after approval)')
  const ok = missing.length === 0
  return {
    ok, missing,
    title: ok ? 'Owner visual approval on file' : 'Waiting for owner visual approval',
    summary: ok
      ? 'Redesign PR #' + n + ' has its evidence and the owner approval.'
      : 'Redesign PR #' + n + ' cannot merge yet:\n- ' + missing.join('\n- '),
  }
}

/** New commits after approval clear the approval: the owner approved different visuals. */
function shouldClearApproval(eventName, action, pr) {
  return eventName === 'pull_request' && action === 'synchronize' && isRedesign(pr) && labelsOf(pr).includes(APPROVED_LABEL)
}

async function listReviews(github, owner, repo, number) {
  return github.paginate(github.rest.pulls.listReviews, { owner, repo, pull_number: number, per_page: 100 })
}

async function listFiles(github, owner, repo, number) {
  const f = await github.paginate(github.rest.pulls.listFiles, { owner, repo, pull_number: number, per_page: 100 })
  return f.map(x => x.filename)
}

/** Entry point for pull_request / pull_request_review events. */
async function runForEvent({ github, context, core }) {
  const { owner, repo } = context.repo
  let pr = context.payload.pull_request
  const approver = (process.env.REDESIGN_APPROVER || '').trim()
  if (shouldClearApproval(context.eventName, context.payload.action, pr)) {
    await github.rest.issues.removeLabel({ owner, repo, issue_number: pr.number, name: APPROVED_LABEL }).catch(() => {})
    await github.rest.issues.createComment({ owner, repo, issue_number: pr.number,
      body: 'New commits were pushed after owner approval, so `' + APPROVED_LABEL + '` was removed. ' +
            'Approval covers the visuals that were reviewed; this version needs a fresh ' +
            '"APPROVE REDESIGN PR #' + pr.number + ' FOR MERGE".' })
    pr = { ...pr, labels: labelsOf(pr).filter(l => l !== APPROVED_LABEL) }
  }
  const reviews = approver && isRedesign(pr) ? await listReviews(github, owner, repo, pr.number) : []
  const files = isRedesign(pr) ? await listFiles(github, owner, repo, pr.number) : []
  const res = evaluate(pr, reviews, approver, files)
  core.summary.addHeading(res.title, 3).addRaw(res.summary.replace(/\n/g, '<br>')).write()
  if (!res.ok) core.setFailed(res.summary)
  else core.info(res.summary)
}

/** workflow_dispatch: give every open PR a current result (used once when the check becomes required). */
async function backfill({ github, context, core }) {
  const { owner, repo } = context.repo
  const approver = (process.env.REDESIGN_APPROVER || '').trim()
  const prs = await github.paginate(github.rest.pulls.list, { owner, repo, state: 'open', per_page: 100 })
  for (const pr of prs) {
    const reviews = approver && isRedesign(pr) ? await listReviews(github, owner, repo, pr.number) : []
    const files = isRedesign(pr) ? await listFiles(github, owner, repo, pr.number) : []
    const res = evaluate(pr, reviews, approver, files)
    await github.rest.checks.create({ owner, repo, name: CHECK_NAME, head_sha: pr.head.sha,
      status: 'completed', conclusion: res.ok ? 'success' : 'failure',
      output: { title: res.title, summary: res.summary } })
    core.info('#' + pr.number + ' ' + (res.ok ? 'pass' : 'WAIT') + ' - ' + res.title)
  }
}

module.exports = { evaluate, isRedesign, shouldClearApproval, runForEvent, backfill,
                   REDESIGN_LABEL, APPROVED_LABEL, CHECK_NAME, SECTIONS }
