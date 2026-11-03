// #470 Stage B - God-only import into EvoSys's master service-prospect pool.
// Upload -> map columns -> check -> review (reject rows) -> commit. Nothing is
// assigned to a cleaning client and nothing is sent; that happens later, on purpose.
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'

const BASE = '/intake/platform-pool/batches'
const err = e => (e && (e.detail || e.message)) || 'Something went wrong'

export default function PlatformPoolImport() {
  const [batches, setBatches] = useState(null)
  const [b, setB] = useState(null)
  const [rows, setRows] = useState(null)
  const [filter, setFilter] = useState('')
  const [pick, setPick] = useState({})
  const [file, setFile] = useState(null)
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [msg, setMsg] = useState(null)

  const loadList = useCallback(() => api.get(BASE).then(r => setBatches(r.items)).catch(e => setFail(err(e))), [])
  const open = useCallback(async id => {
    setFail(null); setMsg(null)
    const x = await api.get(`${BASE}/${id}`); setB(x); setPick({})
    if (x.analysis?.counts) setRows((await api.get(`${BASE}/${id}/rows?limit=1000${filter ? `&filter=${filter}` : ''}`)).items)
    else setRows(null)
  }, [filter])
  useEffect(() => { loadList() }, [loadList])
  useEffect(() => { if (b?.id && b.analysis?.counts) open(b.id) }, [filter]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (fn, ok) => {
    setBusy(true); setFail(null); setMsg(null)
    try { const r = await fn(); if (ok) setMsg(typeof ok === 'function' ? ok(r) : ok) } catch (e) { setFail(err(e)) } finally { setBusy(false) }
  }
  const upload = () => run(async () => {
    const fd = new FormData(); fd.append('file', file); fd.append('source_system', source)
    const x = await api.post(BASE, fd); setB(x); setRows(null); setFile(null); loadList()
  }, 'Uploaded. Check the column matches, then press Check rows.')
  const saveMap = () => run(async () => { setB(await api.put(`${BASE}/${b.id}/mapping`, b.mapping)) }, 'Column matches saved')
  const analyze = () => run(async () => { await api.put(`${BASE}/${b.id}/mapping`, b.mapping); await api.post(`${BASE}/${b.id}/analyze`, {}); await open(b.id) }, 'Rows checked. Nothing has been added yet.')
  const review = decision => run(async () => {
    await api.post(`${BASE}/${b.id}/review`, { row_ids: Object.keys(pick).filter(k => pick[k]), decision }); await open(b.id)
  })
  const commit = () => run(async () => { const r = await api.post(`${BASE}/${b.id}/commit`, {}); await open(b.id); loadList(); return r },
    r => `Added ${r.committed} businesses to the master pool. Skipped: ${r.skipped_duplicate} already there, ${r.skipped_invalid} invalid, ${r.skipped_rejected} rejected. Nothing assigned or sent.`)
  const rollback = () => run(async () => { const r = await api.post(`${BASE}/${b.id}/rollback`, {}); await open(b.id); loadList(); return r },
    r => `Removed ${r.removed}. Kept ${r.kept_assigned} that are already assigned to a client.`)

  const c = b?.analysis?.counts
  const committable = rows ? rows.filter(r => r.committable).length : 0
  return (
    <div className="aq-page">
      <h1 className="aq-h1">Import to the master prospect pool</h1>
      <p className="aq-lede">Commercial businesses that might buy cleaning. They go into EvoSys's own pool only. Nothing is assigned to a client and nothing is sent.</p>
      {fail && <div className="aq-err">{fail}</div>}
      {msg && <div className="aq-ok">{msg}</div>}

      <section className="aq-card">
        <h2>1. Upload a CSV or Excel file</h2>
        <div className="aq-actions">
          <input type="file" accept=".csv,.xlsx" onChange={e => setFile(e.target.files[0] || null)} />
          <input placeholder="Where the records came from (e.g. manual_research)" value={source} onChange={e => setSource(e.target.value)} style={{ flex: 1, minWidth: 240 }} />
          <button className="aq-btn aq-btn--primary aq-btn--sm" disabled={!file || !source.trim() || busy} onClick={upload}>Upload</button>
        </div>
      </section>

      {b && (
        <section className="aq-card" style={{ marginTop: 14 }}>
          <h2>{b.name} <span className="aq-pill">{b.status.replace(/_/g, ' ')}</span></h2>
          <p className="aq-lede">{b.rows} rows · source {b.source_system} · {b.code}</p>
          {b.status !== 'committed' && <>
            <h2 style={{ fontSize: 14 }}>2. Match the columns</h2>
            <div className="aq-grid aq-two">{b.fields.map(f => (
              <label key={f.key} className="aq-field"><span>{f.label}{f.key === 'business_name' ? ' *' : ''}</span>
                <select value={b.mapping[f.key] || ''} onChange={e => setB({ ...b, mapping: { ...b.mapping, [f.key]: e.target.value || null } })}>
                  <option value="">Not in this file</option>{b.headers.map(h => <option key={h} value={h}>{h}</option>)}
                </select></label>))}</div>
            <small style={{ color: 'var(--aq-sub)' }}>Columns you don't match are still kept with each record.</small>
            <div className="aq-actions"><button className="aq-btn aq-btn--sm" disabled={busy} onClick={saveMap}>Save matches</button>
              <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={busy || !b.mapping.business_name} onClick={analyze}>Check rows</button></div>
          </>}
          {c && <>
            <h2 style={{ fontSize: 14, marginTop: 16 }}>3. Review</h2>
            <div className="aq-actions">
              <span className="aq-pill aq-pill--ok">{committable} ready to add</span>
              <span className="aq-pill">{c.invalid} invalid</span>
              <span className="aq-pill">{c.duplicate_in_pool} already in pool</span>
              <span className="aq-pill">{c.duplicate_in_file} repeated in file</span>
              <span className="aq-pill">{c.geo_review} location to review</span>
              <select value={filter} onChange={e => setFilter(e.target.value)}><option value="">All rows</option><option value="invalid">Invalid</option><option value="duplicate">Duplicates</option><option value="rejected">Rejected</option></select>
            </div>
            <div style={{ maxHeight: 420, overflow: 'auto' }}>
              <table className="aq-table"><thead><tr><th /><th>#</th><th>Business</th><th>Type</th><th>Location</th><th>Contact</th><th>Source</th><th>Status</th></tr></thead>
                <tbody>{(rows || []).map(r => (
                  <tr key={r.id}>
                    <td>{r.review !== 'committed' && <input type="checkbox" checked={!!pick[r.id]} onChange={e => setPick({ ...pick, [r.id]: e.target.checked })} />}</td>
                    <td>{r.row}</td>
                    <td><b>{r.business.business_name || '—'}</b><br /><small>{r.business.website || ''}</small></td>
                    <td>{r.business.segment || r.business.category || '—'}</td>
                    <td>{[r.business.city, r.business.state].filter(Boolean).join(', ') || '—'}<br /><small>{r.business.geo_status}</small></td>
                    <td><small>{r.business.email || ''}{r.business.email ? (r.business.email_verified ? ' (verified)' : ' (not verified)') : ''}<br />{r.business.phone_raw || ''}</small></td>
                    <td><small>{r.business.source_url ? <a href={r.business.source_url} target="_blank" rel="noreferrer">link</a> : 'none'}</small></td>
                    <td><small>{r.review === 'committed' ? 'Added' : r.review === 'rejected' ? 'Rejected' : r.validation === 'invalid' ? 'Invalid' : r.duplicate !== 'new' ? `Duplicate: ${r.duplicate_reason || ''}` : 'Ready'}
                      {(r.business.errors || []).concat(r.business.warnings || []).map(w => <div key={w}>· {w}</div>)}</small></td>
                  </tr>))}</tbody></table>
            </div>
            {b.status !== 'committed' && <div className="aq-actions">
              <button className="aq-btn aq-btn--sm" disabled={busy || !Object.values(pick).some(Boolean)} onClick={() => review('reject')}>Reject selected</button>
              <button className="aq-btn aq-btn--sm" disabled={busy || !Object.values(pick).some(Boolean)} onClick={() => review('restore')}>Restore selected</button>
              <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={busy || !committable} onClick={commit}>4. Add {committable} to the master pool</button>
            </div>}
            {b.status === 'committed' && <div className="aq-actions"><button className="aq-btn aq-btn--sm aq-btn--danger" disabled={busy} onClick={rollback}>Undo this import</button>
              <small style={{ color: 'var(--aq-sub)' }}>Businesses already assigned to a client are kept.</small></div>}
          </>}
        </section>
      )}

      <section className="aq-card" style={{ marginTop: 14 }}>
        <h2>Recent imports</h2>
        {!batches ? 'Loading…' : batches.length ? batches.map(x => (
          <div className="aq-row" key={x.id}><div className="aq-row__main"><b>{x.name}</b>
            <span>{x.rows} rows · {x.source_system} · {x.status.replace(/_/g, ' ')}{x.commit_report ? ` · ${x.commit_report.committed} added` : ''}</span></div>
            <button className="aq-btn aq-btn--sm" onClick={() => open(x.id)}>Open</button></div>
        )) : <div className="aq-empty">No master-pool imports yet.</div>}
      </section>
    </div>
  )
}
