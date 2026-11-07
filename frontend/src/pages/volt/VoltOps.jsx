/* VOLT OPERATIONS - recruiting staff and owner oversight (#535).
 *
 * Reached through Program Access (VOLT Marketing card) -> /volt-ops/:orgId.
 * Every number, list and file comes from /volt/ops/:orgId/*, which re-checks
 * the signed-in person's authority for this organization on every request.
 * Nothing here decides access; a viewer simply sees no write buttons, and the
 * server refuses the write anyway.
 */
import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, fetchObjectUrl } from '../../api/client'
import { Bolt } from './VoltPortal'
import { Modal } from './VoltDialogs'
import { zoomWhen } from './VoltMyApplication'
import { fmtDate, useVoltTitle } from './voltApi'
import './volt.css'

const STAGES = [['draft', 'Draft'], ['submitted', 'Submitted'], ['needs_update', 'Needs update'], ['approved', 'Approved'], ['started', 'Started'], ['withdrawn', 'Withdrawn / closed']]
const STAGE = Object.fromEntries(STAGES)
const REACHED = [['created', 'Application started'], ['submitted', 'Submitted'], ['approved', 'Approved'], ['started', 'Started']]
const ANSWER = { yes: 'Yes', no: 'No', details: 'Would like details', explain: 'It depends' }
const OUTREACH = { yes: 'Outreach experience', some: 'Some outreach experience', no: 'New to outreach' }
const TIME_AWAY = { '1_2_weeks': '1–2 weeks', '3_4_weeks': '3–4 weeks', '1_3_months': '1–3 months', '3_plus_months': '3+ months', discuss: 'Wants to discuss' }
const D2D = { yes: 'Door-to-door', some: 'Some field sales', no: 'New' }

function travelCell(r) {
  if (r.time_away) return `Away: ${TIME_AWAY[r.time_away] || r.time_away}${r.d2d_experience ? ` · ${D2D[r.d2d_experience] || r.d2d_experience}` : ''}`
  if (r.willing_travel) return `Travel: ${ANSWER[r.willing_travel] || r.willing_travel}`
  if (r.outreach_experience) return `${OUTREACH[r.outreach_experience] || r.outreach_experience}${r.languages ? ` · ${r.languages}` : ''}`
  return '—'
}

function shownQ(q, ans) { return !q.show_if || q.show_if[1].includes(ans[q.show_if[0]] || '') }
function answerText(q, v) {
  if (!v) return null
  if (q.type === 'choice') return (q.options.find((o) => o[0] === v) || [v, v])[1]
  if (q.type === 'date') return new Date(v.slice(0, 10) + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  return v
}

// The project's answers, grouped by the application's steps; the first Fiber
// form's answers (legacy) appear under "Earlier answers" when present.
function Answers({ app }) {
  const ans = app.answers || {}
  const qs = app.questions || []
  const sections = app.sections && app.sections.length ? app.sections : [{ id: 'project', label: 'Project questions' }]
  const earlier = qs.filter((q) => q.legacy && ans[q.key])
  return (
    <div data-answers>
      {sections.map((sec) => {
        const list = qs.filter((q) => !q.legacy && (q.section || 'project') === sec.id && shownQ(q, ans))
        if (!list.length) return null
        return (
          <div key={sec.id} data-answer-section={sec.id}>
            {sec.id !== 'project' && <p className="v-label" style={{ marginTop: 10 }}>{sec.label}</p>}
            <dl className="v-review">{list.map((q) => <Fragment key={q.key}><dt>{q.label}</dt><dd>{answerText(q, ans[q.key]) || <span className="v-muted">—</span>}</dd></Fragment>)}</dl>
          </div>
        )
      })}
      {earlier.length > 0 && (
        <div data-answer-section="earlier">
          <p className="v-label" style={{ marginTop: 10 }}>Earlier answers</p>
          <dl className="v-review">{earlier.map((q) => <Fragment key={q.key}><dt>{q.label}</dt><dd>{answerText(q, ans[q.key])}</dd></Fragment>)}</dl>
        </div>
      )}
    </div>
  )
}
const ACTION_LABEL = { approved: 'Approve', needs_update: 'Return for update', started: 'Mark Started', withdrawn: 'Withdraw / close' }
const REOPEN_LABEL = 'Reopen application'
const ZOOM_STATE = { waiting: 'Not yet', choose: 'Can choose a time', booked: 'Zoom booked' }
const AVAIL = { open: ['Open', 'v-tag--ok'], full: ['Full · closed', 'v-tag--black'], closed: ['Closed by staff', 'v-tag--line'],
  canceled: ['Canceled', 'v-tag--black'], past: ['Past', 'v-tag--line'], assigned_only: ['Assigned only', 'v-tag--line'] }

function useEscape(onClose) {
  useEffect(() => {
    const k = (e) => { if (e.key === 'Escape' && !document.querySelector('.v-modal-bg')) onClose() }
    document.addEventListener('keydown', k)
    return () => document.removeEventListener('keydown', k)
  }, [onClose])
}
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
          <NavLink className="v-tab" to={`${base}/sessions`}>Zoom availability</NavLink>
          <NavLink className="v-tab" to={`${base}/people`}>People & Access</NavLink>
          <NavLink className="v-tab" to={`${base}/talent`}>Talent Pool</NavLink>
          <NavLink className="v-tab" to={`${base}/email`}>Support email</NavLink>
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
      {showReached && <label>Location<input value={f.location || ''} placeholder="City, state, ZIP or region" onChange={up('location')} /></label>}
      {showReached && <label>Travel<select value={f.travel || ''} onChange={up('travel')}>
        <option value="">Any answer</option>
        <option value="long">Can be away 1 month or more</option><option value="short">Can be away up to 4 weeks</option><option value="discuss">Wants to talk it through</option>
        <option value="yes">Earlier form: willing to travel</option><option value="no">Earlier form: not willing</option><option value="details">Earlier form: would like details</option></select></label>}
      {showReached && <label>Experience<select value={f.experience || ''} onChange={up('experience')}>
        <option value="">Any</option><option value="d2d">Has door-to-door experience</option><option value="has">Has relevant or listed experience</option><option value="none">New / no previous experience</option></select></label>}
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
      <label className="v-check" style={{ margin: '0 0 8px' }}><input type="checkbox" checked={f.include_archived === '1'}
        onChange={(e) => { const x = { ...f }; if (e.target.checked) x.include_archived = '1'; else delete x.include_archived; setSp(clean(x)) }} data-include-archived /> Include archived applicants</label>
      {f.recruiter && <p className="v-small">Recruiter: <b>{recruiterName ? `${recruiterName.display_name} · ${recruiterName.rep_code}` : f.recruiter}</b> <button type="button" className="v-btn v-btn--link" onClick={() => { const x = { ...f }; delete x.recruiter; setSp(clean(x)) }}>clear</button></p>}
      <Err>{err}</Err>
      <p className="v-small v-muted" data-count={list?.count}>{list ? `${list.count} application${list.count === 1 ? '' : 's'} · dates filter on "${list.reached_label}". The CSV is exactly this list.` : 'Loading…'}</p>
      <div className="v-panel v-scroll">
        <table className="v-table" data-table="queue">
          <thead><tr><th>Applicant</th><th>Project</th><th>Stage</th><th>Location</th><th>Travel / experience</th><th>Project recruiter</th><th>Original recruiter</th><th>Updated</th><th /></tr></thead>
          <tbody>
            {list?.items.length === 0 && <tr><td colSpan="9" className="v-muted">No matching applications.</td></tr>}
            {list?.items.map((r) => (
              <tr key={r.application_id}>
                <td><b>{r.applicant}</b><br /><span className="v-mono">{r.rep_code}</span>{r.is_test && <> <span className="v-tag v-tag--line">Test</span></>}{r.archived && <> <span className="v-tag v-tag--line">Archived</span></>}</td>
                <td>{r.project}<br /><span className="v-small v-muted">{r.reference}</span></td>
                <td><span className="v-tag">{r.status_label}</span>
                  {r.zoom ? <><br /><span className="v-small" data-zoom-booked>Zoom {r.zoom.kind}: {zoomWhen(r.zoom.starts_at, r.zoom.timezone)}</span></>
                    : r.schedule_stage ? <><br /><span className="v-small v-muted">Zoom {r.schedule_stage} step open</span></> : null}</td>
                <td>{[r.city, r.state].filter(Boolean).join(', ') || '—'}</td>
                <td>{travelCell(r)}</td>
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
  const [busyAct, setBusyAct] = useState('')
  const [closing, setClosing] = useState(false)
  async function act(status, why) {
    if (busyAct) return
    setErr(''); setBusyAct(status)
    try {
      await api.post(`/volt/ops/${orgId}/applications/${appId}/status`, { status, reason: status === 'needs_update' ? reason : (why || undefined) })
      setReason(''); setClosing(false); await load()
    } catch (e) { setErr(msg(e)) } finally { setBusyAct('') }
  }
  const [stage, setStage] = useState('')
  useEffect(() => { if (d) setStage(d.application.schedule_stage || '') }, [d])
  async function saveStage() {
    if (busyAct) return
    setErr(''); setBusyAct('stage')
    try { await api.post(`/volt/ops/${orgId}/applications/${appId}/scheduling`, { stage: stage || null }); await load() } catch (e) { setErr(msg(e)) } finally { setBusyAct('') }
  }
  useEscape(onClose)
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
            <p className="v-mono">{d.profile.rep_code} · {d.application.reference}{d.profile.is_test ? ' · TEST RECORD' : ''}</p>
            {d.person && (d.person.access !== 'active' || d.person.archived) && (
              <p className="v-small" data-person-state>{d.person.access !== 'active' && <span className="v-tag">VOLT access {d.person.access_label}</span>} {d.person.archived && <span className="v-tag v-tag--line">Archived</span>}</p>)}
            <p className="v-small" style={{ marginTop: 6 }}>{d.profile.email} · {d.profile.phone} · DOB {d.profile.date_of_birth}</p>
            <section className="v-panel" style={{ marginTop: 12 }}>
              <div className="v-row"><h3>{d.application.project.name}</h3><span className="v-tag" data-stage={d.application.status}>{d.application.status_label}</span></div>
              {d.application.return_reason && d.application.status === 'needs_update' && <p className="v-ferr">Returned: {d.application.return_reason}</p>}
              <Answers app={d.application} />
              {d.missing.length > 0 && <p className="v-small v-muted">Applicant still needs: {d.missing.join('; ')}</p>}
              {!viewer && d.next_actions.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  {d.next_actions.includes('needs_update') && <label>Reason for the applicant (required to return)<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Exactly what to change" /></label>}
                  <div className="v-row" style={{ justifyContent: 'flex-start', marginTop: 8 }}>
                    {d.next_actions.filter((s) => s !== 'withdrawn').map((s) => (
                      <button key={s} type="button" className={`v-btn v-btn--sm ${s === 'needs_update' ? 'v-btn--line' : 'v-btn--red'}`} disabled={!!busyAct}
                              onClick={() => act(s)} data-action={s}>{busyAct === s ? 'Saving…' : d.application.status === 'withdrawn' ? REOPEN_LABEL : ACTION_LABEL[s]}</button>))}
                    {d.next_actions.includes('withdrawn') && <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setClosing(true)} disabled={!!busyAct} data-action="withdrawn">Withdraw / close…</button>}
                  </div>
                </div>
              )}
              {d.application.status === 'withdrawn' && <p className="v-small v-muted">Closed {fmtDate(d.application.closed_at)} from {STAGE[d.application.closed_from_status] || 'Submitted'}. Reopening returns it there. Access is not affected.</p>}
              {d.other_applications.length > 0 && <p className="v-small v-muted" style={{ marginTop: 8 }}>Also applied: {d.other_applications.map((o) => `${o.project} (${STAGE[o.status]})`).join(', ')}</p>}
            </section>
            <section className="v-panel" data-zoom-step>
              <h3>Zoom step</h3>
              <p className="v-small">Applicant now: <b>{ZOOM_STATE[d.zoom?.state] || 'Not yet'}</b>{d.application.schedule_stage ? ` · ${d.application.schedule_stage}` : ''}</p>
              {(d.zoom?.sessions || []).filter((x) => x.status !== 'canceled').map((x) => (
                <p key={x.id} className="v-small">Booked: <b>{zoomWhen(x.starts_at, x.timezone)}</b> · {x.kind} · attendance {x.attendance.replace('_', ' ')}</p>))}
              {!viewer && (
                <div className="v-row" style={{ justifyContent: 'flex-start', alignItems: 'flex-end' }}>
                  <label style={{ minWidth: 220 }}>Applicant may choose a Zoom time for
                    <select value={stage} onChange={(e) => setStage(e.target.value)} data-stage-select disabled={['draft', 'withdrawn'].includes(d.application.status)}>
                      <option value="">Not yet (waiting)</option><option value="interview">Interview</option><option value="orientation">Orientation</option></select></label>
                  <button type="button" className="v-btn v-btn--red v-btn--sm" onClick={saveStage} disabled={!!busyAct || stage === (d.application.schedule_stage || '') || ['draft', 'withdrawn'].includes(d.application.status)} data-save-stage>
                    {busyAct === 'stage' ? 'Saving…' : 'Save Zoom step'}</button>
                </div>)}
              <p className="v-small v-muted">Only open times for this project and step are offered. Turning the step off keeps an existing booking; the application's review status is unchanged.</p>
            </section>
            {d.hiring && <HiringFile d={d} />}
            <section className="v-panel">
              <h3>Recruiter attribution <span className="v-small v-muted">(history; referrals are paused)</span></h3>
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
            {closing && (
              <ConfirmAction title="Withdraw / close this application?" busy={busyAct === 'withdrawn'} onClose={() => setClosing(false)}
                             onConfirm={(why) => act('withdrawn', why)} confirmLabel="Withdraw / close"
                             body={<><b>{d.profile.first_name} {d.profile.last_name}</b> · {d.application.project.name} ({d.application.reference}). The application is closed and any Zoom booking for it is released. Records are kept, it can be reopened, and VOLT sign-in is not changed.</>} />)}
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

function monthLabel(m) {
  if (!m) return ''
  const [y, mo] = m.split('-')
  return new Date(Number(y), Number(mo) - 1, 1).toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
}

// Person-level file. The rows this project's flow asks for always show; other
// rows (filled in for another project, or on an earlier form) show only when
// they have something in them.
function HiringFile({ d }) {
  const h = d.hiring
  const opts = Object.fromEntries(d.availability_options || [])
  const pf = d.application?.profile_fields || ['location', 'regions', 'earliest_start', 'availability', 'work_history', 'relevant_experience', 'additional_info']
  const avail = (h.availability?.types || []).map((t) => opts[t] || t).join(', ')
  const rows = [
    ['location', 'Location', [h.city, h.state, h.zip_code].filter(Boolean).join(', ')],
    ['regions', 'Preferred regions', h.preferred_regions],
    ['earliest_start', 'Earliest start', h.earliest_start],
    ['availability', 'Availability', avail ? `${avail}${h.availability?.notes ? ` · ${h.availability.notes}` : ''}` : ''],
    ['work_history', 'Work history', h.no_experience ? 'No previous work experience' : (h.work_history || []).length ? (
      <ul style={{ margin: 0, paddingLeft: 18 }}>{h.work_history.map((r, i) => (
        <li key={i}><b>{r.title || '—'}</b>, {r.employer || '—'} · {monthLabel(r.start)} – {r.current ? 'Present' : monthLabel(r.end)}{r.summary ? <><br /><span className="v-small">{r.summary}</span></> : null}</li>))}</ul>) : ''],
    ['relevant_experience', 'Relevant experience', h.relevant_experience],
    ['additional_info', 'Anything else', h.additional_info],
  ].filter(([k, , v]) => pf.includes(k) || v)
  return (
    <section className="v-panel" data-hiring>
      <h3>Hiring file</h3>
      <dl className="v-review">
        {rows.map(([k, label, v]) => <Fragment key={k}><dt>{label}</dt><dd>{v || '—'}</dd></Fragment>)}
      </dl>
    </section>
  )
}

// ── Talent Pool ─────────────────────────────────────────────────────────────
function TalentPool({ orgId }) {
  const [f, setF] = useState({})
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const seq = useRef(0)
  useEffect(() => {
    const n = ++seq.current
    const t = setTimeout(() => api.get(`/volt/ops/${orgId}/talent-pool`, { params: clean(f) })
      .then((x) => { if (n === seq.current) setData(x) }).catch((e) => setErr(msg(e))), 200)
    return () => clearTimeout(t)
  }, [orgId, JSON.stringify(f)]) // eslint-disable-line react-hooks/exhaustive-deps
  const up = (k) => (e) => setF({ ...f, [k]: e.target.value })
  async function exportCsv() {
    setBusy(true)
    try {
      const url = await fetchObjectUrl(`/volt/ops/${orgId}/talent-pool.csv?${new URLSearchParams(clean(f))}`)
      const a = document.createElement('a'); a.href = url; a.download = 'volt-marketing-talent-pool.csv'; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 2000)
    } catch (e) { setErr(msg(e)) } finally { setBusy(false) }
  }
  return (
    <>
      <div className="v-row"><h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>Talent Pool</h2>
        <button type="button" className="v-btn v-btn--black" onClick={exportCsv} disabled={busy}>{busy ? 'Exporting…' : 'Export CSV ↓'}</button></div>
      <p className="v-small v-muted">{data?.note || 'People who asked to hear about future opportunities.'}</p>
      <div className="v-panel v-filters">
        <label>Search<input value={f.q || ''} placeholder="Name or email" onChange={up('q')} /></label>
        <label>Interest<select value={f.interest || ''} onChange={up('interest')}><option value="">Any interest</option>
          {(data?.interests || []).map((i) => <option key={i.key} value={i.key}>{i.label}</option>)}</select></label>
        <label>From<input type="date" value={f.date_from || ''} onChange={up('date_from')} /></label>
        <label>To<input type="date" value={f.date_to || ''} onChange={up('date_to')} /></label>
        <div style={{ display: 'grid', gap: 4 }}>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontWeight: 600 }}><input type="checkbox" style={{ width: 'auto', minHeight: 0, margin: 0 }} checked={f.notify === '1'} onChange={(e) => setF({ ...f, notify: e.target.checked ? '1' : '' })} />Emails welcome only</label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontWeight: 600 }}><input type="checkbox" style={{ width: 'auto', minHeight: 0, margin: 0 }} checked={f.include_test === '1'} onChange={(e) => setF({ ...f, include_test: e.target.checked ? '1' : '' })} />Include test records</label>
        </div>
      </div>
      <Err>{err}</Err>
      <p className="v-small v-muted" data-talent-count={data?.count}>{data ? `${data.count} ${data.count === 1 ? 'person' : 'people'}. The CSV is exactly this list.` : 'Loading…'}</p>
      <div className="v-panel v-scroll">
        <table className="v-table" data-table="talent">
          <thead><tr><th>Name</th><th>Email</th><th>Interests</th><th>Future emails</th><th>Referred by</th><th>Signed up</th><th>Times</th></tr></thead>
          <tbody>
            {data?.items.length === 0 && <tr><td colSpan="7" className="v-muted">Nobody yet.</td></tr>}
            {data?.items.map((r) => (
              <tr key={r.id}>
                <td><b>{r.first_name} {r.last_name}</b>{r.is_test && <> <span className="v-tag v-tag--line">Test</span></>}</td>
                <td>{r.email}</td><td>{r.interest_labels.join(', ') || '—'}</td>
                <td>{r.notify_opt_in ? 'Yes' : 'No'}</td>
                <td>{r.referrer ? `${r.referrer.display_name} · ${r.referrer.rep_code}` : '—'}</td>
                <td>{fmtDate(r.created_at)}</td><td>{r.submit_count}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
    </>
  )
}

// ── Support email: connection, sent email, support inbox ───────────────────
// The mailbox's Microsoft sign-in lives on the server only. Incoming mail is
// untrusted: it is shown as plain text (React escapes it) and attachments are
// downloads, never previews.
const MAIL_KIND = { registered: 'Account ready', receipt: 'Application received', needs_update: 'Update requested',
  doc_replace: 'Replace a document', approved: 'Approved', started: 'Started', password_reset: 'Password reset' }
const MAIL_STATE = { sent: 'Sent', queued: 'Sending', failed: 'Failed', skipped: 'Not sent (test address)' }
const CONNECT_ERR = { wrong_account: 'Microsoft signed in as a different account, so nothing was saved. Sign in as the support mailbox itself.',
  not_authorized: 'Your VOLT access cannot connect the mailbox.', access_denied: 'The Microsoft permission request was declined.' }

function fmtWhen(iso) { return iso ? new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—' }

function SupportEmail({ orgId }) {
  const [params, setParams] = useSearchParams()
  const [st, setSt] = useState(null)
  const [threads, setThreads] = useState(null)
  const [show, setShow] = useState('open')
  const [open, setOpen] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const load = useCallback(() => Promise.all([
    api.get(`/volt/ops/${orgId}/email`).then(setSt),
    api.get(`/volt/ops/${orgId}/inbox`, { params: { show } }).then((x) => setThreads(x.threads)),
  ]).catch((e) => setErr(msg(e))), [orgId, show])
  useEffect(() => { load() }, [load])
  const connected = params.get('mailbox_connected')
  const connectErr = params.get('mailbox_error')
  async function connect() {
    setBusy('connect'); setErr('')
    try { const r = await api.post(`/volt/ops/${orgId}/email/connect`); window.location.assign(r.authorization_url) }
    catch (e) { setErr(msg(e)); setBusy('') }
  }
  async function pollNow() {
    setBusy('poll'); setErr('')
    try { const r = await api.post(`/volt/ops/${orgId}/email/poll-now`); setSt(r.status); await load() }
    catch (e) { setErr(msg(e)) } finally { setBusy('') }
  }
  const state = st?.state
  return (
    <>
      <div className="v-row"><h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>Support email</h2>
        {st && state !== 'not_connected' && <button type="button" className="v-btn v-btn--line" onClick={pollNow} disabled={!!busy}>{busy === 'poll' ? 'Checking…' : 'Check for new mail'}</button>}</div>
      {connected && <div className="v-alert v-alert--ok" role="status">Connected {connected}. <button type="button" className="v-btn v-btn--link" onClick={() => setParams({})}>Dismiss</button></div>}
      {connectErr && <div className="v-alert v-alert--err" role="alert">{CONNECT_ERR[connectErr] || 'The mailbox could not be connected. Try again.'} <button type="button" className="v-btn v-btn--link" onClick={() => setParams({})}>Dismiss</button></div>}
      <Err>{err}</Err>
      <section className="v-panel" data-mailbox-state={state || 'loading'}>
        {!st ? <p className="v-muted">Loading…</p> : (
          <div className="v-mailbox">
            <div>
              <span className="v-mono">Sends and receives as</span>
              <h3>{st.sender_name} &lt;{st.expected_address}&gt;</h3>
              <p className="v-small">
                {state === 'connected' && <><span className="v-tag">● Connected</span> since {fmtWhen(st.connected_at)}{st.connected_by ? ` by ${st.connected_by}` : ''}. Last checked {fmtWhen(st.last_polled_at)}.</>}
                {state === 'reconnect' && <><span className="v-tag v-tag--black">Reconnect needed</span> {st.last_error}</>}
                {state === 'not_connected' && <><span className="v-tag v-tag--line">Not connected</span> Applicant emails are not sent and support mail is not read until the mailbox is connected.</>}
              </p>
              {st.microsoft_display_name && st.microsoft_display_name !== st.sender_name &&
                <p className="v-small v-muted">Microsoft 365 shows this mailbox's name as "{st.microsoft_display_name}". Recipients may see that name; change it in the Microsoft 365 admin to "{st.sender_name}".</p>}
              <p className="v-small v-muted">Permissions: read mail, send mail, stay signed in. Nothing here deletes or moves mail.</p>
            </div>
            {st.can_connect && state !== 'connected' &&
              <button type="button" className="v-btn v-btn--red" onClick={connect} disabled={!!busy} data-connect>{busy === 'connect' ? 'Opening Microsoft…' : state === 'reconnect' ? 'Reconnect support mailbox' : 'Connect support mailbox'}</button>}
          </div>)}
      </section>

      <div className="v-row" style={{ marginTop: 18 }}><h3>Support inbox</h3>
        <label className="v-small" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input type="checkbox" style={{ width: 'auto', minHeight: 0, margin: 0 }} checked={show === 'all'} onChange={(e) => setShow(e.target.checked ? 'all' : 'open')} />Show answered</label></div>
      <div className="v-panel v-scroll">
        <table className="v-table" data-table="inbox">
          <thead><tr><th>From</th><th>Subject</th><th>Messages</th><th>Last</th><th></th></tr></thead>
          <tbody>
            {threads?.length === 0 && <tr><td colSpan="5" className="v-muted">{show === 'open' ? 'Nothing waiting.' : 'No support mail yet.'}</td></tr>}
            {threads?.map((t) => (
              <tr key={t.conversation_id}>
                <td><b>{t.from_name || t.from}</b>{t.rep_code && <> · {t.rep_code}</>}<br /><span className="v-small v-muted">{t.from}</span></td>
                <td>{t.subject || '(no subject)'}{t.kind === 'bounce' && <> <span className="v-tag v-tag--black">Delivery failure</span></>}{t.kind === 'auto_reply' && <> <span className="v-tag v-tag--line">Auto-reply</span></>}{t.has_attachments && ' 📎'}</td>
                <td>{t.count}</td><td>{fmtWhen(t.last_at)}{t.open ? '' : ' · Answered'}</td>
                <td><button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setOpen(t.conversation_id)}>Open</button></td>
              </tr>))}
          </tbody>
        </table>
      </div>

      <h3 style={{ marginTop: 18 }}>Applicant emails sent</h3>
      <p className="v-small v-muted">Account, application, update and status emails. Each is sent once; failures retry automatically.</p>
      <div className="v-panel v-scroll">
        <table className="v-table" data-table="sent">
          <thead><tr><th>Email</th><th>To</th><th>Result</th><th>When</th></tr></thead>
          <tbody>
            {st?.recent?.length === 0 && <tr><td colSpan="4" className="v-muted">Nothing sent yet.</td></tr>}
            {st?.recent?.map((e) => (
              <tr key={e.id}>
                <td><b>{MAIL_KIND[e.kind] || e.kind}</b>{e.is_test && <> <span className="v-tag v-tag--line">Test</span></>}<br /><span className="v-small v-muted">{e.subject}</span></td>
                <td>{e.to}</td>
                <td>{MAIL_STATE[e.state] || e.state}{e.error && <><br /><span className="v-small v-muted">{e.error}</span></>}</td>
                <td>{fmtWhen(e.sent_at || e.created_at)}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
      {open && <SupportThread orgId={orgId} conv={open} canWrite={st?.can_connect} onClose={() => { setOpen(null); load() }} />}
    </>
  )
}

function SupportThread({ orgId, conv, canWrite, onClose }) {
  const [t, setT] = useState(null)
  const [err, setErr] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api.get(`/volt/ops/${orgId}/inbox/thread`, { params: { conversation: conv } }).then(setT).catch((e) => setErr(msg(e)))
  }, [orgId, conv])
  async function send() {
    setBusy(true); setErr('')
    try { setT(await api.post(`/volt/ops/${orgId}/inbox/reply`, { conversation_id: conv, text })); setText('') }
    catch (e) { setErr(msg(e)) } finally { setBusy(false) }
  }
  async function handled(v) {
    try { setT(await api.post(`/volt/ops/${orgId}/inbox/handled`, { conversation_id: conv, handled: v })) } catch (e) { setErr(msg(e)) }
  }
  async function download(m, a) {
    try {
      const url = await fetchObjectUrl(`/volt/ops/${orgId}/inbox/messages/${m.id}/attachments/${encodeURIComponent(a.id)}`)
      const el = document.createElement('a'); el.href = url; el.download = a.name; el.click()
      setTimeout(() => URL.revokeObjectURL(url), 2000)
    } catch (e) { setErr(msg(e)) }
  }
  const last = t?.messages?.[t.messages.length - 1]
  const canReply = canWrite && t?.messages?.some((m) => m.direction === 'in' && m.kind !== 'bounce')
  return (
    <div className="v-drawer-bg" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <aside className="v-drawer" role="dialog" aria-modal="true" aria-label="Support conversation" data-thread>
        <div className="v-row"><h3>{t?.messages?.[0]?.subject || 'Conversation'}</h3><button type="button" className="v-btn v-btn--line v-btn--sm" onClick={onClose}>Close</button></div>
        <Err>{err}</Err>
        {!t ? <p className="v-muted">Loading…</p> : t.messages.map((m) => (
          <article key={m.id} className={`v-msg v-msg--${m.direction}`} data-direction={m.direction}>
            <div className="v-small"><b>{m.direction === 'out' ? `${m.from_name || 'VOLT Marketing'}${m.staff ? ` (${m.staff})` : ''}` : (m.from_name || m.from)}</b>
              {m.direction === 'in' && <span className="v-muted"> &lt;{m.from}&gt;</span>}{m.rep_code && <> · {m.rep_code}</>} · {fmtWhen(m.received_at)}</div>
            <div className="v-msg-body">{m.body_text}</div>
            {m.attachments?.length > 0 && <div className="v-small">Attachments (downloads only; open with care):{' '}
              {m.attachments.map((a) => <button key={a.id} type="button" className="v-btn v-btn--link" onClick={() => download(m, a)}>{a.name} ({Math.max(1, Math.round((a.size || 0) / 1024))} KB)</button>)}</div>}
          </article>))}
        {canReply && (
          <div className="v-panel" style={{ marginTop: 12 }}>
            <label className="v-small" htmlFor="v-reply"><b>Reply as VOLT Marketing</b> (sent from the support mailbox, in this thread, to {[...(t?.messages || [])].reverse().find((m) => m.direction === 'in')?.from})</label>
            <textarea id="v-reply" rows={6} value={text} maxLength={10000} onChange={(e) => setText(e.target.value)} />
            <div className="v-row" style={{ justifyContent: 'flex-end' }}>
              <button type="button" className="v-btn v-btn--line" onClick={() => handled(t.open)}>{t.open ? 'Mark answered' : 'Reopen'}</button>
              <button type="button" className="v-btn v-btn--red" onClick={send} disabled={busy || !text.trim()} data-send-reply>{busy ? 'Sending…' : 'Send reply'}</button>
            </div>
          </div>)}
        {last && !canReply && canWrite && <button type="button" className="v-btn v-btn--line" onClick={() => handled(t.open)}>{t.open ? 'Mark handled' : 'Reopen'}</button>}
      </aside>
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
    capacity: initial.capacity ?? '', booking_open: !!initial.booking_open,
    cutoff_hours: initial.booking_cutoff_minutes != null ? String(initial.booking_cutoff_minutes / 60) : '',
  } : { kind: 'interview', title: '', project_id: projects.find((p) => p.recruiting)?.id || '', starts_local: '', timezone: 'America/Chicago',
    duration_minutes: 30, join_url: '', instructions: '', capacity: 1, booking_open: true, cutoff_hours: '' })
  const [chosen, setChosen] = useState(() => (initial?.attendees || []).filter((a) => a.source !== 'booked'))
  const [assign, setAssign] = useState(() => (initial?.attendees || []).some((a) => a.source !== 'booked'))
  const [errs, setErrs] = useState({})
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const up = (k) => (e) => setV({ ...v, [k]: e.target.value })
  async function save(e) {
    e.preventDefault()
    if (busy) return
    setErrs({}); setErr('')
    const cap = String(v.capacity).trim() === '' ? null : Number(v.capacity)
    if (cap !== null && (!Number.isInteger(cap) || cap < 1)) { setErrs({ capacity: 'Capacity must be a whole number, at least 1.' }); return }
    const body = { kind: v.kind, title: v.title, project_id: v.project_id, starts_local: v.starts_local, timezone: v.timezone,
      duration_minutes: Number(v.duration_minutes), join_url: v.join_url, instructions: v.instructions, capacity: cap,
      booking_open: cap !== null && !!v.booking_open,
      booking_cutoff_minutes: v.cutoff_hours === '' ? null : Math.round(Number(v.cutoff_hours) * 60) }
    if (assign || initial) body.rep_ids = assign ? chosen.map((c) => c.rep_id) : []
    setBusy(true)
    try {
      if (initial) await api.patch(`/volt/ops/${orgId}/sessions/${initial.id}`, body)
      else await api.post(`/volt/ops/${orgId}/sessions`, body)
      onSaved(initial ? 'Saved in VOLT. If the time changed, booked applicants were told. Make the same change in Zoom.' : 'Published in VOLT.')
    } catch (x) { setErr(msg(x)); setErrs(x.detail?.fields || {}) } finally { setBusy(false) }
  }
  const fe = (k) => errs[k] && <span className="v-ferr">{errs[k]}</span>
  return (
    <form className="v-panel v-form" onSubmit={save} noValidate data-form="session">
      <h3>{initial ? 'Edit / reschedule session' : 'Add a session'}</h3>
      <div className="v-note-staff">
        Create the meeting in Zoom first, then paste its <b>participant join link</b> (zoom.us/j/…). Host/start links are refused.
        VOLT shows the link only to applicants booked or assigned. Saving here changes <b>VOLT availability only</b>; VOLT does not create, move or cancel the meeting in Zoom.
      </div>
      <div className="v-split">
        <label>Type<select value={v.kind} onChange={up('kind')}><option value="interview">Interview</option><option value="orientation">Orientation</option></select></label>
        <label>Opportunity<select value={v.project_id} onChange={up('project_id')}>{projects.map((p) => <option key={p.id} value={p.id}>{p.short_name}{p.recruiting ? '' : ' (closed)'}</option>)}</select>{fe('project_id')}</label>
      </div>
      <label>Title<input value={v.title} onChange={up('title')} maxLength={140} placeholder="e.g. ACA outreach interview" /></label>
      <div className="v-split">
        <label>Date and start time<input type="datetime-local" value={v.starts_local} onChange={up('starts_local')} aria-invalid={errs.starts_local ? 'true' : undefined} required />{fe('starts_local')}</label>
        <label>Time zone<select value={v.timezone} onChange={up('timezone')}>{TZS.map((t) => <option key={t}>{t}</option>)}</select>{fe('timezone')}</label>
      </div>
      <div className="v-split">
        <label>Duration (minutes)<input type="number" min="5" max="480" value={v.duration_minutes} onChange={up('duration_minutes')} />{fe('duration_minutes')}</label>
        <label>Capacity (people)<input type="number" min="1" max="500" value={v.capacity} onChange={up('capacity')} aria-invalid={errs.capacity ? 'true' : undefined} data-capacity />{fe('capacity')}</label>
      </div>
      <div className="v-split">
        <label>Zoom participant join link<input type="url" value={v.join_url} onChange={up('join_url')} placeholder="https://zoom.us/j/12345678901?pwd=…" aria-invalid={errs.join_url ? 'true' : undefined} />{fe('join_url')}</label>
        <label>Booking cutoff (hours before start, optional)<input type="number" min="0" max="336" step="0.5" value={v.cutoff_hours} onChange={up('cutoff_hours')} />{fe('booking_cutoff_minutes')}</label>
      </div>
      <label className="v-check"><input type="checkbox" checked={!!v.booking_open} onChange={(e) => setV({ ...v, booking_open: e.target.checked })} data-booking-open /> Open for booking (eligible applicants can choose this time)</label>
      <label>Instructions for applicants (optional)<textarea value={v.instructions} onChange={up('instructions')} maxLength={2000} /></label>
      <label className="v-check"><input type="checkbox" checked={assign} onChange={(e) => setAssign(e.target.checked)} /> Also assign specific applicants directly</label>
      {assign && <RepPicker orgId={orgId} chosen={chosen} setChosen={setChosen} />}
      <Err>{err}</Err>
      <div className="v-row" style={{ justifyContent: 'flex-start' }}>
        <button className="v-btn v-btn--red" type="submit" disabled={busy}>{busy ? 'Saving…' : initial ? 'Save changes' : 'Publish available date'}</button>
        <button className="v-btn v-btn--line" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  )
}

function ConfirmAction({ title, body, confirmLabel = 'Confirm', busy, onConfirm, onClose, reasonLabel = 'Reason (optional)' }) {
  const [why, setWhy] = useState('')
  return (
    <Modal title={title} onClose={busy ? () => {} : onClose} labelId="v-confirm-title" name="confirm">
      <p data-confirm-body>{body}</p>
      <label htmlFor="v-confirm-reason" style={{ display: 'block', marginTop: 10 }}>{reasonLabel}
        <textarea id="v-confirm-reason" rows={3} value={why} onChange={(e) => setWhy(e.target.value)} maxLength={1000} /></label>
      <div className="v-row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
        <button type="button" className="v-btn v-btn--line" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="button" className="v-btn v-btn--red" onClick={() => onConfirm(why.trim())} disabled={busy} data-confirm-go>{busy ? 'Working…' : confirmLabel}</button>
      </div>
    </Modal>
  )
}

function Sessions({ orgId, ops }) {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [edit, setEdit] = useState(null)
  const [busy, setBusy] = useState('')
  const [confirm, setConfirm] = useState(null)
  const [show, setShow] = useState('upcoming')
  useEffect(() => { if (!ops.home) ops.load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const load = useCallback(() => api.get(`/volt/ops/${orgId}/sessions`).then((r) => setRows(r.items)).catch((e) => setErr(msg(e))), [orgId])
  useEffect(() => { load() }, [load])
  const viewer = ops.home?.access === 'viewer'
  async function run(key, fn, done) {
    if (busy) return
    setErr(''); setNote(''); setBusy(key)
    try { await fn(); await load(); if (done) setNote(done) } catch (e) { setErr(msg(e)) } finally { setBusy(''); setConfirm(null) }
  }
  const toggle = (s) => run(`open-${s.id}`, () => api.post(`/volt/ops/${orgId}/sessions/${s.id}/booking`, { open: !s.booking_open }),
    s.booking_open ? 'Booking closed in VOLT. Existing bookings stay.' : 'Booking reopened in VOLT.')
  const attend = (s, a, value) => run(`att-${s.id}-${a.rep_id}`, () => api.post(`/volt/ops/${orgId}/sessions/${s.id}/attendance`, { rep_id: a.rep_id, attendance: value }))
  const list = (rows || []).filter((s) => show === 'all' || (s.availability !== 'past' && s.availability !== 'canceled'))
    .sort((a, b) => (a.starts_at < b.starts_at ? -1 : 1))
  return (
    <>
      <div className="v-row"><div><h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>Zoom availability</h2>
        <p className="v-muted">Set the dates applicants can choose. Each time shows its own time zone.</p></div>
        {!viewer && !edit && <button type="button" className="v-btn v-btn--red" onClick={() => setEdit('new')} data-new-session>+ Add a session</button>}</div>
      <p className="v-small v-muted" style={{ margin: '6px 0 10px' }}>Applicants see these times only after you open the Zoom step on their application (Review queue → application file → Zoom step). Capacity is enforced by the server: capacity 1 closes after one booking; group sessions close when full.</p>
      <Err>{err}</Err>
      {note && <div className="v-alert v-alert--info" role="status">{note}</div>}
      {edit && ops.home && <SessionForm orgId={orgId} projects={ops.home.projects} initial={edit === 'new' ? null : edit} onCancel={() => setEdit(null)} onSaved={(m) => { setEdit(null); setNote(m); load() }} />}
      <div className="v-row" style={{ justifyContent: 'flex-start', margin: '6px 0' }}>
        <label className="v-check"><input type="radio" name="v-sess-show" checked={show === 'upcoming'} onChange={() => setShow('upcoming')} /> Upcoming</label>
        <label className="v-check"><input type="radio" name="v-sess-show" checked={show === 'all'} onChange={() => setShow('all')} /> Include past and canceled</label>
      </div>
      {rows === null ? <p className="v-mono">Loading…</p> : list.length === 0 ? <p className="v-muted v-empty">No sessions here yet.</p> : (
        <div className="v-panel v-scroll">
          <table className="v-table v-table--cards" data-table="sessions">
            <thead><tr><th>Date · time zone</th><th>Session</th><th>Booked / capacity</th><th>Status</th><th>Availability</th></tr></thead>
            <tbody>{list.map((s) => {
              const [label, cls] = AVAIL[s.availability] || [s.availability, '']
              const live = s.status !== 'canceled' && s.availability !== 'past'
              return (
                <Fragment key={s.id}>
                  <tr data-session={s.id}>
                    <td data-label="Date"><b>{zoomWhen(s.starts_at, s.timezone)}</b><br /><span className="v-small v-muted">{s.duration_minutes} min · {s.timezone}</span>
                      {s.rescheduled_at && <><br /><span className="v-small">Rescheduled {fmtDate(s.rescheduled_at)}</span></>}</td>
                    <td data-label="Session">{s.kind === 'interview' ? 'Interview' : 'Orientation'}{s.title ? ` · ${s.title}` : ''}<br /><span className="v-small v-muted">{s.project?.short_name}</span></td>
                    <td data-label="Booked" data-booked={s.booked}>{s.capacity != null ? `${s.booked} / ${s.capacity}` : `${s.booked} assigned`}{s.spots_left != null && live ? <><br /><span className="v-small v-muted">{s.spots_left} left</span></> : null}</td>
                    <td data-label="Status"><span className={`v-tag ${cls}`} data-availability={s.availability}>{label}</span>{s.cancel_reason && <><br /><span className="v-small v-muted">{s.cancel_reason}</span></>}</td>
                    <td data-label="Actions">{!viewer && live && <div className="v-row" style={{ justifyContent: 'flex-start' }}>
                      {s.capacity != null && <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => toggle(s)} disabled={!!busy} data-toggle={s.id}>
                        {busy === `open-${s.id}` ? 'Saving…' : s.booking_open ? 'Close availability' : 'Reopen availability'}</button>}
                      <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setEdit(s)} disabled={!!busy}>Edit / reschedule</button>
                      <button type="button" className="v-btn v-btn--black v-btn--sm" disabled={!!busy}
                              onClick={() => setConfirm({ kind: 'session', s })}>Cancel session</button></div>}</td>
                  </tr>
                  {s.attendees.length > 0 && (
                    <tr className="v-subrow"><td colSpan="5">
                      <span className="v-label">Attendees</span>
                      <ul className="v-attendees">{s.attendees.map((a) => (
                        <li key={a.rep_id}><b>{a.display_name}</b> <span className="v-mono">{a.rep_code}</span> <span className="v-small v-muted">{a.source === 'booked' ? `booked ${fmtDate(a.booked_at)}` : 'assigned'}</span>
                          {viewer || s.status === 'canceled' ? <span className="v-small"> · {a.attendance.replace('_', ' ')}</span> : (
                            <>
                              <select aria-label={`Attendance for ${a.display_name}`} value={a.attendance} onChange={(e) => attend(s, a, e.target.value)} style={{ width: 'auto' }}>
                                <option value="invited">Booked / invited</option><option value="attended">Attended</option><option value="no_show">No-show</option></select>
                              <button type="button" className="v-btn v-btn--link" disabled={!!busy} onClick={() => setConfirm({ kind: 'booking', s, a })}>Cancel booking</button>
                            </>)}</li>))}</ul>
                    </td></tr>)}
                </Fragment>)
            })}</tbody>
          </table>
        </div>)}
      <p className="v-small v-muted">Closing availability stops new bookings; confirmed bookings stay. A full session stays unavailable even if reopened.</p>
      {confirm?.kind === 'session' && (
        <ConfirmAction title="Cancel this session in VOLT?" busy={busy === 'cancel'} confirmLabel="Cancel session" onClose={() => setConfirm(null)}
          body={<>{confirm.s.kind} · {zoomWhen(confirm.s.starts_at, confirm.s.timezone)} · {confirm.s.booked} booked. Attendees lose the Join button and are emailed. <b>This does not cancel the meeting in Zoom</b>; do that in Zoom.</>}
          onConfirm={(why) => run('cancel', () => api.post(`/volt/ops/${orgId}/sessions/${confirm.s.id}/cancel`, { reason: why }), 'Session canceled in VOLT. Cancel the meeting in Zoom too.')} />)}
      {confirm?.kind === 'booking' && (
        <ConfirmAction title="Cancel this booking?" busy={busy === 'cancel'} confirmLabel="Cancel booking" onClose={() => setConfirm(null)}
          body={<><b>{confirm.a.display_name}</b> ({confirm.a.rep_code}) · {zoomWhen(confirm.s.starts_at, confirm.s.timezone)}. Their seat is released and they can choose another time. Their application is not changed.</>}
          onConfirm={(why) => run('cancel', () => api.post(`/volt/ops/${orgId}/sessions/${confirm.s.id}/bookings/${confirm.a.rep_id}/cancel`, { reason: why }), 'Booking canceled. The seat is free again.')} />)}
    </>
  )
}

// ── People & Access ─────────────────────────────────────────────────────────
const ACCESS_TAG = { active: 'v-tag--ok', suspended: '', removed: 'v-tag--line' }
const ACTION_COPY = {
  suspend: ['Suspend access', 'Block VOLT sign-in and every VOLT request now, and end their VOLT sessions. Their records are kept.'],
  restore: ['Restore access', 'Allow VOLT sign-in again. Old sessions stay ended; they sign in fresh.'],
  remove: ['Remove VOLT access', 'Remove their VOLT access (their VOLT staff role, if any) and end their VOLT sessions. Other businesses and their platform login are not touched. Records are kept.'],
  archive: ['Archive applicant', 'Hide their applications from the default queues. Sign-in is not changed.'],
  unarchive: ['Unarchive applicant', 'Return their applications to the default queues. Sign-in is not changed.'],
}

function PersonDrawer({ orgId, person, canManage, actorLevel, meId, onClose, onChanged }) {
  const [hist, setHist] = useState(null)
  const [ask, setAsk] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const closeBtn = useRef(null)
  useEffect(() => { closeBtn.current?.focus() }, [])
  useEscape(onClose)
  const loadHist = useCallback(() => api.get(`/volt/ops/${orgId}/people/history`, { params: { user_id: person.user_id } }).then((r) => setHist(r.items)).catch(() => setHist([])), [orgId, person.user_id])
  useEffect(() => { loadHist() }, [loadHist])
  const self = person.user_id === meId
  const adminTarget = person.is_admin && actorLevel !== 'owner'
  const locked = !canManage || self || adminTarget
  async function go(why) {
    setBusy(true); setErr('')
    try {
      const r = (ask === 'archive' || ask === 'unarchive')
        ? await api.post(`/volt/ops/${orgId}/people/${person.user_id}/archive`, { archived: ask === 'archive', reason: why })
        : await api.post(`/volt/ops/${orgId}/people/${person.user_id}/access`, { action: ask, reason: why })
      setAsk(null); await onChanged(r); await loadHist()
    } catch (e) { setErr(msg(e)); setAsk(null) } finally { setBusy(false) }
  }
  return (
    <div className="v-drawer-bg" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <aside className="v-drawer v-drawer--narrow" role="dialog" aria-modal="true" aria-labelledby="v-person-h" data-person-drawer={person.user_id}>
        <div className="v-row"><span className="v-mono">Manage VOLT access</span><button ref={closeBtn} type="button" className="v-btn v-btn--line v-btn--sm" onClick={onClose} aria-label="Close access panel">Close ✕</button></div>
        <h2 id="v-person-h" style={{ fontSize: 26, letterSpacing: '-.03em', margin: '10px 0 2px' }}>{person.name}</h2>
        <p className="v-muted">{person.email}</p>
        <p style={{ margin: '8px 0' }}>{person.roles.map((r) => <span key={r} className="v-tag v-tag--line" style={{ marginRight: 6 }}>{r}</span>)}{person.is_test && <span className="v-tag v-tag--line">Test</span>}</p>
        <section className="v-panel">
          <h3>VOLT access</h3>
          <p>Current access: <span className={`v-tag ${ACCESS_TAG[person.access] || ''}`} data-access-state>{person.access_label}</span></p>
          {person.reason && <p className="v-small v-muted">Last reason: {person.reason}</p>}
          <Err>{err}</Err>
          {locked ? (
            <p className="v-small v-muted" data-locked>{self ? "You can't change your own access." : adminTarget ? "Only the owner can change another org admin's access." : 'Only the owner or a VOLT org admin can change access.'}</p>
          ) : (
            <div className="v-stack">
              {person.access === 'active'
                ? <button type="button" className="v-btn v-btn--red" onClick={() => setAsk('suspend')} data-act="suspend">Suspend access</button>
                : <button type="button" className="v-btn v-btn--red" onClick={() => setAsk('restore')} data-act="restore">Restore access</button>}
              {person.access !== 'removed' && <button type="button" className="v-btn v-btn--line" onClick={() => setAsk('remove')} data-act="remove">Remove VOLT access</button>}
            </div>)}
          <p className="v-small v-muted">Access changes apply to VOLT Marketing only. Other businesses and historical records stay intact.</p>
        </section>
        <section className="v-panel">
          <h3>Application record</h3>
          {person.applicant ? (
            <>
              <p className="v-small">{person.applications} application{person.applications === 1 ? '' : 's'}{person.archived ? ` · Archived ${fmtDate(person.archived_at)}` : ''}</p>
              {canManage && <button type="button" className="v-btn v-btn--line" onClick={() => setAsk(person.archived ? 'unarchive' : 'archive')} data-act={person.archived ? 'unarchive' : 'archive'}>
                {person.archived ? 'Unarchive applicant' : 'Archive applicant'}</button>}
              <p className="v-small v-muted">Archiving hides the record from active queues. It does not block sign-in.</p>
            </>) : <p className="v-small v-muted">Staff only; no application record.</p>}
        </section>
        <div className="v-audit" data-owner-note>🔒 Owner oversight is protected. Every access change is recorded.</div>
        <section className="v-panel" style={{ marginTop: 12 }}>
          <h3>History</h3>
          {hist === null ? <p className="v-mono">Loading…</p> : hist.length === 0 ? <p className="v-muted v-small">No access changes recorded.</p> : (
            <ul className="v-ma-updates">{hist.map((h, i) => <li key={i} className="v-small"><b>{h.action.replace('access_', '').replace('_', ' ')}</b> · {h.actor} · {new Date(h.at).toLocaleString('en-US', { timeZoneName: 'short' })}{h.reason ? ` · “${h.reason}”` : ''}</li>)}</ul>)}
        </section>
        {ask && (
          <ConfirmAction title={`${ACTION_COPY[ask][0]}?`} busy={busy} confirmLabel={ACTION_COPY[ask][0]} onClose={() => setAsk(null)} onConfirm={go}
            body={<><b>{person.name}</b> ({person.email}) · Scope: VOLT Marketing. {ACTION_COPY[ask][1]}</>} />)}
      </aside>
    </div>
  )
}

function People({ orgId, ops }) {
  const [f, setF] = useState({ q: '', role: '', access: '', include_archived: false, include_test: false })
  const [data, setData] = useState(null)
  const [hist, setHist] = useState([])
  const [err, setErr] = useState('')
  const [open, setOpen] = useState(null)
  const [snap, setSnap] = useState(null)          // the open person, even if a filter now hides them
  useEffect(() => { if (!ops.home) ops.load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const seq = useRef(0)
  const load = useCallback(() => {
    const n = ++seq.current
    const params = clean({ ...f, include_archived: f.include_archived ? 1 : '', include_test: f.include_test ? 1 : '' })
    return Promise.all([api.get(`/volt/ops/${orgId}/people`, { params }), api.get(`/volt/ops/${orgId}/people/history`)])
      .then(([p, h]) => { if (n === seq.current) { setData(p); setHist(h.items) } }).catch((e) => setErr(msg(e)))
  }, [orgId, JSON.stringify(f)]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const t = setTimeout(load, 180); return () => clearTimeout(t) }, [load])
  const listed = open && data?.people.find((p) => p.user_id === open)
  const person = open ? (listed || (snap?.user_id === open ? snap : null)) : null
  const t = data?.totals
  return (
    <>
      <div className="v-row"><div><h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>People & Access</h2><p className="v-muted">Manage access to VOLT Marketing.</p></div>
        <div style={{ textAlign: 'right' }}><b>{ops.home?.me?.full_name}</b><div className="v-small v-muted">{data?.actor_level === 'owner' ? 'Owner' : data?.can_manage ? 'VOLT org admin' : 'View only'}</div></div></div>
      <div className="v-kpis v-kpis--3" data-people-totals>
        <div className="v-kpi" role="group" aria-label="Active access"><span>Active access</span><b>{t ? t.active : '–'}</b></div>
        <div className="v-kpi" role="group" aria-label="Suspended"><span>Suspended</span><b>{t ? t.suspended : '–'}</b></div>
        <div className="v-kpi" role="group" aria-label="Archived records"><span>Archived records</span><b>{t ? t.archived : '–'}</b></div>
      </div>
      <Err>{err}</Err>
      <section className="v-panel">
        <div className="v-filters">
          <input aria-label="Search people" placeholder="Search name or email" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} data-people-search />
          <select aria-label="Role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="">All roles</option><option value="applicant">Applicants</option><option value="staff">Staff</option></select>
          <select aria-label="Access status" value={f.access} onChange={(e) => setF({ ...f, access: e.target.value })}><option value="">All access</option><option value="active">Active</option><option value="suspended">Suspended</option><option value="removed">Removed</option></select>
          <label className="v-check"><input type="checkbox" checked={f.include_archived} onChange={(e) => setF({ ...f, include_archived: e.target.checked })} /> Include archived</label>
          <label className="v-check"><input type="checkbox" checked={f.include_test} onChange={(e) => setF({ ...f, include_test: e.target.checked })} /> Include test records</label>
        </div>
        <div className="v-scroll">
          <table className="v-table v-table--cards" data-table="people">
            <thead><tr><th>Name</th><th>Role</th><th>Access</th><th>Applications</th><th><span className="v-sr">Action</span></th></tr></thead>
            <tbody>
              {data === null && <tr><td colSpan="5" className="v-mono">Loading…</td></tr>}
              {data?.people.length === 0 && <tr><td colSpan="5" className="v-empty">No matching people.</td></tr>}
              {data?.people.map((p) => (
                <tr key={p.user_id} data-person={p.user_id}>
                  <td data-label="Name"><b>{p.name}</b><br /><span className="v-small v-muted">{p.email}{p.archived ? ' · Archived' : ''}{p.is_test ? ' · Test' : ''}</span></td>
                  <td data-label="Role">{p.roles.join(' · ')}</td>
                  <td data-label="Access"><span className={`v-tag ${ACCESS_TAG[p.access] || ''}`}>{p.access_label}</span></td>
                  <td data-label="Applications">{p.applicant ? p.applications : '—'}</td>
                  <td data-label="Action"><button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => { setSnap(p); setOpen(p.user_id) }} data-manage={p.user_id}>Manage access →</button></td>
                </tr>))}
            </tbody>
          </table>
        </div>
      </section>
      <p className="v-small v-muted">Owner oversight is protected. Archiving and access removal are separate actions. No delete feature exists here.</p>
      <section className="v-panel">
        <h3>Access history</h3>
        {hist.length === 0 ? <p className="v-muted v-small">No access changes yet.</p> : (
          <ul className="v-ma-updates" data-access-history>{hist.slice(0, 25).map((h, i) => (
            <li key={i} className="v-small"><b>{h.action.replace('access_', '').replace('_', ' ')}</b> · {h.target} · by {h.actor} · {new Date(h.at).toLocaleString('en-US', { timeZoneName: 'short' })}{h.reason ? ` · “${h.reason}”` : ''}</li>))}</ul>)}
      </section>
      {person && <PersonDrawer orgId={orgId} person={person} canManage={data.can_manage} actorLevel={data.actor_level} meId={data.me?.user_id}
                               onClose={() => setOpen(null)} onChanged={async (r) => { if (r?.user_id) setSnap(r); await load() }} />}
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
  const [purging, setPurging] = useState(false)
  const purgingRef = useRef(false)
  async function purgeTests() {
    if (purgingRef.current) return                 // one click, one request
    purgingRef.current = true
    setPurging(true); setPurge(''); setErr('')
    try {
      const r = await api.post(`/volt/ops/${orgId}/test-records/purge`, {})
      const n = (k, one, many) => `${r[k] || 0} ${(r[k] || 0) === 1 ? one : many}`
      setPurge(`Removed ${n('reps', 'test rep', 'test reps')}, ${n('applications', 'application', 'applications')}, `
        + `${n('documents', 'document', 'documents')}, ${n('sessions', 'session', 'sessions')} and `
        + `${n('logins', 'applicant login', 'applicant logins')}${r.logins_deactivated ? ` (${r.logins_deactivated} switched off)` : ''}.`)
    } catch (e) {
      setErr(e?.detail?.code === 'cleanup_running' ? 'A cleanup is already running. This list refreshes when it finishes.' : msg(e))
    } finally {
      await load()
      ops.load()
      purgingRef.current = false
      setPurging(false)
    }
  }
  return (
    <>
      <div className="v-row"><h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>Reps & recruiter directory</h2>
        {ops.home?.access === 'owner' && <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={purgeTests}
            disabled={purging} aria-busy={purging} data-action="purge-tests">{purging ? 'Removing test records…' : 'Remove test records'}</button>}</div>
      <p className="v-small v-muted">Every person keeps one permanent rep ID. A rep in the directory can be selected as a recruiter by applicants.</p>
      <Err>{err}</Err>{purging && <div className="v-alert v-alert--info" role="status">Removing test records. This can take up to a minute; the list refreshes when it is done.</div>}
      {purge && <div className="v-alert v-alert--ok" role="status" data-purge-result>{purge}</div>}
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
  useVoltTitle()
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
        <Route path="people" element={<People orgId={orgId} ops={ops} />} />
        <Route path="talent" element={<TalentPool orgId={orgId} />} />
        <Route path="email" element={<SupportEmail orgId={orgId} />} />
        <Route path="reps" element={<Reps orgId={orgId} ops={ops} />} />
        <Route path="projects" element={<Projects orgId={orgId} ops={ops} />} />
        <Route path="*" element={<Navigate to={`/volt-ops/${orgId}`} replace />} />
      </Routes>
    </OpsShell>
  )
}
