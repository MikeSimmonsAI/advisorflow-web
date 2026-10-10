/* Wholesale pilot controls — the first controlled batch (250–500 records).
 *
 * GET/PUT /wholesale/ops/pilot and GET /wholesale/ops/skip-trace. Every number
 * is read from the server: the batch cap, the kill switches, and the skip-trace
 * ledger (count, cost, hits, no-result, errors, duplicate-billing protection),
 * and the skip-trace cost panel (ESTIMATED TOTAL COST before any paid run).
 * The page says what ENFORCES each control, so a recorded setting is never
 * presented as an enforced one.
 */
import { useEffect, useRef, useState } from 'react'
import { api } from '../../../api/client'
import { isWholesaleAdmin } from '../../../auth/workspaceAuthority'
import '../../../styles/shared.css'
import '../wholesale.css'
import { Alert, EvoApp, Hero, Metric, Metrics, Panel, Tag } from '../ds/ds'
import '../ds/evo-pages.css'
import { errText, fmtWhen } from '../wsShared'
import { useRecord } from '../wsRecordHook'
import RecoveryNote from '../wsRecoveryNote'
import { knownCount, pilotSaveBlockedReason, supportCode, withCurrentOption } from '../wsListState'
import { DistributionNotice } from './DealOpsPanel'
import SkipTraceCostPanel from './SkipTraceCostPanel'
import './ops.css'

const cents = (c) => (c === null || c === undefined ? '—' : `$${(c / 100).toFixed(2)}`)

export default function PilotControls() {
  const canManage = isWholesaleAdmin()
  const pilotR = useRecord(() => api.get('/wholesale/ops/pilot'))
  const skipR = useRecord(() => api.get('/wholesale/ops/skip-trace'))
  const strR = useRecord(async () => (await api.get('/wholesale/evosense/strategies')).items || [])
  const pilot = pilotR.rec.data
  const skip = skipR.rec.data
  const [form, setForm] = useState(null)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const saving = useRef(false)
  // The form is filled from the server once, and again after a save. A retry of a
  // failed refresh never overwrites what the operator is typing.
  const refill = useRef(true)
  useEffect(() => {
    if (!pilot || !refill.current) return
    refill.current = false
    setForm({ max_records: pilot.max_records, source: pilot.source || '', strategy_id: pilot.strategy_id || '',
              budget: ((pilot.skip_trace_budget_cents || 0) / 100).toFixed(2),
              outreach_daily_limit: pilot.outreach_daily_limit || 0, notes: pilot.notes || '' })
  }, [pilot])

  const blockedReason = pilotSaveBlockedReason(strR.view, busy)
  async function save(extra = {}) {
    // Synchronous latch: two clicks in one tick must not send two PUTs.
    if (saving.current || blockedReason) return
    saving.current = true
    setBusy(true); setErr(''); setNote('')
    try {
      const body = {
        max_records: Number(form.max_records), source: form.source || null,
        strategy_id: form.strategy_id || null,
        skip_trace_budget_cents: Math.round(Number(form.budget || 0) * 100),
        outreach_daily_limit: Number(form.outreach_daily_limit || 0), notes: form.notes || null, ...extra,
      }
      await api.put('/wholesale/ops/pilot', body)
      setNote('Saved.'); refill.current = true; pilotR.reload()
    } catch (e) {
      const code = supportCode(e)
      setErr(`${errText(e)}${code ? ` (support code ${code})` : ''}. Nothing you typed was lost.`)
    } finally { saving.current = false; setBusy(false) }
  }
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))
  const led = skip?.ledger
  const pilotDist = { data: pilot?.distribution ?? null, rec: pilotR.rec, view: pilotR.view, reload: pilotR.reload }
  const strategies = withCurrentOption(strR.rec.data, form?.strategy_id, 'Linked strategy (name unavailable)')

  return (
    <EvoApp world="operations">
      <Hero scene="capital" eyebrow="Wholesale pilot" title="Pilot controls"
            sub="The first controlled batch. The record count is a cap, never a target. Outreach and paid data stay off unless a person turns them on." />
      {err ? <Alert kind="warn">{err}</Alert> : null}
      {note ? <Alert kind="ok">{note}</Alert> : null}
      <RecoveryNote what="Pilot settings" rec={pilotR.rec} view={pilotR.view} onRetry={pilotR.reload} />
      {pilotR.view === 'loading' ? <p className="wso-muted" role="status">Loading pilot settings…</p> : null}
      {pilot ? (
        <>
          <DistributionNotice state={pilotDist} kind="buyers" />
          <DistributionNotice state={pilotDist} kind="funding" />
          <Panel title="Batch" hint={pilot.updated_at ? `Updated ${fmtWhen(pilot.updated_at)}${pilot.updated_by ? ` by ${pilot.updated_by}` : ''}` : 'Not configured yet'}
                 action={<Tag kind={pilot.status === 'running' ? 'live' : pilot.status === 'draft' ? 'info' : 'danger'}>{pilot.status}</Tag>}>
            {form ? (
              <div className="wso-form">
                <label className="wso-label">Maximum records (1–{pilot.limits.hard_cap}; recommended {pilot.limits.recommended_min}–{pilot.limits.recommended_max})
                  <input className="wso-input" type="number" min="1" max={pilot.limits.hard_cap} value={form.max_records}
                         onChange={e => set('max_records', e.target.value)} />
                </label>
                <label className="wso-label">Source
                  <input className="wso-input" value={form.source} placeholder="e.g. county tax delinquent list"
                         onChange={e => set('source', e.target.value)} />
                </label>
                <RecoveryNote what="The strategy list" rec={strR.rec} view={strR.view} onRetry={strR.reload}
                              effect="The linked strategy is kept as saved, and saving is paused until the list loads." />
                <label className="wso-label">Strategy
                  <select className="wso-select" value={form.strategy_id} aria-busy={strR.view === 'loading'}
                          disabled={strR.view === 'loading' || strR.view === 'error'}
                          onChange={e => set('strategy_id', e.target.value)}>
                    <option value="">— none linked —</option>
                    {strategies.map(s => <option key={s.id} value={s.id}>{s.name}{s.unavailable ? '' : ` (${s.status})`}</option>)}
                  </select>
                </label>
                <label className="wso-label">Skip-trace budget ($)
                  <input className="wso-input" type="number" min="0" step="0.01" value={form.budget}
                         onChange={e => set('budget', e.target.value)} />
                </label>
                <label className="wso-label">Outreach limit per day
                  <input className="wso-input" type="number" min="0" value={form.outreach_daily_limit}
                         onChange={e => set('outreach_daily_limit', e.target.value)} />
                </label>
                {/* Changing pilot controls is administrator-only on the server
                    (put_pilot). Everyone else reads them, with the reason. */}
                {canManage ? (
                <div className="wso-row">
                  <button type="button" className="btn btn--primary" disabled={!!blockedReason} aria-describedby="wso-pilot-block"
                          onClick={() => save()}>Save</button>
                  {pilot.status !== 'running' ? <button type="button" className="btn btn--secondary" disabled={!!blockedReason} aria-describedby="wso-pilot-block" onClick={() => save({ status: 'running' })}>Mark running</button> : null}
                  {/* Pause and Stop are safety actions: they stay available whenever no save is in flight. */}
                  {pilot.status === 'running' ? <button type="button" className="btn btn--secondary" disabled={busy} onClick={() => save({ status: 'paused' })}>Pause</button> : null}
                  {pilot.status !== 'stopped' ? <button type="button" className="btn btn--secondary" disabled={busy} onClick={() => save({ status: 'stopped' })}>Stop</button> : null}
                </div>
                <p id="wso-pilot-block" className="wso-small wso-muted" role="status">{blockedReason}</p>
                ) : (
                  <p className="wso-small wso-muted">Only an administrator of this workspace can change pilot controls or start, pause or stop the pilot.</p>
                )}
              </div>
            ) : null}
            <ul className="wso-list" style={{ marginTop: 12 }}>
              {Object.entries(pilot.enforcement).map(([k, v]) => (
                <li key={k} className="wso-small wso-muted"><strong>{(k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, ' ').replace(/ cents$/, '').replace(/^Pause stop$/, 'Pause / stop')}:</strong> {v}</li>
              ))}
            </ul>
            {pilot.kill_switches ? (
              <div className="wso-row" style={{ marginTop: 8 }}>
                {Object.entries(pilot.kill_switches).map(([k, v]) => (
                  <Tag key={k} kind={v ? 'danger' : 'live'}>{k.replace('paused_', '').replace(/_/g, ' ')}: {v ? 'paused' : 'on'}</Tag>
                ))}
              </div>
            ) : <p className="wso-small wso-muted">No kill-switch row yet for this workspace.</p>}
          </Panel>
        </>
      ) : null}

      <Panel title="Skip trace — from the cost ledger">
        <RecoveryNote what="The skip-trace ledger" rec={skipR.rec} view={skipR.view} onRetry={skipR.reload} />
        {!skip || !led ? <p className="wso-muted" role="status">{skipR.view === 'error' ? 'No skip-trace figures are shown.' : skipR.view === 'loading' ? 'Loading…' : 'The ledger was not included in the response.'}</p> : (
          <>
            <Metrics label="Skip trace">
              <Metric label="Attempts" value={knownCount(led.attempts)} />
              <Metric label="Hits" value={knownCount(led.hits)} />
              <Metric label="No result" value={knownCount(led.no_result)} />
              <Metric label="Errors" value={knownCount(led.errors)} />
              <Metric label="Charged" value={cents(led.total_charged_cents)} />
            </Metrics>
            <div className={`wso-banner${skip.target_viable ? '' : ' wso-banner--warn'}`} style={{ marginTop: 12 }}>
              Target ≈ $0.01 per record: {skip.target_note}
            </div>
            <p className="wso-small wso-muted">
              Duplicate-billing protection: {knownCount(skip.duplicate_billing_protection?.skipped_existing_data)} lookups skipped because the data
              was already on file · {knownCount(skip.duplicate_billing_protection?.retry_later)} deferred · {knownCount(skip.retried_properties)} properties retried.
              {' '}{skip.duplicate_billing_protection?.how}
            </p>
            {Object.keys(led.providers || {}).length ? (
              <div className="wso-table-wrap">
                <table className="wso-table">
                  <thead><tr><th>Provider</th><th>Attempts</th><th>Hits</th><th>No result</th><th>Errors</th><th>Charged</th><th>Per hit</th></tr></thead>
                  <tbody>
                    {Object.entries(led.providers || {}).map(([k, p]) => (
                      <tr key={k}><td>{k}</td><td>{knownCount(p.attempts)}</td><td>{knownCount(p.hits)}</td><td>{knownCount(p.no_result)}</td><td>{knownCount(p.errors)}</td>
                        <td>{cents(p.charged_cents)}</td><td>{p.cost_per_hit_cents === null ? '—' : cents(p.cost_per_hit_cents)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="wso-small wso-muted">No paid skip-trace calls recorded for this workspace.</p>}
            {skip.vendor_prices?.length ? (
              <p className="wso-small wso-muted">Configured vendor prices: {skip.vendor_prices.map(v =>
                `${v.label} ${v.cost_cents_per_hit === null || v.cost_cents_per_hit === undefined ? '—' : cents(v.cost_cents_per_hit)}/hit${v.price_confirmed ? '' : ' (unconfirmed)'}`).join(' · ')}</p>
            ) : null}
          </>
        )}
      </Panel>

      <SkipTraceCostPanel />
    </EvoApp>
  )
}
