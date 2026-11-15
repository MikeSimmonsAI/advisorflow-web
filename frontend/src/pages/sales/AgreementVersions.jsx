/**
 * AGREEMENT VERSIONS — owners / God Mode, on My Team.
 *
 * Paste the agreement and Exhibit A version that counsel approved, then mark
 * it approved for signing. Text still marked "DRAFT FOR LEGAL REVIEW" or
 * "NOT FOR SIGNATURE" is refused by the server. Each contractor's copy gets
 * an Exhibit A built from the commission rules for THEIR seat only.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'

const card = { background: '#fff', border: '1px solid #dce5ea', borderRadius: 12, padding: 16, marginBottom: 14 }
const btn = { border: '1px solid #c9d7df', background: '#fff', borderRadius: 8, padding: '6px 10px', cursor: 'pointer', fontSize: 13, marginRight: 6, marginTop: 6 }
const input = { ...btn, cursor: 'text', marginTop: 4, width: '100%', boxSizing: 'border-box' }
function errText(e) { return (e && (e.detail || e.message)) || 'Something went wrong.' }

export default function AgreementVersions() {
  const [list, setList] = useState([])
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ title: 'Independent Sales Representative Agreement', version_label: '', exhibit_version: '', body_text: '', requires_countersign: false })
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const load = () => api.get('/sales-team/agreement-templates', { noOrgContext: true }).then(r => setList(r.templates || [])).catch(e => setErr(errText(e)))
  useEffect(() => { load() }, [])
  const run = async (fn, ok) => { setErr(''); setMsg(''); try { await fn(); setMsg(ok); await load() } catch (e) { setErr(errText(e)) } }
  return (
    <div style={card}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <h3 style={{ margin: 0 }}>Agreement versions</h3>
        <button style={btn} onClick={() => setOpen(o => !o)} aria-expanded={open}>{open ? 'Close' : 'Add a version'}</button>
      </div>
      {(err || msg) && <p role={err ? 'alert' : 'status'} style={{ color: err ? '#a1340c' : '#0b6b4a', fontWeight: 600 }}>{err || msg}</p>}
      {list.length === 0 && <p style={{ color: '#607486' }}>No versions yet. Contractors can't be sent an agreement until one is approved for signing.</p>}
      {list.map(t => (
        <div key={t.id} style={{ borderTop: '1px solid #e6edf1', padding: '8px 0' }}>
          <b>{t.title}</b> · v{t.version_label} · Exhibit A {t.exhibit_version} · <b>{t.status === 'approved' ? 'Approved for signing' : t.status === 'draft' ? 'Draft' : 'Retired'}</b>
          {t.requires_countersign ? ' · needs countersignature' : ''}
          {t.is_draft_text && <span style={{ color: '#a1340c' }}> · text is marked DRAFT FOR LEGAL REVIEW — cannot be approved</span>}
          <div>
            {t.status === 'draft' && !t.is_draft_text && <button style={btn} onClick={() => {
              if (window.confirm('Confirm counsel approved version ' + t.version_label + ' (Exhibit A ' + t.exhibit_version + ') for signing.'))
                run(() => api.post('/sales-team/agreement-templates/' + t.id + '/approve', { approved_for_signing: true }), 'Approved for signing.')
            }}>Approve for signing</button>}
            {t.status !== 'retired' && <button style={btn} onClick={() => run(() => api.post('/sales-team/agreement-templates/' + t.id + '/retire', {}), 'Retired.')}>Retire</button>}
          </div>
        </div>
      ))}
      {open && (
        <form onSubmit={e => { e.preventDefault(); run(() => api.post('/sales-team/agreement-templates', f), 'Saved as a draft. Approve it for signing when counsel signs off.').then(() => setOpen(false)) }}
              style={{ borderTop: '1px solid #e6edf1', paddingTop: 8 }}>
          <label>Title<input style={input} value={f.title} onChange={e => setF({ ...f, title: e.target.value })} required /></label>
          <div style={{ display: 'flex', gap: 8 }}>
            <label style={{ flex: 1 }}>Agreement version<input style={input} value={f.version_label} onChange={e => setF({ ...f, version_label: e.target.value })} placeholder="e.g. 2026-12-01" required /></label>
            <label style={{ flex: 1 }}>Exhibit A version<input style={input} value={f.exhibit_version} onChange={e => setF({ ...f, exhibit_version: e.target.value })} placeholder="e.g. A-2026-12-01" required /></label>
          </div>
          <label>Agreement text (paste the approved text; Exhibit A is added per person)
            <textarea style={{ ...input, minHeight: 160 }} value={f.body_text} onChange={e => setF({ ...f, body_text: e.target.value })} required /></label>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6 }}>
            <input type="checkbox" checked={f.requires_countersign} onChange={e => setF({ ...f, requires_countersign: e.target.checked })} />
            EvoSys Pro countersigns after the contractor
          </label>
          <button type="submit" style={{ ...btn, background: '#087f83', color: '#fff', borderColor: '#087f83' }}>Save draft</button>
        </form>
      )}
    </div>
  )
}
