/* Distress lists: one import, many list kinds. A tax-sale list, a code export,
 * clerk postings or a driving-for-dollars sheet goes through the SAME ingest as
 * every provider (identity, dedupe, signals, scoring). What a list says is its
 * claim — stored as evidence and labelled unverified. */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import { Alert, Panel } from '../ds/ds'

// An address list to verify (an old mailing list, a purchased list): the
// properties come in, the free county lookups fill the current owner, and
// nothing is claimed about distress or foreclosure.
const NO_CLAIM = '__addresses_only__'

export default function EvoLists() {
  const [meta, setMeta] = useState(null)
  const [kind, setKind] = useState('')
  const [source, setSource] = useState('')
  const [saleDate, setSaleDate] = useState('')
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [out, setOut] = useState(null)
  const [err, setErr] = useState('')
  const [lists, setLists] = useState([])
  const [undo, setUndo] = useState(null)

  useEffect(() => { api.get('/wholesale/evosense/import/list-kinds').then(setMeta).catch(e => setErr(errText(e))) }, [])
  useEffect(() => { api.get('/wholesale/evosense/cohorts').then(r => setLists(r.cohorts || [])).catch(() => {}) }, [out])

  async function go() {
    if (!file || !kind) return
    setBusy(true); setErr(''); setOut(null); setUndo(null)
    const fd = new FormData()
    fd.append('file', file)
    // "Addresses only" sends no list kind: the rows assert NO distress signal.
    if (kind !== NO_CLAIM) fd.append('list_kind', kind)
    if (source.trim()) fd.append('list_source', source.trim())
    if (saleDate) fd.append('declared_sale_date', saleDate)
    try { setOut(await api.upload('/wholesale/evosense/import', fd)) }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  // Undo is REVERSIBLE: the properties this import created are archived (never
  // deleted); one already promoted or in a conversation is kept and named.
  async function undoImport(restore) {
    if (!out?.import_id) return
    setBusy(true); setErr('')
    try { setUndo(await api.post(`/wholesale/evosense/import/${out.import_id}/${restore ? 'restore' : 'undo'}`, {})) }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const k = meta?.list_kinds?.find(x => x.key === kind)
  return (
    <>
    {lists.length ? (
      <Panel title="Imported lists" hint="Each list followed as a group: owner now, taxes, foreclosure, score and next action">
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {lists.map(l => (
            <li key={l.kind}><Link to={`/wholesale/evosense/lists/${encodeURIComponent(l.kind)}`}>
              {/^top100/i.test(l.kind) ? 'Top 100' : l.kind}</Link> — {l.properties} properties</li>
          ))}
        </ul>
      </Panel>
    ) : null}
    <Panel title="Import a distress list"
           hint="Same ingest, dedupe and scoring as every source · each row's claim is kept as evidence, not verified fact">
      <div style={{ display: 'grid', gap: 12, maxWidth: 640 }}>
        <label>List kind
          <select className="filter-select" value={kind} onChange={e => setKind(e.target.value)}>
            <option value="">Choose…</option>
            <option value={NO_CLAIM}>Addresses only — no distress claim (owner and status to be verified)</option>
            {(meta?.list_kinds || []).map(x => <option key={x.key} value={x.key}>{x.label}</option>)}
          </select>
        </label>
        {k ? <p className="evo-muted evo-small">Rows assert <b>{k.signal_label}</b> unless the row's own
          <code> signals</code> column says otherwise.</p> : null}
        {kind === NO_CLAIM ? <p className="evo-muted evo-small">Rows assert <b>no signal</b>. Each property is
          looked up in the free county records on the next hunt; one the county sources cannot answer stays
          marked as needing verification.</p> : null}
        <label>Where the list came from
          <input className="settings-input" value={source} placeholder="e.g. Dallas County tax office, Sept 2026"
                 onChange={e => setSource(e.target.value)} />
        </label>
        <label>Foreclosure sale date for the whole list (optional)
          <input className="settings-input" type="date" value={saleDate} onChange={e => setSaleDate(e.target.value)} />
          <span className="evo-muted evo-small">Only if the list states one sale date for every row. Rows with their own
            sale date column keep theirs. Recorded as your claim until a county notice confirms it. The file name is never read for dates.</span>
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
            {out.counts.contacts_attached || out.counts.contacts_refused ? (
              <> {' '}Contacts from the list: {out.counts.contacts_attached || 0} attached to the owner of record,
                {' '}{out.counts.contacts_refused || 0} rows held back (the list names someone else, or no name to
                compare). A number on a list is never permission to call or text.</>) : null}
            {out.rejected?.length ? <ul>{out.rejected.map(r => <li key={r.line}>Line {r.line}: {r.reason}</li>)}</ul> : null}
            {out.import_id && out.counts.created ? (
              <div style={{ marginTop: 8 }}>
                {!undo || undo.restored ? (
                  <button className="btn" disabled={busy} onClick={() => undoImport(false)}>
                    Undo this import (archive the {out.counts.created} new properties)</button>
                ) : (
                  <button className="btn" disabled={busy} onClick={() => undoImport(true)}>Restore them</button>
                )}
              </div>
            ) : null}
          </Alert>
        ) : null}
        {undo ? (
          <Alert kind="ok">
            {undo.restored ? `${undo.restored} properties restored.` :
              `${undo.archived} new properties archived (nothing deleted).`}
            {undo.kept?.length ? <ul>{undo.kept.map(x => <li key={x.property_id}>{x.address}: kept - {x.why}</li>)}</ul> : null}
          </Alert>
        ) : null}
      </div>
    </Panel>
    </>
  )
}
