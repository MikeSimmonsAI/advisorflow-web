/**
 * Leads & Data - bring existing leads in, see how they qualify, start follow-up.
 *
 * Import goes through the platform's ONE importer (POST /intake/batches), and
 * the real wizard (/imports/:id) does column matching, duplicate checks,
 * review and approval - this page never commits rows itself. Qualification is
 * the platform engine's READY / REVIEW / EXCLUDED answer.
 */
import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { BASE, useRoofing } from './RoofingShell'
import AddHomeowner from './AddHomeowner'
import { Avatar, Empty, ErrorNote, Icon, Loading, Panel, Pill, SampleTag, errText, n, useApi, useToast } from './ui'
import './roofing-ops.css'

const STEPS = [
  ['File uploaded', 'CSV or Excel, up to 250,000 rows'],
  ['Match columns', 'Roofing columns are matched automatically'],
  ['Check duplicates', 'Against this file and the homeowners you already have'],
  ['Qualify', 'Ready, needs review, or excluded - with the reason'],
  ['Start follow-up', 'Only homeowners ready to contact are enrolled'],
]

export default function LeadsData() {
  const { status } = useRoofing()
  const [toast, say] = useToast()
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState(0)
  return (
    <div className="rf-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Leads &amp; data</h1>
          <p className="rf-lede">Add a homeowner by hand, or upload the leads you already have. EvoSys organizes and qualifies them; follow-up only starts when you start it.</p>
        </div>
        <div className="rf-head-btns">
          <button className="rf-btn rf-btn--primary rf-add__cta" onClick={() => setAdding(true)} aria-expanded={adding}><Icon name="plus" size={18} />Add Homeowner</button>
          <Link className="rf-btn rf-btn--ghost" to="/imports"><Icon name="file" size={16} />Past imports</Link>
        </div>
      </div>
      <div className="rf-stack">
        {adding && <AddHomeowner onClose={() => setAdding(false)} onSaved={() => setAdded((x) => x + 1)} say={say} />}
        <ImportPanel status={status} />
        <Qualification status={status} say={say} />
        <LeadTable status={status} refresh={added} />
      </div>
      {toast}
    </div>
  )
}

function ImportPanel({ status }) {
  const nav = useNavigate()
  const fileRef = useRef(null)
  const [file, setFile] = useState(null)
  const [listName, setListName] = useState('')
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const extra = [{ key: 'address', label: 'Property address' }, { key: 'source', label: 'Lead source' }]
  const chips = [extra[0], ...(status.fields || []), extra[1]]

  const upload = async () => {
    if (!file) return
    setBusy(true); setErr('')
    const fd = new FormData()
    fd.append('file', file)
    fd.append('source', 'csv')
    if (listName.trim()) fd.append('list_name', listName.trim())
    fd.append('tags', 'roofing')
    fd.append('campaign_purpose', 'roofing follow-up')
    try {
      const b = await api.upload('/intake/batches', fd)
      nav(`/imports/${b.id}?step=2`)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <Panel title="Import & qualify your leads" sub="Your contacts stay yours. Nothing is sent while you import.">
      <div className="rf-import">
        <div className="rf-stack" style={{ gap: 14 }}>
          <div className={`rf-drop ${over ? 'over' : ''}`} role="button" tabIndex={0}
            onClick={() => fileRef.current?.click()}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileRef.current?.click() } }}
            onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]) }}>
            <Icon name="upload" size={30} />
            <b>{file ? file.name : 'Drag & drop your file here'}</b>
            <span>{file ? `${Math.max(1, Math.round(file.size / 1024))} KB, ready to upload` : 'or click to browse. CSV, Excel or tab-separated.'}</span>
            <input ref={fileRef} type="file" hidden accept=".csv,.xlsx,.xlsm,.tsv,.txt"
              onChange={(e) => e.target.files[0] && setFile(e.target.files[0])} />
          </div>
          <label className="rf-label">List name (optional)
            <input className="rf-input" value={listName} onChange={(e) => setListName(e.target.value)} placeholder="e.g. 2025 storm inquiries" maxLength={120} />
          </label>
          {err && <ErrorNote>{err}</ErrorNote>}
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <button className="rf-btn rf-btn--primary" disabled={!file || busy} onClick={upload}>
              <Icon name="upload" size={16} />{busy ? 'Uploading…' : 'Upload and match columns'}
            </button>
            {file && <button className="rf-btn rf-btn--ghost" onClick={() => setFile(null)}>Choose another file</button>}
          </div>
          <div>
            <p className="rf-label" style={{ marginBottom: 8 }}>Roofing fields</p>
            <div className="rf-chips">{chips.map((c) => <span className="rf-chip" key={c.key}>{c.label}</span>)}</div>
            <p className="rf-muted" style={{ fontSize: 13, marginTop: 8 }}>
              Columns named like these are matched automatically. Anything else is kept and shown so you can decide.
            </p>
          </div>
        </div>
        <div>
          <p className="rf-label" style={{ marginBottom: 12 }}>What happens next</p>
          <ol className="rf-checklist">
            {STEPS.map(([t, s], i) => (
              <li key={t}>
                <span className={`rf-dot ${i === 0 && file ? 'done' : ''}`}>{i === 0 && file ? <Icon name="check" size={13} /> : i + 1}</span>
                <div>{t}<span>{s}</span></div>
              </li>
            ))}
          </ol>
          <p className="rf-muted" style={{ fontSize: 13, marginTop: 14 }}>
            Steps 2 to 4 happen in the import screen, where you approve exactly what comes in.
          </p>
        </div>
      </div>
    </Panel>
  )
}

const CHANNEL_LABEL = { sms: 'Text messages', email: 'Email' }

function Qualification({ status, say }) {
  const { data, error, loading, reload } = useApi('/roofing-ops/qualification')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  const enroll = async () => {
    setBusy(true)
    try {
      const r = await api.post('/roofing-ops/outreach/enroll', {})
      setResult(r)
      say(r.enrolled ? `Started follow-up for ${r.enrolled} homeowner${r.enrolled === 1 ? '' : 's'}` : 'No new homeowners were ready to start')
      reload()
    } catch (e) { say(errText(e), 'err') } finally { setBusy(false) }
  }

  return (
    <Panel title="Qualification" sub="The platform’s send checks: consent, opt-outs, valid contact details and your rules."
      actions={status.can_manage && data && (
        <button className="rf-btn rf-btn--primary" onClick={enroll} disabled={busy || !(data.channels?.sms?.ready > 0)}>
          <Icon name="play" size={15} />{busy ? 'Starting…' : 'Start follow-up for ready homeowners'}
        </button>
      )}>
      {error && <ErrorNote onRetry={reload}>{error}</ErrorNote>}
      {!data && loading && <Loading label="Checking your homeowners" />}
      {data && (
        <div className="rf-stack" style={{ gap: 16 }}>
          {!data.texts_on && (
            <div className="rf-note rf-note--warn"><Icon name="alert" size={18} />
              <span>Automated texting is off for this workspace. Homeowners you start are scheduled, but no text goes out until the platform owner switches it on.</span>
            </div>
          )}
          <p className="rf-muted" style={{ fontSize: 13.5 }}>{n(data.in_follow_up)} homeowners are already in follow-up.</p>
          <div className="rf-grid-2">
            {['sms', 'email'].map((ch) => {
              const c = data.channels?.[ch]
              if (!c) return null
              const reasons = [...(c.exclusion_reasons || []).map((r) => ({ ...r, b: 'Excluded' })), ...(c.review_reasons || []).map((r) => ({ ...r, b: 'Review' }))]
                .sort((a, b) => b.count - a.count).slice(0, 5)
              return (
                <div key={ch}>
                  <p className="rf-label">{CHANNEL_LABEL[ch]} <span className="rf-muted" style={{ fontWeight: 500 }}>({n(c.total_selected)} homeowners checked)</span></p>
                  <div className="rf-qual">
                    <div className="rf-qual__b rf-qual__ready"><b>{n(c.ready)}</b><span>Ready</span></div>
                    <div className="rf-qual__b rf-qual__review"><b>{n(c.review)}</b><span>Needs review</span></div>
                    <div className="rf-qual__b rf-qual__excl"><b>{n(c.excluded)}</b><span>Excluded</span></div>
                  </div>
                  {reasons.length > 0 && (
                    <ul className="rf-reasons">
                      {reasons.map((r) => <li key={r.b + r.code}><span>{r.b}: {r.label}</span><b>{n(r.count)}</b></li>)}
                    </ul>
                  )}
                </div>
              )
            })}
          </div>
          {result && (
            <div className="rf-note">
              <Icon name="check" size={18} />
              <div>
                <b>{n(result.enrolled)} started</b> of {n(result.considered)} checked.
                {result.skipped?.length > 0 && (
                  <ul className="rf-reasons">{result.skipped.slice(0, 5).map((s) => <li key={s.reason}><span>{s.reason}</span><b>{n(s.count)}</b></li>)}</ul>
                )}
                {result.note && <p style={{ marginTop: 6 }}>{result.note}</p>}
              </div>
            </div>
          )}
        </div>
      )}
    </Panel>
  )
}

const URG_TONE = { emergency: 'red', high: 'orange', normal: 'blue', low: 'slate' }

function LeadTable({ status, refresh = 0 }) {
  const nav = useNavigate()
  const [stage, setStage] = useState('')
  const [owner, setOwner] = useState('')
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const qs = new URLSearchParams({ page: String(page), page_size: '25', ...(stage ? { stage } : {}), ...(owner ? { owner } : {}), ...(query ? { q: query } : {}) }).toString()
  const { data, error, loading, reload } = useApi(`/roofing-ops/leads?${qs}`, [refresh])
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  const label = Object.fromEntries((status.stages || []).map((s) => [s.key, s.label]))

  return (
    <Panel title="Homeowners" sub="Everyone you can see in this workspace, newest first."
      actions={
        <form className="rf-filters" onSubmit={(e) => { e.preventDefault(); setPage(1); setQuery(q.trim()) }}>
          <div className="rf-search">
            <Icon name="search" size={16} />
            <input className="rf-input" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search homeowners" />
          </div>
          <select className="rf-select" value={stage} onChange={(e) => { setPage(1); setStage(e.target.value) }} aria-label="Stage">
            <option value="">All stages</option>
            {(status.stages || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          {status.can_manage && (
            <select className="rf-select" value={owner} onChange={(e) => { setPage(1); setOwner(e.target.value) }} aria-label="Salesperson">
              <option value="">All salespeople</option>
              <option value="unassigned">Unassigned</option>
              {(status.team || []).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
          )}
        </form>
      }>
      {error && <ErrorNote onRetry={reload}>{error}</ErrorNote>}
      {!data && loading && <Loading />}
      {data && data.total === 0 && (
        <Empty icon="users" title={query || stage || owner ? 'No homeowners match' : 'No homeowners yet'}>
          {query || stage || owner ? 'Try clearing a filter.' : 'Add a homeowner or upload your first list above.'}
        </Empty>
      )}
      {data && data.total > 0 && (
        <>
          <div className="rf-scroll-x">
            <table className="rf-table">
              <thead><tr><th>Homeowner</th><th>Stage</th><th>Roof issue</th><th>Repair or replace</th><th>Urgency</th><th>Owner</th><th>Source</th></tr></thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.lead_id} className="rf-row-link" tabIndex={0}
                    onClick={() => nav(`${BASE}/homeowners/${r.lead_id}`)}
                    onKeyDown={(e) => { if (e.key === 'Enter') nav(`${BASE}/homeowners/${r.lead_id}`) }}>
                    <td>
                      <div className="rf-cell-person">
                        <Avatar name={r.name} size={32} />
                        <div><b>{r.name}</b><span>{r.address || r.phone || r.email}</span></div>
                        {r.is_test && <span className="rf-muted" style={{ fontSize: 12 }}>sample</span>}
                      </div>
                    </td>
                    <td><Pill tone={r.stage === 'won' ? 'green' : r.stage === 'lost' ? 'slate' : 'blue'}>{label[r.stage] || r.stage_label}</Pill>{r.status === 'dnc' && <> <Pill tone="slate">Opted out</Pill></>}</td>
                    <td>{r.roof_issue || <span className="rf-muted">—</span>}</td>
                    <td>{r.service_type ? r.service_type.replace('_', ' ') : <span className="rf-muted">—</span>}</td>
                    <td>{r.urgency ? <Pill tone={URG_TONE[r.urgency]}>{r.urgency}</Pill> : <span className="rf-muted">—</span>}</td>
                    <td>{r.owner_name || <span className="rf-muted">Unassigned</span>}</td>
                    <td className="rf-muted">{r.source || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="rf-pager">
            <span>{n((page - 1) * data.page_size + 1)}–{n(Math.min(page * data.page_size, data.total))} of {n(data.total)}</span>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="rf-btn rf-btn--ghost rf-btn--sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
              <button className="rf-btn rf-btn--ghost rf-btn--sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </div>
        </>
      )}
    </Panel>
  )
}
