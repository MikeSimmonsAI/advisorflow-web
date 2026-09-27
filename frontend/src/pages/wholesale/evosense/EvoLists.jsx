/* Distress lists: one import, many list kinds. A tax-sale list, a code export,
 * clerk postings or a driving-for-dollars sheet goes through the SAME ingest as
 * every provider (identity, dedupe, signals, scoring). What a list says is its
 * claim — stored as evidence and labelled unverified. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import { Alert, Panel } from '../ds/ds'

export default function EvoLists() {
  const [meta, setMeta] = useState(null)
  const [kind, setKind] = useState('')
  const [source, setSource] = useState('')
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [out, setOut] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => { api.get('/wholesale/evosense/import/list-kinds').then(setMeta).catch(e => setErr(errText(e))) }, [])

  async function go() {
    if (!file || !kind) return
    setBusy(true); setErr(''); setOut(null)
    const fd = new FormData()
    fd.append('file', file)
    fd.append('list_kind', kind)
    if (source.trim()) fd.append('list_source', source.trim())
    try { setOut(await api.upload('/wholesale/evosense/import', fd)) }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const k = meta?.list_kinds?.find(x => x.key === kind)
  return (
    <Panel title="Import a distress list"
           hint="Same ingest, dedupe and scoring as every source · each row's claim is kept as evidence, not verified fact">
      <div style={{ display: 'grid', gap: 12, maxWidth: 640 }}>
        <label>List kind
          <select className="filter-select" value={kind} onChange={e => setKind(e.target.value)}>
            <option value="">Choose…</option>
            {(meta?.list_kinds || []).map(x => <option key={x.key} value={x.key}>{x.label}</option>)}
          </select>
        </label>
        {k ? <p className="evo-muted evo-small">Rows assert <b>{k.signal_label}</b> unless the row's own
          <code> signals</code> column says otherwise.</p> : null}
        <label>Where the list came from
          <input className="settings-input" value={source} placeholder="e.g. Dallas County tax office, Sept 2026"
                 onChange={e => setSource(e.target.value)} />
        </label>
        <label>CSV file
          <input type="file" accept=".csv" onChange={e => setFile(e.target.files[0] || null)} />
        </label>
        <p className="evo-muted evo-small">
          Columns: {(meta?.columns || []).join(', ')}. Evidence columns: {(meta?.evidence_columns || []).join(', ')}.
          <code> street_address</code> and <code>zip_code</code> or <code>city</code> are required.
        </p>
        <div><button className="btn btn--primary" disabled={busy || !file || !kind} onClick={go}>
          {busy ? 'Importing…' : 'Import list'}</button></div>
        {err ? <Alert kind="warn">{err}</Alert> : null}
        {out ? (
          <Alert kind="ok">
            {out.counts.rows} rows: {out.counts.created || 0} new, {out.counts.merged || 0} merged,
            {' '}{out.counts.seen || 0} already known, {out.counts.review || 0} for review, {out.counts.rejected || 0} rejected.
            {out.rejected?.length ? <ul>{out.rejected.map(r => <li key={r.line}>Line {r.line}: {r.reason}</li>)}</ul> : null}
          </Alert>
        ) : null}
      </div>
    </Panel>
  )
}
