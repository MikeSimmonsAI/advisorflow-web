/* VOLT Operations → Development (#571).
 *
 * For VOLT's two authorized people only (the server decides; this screen just
 * shows what the server allows). Submit a change, watch it move from Queued to
 * Deployed, approve the exact tested commit for production, retry, cancel or
 * roll back, and make a personal access key for a ChatGPT action.
 *
 * Every status shown here was reported by the GitHub runner after it actually
 * happened (tests run, PR opened, merge done, the deployed commit seen running).
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, API_BASE } from '../../api/client'
import { Modal } from './VoltDialogs'
import { fmtDate } from './voltApi'

// Each step is shown done only when its proof arrived: "Dispatched" = GitHub
// accepted the start request; "Running" = the runner claimed the job; "Deployed"
// = production was seen running the merged commit.
const STEPS = [['queued', 'Submitted'], ['dispatched', 'Dispatched'], ['claude_working', 'Running'], ['coding_completed', 'Testing'],
  ['ready_to_test', 'Ready to review'], ['merge_requested', 'Approved'], ['merged', 'Merged'], ['deployed', 'Deployed & verified']]
const ORDER = STEPS.map(([k]) => k)
function stepIndex(job) {
  if (job.status === 'queued') return job.dispatch?.state === 'dispatched' ? 1 : 0
  return ORDER.indexOf(job.status)
}
const BAD = { tests_failed: 'Tests failed', failed: 'Failed', canceled: 'Canceled' }

// Refresh while the page is in front; pause while it is hidden, and catch up
// the moment it is shown again. No refresh storms from background tabs.
function useVisiblePoll(fn, ms) {
  useEffect(() => {
    fn()
    const t = setInterval(() => { if (!document.hidden) fn() }, ms)
    const v = () => { if (!document.hidden) fn() }
    document.addEventListener('visibilitychange', v)
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', v) }
  }, [fn, ms])
}
const VIA = { api: 'ChatGPT (action)', chatgpt: 'ChatGPT', console: 'VOLT Operations' }
const stageTag = (p) => (p?.stage === 'Deployed and verified' || p?.stage === 'No runtime deployment needed' ? 'v-tag--ok'
  : ['Failed', 'Blocked', 'Canceled'].includes(p?.stage) ? '' : 'v-tag--black')

function Review({ orgId, job }) {
  const [rv, setRv] = useState(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState(false)
  async function show() {
    setOpen(true)
    if (rv) return
    try { setRv(await api.get(`/volt/dev/${orgId}/jobs/${job.ref}/review`)) } catch (e) { setErr(msg(e)) }
  }
  if (!job.pr_number) return null
  return (
    <div data-dev-review>
      {!open && <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={show} data-show-change>Show the change</button>}
      {open && !rv && !err && <p className="v-small v-muted">Loading the change…</p>}
      {err && <div className="v-alert v-alert--err" role="alert">{err}</div>}
      {rv && (rv.review.available ? (
        <div>
          <p className="v-small">Tested commit <code>{(rv.tested_commit || '').slice(0, 12)}</code> · {rv.review.files.length} file(s)</p>
          {rv.review.files.map((f) => (
            <details key={f.file} className="v-small"><summary><code>{f.file}</code> ({f.status}, +{f.additions} −{f.deletions})</summary>
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, maxHeight: 360, overflow: 'auto', background: 'rgba(0,0,0,.04)', padding: 8 }}>{f.diff || '(no text diff)'}{f.diff_truncated ? '\n… (shortened - full diff on the pull request)' : ''}</pre>
            </details>))}
        </div>) : <p className="v-small v-muted">{rv.review.detail}</p>)}
    </div>
  )
}

const msg = (e) => e?.detail?.message || (typeof e?.detail === 'string' ? e.detail : null) || e?.message || 'Something went wrong.'
const newKey = () => (window.crypto?.randomUUID ? window.crypto.randomUUID() : String(Date.now()) + Math.random())

function Steps({ job }) {
  const at = stepIndex(job)
  const bad = BAD[job.status]
  return (
    <ol className="v-ma-steps v-dev-steps" aria-label={`Progress of ${job.ref}`}>
      {STEPS.map(([k, label], i) => {
        const done = at > i || job.status === 'deployed'
        const now = at === i && job.status !== 'deployed'
        return <li key={k} className={`v-ma-step${done ? ' done' : ''}${now ? ' now' : ''}`} aria-current={now ? 'step' : undefined}>{label}</li>
      })}
      {bad && <li className="v-ma-step now" data-bad>{bad}</li>}
    </ol>
  )
}

function JobDrawer({ orgId, job: initial, onClose, onChanged }) {
  const [job, setJob] = useState({ ...initial, can: { ...initial.can, approve_merge: false } })
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [ask, setAsk] = useState(null)
  const load = useCallback(() => api.get(`/volt/dev/${orgId}/jobs/${initial.ref}`).then(setJob).catch((e) => setErr(msg(e))), [orgId, initial.ref])
  useVisiblePoll(load, 15000)
  useEffect(() => { const k = (e) => { if (e.key === 'Escape' && !document.querySelector('.v-modal-bg')) onClose() }; document.addEventListener('keydown', k); return () => document.removeEventListener('keydown', k) }, [onClose])
  async function act(action) {
    if (busy) return
    setBusy(action); setErr('')
    try { const j = await api.post(`/volt/dev/${orgId}/jobs/${job.ref}/${action}`, action === 'approve-merge' ? { tested_commit: job.head_sha } : {}); setJob(j); setAsk(null); onChanged() } catch (e) { setErr(msg(e)); setAsk(null) } finally { setBusy('') }
  }
  const COPY = {
    'approve-merge': ['Approve for production?', `Release ${job.ref} at tested commit ${(job.head_sha || '').slice(0, 12)} (${job.tests_summary || 'tests passed'}). It is merged into main and Render deploys it. Only this exact commit can be merged; a newer or untested one is refused.`],
    rollback: ['Roll back this change?', `Create a rollback job that reverts ${job.ref}. It is tested and then needs approval like any change.`],
    cancel: ['Cancel this job?', 'It will not be worked on or merged. You can retry it later.'],
    retry: ['Run it again?', 'Claude starts over on a fresh branch.'],
  }
  return (
    <div className="v-drawer-bg" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <aside className="v-drawer" role="dialog" aria-modal="true" aria-labelledby="v-dev-h" data-dev-drawer={job.ref}>
        <div className="v-row"><span className="v-mono">{job.ref} · {job.kind === 'revert' ? 'Rollback' : 'Change'} · attempt {job.attempt}</span>
          <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={onClose} autoFocus>Close ✕</button></div>
        <h2 id="v-dev-h" style={{ fontSize: 24, letterSpacing: '-.03em', margin: '10px 0 4px' }}>{job.title}</h2>
        <p className="v-small v-muted">Requested by {job.requested_by} · {fmtDate(job.created_at)} · via {VIA[job.via] || job.via}</p>
        <section className="v-panel">
          <p><span className={`v-tag ${stageTag(job.progress)}`} data-dev-status>{job.progress?.stage || job.status_label}</span>
            {job.progress?.latest_at && <span className="v-small v-muted"> · updated {fmtDate(job.progress.latest_at)}</span>}</p>
          <Steps job={job} />
          {job.progress?.summary && <p className="v-small" style={{ marginTop: 10 }} data-dev-summary>{job.progress.summary}</p>}
          {job.progress?.blocker && <div className="v-alert v-alert--err" role="status" data-dev-blocker>What is blocking it: {job.progress.blocker}</div>}
          {job.progress?.next_action && <p className="v-small" data-dev-next><b>Next:</b> {job.progress.next_action}</p>}
          {job.release_blocker && <div className="v-alert v-alert--err" role="status">{job.release_blocker}</div>}
          {err && <div className="v-alert v-alert--err" role="alert">{err}</div>}
          <div className="v-row" style={{ justifyContent: 'flex-start', marginTop: 10 }}>
            {job.can.approve_merge && <button type="button" className="v-btn v-btn--red" onClick={() => setAsk('approve-merge')} disabled={!!busy} data-approve>Approve &amp; deploy to production</button>}
            {job.can.retry && <button type="button" className="v-btn v-btn--line" onClick={() => setAsk('retry')} disabled={!!busy} data-retry>Retry</button>}
            {job.can.cancel && <button type="button" className="v-btn v-btn--line" onClick={() => setAsk('cancel')} disabled={!!busy}>Cancel</button>}
            {job.can.rollback && <button type="button" className="v-btn v-btn--black" onClick={() => setAsk('rollback')} disabled={!!busy} data-rollback>Roll back</button>}
            {job.can.resend && (job.dispatch?.state !== 'dispatched' || job.blocked_reason) && <button type="button" className="v-btn v-btn--line" onClick={() => act('dispatch')} disabled={!!busy} data-dispatch>{busy === 'dispatch' ? 'Sending…' : 'Send start request again'}</button>}
          </div>
          {job.dispatch?.at && <p className="v-small v-muted" data-dispatch-info>Last start request: {job.dispatch.state === 'dispatched' ? 'accepted by GitHub' : job.dispatch.state === 'failed' ? `failed (${job.dispatch.error})` : job.dispatch.error} · {fmtDate(job.dispatch.at)}. Accepted means GitHub received it; "Running" appears once the runner actually picks the job up.</p>}
        </section>
        <section className="v-panel">
          <h3>Result</h3>
          {job.tests_summary ? <p className="v-small" data-tests>{job.tests_passed ? '✓ ' : '✗ '}{job.tests_summary}</p> : <p className="v-small v-muted">Tests run after Claude finishes.</p>}
          {job.pr_url && <p className="v-small">Code review: <a href={job.pr_url} target="_blank" rel="noreferrer">pull request #{job.pr_number}</a> (public GitHub page; no sign-in needed to read).</p>}
          {job.preview_url ? <p><a className="v-btn v-btn--line v-btn--sm" href={job.preview_url} target="_blank" rel="noreferrer">Open preview ↗</a></p>
            : <p className="v-small v-muted">No hosted preview environment exists yet; changes are tested automatically, then checked live after deploy.</p>}
          {job.head_sha && <p className="v-small" data-tested-commit>Tested commit <code>{job.head_sha.slice(0, 12)}</code>{job.branch ? <> on <code>{job.branch}</code></> : null}</p>}
          {job.run_url && <p className="v-small">GitHub run: <a href={job.run_url} target="_blank" rel="noreferrer">log ↗</a></p>}
          {job.changed_files?.length > 0 && <details><summary className="v-small">{job.changed_files.length} changed file(s)</summary><ul className="v-small">{job.changed_files.map((f) => <li key={f}><code>{f}</code></li>)}</ul></details>}
          <Review orgId={orgId} job={job} />
          {job.merge_approved_by && <p className="v-small">Approved by {job.merge_approved_by} · {fmtDate(job.merge_requested_at)}</p>}
          {job.merge_sha && <p className="v-small">Merged commit <code>{job.merge_sha.slice(0, 7)}</code>{job.deploy_targets ? ` · deploys: ${job.deploy_targets}` : ''}</p>}
          {job.deploy_detail && <p className="v-small" data-deployed>{job.deploy_detail}</p>}
        </section>
        <section className="v-panel">
          <h3>Request</h3>
          <p className="v-small" style={{ whiteSpace: 'pre-wrap' }}>{job.request}</p>
        </section>
        <section className="v-panel">
          <h3>Timeline</h3>
          <ul className="v-ma-updates" data-dev-timeline>{(job.events || []).map((e, i) => (
            <li key={i} className="v-small"><b>{(e.status && (STEPS.find(([k]) => k === e.status)?.[1] || BAD[e.status])) || e.action.replace(/_/g, ' ')}</b> · {e.actor} · {new Date(e.at).toLocaleString('en-US', { timeZone: 'America/Chicago', timeZoneName: 'short' })}{e.detail ? ` · ${e.detail}` : ''}</li>))}</ul>
          {job.run_url && <p className="v-small"><a href={job.run_url} target="_blank" rel="noreferrer">GitHub run log ↗</a></p>}
        </section>
        {ask && (
          <Modal title={COPY[ask][0]} onClose={busy ? () => {} : () => setAsk(null)} labelId="v-dev-confirm" name="dev-confirm">
            <p>{COPY[ask][1]}</p>
            <div className="v-row" style={{ justifyContent: 'flex-end' }}>
              <button type="button" className="v-btn v-btn--line" onClick={() => setAsk(null)} disabled={!!busy}>Not now</button>
              <button type="button" className="v-btn v-btn--red" onClick={() => act(ask)} disabled={!!busy} data-confirm-go>{busy ? 'Working…' : 'Confirm'}</button>
            </div>
          </Modal>)}
      </aside>
    </div>
  )
}

function Keys({ orgId }) {
  const [items, setItems] = useState(null)
  const [fresh, setFresh] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const load = useCallback(() => api.get(`/volt/dev/${orgId}/tokens`).then((r) => setItems(r.items)).catch((e) => setErr(msg(e))), [orgId])
  useEffect(() => { load() }, [load])
  const schema = `${String(API_BASE).replace(/\/$/, '')}/volt/dev/api/openapi.json`
  async function make() {
    setBusy(true); setErr('')
    try { setFresh(await api.post(`/volt/dev/${orgId}/tokens`, { name: 'ChatGPT' })); await load() } catch (e) { setErr(msg(e)) } finally { setBusy(false) }
  }
  async function revoke(id) { try { await api.delete(`/volt/dev/${orgId}/tokens/${id}`); await load() } catch (e) { setErr(msg(e)) } }
  const base = String(API_BASE).replace(/\/$/, '')
  const mcpUrl = `${base}/volt/dev/mcp`
  const live = (items || []).filter((t) => !t.revoked)
  return (
    <section className="v-panel" data-dev-keys>
      <h3>ChatGPT access</h3>
      <p className="v-small"><b>Connect your own ChatGPT</b> (web, paid plan): go to <b>Plugins</b> (chatgpt.com/plugins) → <b>Add</b> → <b>Add custom MCP server</b>,
        name it <b>VOLT Development</b>, paste this server URL and choose <b>OAuth</b> sign-in:</p>
      <p><code style={{ wordBreak: 'break-all' }} data-mcp-url>{mcpUrl}</code></p>
      <p className="v-small">ChatGPT then sends you to a VOLT page: sign in with <b>your own</b> login and click <b>Allow</b>. Install the plugin, start a new chat,
        type <b>@VOLT Development</b> and ask, e.g. <i>"Submit a VOLT change: …"</i>, <i>"Show my latest VOLT requests"</i>, <i>"What is blocking DEV-3?"</i>. It can submit requests, show status, the diff and test results, retry, and - only after you confirm a specific
        job and tested commit - approve a release. Ordinary chats, shared projects and other people&apos;s chats do not send anything; only this connection does.</p>
      {err && <div className="v-alert v-alert--err" role="alert">{err}</div>}
      <h4 style={{ margin: '12px 0 4px' }}>Your connections</h4>
      {items && live.length === 0 && <p className="v-small v-muted">None yet.</p>}
      {live.length > 0 && <ul className="v-ma-updates" data-dev-connections>{live.map((t) => (
        <li key={t.id} className="v-small">{t.kind === 'oauth' ? 'ChatGPT sign-in' : 'Personal key'} · {t.name} · <code>{t.prefix}…</code> · made {fmtDate(t.created_at)}{t.last_used_at ? ` · last used ${fmtDate(t.last_used_at)}` : ''}
          {' · '}<button type="button" className="v-btn v-btn--link" onClick={() => revoke(t.id)} data-revoke>{t.kind === 'oauth' ? 'Disconnect' : 'Revoke'}</button></li>))}</ul>}
      <details className="v-small" style={{ marginTop: 10 }}>
        <summary>Older custom GPT setup (custom GPTs stop working on Dec 11, 2026)</summary>
        <p>A custom GPT Action can import <code>{schema}</code>. Use a personal key only in a GPT that you alone use - everyone using a GPT acts with its key.</p>
        {fresh && <div className="v-alert v-alert--info" role="status" data-new-key>Your new key (shown once - copy it now): <code style={{ wordBreak: 'break-all' }}>{fresh.token}</code></div>}
        <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={make} disabled={busy}>{busy ? 'Creating…' : 'Create a personal key'}</button>
      </details>
    </section>
  )
}

export default function Development({ orgId }) {
  const [sp, setSp] = useSearchParams()
  const [jobs, setJobs] = useState(null)
  const [err, setErr] = useState('')
  const [v, setV] = useState({ title: '', request: '' })
  const [idem, setIdem] = useState(newKey)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const load = useCallback(() => api.get(`/volt/dev/${orgId}/jobs`).then((r) => setJobs(r.items)).catch((e) => setErr(msg(e))), [orgId])
  useVisiblePoll(load, 20000)
  const open = sp.get('job')
  const openJob = useMemo(() => (jobs || []).find((j) => String(j.number) === String(open) || j.ref === open), [jobs, open])
  async function submit(e) {
    e.preventDefault()
    if (busy) return
    setBusy(true); setErr(''); setNote('')
    try {
      const j = await api.post(`/volt/dev/${orgId}/jobs`, { ...v, idempotency_key: idem })
      setV({ title: '', request: '' }); setIdem(newKey())
      setNote(j.dispatch?.state === 'dispatched'
        ? `${j.ref} submitted, and GitHub accepted the start request. It shows "Running" once the runner picks it up (usually within a minute or two).`
        : `${j.ref} submitted. ${j.dispatch?.error || 'Waiting for the runner.'}`)
      await load()
    } catch (x) { setErr(msg(x)) } finally { setBusy(false) }
  }
  return (
    <>
      <div className="v-row"><div><h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>Development</h2>
        <p className="v-muted">Ask for a VOLT Marketing change, watch it, approve it for production. Only VOLT's two authorized people see this.</p></div></div>
      {err && <div className="v-alert v-alert--err" role="alert">{err}</div>}
      <form className="v-panel v-form" onSubmit={submit} data-dev-form>
        <h3>Request a change</h3>
        <label>Title<input value={v.title} onChange={(e) => setV({ ...v, title: e.target.value })} maxLength={140} required data-dev-title /></label>
        <label>What should change?<textarea rows={5} value={v.request} onChange={(e) => setV({ ...v, request: e.target.value })} maxLength={8000} required data-dev-request
          placeholder="Describe the change in VOLT Marketing: which page, what it should say or do, anything to keep the same." /></label>
        <p className="v-small v-muted">Claude may change VOLT Marketing files only. Anything else is refused automatically.</p>
        <button className="v-btn v-btn--red" type="submit" disabled={busy || !v.title.trim() || v.request.trim().length < 10} data-dev-submit>{busy ? 'Sending…' : 'Send to Claude'}</button>
        {note && <div className="v-alert v-alert--info" role="status" data-dev-note>{note}</div>}
      </form>
      <section className="v-panel">
        <h3>Jobs</h3>
        {jobs === null ? <p className="v-mono">Loading…</p> : jobs.length === 0 ? <p className="v-muted v-empty">No requests yet.</p> : (
          <div className="v-scroll"><table className="v-table v-table--cards" data-table="dev">
            <thead><tr><th>Job</th><th>Status</th><th>Requested</th><th><span className="v-sr">Open</span></th></tr></thead>
            <tbody>{jobs.map((j) => (
              <tr key={j.id} data-dev-job={j.ref}>
                <td data-label="Job"><b>{j.ref}</b> {j.title}{j.kind === 'revert' ? ' (rollback)' : ''}</td>
                <td data-label="Status"><span className={`v-tag ${stageTag(j.progress)}`}>{j.progress?.stage || j.status_label}</span>
                  {j.progress?.latest_at && <><br /><span className="v-small v-muted">{fmtDate(j.progress.latest_at)}</span></>}</td>
                <td data-label="Requested">{j.requested_by}<br /><span className="v-small v-muted">{fmtDate(j.created_at)}</span></td>
                <td data-label="Open"><button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setSp({ job: String(j.number) })}>Open →</button></td>
              </tr>))}</tbody></table></div>)}
      </section>
      <Keys orgId={orgId} />
      {openJob && <JobDrawer orgId={orgId} job={openJob} onClose={() => setSp({})} onChanged={load} />}
    </>
  )
}
