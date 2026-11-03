// #470 Stage B - import businesses into EvoSys Sales or one cleaning
// customer's Acquisition pipeline. The destination is fixed in the URL, shown
// on every step, re-checked by the server on every call, and confirmed again
// at commit. Location-pending and excluded rows are never created. Nothing is sent.
import { useCallback, useEffect, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { api, fetchObjectUrl } from '../../api/client'

const err = e => (e && (e.detail || e.message)) || 'Something went wrong'
const OUTCOMES = [
  ['', 'All'], ['ready', 'Ready'], ['matched_existing', 'Already there'], ['held_for_review', 'Held: location'],
  ['excluded', 'Excluded'], ['invalid', 'Invalid'], ['duplicate_in_file', 'Repeated in file'],
  ['rejected', 'Rejected'], ['created', 'Created'], ['failed', 'Failed'],
]

export default function BusinessImport({ mode }) {
  const [params] = useSearchParams()
  const target = params.get('target') || ''
  const BASE = `/intake/${mode === 'sales' ? 'sales' : 'acquisition'}/${encodeURIComponent(target)}`
  const [dest, setDest] = useState(null)
  const [batches, setBatches] = useState(null)
  const [b, setB] = useState(null)
  const [rows, setRows] = useState(null)
  const [filter, setFilter] = useState('')
  const [pick, setPick] = useState({})
  const [file, setFile] = useState(null)
  const [source, setSource] = useState('')
  const [profile, setProfile] = useState(mode === 'sales' ? 'general' : '')
  const [confirmed, setConfirmed] = useState(false)
  const [backfill, setBackfill] = useState(null)
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [msg, setMsg] = useState(null)

  useEffect(() => {
    api.get('/intake/destinations').then(r => {
      const d = r.items.find(x => x.key === (mode === 'sales' ? 'evosys_sales' : 'customer_acquisition'))
      const t = d?.targets?.find(x => x.id === target)
      setDest(t ? { ...d, target: t } : false)
    }).catch(e => setFail(err(e)))
  }, [mode, target])

  const loadList = useCallback(() => api.get(`${BASE}/batches`).then(r => setBatches(r.items)).catch(e => setFail(err(e))), [BASE])
  const open = useCallback(async id => {
    const x = await api.get(`${BASE}/batches/${id}`); setB(x); setPick({}); setConfirmed(false)
    if (x.counts) setRows((await api.get(`${BASE}/batches/${id}/rows?limit=1000${filter ? `&outcome=${filter}` : ''}`)).items)
    else setRows(null)
  }, [BASE, filter])
  useEffect(() => { if (dest) loadList() }, [dest, loadList])
  useEffect(() => { if (b?.id && b.counts) open(b.id) }, [filter]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (fn, ok) => {
    setBusy(true); setFail(null); setMsg(null)
    try { const r = await fn(); if (ok) setMsg(typeof ok === 'function' ? ok(r) : ok) } catch (e) { setFail(err(e)) } finally { setBusy(false) }
  }
  const upload = () => run(async () => {
    const fd = new FormData(); fd.append('file', file); fd.append('source_system', source)
    if (profile) fd.append('profile', profile)
    const x = await api.upload(`${BASE}/batches`, fd); setB(x); setRows(null); setFile(null); loadList()
  }, 'Uploaded. Check the column matches, then press Check rows.')
  const analyze = () => run(async () => {
    await api.put(`${BASE}/batches/${b.id}/mapping`, b.mapping); await api.post(`${BASE}/batches/${b.id}/analyze`, {}); await open(b.id)
  }, 'Rows checked. Nothing has been added yet.')
  const review = decision => run(async () => {
    await api.post(`${BASE}/batches/${b.id}/review`, { row_ids: Object.keys(pick).filter(k => pick[k]), decision }); await open(b.id)
  })
  const commit = () => run(async () => {
    const r = await api.post(`${BASE}/batches/${b.id}/commit`, { confirm_target_id: target }); await open(b.id); loadList(); return r
  }, r => `Created ${r.created}. Already there: ${r.matched_existing}. Held for location review: ${r.held_for_review}. Excluded: ${r.excluded}. Invalid: ${r.invalid}. Failed: ${r.failed}. Messages sent: 0.`)
  const rollback = () => run(async () => { const r = await api.post(`${BASE}/batches/${b.id}/rollback`, {}); await open(b.id); loadList(); return r },
    r => `Removed ${r.removed}. Kept ${r.kept_worked_on} that have been worked on since.`)
  const attach = () => run(() => api.post(`${BASE}/batches/${b.id}/attach-matched`, {}),
    r => `Added notes and drafts to ${r.notes_added} matched prospects (${r.already_attached} already had them). Filled ${r.source_id_filled} blank source IDs. Nothing else changed.`)
  const download = () => run(async () => {
    const url = await fetchObjectUrl(`${BASE}/batches/${b.id}/report.csv`)
    const a = document.createElement('a'); a.href = url; a.download = `${b.code || 'import'}-results.csv`; a.click()
  })
  const previewBackfill = () => run(async () => setBackfill(await api.get(`${BASE}/source-ids/preview`)))
  const applyBackfill = () => run(async () => { const r = await api.post(`${BASE}/source-ids/apply`, {}); setBackfill(r); return r },
    r => `Recorded the source ID on ${r.filled} prospects. Nothing else changed.`)

  if (dest === false) return <div className="aq-page"><div className="aq-err">You can't import into this destination.</div><Link to="/imports/start">Choose a destination</Link></div>
  if (!dest) return <div className="aq-page">{fail ? <div className="aq-err">{fail}</div> : 'Loading…'}</div>
  const c = b?.counts
  const ready = rows ? rows.filter(r => r.committable).length : 0
  return (
    <div className="aq-page">
      <div className="aq-ok" style={{ marginBottom: 12 }}>
        Importing into <strong>{dest.label}</strong>: <strong>{dest.target.name}</strong>. <Link to="/imports/start">Change</Link>
      </div>
      <h1 className="aq-h1">Import businesses</h1>
      <p className="aq-lede">Location-pending and excluded rows are held, never created. Intro messages are kept as drafts. Nothing is sent and nobody is put in a campaign.</p>
      {fail && <div className="aq-err">{fail}</div>}
      {msg && <div className="aq-ok">{msg}</div>}

      {mode === 'sales' && (
        <section className="aq-card">
          <h2>Prospects entered by hand</h2>
          <p>Record the source ID that is already written in each prospect's source text (for example FB-CLEAN-004) so imports match them. Preview first.</p>
          <button className="btn" disabled={busy} onClick={previewBackfill}>Preview</button>{' '}
          {backfill?.will_fill > 0 && <button className="btn btn-primary" disabled={busy} onClick={applyBackfill}>Record {backfill.will_fill} source IDs</button>}
          {backfill && (
            <table className="aq-table"><thead><tr><th>Company</th><th>Source ID</th><th>Action</th></tr></thead>
              <tbody>{backfill.items.map(i => <tr key={i.opportunity_id}><td>{i.company}</td><td>{i.source_record_id}</td><td>{i.action.replace(/_/g, ' ')}</td></tr>)}
                {backfill.conflicts.map(i => <tr key={i.source_record_id}><td colSpan={2}>{i.source_record_id}</td><td>{i.why}</td></tr>)}</tbody></table>
          )}
        </section>
      )}

      <section className="aq-card">
        <h2>1. Upload a CSV or Excel file</h2>
        <input type="file" accept=".csv,.xlsx" onChange={e => setFile(e.target.files?.[0] || null)} />{' '}
        <input placeholder="Where these came from, e.g. facebook_ads" value={source} onChange={e => setSource(e.target.value)} />{' '}
        {mode === 'sales' && (
          <select value={profile} onChange={e => setProfile(e.target.value)}>
            {(dest.profiles || []).map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
        )}{' '}
        <button className="btn btn-primary" disabled={busy || !file || !source.trim()} onClick={upload}>Upload</button>
        {batches?.length > 0 && (
          <p>Earlier imports: {batches.map(x => <button key={x.id} className="btn btn-link" onClick={() => open(x.id)}>{x.code} ({x.status})</button>)}</p>
        )}
      </section>

      {b && (
        <section className="aq-card">
          <h2>2. Match the columns - {b.filename}</h2>
          <table className="aq-table"><tbody>
            {b.fields.map(f => (
              <tr key={f.key}><td>{f.label}</td><td>
                <select value={b.mapping[f.key] || ''} disabled={b.status === 'committed'}
                        onChange={e => setB({ ...b, mapping: { ...b.mapping, [f.key]: e.target.value || null } })}>
                  <option value="">(not in this file)</option>
                  {b.headers.map(h => <option key={h} value={h}>{h}</option>)}
                </select></td></tr>
            ))}
          </tbody></table>
          <button className="btn btn-primary" disabled={busy || b.status === 'committed'} onClick={analyze}>Check rows</button>
        </section>
      )}

      {c && (
        <section className="aq-card">
          <h2>3. Review</h2>
          <p>{c.rows} rows: <strong>{c.ready} ready</strong>, {c.matched_existing} already there, {c.held_for_review} held for location review,
            {' '}{c.excluded} excluded, {c.invalid} invalid, {c.duplicate_in_file} repeated in the file. {c.with_intro_draft} have an intro draft.</p>
          <select value={filter} onChange={e => setFilter(e.target.value)}>{OUTCOMES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>{' '}
          <button className="btn" disabled={busy} onClick={() => review('reject')}>Reject selected</button>{' '}
          <button className="btn" disabled={busy} onClick={() => review('restore')}>Restore selected</button>{' '}
          <button className="btn" disabled={busy} onClick={download}>Download results (CSV)</button>
          <table className="aq-table"><thead><tr><th></th><th>#</th><th>Company</th><th>Source ID</th><th>State</th><th>Result</th><th>Why</th></tr></thead>
            <tbody>{(rows || []).map(r => (
              <tr key={r.id}>
                <td><input type="checkbox" checked={!!pick[r.id]} onChange={e => setPick({ ...pick, [r.id]: e.target.checked })} /></td>
                <td>{r.row}</td><td>{r.business.business_name}</td><td>{r.business.source_record_id}</td><td>{r.business.state}</td>
                <td>{r.outcome.replace(/_/g, ' ')}</td>
                <td>{r.match_reason || r.business.territory_note || (r.business.errors || []).join('; ')}</td>
              </tr>))}</tbody></table>
          {b.status !== 'committed' ? (
            <div style={{ marginTop: 12 }}>
              <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> Create {ready} records in <strong>{dest.target.name}</strong></label>{' '}
              <button className="btn btn-primary" disabled={busy || !confirmed || ready === 0} onClick={commit}>Create {ready}</button>
            </div>
          ) : (
            <div style={{ marginTop: 12 }}>
              {mode === 'sales' && c.matched_existing > 0 && <><button className="btn" disabled={busy} onClick={attach}>Add the file's notes and intro drafts to the {c.matched_existing} matched prospects</button>{' '}</>}
              <button className="btn" disabled={busy} onClick={rollback}>Undo this import</button>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
