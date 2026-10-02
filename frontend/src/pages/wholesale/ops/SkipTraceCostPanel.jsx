/* Skip-trace cost panel — ESTIMATED TOTAL COST before any paid run.
 *
 * GET  /wholesale/skip-trace/providers   price catalogue (every price with its source + date, or "unverified")
 * GET  /wholesale/skip-trace/compare     every product ranked by TOTAL cost for N records
 * POST /wholesale/skip-trace/estimate    saves an estimate the owner can approve
 * POST /wholesale/skip-trace/estimates/{id}/confirm   admin types the phrase; nothing runs or queues
 *
 * Nothing on this panel calls a vendor. Numbers come from the server; a price
 * nobody published shows "—", never a guess.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { readAuthority } from '../../../auth/workspaceAuthority'
import { Alert, Metric, Metrics, Panel, Tag } from '../ds/ds'
import { errText } from '../wsShared'
import './ops.css'
import './skiptrace.css'

const usd = (c, blank = '—') => {
  if (c === null || c === undefined) return blank
  const v = Number(c) / 100
  return v >= 100 ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : `$${v.toFixed(v < 1 && v > 0 ? 3 : 2)}`
}
const perRec = (c) => (c === null || c === undefined ? '—' : `${Number(c).toFixed(Number(c) < 10 ? 2 : 1)}¢`)
const yn = (v) => (v === true ? 'Yes' : v === false ? 'No' : 'Unknown')
const PRICE_TAG = {
  vendor_published: ['live', 'Vendor price'],
  third_party_reported: ['sandbox', 'Third-party report'],
  unverified: ['danger', 'Unverified'],
}

export default function SkipTraceCostPanel() {
  const [prov, setProv] = useState(null)
  const [records, setRecords] = useState(250)
  const [hitRate, setHitRate] = useState(70)
  const [cmp, setCmp] = useState(null)
  const [est, setEst] = useState(null)
  const [product, setProduct] = useState('tracerfy')
  const [phrase, setPhrase] = useState('')
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  useEffect(() => {
    api.get('/wholesale/skip-trace/providers').then(setProv).catch(e => setErr(errText(e)))
  }, [])

  const compare = useCallback(async (override) => {
    setErr('')
    const n = Math.max(0, Math.min(500000, Number(override ?? records) || 0))
    const hr = Math.max(0, Math.min(100, Number(hitRate) || 0)) / 100
    try { setCmp(await api.get(`/wholesale/skip-trace/compare?records=${n}&hit_rate=${hr}`)) } catch (e) { setErr(errText(e)) }
  }, [records, hitRate])
  useEffect(() => { compare() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function estimate() {
    setErr(''); setNote(''); setPhrase('')
    try {
      setEst(await api.post('/wholesale/skip-trace/estimate', {
        product_key: product, record_count: Number(records) || 0, hit_rate: (Number(hitRate) || 0) / 100,
      }))
    } catch (e) { setErr(errText(e)) }
  }
  async function approve() {
    setErr(''); setNote('')
    try {
      const r = await api.post(`/wholesale/skip-trace/estimates/${est.estimate_id}/confirm`, { confirm: phrase })
      setEst({ ...est, ...r }); setNote(r.note)
    } catch (e) { setErr(errText(e)) }
  }

  const facts = prov?.integration?.current
  const rec = cmp?.recommendation
  const cur = cmp?.current
  const products = (prov?.items || []).filter(p => p.is_active)

  return (
    <Panel title="Skip-trace cost — lowest total for this volume"
           hint="Estimates only. Nothing here calls a vendor or queues a run.">
      {err ? <Alert kind="warn">{err}</Alert> : null}
      {note ? <Alert kind="ok">{note}</Alert> : null}

      {facts ? (
        <div className="wso-banner wso-banner--warn">
          <strong>In use today:</strong> {facts.provider} {facts.product} — {facts.endpoint}. {facts.cost}. {facts.why_010}
          {' '}Default for new batches: <strong>{prov.integration.default_product}</strong> (config {prov.integration.default_product_env}).
        </div>
      ) : null}

      <div className="wso-form" style={{ marginBottom: 12 }}>
        <label className="wso-label">Records in the batch
          <input className="wso-input" type="number" min="0" max="500000" value={records}
                 onChange={e => setRecords(e.target.value)} />
        </label>
        <label className="wso-label">Planning hit rate (%) — an assumption, not measured
          <input className="wso-input" type="number" min="0" max="100" value={hitRate}
                 onChange={e => setHitRate(e.target.value)} />
        </label>
        <div className="wso-row">
          <button className="btn btn--primary" onClick={() => compare()}>Compare providers</button>
          {[250, 500].map(n => (
            <button key={n} className="btn btn--secondary" onClick={() => { setRecords(n); compare(n) }}>{n}</button>
          ))}
        </div>
      </div>

      {rec ? (
        <>
          <p className="stc-rec">Lowest total for {cmp.records} records: <strong>{rec.label}</strong> <Tag kind="live">recommended</Tag></p>
          <Metrics label="Recommendation">
            <Metric label={`Recommended — this batch (${cmp.records})`} value={usd(rec.this_batch_cents)} />
            <Metric label="Month incl. minimum" value={usd(rec.monthly_incl_minimum_cents)} />
            {cur ? <Metric label={`Today's product (${cur.label})`} value={usd(cur.this_batch_cents)} /> : null}
          </Metrics>
          <ul className="wso-list" style={{ margin: '12px 0' }}>
            {cmp.reasoning.map((r, i) => <li key={i} className="wso-small">{r}</li>)}
          </ul>
        </>
      ) : null}

      {cmp ? (
        <div className="wso-table-wrap">
          <table className="wso-table stc-table">
            <thead>
              <tr><th>#</th><th>Provider / product</th><th>Per hit</th><th>Misses charged</th><th>Minimum</th>
                <th>This batch</th><th>Worst case</th><th>Month incl. min.</th><th>All-in / record</th>
                <th>API · batch · webhook</th><th>Price source</th></tr>
            </thead>
            <tbody>
              {cmp.items.map(i => {
                const [kind, label] = PRICE_TAG[i.price_status] || PRICE_TAG.unverified
                return (
                  <tr key={i.key} className={i.runnable ? '' : 'stc-row--off'}>
                    <td>{i.rank}</td>
                    <td className="stc-wrap">
                      <strong>{i.label}</strong>
                      {i.is_current ? <> <Tag kind="info">in use</Tag></> : null}
                      {i.is_default ? <> <Tag kind="live">default</Tag></> : null}
                      {rec && rec.key === i.key ? <> <Tag kind="live">lowest total</Tag></> : null}
                      {!i.runnable ? <> <Tag kind="danger">{i.provider === 'benchmark_derrick' ? 'benchmark'
                        : i.api_available === false ? 'no public API' : !i.is_active ? 'inactive' : 'API not documented'}</Tag></> : null}
                      <div className="wso-small wso-muted">{i.endpoint_label}</div>
                    </td>
                    <td>{usd(i.cost_per_hit_cents)}</td>
                    <td>{yn(i.misses_charged)}</td>
                    <td className="stc-wrap wso-small">{i.monthly_minimum_note}</td>
                    <td>{usd(i.this_batch_cents)}</td>
                    <td>{usd(i.maximum_cents)}</td>
                    <td><strong>{usd(i.monthly_incl_minimum_cents)}</strong></td>
                    <td>{perRec(i.per_record_effective_cents)}</td>
                    <td className="wso-small">{yn(i.api_available)} · {yn(i.batch_supported)} · {yn(i.webhook_supported)}</td>
                    <td className="stc-wrap wso-small">
                      <Tag kind={kind}>{label}</Tag>{' '}
                      {i.source_url ? <a href={i.source_url} target="_blank" rel="noreferrer">source</a> : 'no source'}
                      {i.verified_at ? ` · read ${i.verified_at}` : ' · owner to confirm'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : <p className="wso-muted">Loading…</p>}

      <h3 className="stc-h">Estimate a batch before running it</h3>
      <div className="wso-form">
        <label className="wso-label">Provider / product
          <select className="wso-select" value={product} onChange={e => setProduct(e.target.value)}>
            <option value="tracerfy">Tracerfy — configured default</option>
            {products.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
        </label>
        <div className="wso-row"><button className="btn btn--primary" onClick={estimate}>Estimate {Number(records) || 0} records</button></div>
      </div>
      {est ? (
        <div className="wso-item" style={{ marginTop: 12 }}>
          <div className="wso-item__title">ESTIMATED TOTAL COST: {est.estimated_total ?? '—'} <span className="wso-muted">
            (worst case {est.maximum_total ?? '—'} · month incl. minimum {usd(est.monthly_total_cents)})</span></div>
          <p className="wso-item__body wso-small">
            {est.records} records · {est.already_traced} already traced (excluded) · {est.billable_requests} billable ·
            {' '}{est.monthly_minimum_note || ''} Status: <strong>{est.status}</strong>
          </p>
          <ul className="wso-list" style={{ marginTop: 6 }}>
            {(est.assumptions || []).map((a, i) => <li key={i} className="wso-small wso-muted">{a}</li>)}
          </ul>
          {est.status === 'estimated' && !est.approvable ? (
            <p className="wso-small wso-muted" style={{ marginTop: 8 }}>
              Count-only estimate: informational. A paid run can only be approved against an estimate of the
              actual property selection (property ids), after dedupe.
            </p>
          ) : null}
          {est.status === 'estimated' && est.approvable && !readAuthority().isManager ? (
            <p className="wso-small wso-muted" style={{ marginTop: 10 }}>
              An administrator of this workspace approves paid spend against this estimate.
            </p>
          ) : null}
          {est.status === 'estimated' && est.approvable && readAuthority().isManager ? (
            <div className="wso-form stc-approve" style={{ marginTop: 10 }}>
              <label className="wso-label">Admin approval — type <code>{est.confirmation_phrase}</code>
                <input className="wso-input" value={phrase} onChange={e => setPhrase(e.target.value)} />
              </label>
              <div className="wso-row">
                <button className="btn btn--secondary" disabled={phrase !== est.confirmation_phrase} onClick={approve}>Approve this total</button>
              </div>
              <p className="wso-small wso-muted" style={{ gridColumn: '1 / -1', margin: 0 }}>
                Approving records the decision only. No run starts; a paid run must present this estimate id,
                and is refused for another provider, a larger batch, or records outside this estimate. One approval, one run.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </Panel>
  )
}
