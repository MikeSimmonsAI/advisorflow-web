/* PROVIDER EVALUATION - the referee for vendors, before anyone buys one.
 *
 * The same authorized sample through each provider, compared on the same
 * metrics. Results are REAL PROVIDER EVALUATION DATA, isolated: nothing a
 * vendor returns becomes a contact, a comp or a deal value, and nothing is
 * sent to anyone. A run that calls a real vendor costs money, so it needs
 * the owner's typed confirmation ("RUN PAID EVALUATION <n>") - the server
 * says exactly which phrase. Admin only.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import { Alert, Panel, cents } from '../ds/ds'

const pct = (v) => (v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`)
const c2 = (v) => (v === null || v === undefined ? '—' : cents(v))

const CONTACT_ROWS = [
  ['match_rate', 'Match rate', pct], ['correct_owner_rate_truth', 'Correct owner (ground truth)', pct],
  ['owner_name_agreement_rate', 'Name agrees with owner of record (proxy)', pct],
  ['phone_coverage', 'Phone coverage', pct], ['mobile_coverage', 'Mobile coverage', pct],
  ['email_coverage', 'Email coverage', pct], ['line_type_completeness', 'Line type supplied', pct],
  ['line_type_agreement', 'Line type agrees with referee', pct], ['false_positive_rate', 'False positives', pct],
  ['median_last_seen_days', 'Median last seen (days)', (v) => (v ?? '—')],
  ['median_latency_ms', 'Median latency (ms)', (v) => (v ?? '—')], ['failures', 'Failures', (v) => v],
  ['cost_per_lookup_cents', 'Cost / lookup', c2], ['cost_per_matched_owner_cents', 'Cost / matched owner', c2],
  ['cost_per_usable_contact_cents', 'Cost / usable contact', c2],
  ['cost_per_verified_contact_cents', 'Cost / verified contact', c2], ['cost_cents', 'Total cost', c2],
]
const COMPS_ROWS = [
  ['subjects', 'Subjects', (v) => v], ['comps_returned', 'Comps returned', (v) => v],
  ['closed_price_share', 'Closed sale price', pct], ['mls_closed_share', 'MLS closed price', pct],
  ['median_close_age_days', 'Median close age (days)', (v) => (v ?? '—')],
  ['facts_complete_share', 'Property facts complete', pct],
  ['eligible_per_subject', 'Eligible comps / subject', (v) => (v ?? '—')],
  ['subjects_with_3_eligible', 'Subjects with ≥3 eligible', pct],
  ['median_latency_ms', 'Median latency (ms)', (v) => (v ?? '—')], ['failures', 'Failures', (v) => v],
  ['cost_per_subject_cents', 'Cost / subject', c2], ['cost_per_eligible_comp_cents', 'Cost / eligible comp', c2],
  ['cost_cents', 'Total cost', c2],
]

function Results({ ev }) {
  const res = ev.results || {}
  const provs = Object.values(res.providers || {})
  if (!provs.length) return null
  const rows = ev.mode === 'comps' ? COMPS_ROWS : CONTACT_ROWS
  return (
    <div className="evo-table-wrap">
      <table className="evo-table">
        <thead><tr><th scope="col">Metric</th>{provs.map((p) => <th key={p.provider} scope="col">{p.label}</th>)}</tr></thead>
        <tbody>
          {rows.map(([k, label, fmt]) => (
            <tr key={k}><td>{label}</td>{provs.map((p) => <td key={p.provider}>{fmt(p[k])}</td>)}</tr>
          ))}
          {ev.mode === 'comps' ? (
            <>
              <tr><td>Price sources</td>{provs.map((p) => <td key={p.provider} className="evo-small">
                {Object.entries(p.comps_by_price_source || {}).map(([s, n]) => `${s} ${n}`).join(' · ') || '—'}</td>)}</tr>
              <tr><td>Production ready</td>{provs.map((p) => <td key={p.provider} className="evo-small">
                {p.production_ready ? 'yes' : 'NO'} — {p.readiness_note}</td>)}</tr>
            </>
          ) : null}
          <tr><td>Price basis</td>{provs.map((p) => <td key={p.provider} className="evo-small">
            {p.price_confirmed ? 'published price' : 'UNCONFIRMED price'}{p.price_note ? ` — ${p.price_note}` : ''}</td>)}</tr>
        </tbody>
      </table>
    </div>
  )
}

export default function EvoEvaluation() {
  const [mode, setMode] = useState('contact')
  const [setup, setSetup] = useState(null)
  const [runs, setRuns] = useState([])
  const [open, setOpen] = useState(null)
  const [pick, setPick] = useState({})
  const [skip, setSkip] = useState({})
  const [referee, setReferee] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const load = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([
        api.get(`/wholesale/evosense/provider-evaluations/setup?mode=${mode}`),
        api.get('/wholesale/evosense/provider-evaluations')])
      setSetup(s); setRuns(r.items || []); setError(null)
    } catch (e) { setError(errText(e)) }
  }, [mode])
  useEffect(() => { load(); setPick({}); setSkip({}); setReferee(''); setConfirm('') }, [load])

  const candidates = setup ? setup.providers[mode] || [] : []
  const sample = setup ? setup.sample.items : []
  const chosen = useMemo(() => Object.keys(pick).filter((k) => pick[k]), [pick])
  const ids = sample.filter((s) => !skip[s.property_id]).map((s) => s.property_id)

  async function run() {
    setBusy(true); setError(null); setNotice(null)
    try {
      const ev = await api.post('/wholesale/evosense/provider-evaluations', {
        name: `${mode === 'comps' ? 'Comps' : 'Contact'} evaluation ${new Date().toLocaleDateString()}`,
        mode, provider_keys: chosen, property_ids: ids, referee_key: referee || null, confirm: confirm || null })
      setNotice(`Evaluation finished (${ev.label}). Cost ${cents(ev.total_cost_cents)}.`)
      setOpen(ev); load()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  async function view(id) {
    try { setOpen(await api.get(`/wholesale/evosense/provider-evaluations/${id}`)) } catch (e) { setError(errText(e)) }
  }
  async function purge(id) {
    setBusy(true)
    try { setOpen(await api.post(`/wholesale/evosense/provider-evaluations/${id}/purge`)); setNotice('Returned data deleted; metrics kept.'); load() }
    catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  return (
    <div className="evo-stack">
      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>
      <Panel title="Provider evaluation"
             hint="Isolated: nothing returned becomes a contact, comp or deal value; nothing is sent to anyone · paid runs need the owner's typed confirmation · admin only">
        <div className="evo-chips" style={{ marginBottom: 12 }}>
          {['contact', 'comps'].map((m) => (
            <button key={m} type="button" className={`evo-btn evo-btn--sm ${mode === m ? 'evo-btn--primary' : 'evo-btn--ghost'}`}
                    onClick={() => setMode(m)}>{m === 'contact' ? 'Contact / skip trace' : 'Sold comps'}</button>
          ))}
        </div>
        {!setup ? <p className="evo-muted">Loading…</p> : (
          <>
            <h3 className="evo-panel__title" style={{ fontSize: 13 }}>Providers</h3>
            {candidates.map((p) => (
              <label key={p.key} className="evo-small" style={{ display: 'block', marginBottom: 6 }}>
                <input type="checkbox" disabled={!p.ready} checked={!!pick[p.key]}
                       onChange={(e) => setPick({ ...pick, [p.key]: e.target.checked })} />{' '}
                <strong>{p.label}</strong> {p.connector_kind === 'sandbox' ? '(SANDBOX)' : ''} · {cents(p.cost_cents)} per call
                {p.price_confirmed === false ? ' (UNCONFIRMED price)' : ''}
                <span className="evo-muted" style={{ display: 'block' }}>
                  {p.ready ? 'Ready to evaluate' : (p.missing_env && p.missing_env.length ? `Needs platform credential: ${p.missing_env.join(', ')}` : p.why)}
                </span>
              </label>
            ))}
            {mode === 'contact' && setup.providers.referees.length ? (
              <label className="evo-small" style={{ display: 'block', margin: '10px 0' }}>Line-type referee (lookup only, sends nothing):{' '}
                <select value={referee} onChange={(e) => setReferee(e.target.value)}>
                  <option value="">none</option>
                  {setup.providers.referees.map((r) => <option key={r.key} value={r.key} disabled={!r.ready}>
                    {r.label}{r.ready ? '' : ' (not ready)'}</option>)}
                </select>
              </label>
            ) : null}
            <h3 className="evo-panel__title" style={{ fontSize: 13, marginTop: 12 }}>
              Sample ({ids.length} of {sample.length}) <span className="evo-muted evo-small">{setup.sample.note}</span></h3>
            <div style={{ maxHeight: 220, overflow: 'auto' }}>
              {sample.map((s) => (
                <label key={s.property_id} className="evo-small" style={{ display: 'block' }}>
                  <input type="checkbox" checked={!skip[s.property_id]}
                         onChange={(e) => setSkip({ ...skip, [s.property_id]: !e.target.checked })} />{' '}
                  {s.address}, {s.city}{s.owner_of_record ? ` — ${s.owner_of_record}` : ''}
                </label>
              ))}
              {!sample.length ? <p className="evo-muted">No properties fit this mode's sample yet.</p> : null}
            </div>
            {(setup.sample.left_out || []).length ? (
              <details className="evo-small" style={{ marginTop: 6 }}>
                <summary>{setup.sample.left_out.length} awaiting propert{setup.sample.left_out.length === 1 ? 'y' : 'ies'} left out, with the reason</summary>
                {setup.sample.left_out.map((x) => <div key={x.property_id} className="evo-muted">{x.address}: {x.why}</div>)}
              </details>
            ) : null}
            <label className="evo-small" style={{ display: 'block', marginTop: 10 }}>
              Owner confirmation for paid calls ({setup.confirmation_format}; the server tells you the exact number):
              <input className="evo-input" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="RUN PAID EVALUATION n" />
            </label>
            <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" style={{ marginTop: 8 }}
                    disabled={busy || !chosen.length || !ids.length} onClick={run}>Run evaluation</button>
          </>
        )}
      </Panel>

      <Panel title="Evaluations" flush>
        {!runs.length ? <p className="evo-muted" style={{ padding: '0 20px' }}>None yet.</p> : (
          <ul className="evo-feed" style={{ padding: '0 20px' }}>
            {runs.map((r) => (
              <li key={r.id}>
                <strong>{r.name}</strong> · {r.label} · {r.mode} · {cents(r.total_cost_cents)}{r.purged ? ' · data deleted' : ''}{' '}
                <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => view(r.id)}>Open</button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {open ? (
        <Panel title={`${open.name} — ${open.label}`}
               action={!open.purged ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy}
                                              onClick={() => purge(open.id)}>Delete returned data</button> : null}
               hint={(open.results && open.results.label) || ''}>
          <Results ev={open} />
        </Panel>
      ) : null}
    </div>
  )
}
