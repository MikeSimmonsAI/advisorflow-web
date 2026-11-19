/* CLIENT GROWTH WORKSPACE (/client-growth) - EvoSys Pro platform capability.
 *
 * Opened from its own Program Access card. Every list, count and action comes
 * from /client-growth/*, which re-checks the signed-in person's access on each
 * request (God Mode, the sales program owners, or the client_growth grant).
 * Prospects are the existing EvoSys Pro Sales records; nothing here is a second
 * CRM. Creating or copying a link never sends anything to anybody.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { launcherOrigin } from '../program/launcherOrigin.js'
import '@fontsource/playfair-display/600.css'
import '@fontsource/playfair-display/700.css'
import './clientGrowth.css'

const TABS = [
  ['overview', 'Overview'], ['prospects', 'Prospects'], ['questionnaires', 'Questionnaires'],
  ['tasks', 'Proposal tasks'], ['templates', 'Templates'],
]

export function fmt (iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric',
      year: 'numeric', hour: 'numeric', minute: '2-digit' }) + ' CT'
  } catch { return iso }
}

function errText (e) {
  const d = e && e.detail
  if (typeof d === 'string') return d
  if (d && d.message) return d.message
  return (e && e.message) || 'Something went wrong.'
}

export function Badge ({ children, tone }) {
  return <span className={'cg-badge' + (tone ? ' cg-badge--' + tone : '')}>{children}</span>
}

const INV_TONE = { active: 'info', sent: 'info', opened: 'info', submitted: 'ok', revoked: 'bad', expired: 'muted' }
const SUB_TONE = { submitted: 'warn', under_review: 'info', proposal_prepared: 'ok' }

function useLoad (fn, deps) {
  const [state, setState] = useState({ loading: true })
  const load = useCallback(() => {
    setState(s => ({ ...s, loading: true, error: null }))
    return fn().then(data => setState({ loading: false, data })).catch(e => setState({ loading: false, error: errText(e) }))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(() => { load() }, [load])
  return [state, load]
}

function Loading ({ state, retry, children }) {
  if (state.loading && !state.data) return <p className="cg-muted" role="status">Loading…</p>
  if (state.error) return <div className="cg-alert" role="alert">{state.error} <button className="cg-link" onClick={retry}>Try again</button></div>
  return children
}

function CopyButton ({ invId, url, onDone }) {
  const [msg, setMsg] = useState('')
  const copy = async () => {
    try {
      const r = url ? { url } : await api.post(`/client-growth/invitations/${invId}/copy`, {})
      await navigator.clipboard.writeText(r.url)
      setMsg('Copied')
    } catch (e) {
      setMsg(e && e.detail ? errText(e) : 'Copy failed - select the link and copy it')
    }
    setTimeout(() => setMsg(''), 2500)
    onDone && onDone()
  }
  return <span><button className="cg-btn cg-btn--small" onClick={copy}>Copy link</button>{msg ? <span className="cg-muted cg-inline"> {msg}</span> : null}</span>
}

/* ── overview ────────────────────────────────────────────────────────────── */

function Overview ({ openSubmission, setTab }) {
  const [st, reload] = useLoad(() => api.get('/client-growth/overview'), [])
  const c = st.data?.counts || {}
  const t = st.data?.test_counts || {}
  const tiles = [
    ['Pending invitations', c.pending_invitations, 'Links not yet answered (not proof they were sent)'],
    ['Submitted questionnaires', c.submitted, 'Real submissions received'],
    ['Awaiting review', c.awaiting_review, 'Submitted, nobody has started review'],
    ['Proposals prepared', c.proposals_prepared, 'Marked prepared by staff'],
  ]
  const testsHeld = (t.submitted || 0) - (c.submitted || 0) + (t.pending_invitations || 0) - (c.pending_invitations || 0)
  return (
    <Loading state={st} retry={reload}>
      <div className="cg-tiles">
        {tiles.map(([label, n, hint]) => (
          <div className="cg-tile" key={label}><div className="cg-tile-n">{n ?? 0}</div><div className="cg-tile-l">{label}</div><div className="cg-tile-h">{hint}</div></div>
        ))}
      </div>
      {testsHeld > 0 ? <p className="cg-muted">Synthetic test records are not counted above ({testsHeld}).</p> : null}
      <h2 className="cg-h2">Needs attention</h2>
      {(st.data?.attention || []).length === 0 ? <p className="cg-muted">Nothing waiting for review.</p> : (
        <table className="cg-table"><thead><tr><th>Prospect</th><th>Submitted</th><th>Status</th><th /></tr></thead><tbody>
          {st.data.attention.map(s => (
            <tr key={s.id}><td>{s.company}{s.is_test ? <> <Badge tone="muted">Test</Badge></> : null}<div className="cg-sub">{s.contact}</div></td>
              <td>{fmt(s.submitted_at)}</td><td><Badge tone={SUB_TONE[s.status]}>{s.status_label}</Badge></td>
              <td><button className="cg-btn cg-btn--small" onClick={() => openSubmission(s.id)}>Open answers</button></td></tr>
          ))}
        </tbody></table>
      )}
      <h2 className="cg-h2">Recent links</h2>
      {(st.data?.recent_invitations || []).length === 0
        ? <p className="cg-muted">No questionnaire links yet. <button className="cg-link" onClick={() => setTab('prospects')}>Choose a prospect</button> to create one.</p>
        : <InvitationTable rows={st.data.recent_invitations} reload={reload} openSubmission={openSubmission} compact />}
    </Loading>
  )
}

/* ── prospects ───────────────────────────────────────────────────────────── */

function NewProspect ({ onCreated }) {
  const [f, setF] = useState({ company: '', contact: '', email: '', phone: '', industry: '' })
  const [dups, setDups] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const set = k => e => setF({ ...f, [k]: e.target.value })
  const save = async (confirmNew) => {
    setErr(''); setBusy(true)
    try {
      const r = await api.post('/client-growth/prospects', { ...f, confirm_new: !!confirmNew })
      setDups(null); onCreated(r.prospect)
    } catch (e) {
      if (e.status === 409 && e.detail && e.detail.duplicates) setDups(e.detail.duplicates)
      else setErr(errText(e))
    } finally { setBusy(false) }
  }
  return (
    <div className="cg-panel">
      <h3 className="cg-h3">Add a prospect</h3>
      <p className="cg-muted">Checked against existing EvoSys Pro Sales prospects first. New prospects start in Discovery and are not added to any outreach.</p>
      <div className="cg-form">
        <label>Business name<input value={f.company} onChange={set('company')} maxLength={200} /></label>
        <label>Primary contact<input value={f.contact} onChange={set('contact')} maxLength={200} /></label>
        <label>Email<input type="email" value={f.email} onChange={set('email')} maxLength={160} /></label>
        <label>Phone <span className="cg-muted">(optional)</span><input value={f.phone} onChange={set('phone')} maxLength={25} /></label>
        <label>Industry <span className="cg-muted">(optional)</span><input value={f.industry} onChange={set('industry')} placeholder="insurance" maxLength={60} /></label>
      </div>
      {err ? <div className="cg-alert" role="alert">{err}</div> : null}
      {dups ? (
        <div className="cg-alert cg-alert--warn" role="alert">
          <strong>This may already be a prospect.</strong>
          <ul>{dups.map(d => <li key={d.id}>{d.company} — {d.contact || 'no contact'} · {d.email || 'no email'} · {d.stage_label}
            {' '}<button className="cg-link" onClick={() => onCreated(d)}>Use this record</button></li>)}</ul>
          <button className="cg-btn cg-btn--small" disabled={busy} onClick={() => save(true)}>It's a different business — create it</button>
        </div>
      ) : null}
      <button className="cg-btn cg-btn--primary" disabled={busy || !f.company.trim()} onClick={() => save(false)}>Add prospect</button>
    </div>
  )
}

function InviteForm ({ prospect, onCreated }) {
  const [tpl, setTpl] = useState(null)
  const [f, setF] = useState({ template_key: '', prepared_for: prospect.contact || '', contact_name: prospect.contact || '',
    business_name: prospect.company || '', email: prospect.email || '', expires_days: 30 })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => {
    api.get('/client-growth/templates').then(r => {
      const act = r.templates.filter(t => t.is_active)
      setTpl(act)
      if (act.length) setF(x => ({ ...x, template_key: x.template_key || act[act.length - 1].key }))
    }).catch(e => setErr(errText(e)))
  }, [])
  const set = k => e => setF({ ...f, [k]: e.target.value })
  const go = async () => {
    setBusy(true); setErr('')
    try {
      const r = await api.post('/client-growth/invitations', { prospect_id: prospect.id, ...f, expires_days: Number(f.expires_days) || 30 })
      onCreated(r.invitation)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="cg-panel">
      <h3 className="cg-h3">Create questionnaire link</h3>
      <p className="cg-muted">Creates a private link only. Nothing is emailed or texted — copy it and send it yourself.</p>
      <div className="cg-form">
        <label>Template<select value={f.template_key} onChange={set('template_key')}>
          {(tpl || []).map(t => <option key={t.id} value={t.key}>{t.name} (v{t.version})</option>)}</select></label>
        <label>"Prepared for" name<input value={f.prepared_for} onChange={set('prepared_for')} maxLength={120} /></label>
        <label>Pre-filled contact name<input value={f.contact_name} onChange={set('contact_name')} maxLength={160} /></label>
        <label>Pre-filled business name<input value={f.business_name} onChange={set('business_name')} maxLength={160} /></label>
        <label>Pre-filled email<input type="email" value={f.email} onChange={set('email')} maxLength={160} /></label>
        <label>Expires after (days)<input type="number" min="1" max="180" value={f.expires_days} onChange={set('expires_days')} /></label>
      </div>
      {err ? <div className="cg-alert" role="alert">{err}</div> : null}
      <button className="cg-btn cg-btn--primary" disabled={busy || !f.template_key} onClick={go}>Create link</button>
    </div>
  )
}

function ProspectDetail ({ id, openSubmission, onBack }) {
  const [st, reload] = useLoad(() => api.get('/client-growth/prospects/' + id), [id])
  const [created, setCreated] = useState(null)
  const p = st.data?.prospect
  return (
    <Loading state={st} retry={reload}>
      {p ? (
        <div>
          <button className="cg-link" onClick={onBack}>← All prospects</button>
          <h2 className="cg-h2">{p.company} {p.is_test ? <Badge tone="muted">Synthetic test</Badge> : null}</h2>
          <p className="cg-sub">{p.contact || 'No contact'} · {p.email || 'no email'} · {p.stage_label} · source: {p.source || '—'}</p>
          {created ? (
            <div className="cg-alert cg-alert--ok" role="status">
              <strong>Link created — not sent.</strong> <code className="cg-code">{created.url}</code> <CopyButton url={created.url} />
            </div>
          ) : null}
          <div className="cg-two">
            <InviteForm prospect={p} onCreated={(i) => { setCreated(i); reload() }} />
            <div className="cg-panel">
              <h3 className="cg-h3">Activity history</h3>
              <ul className="cg-activity">{st.data.activity.map((a, i) => (
                <li key={i}><span className="cg-sub">{fmt(a.at)}</span> {a.summary}{a.detail ? <div className="cg-sub">{a.detail}</div> : null}</li>))}</ul>
            </div>
          </div>
          <h3 className="cg-h3">Links</h3>
          {st.data.invitations.length ? <InvitationTable rows={st.data.invitations} reload={reload} openSubmission={openSubmission} /> : <p className="cg-muted">None yet.</p>}
          <h3 className="cg-h3">Submissions</h3>
          {st.data.submissions.length ? <SubmissionTable rows={st.data.submissions} openSubmission={openSubmission} /> : <p className="cg-muted">None yet.</p>}
        </div>
      ) : null}
    </Loading>
  )
}

function Prospects ({ openSubmission, initialId }) {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const [sel, setSel] = useState(initialId || null)
  const [st, reload] = useLoad(() => api.get('/client-growth/prospects', { params: term ? { q: term } : {} }), [term])
  useEffect(() => { const t = setTimeout(() => setTerm(q.trim()), 250); return () => clearTimeout(t) }, [q])
  if (sel) return <ProspectDetail id={sel} openSubmission={openSubmission} onBack={() => { setSel(null); reload() }} />
  return (
    <div className="cg-two">
      <div>
        <label className="cg-search">Find an existing EvoSys Pro Sales prospect
          <input type="search" value={q} onChange={e => setQ(e.target.value)} placeholder="Business, contact or email" /></label>
        <Loading state={st} retry={reload}>
          {(st.data?.prospects || []).length === 0 ? <p className="cg-muted">{term ? 'No matching prospects.' : 'No prospects in discovery yet. Search above, or add one.'}</p> : (
            <table className="cg-table"><thead><tr><th>Business</th><th>Contact</th><th>Stage</th><th>Links</th><th /></tr></thead><tbody>
              {st.data.prospects.map(p => (
                <tr key={p.id}><td>{p.company}{p.is_test ? <> <Badge tone="muted">Test</Badge></> : null}</td><td>{p.contact || '—'}<div className="cg-sub">{p.email}</div></td>
                  <td>{p.stage_label}</td><td>{p.invitations} / {p.submissions} answered</td>
                  <td><button className="cg-btn cg-btn--small" onClick={() => setSel(p.id)}>Open</button></td></tr>
              ))}
            </tbody></table>
          )}
        </Loading>
      </div>
      <NewProspect onCreated={p => setSel(p.id)} />
    </div>
  )
}

/* ── questionnaires ──────────────────────────────────────────────────────── */

function RevokeBox ({ inv, onDone, onCancel }) {
  const [reason, setReason] = useState('')
  const [err, setErr] = useState('')
  const go = async () => {
    try { await api.post(`/client-growth/invitations/${inv.id}/revoke`, { reason, confirm: true }); onDone() } catch (e) { setErr(errText(e)) }
  }
  return (
    <div className="cg-confirm" role="dialog" aria-label="Revoke link">
      <p><strong>Revoke the link for {inv.company}?</strong> It stops working immediately. This is recorded in the audit log.</p>
      <label>Reason<input value={reason} onChange={e => setReason(e.target.value)} maxLength={500} autoFocus /></label>
      {err ? <div className="cg-alert" role="alert">{err}</div> : null}
      <button className="cg-btn cg-btn--danger" disabled={!reason.trim()} onClick={go}>Revoke link</button>{' '}
      <button className="cg-btn" onClick={onCancel}>Cancel</button>
    </div>
  )
}

function InvitationTable ({ rows, reload, openSubmission, compact }) {
  const [revoking, setRevoking] = useState(null)
  const [msg, setMsg] = useState(null)
  const markSent = async (i) => {
    try { await api.post(`/client-growth/invitations/${i.id}/mark-sent`, { note: null }); reload() } catch (e) { setMsg(errText(e)) }
  }
  const reissue = async (i) => {
    try { const r = await api.post(`/client-growth/invitations/${i.id}/reissue`, { reason: 'Replaced with a new link' }); setMsg({ url: r.invitation.url }); reload() } catch (e) { setMsg(errText(e)) }
  }
  return (
    <div>
      {msg ? (typeof msg === 'string' ? <div className="cg-alert">{msg}</div>
        : <div className="cg-alert cg-alert--ok">New link created — not sent. <code className="cg-code">{msg.url}</code> <CopyButton url={msg.url} /></div>) : null}
      {revoking ? <RevokeBox inv={revoking} onCancel={() => setRevoking(null)} onDone={() => { setRevoking(null); reload() }} /> : null}
      <table className="cg-table"><thead><tr><th>Prospect</th><th>Template</th><th>Status</th><th>Created</th>{compact ? null : <th>Expires</th>}<th>Actions</th></tr></thead><tbody>
        {rows.map(i => (
          <tr key={i.id}>
            <td>{i.company}{i.is_test ? <> <Badge tone="muted">Test</Badge></> : null}<div className="cg-sub">for {i.prepared_for || i.contact || '—'}</div></td>
            <td>{i.template ? `${i.template.name} v${i.template.version}` : '—'}</td>
            <td><Badge tone={INV_TONE[i.state]}>{i.state_label}</Badge>
              {i.sent_at ? <div className="cg-sub">Sent recorded {fmt(i.sent_at)}</div> : <div className="cg-sub">Not recorded as sent</div>}
              {i.opened_at ? <div className="cg-sub">Opened {fmt(i.opened_at)}</div> : null}</td>
            <td>{fmt(i.created_at)}</td>{compact ? null : <td>{fmt(i.expires_at)}</td>}
            <td className="cg-actions">
              {['active', 'sent', 'opened'].includes(i.state) ? <>
                <CopyButton invId={i.id} />
                {i.sent_at ? null : <button className="cg-btn cg-btn--small" onClick={() => markSent(i)} title="Record that you sent it yourself">I sent it</button>}
                <button className="cg-btn cg-btn--small cg-btn--danger" onClick={() => setRevoking(i)}>Revoke</button>
              </> : null}
              {i.template && i.template.id ? <Link className="cg-btn cg-btn--small" title="See the form as this prospect will - their link is not opened"
                to={'/client-growth/preview/' + i.template.id + '?for=' + encodeURIComponent(i.prepared_for || '')
                  + '&contact_name=' + encodeURIComponent(i.contact || '') + '&business_name=' + encodeURIComponent(i.company || '')}>Preview</Link> : null}
              {i.submission_id ? <button className="cg-btn cg-btn--small" onClick={() => openSubmission(i.submission_id)}>Open answers</button> : null}
              {['revoked', 'expired', 'submitted'].includes(i.state) && !compact
                ? <button className="cg-btn cg-btn--small" onClick={() => reissue(i)} title="Correction / re-invitation: a fresh link; earlier answers stay as received">New link</button> : null}
            </td>
          </tr>
        ))}
      </tbody></table>
      <p className="cg-sub">Delivery is never known here: a link being created or copied is not proof it was sent or received.</p>
    </div>
  )
}

function SubmissionTable ({ rows, openSubmission }) {
  return (
    <table className="cg-table"><thead><tr><th>Prospect</th><th>Template</th><th>Submitted</th><th>Status</th><th /></tr></thead><tbody>
      {rows.map(s => (
        <tr key={s.id}><td>{s.company}{s.is_test ? <> <Badge tone="muted">Test</Badge></> : null}<div className="cg-sub">{s.contact}</div></td>
          <td>{s.template_key} v{s.template_version}</td><td>{fmt(s.submitted_at)}</td>
          <td><Badge tone={SUB_TONE[s.status]}>{s.status_label}</Badge></td>
          <td><button className="cg-btn cg-btn--small" onClick={() => openSubmission(s.id)}>Open answers</button></td></tr>
      ))}
    </tbody></table>
  )
}

function Questionnaires ({ openSubmission }) {
  const [inv, reloadInv] = useLoad(() => api.get('/client-growth/invitations'), [])
  const [sub, reloadSub] = useLoad(() => api.get('/client-growth/submissions'), [])
  return (
    <div>
      <h2 className="cg-h2">Submissions</h2>
      <Loading state={sub} retry={reloadSub}>
        {(sub.data?.submissions || []).length ? <SubmissionTable rows={sub.data.submissions} openSubmission={openSubmission} /> : <p className="cg-muted">No submissions yet.</p>}
      </Loading>
      <h2 className="cg-h2">Invitation links</h2>
      <Loading state={inv} retry={reloadInv}>
        {(inv.data?.invitations || []).length ? <InvitationTable rows={inv.data.invitations} reload={reloadInv} openSubmission={openSubmission} /> : <p className="cg-muted">No links yet.</p>}
      </Loading>
    </div>
  )
}

export function SubmissionView ({ id, onClose, onOpenProspect }) {
  const [st, reload] = useLoad(() => api.get('/client-growth/submissions/' + id), [id])
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [raw, setRaw] = useState(false)
  const s = st.data?.submission
  const act = async (path, label) => {
    setBusy(label); setErr('')
    try { await api.post(`/client-growth/submissions/${id}/${path}`, {}); await reload() } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }
  const groups = useMemo(() => {
    const g = []
    for (const r of s?.readable || []) {
      const last = g[g.length - 1]
      if (!last || last.step !== r.step) g.push({ step: r.step, rows: [r] }); else last.rows.push(r)
    }
    return g
  }, [s])
  return (
    <div className="cg-sheet" role="dialog" aria-label="Submitted answers">
      <div className="cg-sheet-inner">
        <button className="cg-link cg-close" onClick={onClose}>Close ✕</button>
        <Loading state={st} retry={reload}>
          {s ? <>
            <h2 className="cg-h2">{s.company} {s.is_test ? <Badge tone="muted">Synthetic test</Badge> : null}</h2>
            <p className="cg-sub">Submitted {fmt(s.submitted_at)} · {s.template_key} v{s.template_version} · <Badge tone={SUB_TONE[s.status]}>{s.status_label}</Badge></p>
            <div className="cg-toolbar">
              {s.status === 'submitted' ? <button className="cg-btn" disabled={!!busy} onClick={() => act('review', 'review')}>Mark under review</button> : null}
              {s.status !== 'proposal_prepared' ? <button className="cg-btn cg-btn--primary" disabled={!!busy} onClick={() => act('proposal-prepared', 'prep')}>Mark proposal prepared</button> : null}
              <button className="cg-btn" disabled={!!busy} onClick={() => act('ai-summary', 'ai')}>{busy === 'ai' ? 'Working…' : (s.ai_summary ? 'Redo AI summary' : 'AI summary')}</button>
              <button className="cg-btn" onClick={() => onOpenProspect(s.prospect_id)}>Open prospect</button>
            </div>
            {err ? <div className="cg-alert" role="alert">{err}</div> : null}
            {s.task ? (
              <div className="cg-panel cg-task" id="task">
                <strong>Proposal task:</strong> {s.task.title} — <Badge tone={s.task.status === 'done' ? 'ok' : 'warn'}>{s.task.status === 'done' ? 'Done' : 'Open'}</Badge>
                <span className="cg-sub"> created {fmt(s.task.created_at)}{s.task.completed_at ? ' · done ' + fmt(s.task.completed_at) : ''}</span>
              </div>
            ) : null}
            {s.ai_summary ? (
              <div className="cg-panel cg-ai">
                <p className="cg-ai-label">{s.ai_summary.label}</p>
                {s.ai_summary.status === 'done' ? <dl>
                  <dt>Goals</dt><dd>{s.ai_summary.content.goals || '—'}</dd>
                  <dt>Budget</dt><dd>{s.ai_summary.content.budget || '—'}</dd>
                  <dt>VA readiness</dt><dd>{s.ai_summary.content.va_readiness || '—'}</dd>
                  <dt>Still to ask</dt><dd>{(s.ai_summary.content.missing || []).join('; ') || '—'}</dd>
                </dl> : <p className="cg-muted">{s.ai_summary.error}</p>}
              </div>
            ) : null}
            <h3 className="cg-h3">Original answers</h3>
            {groups.map(g => (
              <div key={g.step} className="cg-answers">
                <h4>{g.step}</h4>
                <dl>{g.rows.map(r => <div key={r.key}><dt>{r.label}</dt><dd>{Array.isArray(r.display) ? r.display.join(', ') : String(r.display)}</dd></div>)}</dl>
              </div>
            ))}
            <button className="cg-link" onClick={() => setRaw(!raw)}>{raw ? 'Hide' : 'Show'} exactly as received</button>
            {raw ? <pre className="cg-pre">{JSON.stringify(s.answers, null, 2)}</pre> : null}
          </> : null}
        </Loading>
      </div>
    </div>
  )
}

/* ── tasks and templates ─────────────────────────────────────────────────── */

function Tasks ({ openSubmission }) {
  const [st, reload] = useLoad(() => api.get('/client-growth/tasks'), [])
  return (
    <Loading state={st} retry={reload}>
      {(st.data?.tasks || []).length === 0 ? <p className="cg-muted">No proposal tasks yet. A task is created when a questionnaire is submitted.</p> : (
        <table className="cg-table"><thead><tr><th>Task</th><th>Prospect</th><th>Created</th><th>Status</th><th /></tr></thead><tbody>
          {st.data.tasks.map(k => (
            <tr key={k.id}><td>{k.title}{k.is_test ? <> <Badge tone="muted">Test</Badge></> : null}</td><td>{k.company}</td><td>{fmt(k.created_at)}</td>
              <td><Badge tone={k.status === 'done' ? 'ok' : 'warn'}>{k.status === 'done' ? 'Proposal prepared' : 'Open'}</Badge></td>
              <td><button className="cg-btn cg-btn--small" onClick={() => openSubmission(k.submission_id)}>Open</button></td></tr>
          ))}
        </tbody></table>
      )}
    </Loading>
  )
}

function Templates () {
  const [st, reload] = useLoad(() => api.get('/client-growth/templates'), [])
  const [open, setOpen] = useState(null)
  const [def, setDef] = useState(null)
  useEffect(() => { if (open) api.get('/client-growth/templates/' + open).then(r => setDef(r.definition)).catch(() => setDef(null)) }, [open])
  return (
    <Loading state={st} retry={reload}>
      <p className="cg-muted">Shared discovery questions (goals, budget, team, contact, notes) with vertical-specific questions per template. Each submission keeps the template version it was answered on.</p>
      <table className="cg-table"><thead><tr><th>Template</th><th>Vertical</th><th>Version</th><th>Sections</th><th>Links</th><th>Status</th><th /></tr></thead><tbody>
        {(st.data?.templates || []).map(t => (
          <tr key={t.id}><td>{t.name}</td><td>{t.vertical || 'Any'}</td><td>v{t.version}</td>
            <td>{t.steps.map(s => `${s.title} (${s.questions})`).join(' · ')}</td><td>{t.invitations}</td>
            <td><Badge tone={t.is_active ? 'ok' : 'muted'}>{t.is_active ? 'Active' : 'Off'}</Badge></td>
            <td className="cg-actions">
              <Link className="cg-btn cg-btn--small cg-btn--primary" to={'/client-growth/preview/' + t.id}>Preview form</Link>
              <button className="cg-btn cg-btn--small" onClick={() => setOpen(open === t.id ? null : t.id)}>{open === t.id ? 'Hide' : 'Questions'}</button></td></tr>
        ))}
      </tbody></table>
      {open && def ? (
        <div className="cg-panel">{def.steps.map(s => (
          <div key={s.key}><h4>{s.number} {s.title}</h4><ul>{s.fields.map(f => (
            <li key={f.key}>{f.label} <span className="cg-sub">({f.type}{f.required ? ', required' : ''}{f.template_specific ? ', template-specific' : ''}{f.show_if ? ', conditional' : ''})</span></li>))}</ul></div>))}
        </div>
      ) : null}
    </Loading>
  )
}

/* ── shell ───────────────────────────────────────────────────────────────── */

export default function ClientGrowthWorkspace () {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const [me, setMe] = useState(null)
  const [denied, setDenied] = useState('')
  const tab = params.get('tab') || 'overview'
  const subId = params.get('submission')
  const prospectId = params.get('prospect')
  useEffect(() => { document.title = 'Client Growth · EvoSys Pro' }, [])
  useEffect(() => {
    api.get('/client-growth/me').then(setMe).catch(e => setDenied(e.status === 403 ? errText(e) : errText(e)))
  }, [])
  const setTab = (t) => setParams(t === 'overview' ? {} : { tab: t })
  const openSubmission = (id) => setParams({ ...(tab !== 'overview' ? { tab } : {}), submission: id })
  const openProspect = (id) => setParams({ tab: 'prospects', prospect: id })
  const back = launcherOrigin() || '/workspaces'

  if (denied) {
    return <div className="cg-page"><div className="cg-denied" role="alert"><h1>Client Growth</h1><p>{denied}</p><Link to="/workspaces">Back to Program Access</Link></div></div>
  }
  return (
    <div className="cg-page">
      <header className="cg-top">
        <div className="cg-brand">
          <img src="/brands/evosyspro-emblem-96.png" alt="" width="34" height="34" />
          <span className="cg-bar" aria-hidden="true" />
          <span className="cg-brand-name">EvoSys Pro</span>
          <span className="cg-product">Client Growth</span>
        </div>
        <nav className="cg-topnav" aria-label="Client Growth">
          {me && me.is_owner ? <Link to="/god/client-growth" className="cg-toplink">God Mode controls</Link> : null}
          <button className="cg-toplink" onClick={() => navigate(back)}>Program Access</button>
        </nav>
      </header>
      <div className="cg-wrap">
        <p className="cg-tagline">Discovery • Questionnaires • Proposals</p>
        <nav className="cg-tabs" role="tablist" aria-label="Sections">
          {TABS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={'cg-tab' + (tab === k ? ' is-on' : '')} onClick={() => setTab(k)}>{label}</button>
          ))}
        </nav>
        <main className="cg-body">
          {tab === 'overview' ? <Overview openSubmission={openSubmission} setTab={setTab} /> : null}
          {tab === 'prospects' ? <Prospects key={prospectId || 'list'} openSubmission={openSubmission} initialId={prospectId} /> : null}
          {tab === 'questionnaires' ? <Questionnaires openSubmission={openSubmission} /> : null}
          {tab === 'tasks' ? <Tasks openSubmission={openSubmission} /> : null}
          {tab === 'templates' ? <Templates /> : null}
        </main>
      </div>
      {subId ? <SubmissionView id={subId} onClose={() => setParams(tab !== 'overview' ? { tab } : {})} onOpenProspect={openProspect} /> : null}
    </div>
  )
}
