/* Source coverage by PROPERTY TYPE: which connected source can say what about
 * which kind of property — connected, verification level, cost, limits.
 * Written from what the adapters actually read, not from the UI's categories. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import { Alert, Panel, Skeleton, Tag } from '../ds/ds'
import { CATEGORY_LABEL } from './ptypes'

export default function TypeCoverage() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => { api.get('/wholesale/evosense/property-types').then(setD).catch((e) => setErr(errText(e))) }, [])
  if (err) return <Alert kind="warn">{err}</Alert>
  if (!d) return <Skeleton rows={6} />
  return (
    <>
      <Panel title="Properties by type" hint="From the county's own category code">
        <ul className="evo-fcc-list">
          {d.categories.map((c) => <li key={c.key}><span>{c.label}</span><b>{c.count}</b></li>)}
          {d.unclassified_yet ? <li><span className="evo-muted">Not classified yet (runs in the background)</span><b>{d.unclassified_yet}</b></li> : null}
        </ul>
      </Panel>
      {d.checked?.length ? (
        <Panel title="What discovery actually checked" hint="Counted by the county readers in this workspace's latest hunts" flush>
          <div className="evo-table-wrap">
            <table className="evo-table evo-table--cards evo-fcc-table">
              <thead><tr><th>Source</th><th>County</th><th>Type</th><th>Result</th><th>Found</th><th>Eligible</th>
                <th>Imported (last run)</th><th>Why</th></tr></thead>
              <tbody>{d.checked.map((r) => (
                <tr key={`${r.source}-${r.category}`}>
                  <td className="is-lead" data-label=""><b>{r.source}</b>
                    {r.last_checked ? <span className="evo-prop__sub" style={{ display: 'block' }}>Checked {new Date(r.last_checked).toLocaleString()}</span> : null}</td>
                  <td data-label="County">{r.county}</td>
                  <td data-label="Type">{r.category_label}</td>
                  <td data-label="Result"><Tag kind={r.status === 'SOURCE SUCCESSFULLY CHECKED' ? 'live' : r.status === 'SOURCE BLOCKED' ? 'danger' : undefined}>{r.status}</Tag></td>
                  <td data-label="Found">{r.identified}</td>
                  <td data-label="Eligible">{r.eligible}</td>
                  <td data-label="Imported (last run)">{r.imported_last_run}</td>
                  <td data-label="Why" className="is-stack">{r.reason || ''}</td>
                </tr>))}</tbody>
            </table>
          </div>
        </Panel>
      ) : null}
      <Panel title="Source coverage by property type" flush>
        <div className="evo-table-wrap">
          <table className="evo-table evo-table--cards evo-fcc-table">
            <thead><tr><th>Source</th><th>Property types</th><th>Coverage</th><th>Fields</th><th>Verification</th>
              <th>Status</th><th>Cost</th><th>Limits</th></tr></thead>
            <tbody>{d.coverage.map((r) => (
              <tr key={r.source}>
                <td className="is-lead" data-label=""><b>{r.label}</b>
                  {r.last_success ? <span className="evo-prop__sub" style={{ display: 'block' }}>Last good check {new Date(r.last_success).toLocaleDateString()}</span> : null}</td>
                <td data-label="Property types" className="is-stack">{r.property_types.map((t) => CATEGORY_LABEL[t] || t).join(', ')}</td>
                <td data-label="Coverage">{r.geography}</td>
                <td data-label="Fields" className="is-stack">{r.fields}</td>
                <td data-label="Verification">{r.verification}</td>
                <td data-label="Status"><Tag kind={r.status === 'SOURCE CONNECTED' ? 'live' : r.status === 'PAID ACCESS REQUIRED' ? 'estimate' : undefined}>{r.status}</Tag></td>
                <td data-label="Cost">{r.cost}</td>
                <td data-label="Limits" className="is-stack">{r.limitations}</td>
              </tr>))}</tbody>
          </table>
        </div>
      </Panel>
    </>
  )
}
