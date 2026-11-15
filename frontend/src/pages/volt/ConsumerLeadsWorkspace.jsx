// CONSUMER LEADS - VOLT Marketing's subsidiary business, in its own workspace.
//   /consumer-leads/:orgId/*          private back office (this file)
//   /consumer-leads/:orgId/preview    signed-in preview of the customer website;
//                                     submissions go to TEST data only
// Every permission is decided by the server (app/services/volt/leads.py and
// lead_sources.py). This page only shows what the server returns.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useParams, useSearchParams } from 'react-router-dom'
import { api, API_BASE, fetchObjectUrl } from '../../api/client'
import { VOLT_API_ORIGIN } from '../../utils/voltDomain'
import { CoverageFunnel, fmtCT, publicSiteUrl } from './VoltLeads'
import Development from './VoltDev'
import SheetsPanel, { OpenSheetLink } from './SheetsPanel'
import { useVoltTitle } from './voltApi'
import './volt.css'

const msg = (e) => e?.message || 'Something went wrong.'
const NAVY = '#163B60'
const STATUSES = [['new', 'New'], ['contacted', 'Contacted'], ['qualified', 'Qualified'], ['appointment_set', 'Appointment Set'], ['enrolled', 'Enrolled'], ['not_interested', 'Not Interested']]
const CHANNELS = [['meta', 'Meta / Facebook'], ['google', 'Google'], ['organic', 'Organic search'], ['direct', 'Direct'], ['referral', 'Referral'], ['other', 'Other']]
const SECTIONS = [['', 'Overview'], ['leads', 'Leads'], ['sources', 'Lead Sources'], ['funnels', 'Funnels & Campaigns'],
  ['assignments', 'Assignments'], ['followup', 'Responses & Follow-Up'], ['reporting', 'Reporting'], ['dev', 'Development'], ['settings', 'Settings']]
const apiOrigin = () => (window.location.hostname.endsWith('.live') ? VOLT_API_ORIGIN : API_BASE)

// Real leads vs the test partition. Kept in the URL (?data=test) so a reload or
// a shared link never silently switches which data is shown.
function useDataMode() {
  const [sp, setSp] = useSearchParams()
  const test = sp.get('data') === 'test'
  const setTest = (on) => { const n = new URLSearchParams(sp); if (on) n.set('data', 'test'); else n.delete('data'); setSp(n, { replace: true }) }
  return [test, setTest, test ? { test: 1 } : {}]
}

function Kpis({ items }) {
  return <div className="v-kpis">{items.map(([v, l, k]) => <div key={l} className="v-kpi" data-kpi={k || l}><b>{v ?? '—'}</b><span>{l}</span></div>)}</div>
}

function Shell({ orgId, summary, test, setTest, children }) {
  const base = `/consumer-leads/${orgId}`
  return (
    <div className="volt" data-cl-workspace>
      <header className="v-head" style={{ background: NAVY }}>
        <div className="v-wrap">
          <Link className="v-brand" to={base} aria-label="Consumer Leads home" style={{ color: '#fff' }}>
            <span aria-hidden="true" style={{ fontSize: 22 }}>✚</span> <span className="v-word">Consumer Leads</span>
            <span className="v-word-sub">a VOLT Marketing business</span></Link>
          <nav aria-label="Consumer Leads actions" style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {summary?.can_preview && <Link className="v-btn v-btn--sm" style={{ background: '#fff', color: NAVY }} to={`${base}/preview`} data-preview-site>Preview Customer Website</Link>}
            <a className="v-btn v-btn--ghost v-btn--sm" href={publicSiteUrl()} target="_blank" rel="noreferrer" data-public-site>View Public Website ↗</a>
            <Link className="v-btn v-btn--ghost v-btn--sm" to="/workspaces">Program Access</Link>
          </nav>
        </div>
      </header>
      <main className="v-wrap" style={{ flex: 1 }}>
        <nav className="v-tabs" aria-label="Consumer Leads sections">
          {SECTIONS.map(([p, l]) => <NavLink key={p} end={!p} className="v-tab" to={p ? `${base}/${p}${test ? '?data=test' : ''}` : `${base}${test ? '?data=test' : ''}`} data-section={p || 'overview'}>{l}</NavLink>)}
        </nav>
        {summary?.can_preview && <div className="v-row" style={{ justifyContent: 'flex-start', gap: 10, margin: '8px 0 14px' }} data-mode>
          <span className="v-small">Showing:</span>
          <button type="button" className={`v-btn v-btn--sm ${test ? 'v-btn--line' : 'v-btn--black'}`} aria-pressed={!test} onClick={() => setTest(false)} data-mode-real>Real leads</button>
          <button type="button" className={`v-btn v-btn--sm ${test ? 'v-btn--black' : 'v-btn--line'}`} aria-pressed={test} onClick={() => setTest(true)} data-mode-test>Test data</button>
          {test && <span className="v-tag" style={{ background: '#FFF4D6' }}>TEST DATA - not real customers, never in real reports</span>}
        </div>}
        {children}
      </main>
      <footer className="v-foot"><div className="v-wrap"><span>Consumer Leads · VOLT Marketing · times shown in Central time</span></div></footer>
    </div>
  )
}

// ── Overview ────────────────────────────────────────────────────────────────
function Overview({ orgId, summary, test, q }) {
  const [r, setR] = useState(null)
  const [f, setF] = useState(null)
  const [src, setSrc] = useState(null)
  useEffect(() => {
    api.get(`/volt/ops/${orgId}/leads/report`, { params: q }).then(setR).catch(() => {})
    api.get(`/volt/ops/${orgId}/leads/followup`, { params: q }).then(setF).catch(() => {})
    if (summary?.can_preview) api.get(`/volt/ops/${orgId}/leads/sources`).then(setSrc).catch(() => {})
  }, [orgId, test]) // eslint-disable-line react-hooks/exhaustive-deps
  const base = `/consumer-leads/${orgId}`
  return (
    <section aria-labelledby="cl-ov" data-cl-overview>
      <h1 id="cl-ov" style={{ fontSize: 30, marginBottom: 2 }}>Consumer Leads</h1>
      <p className="v-muted" style={{ marginTop: 0 }}>Health Insurance Lead Acquisition &amp; Enrollment · {summary?.access === 'all' ? 'you see every lead' : 'you see the leads assigned to you'}</p>
      <p><strong>Public intake: {summary?.intake_enabled ? 'ON' : 'OFF'}</strong> · {summary?.intake_enabled
        ? 'the customer website and active real sources accept requests.'
        : 'the customer website is visible but does not accept requests; real sources are held.'}</p>
      {r && <Kpis items={[[r.total, 'Total leads', 'total'], [r.by_status.new, 'New', 'new'], [r.by_status.contacted, 'Contacted'], [r.by_status.qualified, 'Qualified'],
        [r.appointments, 'Appointments'], [r.enrollments, 'Enrollments']]} />}
      {r && <p className="v-small v-muted">Last activity: {fmtCT(r.last_activity_at)}</p>}
      <div className="v-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: 14, marginTop: 14 }}>
        <div className="v-panel"><h3>Waiting for first contact</h3><p style={{ fontSize: 28, margin: 0 }}>{f ? f.response.never_contacted : '—'}</p>
          <Link to={`${base}/followup${test ? '?data=test' : ''}`}>Responses &amp; Follow-Up →</Link></div>
        <div className="v-panel"><h3>Follow-ups due</h3><p style={{ fontSize: 28, margin: 0 }}>{f ? f.follow_ups_due.length : '—'}</p>
          <Link to={`${base}/leads${test ? '?data=test' : ''}`}>Open leads →</Link></div>
        {src && <div className="v-panel"><h3>Lead sources</h3><p style={{ margin: 0 }}>{src.sources.filter((s) => s.status === 'active').length} on · {src.sources.length} set up</p>
          <Link to={`${base}/sources`}>Lead Sources →</Link></div>}
        {summary?.can_develop && <div className="v-panel"><h3>Development</h3><p className="v-small" style={{ margin: 0 }}>Request a change to Consumer Leads.</p>
          <Link to={`${base}/dev`}>Development →</Link></div>}
      </div>
    </section>
  )
}

// ── Leads (inbox + detail) ──────────────────────────────────────────────────
export function LeadsInbox({ orgId, test, q, unassignedOnly = false, onChanged }) {
  const base = `/volt/ops/${orgId}/leads`
  const [f, setF] = useState({ q: '', status: '', channel: '', campaign: '' })
  const [range, setRange] = useState({ from: '', to: '' })
  const [data, setData] = useState(null)
  const [sel, setSel] = useState(null)
  const [agents, setAgents] = useState([])
  const [note, setNote] = useState('')
  const [appt, setAppt] = useState('')
  const [fu, setFu] = useState('')
  const [err, setErr] = useState('')
  const seq = useRef(0)
  const load = useCallback(() => {
    const n = ++seq.current
    const params = { ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)), ...q, ...(unassignedOnly ? { unassigned: 1 } : {}) }
    api.get(base, { params }).then((x) => { if (n === seq.current) setData(x) }).catch((e) => setErr(msg(e)))
  }, [base, JSON.stringify(f), test, unassignedOnly]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t) }, [load])
  useEffect(() => { api.get(`${base}/agents`).then((x) => setAgents(x.agents)).catch(() => {}) }, [base])
  useEffect(() => { setSel(null) }, [test])
  const open = (id) => api.get(`${base}/${id}`, { params: q }).then((x) => { setSel(x); setAppt(''); setFu('') }).catch((e) => setErr(msg(e)))
  const act = (p) => p.then((x) => { setSel(x); setNote(''); setErr(''); load(); onChanged?.() }).catch((e) => setErr(msg(e)))
  const patch = (body) => act(api.patch(`${base}/${sel.id}`, test ? { ...body, test: true } : body))
  const assign = (id, user_id) => api.post(`${base}/${id}/assign`, { user_id: user_id || null, ...(test ? { test: true } : {}) })
  const canAssign = data?.access === 'all'
  const downloadCsv = async () => {
    try {
      const p = { ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)), ...q, ...(unassignedOnly ? { unassigned: 1 } : {}), ...(range.from ? { from: range.from } : {}), ...(range.to ? { to: range.to } : {}) }
      const url = await fetchObjectUrl(`${base}/export.csv?${new URLSearchParams(p)}`)
      const a = document.createElement('a'); a.href = url; a.download = test ? 'consumer-leads-TEST-DATA.csv' : 'consumer-leads.csv'; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 2000)
    } catch (e) { setErr(msg(e)) }
  }
  return (
    <section data-cl-leads>
      {err && <div className="v-alert v-alert--err" role="alert">{err}</div>}
      {!unassignedOnly && <div className="v-row" style={{ justifyContent: 'flex-start', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }} data-export>
        <label className="v-small">From<input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} data-csv-from /></label>
        <label className="v-small">To<input type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} data-csv-to /></label>
        <button type="button" className="v-btn v-btn--black v-btn--sm" onClick={downloadCsv} data-download-csv>Download CSV ↓</button>
        {!test && <OpenSheetLink orgId={orgId} business="consumer" />}
        <span className="v-small v-muted">The CSV is exactly the leads you can see, with these filters{test ? ' (test data)' : ''}.</span>
      </div>}
      {!unassignedOnly && <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', margin: '6px 0 14px' }}>
        <label>Search<input value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} placeholder="Name, phone, email or ZIP" data-lead-search /></label>
        <label>Status<select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">All statuses</option>
          {STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label>Channel<select value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}><option value="">All channels</option>
          {CHANNELS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label>Campaign<input value={f.campaign} onChange={(e) => setF({ ...f, campaign: e.target.value })} placeholder="utm_campaign" /></label>
      </div>}
      <div className="v-scroll"><table className="v-table" data-table="leads"><thead><tr><th>Lead</th><th>ZIP / State</th><th>Channel</th><th>Assigned to</th><th>Status</th><th>Received</th><th><span className="v-sr">Open</span></th></tr></thead>
        <tbody>{(data?.leads || []).map((l) => (
          <tr key={l.id} data-lead-row={l.id}><td>{l.name}{l.test && <span className="v-tag" style={{ marginLeft: 6 }}>TEST</span>}<br /><span className="v-small">{l.phone}</span></td><td>{l.zip}{l.state ? ` · ${l.state}` : ''}</td>
            <td>{l.channel}{l.campaign ? <><br /><span className="v-small">{l.campaign}</span></> : null}</td>
            <td>{unassignedOnly && canAssign
              ? <select aria-label={`Assign ${l.name}`} defaultValue="" onChange={(e) => e.target.value && assign(l.id, e.target.value).then(() => { load(); onChanged?.() }).catch((x) => setErr(msg(x)))} data-quick-assign>
                <option value="">Choose…</option>{agents.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select>
              : (l.assigned_to || 'Unassigned')}</td>
            <td>{l.status_label}{l.appointment_at ? <><br /><span className="v-small">Appt {fmtCT(l.appointment_at)}</span></> : null}
              {l.next_follow_up_at ? <><br /><span className="v-small">Follow up {fmtCT(l.next_follow_up_at)}</span></> : null}</td>
            <td className="v-small">{fmtCT(l.created_at)}</td>
            <td><button type="button" className="v-btn v-btn--link" onClick={() => open(l.id)} aria-label={`Open ${l.name}`}>View</button></td></tr>))}
          {data && !data.leads.length && <tr><td colSpan={7}>{unassignedOnly ? 'Every lead is assigned.' : 'No matching leads.'}</td></tr>}</tbody></table></div>
      {sel && <aside className="v-card" aria-label="Selected lead" style={{ marginTop: 16 }} data-lead-detail>
        <h3>{sel.name}{sel.test && <span className="v-tag" style={{ marginLeft: 8 }}>TEST DATA</span>}</h3>
        <p className="v-small">Phone {sel.phone || '—'} · Email {sel.email || '—'} · ZIP {sel.zip}{sel.state ? ` · ${sel.state}` : ''} · Age {sel.age_range || '—'} · Household {sel.household_size || '—'} · Income {sel.income_range || '—'}<br />
          Prefers {sel.contact_preference || '—'}{sel.best_time ? ` (${sel.best_time})` : ''} · Coverage now: {sel.coverage_status || '—'}<br />
          Channel {sel.channel}{sel.utm?.utm_campaign ? ` · campaign ${sel.utm.utm_campaign}` : ''} · Consent {sel.consent_version} at {fmtCT(sel.consent_at)} · {sel.submissions} submission(s)</p>
        <label>Status<select value={sel.status} onChange={(e) => patch({ status: e.target.value })} data-lead-status>
          {STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        {canAssign && <label>Assign to<select value={sel.assigned_user_id || ''} onChange={(e) => act(assign(sel.id, e.target.value))} data-lead-assign>
          <option value="">Unassigned</option>{agents.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>}
        <div className="v-row" style={{ justifyContent: 'flex-start', gap: 8, flexWrap: 'wrap', margin: '10px 0' }}>
          <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => patch({ event: 'call_attempted', note })} data-log="call_attempted">Log call attempt</button>
          <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => patch({ event: 'contact_completed', note })} data-log="contact_completed">Contact made</button>
          <label className="v-small">Appointment<input type="datetime-local" value={appt} onChange={(e) => setAppt(e.target.value)} data-appt-input /></label>
          <button type="button" className="v-btn v-btn--line v-btn--sm" disabled={!appt} onClick={() => patch({ event: 'appointment_set', appointment_at: new Date(appt).toISOString(), note })} data-log="appointment_set">{sel.appointment_at ? 'Change appointment' : 'Set appointment'}</button>
          <label className="v-small">Follow up<input type="datetime-local" value={fu} onChange={(e) => setFu(e.target.value)} data-fu-input /></label>
          <button type="button" className="v-btn v-btn--line v-btn--sm" disabled={!fu} onClick={() => patch({ event: 'follow_up_scheduled', follow_up_at: new Date(fu).toISOString(), note })} data-log="follow_up_scheduled">Schedule follow-up</button>
          <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => patch({ event: 'enrollment_recorded', note })} data-log="enrollment_recorded">Record enrollment</button>
        </div>
        <label>Note (added to the next action, or saved on its own)<input value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} data-lead-note /></label>
        <button type="button" className="v-btn v-btn--red v-btn--sm" disabled={!note.trim()} onClick={() => patch({ note })}>Save note</button>
        <h4>History (Central time)</h4><ul data-lead-history>{sel.history.map((h) => <li key={h.id} className="v-small">
          <b>{fmtCT(h.at)}</b> · {h.label}{h.old || h.new ? `: ${h.old ? `${h.old} → ` : ''}${h.new || ''}` : ''}{h.note ? ` - ${h.note}` : ''} · {h.by}</li>)}</ul>
        <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setSel(null)}>Close</button>
      </aside>}
    </section>
  )
}

// ── Lead Sources ────────────────────────────────────────────────────────────
function parseCsv(text) {
  const rows = []
  let row = [], cell = '', quoted = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++ } else if (ch === '"') quoted = false; else cell += ch
    } else if (ch === '"') quoted = true
    else if (ch === ',') { row.push(cell); cell = '' } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++
      row.push(cell); cell = ''; if (row.some((c) => c !== '')) rows.push(row); row = []
    } else cell += ch
  }
  row.push(cell); if (row.some((c) => c !== '')) rows.push(row)
  if (rows.length < 2) return []
  const head = rows[0].map((h) => h.trim())
  return rows.slice(1).map((r) => Object.fromEntries(head.map((h, i) => [h, (r[i] || '').trim()])))
}

function SourceCard({ orgId, s, targets, onChanged }) {
  const base = `/volt/ops/${orgId}/leads`
  const [open, setOpen] = useState(false)
  const [map, setMap] = useState(s.field_map)
  const [dl, setDl] = useState(null)
  const [err, setErr] = useState('')
  const [csv, setCsv] = useState(null)
  const [att, setAtt] = useState('')
  const [res, setRes] = useState(null)
  const loadDl = () => api.get(`${base}/sources/${s.id}/deliveries`).then((x) => setDl(x.deliveries)).catch((e) => setErr(msg(e)))
  useEffect(() => { if (open) loadDl() }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  const patch = (body) => api.patch(`${base}/sources/${s.id}`, body).then(() => { setErr(''); onChanged() }).catch((e) => setErr(msg(e)))
  const retry = (id) => api.post(`${base}/deliveries/${id}/retry`).then(() => { loadDl(); onChanged() }).catch((e) => setErr(msg(e)))
  const pick = (e) => { const file = e.target.files?.[0]; if (!file) return; file.text().then((t) => setCsv(parseCsv(t))) }
  const doImport = () => api.post(`${base}/sources/${s.id}/import`, { rows: csv, attestation: att })
    .then((r) => { setRes(r); setCsv(null); onChanged(); loadDl() }).catch((e) => setErr(msg(e)))
  return (
    <article className="v-panel" data-source={s.provider} data-source-id={s.id}>
      <div className="v-row"><div><h3 style={{ margin: 0 }}>{s.name} {s.test && <span className="v-tag">TEST</span>}</h3>
        <p className="v-small v-muted" style={{ margin: 0 }}>{s.provider_label}{s.campaign ? ` · campaign ${s.campaign}` : ''}</p></div>
        <span className="v-tag" data-source-state>{s.state_label}</span></div>
      {err && <div className="v-alert v-alert--err" role="alert">{err}</div>}
      {s.status === 'pending_connection' && <p className="v-small" data-source-needs><b>Needs:</b> {s.needs}</p>}
      <p className="v-small">Received {s.received} · failed {s.failed} · repeats {s.duplicates} · rejected {s.rejected} · last {fmtCT(s.last_received_at)}</p>
      {s.last_error && <p className="v-small" style={{ color: '#a0281e' }}>Last failure: {s.last_error}</p>}
      <div className="v-row" style={{ justifyContent: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
        {s.status !== 'pending_connection' && <button type="button" className="v-btn v-btn--sm v-btn--black" onClick={() => patch({ status: s.status === 'active' ? 'paused' : 'active' })} data-source-toggle>
          {s.status === 'active' ? 'Pause' : 'Turn on'}</button>}
        <button type="button" className="v-btn v-btn--sm v-btn--line" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? 'Hide details' : 'Details, mapping & deliveries'}</button>
      </div>
      {open && <div style={{ marginTop: 12 }}>
        {s.webhook_url && <p className="v-small">Webhook address: <code data-webhook-url>{apiOrigin()}{s.webhook_url}</code></p>}
        {s.how && <p className="v-small">{s.how}</p>}
        <h4>Field mapping</h4>
        <p className="v-small v-muted">Left: our field. Right: the name the source uses for it.</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 6 }}>
          {targets.map((t) => <label key={t} className="v-small">{t}<input value={map[t] || ''} onChange={(e) => setMap({ ...map, [t]: e.target.value })} data-map={t} /></label>)}
        </div>
        <button type="button" className="v-btn v-btn--sm v-btn--line" onClick={() => patch({ field_map: map })} data-save-map>Save mapping</button>
        {s.provider === 'csv' && <div className="v-card" style={{ marginTop: 12 }} data-csv>
          <h4>Import a CSV</h4>
          <input type="file" accept=".csv,text/csv" onChange={pick} data-csv-file />
          {csv && <p className="v-small">{csv.length} row(s) read.</p>}
          <label className="v-small">Where and when did these people agree to be contacted?<input value={att} onChange={(e) => setAtt(e.target.value)} data-csv-attest /></label>
          <button type="button" className="v-btn v-btn--sm v-btn--black" disabled={!csv || !csv.length} onClick={doImport} data-csv-import>Import</button>
          {res && <p className="v-small" role="status">Imported {res.processed}, failed {res.failed}, repeats {res.duplicate}.</p>}
        </div>}
        <h4>Recent deliveries</h4>
        <div className="v-scroll"><table className="v-table"><thead><tr><th>Received</th><th>ID</th><th>Result</th><th>Tries</th><th /></tr></thead>
          <tbody>{(dl || []).map((d) => <tr key={d.id}><td className="v-small">{fmtCT(d.received_at)}</td><td className="v-small">{d.external_id.slice(0, 24)}</td>
            <td>{d.status}{d.error ? <><br /><span className="v-small">{d.error}</span></> : null}{d.next_retry_at ? <><br /><span className="v-small">auto-retry {fmtCT(d.next_retry_at)}</span></> : null}</td>
            <td>{d.attempts}</td><td>{d.can_retry && <button type="button" className="v-btn v-btn--link" onClick={() => retry(d.id)} data-retry>Retry</button>}</td></tr>)}
            {dl && !dl.length && <tr><td colSpan={5} className="v-muted">Nothing received yet.</td></tr>}</tbody></table></div>
      </div>}
    </article>
  )
}

function LeadSources({ orgId }) {
  const base = `/volt/ops/${orgId}/leads`
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [form, setForm] = useState({ provider: 'custom_webhook', name: '', campaign: '', test: true })
  const [fresh, setFresh] = useState(null)
  const load = () => api.get(`${base}/sources`).then(setD).catch((e) => setErr(msg(e)))
  useEffect(() => { load() }, [orgId]) // eslint-disable-line react-hooks/exhaustive-deps
  const create = (e) => { e.preventDefault(); api.post(`${base}/sources`, form).then((s) => { setFresh(s); setForm({ ...form, name: '', campaign: '' }); load() }).catch((x) => setErr(msg(x))) }
  if (err && !d) return <div className="v-alert v-alert--err" role="alert">{err}</div>
  if (!d) return <p role="status">Loading…</p>
  return (
    <section aria-labelledby="cl-src" data-cl-sources>
      <h2 id="cl-src">Lead Sources</h2>
      <p className="v-small">Every source feeds the same lead inbox, with its channel and campaign kept on each lead. Real sources accept leads only while public intake is on. Test sources write test data only.</p>
      {err && <div className="v-alert v-alert--err" role="alert">{err}</div>}
      <div className="v-panel" data-source="website"><div className="v-row"><h3 style={{ margin: 0 }}>{d.website.label}</h3><span className="v-tag">{d.website.state}</span></div>
        <p className="v-small">{d.website.path} · attribution from utm_source, utm_medium, utm_campaign and the referring site.</p></div>
      <h3>Connections</h3>
      <div className="v-scroll"><table className="v-table" data-providers><thead><tr><th>Source</th><th>Status</th><th>What it needs</th></tr></thead>
        <tbody>{d.providers.map((p) => <tr key={p.key} data-provider={p.key}><td>{p.label}</td><td>{p.ready ? 'Available' : 'Waiting for account connection'}</td><td className="v-small">{p.ready ? p.how : p.needs}</td></tr>)}</tbody></table></div>
      <form className="v-card" onSubmit={create} style={{ margin: '14px 0' }} data-add-source>
        <h3>Add a source</h3>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label>Type<select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })} data-new-provider>
            {d.providers.map((p) => <option key={p.key} value={p.key}>{p.label}{p.ready ? '' : ' (needs connection)'}</option>)}</select></label>
          <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Fall Google form" data-new-name /></label>
          <label>Campaign<input value={form.campaign} onChange={(e) => setForm({ ...form, campaign: e.target.value })} placeholder="utm_campaign" /></label>
          <label className="v-small" style={{ display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={form.test} onChange={(e) => setForm({ ...form, test: e.target.checked })} data-new-test /> Test source (test data only)</label>
          <button className="v-btn v-btn--black v-btn--sm" type="submit" data-new-create>Add source</button>
        </div>
      </form>
      {fresh?.secret && <div className="v-alert" role="status" style={{ background: '#FFF4D6' }} data-fresh-secret>
        <b>Copy this key now - it is shown only once.</b> {fresh.provider === 'google_ads' ? 'Paste it as the key in the Google Ads lead form webhook.' : 'Use it to sign each request (see the source details).'}
        <br /><code style={{ wordBreak: 'break-all' }}>{fresh.secret}</code><br />
        <button type="button" className="v-btn v-btn--link" onClick={() => setFresh(null)}>I copied it</button></div>}
      {d.sources.map((s) => <SourceCard key={s.id} orgId={orgId} s={s} targets={d.targets} onChanged={load} />)}
      {!d.sources.length && <p className="v-muted">No sources added yet.</p>}
    </section>
  )
}

// ── Funnels & Campaigns ─────────────────────────────────────────────────────
function Funnels({ orgId, q, test }) {
  const [r, setR] = useState(null)
  const [u, setU] = useState({ utm_source: 'facebook', utm_medium: 'paid_social', utm_campaign: '' })
  useEffect(() => { api.get(`/volt/ops/${orgId}/leads/report`, { params: q }).then(setR).catch(() => {}) }, [orgId, test]) // eslint-disable-line react-hooks/exhaustive-deps
  const site = 'https://voltmarketing.live/volt/check-coverage'
  const link = useMemo(() => {
    const p = new URLSearchParams(Object.entries(u).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()]))
    return p.toString() ? `${site}?${p}` : site
  }, [u])
  return (
    <section aria-labelledby="cl-fun" data-cl-funnels>
      <h2 id="cl-fun">Funnels &amp; Campaigns</h2>
      <div className="v-panel"><h3>Customer website funnel</h3>
        <p className="v-small">{site} · 8 questions, one at a time, then contact details and consent.</p>
        <h4>Build a tracked link for an ad or referral</h4>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {Object.keys(u).map((k) => <label key={k} className="v-small">{k}<input value={u[k]} onChange={(e) => setU({ ...u, [k]: e.target.value })} data-utm={k} /></label>)}
        </div>
        <p className="v-small">Link: <code data-utm-link style={{ wordBreak: 'break-all' }}>{link}</code></p></div>
      <div className="v-panel"><h3>Campaigns</h3>
        <div className="v-scroll"><table className="v-table" data-campaigns><thead><tr><th>Campaign</th><th>Leads</th><th>Appointments</th><th>Enrolled</th></tr></thead>
          <tbody>{!r ? null : r.by_campaign.length === 0 ? <tr><td colSpan={4} className="v-muted">No leads yet.</td></tr>
            : r.by_campaign.map((c) => <tr key={c.campaign}><td>{c.campaign}</td><td>{c.leads}</td><td>{c.appointments}</td><td>{c.enrolled}</td></tr>)}</tbody></table></div></div>
    </section>
  )
}

// ── Assignments ─────────────────────────────────────────────────────────────
function Assignments({ orgId, q, test }) {
  const [r, setR] = useState(null)
  const [tick, setTick] = useState(0)
  useEffect(() => { api.get(`/volt/ops/${orgId}/leads/report`, { params: q }).then(setR).catch(() => {}) }, [orgId, test, tick]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section aria-labelledby="cl-as" data-cl-assignments>
      <h2 id="cl-as">Assignments</h2>
      <h3>Unassigned leads</h3>
      <LeadsInbox orgId={orgId} test={test} q={q} unassignedOnly onChanged={() => setTick((n) => n + 1)} />
      <h3>Agent workload</h3>
      <div className="v-scroll"><table className="v-table" data-agent-load><thead><tr><th>Agent</th><th>Assigned</th><th>Enrolled</th><th>Conversion</th></tr></thead>
        <tbody>{!r ? null : r.agents.length === 0 ? <tr><td colSpan={4} className="v-muted">No assigned leads yet.</td></tr>
          : r.agents.map((x) => <tr key={x.name}><td>{x.name}</td><td>{x.assigned}</td><td>{x.enrolled}</td><td>{x.conversion_pct == null ? '—' : `${x.conversion_pct}%`}</td></tr>)}</tbody></table></div>
      <p className="v-small v-muted">Add or remove agents in Settings → Team.</p>
    </section>
  )
}

// ── Responses & Follow-Up ───────────────────────────────────────────────────
function FollowUp({ orgId, q, test }) {
  const [f, setF] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => { api.get(`/volt/ops/${orgId}/leads/followup`, { params: q }).then(setF).catch((e) => setErr(msg(e))) }, [orgId, test]) // eslint-disable-line react-hooks/exhaustive-deps
  const table = (rows, extra) => (
    <div className="v-scroll"><table className="v-table"><thead><tr><th>Lead</th><th>Assigned to</th><th>Status</th><th>{extra[0]}</th></tr></thead>
      <tbody>{rows.length === 0 ? <tr><td colSpan={4} className="v-muted">None.</td></tr> : rows.map((l) => <tr key={l.id}><td>{l.name}</td><td>{l.assigned_to || 'Unassigned'}</td>
        <td>{l.status_label}</td><td className="v-small">{extra[1](l)}</td></tr>)}</tbody></table></div>)
  return (
    <section aria-labelledby="cl-fu" data-cl-followup>
      <h2 id="cl-fu">Responses &amp; Follow-Up</h2>
      <p className="v-small"><b>Nothing here contacts a customer.</b> Customer texts, emails and calls stay off until outreach rules, consent wording and calling/texting compliance are final.</p>
      {err && <div className="v-alert v-alert--err" role="alert">{err}</div>}
      {f && <>
        <Kpis items={[[f.response.never_contacted, 'Waiting for first contact'], [f.response.median_minutes == null ? '—' : `${f.response.median_minutes} min`, 'Median time to first contact'],
          [f.response.within_15_min, 'Reached within 15 min'], [f.follow_ups_due.length, 'Follow-ups due (24h)']]} />
        <h3>Waiting for first contact</h3>{table(f.waiting_first_contact, ['Waiting', (l) => `${l.waiting_minutes} min · since ${fmtCT(l.created_at)}`])}
        <h3>Follow-ups due</h3>{table(f.follow_ups_due, ['Due', (l) => `${fmtCT(l.next_follow_up_at)}${l.overdue ? ' · overdue' : ''}`])}
        <h3>Upcoming appointments</h3>{table(f.upcoming_appointments, ['When', (l) => fmtCT(l.appointment_at)])}
        <h3>Alerts and outreach</h3>
        <div className="v-scroll"><table className="v-table" data-channels><thead><tr><th>What</th><th>State</th><th>Notes</th></tr></thead>
          <tbody>{f.channels.map((c) => <tr key={c.key} data-channel={c.key}><td>{c.label}</td><td>{c.state === 'prepared' ? 'Prepared · off' : 'Off · not authorized'}</td><td className="v-small">{c.note}</td></tr>)}</tbody></table></div>
      </>}
    </section>
  )
}

// ── Reporting ───────────────────────────────────────────────────────────────
function Reporting({ orgId, q, test }) {
  const [r, setR] = useState(null)
  useEffect(() => { api.get(`/volt/ops/${orgId}/leads/report`, { params: q }).then(setR).catch(() => {}) }, [orgId, test]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!r) return <p role="status">Loading…</p>
  return (
    <section aria-labelledby="cl-rep" data-cl-reporting>
      <h2 id="cl-rep">Reporting{test ? ' (test data)' : ''}</h2>
      <Kpis items={[[r.total, 'Total'], ...STATUSES.map(([k, l]) => [r.by_status[k] || 0, l])]} />
      <h3>By channel</h3>
      <div className="v-scroll"><table className="v-table"><thead><tr><th>Channel</th><th>Leads</th><th>Qualified</th><th>Appointments</th><th>Enrolled</th></tr></thead>
        <tbody>{r.by_channel.length === 0 ? <tr><td colSpan={5} className="v-muted">No leads yet.</td></tr>
          : r.by_channel.map((c) => <tr key={c.channel}><td>{c.label}</td><td>{c.leads}</td><td>{c.qualified}</td><td>{c.appointments}</td><td>{c.enrolled}</td></tr>)}</tbody></table></div>
      <h3>By campaign</h3>
      <div className="v-scroll"><table className="v-table"><thead><tr><th>Campaign</th><th>Leads</th><th>Appointments</th><th>Enrolled</th></tr></thead>
        <tbody>{r.by_campaign.length === 0 ? <tr><td colSpan={4} className="v-muted">No leads yet.</td></tr>
          : r.by_campaign.map((c) => <tr key={c.campaign}><td>{c.campaign}</td><td>{c.leads}</td><td>{c.appointments}</td><td>{c.enrolled}</td></tr>)}</tbody></table></div>
      <h3>Agents</h3>
      <div className="v-scroll"><table className="v-table"><thead><tr><th>Agent</th><th>Assigned</th><th>Enrolled</th><th>Conversion</th></tr></thead>
        <tbody>{r.agents.length === 0 ? <tr><td colSpan={4} className="v-muted">No assigned leads yet.</td></tr>
          : r.agents.map((x) => <tr key={x.name}><td>{x.name}</td><td>{x.assigned}</td><td>{x.enrolled}</td><td>{x.conversion_pct == null ? '—' : `${x.conversion_pct}%`}</td></tr>)}</tbody></table></div>
      <p className="v-small v-muted">{r.cost_note}</p>
    </section>
  )
}

// ── Settings ────────────────────────────────────────────────────────────────
function Settings({ orgId }) {
  const base = `/volt/ops/${orgId}/leads`
  const [s, setS] = useState(null)
  const [err, setErr] = useState('')
  const [pick, setPick] = useState({ user_id: '', role: 'agent' })
  const [cleared, setCleared] = useState(null)
  const load = () => api.get(`${base}/settings`).then(setS).catch((e) => setErr(msg(e)))
  useEffect(() => { load() }, [orgId]) // eslint-disable-line react-hooks/exhaustive-deps
  const toggleIntake = () => {
    const enable = !s.intake_enabled
    const review_note = enable ? window.prompt('Who approved the privacy copy, eligibility claims, consent and agent routing? (required)') : ''
    if (enable && !review_note) return
    api.put(`${base}/settings`, { intake_enabled: enable, reviewed: enable, review_note }).then(load).catch((e) => setErr(msg(e)))
  }
  const grant = (body) => api.post(`${base}/grants`, body).then(load).catch((e) => setErr(msg(e)))
  if (err && !s) return <div className="v-alert v-alert--err" role="alert">{err}</div>
  if (!s) return <p role="status">Loading…</p>
  const on = new Set(s.grants.map((g) => g.user_id))
  return (
    <section aria-labelledby="cl-set" data-cl-settings>
      <h2 id="cl-set">Settings</h2>
      {err && <div className="v-alert v-alert--err" role="alert">{err}</div>}
      <div className="v-panel" data-intake>
        <h3>Public intake</h3>
        <p><strong>{s.intake_enabled ? 'ON' : 'OFF'}</strong>{s.reviewed_at ? ` · last approved ${fmtCT(s.reviewed_at)}` : ''}</p>
        <p className="v-small">The customer website is always visible. This switch decides whether it (and real lead sources) accept requests. Before turning it on, the owner confirms:</p>
        <ul className="v-small">{s.checklist.map((c) => <li key={c}>{c}</li>)}</ul>
        {s.can_intake ? <button type="button" className="v-btn v-btn--black v-btn--sm" onClick={toggleIntake}>{s.intake_enabled ? 'Turn intake off' : 'Turn intake on'}</button>
          : <p className="v-small v-muted">Only the owner can change this.</p>}
      </div>
      <div className="v-panel" data-team>
        <h3>Team</h3>
        <p className="v-small v-muted">Managers see every lead. Agents see only leads assigned to them. Recruiting roles do not give Consumer Leads access. {s.can_make_manager ? '' : 'Only the owner can add or remove managers.'}</p>
        <ul className="v-small">{s.grants.map((g) => <li key={g.user_id} data-member={g.user_id}>{g.name} · {g.role === 'manager' ? 'Manager' : 'Agent'} · {(g.role === 'agent' || s.can_make_manager)
          && <button type="button" className="v-btn v-btn--link" onClick={() => grant({ user_id: g.user_id, active: false })}>Remove</button>}</li>)}
          {!s.grants.length && <li className="v-muted">No managers or agents yet (the owner and VOLT&apos;s named operators always have access).</li>}</ul>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label>Person<select value={pick.user_id} onChange={(e) => setPick({ ...pick, user_id: e.target.value })} data-team-person>
            <option value="">Choose a VOLT member…</option>{s.candidates.filter((c) => !on.has(c.id)).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
          <label>Role<select value={pick.role} onChange={(e) => setPick({ ...pick, role: e.target.value })} data-team-role>
            <option value="agent">Agent</option>{s.can_make_manager && <option value="manager">Manager</option>}</select></label>
          <button type="button" className="v-btn v-btn--black v-btn--sm" disabled={!pick.user_id} onClick={() => grant(pick).then(() => setPick({ user_id: '', role: 'agent' }))} data-team-add>Add</button>
        </div>
      </div>
      {s.can_intake !== undefined && <SheetsPanel orgId={orgId} business="consumer" isOwner={!!s.can_intake} />}
      <div className="v-panel" data-test-data>
        <h3>Test data</h3>
        <p className="v-small">Website-preview submissions and test sources write test data only. It never appears in real leads, reports or the workspace card.</p>
        <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => api.post(`${base}/test-data/clear`).then(setCleared).catch((e) => setErr(msg(e)))} data-clear-test>Remove all test leads</button>
        {cleared && <p className="v-small" role="status">Removed {cleared.removed} test lead(s).</p>}
      </div>
    </section>
  )
}

// ── Preview ─────────────────────────────────────────────────────────────────
function Preview({ orgId }) {
  return (
    <div data-cl-preview>
      <div role="note" style={{ background: '#FFF4D6', border: '2px solid #E0B44C', padding: '10px 16px', fontSize: 16 }} data-preview-banner>
        <b>Preview - test mode.</b> This is the customer website as visitors see it. Anything you send here is saved as <b>test data</b> only:
        it never reaches the real inbox or reports, and nothing is sent to anyone. <Link to={`/consumer-leads/${orgId}/leads?data=test`}>See test leads</Link> ·{' '}
        <Link to={`/consumer-leads/${orgId}`}>Back to the back office</Link>
      </div>
      <CoverageFunnel preview={{ orgId }} />
    </div>
  )
}

export default function ConsumerLeadsWorkspace() {
  const { orgId } = useParams()
  const [summary, setSummary] = useState(null)
  const [err, setErr] = useState('')
  const [test, setTest, q] = useDataMode()
  useVoltTitle()
  useEffect(() => { api.get(`/volt/ops/${orgId}/leads/summary`).then(setSummary).catch((e) => setErr(msg(e))) }, [orgId])
  useEffect(() => { document.title = 'Consumer Leads · VOLT Marketing' }, [])
  if (err && !summary) {
    return <div className="volt"><div className="v-wrap v-center"><div data-cl-denied>
      <h2>Consumer Leads</h2><div className="v-alert v-alert--err" role="alert">{err}</div>
      <p><Link to="/workspaces">Back to Program Access</Link></p></div></div></div>
  }
  if (!summary) return <div className="volt v-wrap" role="status">Loading Consumer Leads…</div>
  const testOk = summary.can_preview
  const t = testOk && test
  const qq = t ? q : {}
  return (
    <Routes>
      <Route path="preview" element={testOk ? <Preview orgId={orgId} /> : <Navigate to={`/consumer-leads/${orgId}`} replace />} />
      <Route path="*" element={
        <Shell orgId={orgId} summary={summary} test={t} setTest={setTest}>
          <Routes>
            <Route index element={<Overview orgId={orgId} summary={summary} test={t} q={qq} />} />
            <Route path="leads" element={<><h2>Leads</h2><LeadsInbox orgId={orgId} test={t} q={qq} /></>} />
            <Route path="sources" element={summary.access === 'all' ? <LeadSources orgId={orgId} /> : <p>Lead sources are managed by Consumer Leads managers.</p>} />
            <Route path="funnels" element={<Funnels orgId={orgId} q={qq} test={t} />} />
            <Route path="assignments" element={summary.access === 'all' ? <Assignments orgId={orgId} q={qq} test={t} /> : <p>Assignments are made by Consumer Leads managers.</p>} />
            <Route path="followup" element={<FollowUp orgId={orgId} q={qq} test={t} />} />
            <Route path="reporting" element={<Reporting orgId={orgId} q={qq} test={t} />} />
            <Route path="dev" element={summary.can_develop
              ? <Development orgId={orgId} campaign="consumer_leads" backTo={`/consumer-leads/${orgId}`} />
              : <p>Development requests are limited to the owner and VOLT&apos;s named operators.</p>} />
            <Route path="settings" element={summary.can_settings ? <Settings orgId={orgId} /> : <p>Settings are managed by the owner and VOLT&apos;s named operators.</p>} />
            <Route path="*" element={<Navigate to={`/consumer-leads/${orgId}`} replace />} />
          </Routes>
        </Shell>} />
    </Routes>
  )
}
