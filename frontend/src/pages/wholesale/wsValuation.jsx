/* VALUATION: what the evidence supports, and what it does not.
 *
 * Reads room.valuation (GET /wholesale/deals/{id}/valuation):
 *   ARV      from the ARV engine - eligible closed sales only - with its
 *            confidence EXPLAINED (every factor that raised or lowered it),
 *            its limitations, and each comp's eligibility with the reason.
 *            "INSUFFICIENT COMPARABLE SALES" is shown as the answer it is.
 *            Tax / appraisal values and AVMs sit beside it, labelled, never
 *            as the ARV.
 *   REPAIRS  a status, never an assumed $0: UNKNOWN / SELLER REPORTED /
 *            MANUAL / INSPECTION / SYSTEM / VERIFIED, with the history.
 *   MAO      CALCULATED, or NOT CALCULATED with every reason.
 *   COST     what finding and qualifying this opportunity cost (ledger only).
 */
import { useState } from 'react'
import { api } from '../../api/client'
import { fmtMoney } from './wsShared'

const REPAIR_CHOICES = [
  ['MANUAL_ESTIMATE', 'Manual estimate (mine)'],
  ['INSPECTION_ESTIMATE', 'Inspection / contractor estimate'],
  ['VERIFIED', 'Verified'],
  ['SELLER_REPORTED', 'Seller reported'],
]

function tone(label) {
  if (label === 'high') return 'band-high'
  if (label === 'medium') return 'band-medium'
  if (label === 'insufficient') return 'band-excluded'
  return 'is-warn'
}

function CompLine({ c, used }) {
  const why = used ? c.unknown : c.excluded
  return (
    <li className="ws-val-comp">
      <span className="ws-pill">{c.origin === 'MANUAL' ? 'MANUAL' : 'PROVIDER'}</span>
      {c.verification === 'human_verified' ? <span className="ws-pill is-ok">Checked by a person</span> : null}
      {c.verification === 'provider_verified' ? <span className="ws-pill is-ok">Provider verified</span> : null}
      <span className={`ws-pill ${used ? 'is-ok' : 'band-excluded'}`}>{used ? 'Counts' : 'Excluded'}</span>
      <b style={{ marginLeft: 6 }}>{c.address || 'unnamed'}</b>
      <span className="ws-muted"> {fmtMoney(c.sale_price)} · {c.sale_date || 'no date'}
        {c.distance_miles != null ? ` · ${Number(c.distance_miles).toFixed(2)} mi` : ''}
        {c.price_per_sqft ? ` · $${Math.round(c.price_per_sqft)}/sqft` : ''}</span>
      {why && why.length ? (
        <div className="ws-muted">
          {used ? 'Unknown: ' : 'Why not: '}
          {why.map((e) => e.label + (e.detail ? ` (${e.detail})` : '')).join('; ')}
        </div>
      ) : null}
    </li>
  )
}

export function ValuationPanel({ dealId, valuation, act, busy }) {
  const [rep, setRep] = useState({ status: 'MANUAL_ESTIMATE', amount: '', source: '', notes: '' })
  if (!valuation) return null
  const arv = valuation.arv
  const conf = arv?.confidence || {}
  const mao = valuation.mao || {}
  const repairs = valuation.repairs || {}
  const cost = valuation.acquisition_cost
  const other = Object.entries(arv?.other_valuations || {})

  return (
    <div className="panel ws-panel ws-val">
      <div className="panel-title ws-panel-title">Valuation — what the evidence supports</div>

      <div className="ws-kv ws-kv--tight">
        <div>
          <span className="ws-kv-label">ARV (sold comps)</span>
          {valuation.arv_value != null
            ? <b>{fmtMoney(valuation.arv_value)}</b>
            : <b>INSUFFICIENT COMPARABLE SALES</b>}
          {valuation.arv_source === 'manual' || valuation.arv_source === 'verified'
            ? <span className="ws-pill is-warn">Entered by a person</span> : null}
          {conf.label ? <span className={`ws-pill ${tone(conf.label)}`}>{conf.label} confidence</span> : null}
          {arv && arv.status === 'ESTIMATED' && arv.low != null
            ? <div className="ws-muted">Range {fmtMoney(arv.low)} – {fmtMoney(arv.high)} · {arv.comps_used.length} eligible closed sales</div>
            : null}
          {arv && (valuation.arv_source === 'manual' || valuation.arv_source === 'verified') && arv.value != null
            ? <div className="ws-muted">The comps alone say {fmtMoney(arv.value)}.</div> : null}
        </div>
        <div>
          <span className="ws-kv-label">Repairs</span>
          <b>{repairs.amount != null ? fmtMoney(repairs.amount) : '—'}</b>
          <span className={`ws-pill ${repairs.status === 'UNKNOWN' ? 'band-excluded' : ''}`}>{repairs.label}</span>
          {repairs.low != null && repairs.high != null
            ? <div className="ws-muted">Range {fmtMoney(repairs.low)} – {fmtMoney(repairs.high)}</div> : null}
        </div>
        <div>
          <span className="ws-kv-label">Maximum allowable offer</span>
          {mao.status === 'CALCULATED'
            ? <b>{fmtMoney(mao.value)}</b>
            : <b>NOT CALCULATED</b>}
          {(mao.reasons || []).map((r, i) => <div key={i} className="ws-muted">• {r}</div>)}
        </div>
        {cost ? (
          <div>
            <span className="ws-kv-label">Cost to acquire (ledger)</span>
            <b>{cost.total}</b>
            <div className="ws-muted">
              Find {fmtMoney((cost.cost_to_find_cents || 0) / 100)} · Contact {cost.cost_to_contactability_cents != null ? fmtMoney(cost.cost_to_contactability_cents / 100) : 'not reached'} · Qualify {cost.cost_to_qualification_cents != null ? fmtMoney(cost.cost_to_qualification_cents / 100) : 'not reached'}
            </div>
          </div>
        ) : null}
      </div>

      {conf.factors && conf.factors.length ? (
        <details className="ws-val-details">
          <summary>Why this confidence</summary>
          <ul>{conf.factors.map((f, i) => (
            <li key={i}><span className="ws-num">{f.points > 0 ? '+' : ''}{f.points}</span> {f.label}</li>))}</ul>
        </details>
      ) : null}
      {arv?.limitations?.length ? (
        <details className="ws-val-details">
          <summary>Limitations</summary>
          <ul>{arv.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
        </details>
      ) : null}

      {arv ? (
        <details className="ws-val-details" open={arv.status !== 'ESTIMATED'}>
          <summary>Comp eligibility ({arv.comps_used.length} count, {arv.comps_excluded.length} excluded · rules {arv.rules_version})</summary>
          <ul className="ws-val-comps">
            {arv.comps_used.map((c) => <CompLine key={c.comp_id} c={c} used />)}
            {arv.comps_excluded.map((c) => <CompLine key={c.comp_id} c={c} used={false} />)}
          </ul>
          {!arv.comps_used.length && !arv.comps_excluded.length
            ? <div className="ws-muted">No sold comps on file. Add closed sales below, each with a source reference.</div> : null}
        </details>
      ) : null}

      {other.length ? (
        <div className="ws-muted" style={{ marginTop: 8 }}>
          Reference only, never the ARV: {other.map(([k, v]) => `${v.type} ${fmtMoney(v.value)}`).join(' · ')}
        </div>
      ) : null}

      <div className="ws-val-repair">
        <div className="ws-kv-label">Record a repair estimate</div>
        <div className="ws-grid">
          <div className="ws-field">
            <label htmlFor="rep-status">What kind</label>
            <select id="rep-status" value={rep.status} onChange={(e) => setRep({ ...rep, status: e.target.value })}>
              {REPAIR_CHOICES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          <div className="ws-field">
            <label htmlFor="rep-amount">Amount</label>
            <input id="rep-amount" inputMode="decimal" value={rep.amount}
                   onChange={(e) => setRep({ ...rep, amount: e.target.value })} />
          </div>
          <div className="ws-field">
            <label htmlFor="rep-source">Source</label>
            <input id="rep-source" value={rep.source} placeholder="e.g. walkthrough 9/26, contractor bid"
                   onChange={(e) => setRep({ ...rep, source: e.target.value })} />
          </div>
        </div>
        <div className="ws-actions" style={{ marginTop: 8 }}>
          <button className="btn btn--secondary btn--sm" disabled={busy || rep.amount === ''}
                  onClick={() => act(() => api.post(`/wholesale/deals/${dealId}/repairs`, {
                    status: rep.status, amount: Number(rep.amount),
                    source: rep.source || null, notes: rep.notes || null,
                  }), 'Repair estimate recorded.')}>
            Save repair estimate
          </button>
        </div>
        {repairs.history && repairs.history.length ? (
          <details className="ws-val-details">
            <summary>Repair history ({repairs.history.length})</summary>
            <ul>{repairs.history.map((h) => (
              <li key={h.id} className={h.current ? '' : 'ws-muted'}>
                {h.label} {h.amount != null ? fmtMoney(h.amount) : ''} {h.source ? `· ${h.source}` : ''}
                {h.supplied_by ? ` · ${h.supplied_by}` : ''}{h.current ? ' (current)' : ''}
              </li>))}</ul>
          </details>
        ) : null}
      </div>
    </div>
  )
}
