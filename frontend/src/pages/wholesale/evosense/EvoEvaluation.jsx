/* PROVIDER EVALUATION - the referee for vendors, before anyone buys one.
 *
 * The same authorized sample through each provider, compared on the same
 * metrics. Results are REAL PROVIDER EVALUATION DATA, isolated: nothing a
 * vendor returns becomes a contact, a comp or a deal value, and nothing is
 * sent to anyone. A run that calls a real vendor costs money, so it needs
 * the owner's typed confirmation ("RUN PAID EVALUATION <evaluation id>") after a plan shows the maximum spend - the server
 * says exactly which phrase. Admin only.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../../api/client'
import { readAuthority } from '../../../auth/workspaceAuthority'
import { errText } from '../wsShared'
import { Alert, Panel, cents } from '../ds/ds'

const pct = (v) => (v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`)
const c2 = (v) => (v === null || v === undefined ? '—' : cents(v))

const CONTACT_ROWS = [
  ['match_rate', 'Match rate', pct], ['correct_owner_rate_truth', 'Correct owner (ground truth)', pct],
  ['owner_name_agreement_rate', 'Name agrees with owner of record (proxy)', pct],
  ['phone_coverage', 'Phone coverage', pct], ['usable_phone_coverage', 'Usable phone coverage', pct],
  ['mobile_coverage', 'Mobile coverage', pct], ['usable_mobile_coverage', 'Usable mobile coverage', pct],
  ['email_coverage', 'Email coverage', pct], ['usable_email_coverage', 'Usable email coverage', pct],
  ['hits_charged', 'Hits charged', (v) => v], ['hits_free', 'Hits free', (v) => v],
  ['misses', 'Misses', (v) => v], ['misses_charged', 'Misses charged', (v) => v], ['refunded', 'Refunded (failures)', (v) => v], ['line_type_completeness', 'Line type supplied', pct],
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
  ['eligible_comps', 'Eligible comps', (v) => v], ['excluded_comps', 'Excluded comps', (v) => v],
  ['median_distance_miles', 'Median distance (mi)', (v) => (v ?? '—')],
  ['ground_truth_sales_matched', 'Ground-truth sales matched', (v) => v],
  ['ground_truth_price_accuracy', 'Ground-truth price accuracy', pct],
  ['median_latency_ms', 'Median latency (ms)', (v) => (v ?? '—')], ['failures', 'Failures', (v) => v],
  ['cost_per_subject_cents', 'Cost / subject', c2], ['cost_per_eligible_comp_cents', 'Cost / eligible comp', c2],
  ['cost_cents', 'Total cost', c2],
]

function Report({ ev }) {
  const r = ev.report
  if (!r) return null
  return (
    <div style={{ marginBottom: 12 }}>
      {r.lines.map((l) => <p key={l.provider} style={{ margin: '4px 0' }}><strong>{l.label}:</strong> {l.summary}</p>)}
      {r.notes.map((n, i) => <p key={i} className="evo-muted evo-small" style={{ margin: '2px 0' }}>{n}</p>)}
      {(r.examples || []).length ? (
        <details style={{ marginTop: 6 }}><summary className="evo-small">Examples ({r.examples.length}) - masked, with provenance</summary>
          <ul className="evo-small">{r.examples.map((e, i) => (
            <li key={i}><strong>{e.kind}</strong> · {e.why} · name match: {e.name_match || 'none'} · {e.phones.map((p) => `${p.masked} ${p.type || '?'}${p.referee_type ? `/${p.referee_type}` : ''}${p.last_seen ? ` seen ${p.last_seen}` : ''}`).join(', ')}{e.provider_reference ? ` · ref ${e.provider_reference}` : ''}</li>
          ))}</ul></details>
      ) : null}
    </div>
  )
}

function Plan({ ev, busy, onExecute }) {
  const [phrase, setPhrase] = useState('')
  const plan = ev.plan || {}
  return (
    <div>
      <p><strong>Nothing has been called.</strong> Maximum spend: <strong>{cents(ev.max_spend_cents)}</strong> for {plan.sample_size} properties.</p>
      <div className="evo-table-wrap">
        <table className="evo-table">
          <thead><tr><th>Provider</th><th>Role</th><th>Max calls</th><th>Per call</th><th>Maximum</th><th>Price</th></tr></thead>
          <tbody>{(plan.lines || []).map((l) => (
            <tr key={l.provider + l.role}><td>{l.label}</td><td>{l.role}</td><td>{l.max_calls}</td><td>{cents(l.unit_cents)}</td>
              <td>{cents(l.max_cents)}</td><td className="evo-small">{l.price_confirmed ? 'published' : 'UNCONFIRMED'}{l.misses_free ? ' · misses free' : ''}</td></tr>
          ))}</tbody>
        </table>
      </div>
      <p className="evo-muted evo-small">{plan.note}</p>
      {(() => {
        const rd = ev.readiness || { ready: (plan.lines || []).every((l) => !l.readiness || l.readiness.ready),
                                     lines: (plan.lines || []).map((l) => ({ provider: l.provider, ...(l.readiness || {}) })) }
        return rd.ready ? <p className="evo-small">Ready to run once authorized.</p> : (
          <p className="evo-small"><strong>Not ready yet:</strong> {rd.lines.filter((l) => !l.ready).map((l) => `${l.provider}: ${l.why}`).join(' · ')}</p>)
      })()}
      <label className="evo-small" style={{ display: 'block' }}>To authorize exactly this plan, type: <code>{ev.confirmation}</code>
        <input className="evo-input" value={phrase} onChange={(e) => setPhrase(e.target.value)} />
      </label>
      <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" style={{ marginTop: 8 }}
              disabled={busy || phrase.trim() !== ev.confirmation} onClick={() => onExecute(ev.id, phrase.trim())}>
        Authorize and run (up to {cents(ev.max_spend_cents)})</button>
    </div>
  )
}

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
              <tr><td>Exclusion reasons</td>{provs.map((p) => <td key={p.provider} className="evo-small">
                {Object.entries(p.exclusion_reasons || {}).map(([s, n]) => `${s} ${n}`).join(' · ') || '—'}</td>)}</tr>
              <tr><td>Would-be ARV confidence</td>{provs.map((p) => <td key={p.provider} className="evo-small">
                {Object.entries(p.would_be_arv_confidence || {}).map(([s, n]) => `${s} ${n}`).join(' · ') || '—'}</td>)}</tr>
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
  // Every write here is workspace-admin only on the server.
  const isAdmin = readAuthority().isManager
  const [mode, setMode] = useState('contact')
  const [setup, setSetup] = useState(null)
  const [runs, setRuns] = useState([])
  const [open, setOpen] = useState(null)
  const [pick, setPick] = useState({})
  const [skip, setSkip] = useState({})
  const [referee, setReferee] = useState('')
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
  useEffect(() => { load(); setPick({}); setSkip({}); setReferee('') }, [load])

  const candidates = setup ? setup.providers[mode] || [] : []
  const sample = setup ? setup.sample.items : []
  const chosen = useMemo(() => Object.keys(pick).filter((k) => pick[k]), [pick])
  const ids = sample.filter((s) => !skip[s.property_id]).map((s) => s.property_id)

  async function run() {
    setBusy(true); setError(null); setNotice(null)
    try {
      const ev = await api.post('/wholesale/evosense/provider-evaluations', {
        name: `${mode === 'comps' ? 'Comps' : 'Contact'} evaluation ${new Date().toLocaleDateString()}`,
        mode, provider_keys: chosen, property_ids: ids, referee_key: referee || null })
      setNotice(ev.status === 'planned'
        ? `Planned - nothing called. Maximum spend ${cents(ev.max_spend_cents)}. Review and authorize below.`
        : `Evaluation finished (${ev.label}). Cost ${cents(ev.total_cost_cents)}.`)
      setOpen(ev); load()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  async function execute(id, phrase) {
    setBusy(true); setError(null); setNotice(null)
    try {
      const ev = await api.post(`/wholesale/evosense/provider-evaluations/${id}/execute`, { confirm: phrase })
      setNotice(`Evaluation ${ev.status}. Spent ${cents(ev.total_cost_cents)} of ${cents(ev.max_spend_cents)} authorized.`)
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
      {!isAdmin ? <p className="evo-muted evo-small" style={{ margin: 0 }}>Provider evaluations and ground truth are run by an administrator of this workspace. You can read the results.</p> : null}
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
            <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" style={{ marginTop: 8 }}
                    disabled={busy || !isAdmin || !chosen.length || !ids.length} onClick={run}>Plan evaluation</button>
            <p className="evo-muted evo-small">Planning calls nothing. A plan with a paid provider shows its maximum spend and runs only when you authorize it with its own phrase; a sandbox-only plan runs at once (synthetic, free).</p>
          </>
        )}
      </Panel>

      <Panel title="Evaluations" flush>
        {!runs.length ? <p className="evo-muted" style={{ padding: '0 20px' }}>None yet.</p> : (
          <ul className="evo-feed" style={{ padding: '0 20px' }}>
            {runs.map((r) => (
              <li key={r.id}>
                <strong>{r.name}</strong> · {r.label} · {r.mode} · {r.status}{r.status === 'planned' ? ` · max ${cents(r.max_spend_cents)}` : ` · ${cents(r.total_cost_cents)}`}{r.purged ? ' · data deleted' : ''}{' '}
                <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => view(r.id)}>Open</button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <GroundTruth sample={sample} />
      <TruthGate />

      {open ? (
        <Panel title={`${open.name} — ${open.label}`}
               action={!open.purged && open.status !== 'planned' ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy || !isAdmin}
                                              onClick={() => purge(open.id)}>Delete returned data</button> : null}
               hint={(open.results && open.results.label) || ''}>
          {open.status === 'planned' ? <Plan ev={open} busy={busy} onExecute={execute} /> : (
            <>
              <Report ev={open} />
              <Results ev={open} />
            </>
          )}
        </Panel>
      ) : null}
    </div>
  )
}


/* GROUND TRUTH: only what the workspace already lawfully knows, with how it
 * is known. Never inferred. Used only to score evaluations. Admin only. */
function GroundTruth({ sample }) {
  const [items, setItems] = useState(null)
  const [f, setF] = useState({ kind: 'contact', property_id: '', phones: '', emails: '', street_address: '',
                               sale_price: '', sale_date: '', source_note: '' })
  const [err, setErr] = useState(null)
  const load = useCallback(() => {
    api.get('/wholesale/evosense/ground-truth').then((r) => setItems(r.items)).catch((e) => setErr(errText(e)))
  }, [])
  useEffect(() => { load() }, [load])
  async function add() {
    setErr(null)
    const data = f.kind === 'contact'
      ? { phones: f.phones.split(',').map((x) => x.trim()).filter(Boolean), emails: f.emails.split(',').map((x) => x.trim()).filter(Boolean) }
      : { street_address: f.street_address, sale_price: Number(f.sale_price), sale_date: f.sale_date }
    try {
      await api.post('/wholesale/evosense/ground-truth', { kind: f.kind, property_id: f.property_id || null, data, source_note: f.source_note })
      setF({ ...f, phones: '', emails: '', street_address: '', sale_price: '', sale_date: '', source_note: '' }); load()
    } catch (e) { setErr(errText(e)) }
  }
  async function del(id) {
    try { await api.delete(`/wholesale/evosense/ground-truth/${id}`); load() } catch (e) { setErr(errText(e)) }
  }
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  return (
    <Panel title="Ground truth" hint="Only facts you already lawfully know, with how you know them · used only to score evaluations">
      <Alert>{err}</Alert>
      {items === null ? null : (
        <ul className="evo-feed">
          {items.map((g) => (
            <li key={g.id} className="evo-small">{g.kind === 'contact' ? `Contact for property ${g.property_id?.slice(0, 8)}: ${(g.data.phones || []).length} phone(s), ${(g.data.emails || []).length} email(s)` :
              `Closed sale ${g.data.street_address}: ${cents(g.data.sale_price * 100)} on ${g.data.sale_date}`} — {g.source_note}{' '}
              <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={!isAdmin} onClick={() => del(g.id)}>Delete</button></li>
          ))}
          {!items.length ? <li className="evo-muted">None recorded. Without it, correct-owner and price accuracy are not measured (never guessed).</li> : null}
        </ul>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8 }}>
        <label className="evo-small">Kind<select value={f.kind} onChange={set('kind')}>
          <option value="contact">Known owner contact</option><option value="closed_sale">Known closed sale</option></select></label>
        {f.kind === 'contact' ? (
          <>
            <label className="evo-small">Property<select value={f.property_id} onChange={set('property_id')}>
              <option value="">choose…</option>
              {sample.map((s) => <option key={s.property_id} value={s.property_id}>{s.address}</option>)}</select></label>
            <label className="evo-small">Known phone(s)<input className="evo-input" value={f.phones} onChange={set('phones')} placeholder="comma separated" /></label>
            <label className="evo-small">Known email(s)<input className="evo-input" value={f.emails} onChange={set('emails')} /></label>
          </>
        ) : (
          <>
            <label className="evo-small">Street address<input className="evo-input" value={f.street_address} onChange={set('street_address')} /></label>
            <label className="evo-small">Closed price<input className="evo-input" value={f.sale_price} onChange={set('sale_price')} /></label>
            <label className="evo-small">Close date<input className="evo-input" type="date" value={f.sale_date} onChange={set('sale_date')} /></label>
          </>
        )}
        <label className="evo-small" style={{ gridColumn: '1 / -1' }}>How is this lawfully known?
          <input className="evo-input" value={f.source_note} onChange={set('source_note')} placeholder="e.g. seller's phone from our signed contract; MLS closed record from our broker" /></label>
      </div>
      <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" style={{ marginTop: 8 }} disabled={!isAdmin} onClick={add}>Add ground truth</button>
    </Panel>
  )
}

/* THE DFW SOLD-COMPS TRUTH GATE: every criterion, met or not and why. */
function TruthGate() {
  const [items, setItems] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    api.get('/wholesale/evosense/truth-gate').then((r) => setItems(r.items)).catch((e) => setErr(errText(e)))
  }, [])
  if (!items) return err ? <Alert>{err}</Alert> : null
  return (
    <Panel title="DFW sold-comps truth gate" hint="A field named soldPrice proves nothing. SOLD_COMPS stays manual until every criterion is met.">
      {items.map((g) => (
        <div key={g.provider} style={{ marginBottom: 10 }}>
          <strong>{g.provider}</strong> — {g.passed ? 'PASSED' : 'NOT MET'}
          <ul className="evo-small" style={{ margin: '4px 0 0 18px' }}>
            {g.criteria.map((c) => <li key={c.code}>{c.met ? '✓' : '✗'} {c.label} <span className="evo-muted">— {c.why}{c.evidence ? `: ${c.evidence}` : ''}</span></li>)}
          </ul>
          <p className="evo-muted evo-small" style={{ margin: '4px 0 0' }}>{g.note}</p>
        </div>
      ))}
    </Panel>
  )
}
