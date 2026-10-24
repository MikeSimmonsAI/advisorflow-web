/* BUYER NETWORK - reviewable import of your buyer file, and the DFW segment.
 * Server: /wholesale/buyers/imports (upload, mapping, analyze, rows, commit, rollback,
 * errors.csv) and GET /wholesale/buyers/network.
 * Nothing is written to the buyer list until "Import" on step 3. Every import can
 * be rolled back from its history entry.
 */
import { useCallback, useEffect, useState } from 'react'
import { api, fetchObjectUrl } from '../../api/client'
import { Drawer } from './ds/ds'

const STATE_LABEL = {
  new: 'New buyer', update: 'Matches an existing buyer', conflict: 'Needs your decision',
  duplicate_in_file: 'Repeated in this file', error: 'Cannot import yet', skipped: 'Skipped',
  created: 'Created', updated: 'Updated', rolled_back: 'Rolled back',
}
const err = (e) => (e && (e.message || e.detail)) || String(e)

function Counts({ counts }) {
  const keys = Object.keys(counts || {})
  if (!keys.length) return null
  return (
    <div className="uw-row" style={{ marginTop: 6 }}>
      {keys.map((k) => <span key={k} className={`ws-pill ${k === 'error' || k === 'conflict' ? 'is-warn' : k === 'created' || k === 'updated' ? 'is-ok' : ''}`}>{STATE_LABEL[k] || k}: {counts[k]}</span>)}
    </div>
  )
}

function Row({ r, batchId, onChanged, busy }) {
  const [fix, setFix] = useState({})
  const n = r.normalized || {}
  async function send(body) {
    await api.patch(`/wholesale/buyers/imports/${batchId}/rows/${r.id}`, body)
    onChanged()
  }
  return (
    <div className="uw-case" style={{ marginBottom: 8 }}>
      <div className="uw-case__head">Row {r.row}: {n.company_name || n.contact_name || '(no name)'}
        <span className="ws-muted uw-small"> · {STATE_LABEL[r.state] || r.state}{r.decision ? ` · your decision: ${r.decision}` : ''}</span></div>
      {(r.errors || []).map((x) => <div key={x} className="ws-error uw-small">{x}</div>)}
      {(r.warnings || []).slice(0, 4).map((x) => <div key={x} className="ws-muted uw-small">{x}</div>)}
      {r.match ? (
        <ul className="uw-history">
          {(r.match.candidates || []).map((c) => <li key={c.buyer_id}><b>{c.name}</b>: {c.reasons.join('; ')}</li>)}
          {(r.match.reasons || []).map((x) => <li key={x}>{x}</li>)}
          {r.match.why ? <li className="ws-muted">{r.match.why}</li> : null}
        </ul>
      ) : null}
      {r.state === 'conflict' ? (
        <div className="uw-row">
          {(r.match.candidates || []).map((c) => (
            <button key={c.buyer_id} type="button" className="btn btn--secondary btn--sm" disabled={busy}
                    onClick={() => send({ decision: 'merge', buyer_id: c.buyer_id })}>Same as {c.name}</button>))}
          <button type="button" className="btn btn--secondary btn--sm" disabled={busy} onClick={() => send({ decision: 'create' })}>A different buyer</button>
          <button type="button" className="btn btn--secondary btn--sm" disabled={busy} onClick={() => send({ decision: 'skip' })}>Skip</button>
        </div>
      ) : null}
      {r.state === 'error' ? (
        <div className="uw-row">
          {Object.keys(r.raw || {}).slice(0, 6).map((h) => (
            <label key={h} className="uw-small">{h}
              <input defaultValue={r.raw[h]} onChange={(e) => setFix({ ...fix, [h]: e.target.value })} style={{ width: 130 }} /></label>))}
          <button type="button" className="btn btn--secondary btn--sm" disabled={busy || !Object.keys(fix).length} onClick={() => send({ fix })}>Save fix</button>
          <button type="button" className="btn btn--secondary btn--sm" disabled={busy} onClick={() => send({ decision: 'skip' })}>Skip</button>
        </div>
      ) : null}
    </div>
  )
}

export function BuyerImportWizard({ onClose, onDone }) {
  const [file, setFile] = useState(null)
  const [listName, setListName] = useState('')
  const [isTest, setIsTest] = useState(false)
  const [batch, setBatch] = useState(null)
  const [mapping, setMapping] = useState({})
  const [review, setReview] = useState(null)
  const [filter, setFilter] = useState('conflict')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [history, setHistory] = useState([])

  const loadHistory = useCallback(() => api.get('/wholesale/buyers/imports').then((r) => setHistory(r.imports)).catch(() => {}), [])
  useEffect(() => { loadHistory() }, [loadHistory])
  const loadRows = useCallback(async (b, f) => {
    const r = await api.get(`/wholesale/buyers/imports/${b.id}/rows${f ? `?state=${f}` : ''}`)
    setReview(r)
  }, [])

  async function run(fn) {
    setBusy(true); setError(null)
    try { await fn() } catch (e) { setError(err(e)) } finally { setBusy(false) }
  }
  const upload = () => run(async () => {
    const fd = new FormData()
    fd.append('file', file); fd.append('is_test', isTest ? 'true' : 'false')
    if (listName) fd.append('list_name', listName)
    const b = await api.post('/wholesale/buyers/imports', fd)
    setBatch(b); setMapping(b.mapping); setReview(null)
  })
  const analyze = () => run(async () => {
    await api.put(`/wholesale/buyers/imports/${batch.id}/mapping`, { mapping })
    const b = await api.post(`/wholesale/buyers/imports/${batch.id}/analyze`)
    setBatch(b); await loadRows(b, filter)
  })
  const commit = () => run(async () => {
    const b = await api.post(`/wholesale/buyers/imports/${batch.id}/commit`)
    setBatch(b); await loadRows(b, filter); loadHistory()
    const x = b.result
    onDone(`${x.created} buyer(s) created, ${x.updated} updated, ${x.skipped} skipped` +
      (x.waiting ? `, ${x.waiting} waiting for your decision` : '') + (x.errors ? `, ${x.errors} with problems` : '') + '.')
  })
  const rollback = (id) => run(async () => {
    if (!window.confirm('Undo this import? Buyers it created are removed (or turned off if they have deal history), and buyers it updated get their old values back.')) return
    const b = await api.post(`/wholesale/buyers/imports/${id}/rollback`)
    loadHistory()
    onDone(`Import undone: ${b.rollback.removed.length} removed, ${b.rollback.deactivated.length} turned off, ${b.rollback.restored.length} restored.`)
  })
  const problems = (id) => run(async () => { window.open(await fetchObjectUrl(`/wholesale/buyers/imports/${id}/errors.csv`), '_blank', 'noopener') })

  const step = !batch ? 1 : batch.status === 'mapping' ? 2 : 3
  return (
    <Drawer open onClose={onClose} title="Import your buyer network" sub="CSV or Excel · review before anything is saved">
      {error ? <div className="ws-error">{error}</div> : null}
      <ol className="uw-flow" style={{ listStyle: 'none', padding: 0 }}>
        {['Upload', 'Match columns', 'Review and import'].map((l, i) => (
          <li key={l} className={`uw-step ${i + 1 < step ? 'is-done' : i + 1 === step ? 'is-attention' : ''}`} style={{ cursor: 'default' }}>
            <span className="uw-step__icon">{i + 1 < step ? '✓' : i + 1}</span>{l}</li>))}
      </ol>

      {step === 1 ? (
        <div>
          <div className="ws-field"><label>File (.csv or .xlsx)</label>
            <input type="file" accept=".csv,.tsv,.xlsx,text/csv" onChange={(e) => setFile(e.target.files[0])} /></div>
          <div className="ws-field"><label>Name this list (optional)</label>
            <input value={listName} onChange={(e) => setListName(e.target.value)} placeholder="e.g. National buyer list Oct 2026" /></div>
          <label className="ws-checkbox"><input type="checkbox" checked={isTest} onChange={(e) => setIsTest(e.target.checked)} /> Sandbox records (for practice)</label>
          <div className="uw-row"><button type="button" className="btn btn--primary" disabled={!file || busy} onClick={upload}>{busy ? 'Reading…' : 'Read the file'}</button></div>
          <p className="ws-muted uw-small">Nothing is saved to your buyers yet. Every column is optional except a name or company.</p>
        </div>
      ) : null}

      {step === 2 ? (
        <div>
          <p className="uw-small">{batch.row_count} rows in <b>{batch.filename}</b>{batch.file_stats?.blank_rows ? ` (${batch.file_stats.blank_rows} blank rows ignored)` : ''}. Check what each column means:</p>
          <div className="uw-table-wrap"><table className="uw-table">
            <thead><tr><th>Your column</th><th>Means</th><th>First value</th></tr></thead>
            <tbody>{batch.headers.map((h) => (
              <tr key={h}><td>{h}</td>
                <td><select value={mapping[h] || ''} onChange={(e) => setMapping({ ...mapping, [h]: e.target.value || null })}>
                  <option value="">Not used (kept in notes)</option>
                  {Object.entries(batch.fields).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
                <td className="ws-muted uw-small">{(batch.sample?.[0]?.raw || {})[h] || ''}</td></tr>))}</tbody>
          </table></div>
          <div className="uw-row"><button type="button" className="btn btn--primary" disabled={busy} onClick={analyze}>{busy ? 'Checking…' : 'Check every row'}</button></div>
        </div>
      ) : null}

      {step === 3 ? (
        <div>
          <Counts counts={batch.counts} />
          <p className="ws-muted uw-small">Same email or same phone = the same buyer. Two people at one company are never merged. What your file says about proof of funds or past purchases is kept as your file's word; it never marks a buyer as verified.</p>
          <div className="uw-row">
            {['conflict', 'error', 'update', 'new', 'duplicate_in_file', ''].map((f) => (
              <button key={f || 'all'} type="button" className={`btn btn--sm ${filter === f ? 'btn--primary' : 'btn--secondary'}`}
                      onClick={() => { setFilter(f); loadRows(batch, f) }}>{f ? STATE_LABEL[f] : 'All'}</button>))}
          </div>
          {review ? (
            <div style={{ marginTop: 8 }}>
              {review.rows.length ? review.rows.map((r) => <Row key={r.id} r={r} batchId={batch.id} busy={busy}
                onChanged={async () => { const b = await api.get(`/wholesale/buyers/imports/${batch.id}`); setBatch(b); loadRows(b, filter) }} />)
                : <p className="ws-muted">Nothing in this group.</p>}
              {review.total > review.rows.length ? <p className="ws-muted uw-small">Showing {review.rows.length} of {review.total}.</p> : null}
            </div>
          ) : null}
          <div className="uw-row">
            {['analyzed', 'partially_committed', 'committed'].includes(batch.status)
              ? <button type="button" className="btn btn--primary" disabled={busy} onClick={commit}>{batch.status === 'analyzed' ? 'Import' : 'Import the rows fixed since'}</button> : null}
            <button type="button" className="btn btn--secondary btn--sm" onClick={() => problems(batch.id)}>Download problem rows</button>
          </div>
        </div>
      ) : null}

      {history.length ? (
        <details className="uw-steps" style={{ marginTop: 14 }}><summary>Import history ({history.length})</summary>
          <ul className="uw-history">{history.map((h) => (
            <li key={h.id}><b>{h.list_name || h.filename}</b> · {h.row_count} rows · {h.status.replace('_', ' ')}{h.is_test ? ' · sandbox' : ''}
              {['committed', 'partially_committed'].includes(h.status)
                ? <> · <button type="button" className="btn btn--secondary btn--sm" disabled={busy} onClick={() => rollback(h.id)}>Undo</button></> : null}</li>))}</ul>
        </details>
      ) : null}
    </Drawer>
  )
}

export function BuyerNetworkPanel({ includeTest, refreshKey }) {
  const [segment, setSegment] = useState('dfw')
  const [status, setStatus] = useState('')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    const p = new URLSearchParams({ limit: '200' })
    if (segment) p.set('segment', segment)
    if (status) p.set('status', status)
    if (includeTest) p.set('include_test', 'true')
    api.get('/wholesale/buyers/network?' + p.toString()).then(setData).catch((e) => setError(err(e)))
  }, [segment, status, includeTest, refreshKey])
  if (error) return <div className="ws-error">{error}</div>
  if (!data) return null
  const c = data.counts
  return (
    <div className="panel ws-panel" style={{ marginBottom: 14 }}>
      <div className="panel-title ws-panel-title"><span>Buyer network · {c.total} buyers · {c.dfw} DFW ({c.dfw_priority} priority)</span>
        <span className="uw-row" style={{ marginTop: 0 }}>
          <select value={segment} onChange={(e) => setSegment(e.target.value)}><option value="dfw">DFW buyers</option><option value="">All buyers</option></select>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any status</option><option value="qualified">Qualified</option><option value="verified_active">Verified active</option>
            <option value="responded">Responded</option><option value="contactable">Contactable</option><option value="imported">Imported, no contact</option><option value="inactive">Inactive</option>
          </select></span></div>
      <div className="uw-row" style={{ marginTop: 0 }}>
        {Object.entries(c.by_status || {}).map(([k, v]) => <span key={k} className="ws-pill">{k.replace('_', ' ')}: {v}</span>)}
      </div>
      {data.buyers.length ? (
        <div className="uw-table-wrap"><table className="uw-table">
          <thead><tr><th>Buyer</th><th>Status</th><th>Why DFW</th><th>Buys</th><th>From your file</th></tr></thead>
          <tbody>{data.buyers.slice(0, 100).map((b) => (
            <tr key={b.buyer_id}><td>{b.name}{b.is_test ? <span className="ws-muted"> · sandbox</span> : null}
              <div className="ws-muted uw-small">{[b.has_phone && 'phone', b.has_email && 'email'].filter(Boolean).join(' · ') || 'no contact'}</div></td>
              <td><span className={`ws-pill ${['qualified', 'verified_active'].includes(b.status.status) ? 'is-ok' : ''}`}>{b.status.label}</span></td>
              <td className="uw-small">{[...b.dfw.evidence, ...b.dfw.territory].join('; ') || '—'}{b.dfw.statewide_only ? <div className="ws-muted">Texas-wide only</div> : null}</td>
              <td className="uw-small">{[b.financing, b.close_days ? `closes ~${b.close_days} days` : null].filter(Boolean).join(' · ') || '—'}</td>
              <td className="uw-small ws-muted">{[b.from_file.pof && `POF: ${b.from_file.pof}`, b.from_file.purchases && `Purchases: ${b.from_file.purchases}`].filter(Boolean).join(' · ') || '—'}</td></tr>))}</tbody>
        </table></div>
      ) : <p className="ws-muted">No buyers in this view.</p>}
      <p className="ws-muted uw-small">{data.note}</p>
    </div>
  )
}
