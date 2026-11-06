import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, fetchObjectUrl } from '../../api/client'
import { BASE, useJewelry } from './JewelryShell'
import { Empty, ErrorNote, Field, Loading, Modal, Pill, SEG_CLASS, ago, errText, n, useApi, useToast } from './jwui'

export function SegTags({ keys, status }) {
  const label = Object.fromEntries((status.segments || []).map(s => [s.key, s.label]))
  return <>{(keys || []).map(k => <span key={k} className={`jw-tag jw-tag--${SEG_CLASS[k] || 'general'}`}>{(label[k] || k).replace(/s$/, '').replace(' Customer', '')}</span>)}</>
}

export function CustomerForm({ initial, status, onSaved, onCancel }) {
  const [f, setF] = useState(() => ({
    first_name: '', last_name: '', phone: '', email: '', street_address: '', city: '', state: '', zip_code: '',
    segments: [], interests: '', follow_up: 'new', notes: '', assigned_to_id: '', ...(initial || {}),
    interests_text: (initial?.interests || []).join(', '),
  }))
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const toggleSeg = (k) => setF({ ...f, segments: f.segments.includes(k) ? f.segments.filter(s => s !== k) : [...f.segments, k] })
  const save = async (allowDup = false) => {
    setBusy(true); setErr(null)
    const body = { first_name: f.first_name, last_name: f.last_name, phone: f.phone, email: f.email,
                   street_address: f.street_address, city: f.city, state: f.state, zip_code: f.zip_code,
                   segments: f.segments, interests: f.interests_text.split(',').map(s => s.trim()).filter(Boolean),
                   follow_up: f.follow_up, notes: f.notes }
    if (status.can_manage) body.assigned_to_id = f.assigned_to_id || null
    if (allowDup) body.allow_duplicate = true
    try {
      const out = initial?.id ? await api.patch('/jewelry-ops/customers/' + initial.id, body)
                              : await api.post('/jewelry-ops/customers', body)
      onSaved(out)
    } catch (e) {
      setErr(e.detail?.duplicate_id ? { dup: e.detail.duplicate_id, text: e.detail.message } : { text: errText(e) })
    } finally { setBusy(false) }
  }
  const assignable = (status.segments || []).filter(s => !s.computed)
  return (
    <form onSubmit={e => { e.preventDefault(); save() }}>
      <div className="jw-form2">
        <Field label="First name"><input className="jw-input" value={f.first_name || ''} onChange={set('first_name')} maxLength={80} /></Field>
        <Field label="Last name"><input className="jw-input" value={f.last_name || ''} onChange={set('last_name')} maxLength={80} /></Field>
        <Field label="Mobile phone"><input className="jw-input" value={f.phone || ''} onChange={set('phone')} placeholder="(276) 555-0100" /></Field>
        <Field label="Email"><input className="jw-input" type="email" value={f.email || ''} onChange={set('email')} /></Field>
        <Field label="Street address"><input className="jw-input" value={f.street_address || ''} onChange={set('street_address')} /></Field>
        <div className="jw-form3">
          <Field label="City"><input className="jw-input" value={f.city || ''} onChange={set('city')} /></Field>
          <Field label="State"><input className="jw-input" value={f.state || ''} onChange={set('state')} maxLength={2} /></Field>
          <Field label="ZIP"><input className="jw-input" value={f.zip_code || ''} onChange={set('zip_code')} maxLength={10} /></Field>
        </div>
      </div>
      <Field label="Segments">
        <div className="jw-chips">{assignable.map(s => (
          <button type="button" key={s.key} className={`jw-chip ${f.segments.includes(s.key) ? 'on' : ''}`} onClick={() => toggleSeg(s.key)}>{s.label}</button>
        ))}</div>
      </Field>
      <div className="jw-form2">
        <Field label="Interests" hint="Separate with commas, e.g. Morgan dollars, gold chains"><input className="jw-input" value={f.interests_text} onChange={set('interests_text')} /></Field>
        <Field label="Follow-up status">
          <select className="jw-input" value={f.follow_up || 'new'} onChange={set('follow_up')}>
            {(status.follow_up_statuses || []).map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </Field>
        {status.can_manage && (
          <Field label="Assigned to">
            <select className="jw-input" value={f.assigned_to_id || ''} onChange={set('assigned_to_id')}>
              <option value="">Not assigned</option>
              {(status.team || []).map(m => <option key={m.id} value={m.id}>{m.name} ({m.role_label})</option>)}
            </select>
          </Field>
        )}
      </div>
      <Field label="Notes"><textarea className="jw-input" rows={3} value={f.notes || ''} onChange={set('notes')} /></Field>
      <p className="jw-note">Marketing permission is set separately on the customer’s page, with where the permission came from. New customers are never texted or emailed automatically.</p>
      {err && (
        <div className="jw-error" role="alert">
          <span>{err.text}</span>
          {err.dup && <Link className="jw-btn jw-btn--sm" to={`${BASE}/customers/${err.dup}`}>Open existing</Link>}
          {err.dup && status.can_manage && <button type="button" className="jw-btn jw-btn--sm" onClick={() => save(true)}>Add anyway</button>}
        </div>
      )}
      <div className="jw-row jw-row--end">
        <button type="button" className="jw-btn" onClick={onCancel}>Cancel</button>
        <button className="jw-btn jw-btn--gold" disabled={busy}>{busy ? 'Saving…' : initial?.id ? 'Save changes' : 'Add customer'}</button>
      </div>
    </form>
  )
}

function ImportWizard({ onClose, onDone }) {
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [mapping, setMapping] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const run = async (confirm, map = mapping) => {
    if (!file) return
    setBusy(true); setErr(null)
    const fd = new FormData()
    fd.append('file', file)
    if (map) fd.append('mapping', JSON.stringify(map))
    try {
      const out = await api.post('/jewelry-ops/customers/import/' + (confirm ? 'confirm' : 'preview'), fd)
      if (confirm) onDone(out)
      else { setPreview(out); setMapping(out.mapping) }
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const LABEL = { ready: 'Ready', duplicate: 'Duplicate - skipped', skip: 'Skipped' }
  return (
    <Modal title="Import customers" onClose={onClose} wide>
      {!preview && (
        <>
          <p>Upload a .csv or .xlsx file. You’ll see every row before anything is saved. Duplicates (same phone or email) are skipped, and marketing permission is only recorded where the file says “yes”.</p>
          <input type="file" accept=".csv,.xlsx" onChange={e => setFile(e.target.files?.[0] || null)} />
          <ErrorNote>{err}</ErrorNote>
          <div className="jw-row jw-row--end"><button className="jw-btn" onClick={onClose}>Cancel</button>
            <button className="jw-btn jw-btn--gold" disabled={!file || busy} onClick={() => run(false)}>{busy ? 'Reading…' : 'Preview'}</button></div>
        </>
      )}
      {preview && (
        <>
          <div className="jw-kpis jw-kpis--4">
            <div className="jw-mini"><span>Ready to import</span><b>{n(preview.summary.ready)}</b></div>
            <div className="jw-mini"><span>Duplicates</span><b>{n(preview.summary.duplicate)}</b></div>
            <div className="jw-mini"><span>Skipped</span><b>{n(preview.summary.skip)}</b></div>
            <div className="jw-mini"><span>With text permission</span><b>{n(preview.summary.sms_permission)}</b></div>
          </div>
          {preview.warnings.map(w => <div key={w} className="jw-error">{w}</div>)}
          <details className="jw-details"><summary>Column matching</summary>
            <div className="jw-form3">{preview.fields.map(fl => (
              <Field key={fl} label={fl.replace(/_/g, ' ')}>
                <select className="jw-input" value={mapping[fl] ?? ''} onChange={e => setMapping({ ...mapping, [fl]: e.target.value === '' ? null : Number(e.target.value) })}>
                  <option value="">— not in file —</option>
                  {preview.header.map((h, i) => <option key={i} value={i}>{h || `Column ${i + 1}`}</option>)}
                </select>
              </Field>
            ))}</div>
            <button className="jw-btn jw-btn--sm" onClick={() => run(false)} disabled={busy}>Re-check with these columns</button>
          </details>
          <div className="jw-table-wrap jw-table-wrap--tall"><table className="jw-table">
            <thead><tr><th>Row</th><th>Status</th><th>Name</th><th>Phone</th><th>Email</th><th>Segments</th><th>Text OK</th><th>Issues</th></tr></thead>
            <tbody>{preview.rows.map(r => (
              <tr key={r.row} className={r.status !== 'ready' ? 'muted' : ''}>
                <td>{r.row}</td><td><Pill tone={r.status === 'ready' ? 'ok' : 'warn'}>{LABEL[r.status]}</Pill></td>
                <td>{[r.first_name, r.last_name].filter(Boolean).join(' ')}</td><td>{r.phone}</td><td>{r.email}</td>
                <td>{r.segments.join(', ')}</td><td>{r.sms_permission ? 'Yes' : '—'}</td><td>{r.issues.join('; ')}</td>
              </tr>
            ))}</tbody>
          </table></div>
          {preview.preview_limited && <p className="jw-note">Showing the first 500 rows. All {n(preview.total)} rows are checked on import.</p>}
          <ErrorNote>{err}</ErrorNote>
          <div className="jw-row jw-row--end"><button className="jw-btn" onClick={() => setPreview(null)}>Choose another file</button>
            <button className="jw-btn jw-btn--gold" disabled={busy || !preview.summary.ready} onClick={() => run(true)}>{busy ? 'Importing…' : `Import ${n(preview.summary.ready)} customers`}</button></div>
        </>
      )}
    </Modal>
  )
}

export default function Customers() {
  const { status } = useJewelry()
  const [params, setParams] = useSearchParams()
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [qd, setQd] = useState('')
  const [seg, setSeg] = useState('all')
  const [fu, setFu] = useState('all')
  const [toast, toastNode] = useToast()
  useEffect(() => { const t = setTimeout(() => setQd(q), 250); return () => clearTimeout(t) }, [q])
  const path = `/jewelry-ops/customers?segment=${seg}&follow_up=${fu}` + (qd ? `&q=${encodeURIComponent(qd)}` : '')
  const { data, error, loading, reload } = useApi(path, [path])
  const adding = params.get('add') === '1'
  const importing = params.get('import') === '1' && status.can_manage
  const close = () => setParams({})
  const exportCsv = async () => {
    try {
      const url = await fetchObjectUrl('/jewelry-ops/customers/export')
      const a = document.createElement('a'); a.href = url; a.download = 'customers.csv'; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 2000)
    } catch (e) { toast(errText(e), 'err') }
  }
  const segs = status.segments || []
  return (
    <div>
      <div className="jw-head">
        <div><div className="jw-eyebrow">Customer Vault</div><h1>Customers</h1>
          <p>{data ? `${n(data.all_total)} customer${data.all_total === 1 ? '' : 's'}${status.can_manage ? ' in this business' : ' assigned to you'}` : ' '}</p></div>
        <div className="jw-row">
          {status.can_manage && <button className="jw-btn" onClick={exportCsv}>Export CSV</button>}
          {status.can_manage && <button className="jw-btn" onClick={() => setParams({ import: '1' })}>Import</button>}
          <button className="jw-btn jw-btn--gold" onClick={() => setParams({ add: '1' })}>+ Add customer</button>
        </div>
      </div>
      <div className="jw-chips jw-chips--wrap">
        <button className={`jw-chip ${seg === 'all' ? 'on' : ''}`} onClick={() => setSeg('all')}>All <span>{data ? n(data.all_total) : ''}</span></button>
        {segs.map(s => <button key={s.key} className={`jw-chip ${seg === s.key ? 'on' : ''}`} onClick={() => setSeg(s.key)}>{s.label} <span>{data ? n(data.segment_counts[s.key] || 0) : ''}</span></button>)}
      </div>
      <div className="jw-card">
        <div className="jw-row jw-filters">
          <input className="jw-input" type="search" placeholder="Search name, phone, email, city or interest…" value={q} onChange={e => setQ(e.target.value)} aria-label="Search customers" />
          <select className="jw-input" value={fu} onChange={e => setFu(e.target.value)} aria-label="Follow-up status">
            <option value="all">All follow-up statuses</option>
            {(status.follow_up_statuses || []).map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </div>
        <ErrorNote onRetry={reload}>{error}</ErrorNote>
        {loading && !data && <Loading />}
        {data && data.customers.length === 0 && (
          <Empty title={data.all_total === 0 ? 'No customers yet' : 'No customers match'}>
            {data.all_total === 0 ? 'Add your first customer or import a list.' : 'Try a different search or segment.'}
          </Empty>
        )}
        {data && data.customers.length > 0 && (
          <div className="jw-table-wrap"><table className="jw-table">
            <thead><tr><th>Customer</th><th>Segments</th><th>Interests</th><th>Last seen</th><th>Follow-up</th><th>Contact OK</th></tr></thead>
            <tbody>{data.customers.map(c => (
              <tr key={c.id} className="click" tabIndex={0} onClick={() => nav(`${BASE}/customers/${c.id}`)}
                  onKeyDown={e => { if (e.key === 'Enter') nav(`${BASE}/customers/${c.id}`) }}>
                <td><b>{c.name}</b><small className="jw-sub">{[c.phone, c.city].filter(Boolean).join(' · ')}</small></td>
                <td><SegTags keys={c.segments} status={status} /></td>
                <td className="jw-small">{c.interests.join(', ')}</td>
                <td className="jw-small">{c.last_seen ? ago(c.last_seen) : 'No visits recorded'}</td>
                <td><Pill tone={c.follow_up === 'follow_up_due' ? 'warn' : c.follow_up === 'do_not_contact' ? 'err' : ''}>{(status.follow_up_statuses.find(s => s.key === c.follow_up) || {}).label || c.follow_up}</Pill></td>
                <td className="jw-small">{c.opted_out ? <Pill tone="err">Opted out</Pill> : [c.allow_sms && 'Text', c.allow_email && 'Email'].filter(Boolean).join(' · ') || 'No permission'}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
      {adding && (
        <Modal title="Add a customer" onClose={close} wide>
          <CustomerForm status={status} onCancel={close} onSaved={(c) => { close(); nav(`${BASE}/customers/${c.id}`) }} />
        </Modal>
      )}
      {importing && <ImportWizard onClose={close} onDone={(out) => { close(); reload(); toast(`Imported ${out.created} customers. ${out.skipped_duplicates} duplicates skipped. No messages were sent.`) }} />}
      {toastNode}
    </div>
  )
}
