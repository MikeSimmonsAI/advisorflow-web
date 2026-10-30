/* PROJECT OVERDRIVE Phase B (#439) - provider intelligence on the existing
 * one-property Acquisition Command screen (no new page, no redesign).
 *
 *   PropertyIntelligence  Ownership tab: where each key fact came from, what is
 *                         missing, ATTOM's status, and the routing decision -
 *                         shown BEFORE anything is bought. One button, only
 *                         when the server says a lookup can run.
 *   ContactIntelligence   Contacts tab: Tracerfy's status and this property's
 *                         lookup history with each decision, outcome and cost.
 *
 * Server: GET /wholesale/evosense/properties/{id}/intelligence and
 * POST /wholesale/evosense/properties/{id}/attom. */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../../api/client'
import { errText } from '../../wsShared'
import { Alert, Panel, Skeleton, Tag, ago, humanize, money } from '../../ds/ds'
import * as N from './networkModel'
import './network.css'

function useIntel(propertyId, refreshKey) {
  const [d, setD] = useState(null)
  const [error, setError] = useState(null)
  const load = useCallback(() => api.get(`/wholesale/evosense/properties/${propertyId}/intelligence`)
    .then((x) => { setD(x); setError(null) }).catch((e) => setError(errText(e))), [propertyId])
  useEffect(() => { load() }, [load, refreshKey])
  return [d, error, load]
}

function fmt(f) {
  if (!f.have) return <span className="evo-muted">missing</span>
  if (typeof f.value === 'number') return f.key === 'market_estimate' || f.key === 'recorded_loan' ? money(f.value) : f.value
  return f.value || 'on file'
}

export function PropertyIntelligence({ propertyId, canBuy, onChanged, refreshKey }) {
  const [d, error, load] = useIntel(propertyId, refreshKey)
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState(null)
  const [err, setErr] = useState(null)
  async function lookup() {
    setBusy(true); setErr(null)
    try {
      const r = await api.post(`/wholesale/evosense/properties/${propertyId}/attom`, {})
      setRes(r); await load(); if (onChanged) onChanged()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  if (!d) return <Panel title="Property intelligence">{error ? <Alert>{error}</Alert> : <Skeleton rows={3} />}</Panel>
  const a = d.attom || {}
  const plan = a.plan || {}
  const status = N.safeStatus(a.status)
  return (
    <Panel title="Property intelligence" id="intel-sources" hint={d.rule}
           action={<span className={`pn-badge pn-badge--${N.toneOf(status)}`} title="ATTOM status">ATTOM · {status}</span>}>
      <div className="evo-table-wrap"><table className="evo-table">
        <thead><tr><th>Fact</th><th>On file</th><th>Source</th></tr></thead>
        <tbody>{d.facts.map((f) => (
          <tr key={f.key} data-fact={f.key} className={f.have ? '' : 'is-muted'}>
            <td>{f.label}</td><td>{fmt(f)}</td>
            <td className="evo-small">{f.have ? (f.source || 'record on file (source under Public record)') : <Tag kind="danger">Missing</Tag>}</td>
          </tr>
        ))}</tbody>
      </table></div>
      <p className="evo-small" style={{ margin: '10px 0 0' }}>
        <b>{d.conflicts_open}</b> open source conflict{d.conflicts_open === 1 ? '' : 's'} (kept side by side, never overwritten)
        {a.last ? <> · ATTOM answered {ago(a.last.at)}{a.last.current ? '' : ' (older than 30 days)'}</> : <> · ATTOM has not been asked about this property</>}
      </p>

      <div className="pn-route" aria-label="ATTOM lookup decision">
        <p style={{ margin: '0 0 6px' }}><b>Would ATTOM help?</b> {plan.message}</p>
        <ol className="pn-steps">{(plan.steps || []).map((s, i) => (
          <li key={i} className={s.ok ? 'is-ok' : 'is-stop'}>{s.text}</li>
        ))}</ol>
        <div className="pn-actions">
          {a.can_lookup ? (
            <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy || !canBuy} onClick={lookup}
                    data-action="attom-lookup" title={canBuy ? undefined : 'Only an admin or a team manager can buy property data.'}>
              {busy ? 'Asking ATTOM…' : `Get ATTOM property data${a.max_cost_cents ? ` (up to ${N.money(a.max_cost_cents)})` : ''}`}</button>
          ) : null}
          <Link className="evo-link evo-small" to="/wholesale/evosense/controls#network">Data providers</Link>
        </div>
      </div>
      <Alert>{err}</Alert>
      {res ? (
        <Alert kind={res.called && !res.partial_failure ? 'ok' : 'info'}>
          {res.message}{res.cost_cents != null && res.called ? ` Cost ${N.money(res.cost_cents)} for ${res.calls} request${res.calls === 1 ? '' : 's'}.` : ''}
          {res.still_missing && res.still_missing.length ? ` Still missing: ${res.still_missing.join(', ').toLowerCase()}.` : ''}
          {res.partial_failure ? ` ${res.partial_failure}` : ''}
        </Alert>
      ) : null}
    </Panel>
  )
}

const DECISION = {
  PAID_LOOKUP_APPROVED: 'Paid lookup approved', USE_EXISTING_DATA: 'Used contact already on file', USE_FREE_SOURCE: 'Free source',
  BUDGET_BLOCKED: 'Blocked by budget', RETRY_LATER: 'Waiting', INSUFFICIENT_OPPORTUNITY: 'Not worth paying yet',
  SUPPRESSED: 'Do not contact', APPROVAL_REQUIRED: 'Needs approval', POLICY_BLOCKED: 'Blocked by policy',
}

export function ContactIntelligence({ propertyId, refreshKey }) {
  const [d, error] = useIntel(propertyId, refreshKey)
  if (!d) return error ? <Alert>{error}</Alert> : null
  const t = d.tracerfy || {}
  const status = N.safeStatus(t.status)
  return (
    <Panel title="Contact intelligence" id="contact-intel" hint="Tracerfy owner lookups for this property"
           action={<span className={`pn-badge pn-badge--${N.toneOf(status)}`}>Tracerfy · {status}</span>}>
      <p className="evo-small" style={{ margin: '0 0 8px' }}>{N.MEANING[status]}
        {t.enabled ? ' Automatic lookups are on for this workspace.' : ' Automatic lookups are off for this workspace; Enrich Contacts still asks first.'}
        {' '}A lookup is skipped when a good contact is already on file, the owner was looked up for another property, or a recent search found nothing.</p>
      {!(t.lookups || []).length ? <p className="evo-muted evo-small" style={{ margin: 0 }}>No lookup decisions for this property yet.</p> : (
        <div className="evo-table-wrap"><table className="evo-table">
          <thead><tr><th>When</th><th>Decision</th><th>Outcome</th><th>Provider</th><th className="is-num">Est. cost</th><th>Why</th></tr></thead>
          <tbody>{t.lookups.map((x, i) => (
            <tr key={i}><td className="evo-small">{ago(x.at)}</td><td>{DECISION[x.decision] || humanize(x.decision)}</td>
              <td className="evo-small">{x.outcome ? humanize(x.outcome) : '—'}</td><td className="evo-small">{x.provider || '—'}</td>
              <td className="is-num">{x.cost_cents != null ? N.money(x.cost_cents) : '—'}</td>
              <td className="evo-small">{x.reason || ''}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </Panel>
  )
}
