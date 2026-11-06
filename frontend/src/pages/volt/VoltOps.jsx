/* VOLT OPERATIONS - recruiting staff and owner oversight (#535).
 *
 * Reached through Program Access (VOLT Marketing card) -> /volt-ops/:orgId.
 * Every number, list and file comes from /volt/ops/:orgId/*, which re-checks
 * the signed-in person's authority for this organization on every request.
 * Nothing here decides access; a viewer simply sees no write buttons, and the
 * server refuses the write anyway.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, fetchObjectUrl } from '../../api/client'
import { Bolt } from './VoltPortal'
import { fmtDate, sessionTime } from './voltApi'
import './volt.css'

const STAGES = [['draft', 'Draft'], ['submitted', 'Submitted'], ['needs_update', 'Needs update'], ['approved', 'Approved'], ['started', 'Started']]
const STAGE = Object.fromEntries(STAGES)
const REACHED = [['created', 'Application started'], ['submitted', 'Submitted'], ['approved', 'Approved'], ['started', 'Started']]
const ACTION_LABEL = { approved: 'Approve', needs_update: 'Return for update', started: 'Mark Started' }
const TZS = ['America/Chicago', 'America/New_York', 'America/Denver', 'America/Phoenix', 'America/Los_Angeles', 'America/Anchorage', 'Pacific/Honolulu']

function msg(e) { return e?.message || 'Something went wrong.' }

function Err({ children }) { return children ? <div className="v-alert v-alert--err" role="alert">{children}</div> : null }

function useOps(orgId) {
  const [home, setHome] = useState(null)
  const [err, setErr] = useState('')
  // Filters can change faster than the server answers: only the LATEST
  // request may paint, so a slower earlier answer never overwrites it.
  const seq = useRef(0)
  const load = useCallback((params = {}) => {
    const n = ++seq.current
    return api.get(`/volt/ops/${orgId}`, { params })
      .then((h) => { if (n === seq.current) setHome(h) })
      .catch((e) => { if (n === seq.current) setErr(msg(e)) })
  }, [orgId])
  return { home, err, load }
}

function OpsShell({ orgId, home, children }) {
  const base = `/volt-ops/${orgId}`
  return (
    <div className="volt">
      <header className="v-head">
        <div className="v-wrap">
          <Link className="v-brand" to={base} aria-label="VOLT Operations home"><Bolt /><span className="v-word">VOLT</span> <span className="v-word-sub">Operations</span></Link>
          <nav aria-label="Operations">
            <span className="v-small">{home?.me?.full_name}{home?.access === 'owner' ? ' · Owner' : home?.access === 'viewer' ? ' · View only' : ' · Staff'}</span>
            <Link className="v-btn v-btn--ghost v-btn--sm" to="/workspaces">Program Access</Link>
          </nav>
        </div>
      </header>
      <main className="v-wrap" style={{ flex: 1 }}>
        <nav className="v-tabs" aria-label="VOLT Operations sections">
          <NavLink end className="v-tab" to={base}>Overview</NavLink>
          <NavLink className="v-tab" to={`${base}/review`}>Review queue</NavLink>
          <NavLink className="v-tab" to={`${base}/sessions`}>Zoom Sessions</NavLink>
          <NavLink className="v-tab" to={`${base}/reps`}>Reps</NavLink>
          <NavLink className="v-tab" to={`${base}/projects`}>Projects & documents</NavLink>
        </nav>
        {children}
      </main>
      <footer className="v-foot"><div className="v-wrap"><span>VOLT Marketing · Operations</span><a href="/volt" target="_blank" rel="noreferrer">Open applicant portal ↗</a></div></footer>
    </div>
  )
}

function Filters({ projects, f, set, showStage = false, showReached = false }) {
  const up = (k) => (e) => set({ ...f, [k]: e.target.value })
  return (
    <div className="v-panel v-filters">
      <label>Project<select value={f.project_id || ''} onChange={up('project_id')}>
        <option value="">All projects</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.short_name}</option>)}</select></label>
      {showStage && <label>Current stage<select value={f.status || ''} onChange={up('status')}>
        <option value="">All stages</option>{STAGES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>}
      {showReached && <label>Date applies to<select value={f.reached || 'created'} onChange={up('reached')}>
        {REACHED.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>}
      <label>From<input type="date" value={f.date_from || ''} onChange={up('date_from')} /></label>
      <label>To<input type="date" value={f.date_to || ''} onChange={up('date_to')} /></label>
      <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontWeight: 600 }}>
        <input type="checkbox" style={{ width: 'auto', minHeight: 0, margin: 0 }} checked={f.include_test === '1'}
               onChange={(e) => set({ ...f, include_test: e.target.checked ? '1' : '' })} />Include test records</label>
    </div>
  )
}

function clean(f) { return Object.fromEntries(Object.entries(f).filter(([, v]) => v !== '' && v !== undefined && v !== null)) }

// ── Overview ────────────────────────────────────────────────────────────────
function Overview({ orgId, ops }) {
  const [f, setF] = useState({})
  const navigate = useNavigate()
  useEffect(() => { ops.load(clean(f)) }, [JSON.stringify(f)]) // eslint-disable-line react-hooks/exhaustive-deps
  const h = ops.home
  if (!h) return <p className="v-mono">Loading…</p>
  const o = h.overview
  const kpis = [['unique_people', 'Unique people'], ['applications', 'Applications'], ['awaiting_review', 'Awaiting review'],
    ['submitted', 'Submitted'], ['approvals', 'Approvals'], ['starts', 'Starts']]
  const open = (k) => navigate(`/volt-ops/${orgId}/review?${new URLSearchParams(clean({ ...f, ...o.links[k] })).toString()}`)
  return (
    <>
      <div className="v-row"><h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>Recruiting overview</h2>
        <span className="v-tag">Zoom: staff-supplied participant links</span></div>
      <Filters projects={h.projects} f={f} set={setF} />
      <p className="v-small v-muted">{o.date_rule} Click a number to open that exact list.</p>
      <div className="v-kpis">
        {kpis.map(([k, l]) => <button key={k} type="button" className="v-kpi" data-kpi={k} onClick={() => open(k)}><b>{o[k]}</b><span>{l}</span></button>)}
      </div>
      <section className="v-panel">
        <h3>Referral performance{f.project_id ? ' · ' + (h.projects.find((p) => p.id === f.project_id)?.short_name || '') : ' · all projects'}</h3>
        <p className="v-small v-muted">{h.leaderboard.rule} Columns count distinct people credited to that recruiter for the project referral.</p>
        <div className="v-scroll"><table className="v-table" data-table="leaderboard">
          <thead><tr><th>#</th><th>Recruiter</th><th>Signed up</th><th>Submitted</th><th>Approved</th><th>Started</th></tr></thead>
          <tbody>{h.leaderboard.rows.length === 0 ? <tr><td colSpan="6" className="v-muted">No referrals in this view yet.</td></tr>
            : h.leaderboard.rows.map((r) => (
              <tr key={r.rep_id}><td>{r.rank}</td><td><b>{r.display_name}</b> <span className="v-mono">{r.rep_code}</span></td>
                <td>{r.registrations}</td><td>{r.submissions}</td><td>{r.approvals}</td>
                <td><button type="button" className="v-btn v-btn--link" onClick={() => navigate(`/volt-ops/${orgId}/review?${new URLSearchParams(clean({ ...f, reached: 'started', recruiter: r.rep_id }))}`)}>{r.starts}</button></td></tr>))}
          </tbody></table></div>
      </section>
    </>
  )
}

// ── Review queue + application file ─────────────────────────────────────────
function ReviewQueue({ orgId, ops }) {
  const [sp, setSp] = useSearchParams()
  const f = Object.fromEntries(sp.entries())
  const [list, setList] = useState(null)
  const [err, setErr] = useState('')
  const [openId, setOpenId] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (!ops.home) ops.load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const qseq = useRef(0)
  const load = useCallback(() => {
    const n = ++qseq.current
    return api.get(`/volt/ops/${orgId}/applications`, { params: clean(f) })
      .then((x) => { if (n === qseq.current) setList(x) }).catch((e) => setErr(msg(e)))
  }, [orgId, sp.toString()]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [load])
  async function exportCsv() {
    setBusy(true)
    try {
      const url = await fetchObjectUrl(`/volt/ops/${orgId}/export.csv?${new URLSearchParams(clean(f))}`)
      const a = document.createElement('a'); a.href = url; a.download = 'volt-marketing-applications.csv'; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 2000)
    } catch (e) { setErr(msg(e)) } finally { setBusy(false) }
  }
  const projects = ops.home?.projects || []
  const recruiterName = list?.items.find((r) => String(r.project_recruiter?.rep_id) === String(f.recruiter))?.project_recruiter
  return (
    <>
      <div className="v-row"><h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>Review queue</h2>
        <button type="button" className="v-btn v-btn--black" onClick={exportCsv} disabled={busy}>{busy ? 'Exporting…' : 'Export CSV ↓'}</button></div>
      <Filters projects={projects} f={f} set={(x) => setSp(clean(x))} showStage showReached />
      {f.recruiter && <p className="v-small">Recruiter: <b>{recruiterName ? `${recruiterName.display_name} · ${recruiterName.rep_code}` : f.recruiter}</b> <button type="button" className="v-btn v-btn--link" onClick={() => { const x = { ...f }; delete x.recruiter; setSp(clean(x)) }}>clear</button></p>}
      <Err>{err}</Err>
      <p className="v-small v-muted" data-count={list?.count}>{list ? `${list.count} application${list.count === 1 ? '' : 's'} · dates filter on "${list.reached_label}". The CSV is exactly this list.` : 'Loading…'}</p>
      <div className="v-panel v-scroll">
        <table className="v-table" data-table="queue">
          <thead><tr><th>Applicant</th><th>Project</th><th>Stage</th><th>Project recruiter</th><th>Original recruiter</th><th>Updated</th><th /></tr></thead>
          <tbody>
            {list?.items.length === 0 && <tr><td colSpan="7" className="v-muted">No matching applications.</td></tr>}
            {list?.items.map((r) => (
              <tr key={r.application_id}>
                <td><b>{r.applicant}</b><br /><span className="v-mono">{r.rep_code}</span>{r.is_test && <> <span className="v-tag v-tag--line">Test</span></>}</td>
                <td>{r.project}</td><td><span className="v-tag">{r.status_label}</span></td>
                <td>{r.project_recruiter ? `${r.project_recruiter.display_name} · ${r.project_recruiter.rep_code}` : 'Direct'}</td>
                <td>{r.original_recruiter ? `${r.original_recruiter.display_name} · ${r.original_recruiter.rep_code}` : 'Direct'}</td>
                <td>{fmtDate(r.updated_at)}</td>
                <td><button type="button" className="v-btn v-btn--red v-btn--sm" onClick={() => setOpenId(r.application_id)}>Open</button></td>
              </tr>))}
          </tbody>
        </table>
      </div>
      {openId && <ApplicationFile orgId={orgId} appId={openId} viewer={ops.home?.access === 'viewer'} onClose={() => { setOpenId(null); load(); ops.load() }} />}
    </>
  )
}

function RecruiterChooser({ orgId, onPick }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState([])
  useEffect(() => {
    if (q.trim().length < 2) { setRows([]); return undefined }
    const t = setTimeout(() => api.get(`/volt/ops/${orgId}/reps`, { params: { q } }).then((r) => setRows(r.items)).catch(() => setRows([])), 220)
    return () => clearTimeout(t)
  }, [q, orgId])
  return (
    <div>
      <input placeholder="Search recruiter by name or REP code" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search recruiter" />
      <div className="v-matches">{rows.map((r) => (
        <button type="button" key={r.rep_id} className="v-match" onClick={() => { onPick(r); setQ(''); setRows([]) }}>
          <b>{r.display_name}</b><span className="v-mono">{r.rep_code}{r.recruiter_active ? '' : ' · inactive'}</span></button>))}</div>
    </div>
  )
}

function ApplicationFile({ orgId, appId, viewer, onClose }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [reason, setReason] = useState('')
  const [docReason, setDocReason] = useState({})
  const [fix, setFix] = useState(null)
  const [fixReason, setFixReason] = useState('')
  const load = useCallback(() => api.get(`/volt/ops/${orgId}/applications/${appId}`).then(setD).catch((e) => setErr(msg(e))), [orgId, appId])
  useEffect(() => { load() }, [load])
  async function act(status) {
    setErr('')
    try { await api.post(`/volt/ops/${orgId}/applications/${appId}/status`, { status, reason: status === 'needs_update' ? reason : undefined }); setReason(''); await load() } catch (e) { setErr(msg(e)) }
  }
  async function reviewDoc(doc, status) {
    setErr('')
    try { await api.post(`/volt/ops/${orgId}/documents/${doc.id}/review`, { status, reason: docReason[doc.id] }); await load() } catch (e) { setErr(msg(e)) }
  }
  async function openDoc(doc) {
    try { const url = await fetchObjectUrl(`/volt/ops/${orgId}/documents/${doc.id}/file`); window.open(url, '_blank', 'noopener') } catch (e) { setErr(msg(e)) }
  }
  async function saveFix() {
    setErr('')
    const path = fix.kind === 'project' ? `/volt/ops/${orgId}/applications/${appId}/referral` : `/volt/ops/${orgId}/reps/${d.profile.rep_id}/original-recruiter`
    try { await api.post(path, { referrer_rep_id: fix.rep ? fix.rep.rep_id : null, reason: fixReason }); setFix(null); setFixReason(''); await load() } catch (e) { setErr(msg(e)) }
  }
  return (
    <div className="v-drawer-bg" role="dialog" aria-modal="true" aria-label="Application file" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="v-drawer">
        <div className="v-row"><span className="v-mono">Application file</span><button type="button" className="v-btn v-btn--line v-btn--sm" onClick={onClose}>Close</button></div>
        <Err>{err}</Err>
        {!d ? <p className="v-mono">Loading…</p> : (
          <>
            <h2 style={{ fontSize: 28, letterSpacing: '-.04em', margin: '8px 0 2px' }}>{d.profile.first_name} {d.profile.last_name}</h2>
            <p className="v-mono">{d.profile.rep_code}{d.profile.is_test ? ' · TEST RECORD' : ''}</p>
            <p className="v-small" style={{ marginTop: 6 }}>{d.profile.email} · {d.profile.phone} · DOB {d.profile.date_of_birth}</p>
            <section className="v-panel" style={{ marginTop: 12 }}>
              <div className="v-row"><h3>{d.application.project.name}</h3><span className="v-tag" data-stage={d.application.status}>{d.application.status_label}</span></div>
              {d.application.return_reason && d.application.status === 'needs_update' && <p className="v-ferr">Returned: {d.application.return_reason}</p>}
              <p className="v-label">{d.application.project.question}</p>
              <p>{d.application.answers.experience || <span className="v-muted">No answer yet.</span>}</p>
              {d.missing.length > 0 && <p className="v-small v-muted">Applicant still needs: {d.missing.join('; ')}</p>}
              {!viewer && d.next_actions.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  {d.next_actions.includes('needs_update') && <label>Reason for the applicant (required to return)<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Exactly what to change" /></label>}
                  <div className="v-row" style={{ justifyContent: 'flex-start', marginTop: 8 }}>
                    {d.next_actions.map((s) => <button key={s} type="button" className={`v-btn v-btn--sm ${s === 'needs_update' ? 'v-btn--line' : 'v-btn--red'}`} onClick={() => act(s)}>{ACTION_LABEL[s]}</button>)}
                  </div>
                </div>
              )}
              {d.other_applications.length > 0 && <p className="v-small v-muted" style={{ marginTop: 8 }}>Also applied: {d.other_applications.map((o) => `${o.project} (${STAGE[o.status]})`).join(', ')}</p>}
            </section>
            <section className="v-panel">
              <h3>Recruiter attribution</h3>
              <p><b>Original recruiter:</b> {d.original_recruiter ? `${d.original_recruiter.display_name} · ${d.original_recruiter.rep_code}` : 'Direct signup'}</p>
              <p><b>Project referral:</b> {d.project_recruiter ? `${d.project_recruiter.display_name} · ${d.project_recruiter.rep_code}` : 'Direct'}{d.application.referral_source ? ` (${d.application.referral_source})` : ''}</p>
              {!viewer && !fix && <div className="v-row" style={{ justifyContent: 'flex-start', marginTop: 8 }}>
                <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setFix({ kind: 'project', rep: null })}>Correct project referral</button>
                <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setFix({ kind: 'original', rep: null })}>Correct original recruiter</button></div>}
              {fix && (
                <div className="v-note-staff">
                  <p><b>{fix.kind === 'project' ? 'New project referral' : 'New original recruiter'}:</b> {fix.rep ? `${fix.rep.display_name} · ${fix.rep.rep_code}` : 'None (direct)'}</p>
                  <RecruiterChooser orgId={orgId} onPick={(r) => setFix({ ...fix, rep: r })} />
                  <label>Reason (required, kept in the audit trail)<input value={fixReason} onChange={(e) => setFixReason(e.target.value)} /></label>
                  <div className="v-row" style={{ justifyContent: 'flex-start', marginTop: 8 }}>
                    <button type="button" className="v-btn v-btn--red v-btn--sm" onClick={saveFix}>Save correction</button>
                    <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setFix(null)}>Cancel</button></div>
                </div>
              )}
            </section>
            <section className="v-panel">
              <h3>Documents</h3>
              {d.document_versions.length === 0 ? <p className="v-muted">No uploads yet.</p> : d.document_versions.map((doc) => (
                <div key={doc.id} className="v-doc" data-doc-version={doc.version}>
                  <div style={{ flex: 1, minWidth: 220 }}>
                    <b>{doc.label}</b> <span className="v-mono">v{doc.version}</span> <span className="v-tag">{doc.status.replace('_', ' ')}</span>
                    <p className="v-small v-muted">{doc.filename} · {fmtDate(doc.uploaded_at)} · {doc.application_id ? 'Project drawer' : 'Profile drawer'}</p>
                    {doc.reason && <p className="v-ferr">Replacement requested: {doc.reason}</p>}
                    {!viewer && doc.status === 'under_review' && <input placeholder="Reason if requesting a replacement" value={docReason[doc.id] || ''} onChange={(e) => setDocReason({ ...docReason, [doc.id]: e.target.value })} />}
                  </div>
                  <div className="v-row">
                    <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => openDoc(doc)}>Open file</button>
                    {!viewer && doc.status === 'under_review' && <>
                      <button type="button" className="v-btn v-btn--red v-btn--sm" onClick={() => reviewDoc(doc, 'accepted')}>Accept</button>
                      <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => reviewDoc(doc, 'replace')}>Request replacement</button></>}
                  </div>
                </div>))}
            </section>
            <section className="v-panel">
              <h3>Review history</h3>
              <ol className="v-board">{d.history.map((h, i) => (
                <li key={i}><span className="v-small"><b>{h.action.replace(/_/g, ' ')}</b>{h.field ? ` · ${h.field}` : ''}{h.old || h.new ? `: ${h.old ?? '—'} → ${h.new ?? '—'}` : ''}{h.reason ? ` · “${h.reason}”` : ''}</span>
                  <span className="v-small v-muted">{h.actor} · {new Date(h.at).toLocaleString()}</span></li>))}</ol>
            </section>
          </>
        )}
      </div>
    </div>
  )
}

// ── Zoom Sessions ───────────────────────────────────────────────────────────
function RepPicker({ orgId, chosen, setChosen }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState([])
  useEffect(() => {
    const t = setTimeout(() => api.get(`/volt/ops/${orgId}/reps`, { params: { q } }).then((r) => setRows(r.items)).catch(() => setRows([])), 200)
    return () => clearTimeout(t)
  }, [q, orgId])
  const has = (id) => chosen.some((c) => c.rep_id === id)
  return (
    <div>
      <span className="v-label">Assigned applicants</span>
      <div className="v-row" style={{ justifyContent: 'flex-start', margin: '4px 0' }}>
        {chosen.length === 0 && <span className="v-muted v-small">Nobody assigned yet.</span>}
        {chosen.map((c) => <span key={c.rep_id} className="v-tag">{c.display_name} · {c.rep_code} <button type="button" aria-label={`Remove ${c.display_name}`} className="v-btn--link" style={{ border: 0, background: 'none', cursor: 'pointer' }} onClick={() => setChosen(chosen.filter((x) => x.rep_id !== c.rep_id))}>×</button></span>)}
      </div>
      <input placeholder="Find applicant by name or REP code" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find applicant" />
      <div className="v-matches" style={{ maxHeight: 180, overflowY: 'auto' }}>
        {rows.filter((r) => !has(r.rep_id)).slice(0, 8).map((r) => (
          <button type="button" key={r.rep_id} className="v-match" onClick={() => setChosen([...chosen, r])}><b>{r.display_name}</b><span className="v-mono">{r.rep_code}</span></button>))}
      </div>
    </div>
  )
}

function SessionForm({ orgId, projects, initial, onSaved, onCancel }) {
  const [v, setV] = useState(() => initial ? {
    kind: initial.kind, title: initial.title || '', project_id: initial.project?.id, starts_local: initial.starts_local,
    timezone: initial.timezone, duration_minutes: initial.duration_minutes, join_url: initial.join_url, instructions: initial.instructions || '',
  } : { kind: 'interview', title: '', project_id: projects.find((p) => p.recruiting)?.id || '', starts_local: '', timezone: 'America/Chicago', duration_minutes: 30, join_url: '', instructions: '' })
  const [chosen, setChosen] = useState(() => initial?.attendees || [])
  const [errs, setErrs] = useState({})
  const [err, setErr] = useState('')
  const up = (k) => (e) => setV({ ...v, [k]: e.target.value })
  async function save(e) {
    e.preventDefault(); setErrs({}); setErr('')
    const body = { ...v, duration_minutes: Number(v.duration_minutes), rep_ids: chosen.map((c) => c.rep_id) }
    try {
      if (initial) await api.patch(`/volt/ops/${orgId}/sessions/${initial.id}`, body)
      else await api.post(`/volt/ops/${orgId}/sessions`, body)
      onSaved()
    } catch (x) { setErr(msg(x)); setErrs(x.detail?.fields || {}) }
  }
  const fe = (k) => errs[k] && <span className="v-ferr">{errs[k]}</span>
  return (
    <form className="v-panel v-form" onSubmit={save} noValidate data-form="session">
      <h3>{initial ? 'Edit / reschedule session' : 'New Zoom session'}</h3>
      <div className="v-note-staff">
        Create the meeting in Zoom first, then paste its <b>participant join link</b> (zoom.us/j/…). VOLT shows that link only to the applicants you assign.
        Editing or canceling here updates the VOLT schedule only. <b>Make the same change to the meeting in Zoom.</b>
      </div>
      <div className="v-split">
        <label>Type<select value={v.kind} onChange={up('kind')}><option value="interview">Interview</option><option value="orientation">Orientation</option></select></label>
        <label>Project<select value={v.project_id} onChange={up('project_id')}>{projects.map((p) => <option key={p.id} value={p.id}>{p.short_name}{p.recruiting ? '' : ' (closed)'}</option>)}</select>{fe('project_id')}</label>
      </div>
      <label>Title (optional)<input value={v.title} onChange={up('title')} maxLength={140} /></label>
      <div className="v-split">
        <label>Date and time<input type="datetime-local" value={v.starts_local} onChange={up('starts_local')} aria-invalid={errs.starts_local ? 'true' : undefined} />{fe('starts_local')}</label>
        <label>Time zone<select value={v.timezone} onChange={up('timezone')}>{TZS.map((t) => <option key={t}>{t}</option>)}</select>{fe('timezone')}</label>
      </div>
      <div className="v-split">
        <label>Duration (minutes)<input type="number" min="5" max="480" value={v.duration_minutes} onChange={up('duration_minutes')} />{fe('duration_minutes')}</label>
        <label>Zoom participant join link<input type="url" value={v.join_url} onChange={up('join_url')} placeholder="https://zoom.us/j/12345678901?pwd=…" aria-invalid={errs.join_url ? 'true' : undefined} />{fe('join_url')}</label>
      </div>
      <label>Instructions for applicants<textarea value={v.instructions} onChange={up('instructions')} maxLength={2000} /></label>
      <RepPicker orgId={orgId} chosen={chosen} setChosen={setChosen} />
      <Err>{err}</Err>
      <div className="v-row" style={{ justifyContent: 'flex-start' }}>
        <button className="v-btn v-btn--red" type="submit">{initial ? 'Save changes' : 'Schedule session'}</button>
        <button className="v-btn v-btn--line" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

function Sessions({ orgId, ops }) {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')
  const [edit, setEdit] = useState(null)
  useEffect(() => { if (!ops.home) ops.load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const load = useCallback(() => api.get(`/volt/ops/${orgId}/sessions`).then((r) => setRows(r.items)).catch((e) => setErr(msg(e))), [orgId])
  useEffect(() => { load() }, [load])
  const viewer = ops.home?.access === 'viewer'
  async function cancel(s) {
    try { await api.post(`/volt/ops/${orgId}/sessions/${s.id}/cancel`, {}); load() } catch (e) { setErr(msg(e)) }
  }
  async function attend(s, repId, attendance) {
    try { await api.post(`/volt/ops/${orgId}/sessions/${s.id}/attendance`, { rep_id: repId, attendance }); load() } catch (e) { setErr(msg(e)) }
  }
  return (
    <>
      <div className="v-row"><h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>Zoom Sessions</h2>
        {!viewer && !edit && <button type="button" className="v-btn v-btn--red" onClick={() => setEdit('new')}>+ New session</button>}</div>
      <p className="v-small v-muted" style={{ marginBottom: 10 }}>Method: staff-supplied Zoom participant links. VOLT does not create, change or cancel meetings in Zoom.</p>
      <Err>{err}</Err>
      {edit && ops.home && <SessionForm orgId={orgId} projects={ops.home.projects} initial={edit === 'new' ? null : edit} onCancel={() => setEdit(null)} onSaved={() => { setEdit(null); load() }} />}
      {rows?.length === 0 && <p className="v-muted">No sessions yet.</p>}
      {rows?.map((s) => (
        <section key={s.id} className="v-panel" data-session={s.id}>
          <div className="v-row">
            <div><b>{s.kind === 'interview' ? 'Interview' : 'Orientation'}{s.title ? ` · ${s.title}` : ''}</b> <span className={`v-tag ${s.status === 'canceled' ? 'v-tag--black' : ''}`}>{s.status}</span>
              <p className="v-small">{s.project?.short_name} · {sessionTime(s.starts_at, s.timezone)} · {s.duration_minutes} min · {s.timezone}</p>
              <p className="v-small v-muted" style={{ wordBreak: 'break-all' }}>{s.join_url}</p></div>
            {!viewer && s.status !== 'canceled' && <div className="v-row">
              <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setEdit(s)}>Edit / reschedule</button>
              <button type="button" className="v-btn v-btn--black v-btn--sm" onClick={() => cancel(s)}>Cancel session</button></div>}
          </div>
          <table className="v-table" style={{ marginTop: 8 }}><thead><tr><th>Applicant</th><th>Attendance</th></tr></thead>
            <tbody>{s.attendees.map((a) => (
              <tr key={a.rep_id}><td>{a.display_name} <span className="v-mono">{a.rep_code}</span></td>
                <td>{viewer || s.status === 'canceled' ? a.attendance : (
                  <select aria-label={`Attendance for ${a.display_name}`} value={a.attendance} onChange={(e) => attend(s, a.rep_id, e.target.value)} style={{ width: 'auto' }}>
                    <option value="invited">Invited</option><option value="attended">Attended</option><option value="no_show">No-show</option></select>)}</td></tr>))}
            </tbody></table>
        </section>))}
    </>
  )
}

// ── Reps ────────────────────────────────────────────────────────────────────
function Reps({ orgId, ops }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const [purge, setPurge] = useState('')
  const load = useCallback(() => api.get(`/volt/ops/${orgId}/reps`, { params: { q } }).then((r) => setRows(r.items)).catch((e) => setErr(msg(e))), [orgId, q])
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t) }, [load])
  useEffect(() => { if (!ops.home) ops.load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const viewer = ops.home?.access === 'viewer'
  async function toggle(r, field) {
    try { await api.patch(`/volt/ops/${orgId}/reps/${r.rep_id}`, { [field]: !r[field] }); load() } catch (e) { setErr(msg(e)) }
  }
  async function purgeTests() {
    try { const r = await api.post(`/volt/ops/${orgId}/test-records/purge`, {}); setPurge(`Removed ${r.reps} test reps, ${r.applications} applications, ${r.documents} documents.`); load() } catch (e) { setErr(msg(e)) }
  }
  return (
    <>
      <div className="v-row"><h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>Reps & recruiter directory</h2>
        {ops.home?.access === 'owner' && <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={purgeTests}>Remove test records</button>}</div>
      <p className="v-small v-muted">Every person keeps one permanent rep ID. A rep in the directory can be selected as a recruiter by applicants.</p>
      <Err>{err}</Err>{purge && <div className="v-alert v-alert--ok">{purge}</div>}
      <input placeholder="Search by name or REP code" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search reps" style={{ maxWidth: 420 }} />
      <div className="v-panel v-scroll" style={{ marginTop: 10 }}>
        <table className="v-table"><thead><tr><th>Rep</th><th>Joined</th><th>In recruiter directory</th><th>Test record</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.rep_id}><td><b>{r.display_name}</b> <span className="v-mono">{r.rep_code}</span></td><td>{fmtDate(r.created_at)}</td>
              <td>{viewer ? (r.recruiter_active ? 'Yes' : 'No') : <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => toggle(r, 'recruiter_active')}>{r.recruiter_active ? 'Yes · remove' : 'No · add'}</button>}</td>
              <td>{viewer ? (r.is_test ? 'Yes' : 'No') : <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => toggle(r, 'is_test')}>{r.is_test ? 'Yes' : 'No'}</button>}</td></tr>))}
          </tbody></table>
      </div>
    </>
  )
}

// ── Projects & documents ────────────────────────────────────────────────────
function DocListEditor({ value, onSave, disabled }) {
  const [rows, setRows] = useState(value)
  const [label, setLabel] = useState('')
  useEffect(() => setRows(value), [JSON.stringify(value)]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div>
      {rows.length === 0 && <p className="v-muted v-small">No documents required.</p>}
      {rows.map((r, i) => <div key={r.key} className="v-doc"><span>{r.label}</span>{!disabled && <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button>}</div>)}
      {!disabled && <div className="v-row" style={{ marginTop: 8 }}>
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Document name, e.g. Photo ID" style={{ flex: 1, minWidth: 180 }} />
        <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => { if (label.trim()) { setRows([...rows, { label: label.trim(), key: '' }]); setLabel('') } }}>Add</button>
        <button type="button" className="v-btn v-btn--red v-btn--sm" onClick={() => onSave(rows)}>Save</button></div>}
    </div>
  )
}

function Projects({ orgId, ops }) {
  const [err, setErr] = useState('')
  useEffect(() => { ops.load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const h = ops.home
  if (!h) return <p className="v-mono">Loading…</p>
  const viewer = h.access === 'viewer'
  async function patch(p, body) { setErr(''); try { await api.patch(`/volt/ops/${orgId}/projects/${p.id}`, body); ops.load() } catch (e) { setErr(msg(e)) } }
  async function profileDocs(rows) { setErr(''); try { await api.patch(`/volt/ops/${orgId}/program`, { profile_required_docs: rows }); ops.load() } catch (e) { setErr(msg(e)) } }
  return (
    <>
      <h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>Projects & documents</h2>
      <p className="v-small v-muted">Opening or closing a project takes effect immediately. A closed project keeps its history and rejects new applications on the server.</p>
      <Err>{err}</Err>
      <div className="v-grid3" style={{ marginTop: 12 }}>
        {h.projects.map((p) => (
          <section key={p.id} className="v-panel" data-project={p.key}>
            <div className="v-row"><h3>{p.name}</h3><span className={`v-tag ${p.recruiting ? '' : 'v-tag--black'}`}>{p.recruiting ? 'Recruiting' : 'Recruiting closed'}</span></div>
            {!viewer && <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => patch(p, { status: p.recruiting ? 'closed' : 'open' })}>{p.recruiting ? 'Close recruiting' : 'Reopen recruiting'}</button>}
            <p className="v-label" style={{ marginTop: 12 }}>Required project documents</p>
            <DocListEditor value={p.required_docs} disabled={viewer} onSave={(rows) => patch(p, { required_docs: rows })} />
          </section>))}
      </div>
      <section className="v-panel">
        <h3>Profile drawer (every applicant)</h3>
        <DocListEditor value={h.program.profile_required_docs} disabled={viewer} onSave={profileDocs} />
      </section>
    </>
  )
}

export default function VoltOps() {
  const { orgId } = useParams()
  const ops = useOps(orgId)
  useEffect(() => { document.title = 'VOLT Operations' }, [])
  useEffect(() => { ops.load() }, [orgId]) // eslint-disable-line react-hooks/exhaustive-deps
  if (ops.err && !ops.home) {
    return (
      <div className="volt"><div className="v-wrap v-center"><div>
        <h2 style={{ marginBottom: 10 }}>VOLT Operations</h2>
        <div className="v-alert v-alert--err" role="alert">{ops.err}</div>
        <p style={{ marginTop: 12 }}><Link to="/workspaces">Back to Program Access</Link></p></div></div></div>
    )
  }
  return (
    <OpsShell orgId={orgId} home={ops.home}>
      <Routes>
        <Route index element={<Overview orgId={orgId} ops={ops} />} />
        <Route path="review" element={<ReviewQueue orgId={orgId} ops={ops} />} />
        <Route path="sessions" element={<Sessions orgId={orgId} ops={ops} />} />
        <Route path="reps" element={<Reps orgId={orgId} ops={ops} />} />
        <Route path="projects" element={<Projects orgId={orgId} ops={ops} />} />
        <Route path="*" element={<Navigate to={`/volt-ops/${orgId}`} replace />} />
      </Routes>
    </OpsShell>
  )
}
